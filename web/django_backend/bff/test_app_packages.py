import json
import os
from unittest.mock import patch, AsyncMock

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction
from django.test import TestCase, RequestFactory
from django.http import HttpResponse
from rest_framework_simplejwt.tokens import AccessToken

from .app_access import decision, available_apps
from .models import AppInstallation, AppGrant
from .views.app_platform import apps, authorize, worker_authorize, administration
from .views.app_proxy import proxy_app, valid_path
from .views.official_documents import proxy_documents
from src.platform.kernel.registry import discover
from src.platform.authorization.client import service_token


class AppPackageTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(username="package-user")
        self.admin = get_user_model().objects.create_superuser(
            username="package-admin", password="test-only"
        )
        self.manifest = discover()["official-documents"]
        self.app = AppInstallation.objects.create(
            app_id=self.manifest.id,
            installed_version=self.manifest.data["version"],
            deployment_digest=self.manifest.digest,
            desired_enabled=True,
            state="enabled",
        )

    def request(self, method="get", body=None, user=None):
        data = (body or {}) if method == "get" else json.dumps(body or {})
        request = getattr(self.factory, method)(
            "/api/platform/apps/", data=data, content_type="application/json"
        )
        request.META["HTTP_AUTHORIZATION"] = "Bearer " + str(
            AccessToken.for_user(user or self.user)
        )
        return request

    def call(self, view, request=None, **kwargs):
        with patch("bff.decorators.token_is_revoked", return_value=False):
            return view(request or self.request(), **kwargs)

    def test_no_grant_is_hidden_and_denied(self):
        self.assertEqual(available_apps(self.user), [])
        self.assertEqual(
            self.call(authorize, self.request("post"), app_id=self.app.pk).status_code, 403
        )
        self.assertEqual(self.call(apps).status_code, 200)

    def test_direct_and_group_grants_and_revocation(self):
        group = Group.objects.create(name="document-team")
        AppGrant.objects.create(app=self.app, group=group)
        self.user.groups.add(group)
        self.assertEqual(decision(self.user, self.app.pk), 200)
        self.user.groups.remove(group)
        self.assertEqual(decision(self.user, self.app.pk), 403)
        grant = AppGrant.objects.create(app=self.app, user=self.user)
        self.assertEqual(decision(self.user, self.app.pk), 200)
        grant.delete()
        self.assertEqual(decision(self.user, self.app.pk), 403)

    def test_legacy_viewer_with_explicit_grant_has_full_app(self):
        self.user.groups.add(Group.objects.create(name="viewer"))
        AppGrant.objects.create(app=self.app, user=self.user)
        self.assertEqual(
            self.call(authorize, self.request("post"), app_id=self.app.pk).status_code, 200
        )

    def test_admin_cannot_bypass_disabled_or_version_mismatch(self):
        self.assertEqual(decision(self.admin, self.app.pk), 200)
        self.app.deployment_digest = "wrong"
        self.app.save()
        self.assertEqual(decision(self.admin, self.app.pk), 503)
        self.app.desired_enabled = False
        self.app.save()
        self.assertEqual(decision(self.admin, self.app.pk), 404)

    def test_inactive_user_is_rejected(self):
        AppGrant.objects.create(app=self.app, user=self.user)
        self.user.is_active = False
        self.assertEqual(decision(self.user, self.app.pk), 401)

    def test_stream_identity_is_refreshed_after_admin_deactivation(self):
        self.assertEqual(decision(self.admin, self.app.pk), 200)
        type(self.admin).objects.filter(pk=self.admin.pk).update(is_active=False)
        self.assertTrue(self.admin.is_active)
        self.assertEqual(decision(self.admin, self.app.pk), 401)

    def test_grant_constraints(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            AppGrant.objects.create(app=self.app)
        AppGrant.objects.create(app=self.app, user=self.user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AppGrant.objects.create(app=self.app, user=self.user)

    def test_admin_manages_users_and_groups_atomically(self):
        body = {"user_ids": [self.user.pk], "group_ids": []}
        self.assertEqual(
            self.call(administration, self.request("put", body), app_id=self.app.pk).status_code,
            403,
        )
        response = self.call(
            administration, self.request("put", body, self.admin), app_id=self.app.pk
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(decision(self.user, self.app.pk), 200)
        bad = {"user_ids": [999999], "group_ids": []}
        self.assertEqual(
            self.call(
                administration, self.request("put", bad, self.admin), app_id=self.app.pk
            ).status_code,
            400,
        )
        self.assertEqual(decision(self.user, self.app.pk), 200)

    def test_worker_requires_signed_scoped_token_and_current_grant(self):
        with patch.dict(os.environ, {"NETOPS_APP_INTERNAL_SECRET": "package-test-secret-" * 3}):
            token = service_token(str(self.user.pk), self.app.pk)
            request = self.request("post")
            request.META["HTTP_AUTHORIZATION"] = "Bearer " + token
            self.assertEqual(worker_authorize(request, self.app.pk).status_code, 403)
            grant = AppGrant.objects.create(app=self.app, user=self.user)
            self.assertEqual(worker_authorize(request, self.app.pk).status_code, 200)
            self.assertEqual(worker_authorize(request, "another-app").status_code, 403)
            grant.delete()
            self.assertEqual(worker_authorize(request, self.app.pk).status_code, 403)
            request = self.request("post")
            request.META["HTTP_AUTHORIZATION"] = "Bearer forged"
            self.assertEqual(worker_authorize(request, self.app.pk).status_code, 401)

    def test_proxy_legacy_url_and_download_are_guarded_before_forward(self):
        with (
            patch("bff.decorators.token_is_revoked", return_value=False),
            patch(
                "bff.views.app_proxy.forward",
                new_callable=AsyncMock,
                return_value=HttpResponse("{}", content_type="application/json"),
            ) as upstream,
        ):
            for view, kw in [(proxy_app, {"app_id": self.app.pk}), (proxy_documents, {})]:
                response = async_to_sync(view)(self.request(), path="doc/exports/op/download", **kw)
                self.assertEqual(response.status_code, 403)
            upstream.assert_not_called()
            AppGrant.objects.create(app=self.app, user=self.user)
            response = async_to_sync(proxy_app)(
                self.request(), app_id=self.app.pk, path="templates"
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(upstream.call_count, 1)

    def test_proxy_rejects_encoded_traversal(self):
        for value in (
            "../parser",
            "%2e%2e/parser",
            "%252e%252e/parser",
            "a\\..\\b",
            "/https://x",
            "a?url=x",
        ):
            self.assertFalse(valid_path(value), value)
        self.assertTrue(valid_path("doc/operations/op/events"))

    def test_forged_identity_headers_do_not_authenticate(self):
        request = self.factory.get(
            "/api/apps/official-documents/",
            HTTP_X_USER_ID=str(self.admin.pk),
            HTTP_X_USER_ROLE="admin",
        )
        self.assertEqual(async_to_sync(proxy_app)(request, app_id=self.app.pk).status_code, 401)

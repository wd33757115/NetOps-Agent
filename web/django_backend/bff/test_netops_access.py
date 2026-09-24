import json
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from rest_framework_simplejwt.tokens import AccessToken
from .roles import assign_user_role, assign_netops_configuration, get_user_permissions
from .jwt_utils import attach_auth_context
from .user_admin import bff_user_detail
from .views._helpers import forward_client_headers

class NetOpsAccessTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(username="config-admin")
        assign_user_role(self.actor, "admin")
        self.actor.save()
        self.operator = get_user_model().objects.create_user(username="config-operator")
        assign_user_role(self.operator, "operator")
        self.operator.save()
        self.factory = RequestFactory()

    def test_ordinary_user_defaults_and_explicit_grant(self):
        self.assertEqual(get_user_permissions(self.operator), [])
        assign_netops_configuration(self.operator, True)
        self.assertEqual(get_user_permissions(self.operator), ["apps.manage"])
        assign_user_role(self.operator, "operator")
        self.assertEqual(get_user_permissions(self.operator), ["apps.manage"])
        assign_netops_configuration(self.operator, False)
        self.assertEqual(get_user_permissions(self.operator), [])

    def test_old_token_and_forged_headers_cannot_restore_revoked_grant(self):
        request = self.factory.get("/api/devices/", HTTP_X_USER_PERMISSIONS="netops.configure", HTTP_X_USER_ROLE="admin")
        attach_auth_context(request, self.operator, {"role": "admin", "permissions": ["netops.configure"]})
        headers = forward_client_headers(request)
        self.assertEqual(headers["X-User-Role"], "operator")
        self.assertEqual(headers["X-User-Permissions"], "")
        assign_netops_configuration(self.operator, True)
        attach_auth_context(request, self.operator, {})
        self.assertEqual(forward_client_headers(request)["X-User-Permissions"], "apps.manage")
        assign_netops_configuration(self.operator, False)
        attach_auth_context(request, self.operator, {"permissions": ["netops.configure"]})
        self.assertEqual(request.bff_user_permissions, [])

    def call_update(self, actor, payload):
        request = self.factory.patch("/api/auth/users/", data=json.dumps(payload), content_type="application/json", HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(actor)))
        with patch("bff.decorators.token_is_revoked", return_value=False), patch("bff.user_admin.log_auth_event"):
            return bff_user_detail(request, self.operator.id)

    def test_only_admin_can_grant_and_revoke(self):
        self.assertEqual(self.call_update(self.operator, {"netops_configure": True}).status_code, 403)
        response = self.call_update(self.actor, {"netops_configure": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["data"]["permissions"], ["apps.manage"])
        self.assertEqual(self.call_update(self.actor, {"netops_configure": False}).status_code, 200)
        self.assertEqual(get_user_permissions(self.operator), [])
        self.assertEqual(self.call_update(self.actor, {"netops_configure": "true"}).status_code, 400)

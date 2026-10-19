import base64
import json
from unittest.mock import AsyncMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.http import JsonResponse
from django.test import RequestFactory, TestCase

from .roles import assign_user_role
from .views import itsm


def basic(username, password):
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


class ItsmWebhookAuthTests(TestCase):
    """An ITSM system starts a Workflow with a platform account (HTTP Basic)."""

    def setUp(self):
        self.operator = get_user_model().objects.create_user(username="itsm-bot", password="s3cret-pass")
        assign_user_role(self.operator, "operator")
        self.viewer = get_user_model().objects.create_user(username="itsm-viewer", password="s3cret-pass")
        # A legacy read-only account: the viewer group is no longer assigned, only honoured.
        self.viewer.groups.add(Group.objects.get_or_create(name="viewer")[0])
        self.factory = RequestFactory()
        for name in ("check_login_blocked", "record_login_failure", "clear_login_failures", "log_auth_event"):
            patcher = patch.object(itsm, name, return_value=(False, 0) if name == "check_login_blocked" else None)
            self.addCleanup(patcher.stop)
            setattr(self, name, patcher.start())

    def call(self, authorization=None, route_key="demo-text-report"):
        request = self.factory.post(
            f"/api/itsm/webhook/{route_key}/",
            data=json.dumps({"ticket_id": "DEMO-9", "text": "x"}),
            content_type="application/json",
            **({"HTTP_AUTHORIZATION": authorization} if authorization else {}),
        )
        request.bff_request_id = "req-1"
        with patch.object(itsm, "proxy_to_fastapi", new=AsyncMock(
            return_value=JsonResponse({"status": "accepted"}, status=202)
        )) as forwarded:
            response = itsm.proxy_plugin_webhook(request, route_key=route_key)
        return response, forwarded

    def test_forwards_the_account_and_never_the_password(self):
        response, forwarded = self.call(basic("itsm-bot", "s3cret-pass"))
        self.assertEqual(response.status_code, 202)
        sent = forwarded.await_args.kwargs
        self.assertEqual(sent["fastapi_path"], "/api/v1/itsm/webhook/demo-text-report")
        self.assertEqual(sent["data"], {"ticket_id": "DEMO-9", "text": "x"})
        headers = sent["extra_headers"]
        self.assertEqual(headers["X-User-Id"], str(self.operator.id))
        self.assertEqual(headers["X-User-Role"], "operator")
        self.assertNotIn("Authorization", headers)
        self.clear_login_failures.assert_called_once()

    def test_missing_or_wrong_credentials_are_challenged(self):
        for authorization in (None, "Bearer abc", "Basic !!!", basic("itsm-bot", "wrong")):
            response, forwarded = self.call(authorization)
            self.assertEqual(response.status_code, 401, authorization)
            self.assertIn("Basic", response["WWW-Authenticate"])
            forwarded.assert_not_awaited()
        self.record_login_failure.assert_called_once()

    def test_a_viewer_cannot_start_workflows(self):
        response, forwarded = self.call(basic("itsm-viewer", "s3cret-pass"))
        self.assertEqual(response.status_code, 403)
        forwarded.assert_not_awaited()

    def test_repeated_failures_are_blocked_like_login(self):
        self.check_login_blocked.return_value = (True, 60)
        response, forwarded = self.call(basic("itsm-bot", "s3cret-pass"))
        self.assertEqual(response.status_code, 429)
        forwarded.assert_not_awaited()

    def test_the_fixed_firewall_route_needs_the_account_too(self):
        request = self.factory.post("/api/itsm/webhook/firewall-policy/", data="{}",
                                    content_type="application/json")
        request.bff_request_id = "req-2"
        self.assertEqual(itsm.proxy_firewall_policy_webhook(request).status_code, 401)

"""Old parser URLs share App admission, body and query forwarding."""
from unittest.mock import AsyncMock, patch
from asgiref.sync import async_to_sync
from django.http import JsonResponse
from django.test import RequestFactory, SimpleTestCase
from django.urls import resolve

class ParserRevisionProxyTests(SimpleTestCase):
    def test_actions_use_the_same_package_proxy(self):
        for action in ("engineering-revision", "inspection", "resolve", "approve"):
            request = RequestFactory().post(
                f"/api/parser/rule-asset-bundles/bundle/{action}/?async_mode=true",
                {"reason": "reviewed"}, content_type="application/json")
            matched = resolve(request.path)
            with patch("bff.views.parser.proxy_app", new_callable=AsyncMock) as proxy:
                proxy.return_value = JsonResponse({"ok": True})
                response = async_to_sync(matched.func)(request, **matched.kwargs)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(proxy.call_args.args, (request, "rule-governance", f"parser/rule-asset-bundles/bundle/{action}"))
            self.assertEqual(request.GET["async_mode"], "true")
            self.assertIn(b"reviewed", request.body)

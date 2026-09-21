import os
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from src.platform.kernel.runtime import token

from .middleware import BFFRateLimitMiddleware


@override_settings(DEBUG=False, BFF_RATE_LIMIT_DEFAULT=1)
class AppCallbackRateTests(SimpleTestCase):
    @patch.dict(os.environ, {"NETOPS_APP_INTERNAL_SECRET": "isolated-rate-limit-service-secret"})
    def test_service_callback_does_not_spend_user_budget_or_trust_forged_header(self):
        limiter = BFFRateLimitMiddleware(lambda request: HttpResponse("ok"))
        factory = RequestFactory()
        with patch.object(limiter, "_redis_rate_limit", return_value=(False, 0)):
            self.assertIsNone(
                limiter._check_rate_limit(factory.get("/api/apps/firewall-policy/runs"))
            )
            signed = factory.post(
                "/api/platform/apps/firewall-policy/authorize/",
                HTTP_X_NETOPS_APP_SERVICE="Bearer " + token(),
            )
            self.assertIsNone(limiter._check_rate_limit(signed))
            forged = factory.post(
                "/api/platform/apps/firewall-policy/authorize/",
                HTTP_X_NETOPS_APP_SERVICE="Bearer forged",
            )
            self.assertEqual(limiter._check_rate_limit(forged).status_code, 429)
            # A genuine service signature grants no exemption to business routes.
            external = factory.get(
                "/api/apps/firewall-policy/runs", HTTP_X_NETOPS_APP_SERVICE="Bearer " + token()
            )
            self.assertEqual(limiter._check_rate_limit(external).status_code, 429)

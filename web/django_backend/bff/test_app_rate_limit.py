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

    @patch.dict(os.environ, {"NETOPS_APP_INTERNAL_SECRET": "isolated-rate-limit-service-secret"})
    def test_runtime_callback_requires_its_service_signature(self):
        limiter = BFFRateLimitMiddleware(lambda request: HttpResponse('ok'))
        factory = RequestFactory()
        with patch.object(limiter, '_redis_rate_limit', return_value=(False, 0)):
            self.assertIsNone(limiter._check_rate_limit(factory.get('/api/apps/inspection/test')))
            signed = factory.post('/api/platform/apps/runtime/', HTTP_AUTHORIZATION='Bearer ' + token())
            self.assertIsNone(limiter._check_rate_limit(signed))
            forged = factory.post('/api/platform/apps/runtime/', HTTP_AUTHORIZATION='Bearer forged')
            self.assertEqual(limiter._check_rate_limit(forged).status_code, 429)

    def test_users_behind_one_address_have_separate_validated_quotas(self):
        from rest_framework_simplejwt.tokens import AccessToken
        limiter = BFFRateLimitMiddleware(lambda request: HttpResponse('ok'))
        factory = RequestFactory()
        one, two = AccessToken(), AccessToken()
        one['user_id'], two['user_id'] = 1, 2
        def request(access):
            return factory.get('/api/apps/inspection/test', HTTP_AUTHORIZATION='Bearer ' + str(access))
        with patch.object(limiter, '_redis_rate_limit', return_value=(False, 0)):
            self.assertIsNone(limiter._check_rate_limit(request(one)))
            self.assertEqual(limiter._check_rate_limit(request(one)).status_code, 429)
            self.assertIsNone(limiter._check_rate_limit(request(two)))
            self.assertTrue(limiter._identity(request('forged')).startswith('ip:'))

    @override_settings(BFF_RATE_LIMIT_CHAT=1)
    def test_page_requests_do_not_spend_chat_budget_when_redis_is_unavailable(self):
        limiter = BFFRateLimitMiddleware(lambda request: HttpResponse('ok'))
        factory = RequestFactory()
        with patch.object(limiter, '_redis_rate_limit', return_value=(False, 0)):
            self.assertIsNone(limiter._check_rate_limit(factory.get('/api/devices/')))
            self.assertIsNone(limiter._check_rate_limit(factory.post('/api/chat/send/')))
            self.assertEqual(limiter._check_rate_limit(factory.post('/api/chat/send/')).status_code, 429)
            self.assertEqual(limiter._check_rate_limit(factory.get('/api/devices/')).status_code, 429)

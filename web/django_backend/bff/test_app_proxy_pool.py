"""App proxy releases each response while keeping pooled connections usable."""
from unittest.mock import patch

import httpx
from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, RequestFactory

from .views.app_proxy import forward


class AppProxyPoolTests(SimpleTestCase):
    async def test_json_and_stream_do_not_close_shared_client(self):
        def respond(request):
            if 'stream' in str(request.url):
                return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=b'data: ok\n\n')
            return httpx.Response(200, json={'ok': True})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            request = RequestFactory().get('/api/apps/inspection/test')
            request.user = AnonymousUser()
            with patch('bff.views.app_proxy._get_client', return_value=client), patch('bff.views.app_proxy.decision', return_value=200):
                for _ in range(2):
                    result = await forward(request, 'inspection', 'test')
                    self.assertEqual(result.status_code, 200)
                    self.assertIn(b'true', result.content)
                    self.assertFalse(client.is_closed)
                result = await forward(request, 'inspection', 'stream')
                content = b''.join([chunk async for chunk in result.streaming_content])
                self.assertEqual(content, b'data: ok\n\n')
                self.assertFalse(client.is_closed)

    def test_proxy_releases_idle_connections_without_breaking_transactions(self):
        from unittest.mock import Mock
        from bff.sync_async import release_proxy_connections
        idle = Mock(in_atomic_block=False)
        transaction = Mock(in_atomic_block=True)
        with patch('bff.sync_async.connections.all', return_value=[idle, transaction]):
            release_proxy_connections()
        idle.close.assert_called_once()
        transaction.close.assert_not_called()

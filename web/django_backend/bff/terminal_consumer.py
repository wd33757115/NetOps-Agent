"""Same-origin terminal proxy. Authentication is a one-use ticket in the first frame."""

import asyncio
import contextlib
from urllib.parse import urlsplit

import websockets
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from .proxy_client import build_fastapi_ws_url


class TerminalProxyConsumer(AsyncWebsocketConsumer):
    upstream = None
    forwarding = None

    async def connect(self):
        headers = dict(self.scope.get("headers", []))
        origin = headers.get(b"origin", b"").decode()
        # Explicit origins also support Vite's local development proxy.
        allowed = set(getattr(settings, "CSRF_TRUSTED_ORIGINS", []))
        host = headers.get(b"host", b"").decode()
        parsed = urlsplit(origin)
        if (
            not origin
            or parsed.scheme not in {"http", "https"}
            or (parsed.netloc != host and origin not in allowed)
        ):
            await self.close(code=4403)
            return
        session_id = self.scope["url_route"]["kwargs"]["session_id"]
        try:
            self.upstream = await websockets.connect(
                build_fastapi_ws_url(f"/ws/v1/terminals/{session_id}", b""),
                open_timeout=5,
                max_size=65536,
                max_queue=16,
            )
            await self.accept()
            self.forwarding = asyncio.create_task(self.forward())
        except Exception:
            await self.close(code=1011)

    async def receive(self, text_data=None, bytes_data=None):
        if not self.upstream or text_data is None or len(text_data.encode()) > 4096:
            await self.close(code=1009)
            return
        try:
            await asyncio.wait_for(self.upstream.send(text_data), timeout=5)
        except Exception:
            await self.close(code=1011)

    async def forward(self):
        try:
            async for message in self.upstream:
                await self.send(text_data=message)
        except Exception:
            pass
        finally:
            await self.close()

    async def disconnect(self, close_code):
        if self.forwarding:
            self.forwarding.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.forwarding
        if self.upstream:
            await self.upstream.close()

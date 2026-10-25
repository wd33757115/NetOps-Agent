# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import asyncio
import logging
import uuid

import websockets
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from .proxy_client import build_fastapi_ws_url
from .roles import get_user_role, user_thread_prefix
from .ws_auth import authenticate_websocket_scope

logger = logging.getLogger("bff.consumers")


class ChatProxyConsumer(AsyncWebsocketConsumer):
    """将前端 WebSocket 双向转发至 FastAPI /ws/v1/chat。"""

    upstream = None
    upstream_task = None
    request_id = None
    ws_user = None

    async def connect(self):
        self.request_id = str(uuid.uuid4())

        auth_required = getattr(settings, "BFF_REQUIRE_AUTH", not settings.DEBUG)
        if auth_required:
            user, claims, error = await asyncio.to_thread(authenticate_websocket_scope, self.scope)
            if error or not user:
                logger.warning("ws_auth_failed request_id=%s error=%s", self.request_id, error)
                await self.close(code=4401)
                return
            self.ws_user = user
            self.ws_claims = claims

        query_string = self.scope.get("query_string", b"")
        upstream_url = build_fastapi_ws_url("/ws/v1/chat", query_string)
        upstream_headers = {
            "X-Request-ID": self.request_id,
            "X-Forwarded-From": "django-bff",
            "X-Internal-Request": "true",
        }
        if self.ws_user:
            upstream_headers["X-User-Id"] = str(self.ws_user.id)
            upstream_headers["X-User-Name"] = self.ws_user.username
            upstream_headers["X-User-Role"] = get_user_role(self.ws_user)
            upstream_headers["X-User-Thread-Prefix"] = user_thread_prefix(self.ws_user)
            session_id = getattr(self, "ws_claims", {}).get("session_id")
            if session_id:
                upstream_headers["X-Session-Id"] = str(session_id)

        try:
            self.upstream = await websockets.connect(
                upstream_url,
                additional_headers=upstream_headers,
                open_timeout=10,
            )
        except Exception as exc:
            logger.error(
                "ws_proxy_upstream_connect_failed request_id=%s url=%s error=%s",
                self.request_id,
                upstream_url,
                exc,
            )
            if self.upstream:
                await self.upstream.close()
                self.upstream = None
            return

        await self.accept()
        self.upstream_task = asyncio.create_task(self._forward_upstream())
        logger.info(
            "ws_proxy_connected request_id=%s upstream=%s user=%s",
            self.request_id,
            upstream_url,
            getattr(self.ws_user, "username", "anonymous"),
        )

    async def disconnect(self, close_code):
        if self.upstream_task:
            self.upstream_task.cancel()
            try:
                await self.upstream_task
            except asyncio.CancelledError:
                pass

        if self.upstream:
            await self.upstream.close()
            self.upstream = None

        logger.info(
            "ws_proxy_disconnected request_id=%s close_code=%s",
            self.request_id,
            close_code,
        )

    async def receive(self, text_data=None, bytes_data=None):
        if not self.upstream:
            return

        try:
            if text_data is not None:
                await self.upstream.send(text_data)
            elif bytes_data is not None:
                await self.upstream.send(bytes_data)
        except Exception as exc:
            logger.warning(
                "ws_proxy_client_to_upstream_failed request_id=%s error=%s",
                self.request_id,
                exc,
            )
            await self.close(code=1011)

    async def _forward_upstream(self):
        try:
            async for message in self.upstream:
                if isinstance(message, bytes):
                    await self.send(bytes_data=message)
                else:
                    await self.send(text_data=message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "ws_proxy_upstream_to_client_failed request_id=%s error=%s",
                self.request_id,
                exc,
            )
        finally:
            await self.close()

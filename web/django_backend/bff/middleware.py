# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import asyncio
import logging
import time
import uuid
from collections import defaultdict

from asgiref.sync import async_to_sync
from django.conf import settings
from django.http import HttpRequest, JsonResponse

from .response import bff_error

logger = logging.getLogger("bff.middleware")


def get_client_ip(request: HttpRequest) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _resolve_response(response):
    if asyncio.iscoroutine(response):
        async def _await_coro():
            return await response

        return async_to_sync(_await_coro)()
    return response


def _attach_request_meta(request: HttpRequest) -> str:
    request_id = request.META.get("HTTP_X_REQUEST_ID") or str(uuid.uuid4())
    request.bff_request_id = request_id
    request.bff_start_time = time.monotonic()
    return request_id


def _log_request(request: HttpRequest, response, request_id: str) -> None:
    duration_ms = (time.monotonic() - request.bff_start_time) * 1000
    response["X-Request-ID"] = request_id
    logger.info(
        "bff_request",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.path,
            "status": response.status_code,
            "duration_ms": round(duration_ms, 2),
            "client_ip": get_client_ip(request),
        },
    )


class BFFRequestIDMiddleware:
    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest):
        if asyncio.iscoroutinefunction(self.get_response):
            return self.__acall__(request)
        request_id = _attach_request_meta(request)
        response = _resolve_response(self.get_response(request))
        _log_request(request, response, request_id)
        return response

    async def __acall__(self, request: HttpRequest):
        request_id = _attach_request_meta(request)
        response = await self.get_response(request)
        _log_request(request, response, request_id)
        return response


class BFFRateLimitMiddleware:
    """限流：优先 Redis 滑动窗口，不可用时进程内计数。"""

    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self._window: dict[str, list[float]] = defaultdict(list)
        self._limit_chat = getattr(settings, "BFF_RATE_LIMIT_CHAT", 30)
        self._limit_default = getattr(settings, "BFF_RATE_LIMIT_DEFAULT", 60)
        self._window_seconds = getattr(settings, "BFF_RATE_LIMIT_WINDOW", 60)

    def _redis_rate_limit(self, ip: str, path: str) -> tuple[bool, int]:
        try:
            import sys
            from pathlib import Path

            root = Path(__file__).resolve().parents[3]
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            from src.auth.token_store import get_redis

            client = get_redis()
            if not client:
                return False, 0
            limit = self._limit_chat if "/chat/" in path else self._limit_default
            bucket = int(time.time()) // self._window_seconds
            key = f"ratelimit:bff:{ip}:{bucket}:{ 'chat' if '/chat/' in path else 'default'}"
            count = client.incr(key)
            if count == 1:
                client.expire(key, self._window_seconds + 1)
            if count > limit:
                return True, client.ttl(key) or self._window_seconds
            return False, 0
        except Exception:
            return False, 0

    def _clean_window(self, ip: str) -> None:
        now = time.monotonic()
        cutoff = now - self._window_seconds
        self._window[ip] = [t for t in self._window[ip] if t > cutoff]

    def _is_rate_limited(self, ip: str, path: str) -> bool:
        self._clean_window(ip)
        limit = self._limit_chat if "/chat/" in path else self._limit_default
        return len(self._window[ip]) >= limit

    def _check_rate_limit(self, request: HttpRequest):
        # Each external App request already consumes the user-facing budget.
        # Only a cryptographically authenticated service can avoid counting its
        # internal authorization callback a second time; the view still checks
        # the original user/worker token, current App grant and package digest.
        if request.method == "POST" and request.path.startswith("/api/platform/apps/") and (
            request.path.endswith("/authorize/") or request.path.endswith("/worker-authorize/")
        ):
            import jwt
            from src.platform.kernel.runtime import authenticate

            try:
                authenticate(request.headers.get("X-Netops-App-Service", ""))
            except (ValueError, jwt.InvalidTokenError):
                pass
            else:
                return None
        if settings.DEBUG and not getattr(settings, "BFF_RATE_LIMIT_IN_DEBUG", False):
            return None
        ip = get_client_ip(request)
        limited, retry_after = self._redis_rate_limit(ip, request.path)
        if limited:
            return bff_error(
                "Rate limit exceeded. Please try again later.",
                429,
                data={"retry_after_seconds": retry_after or self._window_seconds},
            )
        if self._is_rate_limited(ip, request.path):
            return bff_error(
                "Rate limit exceeded. Please try again later.",
                429,
                data={"retry_after_seconds": self._window_seconds},
            )
        self._window[ip].append(time.monotonic())
        return None

    def __call__(self, request: HttpRequest):
        if asyncio.iscoroutinefunction(self.get_response):
            return self.__acall__(request)
        limited = self._check_rate_limit(request)
        if limited is not None:
            return limited
        response = _resolve_response(self.get_response(request))
        return response

    async def __acall__(self, request: HttpRequest):
        limited = self._check_rate_limit(request)
        if limited is not None:
            return limited
        return await self.get_response(request)

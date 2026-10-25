# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Redis：JWT 黑名单、会话吊销、登录限流。"""

from __future__ import annotations

import logging
import time
from typing import Optional

import jwt

from src.common.config import get_settings

logger = logging.getLogger(__name__)

_redis_client = None
_redis_checked = False
_redis_next_retry_at = 0.0

# 登录失败限流
LOGIN_FAIL_WINDOW_SEC = 900  # 15 分钟
LOGIN_FAIL_MAX_ATTEMPTS = 5
LOGIN_BLOCK_MESSAGE = "登录尝试过多，请稍后再试"


def get_redis():
    """获取 Redis 客户端；不可用时返回 None（限流降级为内存，黑名单跳过）。"""
    global _redis_client, _redis_checked, _redis_next_retry_at
    if _redis_client is not None:
        return _redis_client
    if _redis_checked and time.monotonic() < _redis_next_retry_at:
        return None
    _redis_checked = True
    try:
        import redis

        settings = get_settings()
        client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        _redis_client = client
        _redis_next_retry_at = 0.0
        logger.info("Redis token store connected: %s:%s", settings.REDIS_HOST, settings.REDIS_PORT)
    except Exception as exc:
        logger.warning("Redis unavailable, auth store degraded: %s", exc)
        _redis_client = None
        _redis_next_retry_at = time.monotonic() + max(
            1, int(get_settings().REDIS_RECONNECT_BACKOFF_SECONDS)
        )
    return _redis_client


def invalidate_redis_client(error: Exception | str | None = None) -> None:
    global _redis_client, _redis_checked, _redis_next_retry_at
    _redis_client = None
    _redis_checked = True
    _redis_next_retry_at = time.monotonic() + max(
        1, int(get_settings().REDIS_RECONNECT_BACKOFF_SECONDS)
    )
    if error:
        logger.warning("Redis client invalidated; reconnect scheduled: %s", error)


def _blacklist_key(jti: str) -> str:
    return f"jwt:blacklist:{jti}"


def _session_revoked_key(session_id: str) -> str:
    return f"jwt:session:revoked:{session_id}"


def _token_ttl_seconds(payload: dict, default: int = 3600) -> int:
    exp = payload.get("exp")
    if not exp:
        return default
    return max(int(exp) - int(time.time()), 1)


def blacklist_token(token_str: str, token_type: str = "access") -> bool:
    """将 token 的 jti 写入黑名单，TTL 与 token 剩余有效期一致。"""
    client = get_redis()
    if not client:
        return False
    try:
        settings = get_settings()
        payload = jwt.decode(
            token_str,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_exp": False},
        )
        jti = payload.get("jti")
        if not jti:
            return False
        client.setex(_blacklist_key(str(jti)), _token_ttl_seconds(payload), token_type)
        session_id = payload.get("session_id")
        if session_id and token_type == "refresh":
            revoke_session(str(session_id))
        return True
    except Exception as exc:
        logger.warning("blacklist_token failed: %s", exc)
        return False


def revoke_session(session_id: str, ttl_seconds: int = 7 * 86400) -> bool:
    client = get_redis()
    if not client or not session_id:
        return False
    try:
        client.setex(_session_revoked_key(session_id), ttl_seconds, "1")
        return True
    except Exception as exc:
        logger.warning("revoke_session failed: %s", exc)
        return False


def is_token_blacklisted(jti: Optional[str]) -> bool:
    if not jti:
        return False
    client = get_redis()
    if not client:
        return False
    try:
        return bool(client.exists(_blacklist_key(str(jti))))
    except Exception:
        return False


def is_session_revoked(session_id: Optional[str]) -> bool:
    if not session_id:
        return False
    client = get_redis()
    if not client:
        return False
    try:
        return bool(client.exists(_session_revoked_key(str(session_id))))
    except Exception:
        return False


def is_auth_token_revoked(jti: Optional[str], session_id: Optional[str]) -> bool:
    return is_token_blacklisted(jti) or is_session_revoked(session_id)


def _login_fail_keys(ip: str, username: str) -> tuple[str, str]:
    return f"auth:login:fail:ip:{ip}", f"auth:login:fail:user:{username.lower()}"


def check_login_blocked(ip: str, username: str) -> tuple[bool, int]:
    """检查 IP/用户名是否因失败过多被临时封禁。返回 (blocked, retry_after_seconds)。"""
    client = get_redis()
    if not client:
        return False, 0
    try:
        ip_key, user_key = _login_fail_keys(ip, username)
        for key in (ip_key, user_key):
            count = int(client.get(key) or 0)
            if count >= LOGIN_FAIL_MAX_ATTEMPTS:
                ttl = client.ttl(key)
                return True, max(ttl, 1)
        return False, 0
    except Exception:
        return False, 0


def record_login_failure(ip: str, username: str) -> None:
    client = get_redis()
    if not client:
        return
    try:
        ip_key, user_key = _login_fail_keys(ip, username)
        pipe = client.pipeline()
        for key in (ip_key, user_key):
            pipe.incr(key)
            pipe.expire(key, LOGIN_FAIL_WINDOW_SEC)
        pipe.execute()
    except Exception as exc:
        logger.warning("record_login_failure failed: %s", exc)


def clear_login_failures(ip: str, username: str) -> None:
    client = get_redis()
    if not client:
        return
    try:
        ip_key, user_key = _login_fail_keys(ip, username)
        client.delete(ip_key, user_key)
    except Exception:
        pass

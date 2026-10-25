# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""经 BFF 代理的对象下载链接（替代 MinIO localhost 预签名 URL）。"""

from __future__ import annotations

import hmac
import hashlib
import time
from urllib.parse import quote, urlencode

from src.common.config import get_settings

DEFAULT_ARTIFACT_EXPIRES = 3600 * 24 * 7


def _signing_secret() -> str:
    settings = get_settings()
    return settings.ARTIFACT_DOWNLOAD_SECRET or settings.JWT_SECRET_KEY or settings.ITSM_WEBHOOK_SECRET


def compute_download_signature(object_key: str, expires_at: int) -> str:
    payload = f"{object_key}\n{expires_at}"
    return hmac.new(_signing_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()


def verify_download_signature(object_key: str, expires_at: int, signature: str) -> bool:
    if expires_at < int(time.time()):
        return False
    expected = compute_download_signature(object_key, expires_at)
    return hmac.compare_digest(expected, signature or "")


def build_object_download_url(
    object_key: str,
    *,
    filename: str | None = None,
    expires: int = DEFAULT_ARTIFACT_EXPIRES,
) -> str | None:
    """生成用户可访问的下载 URL（同源 /api 或 PUBLIC_APP_URL 绝对地址）。"""
    if not object_key or not str(object_key).strip():
        return None

    key = str(object_key).strip()
    exp = int(time.time()) + int(expires)
    sig = compute_download_signature(key, exp)
    params: dict[str, str] = {"key": key, "exp": str(exp), "sig": sig}
    if filename:
        params["filename"] = filename

    query = urlencode(params, quote_via=quote)
    path = f"/api/artifacts/download/?{query}"
    base = (get_settings().PUBLIC_APP_URL or "").rstrip("/")
    return f"{base}{path}" if base else path

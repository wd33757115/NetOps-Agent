# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Redis token store 单元测试（mock Redis）。"""

from unittest.mock import MagicMock, patch

import jwt

from src.auth import token_store
from src.common.config import get_settings


def _reset_redis_cache():
    token_store._redis_client = None
    token_store._redis_checked = False
    token_store._redis_next_retry_at = 0.0


def test_is_auth_token_revoked_blacklisted_jti():
    _reset_redis_cache()
    mock_client = MagicMock()
    mock_client.exists.return_value = 1

    with patch.object(token_store, "get_redis", return_value=mock_client):
        assert token_store.is_auth_token_revoked("jti-abc", None) is True
    mock_client.exists.assert_called_with("jwt:blacklist:jti-abc")


def test_is_auth_token_revoked_session():
    _reset_redis_cache()
    mock_client = MagicMock()

    def exists_side_effect(key):
        return key == "jwt:session:revoked:sess-1"

    mock_client.exists.side_effect = exists_side_effect

    with patch.object(token_store, "get_redis", return_value=mock_client):
        assert token_store.is_auth_token_revoked(None, "sess-1") is True
        assert token_store.is_auth_token_revoked("jti-x", "sess-1") is True


def test_blacklist_token_writes_jti():
    _reset_redis_cache()
    settings = get_settings()
    payload = {
        "jti": "refresh-jti-1",
        "exp": 9999999999,
        "session_id": "sess-99",
    }
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    mock_client = MagicMock()
    with patch.object(token_store, "get_redis", return_value=mock_client):
        assert token_store.blacklist_token(token, "refresh") is True

    mock_client.setex.assert_any_call(
        "jwt:blacklist:refresh-jti-1", mock_client.setex.call_args_list[0][0][1], "refresh"
    )
    mock_client.setex.assert_any_call("jwt:session:revoked:sess-99", 7 * 86400, "1")


def test_check_login_blocked():
    _reset_redis_cache()
    mock_client = MagicMock()
    mock_client.get.return_value = "5"
    mock_client.ttl.return_value = 120

    with patch.object(token_store, "get_redis", return_value=mock_client):
        blocked, retry = token_store.check_login_blocked("1.2.3.4", "admin")
        assert blocked is True
        assert retry == 120


def test_record_login_failure_increments():
    _reset_redis_cache()
    mock_pipe = MagicMock()
    mock_client = MagicMock()
    mock_client.pipeline.return_value = mock_pipe

    with patch.object(token_store, "get_redis", return_value=mock_client):
        token_store.record_login_failure("1.2.3.4", "admin")

    assert mock_pipe.incr.call_count == 2
    assert mock_pipe.expire.call_count == 2
    mock_pipe.execute.assert_called_once()


def test_degraded_when_redis_unavailable():
    _reset_redis_cache()
    with patch.object(token_store, "get_redis", return_value=None):
        assert token_store.is_token_blacklisted("any-jti") is False
        assert token_store.is_session_revoked("sess-1") is False
        blocked, retry = token_store.check_login_blocked("1.1.1.1", "u")
        assert blocked is False
        assert retry == 0


def test_get_redis_retries_after_initial_connection_failure():
    _reset_redis_cache()
    unavailable = MagicMock()
    unavailable.ping.side_effect = ConnectionError("redis starting")
    available = MagicMock()

    with (
        patch("redis.Redis", side_effect=[unavailable, available]) as redis_factory,
        patch.object(token_store.time, "monotonic", side_effect=[100.0, 102.0, 106.0]),
    ):
        assert token_store.get_redis() is None
        assert token_store.get_redis() is None
        assert token_store.get_redis() is available

    assert redis_factory.call_count == 2
    available.ping.assert_called_once_with()

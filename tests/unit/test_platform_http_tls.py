import ssl
from unittest.mock import Mock

import pytest

from src.platform import http_tls


@pytest.fixture(autouse=True)
def reset_context(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    http_tls.client_tls_context.cache_clear()
    yield
    http_tls.client_tls_context.cache_clear()


def test_context_reuses_trust_configuration_without_disabling_verification(monkeypatch):
    create = Mock(wraps=ssl.create_default_context)
    monkeypatch.setattr(http_tls.ssl, "create_default_context", create)
    context = http_tls.client_tls_context()
    assert http_tls.client_tls_context() is context
    assert create.call_count == 1
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname


@pytest.mark.parametrize("variable,option", [("SSL_CERT_FILE", "cafile"), ("SSL_CERT_DIR", "capath")])
def test_custom_trust_roots_are_respected(monkeypatch, variable, option):
    create = Mock()
    monkeypatch.setattr(http_tls.ssl, "create_default_context", create)
    monkeypatch.setenv(variable, "custom-roots")
    assert http_tls.client_tls_context() is create.return_value
    create.assert_called_once_with(**{option: "custom-roots"})

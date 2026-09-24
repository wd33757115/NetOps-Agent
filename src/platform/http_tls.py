"""Reusable trust configuration; clients, credentials and decisions stay request-scoped."""

import os
import ssl
from functools import lru_cache

import certifi


@lru_cache(maxsize=1)
def client_tls_context() -> ssl.SSLContext:
    """Match HTTPX's default trusted roots without loading them for every request.

    This caches no connections or authorization. Certificate environment changes,
    like other service configuration changes, take effect on service restart.
    """
    if os.environ.get("SSL_CERT_FILE"):
        return ssl.create_default_context(cafile=os.environ["SSL_CERT_FILE"])
    if os.environ.get("SSL_CERT_DIR"):
        return ssl.create_default_context(capath=os.environ["SSL_CERT_DIR"])
    return ssl.create_default_context(cafile=certifi.where())

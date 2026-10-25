# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""将 async BFF 视图暴露为 Django 同步视图（Daphne 兼容）。"""

from __future__ import annotations

from asgiref.sync import async_to_sync
from functools import wraps
from typing import Callable, TypeVar
from django.db import connections

F = TypeVar("F", bound=Callable)


def release_proxy_connections():
    """Do not hold authentication DB connections while awaiting the Gateway.

    Its authorization callback needs another connection. Preserve explicit
    transactions (including TestCase); close only on the owning sync thread.
    """
    for connection in connections.all(initialized_only=True):
        if not connection.in_atomic_block:
            connection.close()


def sync_bff_view(async_view: F) -> F:
    @wraps(async_view)
    def wrapper(*args, **kwargs):
        release_proxy_connections()
        return async_to_sync(async_view)(*args, **kwargs)

    return wrapper  # type: ignore[return-value]

# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from django.urls import re_path

from . import consumers
from .terminal_consumer import TerminalProxyConsumer

websocket_urlpatterns = [
    re_path(r"ws/v1/terminals/(?P<session_id>[a-f0-9]{32})$", TerminalProxyConsumer.as_asgi()),
    re_path(r"ws/v1/chat$", consumers.ChatProxyConsumer.as_asgi()),
]

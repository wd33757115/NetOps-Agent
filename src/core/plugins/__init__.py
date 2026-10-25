# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 插件加载（ITSM Webhook、聊天意图等）。"""

from src.core.plugins.chat_intent import ChatIntentRegistry, match_chat_workflow
from src.core.plugins.context_mapping import map_request_to_context
from src.core.plugins.itsm_webhook import ITSMWebhookRegistry, get_itsm_webhook_registry

__all__ = [
    "ChatIntentRegistry",
    "ITSMWebhookRegistry",
    "get_itsm_webhook_registry",
    "map_request_to_context",
    "match_chat_workflow",
]

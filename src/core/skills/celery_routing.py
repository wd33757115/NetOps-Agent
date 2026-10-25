# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Skill → Celery Queue 路由（基于 Catalog domain / celery_queue）。"""

from __future__ import annotations

from src.skill_system.catalog.repository import get_catalog_entry

DOMAIN_QUEUE_MAP: dict[str, str] = {
    "security": "netops.firewall",
    "network": "netops.device",
    "itsm": "netops.default",
    "general": "netops.default",
    "default": "netops.default",
}

ROLE_QUEUE_MAP: dict[str, str] = {
    "device-patrol": "netops.patrol",
    "state-normalizer": "netops.patrol",
    "change-detector": "netops.patrol",
    "event-builder": "netops.patrol",
    "patrol-raw-ingestor": "netops.ai",
    "patrol-attested-offline-replay": "netops.patrol",
    "report-generator": "netops.patrol",
    "textfsm-generator": "netops.ai",
    "change-policy-generator": "netops.ai",
    "event-rule-generator": "netops.ai",
    "official-document-writing": "netops.ai",
    "official_document": "netops.ai",
}


def resolve_skill_celery_queue(skill_name: str) -> str:
    role_queue = ROLE_QUEUE_MAP.get(str(skill_name or "").strip())
    if role_queue:
        return role_queue
    entry = get_catalog_entry(skill_name)
    if entry:
        if entry.get("celery_queue"):
            return str(entry["celery_queue"])
        domain = str(entry.get("domain") or "default").lower()
        return DOMAIN_QUEUE_MAP.get(domain, "netops.default")
    return DOMAIN_QUEUE_MAP["default"]

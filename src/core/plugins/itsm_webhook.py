# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""ITSM Webhook 插件注册表（扫描 **/ITSM.webhook.yaml）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.core.workflows.registry import WORKFLOWS_ROOT

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ITSMWebhookPlugin:
    route_key: str
    workflow: str
    context_mapping: dict[str, str]
    plugin_dir: Path
    description: str = ""
    legacy_paths: list[str] = field(default_factory=list)
    accepted_message: str = "Workflow 已启动"


class ITSMWebhookRegistry:
    def __init__(self) -> None:
        self._by_route: dict[str, ITSMWebhookPlugin] = {}
        self._by_legacy: dict[str, ITSMWebhookPlugin] = {}

    def load(self, force: bool = False) -> None:
        if self._by_route and not force:
            return
        self._by_route.clear()
        self._by_legacy.clear()
        if not WORKFLOWS_ROOT.is_dir():
            return
        for path in sorted(WORKFLOWS_ROOT.rglob("ITSM.webhook.yaml")):
            plugin = self._parse(path)
            if not plugin:
                continue
            self._by_route[plugin.route_key] = plugin
            for legacy in plugin.legacy_paths:
                self._by_legacy[legacy.rstrip("/")] = plugin
            logger.info("已加载 ITSM Webhook 插件: %s → %s", plugin.route_key, plugin.workflow)

    def _parse(self, path: Path) -> ITSMWebhookPlugin | None:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            logger.warning("解析 ITSM.webhook.yaml 失败 %s: %s", path, exc)
            return None
        route_key = raw.get("route_key")
        workflow = raw.get("workflow")
        if not route_key or not workflow:
            return None
        legacy = list(raw.get("legacy_paths") or [])
        route_path = f"/api/v1/itsm/webhook/{route_key}"
        if route_path not in legacy:
            legacy.append(route_path)
        return ITSMWebhookPlugin(
            route_key=str(route_key),
            workflow=str(workflow),
            context_mapping=dict(raw.get("context_mapping") or {}),
            plugin_dir=path.parent,
            description=str(raw.get("description") or ""),
            legacy_paths=legacy,
            accepted_message=str(raw.get("accepted_message") or "Workflow 已启动"),
        )

    def get_by_route(self, route_key: str) -> ITSMWebhookPlugin | None:
        self.load()
        return self._by_route.get(route_key)

    def get_by_path(self, path: str) -> ITSMWebhookPlugin | None:
        self.load()
        normalized = path.rstrip("/")
        if normalized in self._by_legacy:
            return self._by_legacy[normalized]
        for legacy, plugin in self._by_legacy.items():
            if normalized.endswith(legacy) or legacy.endswith(normalized):
                return plugin
        return None

    def all_plugins(self) -> list[ITSMWebhookPlugin]:
        self.load()
        return list(self._by_route.values())


_registry = ITSMWebhookRegistry()


def get_itsm_webhook_registry() -> ITSMWebhookRegistry:
    return _registry

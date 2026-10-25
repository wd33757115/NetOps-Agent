# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""声明式请求体 → Workflow context 映射（点路径 / $.jsonpath 简写）。"""

from __future__ import annotations

from typing import Any


def _get_by_path(data: Any, path: str) -> Any:
    if not path:
        return None
    path = path.strip()
    if path.startswith("$."):
        path = path[2:]
    cur = data
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def map_request_to_context(body: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    ctx: dict[str, Any] = {}
    for target_key, source_path in (mapping or {}).items():
        val = _get_by_path(body, source_path)
        if val is not None and val != "":
            ctx[target_key] = val
    return ctx


def map_state_to_context(state: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    ctx: dict[str, Any] = {}
    for target_key, state_key in (mapping or {}).items():
        if state_key == "query":
            messages = state.get("messages") or []
            val = messages[-1].content if messages else ""
        else:
            val = state.get(state_key)
        if val is not None and val != "":
            ctx[target_key] = val
    return ctx

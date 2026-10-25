# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""声明式 Skill 链式参数解析（上游 execution → 下游 inputs）。"""

from __future__ import annotations

from typing import Any

# 已知 Skill 链：下游 skill_name -> {上游 skill: [要合并的字段]}
KNOWN_SKILL_CHAIN_FIELDS: dict[str, dict[str, tuple[str, ...]]] = {
    "itsm-change-ticket-writer": {
        "firewall-policy-generator": (
            "manifest",
            "config_file_key",
            "config_files_url",
            "download_url",
            "filename",
        ),
    },
}


def _extract_dep_payload(dep_result: dict[str, Any]) -> dict[str, Any]:
    """从 intermediate_results 条目提取可用于链式传参的扁平字段。"""
    payload: dict[str, Any] = dict(dep_result)
    data = dep_result.get("data")
    if isinstance(data, dict):
        for key, val in data.items():
            payload.setdefault(key, val)
    output = dep_result.get("output")
    if isinstance(output, dict):
        for key, val in output.items():
            payload.setdefault(key, val)
    artifacts = dep_result.get("artifacts") or {}
    if isinstance(artifacts, dict):
        zip_art = artifacts.get("config_zip")
        if isinstance(zip_art, dict):
            payload.setdefault("config_file_key", zip_art.get("file_key"))
            payload.setdefault("download_url", zip_art.get("download_url"))
            payload.setdefault("config_files_url", zip_art.get("download_url"))
            payload.setdefault("filename", zip_art.get("filename"))
    return payload


def merge_upstream_params(
    target_skill: str,
    params: dict[str, Any],
    depends_on: list[str],
    intermediate_results: dict[str, Any] | None,
) -> dict[str, Any]:
    """将上游 Skill 结果字段合并到下游 params（保留 legacy dep_output）。"""
    merged = dict(params)
    if not intermediate_results:
        return merged

    chain_map = KNOWN_SKILL_CHAIN_FIELDS.get(target_skill, {})

    for dep in depends_on:
        dep_result = intermediate_results.get(dep)
        if not isinstance(dep_result, dict):
            continue
        merged.setdefault(f"{dep}_output", dep_result)
        if dep_result.get("data"):
            merged.setdefault("previous_data", dep_result["data"])

        fields = chain_map.get(dep)
        if not fields:
            continue
        payload = _extract_dep_payload(dep_result)
        for field in fields:
            if field in payload and payload[field] is not None:
                merged.setdefault(field, payload[field])

    return merged

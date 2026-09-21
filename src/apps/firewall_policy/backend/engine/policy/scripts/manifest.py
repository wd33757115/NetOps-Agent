# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""从防火墙策略输出目录构建 manifest.json。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load_topology_firewalls(topology_path: str | None) -> dict[str, dict[str, Any]]:
    if not topology_path or not os.path.exists(topology_path):
        return {}
    try:
        with open(topology_path, encoding="utf-8") as f:
            data = json.load(f)
        return {fw["name"]: fw for fw in data.get("firewalls", []) if fw.get("name")}
    except Exception:
        return {}


def _guess_vendor(fw_name: str, firewalls: dict[str, dict]) -> str:
    meta = firewalls.get(fw_name) or {}
    return str(meta.get("type") or "未知")


def _guess_rollback_lines(vendor: str, commands: list[str]) -> list[str]:
    rollback: list[str] = []
    for line in commands:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("acl number"):
            m = re.search(r"acl number (\d+)", stripped)
            if m:
                rollback.append(f"undo acl number {m.group(1)}")
        elif stripped.startswith("security-policy"):
            rollback.append("undo security-policy")
        elif stripped.startswith("object-group"):
            rollback.append(f"undo {stripped.split()[0]} {stripped.split()[1]}")
    if not rollback and vendor in ("华为", "H3C", "H3CF1000"):
        rollback.append("rollback configuration")
    return rollback[:20]


def build_manifest_from_output(
    output_dir: str,
    *,
    ticket_id: str,
    ticket_title: str = "",
    change_background: str = "",
    change_purpose: str = "",
    requester: str = "",
    requester_dept: str = "",
    assignee: str = "",
    priority: str = "P2",
    due_date: str | None = None,
    topology_path: str | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    firewalls = _load_topology_firewalls(topology_path)
    devices: list[dict[str, Any]] = []
    scripts: list[dict[str, Any]] = []
    rollback_rows: list[dict[str, Any]] = []
    order = 0

    for root, _dirs, files in os.walk(output_dir):
        for fname in sorted(files):
            if not fname.lower().endswith((".txt", ".cfg", ".conf")):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                continue
            device_name = Path(fname).stem
            vendor = _guess_vendor(device_name, firewalls)
            fw_meta = firewalls.get(device_name) or {}
            cmd_lines = [ln for ln in content.splitlines() if ln.strip()]
            order += 1
            devices.append(
                {
                    "device_name": device_name,
                    "ip_address": fw_meta.get("ip") or "",
                    "vendor": vendor,
                    "model": fw_meta.get("model") or "",
                    "version": fw_meta.get("version") or "",
                    "before_summary": "（变更前配置摘要待人工补充）",
                    "after_summary": f"新增/调整 {len(cmd_lines)} 条配置命令",
                }
            )
            scripts.append(
                {
                    "device_name": device_name,
                    "vendor": vendor,
                    "order": order,
                    "commands": content,
                    "command_count": len(cmd_lines),
                }
            )
            rb_cmds = _guess_rollback_lines(vendor, cmd_lines)
            for idx, rb in enumerate(rb_cmds, start=1):
                rollback_rows.append(
                    {
                        "step": len(rollback_rows) + 1,
                        "device_name": device_name,
                        "rollback_command": rb,
                        "expected_effect": "回退本条变更",
                        "duration": "5 分钟",
                        "executor": assignee or requester or "运维",
                    }
                )

    risk = "中" if len(scripts) <= 3 else "高"
    return {
        "version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ticket_id": ticket_id,
        "ticket_title": ticket_title,
        "change_background": change_background or ticket_title,
        "change_purpose": change_purpose or "开通防火墙策略",
        "requester": requester,
        "requester_dept": requester_dept,
        "assignee": assignee,
        "priority": priority,
        "due_date": due_date,
        "device_count": len(devices),
        "change_type": "配置变更",
        "risk_level": risk,
        "trace_id": trace_id,
        "devices": devices,
        "scripts": scripts,
        "rollback": rollback_rows,
    }


def write_manifest_file(output_dir: str, manifest: dict[str, Any]) -> str:
    path = os.path.join(output_dir, "manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return path

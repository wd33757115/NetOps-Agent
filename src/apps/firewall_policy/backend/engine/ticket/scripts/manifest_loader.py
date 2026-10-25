# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""从本地 manifest.json 或策略 ZIP 解析结构化数据（Skill 内聚，不依赖 MinIO/平台）。"""

from __future__ import annotations

import io
import json
import os
import zipfile
from typing import Any


def load_manifest(
    *,
    manifest: dict[str, Any] | None = None,
    manifest_path: str | None = None,
    zip_path: str | None = None,
) -> dict[str, Any]:
    if manifest:
        return manifest
    if manifest_path and os.path.isfile(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            return json.load(f)
    if zip_path and os.path.isfile(zip_path):
        with open(zip_path, "rb") as f:
            return _manifest_from_zip_bytes(f.read())
    raise ValueError("无法加载 manifest：请提供 --manifest、--params 中的 manifest 或 --zip")


def _manifest_from_zip_bytes(data: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        if "manifest.json" in zf.namelist():
            with zf.open("manifest.json") as mf:
                return json.loads(mf.read().decode("utf-8"))
        return _manifest_from_zip_entries(zf)


def _manifest_from_zip_entries(zf: zipfile.ZipFile) -> dict[str, Any]:
    scripts = []
    order = 0
    for name in zf.namelist():
        if not name.lower().endswith((".txt", ".cfg", ".conf")):
            continue
        with zf.open(name) as f:
            content = f.read().decode("utf-8", errors="replace")
        device_name = os.path.splitext(os.path.basename(name))[0]
        order += 1
        lines = [ln for ln in content.splitlines() if ln.strip()]
        scripts.append(
            {
                "device_name": device_name,
                "vendor": "未知",
                "order": order,
                "commands": content,
                "command_count": len(lines),
            }
        )
    return {
        "ticket_id": "UNKNOWN",
        "devices": [{"device_name": s["device_name"], "vendor": s["vendor"]} for s in scripts],
        "scripts": scripts,
        "rollback": [],
    }

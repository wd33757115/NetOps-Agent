#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
ITSM 变更工单 Excel 生成 Skill 主程序。

平台通过 subprocess 调用本脚本；Skill 自包含 manifest 解析与 Excel 写入逻辑。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from change_ticket_excel import build_change_ticket_workbook  # noqa: E402
from manifest_loader import load_manifest  # noqa: E402


def _merge_params(manifest: dict, params: dict) -> dict:
    out = dict(manifest)
    for key in (
        "ticket_id",
        "ticket_title",
        "change_background",
        "change_purpose",
        "requester",
        "requester_dept",
        "priority",
        "due_date",
        "assignee",
        "technical_reviewer",
        "reviewer",
        "workflow_run_id",
        "trace_id",
    ):
        val = params.get(key)
        if val:
            out[key] = val
    if params.get("manifest") and isinstance(params["manifest"], dict):
        out.update({k: v for k, v in params["manifest"].items() if v is not None})
    return out


def _resolve_manifest(
    params: dict,
    *,
    manifest_path: str | None,
    zip_path: str | None,
) -> dict:
    if isinstance(params.get("manifest"), dict):
        base = dict(params["manifest"])
    elif params.get("devices") or params.get("scripts"):
        base = dict(params)
    else:
        base = load_manifest(manifest_path=manifest_path, zip_path=zip_path)
    return _merge_params(base, params)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 ITSM 变更工单 Excel")
    parser.add_argument("--manifest", help="manifest.json 本地路径")
    parser.add_argument("--zip", help="防火墙策略 ZIP 本地路径")
    parser.add_argument("--params", help="工单参数字段 JSON 文件路径")
    parser.add_argument("-o", "--output", required=True, help="输出 Excel 路径")
    args = parser.parse_args()

    params: dict = {}
    if args.params:
        with open(args.params, encoding="utf-8-sig") as f:
            params = json.load(f)

    try:
        manifest = _resolve_manifest(params, manifest_path=args.manifest, zip_path=args.zip)
        excel_bytes = build_change_ticket_workbook(
            manifest,
            workflow_run_id=params.get("workflow_run_id"),
        )
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(excel_bytes)
        result = {
            "success": True,
            "message": "变更工单 Excel 已生成",
            "output_path": str(out_path),
            "ticket_id": manifest.get("ticket_id"),
            "device_count": len(manifest.get("devices") or []),
        }
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        err = {"success": False, "message": str(exc), "error": str(exc)}
        print(json.dumps(err, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

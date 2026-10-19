#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""ITSM 回调 Skill 主程序（无平台依赖的业务 HTTP 回调）。"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


def build_callback_payload(
    *,
    ticket_id: str,
    status: str,
    config_zip: dict | None = None,
    change_excel: dict | None = None,
    execution_time_ms: int = 0,
    workflow_run_id: str | None = None,
) -> dict:
    payload: dict = {
        "version": "1.0",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "callback_id": f"cb_{uuid.uuid4().hex}",
        "source_ticket_id": ticket_id,
        "status": status,
        "metadata": {
            "execution_time_ms": execution_time_ms,
            "workflow_run_id": workflow_run_id,
        },
    }
    attachments = []
    if config_zip and config_zip.get("download_url"):
        attachments.append(
            {
                "filename": config_zip.get("filename", "firewall_policies.zip"),
                "download_url": config_zip["download_url"],
                "type": "config",
            }
        )
    if change_excel and change_excel.get("download_url"):
        attachments.append(
            {
                "filename": change_excel.get("filename", "change_ticket.xlsx"),
                "download_url": change_excel["download_url"],
                "type": "change_ticket",
            }
        )
    payload["result"] = {
        "action": "update_ticket",
        "ticket_update": {
            "status": "变更工单已生成",
            "resolution_note": "防火墙策略与变更工单已生成，请按变更流程审批执行",
            "attachments": attachments,
        },
    }
    return payload


def main() -> int:
    import time

    parser = argparse.ArgumentParser(description="ITSM 回调 Skill")
    parser.add_argument("--params", required=True)
    args = parser.parse_args()
    started = time.perf_counter()

    with open(args.params, encoding="utf-8-sig") as f:
        p = json.load(f)

    ticket_id = p.get("ticket_id") or "UNKNOWN"
    callback_url = p.get("callback_url")
    if not callback_url:
        out = {"success": True, "message": "未配置 callback_url，跳过", "callback_status": "skipped"}
        print(json.dumps(out, ensure_ascii=False))
        return 0

    config_zip = {
        "file_key": p.get("config_file_key"),
        "download_url": p.get("config_files_url"),
        "filename": p.get("config_filename") or f"firewall_policies_{ticket_id}.zip",
    }
    change_excel = {
        "file_key": p.get("change_excel_file_key"),
        "download_url": p.get("change_excel_url"),
        "filename": p.get("change_excel_filename") or f"变更工单_{ticket_id}.xlsx",
    }
    payload = build_callback_payload(
        ticket_id=ticket_id,
        status="success",
        config_zip=config_zip,
        change_excel=change_excel,
        execution_time_ms=int((time.perf_counter() - started) * 1000),
        workflow_run_id=p.get("workflow_run_id"),
    )
    # Without PUBLIC_APP_URL the platform signs same-origin paths, which an ITSM system cannot fetch.
    relative = [
        item["filename"]
        for item in payload["result"]["ticket_update"]["attachments"]
        if not str(item["download_url"]).startswith(("http://", "https://"))
    ]
    warning = (
        f"附件下载地址是相对路径（{'、'.join(relative)}），ITSM 系统无法直接下载；"
        "请为平台配置 PUBLIC_APP_URL"
        if relative else None
    )
    headers = {"Content-Type": "application/json", **(p.get("callback_headers") or {})}
    try:
        resp = requests.post(callback_url, json=payload, headers=headers, timeout=30)
        ok = 200 <= resp.status_code < 300
        message = "ITSM 回调成功" if ok else f"ITSM 回调失败: HTTP {resp.status_code}"
        result = {
            "success": ok,
            "message": f"{message}；{warning}" if warning else message,
            "warning": warning,
            "callback_status": "success" if ok else "failed",
            "http_status": resp.status_code,
            "response_body": resp.text[:500],
            "artifacts": {"config_zip": config_zip, "change_excel": change_excel},
            "error": None if ok else resp.text[:500],
        }
        print(json.dumps(result, ensure_ascii=False))
        return 0 if ok else 1
    except requests.RequestException as exc:
        err = {"success": False, "message": str(exc), "error": str(exc), "callback_status": "failed"}
        print(json.dumps(err, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

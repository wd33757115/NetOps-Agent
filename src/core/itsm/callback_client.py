# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""ITSM 回调 HTTP 客户端。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)


def build_callback_payload(
    *,
    ticket_id: str,
    status: str,
    config_zip: dict[str, Any] | None = None,
    change_excel: dict[str, Any] | None = None,
    resolution_note: str = "",
    execution_time_ms: int = 0,
    workflow_run_id: str | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
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
    if status == "success":
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
                "resolution_note": resolution_note
                or "防火墙策略与变更工单已生成，请按变更流程审批执行",
                "attachments": attachments,
            },
        }
    else:
        payload["error"] = error or {
            "code": "WORKFLOW_FAILED",
            "message": "变更流程执行失败",
        }
    return payload


def post_itsm_callback(
    callback_url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> tuple[bool, int, str]:
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    try:
        resp = requests.post(callback_url, json=payload, headers=hdrs, timeout=timeout)
        ok = 200 <= resp.status_code < 300
        return ok, resp.status_code, resp.text[:500]
    except requests.RequestException as exc:
        logger.warning("ITSM 回调失败: %s", exc)
        return False, 0, str(exc)

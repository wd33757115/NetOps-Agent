# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""ITSM 变更工单与回调相关模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChangeTicketContext(BaseModel):
    ticket_id: str
    ticket_title: str = ""
    change_background: str = ""
    change_purpose: str = ""
    requester: str = ""
    requester_dept: str = ""
    assignee: str = ""
    priority: str = "P2"
    due_date: str | None = None
    callback_url: str | None = None
    callback_headers: dict[str, str] | None = None
    workflow_run_id: str | None = None
    trace_id: str | None = None
    user_id: str | None = None
    thread_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

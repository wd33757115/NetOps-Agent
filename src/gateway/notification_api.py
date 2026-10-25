# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""站内通知 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.auth.dependencies import get_optional_user
from src.auth.models import CurrentUser
from src.core.workflows.repository import (
    clear_notifications,
    count_unread_notifications,
    list_notifications,
    mark_notification_read,
)
from src.gateway.schemas import NotificationListResponse, NotificationResponse

router = APIRouter(prefix="/api/v1/notifications", tags=["Notifications"])


@router.get("/", response_model=NotificationListResponse)
async def get_notifications(
    unread_only: bool = False,
    limit: int = 50,
    user: CurrentUser | None = Depends(get_optional_user),
):
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    items = list_notifications(user.user_id, unread_only=unread_only, limit=limit)
    unread = count_unread_notifications(user.user_id)
    return NotificationListResponse(
        unread_count=unread,
        items=[
            NotificationResponse(
                id=n.id,
                title=n.title,
                body=n.body,
                level=n.level,
                payload=n.payload,
                workflow_run_id=n.workflow_run_id,
                thread_id=n.thread_id,
                read_at=n.read_at,
                created_at=n.created_at,
            )
            for n in items
        ],
    )


@router.post("/clear")
async def clear_all_notifications(
    user: CurrentUser | None = Depends(get_optional_user),
):
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    deleted = clear_notifications(user.user_id)
    return {"ok": True, "deleted_count": deleted, "unread_count": 0}


@router.post("/{notification_id}/read")
async def read_notification(
    notification_id: str,
    user: CurrentUser | None = Depends(get_optional_user),
):
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    ok = mark_notification_read(notification_id, user.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="通知不存在")
    return {"ok": True, "unread_count": count_unread_notifications(user.user_id)}

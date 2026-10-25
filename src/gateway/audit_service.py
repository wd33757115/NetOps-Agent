# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""审计日志写入 PostgreSQL。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.infrastructure.db.models import AuditLogRecord
from src.infrastructure.db.postgres import get_db_session


def write_audit_log(
    *,
    action: str,
    user_id: str | None = None,
    username: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict[str, Any] | None = None,
    ip_address: str | None = None,
    status: str = "success",
    idempotency_key: str | None = None,
    raise_on_error: bool = False,
) -> None:
    try:
        with get_db_session() as session:
            values = {
                "id": (
                    str(uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key))
                    if idempotency_key
                    else f"audit-{uuid.uuid4().hex[:16]}"
                ),
                "idempotency_key": idempotency_key,
                "user_id": user_id,
                "username": username,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "detail": detail,
                "ip_address": ip_address,
                "status": status,
                "created_at": datetime.now(timezone.utc),
            }
            if idempotency_key:
                session.execute(
                    pg_insert(AuditLogRecord)
                    .values(**values)
                    .on_conflict_do_nothing(index_elements=["idempotency_key"])
                )
            else:
                session.add(AuditLogRecord(**values))
    except Exception:
        if raise_on_error:
            raise
        # 审计失败不阻断主流程
        pass

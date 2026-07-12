# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Application coordination for patrol delivery, fencing and completion."""

from __future__ import annotations

import uuid
from typing import Any

from src.core.patrol.run_repository import PatrolRunRepository


class PatrolRunCoordinator:
    def __init__(self, repository: Any | None = None) -> None:
        self._repository = repository or PatrolRunRepository()

    def prepare_delivery(
        self,
        request: Any,
        *,
        mode: str = "production",
    ) -> dict[str, Any]:
        retries = int(getattr(request, "retries", 0) or 0)
        delivery_info = dict(getattr(request, "delivery_info", None) or {})
        if not delivery_info.get("redelivered"):
            return {
                "action": "execute",
                "attempt_number": retries,
                "run_id": None,
                "owner_token": uuid.uuid4().hex,
            }
        task_id = str(getattr(request, "id", "") or "")
        if not task_id:
            return {
                "action": "execute",
                "attempt_number": max(1, retries),
                "run_id": None,
                "owner_token": uuid.uuid4().hex,
            }
        return self._repository.prepare_redelivery_attempt(
            f"patrol-{task_id}",
            worker_id=getattr(request, "hostname", None),
            mode=mode,
        ).to_dict()

    def complete(self, run_id: str, *, owner_token: str | None = None) -> None:
        self._repository.mark_celery_task_completed(
            run_id,
            owner_token=owner_token,
        )

    def attempt_number(self, request: Any, *, mode: str = "production") -> int:
        decision = self.prepare_delivery(request, mode=mode)
        return int(decision.get("attempt_number") or 0)

# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Result payload helpers shared by execution runners."""

from __future__ import annotations

from typing import Any


def runner_success_payload(
    *,
    action: str,
    ticket_id: str | None,
    result: Any,
    execution_time_ms: int,
    download_url: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the stable legacy-compatible payload returned by execution runners."""
    payload = {
        "success": True,
        "status": "success",
        "action": action,
        "ticket_id": ticket_id,
        "download_url": download_url,
        "total_devices": result.total_devices,
        "success_devices": result.success_devices,
        "failed_devices": result.failed_devices,
        "output_files": result.output_files,
        "execution_time_ms": execution_time_ms,
        "message": result.message,
    }
    if extra:
        payload.update(extra)
    return payload

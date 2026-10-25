# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Device backup execution runner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from src.core.execution import (
    ArchiveSpec,
    archive_output_files,
    load_inventory_execution_context,
    run_plan_or_filter,
    runner_success_payload,
)
from src.infrastructure.storage.minio_client import get_minio_storage

BACKUP_ARCHIVE_SPEC = ArchiveSpec(
    object_prefix="config_backup",
    zip_prefix="config_backup",
    default_ticket_prefix="BACKUP",
    log_event_prefix="config_backup",
)


@dataclass(slots=True)
class DeviceBackupRunRequest:
    filter_params: dict[str, Any] | None = None
    ticket_id: str | None = None
    execution_plan: dict[str, Any] | None = None
    actor_id: str = "system"
    task_id: str | None = None


class DeviceBackupRunner:
    """Run one device-backup job outside of transport-specific wrappers."""

    def __init__(
        self,
        *,
        tools_loader: Callable[[], Any] | None = None,
        minio_storage_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._tools_loader = tools_loader
        self._minio_storage_factory = minio_storage_factory or get_minio_storage

    def run(self, request: DeviceBackupRunRequest) -> dict[str, Any]:
        execution_start = datetime.now()

        context = load_inventory_execution_context(
            tool_factory_name="ConfigBackupTool",
            filter_params=request.filter_params,
            execution_plan_data=request.execution_plan,
            tools_loader=self._tools_loader,
        )
        collector = getattr(context.tool, "cli_collector", None)
        if collector is not None:
            collector.actor_id = request.actor_id
            collector.source_app = "device-backup"
            collector.task_id = request.task_id
        result = run_plan_or_filter(
            context=context,
            plan_runner=context.tool.backup_by_plan,
            filter_runner=context.tool.backup_by_filter,
        )

        if not result.success:
            raise RuntimeError(result.message or "device backup failed")

        # Optional post-backup config-diff enrichment for paired before/after artifacts.
        config_diff_summary = None
        try:
            from src.core.patrol.config_diff_bridge import compare_config_texts

            outputs = list(result.output_files or [])
            if len(outputs) >= 2:
                before = Path(str(outputs[0])).read_text(encoding="utf-8", errors="replace")
                after = Path(str(outputs[1])).read_text(encoding="utf-8", errors="replace")
                config_diff_summary = compare_config_texts(before, after)
        except Exception:
            config_diff_summary = None

        download_url = self._archive_backup_outputs(
            output_files=result.output_files or [],
            ticket_id=request.ticket_id,
        )

        execution_time_ms = int((datetime.now() - execution_start).total_seconds() * 1000)
        payload = runner_success_payload(
            action="backup",
            ticket_id=request.ticket_id,
            result=result,
            execution_time_ms=execution_time_ms,
            download_url=download_url,
        )
        if config_diff_summary is not None:
            payload["config_diff"] = config_diff_summary
        return payload

    def _archive_backup_outputs(
        self,
        *,
        output_files: list[str],
        ticket_id: str | None,
    ) -> str | None:
        return archive_output_files(
            output_files=output_files,
            ticket_id=ticket_id,
            minio_storage_factory=self._minio_storage_factory,
            spec=BACKUP_ARCHIVE_SPEC,
        )

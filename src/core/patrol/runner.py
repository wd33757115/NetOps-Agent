# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Device patrol execution runner.

The Celery task is intentionally kept as a thin scheduling wrapper. This
module owns the platform-level patrol flow: resolve an execution plan, collect
CLI evidence, persist snapshots, archive raw outputs, and return a stable
workflow payload.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from src.core.collection import ExecutionPlanRepository
from src.core.execution import (
    ArchiveSpec,
    archive_output_files,
    load_inventory_execution_context,
    run_plan_or_filter,
    runner_success_payload,
)
from src.core.patrol.modes import normalize_patrol_mode
from src.infrastructure.storage.minio_client import get_minio_storage

PATROL_ARCHIVE_SPEC = ArchiveSpec(
    object_prefix="device_patrol",
    zip_prefix="device_patrol",
    default_ticket_prefix="PATROL",
    log_event_prefix="device_patrol",
)


def persist_patrol_snapshots(
    *,
    details: list[dict[str, Any]],
    snapshot_run_id: str,
    db_path: str | None = None,
    ticket_id: str | None = None,
    mode: str = "production",
    execution_plan_id: str | None = None,
) -> dict[str, Any]:
    """Import completed patrol raw-output files into the snapshot store."""
    from src.core.patrol.raw_importer import import_raw_capture

    imported_commands = 0
    imported_devices = 0
    import_errors: list[str] = []
    for detail in details:
        output_file = detail.get("output_file")
        if not output_file or not os.path.isfile(str(output_file)):
            continue
        try:
            imported = import_raw_capture(
                file_path=str(output_file),
                mode=mode,
                run_id=snapshot_run_id,
                device_name=detail.get("device_name"),
                ip=detail.get("ip"),
                vendor=detail.get("vendor"),
                model=detail.get("model"),
                metadata={
                    "ticket_id": ticket_id,
                    "source": "device-patrol",
                    "execution_plan_id": execution_plan_id,
                },
            )
            imported_commands += int(imported.get("command_count") or 0)
            imported_devices += 1
        except Exception as exc:
            import_errors.append(f"{output_file}: {exc}")

    return {
        "snapshot_run_id": snapshot_run_id,
        "snapshot_storage": "postgresql",
        "legacy_db_path_ignored": db_path,
        "snapshot_devices": imported_devices,
        "snapshot_commands": imported_commands,
        "snapshot_errors": import_errors,
    }


@dataclass(slots=True)
class DevicePatrolRunRequest:
    filter_params: dict[str, Any] | None = None
    ticket_id: str | None = None
    save_baseline: bool = False
    mode: str = "production"
    execution_plan: dict[str, Any] | None = None
    execution_plan_id: str | None = None
    task_id: str | None = None


class DevicePatrolRunner:
    """Run one device-patrol job outside of transport-specific wrappers."""

    def __init__(
        self,
        *,
        tools_loader: Callable[[], Any] | None = None,
        minio_storage_factory: Callable[[], Any] | None = None,
        snapshot_persister: Callable[..., dict[str, Any]] = persist_patrol_snapshots,
    ) -> None:
        self._tools_loader = tools_loader
        self._minio_storage_factory = minio_storage_factory or get_minio_storage
        self._snapshot_persister = snapshot_persister

    def run(self, request: DevicePatrolRunRequest) -> dict[str, Any]:
        execution_start = datetime.now()
        execution_plan_data = request.execution_plan
        if execution_plan_data is None and request.execution_plan_id:
            plan_record = ExecutionPlanRepository().get_plan(request.execution_plan_id)
            if plan_record is None:
                raise RuntimeError(f"Execution plan not found: {request.execution_plan_id}")
            execution_plan_data = dict(plan_record.get("plan_json") or {})

        context = load_inventory_execution_context(
            tool_factory_name="PatrolTool",
            filter_params=request.filter_params,
            execution_plan_data=execution_plan_data,
            tools_loader=self._tools_loader,
        )
        mode = normalize_patrol_mode(
            context.execution_plan.mode if context.execution_plan else request.mode
        )
        result = run_plan_or_filter(
            context=context,
            plan_runner=lambda plan: context.tool.patrol_by_plan(
                plan,
                request.save_baseline,
            ),
            filter_runner=lambda device_filter: context.tool.patrol_by_filter(
                device_filter,
                request.save_baseline,
                mode=mode,
            ),
        )

        if not result.success:
            raise RuntimeError(result.message or "device patrol failed")

        snapshot_run_id = f"patrol-{request.task_id or uuid.uuid4()}"
        snapshot_result = self._snapshot_persister(
            details=result.details or [],
            snapshot_run_id=snapshot_run_id,
            ticket_id=request.ticket_id,
            mode=mode,
            execution_plan_id=request.execution_plan_id,
        )
        if snapshot_result["snapshot_errors"]:
            raise RuntimeError("; ".join(snapshot_result["snapshot_errors"]))

        raw_outputs_url = self._archive_raw_outputs(
            output_files=result.output_files or [],
            ticket_id=request.ticket_id,
        )

        execution_time_ms = int((datetime.now() - execution_start).total_seconds() * 1000)
        return runner_success_payload(
            action="patrol",
            ticket_id=request.ticket_id,
            result=result,
            execution_time_ms=execution_time_ms,
            download_url=raw_outputs_url,
            extra={
                "save_baseline": request.save_baseline,
                "mode": mode,
                "execution_plan_id": request.execution_plan_id,
                "raw_outputs_url": raw_outputs_url,
                "raw_outputs_archive": raw_outputs_url,
                **snapshot_result,
            },
        )

    def _archive_raw_outputs(
        self,
        *,
        output_files: list[str],
        ticket_id: str | None,
    ) -> str | None:
        return archive_output_files(
            output_files=output_files,
            ticket_id=ticket_id,
            minio_storage_factory=self._minio_storage_factory,
            spec=PATROL_ARCHIVE_SPEC,
        )

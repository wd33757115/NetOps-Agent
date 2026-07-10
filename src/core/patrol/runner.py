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
    run_purpose: str = "scheduled_patrol",
) -> dict[str, Any]:
    """Import completed patrol raw-output files into the snapshot store."""
    from src.core.patrol.raw_importer import import_raw_capture
    from src.core.patrol.repository import PatrolRepository

    repository = PatrolRepository()
    repository.create_run(
        run_id=snapshot_run_id,
        source="device-patrol",
        mode=mode,
        metadata={
            "ticket_id": ticket_id,
            "execution_plan_id": execution_plan_id,
            "requested_device_count": len(details),
        },
        run_purpose=run_purpose,
    )

    imported_commands = 0
    imported_devices = 0
    import_errors: list[str] = []
    parsed_commands = 0
    failed_parse_commands = 0
    coverage_violations: list[dict[str, Any]] = []
    parse_violations: list[dict[str, Any]] = []
    production_parser_ready = True
    for detail in details:
        output_file = detail.get("output_file")
        if not output_file or not os.path.isfile(str(output_file)):
            plan = detail.get("execution_plan") or {}
            state_device_id = str(
                detail.get("inventory_device_id")
                or detail.get("device_name")
                or detail.get("ip")
                or "unknown"
            )
            for item in plan.get("collection_items") or []:
                command = item.get("command") if isinstance(item, dict) else item
                if command:
                    repository.save_collection_result(
                        run_id=snapshot_run_id,
                        inventory_device_id=detail.get("inventory_device_id"),
                        device_id=state_device_id,
                        canonical_command=str(command),
                        status="failed",
                        failure_code="collection_failed",
                        metadata={"error": detail.get("error")},
                    )
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
                    "run_purpose": run_purpose,
                },
                inventory_device_id=detail.get("inventory_device_id"),
                collector_complete=detail.get("collection_status") == "complete",
                finalize_run=False,
            )
            imported_commands += int(imported.get("command_count") or 0)
            imported_devices += 1
            production_parser_ready = production_parser_ready and bool(
                imported.get("production_parser_ready", True)
            )
            coverage_violations.extend(imported.get("coverage_violations") or [])
            parse_violations.extend(imported.get("parse_violations") or [])
            for parser_result in imported.get("parser_results") or []:
                parse_status = str(parser_result.get("parse_status") or "")
                if parse_status in {"parsed", "parse_empty"}:
                    parsed_commands += 1
                elif parse_status in {
                    "parse_failed",
                    "template_missing",
                    "identity_unknown",
                }:
                    failed_parse_commands += 1
        except Exception as exc:
            import_errors.append(f"{output_file}: {exc}")

    snapshot_health = "ready"
    if import_errors and imported_devices == 0:
        snapshot_health = "failed"
    elif import_errors:
        snapshot_health = "partial"
    elif coverage_violations or parse_violations:
        snapshot_health = "partial"
        if imported_commands > 0 and parsed_commands == 0:
            snapshot_health = "failed_data_quality"

    failed_devices = sum(not bool(detail.get("success")) for detail in details)
    collection_status = "completed"
    if imported_devices == 0:
        collection_status = "failed"
    elif failed_devices or import_errors or snapshot_health != "ready":
        collection_status = "partial"
    repository.finish_run(
        snapshot_run_id,
        collection_status=collection_status,
        analysis_status="pending",
        baseline_eligible=False,
        metadata={
            "successful_device_count": imported_devices,
            "failed_device_count": failed_devices,
            "snapshot_health": snapshot_health,
        },
    )

    return {
        "snapshot_run_id": snapshot_run_id,
        "snapshot_storage": "postgresql",
        "legacy_db_path_ignored": db_path,
        "snapshot_devices": imported_devices,
        "snapshot_commands": imported_commands,
        "snapshot_errors": import_errors,
        "snapshot_health": snapshot_health,
        "collection_status": collection_status,
        "parsed_commands": parsed_commands,
        "failed_parse_commands": failed_parse_commands,
        "coverage_violations": coverage_violations,
        "parse_violations": parse_violations,
        "production_parser_ready": production_parser_ready,
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

        snapshot_run_id = f"patrol-{request.task_id or uuid.uuid4()}"
        snapshot_result = self._snapshot_persister(
            details=result.details or [],
            snapshot_run_id=snapshot_run_id,
            ticket_id=request.ticket_id,
            mode=mode,
            execution_plan_id=request.execution_plan_id,
            run_purpose=("baseline" if request.save_baseline else "scheduled_patrol"),
        )
        raw_outputs_url = self._archive_raw_outputs(
            output_files=result.output_files or [],
            ticket_id=request.ticket_id,
        )
        if int(result.success_devices or 0) == 0:
            raise RuntimeError(result.message or "device patrol failed")
        if snapshot_result["snapshot_errors"] and not snapshot_result.get(
            "snapshot_devices"
        ):
            raise RuntimeError("; ".join(snapshot_result["snapshot_errors"]))

        execution_time_ms = int((datetime.now() - execution_start).total_seconds() * 1000)
        status = "success"
        if snapshot_result.get("snapshot_health") in {"partial", "failed_data_quality"}:
            status = str(snapshot_result["snapshot_health"])
        if not result.success:
            status = "partial"
        return runner_success_payload(
            action="patrol",
            ticket_id=request.ticket_id,
            result=result,
            execution_time_ms=execution_time_ms,
            download_url=raw_outputs_url,
            extra={
                "save_baseline": request.save_baseline,
                "mode": mode,
                "status": status,
                "data_quality_status": snapshot_result.get("snapshot_health", "ready"),
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

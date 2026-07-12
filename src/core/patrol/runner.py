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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

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


class PatrolPlatformError(RuntimeError):
    """Retryable platform failure outside an individual device collection."""


def persist_patrol_snapshots(
    *,
    details: list[dict[str, Any]],
    snapshot_run_id: str,
    db_path: str | None = None,
    ticket_id: str | None = None,
    mode: str = "production",
    execution_plan_id: str | None = None,
    run_purpose: str = "scheduled_patrol",
    started_at: str | datetime | None = None,
    create_run: bool = True,
    finalize_run: bool = True,
    run_owner_token: str | None = None,
    parser_runtime_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Import completed patrol raw-output files into the snapshot store."""
    from src.core.patrol.raw_importer import import_raw_capture
    from src.core.patrol.repository import PatrolCollectionResultRepository
    from src.core.patrol.run_repository import PatrolRunRepository

    run_repository = PatrolRunRepository()
    collection_repository = PatrolCollectionResultRepository()
    if create_run:
        run_repository.create_run(
            run_id=snapshot_run_id,
            source="device-patrol",
            mode=mode,
            metadata={
                "ticket_id": ticket_id,
                "execution_plan_id": execution_plan_id,
                "requested_device_count": len(details),
                "parser_runtime_bundle_digest": (
                    parser_runtime_bundle.get("digest") if parser_runtime_bundle else None
                ),
            },
            run_purpose=run_purpose,
            started_at=started_at,
            owner_token=run_owner_token,
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
        if (
            not output_file
            or not os.path.isfile(str(output_file))
            or os.path.getsize(str(output_file)) == 0
        ):
            plan = detail.get("execution_plan") or {}
            state_device_id = str(
                detail.get("inventory_device_id")
                or detail.get("device_name")
                or detail.get("ip")
                or "unknown"
            )
            items = detail.get("command_collection_results") or plan.get("collection_items") or []
            for item in items:
                command = item.get("command") if isinstance(item, dict) else item
                if command:
                    item_status = (
                        str(item.get("status") or "failed") if isinstance(item, dict) else "failed"
                    )
                    collection_repository.save_collection_result(
                        run_id=snapshot_run_id,
                        inventory_device_id=detail.get("inventory_device_id"),
                        device_id=state_device_id,
                        canonical_command=str(command),
                        status=item_status,
                        execution_status=item_status,
                        parse_status="not_attempted",
                        failure_code=item_status,
                        observed_at=(item.get("observed_at") if isinstance(item, dict) else None),
                        execution_item_id=(
                            item.get("execution_item_id") if isinstance(item, dict) else None
                        ),
                        metadata={
                            "error": (
                                item.get("error") if isinstance(item, dict) else detail.get("error")
                            )
                        },
                        owner_token=run_owner_token,
                    )
            continue
        try:
            command_statuses = [
                item
                for item in detail.get("command_collection_results") or []
                if isinstance(item, dict) and item.get("command")
            ]
            imported = import_raw_capture(
                file_path=str(output_file),
                mode=mode,
                run_owner_token=run_owner_token,
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
                command_collection_statuses=command_statuses,
                parser_runtime_bundle=parser_runtime_bundle,
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
            state_device_id = str(
                detail.get("inventory_device_id")
                or imported.get("device_id")
                or detail.get("device_name")
                or "unknown"
            )
            for command_result in command_statuses:
                command = str(command_result.get("command") or "")
                if command_result.get("status") == "complete":
                    continue
                collection_repository.save_collection_result(
                    run_id=snapshot_run_id,
                    inventory_device_id=detail.get("inventory_device_id"),
                    device_id=state_device_id,
                    canonical_command=command,
                    status=str(command_result.get("status") or "failed"),
                    execution_status=str(command_result.get("status") or "failed"),
                    parse_status="not_attempted",
                    failure_code=str(command_result.get("status") or "collection_failed"),
                    observed_at=command_result.get("observed_at"),
                    execution_item_id=command_result.get("execution_item_id"),
                    metadata={"error": command_result.get("error")},
                    owner_token=run_owner_token,
                )
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
    elif failed_devices or import_errors:
        collection_status = "partial"
    parser_readiness_status = (
        "ready"
        if production_parser_ready
        else "blocked"
        if imported_commands > 0 and parsed_commands == 0
        else "partial"
    )
    if finalize_run:
        run_repository.finish_run(
            snapshot_run_id,
            collection_status=collection_status,
            parser_readiness_status=parser_readiness_status,
            fact_readiness_status=parser_readiness_status,
            analysis_status="pending",
            baseline_eligible=False,
            metadata={
                "successful_device_count": imported_devices,
                "failed_device_count": failed_devices,
                "snapshot_health": snapshot_health,
            },
            owner_token=run_owner_token,
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
        "parser_readiness_status": parser_readiness_status,
        "fact_readiness_status": parser_readiness_status,
        "parsed_commands": parsed_commands,
        "failed_parse_commands": failed_parse_commands,
        "coverage_violations": coverage_violations,
        "parse_violations": parse_violations,
        "production_parser_ready": production_parser_ready,
    }


def _combine_snapshot_results(
    *,
    snapshot_run_id: str,
    details: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = [
        dict(detail.get("snapshot_persistence") or {})
        for detail in details
        if detail.get("snapshot_persistence")
    ]
    imported_devices = sum(int(row.get("snapshot_devices") or 0) for row in rows)
    imported_commands = sum(int(row.get("snapshot_commands") or 0) for row in rows)
    errors = [error for row in rows for error in row.get("snapshot_errors") or []]
    coverage = [item for row in rows for item in row.get("coverage_violations") or []]
    parse = [item for row in rows for item in row.get("parse_violations") or []]
    failed_devices = sum(not bool(detail.get("success")) for detail in details)
    health = "ready"
    if errors and imported_devices == 0:
        health = "failed"
    elif errors or coverage or parse or failed_devices:
        health = "partial"
    collection_status = (
        "failed" if imported_devices == 0 else "partial" if health != "ready" else "completed"
    )
    return {
        "snapshot_run_id": snapshot_run_id,
        "snapshot_storage": "postgresql",
        "legacy_db_path_ignored": None,
        "snapshot_devices": imported_devices,
        "snapshot_commands": imported_commands,
        "snapshot_errors": errors,
        "snapshot_health": health,
        "collection_status": collection_status,
        "parsed_commands": sum(int(row.get("parsed_commands") or 0) for row in rows),
        "failed_parse_commands": sum(int(row.get("failed_parse_commands") or 0) for row in rows),
        "coverage_violations": coverage,
        "parse_violations": parse,
        "production_parser_ready": all(
            bool(row.get("production_parser_ready", True)) for row in rows
        ),
        "failed_devices": failed_devices,
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
    attempt_number: int = 0
    worker_id: str | None = None
    owner_token: str | None = None


class DevicePatrolRunner:
    """Run one device-patrol job outside of transport-specific wrappers."""

    def __init__(
        self,
        *,
        tools_loader: Callable[[], Any] | None = None,
        minio_storage_factory: Callable[[], Any] | None = None,
        snapshot_persister: Callable[..., dict[str, Any]] = persist_patrol_snapshots,
        runtime_bundle_factory: Callable[[Any], Any | None] | None = None,
    ) -> None:
        self._tools_loader = tools_loader
        self._minio_storage_factory = minio_storage_factory or get_minio_storage
        self._snapshot_persister = snapshot_persister
        self._runtime_bundle_factory = runtime_bundle_factory or _build_parser_runtime_bundle

    def run(self, request: DevicePatrolRunRequest) -> dict[str, Any]:
        execution_start = datetime.now(timezone.utc)
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
        collection_started_at = datetime.now(timezone.utc)
        from src.common.config import get_settings

        settings = get_settings()
        logical_run_id = f"patrol-{request.task_id or uuid.uuid4()}"
        attempt_number = max(0, int(request.attempt_number or 0))
        snapshot_run_id = (
            logical_run_id if attempt_number == 0 else f"{logical_run_id}-attempt-{attempt_number}"
        )
        run_purpose = "baseline" if request.save_baseline else "scheduled_patrol"
        streaming_persistence = self._snapshot_persister is persist_patrol_snapshots
        runtime_bundle = (
            self._runtime_bundle_factory(context.execution_plan)
            if streaming_persistence and context.execution_plan is not None
            else None
        )
        runtime_bundle_data = runtime_bundle.to_dict() if runtime_bundle is not None else None
        owner_token = request.owner_token or uuid.uuid4().hex
        if streaming_persistence:
            from src.core.patrol.run_repository import PatrolRunRepository

            PatrolRunRepository().create_run(
                run_id=snapshot_run_id,
                source="device-patrol",
                mode=mode,
                metadata={
                    "ticket_id": request.ticket_id,
                    "execution_plan_id": request.execution_plan_id,
                    "parser_runtime_bundle_digest": (
                        runtime_bundle.digest if runtime_bundle is not None else None
                    ),
                },
                run_purpose=run_purpose,
                started_at=collection_started_at,
                logical_run_id=logical_run_id,
                attempt_number=attempt_number,
                worker_id=request.worker_id,
                lease_seconds=settings.PATROL_RUN_LEASE_SECONDS,
                owner_token=owner_token,
                owner_generation=attempt_number + 1,
            )

            def persist_device_result(detail: dict[str, Any]) -> dict[str, Any]:
                persisted = self._snapshot_persister(
                    details=[detail],
                    snapshot_run_id=snapshot_run_id,
                    ticket_id=request.ticket_id,
                    mode=mode,
                    execution_plan_id=request.execution_plan_id,
                    run_purpose=run_purpose,
                    create_run=False,
                    finalize_run=False,
                    run_owner_token=owner_token,
                    parser_runtime_bundle=runtime_bundle_data,
                )
                PatrolRunRepository().touch_run(
                    snapshot_run_id,
                    lease_seconds=settings.PATROL_RUN_LEASE_SECONDS,
                    worker_id=request.worker_id,
                    owner_token=owner_token,
                    metadata={"last_persisted_device": detail.get("device_name")},
                )
                return persisted

            def persist_command_progress(*, output_file, manifest, device, execution_plan) -> None:
                self._snapshot_persister(
                    details=[
                        {
                            "inventory_device_id": device.get("device_id"),
                            "device_name": device.get("device_name"),
                            "ip": device.get("ip") or device.get("management_ip"),
                            "vendor": device.get("vendor"),
                            "model": device.get("model"),
                            "success": False,
                            "output_file": str(output_file),
                            "collection_status": str(manifest.get("status") or "running"),
                            "command_collection_results": list(manifest.get("commands") or []),
                            "execution_plan": execution_plan.to_metadata(),
                        }
                    ],
                    snapshot_run_id=snapshot_run_id,
                    ticket_id=request.ticket_id,
                    mode=mode,
                    execution_plan_id=request.execution_plan_id,
                    run_purpose=run_purpose,
                    create_run=False,
                    finalize_run=False,
                    run_owner_token=owner_token,
                    parser_runtime_bundle=runtime_bundle_data,
                )
                PatrolRunRepository().touch_run(
                    snapshot_run_id,
                    lease_seconds=settings.PATROL_RUN_LEASE_SECONDS,
                    worker_id=request.worker_id,
                    owner_token=owner_token,
                    metadata={
                        "last_persisted_command": (
                            (manifest.get("commands") or [{}])[-1].get("command")
                        )
                    },
                )

            context.tool.result_callback = persist_device_result
            collector = getattr(context.tool, "cli_collector", None)
            if collector is not None and hasattr(collector, "progress_callback"):
                collector.progress_callback = persist_command_progress
        try:
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
        except Exception as exc:
            if streaming_persistence:
                from src.core.patrol.run_repository import PatrolRunRepository

                PatrolRunRepository().finish_run(
                    snapshot_run_id,
                    collection_status="failed",
                    analysis_status="failed",
                    baseline_eligible=False,
                    metadata={"collection_failure": str(exc)},
                    owner_token=owner_token,
                )
            raise

        if streaming_persistence:
            snapshot_result = _combine_snapshot_results(
                snapshot_run_id=snapshot_run_id,
                details=result.details or [],
            )
        else:
            snapshot_result = self._snapshot_persister(
                details=result.details or [],
                snapshot_run_id=snapshot_run_id,
                ticket_id=request.ticket_id,
                mode=mode,
                execution_plan_id=request.execution_plan_id,
                run_purpose=run_purpose,
                started_at=collection_started_at,
                parser_runtime_bundle=runtime_bundle_data,
            )
        raw_outputs_url = self._archive_raw_outputs(
            output_files=result.output_files or [],
            ticket_id=request.ticket_id,
        )
        if streaming_persistence:
            from src.core.patrol.run_repository import PatrolRunRepository

            PatrolRunRepository().finish_run(
                snapshot_run_id,
                collection_status=str(snapshot_result["collection_status"]),
                analysis_status=(
                    "failed" if snapshot_result["collection_status"] == "failed" else "pending"
                ),
                baseline_eligible=False,
                metadata={
                    "successful_device_count": snapshot_result["snapshot_devices"],
                    "failed_device_count": snapshot_result["failed_devices"],
                    "snapshot_health": snapshot_result["snapshot_health"],
                    "archive_status": "completed",
                    "raw_outputs_archive": raw_outputs_url,
                },
                owner_token=owner_token,
            )
        if int(result.success_devices or 0) == 0 and not snapshot_result.get("snapshot_devices"):
            raise PatrolPlatformError(result.message or "device patrol failed")
        if snapshot_result["snapshot_errors"] and not snapshot_result.get("snapshot_devices"):
            raise PatrolPlatformError("; ".join(snapshot_result["snapshot_errors"]))

        execution_time_ms = int(
            (datetime.now(timezone.utc) - execution_start).total_seconds() * 1000
        )
        return runner_success_payload(
            action="patrol",
            ticket_id=request.ticket_id,
            result=result,
            execution_time_ms=execution_time_ms,
            download_url=raw_outputs_url,
            extra={
                "save_baseline": request.save_baseline,
                "mode": mode,
                # Execution succeeded once at least one collected snapshot was
                # persisted. Parser/data-quality degradation is reported
                # independently so downstream workflow steps can still run.
                "status": "success",
                "data_quality_status": snapshot_result.get("snapshot_health", "ready"),
                "execution_plan_id": request.execution_plan_id,
                "parser_runtime_bundle_digest": (
                    runtime_bundle.digest if runtime_bundle is not None else None
                ),
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


def _build_parser_runtime_bundle(execution_plan: Any):
    """Resolve Parser assets once before an explicit device plan starts."""
    from src.core.device_ops.repository import DeviceRepository
    from src.core.parser.identity import DeviceIdentity
    from src.core.parser.runtime_bundle import ParserRuntimeBundle

    matches = DeviceRepository().get_devices_by_filter(
        device_id=str(execution_plan.device_id),
        status=None,
    )
    if not matches:
        return None
    device = matches[0]
    vendor = str(device.get("vendor") or "").strip()
    model = str(device.get("model") or "").strip()
    if not vendor or not model:
        return None
    identity = DeviceIdentity(
        vendor=vendor,
        model=model,
        family=str(device.get("family") or "").strip() or None,
        confidence=1.0,
        evidence_text="inventory execution plan",
        source="inventory",
    )
    return ParserRuntimeBundle.build(
        identity=identity,
        commands=execution_plan.command_texts(),
    )

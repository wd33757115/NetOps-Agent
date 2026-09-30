# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Device patrol execution runner.

The Celery task is intentionally kept as a thin scheduling wrapper. This
module owns the platform-level patrol flow: resolve an execution plan, collect
CLI evidence, persist snapshots, archive raw outputs, and return a stable
workflow payload.
"""

from __future__ import annotations

import hashlib
import json
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

    def __init__(
        self,
        message: str,
        *,
        failure_code: str = "device_patrol_failed",
    ) -> None:
        super().__init__(message)
        self.failure_code = failure_code


def _terminal_collection_failure_code(details: list[dict[str, Any]]) -> str:
    """Return one stable reason for an all-device collection failure."""

    reasons: list[str] = []
    for detail in details:
        direct = str(detail.get("failure_code") or "").strip()
        if direct:
            reasons.append(direct)
        for item in detail.get("command_collection_results") or []:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").strip()
            if status == "timeout":
                reasons.append("command_timeout")
            elif status == "failed":
                reasons.append("command_failed")
    unique = tuple(dict.fromkeys(reason for reason in reasons if reason))
    if len(unique) == 1:
        return unique[0]
    if len(unique) > 1:
        return "multiple_collection_failures"
    return "device_patrol_failed"


def _device_result_evidence(
    details: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep operator-facing failure facts without persisting raw CLI or secrets."""

    evidence: list[dict[str, Any]] = []
    for detail in details:
        commands = []
        for item in list(detail.get("command_collection_results") or [])[:50]:
            if not isinstance(item, dict):
                continue
            commands.append(
                {
                    "command": str(item.get("command") or ""),
                    "status": str(item.get("status") or "unknown"),
                    "failure_code": str(item.get("failure_code") or "") or None,
                    "error": str(item.get("error") or "")[:1000] or None,
                }
            )
        evidence.append(
            {
                "inventory_device_id": str(
                    detail.get("inventory_device_id") or ""
                )
                or None,
                "device_name": str(detail.get("device_name") or "") or None,
                "success": bool(detail.get("success")),
                "collection_status": str(
                    detail.get("collection_status") or "unknown"
                ),
                "failure_code": str(detail.get("failure_code") or "") or None,
                "error": str(detail.get("error") or "")[:1000] or None,
                "persistence_error": str(
                    detail.get("persistence_error") or ""
                )[:1000]
                or None,
                "commands": commands,
            }
        )
    return evidence


def persist_patrol_snapshots(
    *,
    details: list[dict[str, Any]],
    snapshot_run_id: str,
    db_path: str | None = None,
    ticket_id: str | None = None,
    mode: str = "production",
    execution_plan_id: str | None = None,
    patrol_batch_id: str | None = None,
    run_purpose: str = "scheduled_patrol",
    source: str = "device-patrol",
    dataset_id: str | None = None,
    source_metadata: dict[str, Any] | None = None,
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
            source=source,
            mode=mode,
            dataset_id=dataset_id,
            metadata={
                "ticket_id": ticket_id,
                "execution_plan_id": execution_plan_id,
                "patrol_batch_id": patrol_batch_id,
                "requested_device_count": len(details),
                "parser_runtime_bundle_digest": (
                    parser_runtime_bundle.get("digest") if parser_runtime_bundle else None
                ),
                **dict(source_metadata or {}),
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
                    if item_status in {
                        "fallback_not_needed",
                        "ignored_legacy_fallback",
                    }:
                        continue
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
                        collection_action_id=(
                            item.get("collection_item_id")
                            if isinstance(item, dict)
                            else None
                        ),
                        metadata={
                            "error": (
                                item.get("error") if isinstance(item, dict) else detail.get("error")
                            ),
                            "check_item_bindings": (
                                item.get("check_item_bindings")
                                if isinstance(item, dict)
                                else []
                            ),
                            "concurrency_mode": detail.get(
                                "concurrency_mode"
                            ),
                            "device_fencing_token": detail.get(
                                "device_fencing_token"
                            ),
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
                    "source": source,
                    "dataset_id": dataset_id,
                    "execution_plan_id": execution_plan_id,
                    "patrol_batch_id": patrol_batch_id,
                    "run_purpose": run_purpose,
                    "concurrency_mode": detail.get("concurrency_mode"),
                    "device_fencing_token": detail.get(
                        "device_fencing_token"
                    ),
                    **dict(source_metadata or {}),
                },
                inventory_device_id=detail.get("inventory_device_id"),
                collector_complete=(
                    bool(detail.get("collector_complete"))
                    if "collector_complete" in detail
                    else detail.get("collection_status") == "complete"
                    if "collection_status" in detail
                    else True
                ),
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
                if command_result.get("status") in {
                    "complete",
                    "fallback_not_needed",
                    "ignored_legacy_fallback",
                }:
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
                    collection_action_id=command_result.get(
                        "collection_item_id"
                    ),
                    metadata={
                        "error": command_result.get("error"),
                        "check_item_bindings": command_result.get(
                            "check_item_bindings"
                        )
                        or [],
                    },
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
    failed_commands = sum(
        1
        for detail in details
        for item in detail.get("command_collection_results") or []
        if isinstance(item, dict)
        and str(item.get("status") or "")
        not in {
            "complete",
            "fallback_not_needed",
            "ignored_legacy_fallback",
        }
    )
    collection_status = "completed"
    if imported_devices == 0:
        collection_status = "failed"
    elif failed_devices or failed_commands or import_errors:
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
        "failed_collection_commands": failed_commands,
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
    actor_id: str = "system"
    source_app: str = "inspection"


class DevicePatrolRunner:
    """Run one device-patrol job outside of transport-specific wrappers."""

    def __init__(
        self,
        *,
        tools_loader: Callable[[], Any] | None = None,
        minio_storage_factory: Callable[[], Any] | None = None,
        snapshot_persister: Callable[..., dict[str, Any]] = persist_patrol_snapshots,
        runtime_bundle_factory: Callable[[Any], Any | None] | None = None,
        runtime_manifest_factory: Callable[[Any | None], dict[str, Any]] | None = None,
    ) -> None:
        self._tools_loader = tools_loader
        self._minio_storage_factory = minio_storage_factory or get_minio_storage
        self._snapshot_persister = snapshot_persister
        self._runtime_bundle_factory = runtime_bundle_factory or _build_parser_runtime_bundle
        if runtime_manifest_factory is None:
            def runtime_manifest_factory(_bundle):
                raise ValueError("生产巡检必须使用检查项运行包；旧命令契约链已退役")
        self._runtime_manifest_factory = runtime_manifest_factory

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
        collector = getattr(context.tool, "cli_collector", None)
        if collector is not None:
            collector.actor_id = request.actor_id
            collector.source_app = request.source_app
            collector.task_id = request.task_id or logical_run_id
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
        runtime_asset_manifest = (
            _load_frozen_check_item_runtime_manifest(
                context.execution_plan
            )
            if (
                context.execution_plan is not None
                and context.execution_plan.runtime_manifest_id
            )
            else self._runtime_manifest_factory(runtime_bundle)
        )
        patrol_batch_id = (
            str(runtime_asset_manifest.get("patrol_batch_id") or "").strip()
            or None
        )
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
                    "runtime_asset_manifest_digest": runtime_asset_manifest.get("digest"),
                    "runtime_asset_manifest": runtime_asset_manifest,
                    "patrol_batch_id": patrol_batch_id,
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
                    patrol_batch_id=patrol_batch_id,
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
                            "concurrency_mode": device.get("concurrency_mode"),
                            "device_fencing_token": device.get(
                                "device_fencing_token"
                            ),
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
                    patrol_batch_id=patrol_batch_id,
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

            raw_persist_command_progress = persist_command_progress

            def persist_command_progress(
                *,
                output_file,
                manifest,
                device,
                execution_plan,
            ) -> None:
                from src.core.ingestion.adapters.cli import (
                    CollectionProgressFencedError,
                )
                from src.core.patrol.repository import PatrolRunFencedError

                try:
                    raw_persist_command_progress(
                        output_file=output_file,
                        manifest=manifest,
                        device=device,
                        execution_plan=execution_plan,
                    )
                except PatrolRunFencedError as exc:
                    raise CollectionProgressFencedError(
                        "stale patrol worker was fenced before progress persistence"
                    ) from exc

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
                patrol_batch_id=patrol_batch_id,
                run_purpose=run_purpose,
                started_at=collection_started_at,
                parser_runtime_bundle=runtime_bundle_data,
            )
        raw_outputs_url = self._archive_raw_outputs(
            output_files=result.output_files or [],
            ticket_id=request.ticket_id,
        )
        archive_status = "completed" if raw_outputs_url else "unavailable"
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
                    "archive_status": archive_status,
                    "raw_outputs_archive": raw_outputs_url,
                    "runtime_asset_manifest_digest": runtime_asset_manifest.get("digest"),
                    "device_results": _device_result_evidence(
                        list(result.details or [])
                    ),
                },
                owner_token=owner_token,
            )
        if int(result.success_devices or 0) == 0 and not snapshot_result.get("snapshot_devices"):
            raise PatrolPlatformError(
                result.message or "device patrol failed",
                failure_code=_terminal_collection_failure_code(
                    list(result.details or [])
                ),
            )
        if snapshot_result["snapshot_errors"] and not snapshot_result.get("snapshot_devices"):
            raise PatrolPlatformError(
                "; ".join(snapshot_result["snapshot_errors"]),
                failure_code="snapshot_persistence_failed",
            )

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
                "runtime_asset_manifest": runtime_asset_manifest,
                "runtime_asset_manifest_digest": runtime_asset_manifest.get("digest"),
                "raw_outputs_url": raw_outputs_url,
                "raw_outputs_archive": raw_outputs_url,
                "raw_outputs_archive_status": archive_status,
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
            cleanup_after_upload=True,
        )


def _build_parser_runtime_bundle(execution_plan: Any):
    """Resolve Parser assets once before an explicit device plan starts."""
    if execution_plan.runtime_manifest_id:
        return _build_frozen_check_item_parser_bundle(execution_plan)

    from src.platform.inventory.repository import DeviceRepository
    from src.core.parser.identity import DeviceIdentity
    from src.apps.rule_governance.runtime import ParserRuntimeBundle

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


def load_frozen_check_item_runtime_manifest(
    execution_plan: Any,
) -> dict[str, Any]:
    from src.infrastructure.db.models import (
        CheckItemRuntimeManifestRecord,
    )
    from src.infrastructure.db.postgres import get_db_session

    manifest_id = str(execution_plan.runtime_manifest_id or "")
    supplied_digest = str(execution_plan.runtime_manifest_digest or "")
    if not manifest_id or not supplied_digest:
        raise RuntimeError(
            "check-item execution plan is missing its frozen manifest identity"
        )
    with get_db_session() as db:
        row = db.get(CheckItemRuntimeManifestRecord, manifest_id)
        if row is None:
            raise RuntimeError(
                f"frozen check-item runtime manifest not found: {manifest_id}"
            )
        payload = dict(row.manifest_json or {})
        embedded_digest = str(payload.pop("digest", "") or "")
        calculated_digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        if (
            supplied_digest != str(row.digest)
            or embedded_digest != str(row.digest)
            or calculated_digest != str(row.digest)
        ):
            raise RuntimeError(
                "frozen check-item runtime manifest digest mismatch"
            )
        action_by_id = {
            str(item.get("id")): item
            for item in payload.get("collection_actions") or []
            if isinstance(item, dict) and item.get("id")
        }
        for item in execution_plan.commands:
            action = action_by_id.get(str(item.collection_item_id or ""))
            if action is None:
                raise RuntimeError(
                    "execution plan action is absent from the frozen runtime manifest"
                )
            if str(action.get("command") or "") != str(item.command):
                raise RuntimeError(
                    "execution plan command differs from the frozen runtime manifest"
                )
        return {
            **payload,
            "digest": str(row.digest),
            "manifest_id": str(row.id),
            "manifest_kind": "check_item_runtime",
        }


def build_frozen_check_item_parser_bundle(
    execution_plan: Any,
    *,
    frozen_device: dict[str, Any] | None = None,
):
    from src.apps.rule_governance.runtime import CheckItemRuntimeReader
    from src.platform.inventory.repository import DeviceRepository
    from src.core.parser.policy import (
        DEFAULT_COMMAND_ALIASES,
        ParserPolicy,
    )
    from src.apps.rule_governance.runtime import ParserRuntimeBundle

    load_frozen_check_item_runtime_manifest(execution_plan)
    matches = (
        [dict(frozen_device)]
        if frozen_device is not None
        else DeviceRepository().get_devices_by_filter(
            device_id=str(execution_plan.device_id),
            status=None,
        )
    )
    if not matches:
        raise RuntimeError(
            f"inventory device not found: {execution_plan.device_id}"
        )
    device = matches[0]
    vendor = str(device.get("vendor") or "").strip()
    model = str(device.get("model") or "").strip()
    family = str(device.get("family") or "").strip() or None
    if not vendor or not model:
        raise RuntimeError(
            "frozen check-item parsing requires vendor and model identity"
        )

    def canonical(value: str) -> str:
        basic = ParserPolicy._basic_normalize(value)
        return ParserPolicy._basic_normalize(
            DEFAULT_COMMAND_ALIASES.get(basic, basic)
        )

    templates: dict[str, dict[str, str] | None] = {}
    output_quality_profiles: dict[str, dict[str, Any]] = {}
    bundle_service = CheckItemRuntimeReader()
    for item in execution_plan.commands:
        command = canonical(item.command)
        selected_ref: dict[str, str] | None = None
        selected_profile: dict[str, Any] | None = None
        for binding in item.check_item_bindings:
            version_id = str(
                binding.get("runtime_bundle_version_id") or ""
            )
            if not version_id:
                raise RuntimeError(
                    "collection action is missing a frozen runtime bundle"
                )
            bundle = bundle_service.get_version(version_id)
            if bundle is None:
                raise RuntimeError(
                    f"frozen runtime bundle not found: {version_id}"
                )
            compatibility = dict(bundle.get("compatibility") or {})
            evidence_policy = dict(
                compatibility.get("evidence_policy") or {}
            )
            profile = dict(
                evidence_policy.get("output_quality_profile") or {}
            )
            if profile:
                if (
                    selected_profile is not None
                    and selected_profile != profile
                ):
                    raise RuntimeError(
                        "shared command resolves conflicting frozen "
                        "OutputQualityProfiles"
                    )
                selected_profile = profile
            parser_ref = dict(
                (bundle.get("parser_bundle") or {}).get(command) or {}
            )
            if not parser_ref:
                continue
            if selected_ref is not None and selected_ref != parser_ref:
                raise RuntimeError(
                    "shared command resolves conflicting frozen Parser templates"
                )
            selected_ref = {
                "template_id": str(parser_ref["template_id"]),
                "version": str(parser_ref["version"]),
                "sha256": str(parser_ref["sha256"]),
            }
        templates[command] = selected_ref
        if selected_profile is not None:
            output_quality_profiles[command] = selected_profile
    return ParserRuntimeBundle.from_dict(
        {
            "vendor": vendor,
            "model": model,
            "family": family,
            "templates": templates,
            "output_quality_profiles": output_quality_profiles,
        }
    )


# Compatibility aliases for older internal imports. New governed replay code
# uses the public names above so both online and attested-offline collection
# resolve the exact same frozen runtime assets.
_load_frozen_check_item_runtime_manifest = (
    load_frozen_check_item_runtime_manifest
)
_build_frozen_check_item_parser_bundle = (
    build_frozen_check_item_parser_bundle
)

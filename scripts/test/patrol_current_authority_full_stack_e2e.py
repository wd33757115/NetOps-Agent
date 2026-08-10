#!/usr/bin/env python
"""Exercise governed offline-current evidence through the real platform stack.

This is an engineering verification, not a production network assertion. The
script creates a synthetic, freshly observed H3C configuration capture and an
isolated ``configuration.running`` RuntimeBundle. It then uses the same BFF,
MinIO, Celery, PostgreSQL and report APIs as the browser to prove that:

* historical imports cannot request current-state authority;
* a current-intent import freezes source, identity, scope and result ledgers;
* the importer cannot attest their own evidence;
* an independent administrator can attest the immutable request;
* the platform creates a separate production patrol batch and report; and
* revocation remains auditable and idempotent.

All synthetic database rows and object-storage artifacts are removed unless
``--keep`` is supplied. Passing this script must never be reported as W0.5
production evidence; real devices, real approved assets and independent human
actors are still required for that gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
DJANGO_ROOT = ROOT / "web" / "django_backend"
for import_root in (ROOT, DJANGO_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_backend.settings")

import django  # noqa: E402

django.setup()

from bff.roles import assign_user_role  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402

from scripts.test.check_item_patrol_workflow_full_stack_e2e import (  # noqa: E402
    _cleanup as _cleanup_patrol_batch,
)
from scripts.test.check_item_report_download_full_stack_e2e import (  # noqa: E402
    _download_and_verify,
)
from scripts.test.check_item_profile_full_stack_e2e import (  # noqa: E402
    _cleanup as _cleanup_runtime_bundle,
    _runtime_state,
    _seed_runtime_bundle,
)
from scripts.test.rule_asset_source_import_full_stack_e2e import (  # noqa: E402
    _delete_json,
    _login,
    _request_json,
)
from src.core.celery_tasks.celery_app import celery  # noqa: E402
from src.infrastructure.db.models import (  # noqa: E402
    AuditLogRecord,
    CheckAssessmentRecord,
    CheckExecutionRecord,
    CheckItemRuntimeManifestRecord,
    CommandSnapshot,
    CollectionExecutionPlanRecord,
    FileMetadata,
    NetworkDevice,
    Notification,
    OfflinePatrolDeviceBindingRecord,
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    PatrolBatchDeviceRecord,
    PatrolCollectionResult,
    PatrolCurrentAuthorityRequestRecord,
    PatrolDataset,
    PatrolRun,
    PatrolScopeContractRecord,
    PatrolScopeContractVersionRecord,
    PatrolSourceQualificationRecord,
    StorageFolder,
    WorkflowRun,
    WorkflowStepRecord,
)
from src.infrastructure.db.postgres import get_db_session  # noqa: E402
from src.infrastructure.storage.minio_client import get_minio_storage  # noqa: E402


CHECK_ITEM_ID = "configuration.running"
CANONICAL_COMMAND = "display current-configuration"
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}
TERMINAL_WORKFLOW_STATUSES = {"completed", "failed", "cancelled"}


def _create_user(username: str, password: str, role: str) -> str:
    user = get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.invalid",
        password=password,
    )
    assign_user_role(user, role)
    user.save()
    return str(user.pk)


def _delete_user(username: str) -> int:
    deleted, _detail = get_user_model().objects.filter(
        username=username
    ).delete()
    return int(deleted)


def _synthetic_capture(device_name: str) -> bytes:
    return (
        f"<{device_name}>{CANONICAL_COMMAND}\n"
        "#\n"
        "version 7.1.064, Release 0821P18\n"
        f"sysname {device_name}\n"
        "interface GigabitEthernet1/0/1\n"
        " description CURRENT-AUTHORITY-E2E\n"
        " undo shutdown\n"
        "#\n"
        "return\n"
        f"<{device_name}>\n"
    ).encode("utf-8")


def _seed_device(*, device_id: str, device_name: str, management_ip: str) -> None:
    now = datetime.now(timezone.utc)
    with get_db_session() as db:
        db.add(
            NetworkDevice(
                id=device_id,
                device_name=device_name,
                management_ip=management_ip,
                username="e2e-operator",
                credential_ref="env:NETOPS_PASSWORD",
                vendor="H3C",
                model="MSR5620",
                family="Comware7",
                platform="comware",
                device_type="hp_comware",
                site="current-authority-e2e",
                role="router",
                status="active",
                attributes_json={"role": "router"},
                created_at=now,
                updated_at=now,
            )
        )


def _seed_accounted_collection_failure(
    *,
    job_id: str,
    device_id: str,
    device_name: str,
    management_ip: str,
    confirmed_by: str,
) -> dict[str, str]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    binding_id = f"binding-failed-{uuid.uuid4().hex[:20]}"
    collection_result_id = f"collection-failed-{uuid.uuid4().hex[:20]}"
    with get_db_session() as db:
        period = (
            db.query(OfflinePatrolPeriodRecord)
            .filter(
                OfflinePatrolPeriodRecord.job_id == job_id,
                OfflinePatrolPeriodRecord.role == "current",
            )
            .one()
        )
        run = db.get(PatrolRun, str(period.patrol_run_id or ""))
        job = db.get(OfflinePatrolJobRecord, job_id)
        if run is None or job is None:
            raise AssertionError("completed offline source ledger is missing")
        db.add(
            OfflinePatrolDeviceBindingRecord(
                id=binding_id,
                job_id=job_id,
                period_role="current",
                external_device_id=device_name,
                device_name=device_name,
                management_ip=management_ip,
                inventory_device_id=device_id,
                match_status="confirmed",
                confidence=1.0,
                match_reason="synthetic_accounted_collection_failure",
                confirmed_by=confirmed_by,
                confirmed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            PatrolCollectionResult(
                id=collection_result_id,
                run_id=str(run.id),
                inventory_device_id=device_id,
                device_id=device_name,
                execution_item_id=f"failed-current-{uuid.uuid4().hex[:20]}",
                canonical_command=CANONICAL_COMMAND,
                status="collection_failed",
                execution_status="failed",
                parse_status="not_attempted",
                output_status="missing",
                fact_status="blocked_by_collection",
                normalization_status="blocked",
                authoritative_for_deletion=False,
                baseline_eligible=False,
                failure_code="connection_failed",
                observed_at=now,
                metadata_json={
                    "offline_job_id": job_id,
                    "period_role": "current",
                    "fixture": "accounted_collection_failure",
                },
                created_at=now,
                updated_at=now,
            )
        )
        run.device_count = max(int(run.device_count or 0), 2)
        run.command_count = int(run.command_count or 0) + 1
        run.collection_status = "partial"
        run.updated_at = now
        job_result = dict(job.result_json or {})
        job_result.update(
            {
                "device_count": 2,
                "failed_device_count": 1,
                "command_count": int(job_result.get("command_count") or 0) + 1,
            }
        )
        job.result_json = job_result
        job.updated_at = now
        period_result = dict(period.result_json or {})
        period_result.update(
            {
                "device_count": 2,
                "failed_device_count": 1,
                "command_count": int(period_result.get("command_count") or 0) + 1,
            }
        )
        period.result_json = period_result
        period.updated_at = now
    return {
        "binding_id": binding_id,
        "collection_result_id": collection_result_id,
    }


def _wait_job(
    client: httpx.Client,
    job_id: str,
    *,
    expected: set[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _request_json(
            client,
            "GET",
            f"/api/patrol/offline-jobs/{job_id}/",
        )
        status = str(last.get("status") or "")
        if status in expected:
            return last
        if status in TERMINAL_JOB_STATUSES and status not in expected:
            raise AssertionError(
                f"offline job reached unexpected terminal status {status}: "
                f"{last.get('error_message')}"
            )
        time.sleep(0.5)
    raise TimeoutError(
        f"offline job did not reach {sorted(expected)}: "
        f"last status={last.get('status')}"
    )


def _expect_error_code(
    response: httpx.Response,
    *,
    expected_status: int,
    expected_code: str,
) -> dict[str, Any]:
    if response.status_code != expected_status:
        raise AssertionError(
            f"expected HTTP {expected_status}, got {response.status_code}: "
            f"{response.text}"
        )
    payload = response.json()
    current: Any = payload
    for key in ("error", "data", "detail"):
        if isinstance(current, dict) and key in current:
            current = current[key]
    code = str(current.get("code") or "") if isinstance(current, dict) else ""
    if not code:
        encoded = json.dumps(payload, ensure_ascii=False)
        if expected_code in encoded:
            code = expected_code
    if code != expected_code:
        raise AssertionError(
            f"expected business error {expected_code!r}, got {payload}"
        )
    return dict(payload)


def _wait_authority(
    client: httpx.Client,
    job_id: str,
    *,
    expected: set[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _request_json(
            client,
            "GET",
            f"/api/patrol/offline-jobs/{job_id}/current-authority/",
        )
        if str(last.get("status") or "") in expected:
            return last
        time.sleep(0.5)
    raise TimeoutError(
        f"current authority did not reach {sorted(expected)}: {last}"
    )


def _wait_batch_workflows(
    batch_id: str,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        with get_db_session() as db:
            from src.infrastructure.db.models import PatrolBatchDeviceRecord

            targets = (
                db.query(PatrolBatchDeviceRecord)
                .filter(PatrolBatchDeviceRecord.patrol_batch_id == batch_id)
                .all()
            )
            workflow_ids = [
                str(item.workflow_run_id)
                for item in targets
                if item.workflow_run_id
            ]
            workflows = (
                db.query(WorkflowRun).filter(WorkflowRun.id.in_(workflow_ids)).all()
                if workflow_ids
                else []
            )
            step_rows = (
                db.query(WorkflowStepRecord)
                .filter(WorkflowStepRecord.run_id.in_(workflow_ids))
                .order_by(
                    WorkflowStepRecord.run_id,
                    WorkflowStepRecord.step_index,
                )
                .all()
                if workflow_ids
                else []
            )
            statuses = {str(item.status) for item in workflows}
            last = {
                "target_count": len(targets),
                "workflow_count": len(workflows),
                "workflow_ids": workflow_ids,
                "statuses": sorted(statuses),
                "workflows": [
                    {
                        "id": str(item.id),
                        "status": str(item.status),
                        "error_message": item.error_message,
                    }
                    for item in workflows
                ],
                "steps": [
                    {
                        "run_id": str(item.run_id),
                        "step_index": int(item.step_index or 0),
                        "step_name": str(item.step_name),
                        "skill_name": str(item.skill_name),
                        "status": str(item.status),
                        "error_message": item.error_message,
                        "result": {
                            key: dict(item.result or {}).get(key)
                            for key in (
                                "success",
                                "status",
                                "failure_code",
                                "message",
                            )
                            if key in dict(item.result or {})
                        },
                    }
                    for item in step_rows
                ],
            }
        if (
            last["target_count"] > 0
            and last["workflow_count"] == last["target_count"]
            and statuses
            and statuses.issubset(TERMINAL_WORKFLOW_STATUSES)
        ):
            return last
        time.sleep(0.5)
    raise TimeoutError(f"production workflows did not finish: {last}")


def _batch_fact_state(batch_id: str) -> dict[str, Any]:
    with get_db_session() as db:
        executions = (
            db.query(CheckExecutionRecord)
            .filter(CheckExecutionRecord.patrol_batch_id == batch_id)
            .order_by(CheckExecutionRecord.device_id, CheckExecutionRecord.id)
            .all()
        )
        assessments = {
            str(item.id): item
            for item in db.query(CheckAssessmentRecord)
            .filter(CheckAssessmentRecord.patrol_batch_id == batch_id)
            .all()
        }
        return {
            "execution_count": len(executions),
            "executions": [
                {
                    "id": str(item.id),
                    "device_id": str(item.device_id),
                    "execution_status": str(item.execution_status),
                    "reason_codes": list(item.reason_codes_json or []),
                    "assessment": (
                        {
                            "assessment_status": str(assessment.assessment_status),
                            "completeness_status": str(
                                assessment.completeness_status
                            ),
                            "comparison_status": str(
                                assessment.comparison_status
                            ),
                            "blocking_reason_codes": list(
                                assessment.blocking_reason_codes_json or []
                            ),
                        }
                        if (
                            item.assessment_id
                            and (
                                assessment := assessments.get(
                                    str(item.assessment_id)
                                )
                            )
                            is not None
                        )
                        else None
                    ),
                }
                for item in executions
            ],
        }


def _authority_runtime_counts(
    *,
    request_id: str,
    batch_id: str,
) -> dict[str, int]:
    with get_db_session() as db:
        target_rows = (
            db.query(PatrolBatchDeviceRecord)
            .filter(PatrolBatchDeviceRecord.patrol_batch_id == batch_id)
            .all()
        )
        workflow_ids = [
            str(item.workflow_run_id)
            for item in target_rows
            if item.workflow_run_id
        ]
        patrol_runs = (
            db.query(PatrolRun)
            .filter(
                PatrolRun.source == "attested-offline-current",
                PatrolRun.metadata_json["authority_request_id"].as_string()
                == request_id,
            )
            .all()
        )
        run_ids = [str(item.id) for item in patrol_runs]
        return {
            "qualification_count": int(
                db.query(PatrolSourceQualificationRecord)
                .filter(PatrolSourceQualificationRecord.request_id == request_id)
                .count()
            ),
            "runtime_manifest_count": int(
                db.query(CheckItemRuntimeManifestRecord)
                .filter(CheckItemRuntimeManifestRecord.patrol_batch_id == batch_id)
                .count()
            ),
            "execution_plan_count": int(
                db.query(CollectionExecutionPlanRecord)
                .filter(CollectionExecutionPlanRecord.patrol_batch_id == batch_id)
                .count()
            ),
            "target_count": len(target_rows),
            "workflow_count": len(workflow_ids),
            "patrol_run_count": len(patrol_runs),
            "snapshot_count": int(
                db.query(CommandSnapshot)
                .filter(CommandSnapshot.run_id.in_(run_ids))
                .count()
                if run_ids
                else 0
            ),
            "execution_count": int(
                db.query(CheckExecutionRecord)
                .filter(CheckExecutionRecord.patrol_batch_id == batch_id)
                .count()
            ),
            "assessment_count": int(
                db.query(CheckAssessmentRecord)
                .filter(CheckAssessmentRecord.patrol_batch_id == batch_id)
                .count()
            ),
        }


def _tamper_current_snapshot(job_id: str) -> tuple[str, str]:
    with get_db_session() as db:
        period = (
            db.query(OfflinePatrolPeriodRecord)
            .filter(
                OfflinePatrolPeriodRecord.job_id == job_id,
                OfflinePatrolPeriodRecord.role == "current",
            )
            .one()
        )
        snapshot = (
            db.query(CommandSnapshot)
            .filter(CommandSnapshot.run_id == period.patrol_run_id)
            .one()
        )
        original = str(snapshot.raw_text or "")
        snapshot.raw_text = original + "\n# unauthorized mutation\n"
        return str(snapshot.id), original


def _restore_snapshot(snapshot_id: str, raw_text: str) -> None:
    with get_db_session() as db:
        snapshot = db.get(CommandSnapshot, snapshot_id)
        if snapshot is None:
            raise AssertionError("tampered test snapshot disappeared")
        snapshot.raw_text = raw_text


def _wait_report(
    client: httpx.Client,
    batch_id: str,
    *,
    task_id: str,
    timeout_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        task_state = str(celery.AsyncResult(task_id).state)
        status = _request_json(
            client,
            "GET",
            f"/api/patrol/batches/{batch_id}/reports/materialization-status",
        )
        if task_state == "SUCCESS" and status.get("status") == "completed":
            manifest = _request_json(
                client,
                "GET",
                f"/api/patrol/batches/{batch_id}/report-manifest",
            )
            return status, manifest
        if task_state == "FAILURE" or status.get("status") == "failed":
            raise AssertionError(
                f"report materialization failed: task={task_state}, status={status}"
            )
        time.sleep(0.5)
    raise TimeoutError(f"report did not complete: {status}")


def _break_authority_cycles_and_delete_job(
    *,
    job_id: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source_workflow_run_id": None,
        "dataset_id": None,
        "scope_contract_version_id": None,
        "scope_contract_id": None,
    }
    with get_db_session() as db:
        job = db.get(OfflinePatrolJobRecord, job_id)
        if job is None:
            return result
        result["source_workflow_run_id"] = job.workflow_run_id
        period = (
            db.query(OfflinePatrolPeriodRecord)
            .filter(OfflinePatrolPeriodRecord.job_id == job_id)
            .first()
        )
        if period is not None:
            result["dataset_id"] = period.dataset_id
        requests = (
            db.query(PatrolCurrentAuthorityRequestRecord)
            .filter(PatrolCurrentAuthorityRequestRecord.offline_job_id == job_id)
            .all()
        )
        request_ids = [str(item.id) for item in requests]
        qualification_ids = [
            str(item.approved_qualification_id)
            for item in requests
            if item.approved_qualification_id
        ]
        for request in requests:
            if request.scope_contract_version_id:
                result["scope_contract_version_id"] = str(
                    request.scope_contract_version_id
                )
                version = db.get(
                    PatrolScopeContractVersionRecord,
                    request.scope_contract_version_id,
                )
                if version is not None:
                    result["scope_contract_id"] = str(version.scope_contract_id)
            if request.candidate_batch_id:
                from src.infrastructure.db.models import PatrolBatchRecord

                batch = db.get(PatrolBatchRecord, request.candidate_batch_id)
                if batch is not None:
                    batch.source_qualification_id = None
                    batch.source_qualification_digest = None
                    batch.source_authority_request_id = None
                    batch.source_offline_job_id = None
            request.approved_qualification_id = None
            request.candidate_batch_id = None
        db.flush()
        if qualification_ids:
            db.query(PatrolSourceQualificationRecord).filter(
                PatrolSourceQualificationRecord.id.in_(qualification_ids)
            ).delete(synchronize_session=False)
        if request_ids:
            db.query(PatrolCurrentAuthorityRequestRecord).filter(
                PatrolCurrentAuthorityRequestRecord.id.in_(request_ids)
            ).delete(synchronize_session=False)
        db.query(OfflinePatrolJobRecord).filter(
            OfflinePatrolJobRecord.id == job_id
        ).delete(synchronize_session=False)
    return result


def _cleanup_source_workflow_and_dataset(context: dict[str, Any]) -> None:
    workflow_id = str(context.get("source_workflow_run_id") or "")
    dataset_id = str(context.get("dataset_id") or "")
    with get_db_session() as db:
        if workflow_id:
            db.query(Notification).filter(
                Notification.workflow_run_id == workflow_id
            ).delete(synchronize_session=False)
            db.query(WorkflowStepRecord).filter(
                WorkflowStepRecord.run_id == workflow_id
            ).delete(synchronize_session=False)
            db.query(WorkflowRun).filter(
                WorkflowRun.id == workflow_id
            ).delete(synchronize_session=False)
        if dataset_id:
            db.query(PatrolDataset).filter(
                PatrolDataset.id == dataset_id
            ).delete(synchronize_session=False)


def _cleanup_replay_dataset(request_id: str | None) -> int:
    if not request_id:
        return 0
    with get_db_session() as db:
        return int(
            db.query(PatrolDataset)
            .filter(PatrolDataset.id == f"attested-current-{request_id}")
            .delete(synchronize_session=False)
        )


def _cleanup_scope_contract(context: dict[str, Any]) -> None:
    version_id = str(context.get("scope_contract_version_id") or "")
    contract_id = str(context.get("scope_contract_id") or "")
    with get_db_session() as db:
        if version_id:
            db.query(PatrolScopeContractVersionRecord).filter(
                PatrolScopeContractVersionRecord.id == version_id
            ).delete(synchronize_session=False)
        if contract_id:
            db.query(PatrolScopeContractRecord).filter(
                PatrolScopeContractRecord.id == contract_id
            ).delete(synchronize_session=False)


def _cleanup_audits(*values: str) -> int:
    clean = [str(item) for item in values if str(item)]
    if not clean:
        return 0
    with get_db_session() as db:
        return int(
            db.query(AuditLogRecord)
            .filter(AuditLogRecord.resource_id.in_(clean))
            .delete(synchronize_session=False)
        )


def run(
    *,
    base_url: str,
    output_path: Path,
    timeout_seconds: int,
    keep: bool,
) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:12]
    importer_username = f"current-importer-{suffix}"
    reviewer_username = f"current-reviewer-{suffix}"
    importer_password = f"CurrentImporter-{uuid.uuid4().hex}!aA1"
    reviewer_password = f"CurrentReviewer-{uuid.uuid4().hex}!aA1"
    device_id = f"current-authority-device-{suffix}"
    device_name = f"CURRENT-AUTH-{suffix.upper()}"
    management_ip = f"192.0.2.{int(suffix[:2], 16) % 200 + 1}"
    failed_device_id = f"current-authority-failed-{suffix}"
    failed_device_name = f"CURRENT-FAIL-{suffix.upper()}"
    failed_management_ip = f"198.51.100.{int(suffix[2:4], 16) % 200 + 1}"
    source_bytes = _synthetic_capture(device_name)
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    observed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    before_runtime = _runtime_state()
    comparison_created = before_runtime["comparison_versions"] == 0
    runtime: dict[str, Any] = {}
    current_job_id: str | None = None
    historical_job_id: str | None = None
    current_file_id: str | None = None
    current_folder_id: str | None = None
    historical_folder_id: str | None = None
    object_key: str | None = None
    batch_id: str | None = None
    authority_request_id: str | None = None
    current_cleanup: dict[str, Any] = {}
    historical_cleanup: dict[str, Any] = {}
    importer_created = False
    reviewer_created = False
    summary: dict[str, Any] = {
        "schema_version": "patrol-current-authority-full-stack-e2e/v1",
        "success": False,
        "production_network_claim": False,
        "fixture_kind": "synthetic_fresh_engineering_evidence",
        "base_url": base_url,
        "observed_at": observed_at,
        "source_sha256": source_sha256,
        "before_runtime": before_runtime,
    }
    importer_client = httpx.Client(base_url=base_url.rstrip("/"), timeout=60)
    reviewer_client = httpx.Client(base_url=base_url.rstrip("/"), timeout=60)
    try:
        importer_id = _create_user(
            importer_username,
            importer_password,
            "admin",
        )
        importer_created = True
        reviewer_id = _create_user(
            reviewer_username,
            reviewer_password,
            "admin",
        )
        reviewer_created = True
        _seed_device(
            device_id=device_id,
            device_name=device_name,
            management_ip=management_ip,
        )
        _seed_device(
            device_id=failed_device_id,
            device_name=failed_device_name,
            management_ip=failed_management_ip,
        )
        runtime = _seed_runtime_bundle(suffix)

        importer_client.headers["Authorization"] = (
            "Bearer "
            + _login(
                importer_client,
                username=importer_username,
                password=importer_password,
            )
        )
        reviewer_client.headers["Authorization"] = (
            "Bearer "
            + _login(
                reviewer_client,
                username=reviewer_username,
                password=reviewer_password,
            )
        )

        historical = _request_json(
            importer_client,
            "POST",
            "/api/patrol/offline-jobs/",
            json_body={
                "job_type": "single_patrol",
                "name": f"E2E historical authority rejection {suffix}",
                "periods": [
                    {
                        "role": "current",
                        "label": "historical source",
                        "observed_at": "2026-01-01T00:00:00+00:00",
                    }
                ],
                "vendor": "H3C",
                "model": "MSR5620",
                "publish_events": False,
                "report_intent": "historical",
                "idempotency_key": f"historical-current-reject-{suffix}",
            },
        )
        historical_job_id = str(historical["id"])
        historical_folder_id = str(historical["folder_id"])
        historical_rejection = _expect_error_code(
            importer_client.post(
                (
                    f"/api/patrol/offline-jobs/{historical_job_id}/"
                    "current-authority/prepare/"
                ),
                headers={"Idempotency-Key": f"historical-proof-{suffix}"},
                json={
                    "completeness_statement": (
                        "Historical evidence cannot prove the current network state."
                    ),
                    "completeness_evidence_refs": [
                        "source_manifest",
                        "identity_binding_manifest",
                        "current_run_snapshot_manifest",
                        "offline_job_result_ledger",
                    ],
                    "observed_at_evidence_refs": [
                        "current_period_observed_at",
                        "snapshot_observed_at_set",
                    ],
                    "collection_tool": "synthetic-e2e/1.0",
                    "collector_identity": "current-authority-e2e",
                },
            ),
            expected_status=409,
            expected_code="historical_intent_only",
        )

        created = _request_json(
            importer_client,
            "POST",
            "/api/patrol/offline-jobs/",
            json_body={
                "job_type": "single_patrol",
                "name": f"E2E current authority {suffix}",
                "periods": [
                    {
                        "role": "current",
                        "label": "fresh engineering capture",
                        "observed_at": observed_at,
                    }
                ],
                "vendor": "H3C",
                "model": "MSR5620",
                "publish_events": False,
                "report_intent": "current_attestation",
                "idempotency_key": f"current-authority-create-{suffix}",
            },
        )
        current_job_id = str(created["id"])
        current_folder_id = str(created["folder_id"])
        upload = _request_json(
            importer_client,
            "POST",
            "/api/storage/upload/init/",
            json_body={
                "filename": f"{device_name}-{management_ip}.txt",
                "folder_id": current_folder_id,
                "visibility": "private",
                "content_type": "text/plain",
                "size_bytes": len(source_bytes),
            },
        )
        current_file_id = str(upload["file_id"])
        object_key = str(upload["object_key"])
        put = httpx.put(
            str(upload["upload_url"]),
            content=source_bytes,
            headers={"Content-Type": "text/plain"},
            timeout=30,
        )
        if put.status_code >= 400:
            raise AssertionError(
                f"presigned MinIO PUT returned {put.status_code}: {put.text}"
            )
        _request_json(
            importer_client,
            "POST",
            "/api/storage/upload/complete/",
            json_body={
                "file_id": current_file_id,
                "size_bytes": len(source_bytes),
            },
        )
        if get_minio_storage().download_file(object_key) != source_bytes:
            raise AssertionError("uploaded current evidence differs from MinIO bytes")
        _request_json(
            importer_client,
            "POST",
            f"/api/patrol/offline-jobs/{current_job_id}/sources/",
            json_body={
                "period_role": "current",
                "file_id": current_file_id,
                "relative_path": f"{device_name}-{management_ip}.txt",
                "sha256": source_sha256,
                "size_bytes": len(source_bytes),
            },
        )
        preview_dispatch = _request_json(
            importer_client,
            "POST",
            f"/api/patrol/offline-jobs/{current_job_id}/preview/",
            json_body={},
        )
        preview = _wait_job(
            importer_client,
            current_job_id,
            expected={"ready", "blocked"},
            timeout_seconds=timeout_seconds,
        )
        if preview.get("status") != "ready":
            raise AssertionError(
                f"current evidence preview is blocked: {preview.get('blockers')}"
            )
        bindings = list(preview.get("device_bindings") or [])
        if len(bindings) != 1:
            raise AssertionError(f"expected one device binding, got {bindings}")
        _request_json(
            importer_client,
            "POST",
            (
                f"/api/patrol/offline-jobs/{current_job_id}/"
                "device-bindings/confirm/"
            ),
            json_body={
                "bindings": [
                    {
                        "binding_id": str(bindings[0]["id"]),
                        "inventory_device_id": device_id,
                    }
                ]
            },
        )
        execution = _request_json(
            importer_client,
            "POST",
            f"/api/patrol/offline-jobs/{current_job_id}/execute/",
            json_body={
                "idempotency_key": f"current-authority-execute-{suffix}"
            },
        )
        completed = _wait_job(
            importer_client,
            current_job_id,
            expected={"completed"},
            timeout_seconds=timeout_seconds,
        )
        failed_source_ledger = _seed_accounted_collection_failure(
            job_id=current_job_id,
            device_id=failed_device_id,
            device_name=failed_device_name,
            management_ip=failed_management_ip,
            confirmed_by=importer_id,
        )

        prepare_headers = {
            "Idempotency-Key": f"current-authority-prepare-{suffix}"
        }
        proof_payload = {
            "completeness_statement": (
                "All selected devices, successful outputs and explicit failures "
                "are accounted for in the frozen platform ledgers."
            ),
            "completeness_evidence_refs": [
                "source_manifest",
                "identity_binding_manifest",
                "current_run_snapshot_manifest",
                "offline_job_result_ledger",
            ],
            "observed_at_evidence_refs": [
                "current_period_observed_at",
                "snapshot_observed_at_set",
            ],
            "collection_tool": "netops-current-authority-e2e/1.0",
            "collector_identity": f"fixture:{importer_id}",
        }
        prepared_response = importer_client.post(
            (
                f"/api/patrol/offline-jobs/{current_job_id}/"
                "current-authority/prepare/"
            ),
            headers=prepare_headers,
            json=proof_payload,
        )
        if prepared_response.status_code != 201:
            raise AssertionError(
                f"prepare returned {prepared_response.status_code}: "
                f"{prepared_response.text}"
            )
        prepared = _request_json(
            importer_client,
            "GET",
            f"/api/patrol/offline-jobs/{current_job_id}/current-authority/",
        )
        if prepared.get("status") != "ready_for_attestation":
            raise AssertionError(f"current authority is not ready: {prepared}")
        authority_request_id = str(prepared["request_id"])
        batch_id = str(prepared["candidate_batch_id"])

        replay_response = importer_client.post(
            (
                f"/api/patrol/offline-jobs/{current_job_id}/"
                "current-authority/prepare/"
            ),
            headers=prepare_headers,
            json=proof_payload,
        )
        if replay_response.status_code != 200:
            raise AssertionError(
                f"idempotent prepare returned {replay_response.status_code}: "
                f"{replay_response.text}"
            )
        conflict_payload = dict(proof_payload)
        conflict_payload["collector_identity"] = "different-fixture-collector"
        idempotency_conflict = _expect_error_code(
            importer_client.post(
                (
                    f"/api/patrol/offline-jobs/{current_job_id}/"
                    "current-authority/prepare/"
                ),
                headers=prepare_headers,
                json=conflict_payload,
            ),
            expected_status=409,
            expected_code="idempotency_conflict",
        )
        same_actor_rejection = _expect_error_code(
            importer_client.post(
                (
                    f"/api/patrol/offline-jobs/{current_job_id}/"
                    "current-authority/attest/"
                ),
                headers={"Idempotency-Key": f"same-actor-attest-{suffix}"},
                json={
                    "request_digest": prepared["request_digest"],
                    "reason": "Importer must not approve the same frozen evidence.",
                },
            ),
            expected_status=409,
            expected_code="same_actor_forbidden",
        )
        tampered_snapshot_id, original_snapshot_text = _tamper_current_snapshot(
            current_job_id
        )
        try:
            tamper_rejection = _expect_error_code(
                reviewer_client.post(
                    (
                        f"/api/patrol/offline-jobs/{current_job_id}/"
                        "current-authority/attest/"
                    ),
                    headers={
                        "Idempotency-Key": f"tampered-content-attest-{suffix}"
                    },
                    json={
                        "request_digest": prepared["request_digest"],
                        "reason": (
                            "Independent review must reject changed snapshot content."
                        ),
                    },
                ),
                expected_status=409,
                expected_code="snapshot_content_hash_mismatch",
            )
        finally:
            _restore_snapshot(tampered_snapshot_id, original_snapshot_text)
        attestation_path = (
            f"/api/patrol/offline-jobs/{current_job_id}/"
            "current-authority/attest/"
        )
        attestation_key = f"independent-attest-{suffix}"
        attestation_body = {
            "request_digest": prepared["request_digest"],
            "reason": (
                "Independent review confirms the frozen source, identity, "
                "scope and completeness ledgers."
            ),
        }

        def _attest_once(_index: int) -> tuple[int, dict[str, Any], str]:
            with httpx.Client(
                base_url=base_url.rstrip("/"),
                timeout=60,
                headers={
                    "Authorization": str(
                        reviewer_client.headers["Authorization"]
                    )
                },
            ) as concurrent_client:
                response = concurrent_client.post(
                    attestation_path,
                    headers={"Idempotency-Key": attestation_key},
                    json=attestation_body,
                )
                try:
                    payload = dict(response.json())
                except Exception:
                    payload = {}
                return response.status_code, payload, response.text

        with ThreadPoolExecutor(max_workers=2) as executor:
            attestation_responses = list(executor.map(_attest_once, range(2)))
        if any(item[0] != 202 for item in attestation_responses):
            raise AssertionError(
                "concurrent independent attest failed: "
                + json.dumps(attestation_responses, ensure_ascii=False)
            )
        attestation_payloads: list[dict[str, Any]] = []
        for _status, raw_payload, _text in attestation_responses:
            payload = dict(raw_payload)
            if payload.get("success") is True and isinstance(
                payload.get("data"), dict
            ):
                payload = dict(payload["data"])
            attestation_payloads.append(payload)
        qualification_ids = {
            str(item.get("qualification_id") or "")
            for item in attestation_payloads
        }
        if len(qualification_ids) != 1 or "" in qualification_ids:
            raise AssertionError(
                f"concurrent attest produced different qualifications: "
                f"{attestation_payloads}"
            )
        attested = attestation_payloads[0]
        if str(attested.get("status") or "") != "approved":
            raise AssertionError(f"qualification was not approved: {attested}")

        from src.core.check_items.current_authority import (
            PatrolCurrentAuthorityService,
        )

        def _prepare_plan(_index: int) -> dict[str, Any]:
            return PatrolCurrentAuthorityService().prepare_dispatch(
                authority_request_id
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            concurrent_plan_results = list(executor.map(_prepare_plan, range(2)))
        planned = PatrolCurrentAuthorityService().prepare_dispatch(
            authority_request_id
        )
        if planned.get("status") not in {"dispatching", "completed"}:
            raise AssertionError(f"production plan was not frozen: {planned}")
        planning_counts = _authority_runtime_counts(
            request_id=authority_request_id,
            batch_id=batch_id,
        )
        if (
            planning_counts["qualification_count"] != 1
            or planning_counts["runtime_manifest_count"] != 1
            or planning_counts["execution_plan_count"] != 2
            or planning_counts["target_count"] != 2
        ):
            raise AssertionError(
                "concurrent attestation/planning was not exactly once: "
                + json.dumps(planning_counts, ensure_ascii=False)
            )
        dispatch_task = celery.send_task(
            "dispatch_patrol_batch",
            args=[batch_id],
            queue="netops.patrol",
        )
        dispatch_result = dispatch_task.get(
            timeout=timeout_seconds,
            disable_sync_subtasks=False,
        )
        workflow_state = _wait_batch_workflows(
            batch_id,
            timeout_seconds=timeout_seconds,
        )
        if workflow_state.get("statuses") != ["completed"]:
            raise AssertionError(
                "attested production replay did not complete: "
                + json.dumps(workflow_state, ensure_ascii=False, default=str)
            )
        fact_state = _batch_fact_state(batch_id)
        if fact_state.get("execution_count") != 2:
            raise AssertionError(
                "attested replay did not preserve both target CheckExecutions: "
                + json.dumps(fact_state, ensure_ascii=False, default=str)
            )
        facts_by_device = {
            str(item.get("device_id") or ""): dict(item)
            for item in fact_state["executions"]
        }
        fact = facts_by_device[device_id]
        assessment = dict(fact.get("assessment") or {})
        if (
            fact.get("execution_status") != "completed"
            or fact.get("reason_codes")
            or assessment.get("assessment_status") != "informational"
            or assessment.get("completeness_status") != "complete"
            or assessment.get("comparison_status") != "first_observation"
            or assessment.get("blocking_reason_codes")
        ):
            raise AssertionError(
                "attested replay facts are not report-ready: "
                + json.dumps(fact_state, ensure_ascii=False, default=str)
            )
        failed_fact = facts_by_device[failed_device_id]
        failed_assessment = dict(failed_fact.get("assessment") or {})
        if (
            failed_fact.get("execution_status") == "completed"
            or failed_assessment.get("assessment_status")
            != "unable_to_determine"
            or not failed_assessment.get("blocking_reason_codes")
        ):
            raise AssertionError(
                "accounted collection failure was not retained as unknown: "
                + json.dumps(fact_state, ensure_ascii=False, default=str)
            )
        before_redelivery = _authority_runtime_counts(
            request_id=authority_request_id,
            batch_id=batch_id,
        )
        redelivery_task = celery.send_task(
            "dispatch_patrol_batch",
            args=[batch_id],
            queue="netops.patrol",
        )
        redelivery_result = redelivery_task.get(
            timeout=timeout_seconds,
            disable_sync_subtasks=False,
        )
        redelivery_workflow_state = _wait_batch_workflows(
            batch_id,
            timeout_seconds=timeout_seconds,
        )
        after_redelivery = _authority_runtime_counts(
            request_id=authority_request_id,
            batch_id=batch_id,
        )
        if after_redelivery != before_redelivery:
            raise AssertionError(
                "redelivered batch dispatch duplicated production facts: "
                + json.dumps(
                    {
                        "before": before_redelivery,
                        "after": after_redelivery,
                    },
                    ensure_ascii=False,
                )
            )
        authority_terminal = _wait_authority(
            reviewer_client,
            current_job_id,
            expected={"completed"},
            timeout_seconds=timeout_seconds,
        )

        report_dispatch = reviewer_client.post(
            f"/api/patrol/batches/{batch_id}/reports/materialize",
            headers={"Idempotency-Key": f"current-report-{suffix}"},
            json={},
        )
        if report_dispatch.status_code != 202:
            raise AssertionError(
                f"report dispatch returned {report_dispatch.status_code}: "
                f"{report_dispatch.text}"
            )
        report_payload = report_dispatch.json()
        if report_payload.get("success") is True and isinstance(
            report_payload.get("data"), dict
        ):
            report_payload = dict(report_payload["data"])
        report_status, manifest = _wait_report(
            reviewer_client,
            batch_id,
            task_id=str(report_payload["task_id"]),
            timeout_seconds=timeout_seconds,
        )
        report_json_response = reviewer_client.get(
            f"/api/workflows/runs/patrol-batches/{batch_id}/report.json"
        )
        if report_json_response.status_code != 200:
            raise AssertionError(
                f"report JSON returned {report_json_response.status_code}: "
                f"{report_json_response.text}"
            )
        report_json = report_json_response.json()
        if str(report_json.get("patrol_batch_id") or "") != batch_id:
            raise AssertionError("report JSON lost candidate batch identity")
        if str(manifest.get("report_purpose") or "") != "production_current":
            raise AssertionError("report manifest lost production_current purpose")
        if not manifest.get("source_facts_digest"):
            raise AssertionError("report manifest has no frozen facts digest")
        assessment_counts = dict(report_json.get("assessment_counts") or {})
        conclusion_permission = dict(
            report_json.get("conclusion_permission") or {}
        )
        permission_counts = dict(conclusion_permission.get("counts") or {})
        if (
            assessment_counts.get("informational") != 1
            or assessment_counts.get("unable_to_determine") != 1
            or permission_counts.get("unable_to_determine") != 1
            or conclusion_permission.get("overall_healthy_allowed") is not False
        ):
            raise AssertionError(
                "range-limited current report overstated its conclusion: "
                + json.dumps(
                    {
                        "assessment_counts": assessment_counts,
                        "conclusion_permission": conclusion_permission,
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
        downloads = _download_and_verify(
            reviewer_client,
            batch_id=batch_id,
            manifest=manifest,
        )

        revoke_response = reviewer_client.post(
            (
                f"/api/patrol/offline-jobs/{current_job_id}/"
                "current-authority/revoke/"
            ),
            headers={"Idempotency-Key": f"current-revoke-{suffix}"},
            json={
                "reason": "Synthetic engineering evidence completed its test lifecycle."
            },
        )
        if revoke_response.status_code != 200:
            raise AssertionError(
                f"revocation returned {revoke_response.status_code}: "
                f"{revoke_response.text}"
            )
        revoked = revoke_response.json()
        if revoked.get("success") is True and isinstance(revoked.get("data"), dict):
            revoked = dict(revoked["data"])
        if str(revoked.get("status") or "") != "revoked":
            raise AssertionError(f"qualification was not revoked: {revoked}")

        summary.update(
            {
                "success": True,
                "actors": {
                    "importer_principal_id": importer_id,
                    "reviewer_principal_id": reviewer_id,
                    "independent": importer_id != reviewer_id,
                },
                "historical_rejection": historical_rejection,
                "current_job_id": current_job_id,
                "preview_task_id": preview_dispatch.get("celery_task_id"),
                "source_workflow_run_id": execution.get("workflow_run_id"),
                "authority_request_id": authority_request_id,
                "authority_request_digest": prepared.get("request_digest"),
                "candidate_batch_id": batch_id,
                "idempotency": {
                    "prepare_replay_status": replay_response.status_code,
                    "conflict": idempotency_conflict,
                },
                "same_actor_rejection": same_actor_rejection,
                "tamper_rejection": tamper_rejection,
                "attestation": attested,
                "concurrency": {
                    "attestation_status_codes": [
                        item[0] for item in attestation_responses
                    ],
                    "qualification_ids": sorted(qualification_ids),
                    "planning_statuses": [
                        str(item.get("status") or "")
                        for item in concurrent_plan_results
                    ],
                    "planning_in_progress": [
                        bool(item.get("planning_in_progress"))
                        for item in concurrent_plan_results
                    ],
                    "exactly_once_counts": planning_counts,
                },
                "planned": planned,
                "dispatch_task_id": dispatch_task.id,
                "dispatch_result": dispatch_result,
                "workflow_state": workflow_state,
                "fact_state": fact_state,
                "accounted_failure_source_ledger": failed_source_ledger,
                "redelivery": {
                    "task_id": redelivery_task.id,
                    "result": redelivery_result,
                    "workflow_state": redelivery_workflow_state,
                    "before": before_redelivery,
                    "after": after_redelivery,
                },
                "authority_terminal": authority_terminal,
                "report": {
                    "materialization_status": report_status,
                    "manifest_id": manifest.get("report_manifest_id"),
                    "report_purpose": manifest.get("report_purpose"),
                    "source_facts_digest": manifest.get("source_facts_digest"),
                    "release_status": manifest.get("release_status"),
                    "facts_status": manifest.get("facts_status"),
                    "json_batch_id": report_json.get("patrol_batch_id"),
                    "overall_assessment": report_json.get("overall_assessment"),
                    "assessment_counts": assessment_counts,
                    "conclusion_permission": conclusion_permission,
                    "downloads": downloads,
                },
                "revocation": revoked,
                "runtime_fixture": runtime,
                "kept": keep,
            }
        )
    finally:
        cleanup_error: str | None = None
        if not keep:
            try:
                if current_job_id:
                    current_cleanup = _break_authority_cycles_and_delete_job(
                        job_id=current_job_id
                    )
                if historical_job_id:
                    historical_cleanup = _break_authority_cycles_and_delete_job(
                        job_id=historical_job_id
                    )
                if batch_id:
                    _cleanup_patrol_batch(
                        {
                            "suffix": suffix,
                            "success_device": device_id,
                            "failed_device": failed_device_id,
                            "implementation": f"unused-implementation-{suffix}",
                            "policy": f"unused-policy-{suffix}",
                            "fault_rule": f"unused-fault-{suffix}",
                            "recovery_rule": f"unused-recovery-{suffix}",
                            "parser": f"unused-parser-{suffix}",
                        },
                        username=f"unused-user-{suffix}",
                        profile_version_id=None,
                        runtime={},
                        batch_id=batch_id,
                    )
                _cleanup_source_workflow_and_dataset(current_cleanup)
                _cleanup_source_workflow_and_dataset(historical_cleanup)
                _cleanup_replay_dataset(authority_request_id)
                _cleanup_scope_contract(current_cleanup)
                _cleanup_scope_contract(historical_cleanup)
                if current_file_id:
                    _delete_json(
                        importer_client,
                        f"/api/storage/files/{current_file_id}/",
                    )
                if current_folder_id:
                    _delete_json(
                        importer_client,
                        f"/api/storage/folders/{current_folder_id}/",
                    )
                if historical_folder_id:
                    _delete_json(
                        importer_client,
                        f"/api/storage/folders/{historical_folder_id}/",
                    )
                _cleanup_runtime_bundle(
                    username=importer_username,
                    device_id=device_id,
                    profile_version_id=None,
                    binding_id=None,
                    bundle_version_id=runtime.get("bundle_version_id"),
                    bundle_id=runtime.get("bundle_id"),
                    comparison_created=comparison_created,
                )
                if reviewer_created:
                    _delete_user(reviewer_username)
                _cleanup_audits(
                    current_job_id or "",
                    historical_job_id or "",
                    current_file_id or "",
                    current_folder_id or "",
                    historical_folder_id or "",
                    authority_request_id or "",
                    batch_id or "",
                )
                after_runtime = _runtime_state()
                summary["cleanup"] = {
                    "current": current_cleanup,
                    "historical": historical_cleanup,
                    "database_runtime_restored": after_runtime == before_runtime,
                    "object_removed": (
                        not object_key
                        or get_minio_storage().stat_object(object_key) is None
                    ),
                }
                if not summary["cleanup"]["database_runtime_restored"]:
                    raise AssertionError(
                        f"runtime state was not restored: {before_runtime} != "
                        f"{after_runtime}"
                    )
                if not summary["cleanup"]["object_removed"]:
                    raise AssertionError("uploaded MinIO evidence was not removed")
            except Exception as exc:
                cleanup_error = f"{type(exc).__name__}: {exc}"
                summary["cleanup_error"] = cleanup_error
        importer_client.close()
        reviewer_client.close()
        if importer_created and keep is False:
            _delete_user(importer_username)
        if reviewer_created and keep is False:
            _delete_user(reviewer_username)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        if cleanup_error:
            raise AssertionError(cleanup_error)
    if summary.get("success") is not True:
        raise AssertionError(summary.get("error") or "current authority E2E failed")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify governed offline-current evidence through the real stack"
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("NETOPS_BFF_BASE_URL", "http://localhost:8001"),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=180,
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=(
            ROOT
            / ".tmp"
            / "patrol-current-authority-full-stack-e2e"
            / "summary.json"
        ),
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the isolated engineering fixtures for manual browser review.",
    )
    args = parser.parse_args()
    result = run(
        base_url=args.base_url,
        output_path=args.output_path.resolve(),
        timeout_seconds=max(30, int(args.timeout_seconds)),
        keep=bool(args.keep),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

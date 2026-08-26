#!/usr/bin/env python
"""Exercise the rule-asset source import through the real platform stack.

The source is one real ``display version`` command block extracted from the
``lixian`` corpus. The test uses the same BFF, presigned MinIO upload, Celery
preview, Workflow and PostgreSQL contracts as the browser. It does not invoke
an LLM or publish any production rule/runtime asset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.celery_tasks.celery_app import celery
from src.core.patrol.command_splitter import (
    infer_device_from_filename,
    split_cli_capture,
)
from src.infrastructure.db.models import (
    AuditLogRecord,
    CommandSnapshot,
    FileMetadata,
    Notification,
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    ParserModelCallLedger,
    ParserTemplateJob,
    PatrolDataset,
    PatrolRun,
    RuleAssetCommandUnitRecord,
    StorageFolder,
    WorkflowRun,
    WorkflowStepRecord,
)
from src.infrastructure.db.postgres import get_db_session
from src.infrastructure.storage.minio_client import get_minio_storage

CANONICAL_COMMAND = "display version"
EXPECTED_VENDOR = "H3C"
EXPECTED_MODEL = "MSR5620"
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}


def _login(client: httpx.Client, *, username: str, password: str) -> str:
    response = client.post(
        "/api/auth/login/",
        json={"username": username, "password": password},
    )
    if response.status_code >= 400:
        raise AssertionError(
            f"BFF login returned {response.status_code}: {response.text}"
        )
    payload = response.json()
    access = str((payload.get("data") or {}).get("access") or "")
    if not access:
        raise AssertionError("BFF login did not return an access token")
    return access


def _request_json(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    expected_statuses: tuple[int, ...] = (200, 201, 202),
) -> dict[str, Any]:
    response = client.request(method, path, json=json_body)
    if response.status_code not in expected_statuses:
        raise AssertionError(
            f"{method} {path} returned {response.status_code}: {response.text}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise AssertionError(f"{method} {path} returned a non-object payload")
    if payload.get("success") is True and isinstance(payload.get("data"), dict):
        return dict(payload["data"])
    return payload


def _decode_cli_capture(raw_bytes: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return raw_bytes.decode(encoding, errors="strict"), encoding
        except UnicodeDecodeError:
            continue
    raise AssertionError("source CLI capture is not valid UTF-8, UTF-16 or GB18030")


def _load_lixian_command(
    source_file: Path,
    *,
    canonical_command: str = CANONICAL_COMMAND,
) -> tuple[bytes, dict[str, Any]]:
    raw_bytes = source_file.read_bytes()
    raw_capture, source_encoding = _decode_cli_capture(raw_bytes)
    device_name, management_ip = infer_device_from_filename(source_file)
    blocks = split_cli_capture(raw_capture, device_name=device_name)
    matches = [
        block
        for block in blocks
        if block.command_canonical == canonical_command
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected one {canonical_command!r} block in {source_file}, "
            f"got {len(matches)}"
        )
    block = matches[0]
    resolved_device_name = str(device_name or block.device_name)
    capture = (
        f"<{resolved_device_name}>{block.command}\n"
        f"{block.raw_output}\n"
        f"<{resolved_device_name}>\n"
    ).encode()
    return capture, {
        "source_file": str(source_file.resolve()),
        "source_file_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "source_file_size": len(raw_bytes),
        "source_encoding": source_encoding,
        "source_command_start_line": block.start_line,
        "source_command_end_line": block.end_line,
        "device_name": resolved_device_name,
        "management_ip": management_ip,
        "canonical_command": block.command_canonical,
        "extracted_raw_sha256": hashlib.sha256(
            block.raw_output.encode("utf-8")
        ).hexdigest(),
        "upload_sha256": hashlib.sha256(capture).hexdigest(),
        "upload_size": len(capture),
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
            f"/api/parser/asset-source-jobs/{job_id}/",
        )
        status = str(last.get("status") or "")
        if status in expected:
            return last
        if status in TERMINAL_JOB_STATUSES and status not in expected:
            raise AssertionError(
                f"offline source job reached unexpected terminal status "
                f"{status}: {last.get('error_message')}"
            )
        time.sleep(0.5)
    raise TimeoutError(
        f"offline source job did not reach {sorted(expected)}: "
        f"last status={last.get('status')}"
    )


def _database_baseline() -> dict[str, int]:
    with get_db_session() as db:
        return {
            "parser_jobs": db.query(ParserTemplateJob).count(),
            "parser_model_calls": db.query(ParserModelCallLedger).count(),
        }


def _database_evidence(job_id: str) -> dict[str, Any]:
    with get_db_session() as db:
        job = db.get(OfflinePatrolJobRecord, job_id)
        if job is None:
            raise AssertionError("offline source job was not persisted")
        period = (
            db.query(OfflinePatrolPeriodRecord)
            .filter(
                OfflinePatrolPeriodRecord.job_id == job_id,
                OfflinePatrolPeriodRecord.role == "sample",
            )
            .one()
        )
        if not period.patrol_run_id:
            raise AssertionError("offline source period did not receive a PatrolRun")
        run = db.get(PatrolRun, period.patrol_run_id)
        if run is None:
            raise AssertionError("parser-validation PatrolRun was not persisted")
        snapshots = (
            db.query(CommandSnapshot)
            .filter(CommandSnapshot.run_id == run.id)
            .order_by(CommandSnapshot.canonical_command, CommandSnapshot.id)
            .all()
        )
        units = (
            db.query(RuleAssetCommandUnitRecord)
            .filter(RuleAssetCommandUnitRecord.offline_job_id == job_id)
            .order_by(RuleAssetCommandUnitRecord.canonical_command)
            .all()
        )
        workflow = db.get(WorkflowRun, job.workflow_run_id) if job.workflow_run_id else None
        workflow_steps = (
            db.query(WorkflowStepRecord)
            .filter(WorkflowStepRecord.run_id == job.workflow_run_id)
            .order_by(WorkflowStepRecord.step_index)
            .all()
            if job.workflow_run_id
            else []
        )
        audits = (
            db.query(AuditLogRecord)
            .filter(AuditLogRecord.resource_id == job_id)
            .order_by(AuditLogRecord.created_at)
            .all()
        )
        return {
            "job": {
                "id": job.id,
                "purpose": job.purpose,
                "job_type": job.job_type,
                "status": job.status,
                "folder_id": job.folder_id,
                "workflow_run_id": job.workflow_run_id,
            },
            "period": {
                "id": period.id,
                "dataset_id": period.dataset_id,
                "patrol_run_id": period.patrol_run_id,
                "status": period.status,
            },
            "run": {
                "id": run.id,
                "mode": run.mode,
                "source": run.source,
                "run_purpose": run.run_purpose,
                "status": run.status,
                "command_count": run.command_count,
                "metadata": dict(run.metadata_json or {}),
            },
            "snapshots": [
                {
                    "id": row.id,
                    "dataset_id": row.dataset_id,
                    "inventory_device_id": row.inventory_device_id,
                    "device_name": row.device_name,
                    "management_ip": row.management_ip,
                    "vendor": row.vendor,
                    "model": row.model,
                    "canonical_command": row.canonical_command,
                    "parse_status": row.parse_status,
                    "parser_template_id": row.parser_template_id,
                    "structured_record_count": (
                        len(row.structured_json)
                        if isinstance(row.structured_json, list)
                        else 1
                        if isinstance(row.structured_json, dict)
                        else 0
                    ),
                    "raw_text_hash": row.raw_text_hash,
                }
                for row in snapshots
            ],
            "command_units": [
                {
                    "id": row.id,
                    "asset_key": row.asset_key,
                    "vendor": row.vendor,
                    "model": row.model,
                    "models": list(row.models_json or []),
                    "profile": row.profile,
                    "command": row.canonical_command,
                    "asset_mode": row.asset_mode,
                    "status": row.status,
                    "current_stage": row.current_stage,
                    "parser_template_id": row.parser_template_id,
                    "sample_count": row.sample_count,
                    "device_count": row.device_count,
                    "output_signature_count": row.output_signature_count,
                }
                for row in units
            ],
            "workflow": {
                "id": workflow.id if workflow else None,
                "template_name": workflow.template_name if workflow else None,
                "status": workflow.status if workflow else None,
                "steps": [
                    {
                        "index": row.step_index,
                        "name": row.step_name,
                        "skill": row.skill_name,
                        "status": row.status,
                    }
                    for row in workflow_steps
                ],
            },
            "audit_actions": [str(row.action) for row in audits],
        }


def _assert_evidence(
    *,
    evidence: dict[str, Any],
    pipeline: dict[str, Any],
    units_page: dict[str, Any],
    production_pages: dict[str, dict[str, Any]],
    baseline: dict[str, int],
    after: dict[str, int],
) -> None:
    job = evidence["job"]
    if job["purpose"] != "rule_asset_source":
        raise AssertionError(f"unexpected source purpose: {job}")
    if job["job_type"] != "parser_validation" or job["status"] != "completed":
        raise AssertionError(f"unexpected source job terminal state: {job}")
    run = evidence["run"]
    if (
        run["mode"] != "parser_validation"
        or run["run_purpose"] != "parser_validation"
        or run["source"] != "offline_import"
    ):
        raise AssertionError(f"rule sample escaped parser-validation scope: {run}")
    snapshots = evidence["snapshots"]
    if len(snapshots) != 1:
        raise AssertionError(f"expected one imported snapshot, got {len(snapshots)}")
    snapshot = snapshots[0]
    if (
        snapshot["canonical_command"] != CANONICAL_COMMAND
        or snapshot["parse_status"] != "parsed"
        or not snapshot["parser_template_id"]
        or snapshot["structured_record_count"] < 1
    ):
        raise AssertionError(f"active Parser did not produce structured data: {snapshot}")
    if snapshot["inventory_device_id"] is not None:
        raise AssertionError("external rule sample was incorrectly bound to Inventory")
    if after != baseline:
        raise AssertionError(
            "an already-supported command unexpectedly created a Parser job or "
            f"model call: {baseline} -> {after}"
        )
    step_states = {
        str(item.get("key")): str(item.get("status"))
        for item in pipeline.get("steps") or []
    }
    for required in ("source", "preview", "ingest", "parser"):
        if step_states.get(required) != "completed":
            raise AssertionError(
                f"pipeline stage {required} is not completed: {step_states}"
            )
    unit_items = units_page.get("items") or []
    if len(unit_items) != 1:
        raise AssertionError(
            f"expected one command production unit, got {len(unit_items)}"
        )
    if str(unit_items[0].get("canonical_command") or "") != CANONICAL_COMMAND:
        raise AssertionError(f"unexpected command production unit: {unit_items[0]}")
    if unit_items[0].get("context_id") or unit_items[0].get("bundle_id"):
        raise AssertionError(
            "command unit reused generation evidence from another offline source: "
            f"{unit_items[0]}"
        )
    database_units = evidence["command_units"]
    if len(database_units) != 1:
        raise AssertionError(
            f"expected one PostgreSQL command unit, got {len(database_units)}"
        )
    database_unit = database_units[0]
    production_page = production_pages["first_page"]
    production_items = production_page.get("items") or []
    if (
        production_page.get("page") != 1
        or production_page.get("page_size") != 1
        or production_page.get("total") != 1
        or production_page.get("page_count") != 1
        or len(production_items) != 1
    ):
        raise AssertionError(
            f"global production pagination contract is invalid: {production_page}"
        )
    production_unit = production_items[0]
    projection_fields = {
        "id": database_unit["id"],
        "offline_job_id": job["id"],
        "asset_key": database_unit["asset_key"],
        "vendor": database_unit["vendor"],
        "model": database_unit["model"],
        "models": database_unit["models"],
        "profile": database_unit["profile"],
        "canonical_command": database_unit["command"],
        "sample_count": database_unit["sample_count"],
        "device_count": database_unit["device_count"],
        "output_signature_count": database_unit["output_signature_count"],
    }
    mismatched = {
        key: {"database": expected, "api": production_unit.get(key)}
        for key, expected in projection_fields.items()
        if production_unit.get(key) != expected
    }
    if mismatched:
        raise AssertionError(
            f"global production API diverges from PostgreSQL projection: {mismatched}"
        )
    if production_unit.get("output_signature_count") != 1:
        raise AssertionError(
            "one imported output must expose one source structure even when an "
            "Active Parser already exists"
        )
    second_page = production_pages["second_page"]
    if (
        second_page.get("page") != 2
        or second_page.get("total") != 1
        or second_page.get("page_count") != 1
        or second_page.get("items")
    ):
        raise AssertionError(f"production second-page boundary is invalid: {second_page}")
    search_miss = production_pages["search_miss"]
    if (
        search_miss.get("total") != 0
        or search_miss.get("page_count") != 0
        or search_miss.get("items")
    ):
        raise AssertionError(
            f"production search/filter isolation is invalid: {search_miss}"
        )
    workflow = evidence["workflow"]
    if (
        workflow["template_name"] != "parser-validation-ingest"
        or workflow["status"] != "completed"
        or not workflow["steps"]
        or any(item["status"] != "completed" for item in workflow["steps"])
    ):
        raise AssertionError(f"parser-validation Workflow is incomplete: {workflow}")
    required_audits = {
        "rule_asset_source_create",
        "rule_asset_source_attach",
        "rule_asset_source_preview",
        "rule_asset_source_binding_confirm",
        "rule_asset_source_execute",
    }
    missing_audits = required_audits - set(evidence["audit_actions"])
    if missing_audits:
        raise AssertionError(
            f"rule-source audit trail is incomplete: {sorted(missing_audits)}"
        )


def _delete_json(
    client: httpx.Client,
    path: str,
    *,
    expected_statuses: tuple[int, ...] = (200, 204, 404),
) -> None:
    response = client.delete(path)
    if response.status_code not in expected_statuses:
        raise AssertionError(
            f"DELETE {path} returned {response.status_code}: {response.text}"
        )


def _cleanup_database(
    *,
    job_id: str,
    workflow_run_id: str | None,
    patrol_run_id: str | None,
    dataset_id: str | None,
    file_id: str | None,
    folder_id: str | None,
) -> None:
    with get_db_session() as db:
        db.query(OfflinePatrolJobRecord).filter(
            OfflinePatrolJobRecord.id == job_id
        ).delete(synchronize_session=False)
        if workflow_run_id:
            db.query(Notification).filter(
                Notification.workflow_run_id == workflow_run_id
            ).delete(synchronize_session=False)
            db.query(WorkflowStepRecord).filter(
                WorkflowStepRecord.run_id == workflow_run_id
            ).delete(synchronize_session=False)
            db.query(WorkflowRun).filter(
                WorkflowRun.id == workflow_run_id
            ).delete(synchronize_session=False)
        if patrol_run_id:
            db.query(PatrolRun).filter(
                PatrolRun.id == patrol_run_id
            ).delete(synchronize_session=False)
        if dataset_id:
            db.query(PatrolDataset).filter(
                PatrolDataset.id == dataset_id
            ).delete(synchronize_session=False)
        db.query(AuditLogRecord).filter(
            AuditLogRecord.resource_id.in_(
                [
                    value
                    for value in (job_id, file_id, folder_id)
                    if value
                ]
            )
        ).delete(synchronize_session=False)
        if file_id:
            db.query(FileMetadata).filter(
                FileMetadata.id == file_id
            ).delete(synchronize_session=False)
        if folder_id:
            db.query(StorageFolder).filter(
                StorageFolder.id == folder_id
            ).delete(synchronize_session=False)


def _assert_cleanup(
    *,
    job_id: str,
    workflow_run_id: str | None,
    patrol_run_id: str | None,
    file_id: str | None,
    folder_id: str | None,
    object_key: str | None,
) -> dict[str, bool]:
    with get_db_session() as db:
        state = {
            "job_removed": db.get(OfflinePatrolJobRecord, job_id) is None,
            "workflow_removed": (
                not workflow_run_id or db.get(WorkflowRun, workflow_run_id) is None
            ),
            "run_removed": (
                not patrol_run_id or db.get(PatrolRun, patrol_run_id) is None
            ),
            "file_removed": (
                not file_id or db.get(FileMetadata, file_id) is None
            ),
            "folder_removed": (
                not folder_id or db.get(StorageFolder, folder_id) is None
            ),
        }
    if object_key:
        state["object_removed"] = (
            get_minio_storage().stat_object(object_key) is None
        )
    if not all(state.values()):
        raise AssertionError(f"full-stack E2E cleanup is incomplete: {state}")
    return state


def run(
    *,
    base_url: str,
    username: str,
    password: str,
    source_file: Path,
    output_path: Path,
    timeout_seconds: int,
    keep: bool,
) -> dict[str, Any]:
    source_bytes, provenance = _load_lixian_command(source_file)
    suffix = uuid.uuid4().hex[:12]
    upload_name = f"sample-{suffix}.txt"
    job_id: str | None = None
    file_id: str | None = None
    folder_id: str | None = None
    object_key: str | None = None
    workflow_run_id: str | None = None
    patrol_run_id: str | None = None
    dataset_id: str | None = None
    summary: dict[str, Any] = {
        "schema_version": "rule-asset-source-import-full-stack-e2e/v1",
        "base_url": base_url,
        "success": False,
        "source_provenance": provenance,
    }
    client = httpx.Client(base_url=base_url.rstrip("/"), timeout=30)
    try:
        token = _login(client, username=username, password=password)
        client.headers["Authorization"] = f"Bearer {token}"
        baseline = _database_baseline()
        created = _request_json(
            client,
            "POST",
            "/api/parser/asset-source-jobs/",
            json_body={
                "job_type": "parser_validation",
                "name": f"E2E rule source {suffix}",
                "periods": [
                    {
                        "role": "sample",
                        "label": "Q2 real command sample",
                        "observed_at": "2025-12-24T15:24:35+08:00",
                    }
                ],
                "vendor": EXPECTED_VENDOR,
                "model": EXPECTED_MODEL,
                "publish_events": False,
                "idempotency_key": f"rule-source-e2e-create-{suffix}",
            },
        )
        job_id = str(created["id"])
        folder_id = str(created["folder_id"])

        upload = _request_json(
            client,
            "POST",
            "/api/storage/upload/init/",
            json_body={
                "filename": upload_name,
                "folder_id": folder_id,
                "visibility": "private",
                "content_type": "text/plain",
                "size_bytes": len(source_bytes),
            },
        )
        file_id = str(upload["file_id"])
        object_key = str(upload["object_key"])
        upload_url = str(upload["upload_url"])
        put = httpx.put(
            upload_url,
            content=source_bytes,
            headers={"Content-Type": "text/plain"},
            timeout=30,
        )
        if put.status_code >= 400:
            raise AssertionError(
                f"presigned MinIO PUT returned {put.status_code}: {put.text}"
            )
        _request_json(
            client,
            "POST",
            "/api/storage/upload/complete/",
            json_body={"file_id": file_id, "size_bytes": len(source_bytes)},
        )
        stored = get_minio_storage().download_file(object_key)
        if stored != source_bytes:
            raise AssertionError("MinIO object bytes differ from the uploaded sample")

        _request_json(
            client,
            "POST",
            f"/api/parser/asset-source-jobs/{job_id}/sources/",
            json_body={
                "period_role": "sample",
                "file_id": file_id,
                "relative_path": provenance["source_file"].split("\\")[-1],
                "sha256": provenance["upload_sha256"],
                "size_bytes": len(source_bytes),
            },
        )
        preview_dispatch = _request_json(
            client,
            "POST",
            f"/api/parser/asset-source-jobs/{job_id}/preview/",
            json_body={},
        )
        preview = _wait_job(
            client,
            job_id,
            expected={"ready", "blocked"},
            timeout_seconds=timeout_seconds,
        )
        if preview["status"] != "ready":
            raise AssertionError(
                f"rule-source preview was blocked: {preview.get('blockers')}"
            )
        bindings = preview.get("device_bindings") or []
        if not bindings:
            raise AssertionError("preview did not create an auditable device binding")
        _request_json(
            client,
            "POST",
            f"/api/parser/asset-source-jobs/{job_id}/device-bindings/confirm/",
            json_body={
                "bindings": [
                    {
                        "binding_id": str(item["id"]),
                        "inventory_device_id": None,
                    }
                    for item in bindings
                ]
            },
        )
        execution = _request_json(
            client,
            "POST",
            f"/api/parser/asset-source-jobs/{job_id}/execute/",
            json_body={
                "idempotency_key": f"rule-source-e2e-execute-{suffix}"
            },
        )
        workflow_run_id = str(execution["workflow_run_id"])
        completed = _wait_job(
            client,
            job_id,
            expected={"completed"},
            timeout_seconds=timeout_seconds,
        )
        period = (completed.get("periods") or [None])[0]
        if not isinstance(period, dict):
            raise AssertionError("completed job did not return its sample period")
        patrol_run_id = str(period.get("patrol_run_id") or "")
        dataset_id = str(period.get("dataset_id") or "")

        _request_json(
            client,
            "POST",
            (
                f"/api/parser/asset-source-jobs/{job_id}/"
                "command-asset-units/refresh/"
            ),
            json_body={},
        )
        pipeline = _request_json(
            client,
            "GET",
            (
                f"/api/parser/asset-source-jobs/{job_id}/"
                "asset-production-pipeline/"
            ),
        )
        units_page = _request_json(
            client,
            "GET",
            (
                f"/api/parser/asset-source-jobs/{job_id}/"
                "command-asset-units/?page=1&page_size=20"
            ),
        )
        production_query = urlencode(
            {
                "offline_job_id": job_id,
                "page": 1,
                "page_size": 1,
                "search": CANONICAL_COMMAND,
                "vendor": EXPECTED_VENDOR,
                "model": EXPECTED_MODEL,
            }
        )
        production_pages = {
            "first_page": _request_json(
                client,
                "GET",
                f"/api/parser/asset-production/units/?{production_query}",
            ),
            "second_page": _request_json(
                client,
                "GET",
                (
                    "/api/parser/asset-production/units/?"
                    + urlencode(
                        {
                            "offline_job_id": job_id,
                            "page": 2,
                            "page_size": 1,
                            "search": CANONICAL_COMMAND,
                            "vendor": EXPECTED_VENDOR,
                            "model": EXPECTED_MODEL,
                        }
                    )
                ),
            ),
            "search_miss": _request_json(
                client,
                "GET",
                (
                    "/api/parser/asset-production/units/?"
                    + urlencode(
                        {
                            "offline_job_id": job_id,
                            "page": 1,
                            "page_size": 1,
                            "search": f"missing-{suffix}",
                        }
                    )
                ),
            ),
        }
        evidence = _database_evidence(job_id)
        after = _database_baseline()
        _assert_evidence(
            evidence=evidence,
            pipeline=pipeline,
            units_page=units_page,
            production_pages=production_pages,
            baseline=baseline,
            after=after,
        )
        preview_task_id = str(preview_dispatch.get("celery_task_id") or "")
        preview_task_state = (
            str(celery.AsyncResult(preview_task_id).state)
            if preview_task_id
            else "MISSING"
        )
        if preview_task_state != "SUCCESS":
            raise AssertionError(
                f"preview Celery task is not successful: {preview_task_state}"
            )
        summary.update(
            {
                "success": True,
                "job_id": job_id,
                "file_id": file_id,
                "folder_id": folder_id,
                "object_key": object_key,
                "preview_task_id": preview_task_id,
                "preview_task_state": preview_task_state,
                "workflow_run_id": workflow_run_id,
                "patrol_run_id": patrol_run_id,
                "dataset_id": dataset_id,
                "preview_totals": (preview.get("preview") or {}).get("totals")
                or preview.get("preview_totals")
                or {},
                "database_evidence": evidence,
                "pipeline": pipeline,
                "command_units": units_page,
                "production_pages": production_pages,
                "parser_governance_before": baseline,
                "parser_governance_after": after,
                "kept": keep,
            }
        )
    finally:
        cleanup_error: str | None = None
        if job_id and not keep:
            try:
                _cleanup_database(
                    job_id=job_id,
                    workflow_run_id=workflow_run_id,
                    patrol_run_id=patrol_run_id,
                    dataset_id=dataset_id,
                    file_id=None,
                    folder_id=None,
                )
                if file_id:
                    _delete_json(client, f"/api/storage/files/{file_id}/")
                if folder_id:
                    _delete_json(client, f"/api/storage/folders/{folder_id}/")
                _cleanup_database(
                    job_id=job_id,
                    workflow_run_id=None,
                    patrol_run_id=None,
                    dataset_id=None,
                    file_id=file_id,
                    folder_id=folder_id,
                )
                summary["cleanup"] = _assert_cleanup(
                    job_id=job_id,
                    workflow_run_id=workflow_run_id,
                    patrol_run_id=patrol_run_id,
                    file_id=file_id,
                    folder_id=folder_id,
                    object_key=object_key,
                )
            except Exception as exc:
                cleanup_error = f"{type(exc).__name__}: {exc}"
                summary["cleanup_error"] = cleanup_error
        client.close()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        if cleanup_error:
            raise AssertionError(cleanup_error)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the real rule-asset offline source import stack"
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("NETOPS_BFF_BASE_URL", "http://localhost:8001"),
    )
    parser.add_argument(
        "--username",
        default=os.getenv("NETOPS_E2E_USERNAME", "admin"),
    )
    parser.add_argument(
        "--password",
        default=os.getenv("NETOPS_E2E_PASSWORD", ""),
    )
    parser.add_argument(
        "--source-file",
        type=Path,
        default=(
            ROOT
            / "lixian"
            / "Q2"
            / "LAB-DEVICE-RT5620-1-198.51.2.219.txt"
        ),
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=(
            ROOT
            / ".tmp"
            / "rule-asset-source-import-full-stack-e2e"
            / "summary.json"
        ),
    )
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the generated job and storage object for manual UI review.",
    )
    args = parser.parse_args()
    if not args.password:
        raise SystemExit(
            "NETOPS_E2E_PASSWORD or --password is required for BFF login"
        )
    result = run(
        base_url=args.base_url,
        username=args.username,
        password=args.password,
        source_file=args.source_file.resolve(),
        output_path=args.output_path.resolve(),
        timeout_seconds=max(10, args.timeout_seconds),
        keep=args.keep,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

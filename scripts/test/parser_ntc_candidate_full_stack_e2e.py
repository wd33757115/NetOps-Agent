#!/usr/bin/env python
"""Exercise one NTC-first Parser candidate through the real platform stack.

The test imports one real H3C command from ``lixian`` as an explicit rule-asset
source, configures the temporary Parser Job through the same HTTP API as the
control plane, dispatches the real Celery worker, and verifies the immutable
candidate plus the human-review comparison payload. It never approves or
activates the candidate and removes only its own E2E data.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.test.rule_asset_source_import_full_stack_e2e import (
    _assert_cleanup,
    _cleanup_database,
    _delete_json,
    _load_lixian_command,
    _login,
    _request_json,
    _wait_job,
)
from src.core.celery_tasks.celery_app import celery
from src.infrastructure.db.models import (
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    ParserGenerationAttempt,
    ParserJobSample,
    ParserModelCallLedger,
    ParserTemplate,
    ParserTemplateJob,
)
from src.infrastructure.db.postgres import get_db_session

CANONICAL_COMMAND = "display device manuinfo"
EXPECTED_VENDOR = "H3C"
EXPECTED_NTC_TEMPLATE = "hp_comware_display_device_manuinfo.textfsm"
PARSER_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "superseded"}


def _create_temporary_operator(suffix: str) -> tuple[str, str]:
    django_root = ROOT / "web" / "django_backend"
    if str(django_root) not in sys.path:
        sys.path.insert(0, str(django_root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_backend.settings")
    import django

    django.setup()
    from bff.roles import assign_user_role
    from django.contrib.auth import get_user_model

    username = f"e2e_parser_revision_{suffix}"
    password = secrets.token_urlsafe(24)
    user = get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.invalid",
        password=password,
    )
    assign_user_role(user, "operator")
    user.save()
    return username, password


def _delete_temporary_operator(username: str | None) -> None:
    if not username:
        return
    from django.contrib.auth import get_user_model

    get_user_model().objects.filter(username=username).delete()


def _parser_spec() -> dict[str, Any]:
    return {
        "entity": "device_component",
        "primary_keys": ["slot_type", "slot_id"],
        "fields": {
            "slot_type": {
                "required": True,
                "type": "text",
                "source_labels": ["Slot", "Fan", "Power", "Chassis"],
                "role": "identity",
                "operational_reason": "Identifies the component class.",
                "change_behavior": "stable",
                "monitor": False,
                "canonical_candidate": "device.component.type",
            },
            "slot_id": {
                "required": True,
                "type": "text",
                "source_labels": ["component number"],
                "role": "identity",
                "operational_reason": "Identifies one component within its class.",
                "change_behavior": "stable",
                "monitor": False,
                "canonical_candidate": "device.component.id",
            },
            "device_name": {
                "required": False,
                "type": "text",
                "source_labels": ["DEVICE_NAME"],
                "role": "context",
                "operational_reason": "Reports the installed component model.",
                "change_behavior": "stable",
                "monitor": True,
                "canonical_candidate": "device.component.model",
            },
            "device_serial_number": {
                "required": False,
                "type": "text",
                "source_labels": ["DEVICE_SERIAL_NUMBER", "MANU SERIAL NUMBER"],
                "role": "identity",
                "operational_reason": "Supports inventory reconciliation and replacement tracking.",
                "change_behavior": "stable",
                "monitor": True,
                "canonical_candidate": "device.component.serial_number",
            },
            "manufacturing_date": {
                "required": False,
                "type": "text",
                "source_labels": ["MANUFACTURING_DATE"],
                "role": "context",
                "operational_reason": "Supports lifecycle and aging review.",
                "change_behavior": "stable",
                "monitor": False,
                "canonical_candidate": "device.component.manufacturing_date",
            },
            "vendor_name": {
                "required": False,
                "type": "text",
                "source_labels": ["VENDOR_NAME"],
                "role": "context",
                "operational_reason": "Reports the component manufacturer.",
                "change_behavior": "stable",
                "monitor": False,
                "canonical_candidate": "device.component.vendor",
            },
            "mac_address": {
                "required": False,
                "type": "text",
                "source_labels": ["MAC_ADDRESS"],
                "role": "identity",
                "operational_reason": "Provides an additional stable hardware identifier.",
                "change_behavior": "stable",
                "monitor": False,
                "canonical_candidate": "device.component.mac_address",
            },
        },
        "allow_empty": False,
        "empty_patterns": [],
    }


def _intentionally_unfulfillable_parser_spec() -> dict[str, Any]:
    return {
        "entity": "device_component",
        "primary_keys": ["impossible_component_id"],
        "fields": {
            "impossible_component_id": {
                "required": True,
                "type": "regex",
                "pattern": r"^E2E-IMPOSSIBLE-[A-F0-9]{64}$",
                "source_labels": ["label absent from every source sample"],
                "role": "identity",
                "operational_reason": (
                    "Forces the first isolated generation attempt to fail so "
                    "the governed ParserSpec revision path can be exercised."
                ),
                "change_behavior": "stable",
                "monitor": False,
            }
        },
        "allow_empty": False,
        "empty_patterns": [],
    }


def _parser_spec_with_optional_revision_field() -> dict[str, Any]:
    spec = _parser_spec()
    spec["fields"]["component_note"] = {
        "required": False,
        "type": "text",
        "source_labels": ["optional component note"],
        "role": "context",
        "operational_reason": (
            "Exercises invalidation and regeneration without widening the "
            "required production record contract."
        ),
        "change_behavior": "volatile",
        "monitor": False,
    }
    return spec


def _find_parser_job(*, model: str) -> dict[str, Any]:
    with get_db_session() as db:
        rows = (
            db.query(ParserTemplateJob)
            .filter(
                ParserTemplateJob.vendor == EXPECTED_VENDOR,
                ParserTemplateJob.model.ilike(model),
                ParserTemplateJob.canonical_command == CANONICAL_COMMAND,
            )
            .all()
        )
        if len(rows) != 1:
            raise AssertionError(
                f"expected one isolated Parser Job for {model}, got {len(rows)}"
            )
        row = rows[0]
        samples = (
            db.query(ParserJobSample)
            .filter(ParserJobSample.job_id == row.id)
            .order_by(ParserJobSample.created_at, ParserJobSample.id)
            .all()
        )
        return {
            "id": str(row.id),
            "status": str(row.status),
            "model": str(row.model),
            "profile": str(row.profile),
            "sample_ids": [str(item.id) for item in samples],
            "snapshot_ids": [str(item.snapshot_id) for item in samples],
        }


def _isolated_parser_job_ids(*, model: str) -> list[str]:
    with get_db_session() as db:
        return [
            str(row.id)
            for row in (
                db.query(ParserTemplateJob)
                .filter(
                    ParserTemplateJob.vendor == EXPECTED_VENDOR,
                    ParserTemplateJob.model.ilike(model),
                    ParserTemplateJob.canonical_command == CANONICAL_COMMAND,
                )
                .all()
            )
        ]


def _wait_parser_job(
    client: httpx.Client,
    job_id: str,
    *,
    timeout_seconds: int,
    expected_status: str = "succeeded",
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _request_json(client, "GET", f"/api/parser/jobs/{job_id}/")
        status = str(last.get("status") or "")
        if status in PARSER_TERMINAL_STATUSES:
            if status != expected_status:
                raise AssertionError(
                    "Parser generation reached "
                    f"{status}, expected {expected_status}: "
                    f"{last.get('failure_code')} {last.get('last_error')}"
                )
            return last
        time.sleep(1)
    raise TimeoutError(
        f"Parser Job did not finish: {job_id}, last={last.get('status')}"
    )


def _candidate_rows(job_id: str) -> list[dict[str, str]]:
    with get_db_session() as db:
        rows = (
            db.query(ParserTemplate)
            .filter(ParserTemplate.source_job_id == job_id)
            .order_by(ParserTemplate.created_at.desc(), ParserTemplate.id.desc())
            .all()
        )
        return [
            {
                "id": str(item.id),
                "status": str(item.status),
                "sha256": str(item.sha256),
            }
            for item in rows
        ]


def _candidate_evidence(job_id: str) -> dict[str, Any]:
    with get_db_session() as db:
        candidates = (
            db.query(ParserTemplate)
            .filter(ParserTemplate.source_job_id == job_id)
            .order_by(ParserTemplate.created_at.desc(), ParserTemplate.id.desc())
            .all()
        )
        current_candidates = [
            item
            for item in candidates
            if str(item.status) in {"pending_approval", "active"}
        ]
        if len(current_candidates) != 1:
            raise AssertionError(
                "expected one current candidate for Parser Job "
                f"{job_id}, got {len(current_candidates)} from {len(candidates)} total"
            )
        candidate = current_candidates[0]
        attempts = (
            db.query(ParserGenerationAttempt)
            .filter(ParserGenerationAttempt.job_id == job_id)
            .order_by(ParserGenerationAttempt.created_at, ParserGenerationAttempt.id)
            .all()
        )
        model_calls = (
            db.query(ParserModelCallLedger)
            .filter(ParserModelCallLedger.job_id == job_id)
            .order_by(ParserModelCallLedger.call_number)
            .all()
        )
        validation = dict(candidate.validation_json or {})
        provenance = dict(validation.get("candidate_provenance") or {})
        attempt_provenance = [
            dict((item.validation_report_json or {}).get("candidate_provenance") or {})
            for item in attempts
        ]
        return {
            "candidate_id": str(candidate.id),
            "status": str(candidate.status),
            "source_job_id": str(candidate.source_job_id or ""),
            "profile": str(candidate.profile),
            "sha256": str(candidate.sha256),
            "validation": validation,
            "attempts": [
                {
                    "id": str(item.id),
                    "action": str(item.action),
                    "status": str(item.status),
                    "model_provider": item.model_provider,
                    "candidate_provenance": attempt_provenance[index],
                }
                for index, item in enumerate(attempts)
            ],
            "model_call_ledger": [item.to_dict() for item in model_calls],
            "selected_provenance": provenance,
            "candidate_history": [
                {
                    "id": str(item.id),
                    "status": str(item.status),
                    "sha256": str(item.sha256),
                }
                for item in candidates
            ],
        }


def _source_graph(job_id: str) -> dict[str, str | None]:
    with get_db_session() as db:
        job = db.get(OfflinePatrolJobRecord, job_id)
        if job is None:
            raise AssertionError("offline source job disappeared before cleanup")
        period = (
            db.query(OfflinePatrolPeriodRecord)
            .filter(
                OfflinePatrolPeriodRecord.job_id == job_id,
                OfflinePatrolPeriodRecord.role == "sample",
            )
            .one()
        )
        return {
            "workflow_run_id": str(job.workflow_run_id or "") or None,
            "patrol_run_id": str(period.patrol_run_id or "") or None,
            "dataset_id": str(period.dataset_id or "") or None,
            "folder_id": str(job.folder_id or "") or None,
        }


def _assert_candidate(
    *,
    evidence: dict[str, Any],
    comparison: dict[str, Any],
    parser_job: dict[str, Any],
    model: str,
) -> None:
    if evidence["status"] != "pending_approval":
        raise AssertionError(f"candidate bypassed human approval: {evidence['status']}")
    if evidence["source_job_id"] != parser_job["id"]:
        raise AssertionError("candidate is not bound to the dispatched Parser Job")
    if evidence["profile"].casefold() != model.casefold():
        raise AssertionError("candidate escaped its isolated E2E compatibility scope")
    ntc_attempts = [
        item
        for item in evidence["attempts"]
        if item["candidate_provenance"].get("provider") == "ntc_templates"
    ]
    if not ntc_attempts:
        raise AssertionError("Parser generation did not evaluate an NTC candidate")
    source_templates = {
        str(item["candidate_provenance"].get("source_template") or "")
        for item in ntc_attempts
    }
    if EXPECTED_NTC_TEMPLATE not in source_templates:
        raise AssertionError(f"unexpected NTC provenance: {source_templates}")
    rows = comparison.get("rows") or []
    if len(rows) != 1:
        raise AssertionError(f"approval comparison has {len(rows)} samples")
    proposed = dict(rows[0].get("proposed") or {})
    if proposed.get("status") != "success":
        raise AssertionError(f"candidate did not execute in approval view: {proposed}")
    records = proposed.get("records") or []
    if len(records) != 8:
        raise AssertionError(
            f"real sample should expose 8 inventory components, got {len(records)}"
        )
    if comparison.get("passed_samples") != comparison.get("sample_count"):
        raise AssertionError("candidate did not pass every frozen source sample")
    preview = dict(comparison.get("candidate_template") or {})
    if not preview.get("hash_valid") or not preview.get("content"):
        raise AssertionError("approval view lacks immutable TextFSM content")
    approval_readiness = dict(comparison.get("approval_readiness") or {})
    approval_blockers = [
        str(item) for item in approval_readiness.get("blockers") or []
    ]
    if approval_readiness.get("can_approve") is True:
        raise AssertionError(
            "Parser candidate bypassed the required human semantic review"
        )
    if not any("human semantic review" in item for item in approval_blockers):
        raise AssertionError(
            "Parser candidate did not expose its human-review blocker: "
            f"{approval_readiness}"
        )
    review = dict(evidence["validation"].get("ai_review") or {})
    if review.get("passed") is not True:
        direct_review = (
            evidence["validation"].get("human_direct_review_required") is True
        )
        if not direct_review:
            raise AssertionError(
                "candidate has neither passed LLM review nor direct-review evidence"
            )


def run(
    *,
    base_url: str,
    username: str,
    password: str,
    source_file: Path,
    output_path: Path,
    timeout_seconds: int,
    keep: bool,
    exercise_revision: bool = False,
    temporary_user: bool = False,
) -> dict[str, Any]:
    source_bytes, provenance = _load_lixian_command(
        source_file,
        canonical_command=CANONICAL_COMMAND,
    )
    suffix = uuid.uuid4().hex[:10]
    model = f"S6850-56HF-E2E-{suffix}"
    upload_name = f"ntc-parser-{suffix}.txt"
    source_job_id: str | None = None
    parser_job_id: str | None = None
    file_id: str | None = None
    object_key: str | None = None
    source_graph: dict[str, str | None] = {}
    summary: dict[str, Any] = {
        "schema_version": "parser-ntc-candidate-full-stack-e2e/v1",
        "success": False,
        "base_url": base_url,
        "source_provenance": provenance,
        "isolated_model": model,
    }
    flow_success = False
    run_error: str | None = None
    cleanup_error: str | None = None
    temporary_username: str | None = None
    client = httpx.Client(base_url=base_url.rstrip("/"), timeout=45)
    try:
        if temporary_user:
            username, password = _create_temporary_operator(suffix)
            temporary_username = username
            summary["temporary_auth_fixture"] = True
        token = _login(client, username=username, password=password)
        client.headers["Authorization"] = f"Bearer {token}"
        created = _request_json(
            client,
            "POST",
            "/api/parser/asset-source-jobs/",
            json_body={
                "job_type": "parser_validation",
                "name": f"E2E NTC Parser {suffix}",
                "periods": [
                    {
                        "role": "sample",
                        "label": "Q2 real H3C component inventory",
                        "observed_at": "2025-12-24T15:24:35+08:00",
                    }
                ],
                "vendor": EXPECTED_VENDOR,
                "model": model,
                "publish_events": False,
                "idempotency_key": f"ntc-parser-e2e-create-{suffix}",
            },
        )
        source_job_id = str(created["id"])
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
        put = httpx.put(
            str(upload["upload_url"]),
            content=source_bytes,
            headers={"Content-Type": "text/plain"},
            timeout=45,
        )
        if put.status_code >= 400:
            raise AssertionError(f"MinIO upload failed: {put.status_code} {put.text}")
        _request_json(
            client,
            "POST",
            "/api/storage/upload/complete/",
            json_body={"file_id": file_id, "size_bytes": len(source_bytes)},
        )
        _request_json(
            client,
            "POST",
            f"/api/parser/asset-source-jobs/{source_job_id}/sources/",
            json_body={
                "period_role": "sample",
                "file_id": file_id,
                "relative_path": source_file.name,
                "sha256": provenance["upload_sha256"],
                "size_bytes": len(source_bytes),
            },
        )
        preview_task = _request_json(
            client,
            "POST",
            f"/api/parser/asset-source-jobs/{source_job_id}/preview/",
            json_body={},
        )
        preview = _wait_job(
            client,
            source_job_id,
            expected={"ready", "blocked"},
            timeout_seconds=timeout_seconds,
        )
        if preview.get("status") != "ready":
            raise AssertionError(f"source preview blocked: {preview.get('blockers')}")
        bindings = preview.get("device_bindings") or []
        if not bindings:
            raise AssertionError("source preview has no device binding")
        _request_json(
            client,
            "POST",
            (
                f"/api/parser/asset-source-jobs/{source_job_id}/"
                "device-bindings/confirm/"
            ),
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
        _request_json(
            client,
            "POST",
            f"/api/parser/asset-source-jobs/{source_job_id}/execute/",
            json_body={"idempotency_key": f"ntc-parser-e2e-execute-{suffix}"},
        )
        _wait_job(
            client,
            source_job_id,
            expected={"completed"},
            timeout_seconds=timeout_seconds,
        )
        source_graph = _source_graph(source_job_id)
        parser_job = _find_parser_job(model=model)
        parser_job_id = parser_job["id"]
        if len(parser_job["sample_ids"]) != 1:
            raise AssertionError(f"unexpected Parser samples: {parser_job}")
        _request_json(
            client,
            "PUT",
            f"/api/parser/jobs/{parser_job_id}/spec/",
            json_body={
                "parser_spec": (
                    _intentionally_unfulfillable_parser_spec()
                    if exercise_revision
                    else _parser_spec()
                ),
                "confirmed": True,
                "reason": (
                    "E2E establishes an intentionally failing R1 ParserSpec"
                    if exercise_revision
                    else "E2E confirms an engineer-reviewable component inventory scope"
                ),
            },
        )
        for sample_id in parser_job["sample_ids"]:
            _request_json(
                client,
                "PUT",
                f"/api/parser/jobs/{parser_job_id}/samples/{sample_id}/role/",
                json_body={
                    "role": "generation",
                    "expectation": None,
                    "reason": "E2E uses the real Q2 command as generation evidence",
                },
            )
        ready = _request_json(client, "GET", f"/api/parser/jobs/{parser_job_id}/")
        readiness = dict(ready.get("generation_readiness") or {})
        if readiness.get("ready") is not True:
            raise AssertionError(f"Parser Job is not dispatchable: {readiness}")
        dispatch = _request_json(
            client,
            "POST",
            f"/api/parser/jobs/{parser_job_id}/dispatch/",
            json_body={},
        )
        revision_evidence: dict[str, Any] = {}
        if exercise_revision:
            failed_r1 = _wait_parser_job(
                client,
                parser_job_id,
                timeout_seconds=timeout_seconds,
                expected_status="failed",
            )
            if failed_r1.get("failure_code") != "contract_conflict":
                raise AssertionError(
                    "R1 failure did not preserve the contract conflict code: "
                    f"{failed_r1.get('failure_code')}"
                )
            r2_response = _request_json(
                client,
                "PUT",
                f"/api/parser/jobs/{parser_job_id}/spec/",
                json_body={
                    "parser_spec": _parser_spec(),
                    "confirmed": True,
                    "reason": (
                        "E2E R2 replaces the intentionally impossible contract "
                        "with the engineer-confirmed component inventory contract"
                    ),
                },
            )
            r2 = dict(r2_response.get("job") or r2_response)
            if int(r2.get("parser_spec_revision") or 0) < 2:
                raise AssertionError(f"R2 revision was not persisted: {r2}")
            redispatch_r2 = _request_json(
                client,
                "POST",
                f"/api/parser/jobs/{parser_job_id}/retry-dispatch/",
                json_body={},
            )
            completed_r2 = _wait_parser_job(
                client,
                parser_job_id,
                timeout_seconds=timeout_seconds,
            )
            candidate_r2 = _candidate_evidence(parser_job_id)
            old_candidate_id = str(candidate_r2["candidate_id"])

            r3_response = _request_json(
                client,
                "PUT",
                f"/api/parser/jobs/{parser_job_id}/spec/",
                json_body={
                    "parser_spec": _parser_spec_with_optional_revision_field(),
                    "confirmed": True,
                    "reason": (
                        "E2E R3 proves that a later semantic revision invalidates "
                        "the old pending candidate before regeneration"
                    ),
                },
            )
            r3 = dict(r3_response.get("job") or r3_response)
            if int(r3.get("parser_spec_revision") or 0) < 3:
                raise AssertionError(f"R3 revision was not persisted: {r3}")
            old_candidate = next(
                (
                    item
                    for item in _candidate_rows(parser_job_id)
                    if item["id"] == old_candidate_id
                ),
                None,
            )
            if not old_candidate or old_candidate["status"] != "rejected":
                raise AssertionError(
                    f"old Parser candidate survived semantic revision: {old_candidate}"
                )
            old_activation = client.post(
                f"/api/parser/templates/{old_candidate_id}/activate/",
                json={"reason": "E2E must reject an invalidated Parser candidate"},
            )
            if old_activation.status_code != 409:
                raise AssertionError(
                    "invalidated Parser candidate activation did not return conflict: "
                    f"{old_activation.status_code} {old_activation.text}"
                )
            redispatch_r3 = _request_json(
                client,
                "POST",
                f"/api/parser/jobs/{parser_job_id}/retry-dispatch/",
                json_body={},
            )
            completed = _wait_parser_job(
                client,
                parser_job_id,
                timeout_seconds=timeout_seconds,
            )
            revision_evidence = {
                "r1_failure": failed_r1,
                "r2": r2,
                "r2_redispatch": redispatch_r2,
                "r2_completion": completed_r2,
                "r2_candidate_id": old_candidate_id,
                "r3": r3,
                "old_candidate_after_r3": old_candidate,
                "old_candidate_activation_status": old_activation.status_code,
                "r3_redispatch": redispatch_r3,
            }
        else:
            completed = _wait_parser_job(
                client,
                parser_job_id,
                timeout_seconds=timeout_seconds,
            )
        task_id = str(completed.get("celery_task_id") or "")
        task_state = str(celery.AsyncResult(task_id).state) if task_id else "MISSING"
        if task_state != "SUCCESS":
            raise AssertionError(f"Parser Celery task is not successful: {task_state}")
        evidence = _candidate_evidence(parser_job_id)
        candidate_id = str(evidence["candidate_id"])
        comparison = _request_json(
            client,
            "GET",
            f"/api/parser/templates/{candidate_id}/comparison/",
        )
        preview_task_id = str(preview_task.get("celery_task_id") or "")
        summary.update(
            {
                "source_job_id": source_job_id,
                "parser_job_id": parser_job_id,
                "candidate_id": candidate_id,
                "file_id": file_id,
                "folder_id": source_graph.get("folder_id"),
                "object_key": object_key,
                "preview_task_id": preview_task_id,
                "preview_task_state": (
                    str(celery.AsyncResult(preview_task_id).state)
                    if preview_task_id
                    else "MISSING"
                ),
                "parser_task_id": task_id,
                "parser_task_state": task_state,
                "dispatch": dispatch,
                "parser_job": completed,
                "candidate_evidence": evidence,
                "comparison": comparison,
                "revision_evidence": revision_evidence,
                "source_graph": source_graph,
                "kept": keep,
            }
        )
        _assert_candidate(
            evidence=evidence,
            comparison=comparison,
            parser_job=parser_job,
            model=parser_job["model"],
        )
        active_page = _request_json(
            client,
            "GET",
            (
                "/api/parser/templates/?status=active&search="
                f"{quote(model)}&limit=20&offset=0"
            ),
        )
        if active_page.get("total") != 0:
            raise AssertionError("E2E candidate was automatically activated")
        flow_success = True
    except Exception as exc:
        run_error = f"{type(exc).__name__}: {exc}"
        summary["run_error"] = run_error
    finally:
        if not keep:
            try:
                parser_job_ids = list(
                    dict.fromkeys(
                        [
                            *([parser_job_id] if parser_job_id else []),
                            *_isolated_parser_job_ids(model=model),
                        ]
                    )
                )
                for cleanup_job_id in parser_job_ids:
                    detail_response = client.get(
                        f"/api/parser/jobs/{cleanup_job_id}/"
                    )
                    if detail_response.status_code == 200:
                        detail_payload = detail_response.json()
                        detail = (
                            detail_payload.get("data")
                            if detail_payload.get("success") is True
                            else detail_payload
                        )
                        if str((detail or {}).get("status") or "") in {
                            "collecting",
                            "pending",
                            "running",
                            "validating",
                        }:
                            _request_json(
                                client,
                                "POST",
                                f"/api/parser/jobs/{cleanup_job_id}/cancel/",
                                json_body={},
                            )
                    elif detail_response.status_code != 404:
                        raise AssertionError(
                            "Parser Job lookup during cleanup failed: "
                            f"{detail_response.status_code} {detail_response.text}"
                        )
                    response = client.request(
                        "DELETE",
                        f"/api/parser/jobs/{cleanup_job_id}/",
                        json={
                            "reason": (
                                "Remove isolated NTC Parser full-stack E2E data"
                            )
                        },
                    )
                    if response.status_code not in {200, 404}:
                        raise AssertionError(
                            "Parser Job cleanup failed: "
                            f"{response.status_code} {response.text}"
                        )
                if source_job_id:
                    _cleanup_database(
                        job_id=source_job_id,
                        workflow_run_id=source_graph.get("workflow_run_id"),
                        patrol_run_id=source_graph.get("patrol_run_id"),
                        dataset_id=source_graph.get("dataset_id"),
                        file_id=None,
                        folder_id=None,
                    )
                    if file_id:
                        _delete_json(client, f"/api/storage/files/{file_id}/")
                    folder_id = source_graph.get("folder_id")
                    if folder_id:
                        _delete_json(client, f"/api/storage/folders/{folder_id}/")
                    _cleanup_database(
                        job_id=source_job_id,
                        workflow_run_id=None,
                        patrol_run_id=None,
                        dataset_id=None,
                        file_id=file_id,
                        folder_id=folder_id,
                    )
                    summary["cleanup"] = _assert_cleanup(
                        job_id=source_job_id,
                        workflow_run_id=source_graph.get("workflow_run_id"),
                        patrol_run_id=source_graph.get("patrol_run_id"),
                        file_id=file_id,
                        folder_id=folder_id,
                        object_key=object_key,
                    )
                if parser_job_ids:
                    with get_db_session() as db:
                        surviving_jobs = (
                            db.query(ParserTemplateJob)
                            .filter(ParserTemplateJob.id.in_(parser_job_ids))
                            .count()
                        )
                        if surviving_jobs:
                            raise AssertionError(
                                "Parser Job survived scoped cleanup"
                            )
                        if (
                            db.query(ParserTemplate)
                            .filter(
                                ParserTemplate.source_job_id.in_(parser_job_ids)
                            )
                            .count()
                            != 0
                        ):
                            raise AssertionError("Parser candidate survived scoped cleanup")
                    summary["parser_cleanup"] = {
                        "job_ids": parser_job_ids,
                        "surviving_jobs": 0,
                        "surviving_candidates": 0,
                    }
            except Exception as exc:
                cleanup_error = f"{type(exc).__name__}: {exc}"
                summary["cleanup_error"] = cleanup_error
        try:
            _delete_temporary_operator(temporary_username)
        except Exception as exc:
            cleanup_error = cleanup_error or (
                "temporary user cleanup failed: "
                f"{type(exc).__name__}: {exc}"
            )
            summary["cleanup_error"] = cleanup_error
        client.close()
        summary["flow_success"] = flow_success
        summary["cleanup_success"] = cleanup_error is None
        summary["success"] = (
            flow_success and run_error is None and cleanup_error is None
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    if run_error:
        if cleanup_error:
            raise AssertionError(f"{run_error}; cleanup_error={cleanup_error}")
        raise AssertionError(run_error)
    if cleanup_error:
        raise AssertionError(cleanup_error)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify a real NTC-first Parser candidate full-stack flow"
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
            / "SW1-198.51.2.11_2025-12-24_15-24-35.txt"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            ROOT
            / ".tmp"
            / "parser-ntc-candidate-full-stack-e2e"
            / "summary.json"
        ),
    )
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument(
        "--temporary-user",
        action="store_true",
        help="Create and delete an isolated operator instead of using shared credentials",
    )
    parser.add_argument(
        "--exercise-revision",
        action="store_true",
        help=(
            "Run an intentionally failing R1, governed R2 redispatch, and "
            "old-candidate invalidation before the final candidate"
        ),
    )
    args = parser.parse_args()
    if not args.password and not args.temporary_user:
        raise SystemExit("NETOPS_E2E_PASSWORD or --password is required")
    result = run(
        base_url=args.base_url,
        username=args.username,
        password=args.password,
        source_file=args.source_file,
        output_path=args.output,
        timeout_seconds=max(30, args.timeout_seconds),
        keep=bool(args.keep),
        exercise_revision=bool(args.exercise_revision),
        temporary_user=bool(args.temporary_user),
    )
    comparison = dict(result.get("comparison") or {})
    rows = comparison.get("rows") or []
    proposed = dict((rows[0] if rows else {}).get("proposed") or {})
    evidence = dict(result.get("candidate_evidence") or {})
    validation = dict(evidence.get("validation") or {})
    print(
        json.dumps(
            {
                "success": result.get("success"),
                "source_job_id": result.get("source_job_id"),
                "parser_job_id": result.get("parser_job_id"),
                "candidate_id": result.get("candidate_id"),
                "candidate_status": evidence.get("status"),
                "candidate_provider": dict(
                    validation.get("candidate_provenance") or {}
                ).get("provider"),
                "ai_review_passed": dict(
                    validation.get("ai_review") or {}
                ).get("passed"),
                "sample_count": comparison.get("sample_count"),
                "passed_samples": comparison.get("passed_samples"),
                "parsed_record_count": len(proposed.get("records") or []),
                "approval_ready": dict(
                    comparison.get("approval_readiness") or {}
                ).get("can_approve"),
                "parser_task_state": result.get("parser_task_state"),
                "preview_task_state": result.get("preview_task_state"),
                "cleanup_success": result.get("cleanup_success"),
                "revision_exercised": bool(args.exercise_revision),
                "old_candidate_activation_status": dict(
                    result.get("revision_evidence") or {}
                ).get("old_candidate_activation_status"),
                "evidence_path": str(args.output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())

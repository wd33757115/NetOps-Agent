#!/usr/bin/env python
"""Verify one check-item report manifest through the real browser download path."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.celery_tasks.celery_app import celery
from src.core.parser_generation.validation import TextFSMValidationBackend
from src.core.patrol.command_splitter import (
    infer_device_from_filename,
    split_cli_capture,
)
from src.infrastructure.db.models import (
    AuditLogRecord,
    CheckAssessmentRecord,
    CheckExecutionRecord,
    CheckItemImplementationLinkRecord,
    CheckItemRecord,
    CheckItemRuntimeManifestRecord,
    CheckItemVersionRecord,
    CollectionImplementationRecord,
    CollectionImplementationVersionRecord,
    CommandSnapshot,
    ParserTemplate,
    PatrolBatchRecord,
    PatrolBatchSummaryProjection,
    PatrolRun,
    ReportManifestRecord,
    StateObservationRecord,
    WorkflowRun,
)
from src.infrastructure.db.postgres import get_db_session
from src.infrastructure.storage.minio_client import get_minio_storage

CHECK_ITEM_ID = "device.version"
IMPLEMENTATION_ID = "h3c-device-version"
CANONICAL_COMMAND = "display version"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _load_source(source_file: Path) -> dict[str, Any]:
    source_bytes = source_file.read_bytes()
    raw_capture = source_bytes.decode("utf-8-sig", errors="strict")
    device_name, management_ip = infer_device_from_filename(source_file)
    matches = [
        block
        for block in split_cli_capture(
            raw_capture,
            device_name=device_name,
        )
        if block.command_canonical == CANONICAL_COMMAND
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one {CANONICAL_COMMAND!r} block, got {len(matches)}")
    return {
        "block": matches[0],
        "device_name": str(device_name or matches[0].device_name),
        "management_ip": management_ip,
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
    }


def _build_raw_output_archive(source_file: Path) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(source_file.name, source_file.read_bytes())
        archive.writestr(
            f"{source_file.name}.collection.json",
            json.dumps(
                {
                    "internal": True,
                    "source_file": source_file.name,
                    "purpose": "prove internal sidecars are excluded",
                },
                ensure_ascii=False,
            ),
        )
    return output.getvalue()


def _upload_raw_output_archive(
    *,
    source_file: Path,
    object_key: str,
) -> str:
    storage = get_minio_storage()
    if not storage.is_ready():
        raise AssertionError("MinIO is unavailable")
    if not storage.upload_file(
        object_key,
        _build_raw_output_archive(source_file),
        content_type="application/zip",
    ):
        raise AssertionError("failed to upload the collector raw-output archive")
    download_url = storage.get_presigned_url(
        object_key,
        filename=f"{source_file.stem}-raw-output.zip",
    )
    if not download_url:
        raise AssertionError("failed to build raw-output artifact URL")
    return download_url


def _create_test_user(username: str, password: str, role: str) -> None:
    django_root = ROOT / "web" / "django_backend"
    if str(django_root) not in sys.path:
        sys.path.insert(0, str(django_root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_backend.settings")

    import django

    django.setup()

    from bff.roles import assign_user_role
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.invalid",
        password=password,
    )
    assign_user_role(user, role)
    user.save()


def _delete_test_user(username: str) -> int:
    from django.contrib.auth import get_user_model

    deleted, _details = get_user_model().objects.filter(username=username).delete()
    return int(deleted)


def _runtime_assets(db: Any) -> dict[str, Any]:
    check_item_version = (
        db.query(CheckItemVersionRecord)
        .join(
            CheckItemRecord,
            CheckItemRecord.id == CheckItemVersionRecord.check_item_id,
        )
        .filter(
            CheckItemRecord.id == CHECK_ITEM_ID,
            CheckItemVersionRecord.status == "active",
        )
        .one()
    )
    implementation_version = (
        db.query(CollectionImplementationVersionRecord)
        .join(
            CollectionImplementationRecord,
            CollectionImplementationRecord.id
            == CollectionImplementationVersionRecord.implementation_id,
        )
        .join(
            CheckItemImplementationLinkRecord,
            CheckItemImplementationLinkRecord.implementation_version_id
            == CollectionImplementationVersionRecord.id,
        )
        .filter(
            CollectionImplementationRecord.id == IMPLEMENTATION_ID,
            CollectionImplementationVersionRecord.status == "active",
            CheckItemImplementationLinkRecord.check_item_version_id == check_item_version.id,
        )
        .one()
    )
    parser = (
        db.query(ParserTemplate)
        .filter(
            ParserTemplate.status == "active",
            ParserTemplate.vendor == "H3C",
            ParserTemplate.model == "MSR5620",
            ParserTemplate.canonical_command == CANONICAL_COMMAND,
        )
        .one()
    )
    return {
        "check_item_version": check_item_version,
        "implementation_version": implementation_version,
        "parser": parser,
    }


def _seed(
    *,
    source_file: Path,
    batch_id: str,
    run_id: str,
    workflow_run_id: str,
    device_id: str,
    snapshot_id: str,
    raw_outputs_archive_url: str | None = None,
) -> dict[str, Any]:
    source = _load_source(source_file)
    block = source["block"]
    now = _utcnow()
    action_id = f"action-{batch_id[-12:]}"
    execution_id = f"execution-{batch_id[-12:]}"
    assessment_id = f"assessment-{batch_id[-12:]}"
    runtime_manifest_id = f"runtime-{batch_id[-12:]}"
    target_digest = _digest(
        {
            "device_id": device_id,
            "device_name": source["device_name"],
            "management_ip": source["management_ip"],
        }
    )
    inventory_digest = _digest(
        {
            "device_id": device_id,
            "vendor": "H3C",
            "model": "MSR5620",
        }
    )

    with get_db_session() as db:
        assets = _runtime_assets(db)
        check_item_version = assets["check_item_version"]
        implementation_version = assets["implementation_version"]
        parser = assets["parser"]
        parser_template_id = str(parser.id)
        records = TextFSMValidationBackend().parse(
            str(parser.template_text),
            str(block.raw_output),
        )
        if not records:
            raise AssertionError("active Parser produced no display version records")
        identities = {
            (
                str(item.get("software_version") or ""),
                str(item.get("release") or ""),
                str(item.get("model") or ""),
            )
            for item in records
        }
        if len(identities) != 1:
            raise AssertionError("display version identity fields are inconsistent")
        software_version, release, model = next(iter(identities))
        full_version = f"{software_version}, Release {release}"

        runtime_payload = {
            "schema_version": "check-item-runtime-manifest/v2",
            "patrol_batch_id": batch_id,
            "target_manifest_digest": target_digest,
            "inventory_snapshot_digest": inventory_digest,
            "check_item_versions": [str(check_item_version.id)],
            "device_check_item_versions": {device_id: [str(check_item_version.id)]},
            "collection_actions": [{"id": action_id}],
            "runtime_bundle_refs": [],
            "comparison_contexts": [],
            "entity_identity_policy_versions": [],
            "assessment_schema_version": "check-assessment/v1",
            "report_schema_version": "patrol-report/v2",
        }
        runtime_digest = _digest(runtime_payload)
        runtime_payload["digest"] = runtime_digest

        batch = PatrolBatchRecord(
            patrol_batch_id=batch_id,
            workflow_name="check-item-report-download-e2e",
            launch_mode="workflow",
            patrol_mode="offline_history",
            report_purpose="offline_historical",
            target_type="offline_import",
            target_label="lixian report download full-stack E2E",
            selector_json={
                "source": "lixian",
                "file": source_file.name,
            },
            target_count=1,
            launched_count=1,
            skipped_count=0,
            target_manifest_digest=target_digest,
            inventory_snapshot_digest=inventory_digest,
            runtime_manifest_id=runtime_manifest_id,
            runtime_manifest_digest=runtime_digest,
            save_baseline=False,
            dispatch_status="completed",
            dispatch_requested_at=now,
            dispatch_started_at=now,
            dispatch_completed_at=now,
            report_status="not_requested",
            archived=False,
            created_at=now,
            updated_at=now,
        )
        workflow_run = WorkflowRun(
            id=workflow_run_id,
            template_name="check-item-report-download-e2e",
            source="full-stack-e2e",
            status="completed",
            context={
                "patrol_batch_id": batch_id,
                "device_id": device_id,
                "device_name": source["device_name"],
                "management_ip": source["management_ip"],
            },
            current_step_index=1,
            created_at=now,
            updated_at=now,
            completed_at=now,
        )
        patrol_run = PatrolRun(
            id=run_id,
            mode="parser_validation",
            source="offline_file",
            status="completed",
            collection_status="completed",
            parser_readiness_status="ready",
            fact_readiness_status="ready",
            analysis_status="completed",
            baseline_eligible=False,
            run_purpose="parser_validation",
            logical_run_id=run_id,
            attempt_number=0,
            owner_generation=1,
            device_count=1,
            command_count=1,
            started_at=now,
            completed_at=now,
            metadata_json={
                "source_file": str(source_file),
                "source_sha256": source["source_sha256"],
                "raw_outputs_archive": raw_outputs_archive_url,
                "full_stack_e2e": True,
            },
            created_at=now,
            updated_at=now,
        )
        runtime_manifest = CheckItemRuntimeManifestRecord(
            id=runtime_manifest_id,
            patrol_batch_id=batch_id,
            schema_version=2,
            target_manifest_digest=target_digest,
            inventory_snapshot_digest=inventory_digest,
            check_item_versions_json=[str(check_item_version.id)],
            collection_actions_json=[action_id],
            runtime_bundle_refs_json=[],
            comparison_context_ids_json=[],
            entity_identity_policy_versions_json=[],
            assessment_schema_version="check-assessment/v1",
            report_schema_version="patrol-report/v2",
            manifest_json=runtime_payload,
            digest=runtime_digest,
            status="frozen",
            created_by="check-item-report-download-e2e",
            created_at=now,
        )
        db.add_all([batch, workflow_run, patrol_run, runtime_manifest])
        db.flush()

        from src.infrastructure.db.models import PatrolBatchDeviceRecord

        db.add(
            PatrolBatchDeviceRecord(
                id=f"target-{batch_id[-12:]}",
                patrol_batch_id=batch_id,
                device_id=device_id,
                device_name=source["device_name"],
                management_ip=source["management_ip"],
                selection_status="launched",
                preflight_status="ready",
                workflow_run_id=workflow_run_id,
                dispatch_status="dispatched",
                dispatch_attempts=1,
                dispatched_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        snapshot = CommandSnapshot(
            id=snapshot_id,
            run_id=run_id,
            device_id=device_id,
            execution_item_id=action_id,
            collection_action_id=action_id,
            implementation_version_id=str(implementation_version.id),
            check_item_ids_json=[CHECK_ITEM_ID],
            device_name=source["device_name"],
            management_ip=source["management_ip"],
            vendor="H3C",
            model="MSR5620",
            command=str(block.command),
            canonical_command=CANONICAL_COMMAND,
            parser_template_id=str(parser.id),
            parser_version=str(parser.version),
            contract_version=1,
            observed_at=now,
            structured_json=records,
            raw_text=str(block.raw_output),
            raw_text_hash=hashlib.sha256(str(block.raw_output).encode("utf-8")).hexdigest(),
            parse_status="parsed",
            output_status="success",
            snapshot_kind="full",
            source_file_hash=source["source_sha256"],
            start_line=int(block.start_line),
            end_line=int(block.end_line),
            metadata_json={"full_stack_e2e": True},
            created_at=now,
        )
        execution = CheckExecutionRecord(
            id=execution_id,
            idempotency_key=execution_id,
            patrol_batch_id=batch_id,
            device_id=device_id,
            check_item_version_id=str(check_item_version.id),
            implementation_version_id=str(implementation_version.id),
            collection_action_ids_json=[action_id],
            execution_status="completed",
            reason_codes_json=[],
            observation_count=2,
            observed_entity_count=1,
            change_count=0,
            event_count=0,
            current_finding_count=0,
            assessment_id=assessment_id,
            source_identity_status="confirmed",
            output_quality_status="complete",
            freshness_status="fresh",
            empty_result_classification="not_empty",
            coverage_status="complete",
            coverage_basis="fixed",
            risk_verification_status="not_applicable",
            expected_entity_count=1,
            observation_min_at=now,
            observation_max_at=now,
            target_manifest_digest=target_digest,
            asset_manifest_digest=runtime_digest,
            evidence_refs_json=[snapshot_id],
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add_all([snapshot, execution])
        db.flush()
        observations = (
            (
                "software",
                "software_version",
                "device.software.version",
                full_version,
            ),
            (
                "model",
                "model",
                "device.hardware.model",
                model,
            ),
        )
        for suffix, field_name, observation_type, value in observations:
            observation_id = f"observation-{suffix}-{batch_id[-12:]}"
            db.add(
                StateObservationRecord(
                    id=observation_id,
                    idempotency_key=observation_id,
                    run_id=run_id,
                    snapshot_id=snapshot_id,
                    mode="parser_validation",
                    source="cli",
                    source_type="cli",
                    device_id=device_id,
                    vendor="H3C",
                    model="MSR5620",
                    canonical_command=CANONICAL_COMMAND,
                    execution_item_id=action_id,
                    entity_type="device",
                    entity_key=f'["{device_id}"]',
                    field_name=field_name,
                    observation_type=observation_type,
                    semantic_type="state",
                    dimensions_json={},
                    value_json=value,
                    value_text=value,
                    value_type="string",
                    observation_kind="state",
                    observed_at=now,
                    fact_order_key="",
                    parser_template_id=str(parser.id),
                    parser_version=str(parser.version),
                    contract_version=1,
                    confidence=1.0,
                    metadata_json={"full_stack_e2e": True},
                    created_at=now,
                )
            )
        db.add(
            CheckAssessmentRecord(
                id=assessment_id,
                idempotency_key=assessment_id,
                patrol_batch_id=batch_id,
                device_id=device_id,
                check_item_version_id=str(check_item_version.id),
                execution_id=execution_id,
                assessment_status="informational",
                completeness_status="complete",
                comparison_status="first_observation",
                expected_entity_count=1,
                observed_entity_count=1,
                evaluated_entity_count=1,
                threshold_unknown_count=0,
                current_finding_ids_json=[],
                risk_case_ids_json=[],
                source_identity_status="confirmed",
                output_quality_status="complete",
                freshness_status="fresh",
                empty_result_classification="not_empty",
                coverage_status="complete",
                coverage_basis="fixed",
                risk_verification_status="not_applicable",
                blocking_reason_codes_json=[],
                assessment_rule_bundle_digest=runtime_digest,
                evidence_digest=_digest(
                    {
                        "execution_id": execution_id,
                        "snapshot_id": snapshot_id,
                    }
                ),
                assessed_at=now,
                created_at=now,
            )
        )
    return {
        "device_name": source["device_name"],
        "management_ip": source["management_ip"],
        "source_sha256": source["source_sha256"],
        "snapshot_id": snapshot_id,
        "parser_template_id": parser_template_id,
        "parsed_record_count": len(records),
        "canonical_values": {
            "software_version": full_version,
            "model": model,
        },
    }


def _login(
    client: httpx.Client,
    *,
    username: str,
    password: str,
) -> str:
    response = client.post(
        "/api/auth/login/",
        json={"username": username, "password": password},
    )
    if response.status_code >= 400:
        raise AssertionError(f"BFF login returned {response.status_code}: {response.text}")
    token = str((response.json().get("data") or {}).get("access") or "")
    if not token:
        raise AssertionError("BFF login did not return an access token")
    return token


def _json_response(
    response: httpx.Response,
    *,
    expected_status: int = 200,
) -> dict[str, Any]:
    if response.status_code != expected_status:
        raise AssertionError(
            f"{response.request.method} {response.request.url.path} returned "
            f"{response.status_code}: {response.text}"
        )
    payload = response.json()
    if payload.get("success") is True and isinstance(payload.get("data"), dict):
        return dict(payload["data"])
    return dict(payload)


def _wait_for_report(
    client: httpx.Client,
    *,
    batch_id: str,
    task_id: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status: dict[str, Any] = {}
    last_task_state = "PENDING"
    while time.monotonic() < deadline:
        last_task_state = str(celery.AsyncResult(task_id).state)
        last_status = _json_response(
            client.get(f"/api/patrol/batches/{batch_id}/reports/materialization-status")
        )
        if last_task_state == "SUCCESS" and last_status.get("status") == "completed":
            return last_status
        time.sleep(0.5)
    raise TimeoutError(f"report task did not complete: celery={last_task_state}, api={last_status}")


def _download_and_verify(
    client: httpx.Client,
    *,
    batch_id: str,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    artifact_paths = {
        "markdown": "report.md",
        "docx": "report.docx",
        "excel": "report.xlsx",
        "json": "report.json",
        "evidence": "evidence.zip",
    }
    expected_digests = dict(manifest.get("artifact_sha256") or {})
    object_keys = dict(manifest.get("artifact_object_keys") or {})
    source_digest = str(manifest.get("source_facts_digest") or "")
    if set(expected_digests) != set(artifact_paths):
        raise AssertionError(f"manifest artifact set is incomplete: {sorted(expected_digests)}")
    storage = get_minio_storage()
    result: dict[str, Any] = {}
    for name, suffix in artifact_paths.items():
        response = client.get(f"/api/workflows/runs/patrol-batches/{batch_id}/{suffix}")
        if response.status_code != 200:
            raise AssertionError(
                f"download {name} returned {response.status_code}: {response.text}"
            )
        actual_digest = hashlib.sha256(response.content).hexdigest()
        if actual_digest != expected_digests[name]:
            raise AssertionError(f"{name} digest does not match the frozen manifest")
        if response.headers.get("x-netops-report-status") != "pending_signoff":
            raise AssertionError(f"{name} lost its pending-signoff status")
        if response.headers.get("x-netops-report-facts-status") != "final":
            raise AssertionError(f"{name} does not expose final fact status")
        if response.headers.get("x-netops-report-source-digest") != source_digest:
            raise AssertionError(f"{name} source digest header mismatch")
        object_content = storage.download_file(str(object_keys[name]))
        if object_content is None:
            raise AssertionError(f"{name} object is missing from MinIO")
        if hashlib.sha256(object_content).hexdigest() != actual_digest:
            raise AssertionError(f"{name} BFF bytes differ from MinIO bytes")
        result[name] = {
            "bytes": len(response.content),
            "sha256": actual_digest,
            "content_type": response.headers.get("content-type"),
            "content_disposition": response.headers.get("content-disposition"),
            "report_status": response.headers.get("x-netops-report-status"),
            "facts_status": response.headers.get("x-netops-report-facts-status"),
            "source_digest": response.headers.get("x-netops-report-source-digest"),
            "object_key": object_keys[name],
        }

    report_json = json.loads(
        client.get(f"/api/workflows/runs/patrol-batches/{batch_id}/report.json").content
    )
    if str(report_json.get("patrol_batch_id") or "") != batch_id:
        raise AssertionError("JSON report batch identity mismatch")
    if str(report_json.get("source_digest") or "") != source_digest:
        raise AssertionError("JSON report source digest mismatch")

    evidence_content = client.get(
        f"/api/workflows/runs/patrol-batches/{batch_id}/evidence.zip"
    ).content
    with zipfile.ZipFile(io.BytesIO(evidence_content)) as archive:
        entries = sorted(archive.namelist())
        combined = b"\n".join(archive.read(name) for name in entries)
        if b"H3C Comware Software" in combined:
            raise AssertionError("evidence index package leaked raw CLI text")
        if not entries:
            raise AssertionError("evidence package is empty")
    result["evidence"]["entries"] = entries
    return result


def _download_raw_output_and_verify(
    client: httpx.Client,
    *,
    batch_id: str,
    manifest: dict[str, Any],
    source_file: Path,
    seed: dict[str, Any],
    operator_token: str,
) -> dict[str, Any]:
    manifest_id = str(manifest.get("report_manifest_id") or "")
    report_version = str(manifest.get("version") or "")
    raw_evidence_digest = str(manifest.get("raw_evidence_set_digest") or "")
    params = {"report_manifest_id": manifest_id}
    path = f"/api/workflows/runs/patrol-batches/{batch_id}/raw-cli.zip"

    denied = client.get(
        path,
        params=params,
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    if denied.status_code != 403:
        raise AssertionError(
            "operator unexpectedly downloaded protected raw CLI: "
            f"{denied.status_code} {denied.text}"
        )

    response = client.get(path, params=params)
    if response.status_code != 200:
        raise AssertionError(
            f"admin raw CLI download returned {response.status_code}: {response.text}"
        )
    expected_headers = {
        "x-netops-report-manifest-id": manifest_id,
        "x-netops-report-version": report_version,
        "x-netops-raw-evidence-set-digest": raw_evidence_digest,
        "x-netops-raw-file-count": "1",
    }
    for name, expected in expected_headers.items():
        if response.headers.get(name) != expected:
            raise AssertionError(
                f"raw CLI header {name} mismatch: {response.headers.get(name)!r} != {expected!r}"
            )

    source_bytes = source_file.read_bytes()
    expected_lists = {
        "设备列表.txt",
        "登录失败设备列表.txt",
        "原始输出缺失设备列表.txt",
        "原始输出校验清单.txt",
    }
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        entries = sorted(archive.namelist())
        source_entries = [
            name for name in entries if name.endswith(f"/{source_file.with_suffix('.txt').name}")
        ]
        if len(source_entries) != 1:
            raise AssertionError(f"raw output package has unexpected source entries: {entries}")
        if archive.read(source_entries[0]) != source_bytes:
            raise AssertionError("downloaded raw CLI bytes differ from lixian source")
        if set(entries) != expected_lists | set(source_entries):
            raise AssertionError(f"raw output package contains unexpected entries: {entries}")
        if any(name.endswith((".collection.json", ".json", ".jsonl", ".csv")) for name in entries):
            raise AssertionError("raw output package leaked an internal data file")

        device_list = archive.read("设备列表.txt").decode("utf-8-sig")
        login_failures = archive.read("登录失败设备列表.txt").decode("utf-8-sig")
        missing_outputs = archive.read("原始输出缺失设备列表.txt").decode("utf-8-sig")
        verification = archive.read("原始输出校验清单.txt").decode("utf-8-sig")

    device_lines = [line for line in device_list.splitlines() if line.strip()]
    if len(device_lines) != 2:
        raise AssertionError(f"device list is not complete: {device_lines}")
    if (
        str(seed["device_name"]) not in device_lines[1]
        or str(seed["management_ip"]) not in device_lines[1]
        or not device_lines[1].endswith("\t1")
    ):
        raise AssertionError("device list lost identity or raw-file count")
    for label, content in (
        ("login failure", login_failures),
        ("missing output", missing_outputs),
    ):
        lines = [line for line in content.splitlines() if line.strip()]
        if len(lines) != 1:
            raise AssertionError(f"{label} list contains unexpected devices: {lines}")

    verification_values = (
        batch_id,
        manifest_id,
        report_version,
        raw_evidence_digest,
        str(seed["source_sha256"]),
        str(seed["snapshot_id"]),
    )
    if any(value not in verification for value in verification_values):
        raise AssertionError("raw output verification list is incomplete")

    return {
        "operator_status": denied.status_code,
        "admin_status": response.status_code,
        "bytes": len(response.content),
        "sha256": hashlib.sha256(response.content).hexdigest(),
        "entries": entries,
        "source_entry": source_entries[0],
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "manifest_id": response.headers.get("x-netops-report-manifest-id"),
        "report_version": response.headers.get("x-netops-report-version"),
        "raw_evidence_set_digest": response.headers.get("x-netops-raw-evidence-set-digest"),
        "raw_file_count": int(response.headers.get("x-netops-raw-file-count") or 0),
    }


def _cleanup(
    *,
    batch_id: str,
    run_id: str,
    workflow_run_id: str,
    raw_output_object_key: str,
) -> dict[str, int]:
    deleted_objects = get_minio_storage().delete_objects_by_prefix(f"reports/patrol/{batch_id}")
    deleted_raw_output = int(get_minio_storage().delete_object(raw_output_object_key))
    with get_db_session() as db:
        deleted_audits = (
            db.query(AuditLogRecord)
            .filter(AuditLogRecord.resource_id == batch_id)
            .delete(synchronize_session=False)
        )
        deleted_projections = (
            db.query(PatrolBatchSummaryProjection)
            .filter(PatrolBatchSummaryProjection.patrol_batch_id == batch_id)
            .delete(synchronize_session=False)
        )
        deleted_manifests = (
            db.query(ReportManifestRecord)
            .filter(ReportManifestRecord.patrol_batch_id == batch_id)
            .delete(synchronize_session=False)
        )
        deleted_executions = (
            db.query(CheckExecutionRecord)
            .filter(CheckExecutionRecord.patrol_batch_id == batch_id)
            .delete(synchronize_session=False)
        )
        deleted_runtime_manifests = (
            db.query(CheckItemRuntimeManifestRecord)
            .filter(CheckItemRuntimeManifestRecord.patrol_batch_id == batch_id)
            .delete(synchronize_session=False)
        )
        deleted_runs = (
            db.query(PatrolRun).filter(PatrolRun.id == run_id).delete(synchronize_session=False)
        )
        deleted_batches = (
            db.query(PatrolBatchRecord)
            .filter(PatrolBatchRecord.patrol_batch_id == batch_id)
            .delete(synchronize_session=False)
        )
        deleted_workflows = (
            db.query(WorkflowRun)
            .filter(WorkflowRun.id == workflow_run_id)
            .delete(synchronize_session=False)
        )
    return {
        "objects": int(deleted_objects),
        "raw_output_objects": deleted_raw_output,
        "audits": int(deleted_audits),
        "projections": int(deleted_projections),
        "report_manifests": int(deleted_manifests),
        "executions": int(deleted_executions),
        "runtime_manifests": int(deleted_runtime_manifests),
        "patrol_runs": int(deleted_runs),
        "batches": int(deleted_batches),
        "workflow_runs": int(deleted_workflows),
    }


def run(
    *,
    base_url: str,
    username: str,
    password: str,
    source_file: Path,
    timeout_seconds: int,
    output_path: Path,
) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:12]
    batch_id = f"report-download-e2e-{suffix}"
    run_id = f"report-download-run-{suffix}"
    workflow_run_id = f"report-download-workflow-{suffix}"
    device_id = f"report-download-device-{suffix}"
    snapshot_id = f"report-download-snapshot-{suffix}"
    raw_output_object_key = f"raw-output-e2e/{batch_id}/collector-output.zip"
    operator_username = f"raw-output-operator-{suffix}"
    operator_password = f"RawOutput-{uuid.uuid4().hex}"
    admin_username = username
    admin_password = password
    if not admin_password:
        admin_username = f"raw-output-admin-{suffix}"
        admin_password = f"RawOutput-{uuid.uuid4().hex}"
    created_usernames: list[str] = []
    summary: dict[str, Any] = {
        "schema_version": "check-item-report-download-full-stack-e2e/v2",
        "batch_id": batch_id,
        "base_url": base_url,
        "source_file": str(source_file),
        "success": False,
    }
    try:
        raw_outputs_archive_url = _upload_raw_output_archive(
            source_file=source_file,
            object_key=raw_output_object_key,
        )
        _create_test_user(operator_username, operator_password, "operator")
        created_usernames.append(operator_username)
        if admin_username != username:
            _create_test_user(admin_username, admin_password, "admin")
            created_usernames.append(admin_username)
        summary["seed"] = _seed(
            source_file=source_file,
            raw_outputs_archive_url=raw_outputs_archive_url,
            batch_id=batch_id,
            run_id=run_id,
            workflow_run_id=workflow_run_id,
            device_id=device_id,
            snapshot_id=snapshot_id,
        )
        with httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=60,
        ) as client:
            token = _login(
                client,
                username=admin_username,
                password=admin_password,
            )
            operator_token = _login(
                client,
                username=operator_username,
                password=operator_password,
            )
            client.headers["Authorization"] = f"Bearer {token}"
            idempotency_key = f"report-download-{suffix}"
            dispatch = _json_response(
                client.post(
                    f"/api/patrol/batches/{batch_id}/reports/materialize",
                    headers={"Idempotency-Key": idempotency_key},
                    json={},
                ),
                expected_status=202,
            )
            if dispatch.get("accepted") is not True:
                raise AssertionError(f"report dispatch was not accepted: {dispatch}")
            status = _wait_for_report(
                client,
                batch_id=batch_id,
                task_id=str(dispatch["task_id"]),
                timeout_seconds=timeout_seconds,
            )
            manifest = _json_response(client.get(f"/api/patrol/batches/{batch_id}/report-manifest"))
            if str(manifest.get("facts_status") or "") != "final":
                raise AssertionError("report manifest facts are not final")
            if str(manifest.get("release_status") or "") != "pending_signoff":
                raise AssertionError("unsigned report was not marked pending signoff")
            downloads = _download_and_verify(
                client,
                batch_id=batch_id,
                manifest=manifest,
            )
            raw_output = _download_raw_output_and_verify(
                client,
                batch_id=batch_id,
                manifest=manifest,
                source_file=source_file,
                seed=summary["seed"],
                operator_token=operator_token,
            )
        summary.update(
            {
                "success": True,
                "dispatch": dispatch,
                "materialization_status": status,
                "manifest": {
                    "id": manifest.get("report_manifest_id"),
                    "facts_status": manifest.get("facts_status"),
                    "release_status": manifest.get("release_status"),
                    "report_purpose": manifest.get("report_purpose"),
                    "source_digest": manifest.get("source_facts_digest"),
                    "raw_evidence_set_digest": manifest.get("raw_evidence_set_digest"),
                    "artifact_sha256": manifest.get("artifact_sha256"),
                    "artifact_object_keys": manifest.get("artifact_object_keys"),
                },
                "downloads": downloads,
                "raw_output": raw_output,
            }
        )
    except Exception as exc:
        summary["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        raise
    finally:
        summary["cleanup"] = _cleanup(
            batch_id=batch_id,
            run_id=run_id,
            workflow_run_id=workflow_run_id,
            raw_output_object_key=raw_output_object_key,
        )
        summary["cleanup"]["django_users"] = sum(
            _delete_test_user(item) for item in created_usernames
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    if not summary["success"]:
        raise AssertionError(f"full-stack report download E2E failed; see {output_path}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify check-item report downloads through the real BFF"
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
        default=(ROOT / "lixian" / "Q2" / "XA-FOTIC-RT5620-1-10.34.2.219.txt"),
    )
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument(
        "--output-path",
        type=Path,
        default=(ROOT / ".tmp" / "check-item-report-download-full-stack-e2e" / "summary.json"),
    )
    args = parser.parse_args()
    result = run(
        base_url=args.base_url,
        username=args.username,
        password=args.password,
        source_file=args.source_file.resolve(),
        timeout_seconds=args.timeout_seconds,
        output_path=args.output_path.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

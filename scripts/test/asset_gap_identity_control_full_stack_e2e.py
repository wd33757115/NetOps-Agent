#!/usr/bin/env python
"""Verify identity conflicts and production AssetGap routing through the real stack.

The script extracts a real ``display version`` output from ``lixian``, imports it
through the BFF/MinIO/Celery preview path, and creates two isolated Inventory
records that deliberately conflict by name and management IP. It also verifies
that reading a production AssetGap only returns an evidence-import deep link and
does not create Parser jobs, rule-generation jobs, Workflow runs or model calls.
All fixture records and objects are removed unless ``--keep`` is supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

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

from scripts.test.rule_asset_source_import_full_stack_e2e import (  # noqa: E402
    _delete_json,
    _load_lixian_command,
    _login,
    _request_json,
    _wait_job,
)
from src.core.celery_tasks.celery_app import celery  # noqa: E402
from src.core.check_items.gaps import AssetGapRepository  # noqa: E402
from src.infrastructure.db.models import (  # noqa: E402
    AssetGapRecord,
    AuditLogRecord,
    FileMetadata,
    NetworkDevice,
    OfflinePatrolDeviceBindingRecord,
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    OfflinePatrolSourceFileRecord,
    ParserModelCallLedger,
    ParserTemplateJob,
    RuleGenerationJobRecord,
    StorageFolder,
    WorkflowRun,
)
from src.infrastructure.db.postgres import get_db_session  # noqa: E402
from src.infrastructure.storage.minio_client import get_minio_storage  # noqa: E402


def _create_user(username: str, password: str) -> None:
    user = get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.invalid",
        password=password,
    )
    assign_user_role(user, "operator")
    user.save()


def _delete_user(username: str) -> int:
    deleted, _details = get_user_model().objects.filter(username=username).delete()
    return int(deleted)


def _governance_counts() -> dict[str, int]:
    with get_db_session() as db:
        return {
            "parser_jobs": db.query(ParserTemplateJob).count(),
            "rule_generation_jobs": db.query(RuleGenerationJobRecord).count(),
            "model_calls": db.query(ParserModelCallLedger).count(),
            "workflow_runs": db.query(WorkflowRun).count(),
        }


def _prepare_capture(
    source_file: Path,
    *,
    observed_name: str,
) -> tuple[bytes, dict[str, Any]]:
    source_bytes, provenance = _load_lixian_command(source_file)
    original_name = str(provenance["device_name"])
    text = source_bytes.decode("utf-8")
    text = text.replace(f"<{original_name}>", f"<{observed_name}>")
    capture = text.encode("utf-8")
    return capture, {
        **provenance,
        "observed_device_name": observed_name,
        "upload_sha256": hashlib.sha256(capture).hexdigest(),
        "upload_size": len(capture),
    }


def _insert_inventory(
    *,
    same_name_id: str,
    same_ip_id: str,
    observed_name: str,
    observed_ip: str,
) -> None:
    with get_db_session() as db:
        db.add_all(
            [
                NetworkDevice(
                    id=same_name_id,
                    device_name=observed_name,
                    management_ip="198.51.100.241",
                    vendor="H3C",
                    model="MSR5620",
                    family="Comware7",
                    status="active",
                ),
                NetworkDevice(
                    id=same_ip_id,
                    device_name=f"{observed_name}-BY-IP",
                    management_ip=observed_ip,
                    vendor="H3C",
                    model="MSR5620",
                    family="Comware7",
                    status="active",
                ),
            ]
        )


def _cleanup_database(
    *,
    job_id: str | None,
    gap_id: str | None,
    device_ids: list[str],
    file_id: str | None,
    folder_id: str | None,
    username: str,
) -> None:
    with get_db_session() as db:
        if gap_id:
            db.query(AssetGapRecord).filter(AssetGapRecord.id == gap_id).delete(
                synchronize_session=False
            )
        if job_id:
            db.query(OfflinePatrolDeviceBindingRecord).filter(
                OfflinePatrolDeviceBindingRecord.job_id == job_id
            ).delete(synchronize_session=False)
            db.query(OfflinePatrolSourceFileRecord).filter(
                OfflinePatrolSourceFileRecord.job_id == job_id
            ).delete(synchronize_session=False)
            db.query(OfflinePatrolPeriodRecord).filter(
                OfflinePatrolPeriodRecord.job_id == job_id
            ).delete(synchronize_session=False)
            db.query(OfflinePatrolJobRecord).filter(
                OfflinePatrolJobRecord.id == job_id
            ).delete(synchronize_session=False)
        db.query(AuditLogRecord).filter(
            (AuditLogRecord.username == username)
            | (
                AuditLogRecord.resource_id.in_(
                    [
                        value
                        for value in (job_id, gap_id, file_id, folder_id)
                        if value
                    ]
                )
            )
        ).delete(synchronize_session=False)
        db.query(NetworkDevice).filter(NetworkDevice.id.in_(device_ids)).delete(
            synchronize_session=False
        )
        if file_id:
            db.query(FileMetadata).filter(FileMetadata.id == file_id).delete(
                synchronize_session=False
            )
        if folder_id:
            db.query(StorageFolder).filter(StorageFolder.id == folder_id).delete(
                synchronize_session=False
            )


def _assert_cleanup(
    *,
    job_id: str | None,
    gap_id: str | None,
    device_ids: list[str],
    file_id: str | None,
    folder_id: str | None,
    object_key: str | None,
    username: str,
) -> dict[str, bool]:
    with get_db_session() as db:
        state = {
            "job_removed": not job_id or db.get(OfflinePatrolJobRecord, job_id) is None,
            "gap_removed": not gap_id or db.get(AssetGapRecord, gap_id) is None,
            "devices_removed": (
                db.query(NetworkDevice)
                .filter(NetworkDevice.id.in_(device_ids))
                .count()
                == 0
            ),
            "file_removed": not file_id or db.get(FileMetadata, file_id) is None,
            "folder_removed": not folder_id or db.get(StorageFolder, folder_id) is None,
        }
    state["user_removed"] = not get_user_model().objects.filter(
        username=username
    ).exists()
    state["object_removed"] = (
        not object_key or get_minio_storage().stat_object(object_key) is None
    )
    if not all(state.values()):
        raise AssertionError(f"identity/AssetGap E2E cleanup is incomplete: {state}")
    return state


def _expect_conflict(response: httpx.Response, text: str) -> dict[str, Any]:
    if response.status_code != 409:
        raise AssertionError(
            f"expected 409 conflict, got {response.status_code}: {response.text}"
        )
    try:
        payload: Any = response.json()
    except ValueError:
        payload = {"raw": response.text}
    decoded_response = json.dumps(payload, ensure_ascii=False, default=str)
    if text.casefold() not in decoded_response.casefold():
        raise AssertionError(
            f"conflict response did not explain {text!r}: {decoded_response}"
        )
    return {"status_code": response.status_code, "payload": payload}


def run(
    *,
    base_url: str,
    source_file: Path,
    output_path: Path,
    timeout_seconds: int,
    keep: bool,
) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:12]
    username = f"e2e_identity_{suffix}"
    password = f"E2e-{uuid.uuid4().hex}!Aa1"
    observed_name = f"E2E-ID-{suffix}"
    observed_ip = "198.51.100.240"
    same_name_id = f"e2e-name-{suffix}"
    same_ip_id = f"e2e-ip-{suffix}"
    device_ids = [same_name_id, same_ip_id]
    upload_name = f"{observed_name}-{observed_ip}.txt"
    source_bytes, provenance = _prepare_capture(
        source_file,
        observed_name=observed_name,
    )

    job_id: str | None = None
    file_id: str | None = None
    folder_id: str | None = None
    object_key: str | None = None
    gap_id: str | None = None
    user_created = False
    summary: dict[str, Any] = {
        "schema_version": "asset-gap-identity-control-full-stack-e2e/v1",
        "success": False,
        "base_url": base_url,
        "source_provenance": provenance,
        "fixture_scope": {
            "observed_name": observed_name,
            "observed_ip": observed_ip,
            "inventory_device_ids": device_ids,
        },
    }
    client = httpx.Client(base_url=base_url.rstrip("/"), timeout=30)
    try:
        _create_user(username, password)
        user_created = True
        _insert_inventory(
            same_name_id=same_name_id,
            same_ip_id=same_ip_id,
            observed_name=observed_name,
            observed_ip=observed_ip,
        )
        token = _login(client, username=username, password=password)
        client.headers["Authorization"] = f"Bearer {token}"

        created = _request_json(
            client,
            "POST",
            "/api/parser/asset-source-jobs/",
            json_body={
                "job_type": "parser_validation",
                "name": f"E2E ambiguous identity {suffix}",
                "periods": [
                    {
                        "role": "sample",
                        "label": "real lixian identity sample",
                        "observed_at": "2025-12-24T15:24:35+08:00",
                    }
                ],
                "vendor": "H3C",
                "model": "MSR5620",
                "publish_events": False,
                "idempotency_key": f"asset-gap-identity-create-{suffix}",
            },
        )
        job_id = str(created["id"])
        folder_id = str(created["folder_id"])
        summary.update(job_id=job_id, folder_id=folder_id)

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
        summary.update(file_id=file_id, object_key=object_key)
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
            client,
            "POST",
            "/api/storage/upload/complete/",
            json_body={"file_id": file_id, "size_bytes": len(source_bytes)},
        )
        _request_json(
            client,
            "POST",
            f"/api/parser/asset-source-jobs/{job_id}/sources/",
            json_body={
                "period_role": "sample",
                "file_id": file_id,
                "relative_path": upload_name,
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
        blocked = _wait_job(
            client,
            job_id,
            expected={"blocked"},
            timeout_seconds=timeout_seconds,
        )
        blocker_codes = {
            str(item.get("code") or "") for item in blocked.get("blockers") or []
        }
        if "ambiguous_device_binding" not in blocker_codes:
            raise AssertionError(
                f"identity conflict did not block preview: {blocked.get('blockers')}"
            )
        bindings = blocked.get("device_bindings") or []
        if len(bindings) != 1:
            raise AssertionError(f"expected one imported identity, got {len(bindings)}")
        binding = dict(bindings[0])
        candidates = binding.get("match_candidates") or []
        candidate_ids = [
            str(item.get("inventory_device_id") or "") for item in candidates
        ]
        if candidate_ids != [same_ip_id, same_name_id]:
            raise AssertionError(
                f"ranked Inventory candidates are incorrect: {candidates}"
            )

        governance_before = _governance_counts()
        gap = AssetGapRepository().record(
            check_item_id=f"e2e.identity.{suffix}",
            check_item_version_id=f"e2e-check-{suffix}",
            implementation_version_id=f"e2e-impl-{suffix}",
            canonical_command="display version",
            gap_type="runtime_bundle_missing",
            reason_code=f"e2e_identity_gap_{suffix}",
            purpose="production_patrol",
            patrol_batch_id=f"e2e-patrol-{suffix}",
            evidence_refs=[f"offline_job:{job_id}"],
            metadata={"fixture": "asset_gap_identity_control_e2e"},
        )
        gap_id = str(gap["id"])
        summary["gap_id"] = gap_id
        gap_page = _request_json(
            client,
            "GET",
            (
                "/api/parser/asset-gaps/?"
                + urlencode(
                    {
                        "status": "open",
                        "search": f"e2e_identity_gap_{suffix}",
                        "limit": 10,
                        "offset": 0,
                    }
                )
            ),
        )
        gap_items = gap_page.get("items") or []
        if len(gap_items) != 1 or str(gap_items[0].get("id")) != gap_id:
            raise AssertionError(f"AssetGap API did not isolate the fixture: {gap_page}")
        expected_target = "/skills/parser?" + urlencode(
            {
                "workspace": "production",
                "subtab": "source-jobs",
                "action": "asset-gap-source",
                "asset_gap_id": gap_id,
                "check_item_id": f"e2e.identity.{suffix}",
                "implementation_version_id": f"e2e-impl-{suffix}",
                "command": "display version",
            }
        )
        action = dict(gap_items[0].get("recommended_action") or {})
        if (
            action.get("code") != "import_rule_samples"
            or action.get("target_url") != expected_target
        ):
            raise AssertionError(f"AssetGap action is not an exact source scope: {action}")
        governance_after_read = _governance_counts()
        if governance_after_read != governance_before:
            raise AssertionError(
                "reading an AssetGap created a generation side effect: "
                f"{governance_before} -> {governance_after_read}"
            )

        direct_generation_rejection = _expect_conflict(
            client.post(
                "/api/parser/rule-gaps/seed-jobs/",
                params={"days": 90, "limit": 20},
            ),
            "不能直接创建规则生成任务",
        )
        governance_after_direct_rejection = _governance_counts()
        if governance_after_direct_rejection != governance_before:
            raise AssertionError(
                "a production-gap direct generation rejection had side effects: "
                f"{governance_before} -> {governance_after_direct_rejection}"
            )

        rejected_execution = _expect_conflict(
            client.post(
                f"/api/parser/asset-source-jobs/{job_id}/execute/",
                json={"idempotency_key": f"blocked-execute-{suffix}"},
            ),
            "quality gate blocked execution",
        )
        governance_after_rejection = _governance_counts()
        if governance_after_rejection != governance_before:
            raise AssertionError(
                "an unconfirmed identity reached Workflow/model generation: "
                f"{governance_before} -> {governance_after_rejection}"
            )

        _request_json(
            client,
            "POST",
            f"/api/parser/asset-source-jobs/{job_id}/device-bindings/confirm/",
            json_body={
                "bindings": [
                    {
                        "binding_id": str(binding["id"]),
                        "inventory_device_id": same_ip_id,
                    }
                ]
            },
        )
        confirmed = _request_json(
            client,
            "GET",
            f"/api/parser/asset-source-jobs/{job_id}/",
        )
        confirmed_bindings = confirmed.get("device_bindings") or []
        if (
            confirmed.get("status") != "ready"
            or confirmed.get("blockers")
            or len(confirmed_bindings) != 1
            or confirmed_bindings[0].get("match_status") != "confirmed"
            or confirmed_bindings[0].get("inventory_device_id") != same_ip_id
        ):
            raise AssertionError(
                f"identity confirmation did not immediately restore readiness: {confirmed}"
            )
        governance_after_confirmation = _governance_counts()
        if governance_after_confirmation != governance_before:
            raise AssertionError(
                "identity confirmation unexpectedly dispatched generation: "
                f"{governance_before} -> {governance_after_confirmation}"
            )

        preview_task_id = str(preview_dispatch.get("celery_task_id") or "")
        preview_task_state = (
            str(celery.AsyncResult(preview_task_id).state)
            if preview_task_id
            else "MISSING"
        )
        if preview_task_state != "SUCCESS":
            raise AssertionError(
                f"identity preview Celery task is not successful: {preview_task_state}"
            )
        summary.update(
            {
                "success": True,
                "job_id": job_id,
                "gap_id": gap_id,
                "file_id": file_id,
                "folder_id": folder_id,
                "object_key": object_key,
                "preview_task_id": preview_task_id,
                "preview_task_state": preview_task_state,
                "blocked_preview": {
                    "status": blocked.get("status"),
                    "blocker_codes": sorted(blocker_codes),
                    "binding": binding,
                },
                "asset_gap": gap_items[0],
                "direct_generation_rejection": direct_generation_rejection,
                "unconfirmed_execution": rejected_execution,
                "confirmed_state": {
                    "status": confirmed.get("status"),
                    "blockers": confirmed.get("blockers"),
                    "binding": confirmed_bindings[0],
                },
                "governance_counts": {
                    "before": governance_before,
                    "after_asset_gap_read": governance_after_read,
                    "after_direct_generation_rejection": (
                        governance_after_direct_rejection
                    ),
                    "after_unconfirmed_rejection": governance_after_rejection,
                    "after_identity_confirmation": governance_after_confirmation,
                },
                "kept": keep,
            }
        )
    finally:
        cleanup_error: str | None = None
        if not keep:
            try:
                _cleanup_database(
                    job_id=job_id,
                    gap_id=gap_id,
                    device_ids=device_ids,
                    file_id=None,
                    folder_id=None,
                    username=username,
                )
                if file_id:
                    _delete_json(client, f"/api/storage/files/{file_id}/")
                if folder_id:
                    _delete_json(client, f"/api/storage/folders/{folder_id}/")
                _cleanup_database(
                    job_id=None,
                    gap_id=None,
                    device_ids=device_ids,
                    file_id=file_id,
                    folder_id=folder_id,
                    username=username,
                )
                if user_created:
                    _delete_user(username)
                summary["cleanup"] = _assert_cleanup(
                    job_id=job_id,
                    gap_id=gap_id,
                    device_ids=device_ids,
                    file_id=file_id,
                    folder_id=folder_id,
                    object_key=object_key,
                    username=username,
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
        description="Verify AssetGap routing and ambiguous identity controls"
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("NETOPS_BFF_BASE_URL", "http://localhost:8001"),
    )
    parser.add_argument(
        "--source-file",
        type=Path,
        default=(
            ROOT
            / "lixian"
            / "Q2"
            / "XA-FOTIC-RT5620-1-10.34.2.219.txt"
        ),
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=(
            ROOT
            / ".tmp"
            / "asset-gap-identity-control-full-stack-e2e"
            / "summary.json"
        ),
    )
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep isolated fixture records for manual UI inspection.",
    )
    args = parser.parse_args()
    result = run(
        base_url=args.base_url,
        source_file=args.source_file.resolve(),
        output_path=args.output_path.resolve(),
        timeout_seconds=max(10, args.timeout_seconds),
        keep=args.keep,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Verify stale disclosure, late-fact revision, and immutable report releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
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

from scripts.test.check_item_report_download_full_stack_e2e import (  # noqa: E402
    _json_response,
    _seed,
    _wait_for_report,
)
from src.infrastructure.db.models import (  # noqa: E402
    AuditLogRecord,
    CheckAssessmentRecord,
    CheckExecutionRecord,
    CheckItemRuntimeManifestRecord,
    CommandSnapshot,
    PatrolBatchRecord,
    PatrolBatchSummaryProjection,
    PatrolReportMaterializationRequestRecord,
    PatrolRun,
    ReportContentReviewItemRecord,
    ReportContentReviewRecord,
    ReportManifestRecord,
    ReportReleaseRecord,
    StateObservationRecord,
    WorkflowRun,
)
from src.infrastructure.db.postgres import get_db_session  # noqa: E402
from src.infrastructure.storage.minio_client import (  # noqa: E402
    get_minio_storage,
)

ARTIFACT_PATHS = {
    "markdown": "report.md",
    "docx": "report.docx",
    "excel": "report.xlsx",
    "json": "report.json",
    "evidence": "evidence.zip",
}
CONFIRMATIONS = [
    "facts_reviewed",
    "unknown_scope_disclosed",
    "artifact_digests_verified",
]
CONTENT_CONFIRMATIONS = [
    "machine_reconciliation_reviewed",
    "critical_unknown_and_lifecycle_reviewed",
    "sampled_facts_reviewed",
    "traceability_verified",
    "narrative_readability_accepted",
    "actions_actionable",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _create_user(username: str, password: str, role: str) -> None:
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username=username,
        email=f"{username}@example.invalid",
        password=password,
    )
    assign_user_role(user, role)
    user.save()


def _delete_users(usernames: list[str]) -> None:
    get_user_model().objects.filter(username__in=usernames).delete()


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
        raise AssertionError(
            f"BFF login returned {response.status_code}: {response.text}"
        )
    token = str((response.json().get("data") or {}).get("access") or "")
    if not token:
        raise AssertionError("BFF login did not return an access token")
    return token


def _materialize(
    client: httpx.Client,
    *,
    batch_id: str,
    idempotency_key: str,
    timeout_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
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
    manifest = _json_response(
        client.get(f"/api/patrol/batches/{batch_id}/report-manifest")
    )
    if manifest.get("facts_status") != "final":
        raise AssertionError(f"report manifest is not final: {manifest}")
    return status, manifest


def _release(
    client: httpx.Client,
    *,
    batch_id: str,
    manifest: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    return _json_response(
        client.post(
            (
                f"/api/patrol/batches/{batch_id}/reports/"
                f"{manifest['report_manifest_id']}/release"
            ),
            headers={"Idempotency-Key": idempotency_key},
            json={
                "signoff_comment": (
                    "已复核事实范围、未知项和全部制品摘要，同意交付"
                ),
                "disclosure_confirmations": CONFIRMATIONS,
                "verified_artifact_sha256": manifest["artifact_sha256"],
            },
        )
    )


def _accept_content(
    client: httpx.Client,
    *,
    batch_id: str,
    manifest: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    manifest_id = str(manifest["report_manifest_id"])
    started = _json_response(
        client.post(
            (
                f"/api/patrol/batches/{batch_id}/reports/"
                f"{manifest_id}/content-review/start"
            ),
            headers={"Idempotency-Key": f"{idempotency_key}-start"},
            json={},
        )
    )["review"]
    review = started
    while int(review.get("pending_item_count") or 0) > 0:
        page = _json_response(
            client.get(
                (
                    f"/api/patrol/batches/{batch_id}/reports/"
                    f"{manifest_id}/content-review/items"
                ),
                params={
                    "page": 1,
                    "page_size": 200,
                    "review_status": "pending",
                },
            )
        )
        item_ids = [
            str(item["review_item_id"])
            for item in page.get("items") or []
        ]
        if not item_ids:
            raise AssertionError(
                "content review reports pending items but returned no work"
            )
        review = _json_response(
            client.post(
                (
                    f"/api/patrol/batches/{batch_id}/reports/"
                    f"{manifest_id}/content-review/items/decision"
                ),
                headers={
                    "Idempotency-Key": (
                        f"{idempotency_key}-decision-"
                        f"{review.get('accepted_item_count', 0)}"
                    )
                },
                json={
                    "review_item_ids": item_ids,
                    "decision": "accepted",
                    "review_comment": (
                        "已逐项核对事实结论、证据引用和披露边界"
                    ),
                },
            )
        )["review"]
    accepted = _json_response(
        client.post(
            (
                f"/api/patrol/batches/{batch_id}/reports/"
                f"{manifest_id}/content-review/accept"
            ),
            headers={"Idempotency-Key": f"{idempotency_key}-accept"},
            json={
                "acceptance_comment": (
                    "已验收强制事实、抽样事实、证据链和整改建议"
                ),
                "confirmations": CONTENT_CONFIRMATIONS,
                "verified_artifact_sha256": manifest[
                    "artifact_sha256"
                ],
            },
        )
    )["review"]
    if accepted.get("status") != "accepted":
        raise AssertionError(f"content review was not accepted: {accepted}")
    return accepted


def _download_manifest(
    client: httpx.Client,
    *,
    batch_id: str,
    manifest: dict[str, Any],
    expected_delivery_status: str,
    expected_facts_status: str,
) -> dict[str, Any]:
    manifest_id = str(manifest["report_manifest_id"])
    version = int(manifest["version"])
    expected_digests = dict(manifest["artifact_sha256"])
    object_keys = dict(manifest["artifact_object_keys"])
    storage = get_minio_storage()
    result: dict[str, Any] = {}
    for name, suffix in ARTIFACT_PATHS.items():
        response = client.get(
            f"/api/workflows/runs/patrol-batches/{batch_id}/{suffix}",
            params={"report_manifest_id": manifest_id},
        )
        if response.status_code != 200:
            raise AssertionError(
                f"download {name} returned {response.status_code}: "
                f"{response.text}"
            )
        actual_digest = hashlib.sha256(response.content).hexdigest()
        if actual_digest != str(expected_digests.get(name) or ""):
            raise AssertionError(f"{name} digest changed for manifest {manifest_id}")
        if response.headers.get("x-netops-report-status") != expected_delivery_status:
            raise AssertionError(f"{name} delivery status mismatch")
        if response.headers.get("x-netops-report-facts-status") != expected_facts_status:
            raise AssertionError(f"{name} facts status mismatch")
        if response.headers.get("x-netops-report-manifest-id") != manifest_id:
            raise AssertionError(f"{name} manifest identity header mismatch")
        if response.headers.get("x-netops-report-version") != str(version):
            raise AssertionError(f"{name} version header mismatch")
        object_content = storage.download_file(str(object_keys[name]))
        if object_content != response.content:
            raise AssertionError(f"{name} BFF bytes differ from MinIO bytes")
        result[name] = {
            "bytes": len(response.content),
            "sha256": actual_digest,
        }
    return result


def _mark_initial_facts_stale(batch_id: str) -> None:
    with get_db_session() as db:
        execution = (
            db.query(CheckExecutionRecord)
            .filter(CheckExecutionRecord.patrol_batch_id == batch_id)
            .one()
        )
        assessment = (
            db.query(CheckAssessmentRecord)
            .filter(CheckAssessmentRecord.patrol_batch_id == batch_id)
            .one()
        )
        execution.freshness_status = "stale"
        execution.reason_codes_json = ["freshness_stale"]
        execution.updated_at = _utcnow()
        assessment.freshness_status = "stale"
        assessment.blocking_reason_codes_json = ["freshness_stale"]


def _append_late_fact(batch_id: str) -> None:
    with get_db_session() as db:
        execution = (
            db.query(CheckExecutionRecord)
            .filter(CheckExecutionRecord.patrol_batch_id == batch_id)
            .one()
        )
        execution.reason_codes_json = [
            "freshness_stale",
            "late_fact_revision",
        ]
        execution.updated_at = _utcnow()


def _frozen_manifest_state(manifest_id: str) -> dict[str, Any]:
    with get_db_session() as db:
        manifest = db.get(ReportManifestRecord, manifest_id)
        if manifest is None:
            raise AssertionError("report manifest disappeared")
        release = (
            db.query(ReportReleaseRecord)
            .filter(ReportReleaseRecord.report_manifest_id == manifest_id)
            .one_or_none()
        )
        return {
            "status": str(manifest.status),
            "source_digest": str(manifest.source_digest),
            "source_facts": dict(manifest.source_facts_json or {}),
            "artifact_sha256": dict(manifest.artifact_sha256_json or {}),
            "object_keys": dict(manifest.artifact_object_keys_json or {}),
            "release": release.to_dict() if release is not None else None,
        }


def _cleanup(
    *,
    batch_id: str,
    run_id: str,
    workflow_run_id: str,
) -> dict[str, int]:
    deleted_objects = get_minio_storage().delete_objects_by_prefix(
        f"reports/patrol/{batch_id}"
    )
    with get_db_session() as db:
        release_ids = [
            str(item.id)
            for item in db.query(ReportReleaseRecord)
            .filter(ReportReleaseRecord.patrol_batch_id == batch_id)
            .all()
        ]
        deleted_releases = (
            db.query(ReportReleaseRecord)
            .filter(ReportReleaseRecord.patrol_batch_id == batch_id)
            .delete(synchronize_session=False)
        )
        review_rows = (
            db.query(ReportContentReviewRecord)
            .filter(ReportContentReviewRecord.patrol_batch_id == batch_id)
            .all()
        )
        review_ids = [str(item.id) for item in review_rows]
        deleted_review_items = (
            db.query(ReportContentReviewItemRecord)
            .filter(
                ReportContentReviewItemRecord.review_id.in_(review_ids)
            )
            .delete(synchronize_session=False)
            if review_ids
            else 0
        )
        deleted_content_reviews = (
            db.query(ReportContentReviewRecord)
            .filter(ReportContentReviewRecord.patrol_batch_id == batch_id)
            .delete(synchronize_session=False)
        )
        deleted_audits = (
            db.query(AuditLogRecord)
            .filter(
                AuditLogRecord.resource_id.in_(
                    [batch_id, *release_ids, *review_ids]
                )
            )
            .delete(synchronize_session=False)
        )
        deleted_projections = (
            db.query(PatrolBatchSummaryProjection)
            .filter(PatrolBatchSummaryProjection.patrol_batch_id == batch_id)
            .delete(synchronize_session=False)
        )
        deleted_requests = (
            db.query(PatrolReportMaterializationRequestRecord)
            .filter(
                PatrolReportMaterializationRequestRecord.patrol_batch_id
                == batch_id
            )
            .delete(synchronize_session=False)
        )
        deleted_manifests = (
            db.query(ReportManifestRecord)
            .filter(ReportManifestRecord.patrol_batch_id == batch_id)
            .delete(synchronize_session=False)
        )
        deleted_assessments = (
            db.query(CheckAssessmentRecord)
            .filter(CheckAssessmentRecord.patrol_batch_id == batch_id)
            .delete(synchronize_session=False)
        )
        deleted_observations = (
            db.query(StateObservationRecord)
            .filter(StateObservationRecord.run_id == run_id)
            .delete(synchronize_session=False)
        )
        deleted_snapshots = (
            db.query(CommandSnapshot)
            .filter(CommandSnapshot.run_id == run_id)
            .delete(synchronize_session=False)
        )
        deleted_executions = (
            db.query(CheckExecutionRecord)
            .filter(CheckExecutionRecord.patrol_batch_id == batch_id)
            .delete(synchronize_session=False)
        )
        deleted_runtime_manifests = (
            db.query(CheckItemRuntimeManifestRecord)
            .filter(
                CheckItemRuntimeManifestRecord.patrol_batch_id == batch_id
            )
            .delete(synchronize_session=False)
        )
        deleted_runs = (
            db.query(PatrolRun)
            .filter(PatrolRun.id == run_id)
            .delete(synchronize_session=False)
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
        "releases": int(deleted_releases),
        "content_review_items": int(deleted_review_items),
        "content_reviews": int(deleted_content_reviews),
        "audits": int(deleted_audits),
        "projections": int(deleted_projections),
        "requests": int(deleted_requests),
        "manifests": int(deleted_manifests),
        "assessments": int(deleted_assessments),
        "observations": int(deleted_observations),
        "snapshots": int(deleted_snapshots),
        "executions": int(deleted_executions),
        "runtime_manifests": int(deleted_runtime_manifests),
        "runs": int(deleted_runs),
        "batches": int(deleted_batches),
        "workflow_runs": int(deleted_workflows),
    }


def run(
    *,
    base_url: str,
    source_file: Path,
    timeout_seconds: int,
    output_path: Path,
) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:12]
    batch_id = f"report-revision-e2e-{suffix}"
    run_id = f"report-revision-run-{suffix}"
    workflow_run_id = f"report-revision-workflow-{suffix}"
    device_id = f"report-revision-device-{suffix}"
    snapshot_id = f"report-revision-snapshot-{suffix}"
    generator = f"report-generator-{suffix}"
    content_reviewer = f"report-content-reviewer-{suffix}"
    signer = f"report-signer-{suffix}"
    password = f"E2e-{uuid.uuid4().hex}!A1"
    users = [generator, content_reviewer, signer]
    summary: dict[str, Any] = {
        "schema_version": "report-revision-release-full-stack-e2e/v1",
        "batch_id": batch_id,
        "source_file": str(source_file),
        "success": False,
    }
    users_created = False
    try:
        _create_user(generator, password, "operator")
        _create_user(content_reviewer, password, "operator")
        _create_user(signer, password, "admin")
        users_created = True
        summary["seed"] = _seed(
            source_file=source_file,
            batch_id=batch_id,
            run_id=run_id,
            workflow_run_id=workflow_run_id,
            device_id=device_id,
            snapshot_id=snapshot_id,
        )
        _mark_initial_facts_stale(batch_id)
        with httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=90,
        ) as generator_client, httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=90,
        ) as reviewer_client, httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=90,
        ) as signer_client:
            generator_client.headers["Authorization"] = (
                f"Bearer {_login(generator_client, username=generator, password=password)}"
            )
            reviewer_client.headers["Authorization"] = (
                f"Bearer {_login(reviewer_client, username=content_reviewer, password=password)}"
            )
            signer_client.headers["Authorization"] = (
                f"Bearer {_login(signer_client, username=signer, password=password)}"
            )

            status_v1, manifest_v1 = _materialize(
                generator_client,
                batch_id=batch_id,
                idempotency_key=f"report-v1-{suffix}",
                timeout_seconds=timeout_seconds,
            )
            if int(manifest_v1["version"]) != 1:
                raise AssertionError("first manifest is not version 1")
            if int(manifest_v1.get("late_fact_count") or 0) != 0:
                raise AssertionError("first manifest contains unexpected late facts")
            storage = get_minio_storage()
            integrity_faults: list[dict[str, Any]] = []
            evidence_key = str(manifest_v1["artifact_object_keys"]["evidence"])
            original_evidence = storage.download_file(evidence_key)
            if original_evidence is None:
                raise AssertionError("materialized evidence object is unavailable")
            if not storage.delete_object(evidence_key):
                raise AssertionError("could not inject a missing-object fault")
            missing_response = reviewer_client.post(
                (
                    f"/api/patrol/batches/{batch_id}/reports/"
                    f"{manifest_v1['report_manifest_id']}/content-review/start"
                ),
                headers={"Idempotency-Key": f"content-v1-missing-{suffix}"},
                json={},
            )
            if (
                missing_response.status_code != 409
                or "artifact_objects_verified" not in missing_response.text
            ):
                raise AssertionError(
                    "missing report artifact did not fail closed: "
                    f"{missing_response.status_code} {missing_response.text}"
                )
            integrity_faults.append(
                {
                    "fault": "object_missing",
                    "status_code": missing_response.status_code,
                    "blocked": True,
                }
            )
            if not storage.upload_file(evidence_key, original_evidence):
                raise AssertionError("could not restore the missing evidence object")

            if not storage.upload_file(evidence_key, b"digest-drift-evidence"):
                raise AssertionError("could not inject an artifact digest drift")
            drift_preview = _json_response(
                reviewer_client.get(
                    (
                        f"/api/patrol/batches/{batch_id}/reports/"
                        f"{manifest_v1['report_manifest_id']}/content-review"
                    )
                )
            )
            machine = drift_preview.get("machine_validation") or {}
            artifact_check = next(
                (
                    item
                    for item in machine.get("checks") or []
                    if item.get("id") == "artifact_objects_verified"
                ),
                {},
            )
            evidence_status = (
                (artifact_check.get("detail") or {})
                .get("artifacts", {})
                .get("evidence", {})
                .get("status")
            )
            if machine.get("passed") is not False or evidence_status != (
                "digest_mismatch"
            ):
                raise AssertionError(
                    f"artifact digest drift was not exposed: {drift_preview}"
                )
            integrity_faults.append(
                {
                    "fault": "digest_mismatch",
                    "preview_status": evidence_status,
                    "blocked": True,
                }
            )
            if not storage.upload_file(evidence_key, original_evidence):
                raise AssertionError("could not restore the drifted evidence object")
            content_v1 = _accept_content(
                reviewer_client,
                batch_id=batch_id,
                manifest=manifest_v1,
                idempotency_key=f"content-v1-{suffix}",
            )
            markdown_key = str(manifest_v1["artifact_object_keys"]["markdown"])
            original_markdown = storage.download_file(markdown_key)
            if original_markdown is None:
                raise AssertionError("materialized markdown object is unavailable")
            if not storage.upload_file(markdown_key, b"tampered-after-acceptance"):
                raise AssertionError("could not inject a post-acceptance drift")
            drifted_release_response = signer_client.post(
                (
                    f"/api/patrol/batches/{batch_id}/reports/"
                    f"{manifest_v1['report_manifest_id']}/release"
                ),
                headers={"Idempotency-Key": f"release-v1-drift-{suffix}"},
                json={
                    "signoff_comment": "verify post-acceptance object drift",
                    "disclosure_confirmations": CONFIRMATIONS,
                    "verified_artifact_sha256": manifest_v1[
                        "artifact_sha256"
                    ],
                },
            )
            if (
                drifted_release_response.status_code != 409
                or "machine validation no longer passes"
                not in drifted_release_response.text
            ):
                raise AssertionError(
                    "post-acceptance digest drift did not block release: "
                    f"{drifted_release_response.status_code} "
                    f"{drifted_release_response.text}"
                )
            integrity_faults.append(
                {
                    "fault": "post_acceptance_digest_mismatch",
                    "status_code": drifted_release_response.status_code,
                    "blocked": True,
                }
            )
            if not storage.upload_file(markdown_key, original_markdown):
                raise AssertionError("could not restore the drifted markdown object")
            release_v1 = _release(
                signer_client,
                batch_id=batch_id,
                manifest=manifest_v1,
                idempotency_key=f"release-v1-{suffix}",
            )
            downloads_v1 = _download_manifest(
                signer_client,
                batch_id=batch_id,
                manifest=manifest_v1,
                expected_delivery_status="released",
                expected_facts_status="final",
            )
            frozen_v1 = _frozen_manifest_state(
                str(manifest_v1["report_manifest_id"])
            )
            report_v1_json = json.loads(
                signer_client.get(
                    (
                        f"/api/workflows/runs/patrol-batches/{batch_id}/"
                        "report.json"
                    ),
                    params={
                        "report_manifest_id": (
                            manifest_v1["report_manifest_id"]
                        )
                    },
                ).content
            )
            if "stale" not in json.dumps(
                report_v1_json,
                ensure_ascii=False,
            ):
                raise AssertionError("stale evidence was not disclosed in v1")

            _append_late_fact(batch_id)
            time.sleep(0.2)
            status_v2, manifest_v2 = _materialize(
                generator_client,
                batch_id=batch_id,
                idempotency_key=f"report-v2-{suffix}",
                timeout_seconds=timeout_seconds,
            )
            if int(manifest_v2["version"]) != 2:
                raise AssertionError("late fact did not create version 2")
            if (
                manifest_v2["source_facts_digest"]
                == manifest_v1["source_facts_digest"]
            ):
                raise AssertionError("report revision reused the v1 source digest")
            if int(manifest_v2.get("late_fact_count") or 0) < 1:
                raise AssertionError("v2 did not disclose the late fact")

            downloads_v1_after_revision = _download_manifest(
                signer_client,
                batch_id=batch_id,
                manifest=manifest_v1,
                expected_delivery_status="released",
                expected_facts_status="superseded",
            )
            if downloads_v1_after_revision != downloads_v1:
                raise AssertionError("v1 artifact bytes changed after revision")
            frozen_v1_after_revision = _frozen_manifest_state(
                str(manifest_v1["report_manifest_id"])
            )
            if {
                key: value
                for key, value in frozen_v1_after_revision.items()
                if key != "status"
            } != {
                key: value
                for key, value in frozen_v1.items()
                if key != "status"
            }:
                raise AssertionError("v1 frozen facts or release changed")
            if frozen_v1_after_revision["status"] != "superseded":
                raise AssertionError("v1 was not marked superseded")

            content_v2 = _accept_content(
                reviewer_client,
                batch_id=batch_id,
                manifest=manifest_v2,
                idempotency_key=f"content-v2-{suffix}",
            )
            release_v2 = _release(
                signer_client,
                batch_id=batch_id,
                manifest=manifest_v2,
                idempotency_key=f"release-v2-{suffix}",
            )
            downloads_v2 = _download_manifest(
                signer_client,
                batch_id=batch_id,
                manifest=manifest_v2,
                expected_delivery_status="released",
                expected_facts_status="final",
            )
            versions = _json_response(
                signer_client.get(
                    f"/api/patrol/batches/{batch_id}/reports",
                    params={"page": 1, "page_size": 20},
                )
            )
            version_rows = {
                int(item["version"]): item for item in versions["items"]
            }
            if set(version_rows) != {1, 2}:
                raise AssertionError(f"unexpected report versions: {versions}")
            if version_rows[1]["facts_status"] != "superseded":
                raise AssertionError("v1 history does not disclose supersession")
            if any(
                version_rows[version]["release_status"] != "released"
                for version in (1, 2)
            ):
                raise AssertionError("both report versions must remain released")
            if version_rows[1]["conclusion"] != manifest_v1["conclusion"]:
                raise AssertionError(
                    "supersession changed the frozen v1 conclusion"
                )
            if version_rows[1]["conclusion"].get("formal") is not True:
                raise AssertionError(
                    "supersession downgraded the frozen v1 conclusion"
                )
            if (
                version_rows[1]["current_state_authoritative"]
                != manifest_v1["current_state_authoritative"]
            ):
                raise AssertionError(
                    "supersession changed the frozen v1 authority semantics"
                )

            summary.update(
                {
                    "success": True,
                    "v1": {
                        "materialization": status_v1,
                        "manifest": manifest_v1,
                        "release_digest": release_v1["release"][
                            "release_digest"
                        ],
                        "content_acceptance_digest": content_v1[
                            "acceptance_digest"
                        ],
                        "downloads": downloads_v1,
                    },
                    "v2": {
                        "materialization": status_v2,
                        "manifest": manifest_v2,
                        "release_digest": release_v2["release"][
                            "release_digest"
                        ],
                        "content_acceptance_digest": content_v2[
                            "acceptance_digest"
                        ],
                        "downloads": downloads_v2,
                    },
                    "versions": versions,
                    "artifact_integrity_faults": integrity_faults,
                }
            )
    except Exception as exc:
        summary["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        raise
    finally:
        try:
            summary["cleanup"] = _cleanup(
                batch_id=batch_id,
                run_id=run_id,
                workflow_run_id=workflow_run_id,
            )
        except Exception as cleanup_exc:
            summary["cleanup_error"] = {
                "type": type(cleanup_exc).__name__,
                "message": str(cleanup_exc),
            }
        if users_created:
            _delete_users(users)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                summary,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify immutable report releases and late-fact revisions "
            "through the real stack"
        )
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
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument(
        "--output-path",
        type=Path,
        default=(
            ROOT
            / ".tmp"
            / "report-revision-release-full-stack-e2e"
            / "summary.json"
        ),
    )
    args = parser.parse_args()
    result = run(
        base_url=args.base_url,
        source_file=args.source_file.resolve(),
        timeout_seconds=args.timeout_seconds,
        output_path=args.output_path.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("success") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())

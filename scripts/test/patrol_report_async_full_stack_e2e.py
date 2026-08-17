#!/usr/bin/env python
"""Real API/Celery/PostgreSQL/Redis/MinIO report materialization E2E."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.common.config import get_settings
from src.core.celery_tasks.celery_app import celery
from src.infrastructure.db.models import (
    AuditLogRecord,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    PatrolBatchSummaryProjection,
    PatrolReportMaterializationRequestRecord,
    WorkflowRun,
)
from src.infrastructure.db.postgres import get_db_session
from src.infrastructure.storage.minio_client import get_minio_storage


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _seed(batch_id: str, run_id: str, target_id: str) -> None:
    now = _utcnow()
    with get_db_session() as db:
        batch = PatrolBatchRecord(
            patrol_batch_id=batch_id,
            workflow_name="patrol-change-event",
            launch_mode="workflow",
            patrol_mode="offline_history",
            report_purpose="offline_historical",
            target_type="device",
            target_label="Async report E2E device",
            selector_json={"source": "full_stack_e2e"},
            target_count=1,
            launched_count=1,
            skipped_count=0,
            dispatch_status="completed",
            dispatch_completed_at=now,
            report_status="not_requested",
            archived=False,
            created_at=now,
            updated_at=now,
        )
        run = WorkflowRun(
            id=run_id,
            template_name="patrol-change-event",
            source="full-stack-e2e",
            status="completed",
            context={
                "patrol_batch_id": batch_id,
                "device_id": "report-e2e-device",
                "device_name": "REPORT-E2E-SW1",
            },
            current_step_index=1,
            created_at=now,
            updated_at=now,
            completed_at=now,
        )
        db.add_all([batch, run])
        db.flush()
        db.add(
            PatrolBatchDeviceRecord(
                id=target_id,
                patrol_batch_id=batch_id,
                device_id="report-e2e-device",
                device_name="REPORT-E2E-SW1",
                management_ip="192.0.2.10",
                selection_status="launched",
                preflight_status="ready",
                workflow_run_id=run_id,
                dispatch_status="dispatched",
                dispatched_at=now,
                created_at=now,
                updated_at=now,
            )
        )


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    headers = {}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    response = client.request(method, path, headers=headers, json={})
    if response.status_code >= 400:
        raise AssertionError(
            f"{method} {path} returned {response.status_code}: {response.text}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise AssertionError(f"{method} {path} returned a non-object payload")
    if payload.get("success") is True and isinstance(payload.get("data"), dict):
        return dict(payload["data"])
    return payload


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
    payload = response.json()
    access = str((payload.get("data") or {}).get("access") or "")
    if not access:
        raise AssertionError("BFF login did not return an access token")
    return access


def _dispatch(
    client: httpx.Client,
    batch_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    return _request(
        client,
        "POST",
        f"/api/patrol/batches/{batch_id}/reports/materialize",
        idempotency_key=idempotency_key,
    )


def _wait_task(
    client: httpx.Client,
    batch_id: str,
    task_id: str,
    *,
    expected: str,
    timeout_seconds: int,
) -> tuple[dict[str, Any], str]:
    deadline = time.monotonic() + timeout_seconds
    last_payload: dict[str, Any] = {}
    last_task_state = "PENDING"
    while time.monotonic() < deadline:
        last_task_state = str(celery.AsyncResult(task_id).state)
        last_payload = _request(
            client,
            "GET",
            (
                f"/api/patrol/batches/{batch_id}/reports/"
                "materialization-status"
            ),
        )
        if expected == "completed":
            if (
                last_task_state == "SUCCESS"
                and last_payload.get("status") == "completed"
            ):
                return last_payload, last_task_state
        elif expected == "failed":
            if (
                last_task_state == "FAILURE"
                and last_payload.get("status") == "failed"
            ):
                return last_payload, last_task_state
        else:
            raise ValueError(f"unsupported expected task state: {expected}")
        time.sleep(0.5)
    raise TimeoutError(
        f"task {task_id} did not become {expected}; "
        f"celery={last_task_state}, api={last_payload}"
    )


def _projection(batch_id: str) -> dict[str, Any]:
    with get_db_session() as db:
        row = db.get(PatrolBatchSummaryProjection, batch_id)
        if row is None:
            raise AssertionError("report projection was not persisted")
        return row.to_dict()


def _request_rows(batch_id: str) -> list[dict[str, Any]]:
    with get_db_session() as db:
        rows = (
            db.query(PatrolReportMaterializationRequestRecord)
            .filter(
                PatrolReportMaterializationRequestRecord.patrol_batch_id
                == batch_id
            )
            .order_by(
                PatrolReportMaterializationRequestRecord.requested_at,
                PatrolReportMaterializationRequestRecord.id,
            )
            .all()
        )
        return [row.to_dict() for row in rows]


def _touch_run(run_id: str) -> None:
    with get_db_session() as db:
        run = db.get(WorkflowRun, run_id)
        if run is None:
            raise AssertionError("workflow run disappeared during E2E")
        run.updated_at = _utcnow() + timedelta(seconds=1)


def _docker(action: str, container: str) -> None:
    result = subprocess.run(
        ["docker", action, container],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"docker {action} {container} failed: "
            f"{result.stdout}\n{result.stderr}"
        )


def _wait_minio_ready(timeout_seconds: int = 60) -> None:
    settings = get_settings()
    endpoint = str(settings.MINIO_ENDPOINT).rstrip("/")
    if not endpoint.startswith(("http://", "https://")):
        scheme = "https" if settings.MINIO_SECURE else "http"
        endpoint = f"{scheme}://{endpoint}"
    health_url = f"{endpoint}/minio/health/ready"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            response = httpx.get(health_url, timeout=2)
            if response.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    raise TimeoutError("MinIO did not recover before the timeout")


def _cleanup(batch_id: str, run_id: str) -> dict[str, int]:
    storage = get_minio_storage()
    deleted_objects = storage.delete_objects_by_prefix(
        f"reports/patrol/{batch_id}"
    )
    with get_db_session() as db:
        deleted_audits = (
            db.query(AuditLogRecord)
            .filter(AuditLogRecord.resource_id == batch_id)
            .delete(synchronize_session=False)
        )
        deleted_projections = (
            db.query(PatrolBatchSummaryProjection)
            .filter(
                PatrolBatchSummaryProjection.patrol_batch_id == batch_id
            )
            .delete(synchronize_session=False)
        )
        deleted_batches = (
            db.query(PatrolBatchRecord)
            .filter(PatrolBatchRecord.patrol_batch_id == batch_id)
            .delete(synchronize_session=False)
        )
        deleted_runs = (
            db.query(WorkflowRun)
            .filter(WorkflowRun.id == run_id)
            .delete(synchronize_session=False)
        )
    return {
        "objects": int(deleted_objects),
        "audits": int(deleted_audits),
        "projections": int(deleted_projections),
        "batches": int(deleted_batches),
        "runs": int(deleted_runs),
    }


def run(
    *,
    base_url: str,
    username: str,
    password: str,
    timeout_seconds: int,
    output_path: Path,
    minio_container: str,
) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:12]
    batch_id = f"report-async-e2e-{suffix}"
    run_id = f"report-e2e-run-{suffix}"
    target_id = f"report-e2e-target-{suffix}"
    prefix = f"reports/patrol/{batch_id}"
    minio_stopped = False
    summary: dict[str, Any] = {
        "schema_version": "patrol-report-async-full-stack-e2e/v1",
        "batch_id": batch_id,
        "base_url": base_url,
        "success": False,
    }
    try:
        storage = get_minio_storage()
        _wait_minio_ready()
        if not storage.is_ready():
            raise RuntimeError("MinIO client is not initialized")
        _seed(batch_id, run_id, target_id)
        with httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=15,
        ) as client:
            client.headers["Authorization"] = (
                f"Bearer {_login(client, username=username, password=password)}"
            )
            first_key = f"report-success-{suffix}"
            first = _dispatch(client, batch_id, first_key)
            if first.get("accepted") is not True:
                raise AssertionError(f"first dispatch was not accepted: {first}")
            immediate_replay = _dispatch(client, batch_id, first_key)
            if (
                immediate_replay.get("accepted") is not False
                or immediate_replay.get("reason") != "idempotent_replay"
                or immediate_replay.get("task_id") != first.get("task_id")
            ):
                raise AssertionError(
                    "same-key replay was not idempotent while active"
                )
            completed, first_task_state = _wait_task(
                client,
                batch_id,
                str(first["task_id"]),
                expected="completed",
                timeout_seconds=timeout_seconds,
            )
            first_projection = _projection(batch_id)
            first_object_key = str(first_projection.get("object_key") or "")
            first_object = storage.download_file(first_object_key)
            if not first_object or batch_id.encode("utf-8") not in first_object:
                raise AssertionError("materialized MinIO report is missing or invalid")
            completed_replay = _dispatch(client, batch_id, first_key)
            if (
                completed_replay.get("reason") != "idempotent_replay"
                or completed_replay.get("status") != "completed"
            ):
                raise AssertionError(
                    "same-key replay after completion created a new attempt"
                )
            if len(_request_rows(batch_id)) != 1:
                raise AssertionError("same-key replay created duplicate ledger rows")

            _touch_run(run_id)
            _docker("stop", minio_container)
            minio_stopped = True
            failed_key = f"report-failure-{suffix}"
            failed_dispatch = _dispatch(client, batch_id, failed_key)
            if failed_dispatch.get("accepted") is not True:
                raise AssertionError(
                    f"failure injection dispatch was not accepted: {failed_dispatch}"
                )
            failed, failed_task_state = _wait_task(
                client,
                batch_id,
                str(failed_dispatch["task_id"]),
                expected="failed",
                timeout_seconds=timeout_seconds,
            )
            if not failed.get("completed_at") or not failed.get("error"):
                raise AssertionError(
                    "failed report attempt lacks terminal time or error"
                )
            retained_projection = _projection(batch_id)
            if (
                retained_projection.get("source_digest")
                != first_projection.get("source_digest")
                or retained_projection.get("object_key") != first_object_key
            ):
                raise AssertionError(
                    "failed materialization replaced the last valid projection"
                )

            _docker("start", minio_container)
            minio_stopped = False
            _wait_minio_ready()
            if storage.download_file(first_object_key) != first_object:
                raise AssertionError(
                    "last valid report object changed during failed attempt"
                )

            retry_key = f"report-retry-{suffix}"
            retry = _dispatch(client, batch_id, retry_key)
            if retry.get("accepted") is not True:
                raise AssertionError(f"retry dispatch was not accepted: {retry}")
            retried, retry_task_state = _wait_task(
                client,
                batch_id,
                str(retry["task_id"]),
                expected="completed",
                timeout_seconds=timeout_seconds,
            )
            final_projection = _projection(batch_id)
            final_object_key = str(final_projection.get("object_key") or "")
            if (
                final_projection.get("source_digest")
                == first_projection.get("source_digest")
                or not storage.download_file(final_object_key)
            ):
                raise AssertionError(
                    "retry did not publish the revised report projection"
                )
            rows = _request_rows(batch_id)
            if [str(row["status"]) for row in rows] != [
                "completed",
                "failed",
                "completed",
            ]:
                raise AssertionError(
                    f"unexpected request ledger states: "
                    f"{[row['status'] for row in rows]}"
                )
            summary.update(
                {
                    "success": True,
                    "first": {
                        "dispatch": first,
                        "completed": completed,
                        "celery_state": first_task_state,
                        "object_key": first_object_key,
                        "object_bytes": len(first_object),
                    },
                    "same_key_replay": completed_replay,
                    "failure": {
                        "dispatch": failed_dispatch,
                        "completed": failed,
                        "celery_state": failed_task_state,
                        "retained_object_key": first_object_key,
                    },
                    "retry": {
                        "dispatch": retry,
                        "completed": retried,
                        "celery_state": retry_task_state,
                        "object_key": final_object_key,
                    },
                    "request_statuses": [
                        str(row["status"]) for row in rows
                    ],
                    "minio_prefix": prefix,
                }
            )
    finally:
        if minio_stopped:
            _docker("start", minio_container)
            _wait_minio_ready()
        cleanup = _cleanup(batch_id, run_id)
        summary["cleanup"] = cleanup
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate report materialization through real HTTP, Celery, "
            "PostgreSQL, Redis and MinIO."
        )
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8001",
    )
    parser.add_argument(
        "--username",
        default=os.getenv("NETOPS_E2E_USERNAME", "admin"),
    )
    parser.add_argument(
        "--password-env",
        default="NETOPS_E2E_PASSWORD",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
    )
    parser.add_argument(
        "--minio-container",
        default="netops-minio",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / ".tmp"
        / "patrol-report-async-full-stack-e2e"
        / "summary.json",
    )
    args = parser.parse_args()
    password = os.getenv(args.password_env, "")
    if not password:
        parser.error(
            f"set {args.password_env} to the BFF test user's password"
        )
    summary = run(
        base_url=args.base_url,
        username=args.username,
        password=password,
        timeout_seconds=max(30, args.timeout_seconds),
        output_path=args.output,
        minio_container=args.minio_container,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if summary.get("success") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())

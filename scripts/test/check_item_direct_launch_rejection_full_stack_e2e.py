#!/usr/bin/env python
"""Verify production direct patrol is rejected before any durable side effect."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.celery_tasks.celery_app import celery
from src.infrastructure.db.models import (
    AuditLogRecord,
    CollectionExecutionPlanRecord,
    PatrolBatchRecord,
    WorkflowRun,
)
from src.infrastructure.db.postgres import get_db_session


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


def _database_snapshot() -> dict[str, int]:
    with get_db_session() as db:
        return {
            "patrol_batches": db.query(PatrolBatchRecord).count(),
            "execution_plans": db.query(CollectionExecutionPlanRecord).count(),
            "workflow_runs": db.query(WorkflowRun).count(),
            "patrol_launch_audits": (
                db.query(AuditLogRecord)
                .filter(AuditLogRecord.action == "device_patrol_launch")
                .count()
            ),
        }


def _patrol_queue_snapshot() -> dict[str, int]:
    with celery.connection_for_read() as connection:
        channel = connection.channel()
        try:
            state = channel.queue_declare(
                queue="netops.patrol",
                passive=True,
            )
            return {
                "message_count": int(state.message_count),
                "consumer_count": int(state.consumer_count),
            }
        finally:
            channel.close()


def _contains_failure_code(payload: Any) -> bool:
    if isinstance(payload, dict):
        if payload.get("failure_code") == "direct_check_item_patrol_disabled":
            return True
        return any(_contains_failure_code(value) for value in payload.values())
    if isinstance(payload, list):
        return any(_contains_failure_code(value) for value in payload)
    return "direct_check_item_patrol_disabled" in str(payload)


def run(
    *,
    base_url: str,
    username: str,
    password: str,
    output_path: Path,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "schema_version": "check-item-direct-launch-rejection-e2e/v1",
        "base_url": base_url,
        "success": False,
    }
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=30) as client:
        token = _login(client, username=username, password=password)
        client.headers["Authorization"] = f"Bearer {token}"
        before_database = _database_snapshot()
        before_queue = _patrol_queue_snapshot()
        response = client.post(
            "/api/devices/patrol/launch/",
            json={
                "device_ids": ["direct-rejection-must-not-resolve-device"],
                "mode": "production",
                "launch_mode": "direct",
                "workflow_name": "patrol-change-event",
            },
        )
        try:
            response_payload: Any = response.json()
        except Exception:
            response_payload = response.text
        time.sleep(0.5)
        after_database = _database_snapshot()
        after_queue = _patrol_queue_snapshot()

    if response.status_code != 409:
        raise AssertionError(
            f"expected BFF 409, got {response.status_code}: {response.text}"
        )
    if not _contains_failure_code(response_payload):
        raise AssertionError(
            "BFF response did not preserve direct_check_item_patrol_disabled"
        )
    if after_database != before_database:
        raise AssertionError(
            f"database changed after rejected launch: "
            f"{before_database} -> {after_database}"
        )
    if after_queue["message_count"] != before_queue["message_count"]:
        raise AssertionError(
            f"netops.patrol queue changed after rejected launch: "
            f"{before_queue} -> {after_queue}"
        )

    summary.update(
        {
            "success": True,
            "http_status": response.status_code,
            "response": response_payload,
            "database_before": before_database,
            "database_after": after_database,
            "queue_before": before_queue,
            "queue_after": after_queue,
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify production direct patrol has no durable side effects"
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
        "--output-path",
        type=Path,
        default=(
            ROOT
            / ".tmp"
            / "check-item-direct-launch-rejection-e2e"
            / "summary.json"
        ),
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
        output_path=args.output_path.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

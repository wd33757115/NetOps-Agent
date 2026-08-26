#!/usr/bin/env python
"""Verify 1,000-item report content review pagination and recovery."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterator
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

from scripts.test.check_item_report_download_full_stack_e2e import (  # noqa: E402
    _json_response,
    _seed,
)
from scripts.test.report_revision_release_full_stack_e2e import (  # noqa: E402
    _cleanup,
    _create_user,
    _delete_users,
    _login,
    _mark_initial_facts_stale,
    _materialize,
)
from src.infrastructure.db.models import (  # noqa: E402
    ReportContentReviewItemRecord,
    ReportContentReviewRecord,
)
from src.infrastructure.db.postgres import get_db_session  # noqa: E402


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _review_url(batch_id: str, manifest_id: str, suffix: str = "") -> str:
    base = (
        f"/api/patrol/batches/{batch_id}/reports/{manifest_id}/content-review"
    )
    return f"{base}{suffix}"


def _replace_with_scale_items(review_id: str, *, count: int) -> None:
    now = datetime.now(timezone.utc)
    with get_db_session() as db:
        review = db.get(ReportContentReviewRecord, review_id)
        if review is None:
            raise AssertionError("content review disappeared before scale injection")
        db.query(ReportContentReviewItemRecord).filter(
            ReportContentReviewItemRecord.review_id == review.id
        ).delete(synchronize_session=False)
        db.bulk_save_objects(
            [
                ReportContentReviewItemRecord(
                    id=f"scale-{index:058d}",
                    review_id=review.id,
                    review_item_id=f"{index:064x}",
                    category="check_item_traceability",
                    review_reasons_json=["check_item_traceability"],
                    stratum_key=f"device-{index % 100:03d}",
                    fact_type="assessment",
                    fact_id=f"assessment-{index:04d}",
                    device_id=f"device-{index % 100:03d}",
                    check_item_id=f"system.check-{index % 20:02d}",
                    severity=None,
                    conclusion_status="healthy",
                    evidence_refs_json=[f"snapshot-{index:04d}"],
                    summary_json={
                        "ordinal": index,
                        "fixture": "content-review-scale-e2e",
                    },
                    review_status="pending",
                    created_at=now,
                )
                for index in range(count)
            ]
        )
        review.required_item_count = count
        review.accepted_item_count = 0
        review.rejected_item_count = 0
        review.updated_at = now


@contextmanager
def _login_client(
    base_url: str,
    *,
    username: str,
    password: str,
) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=90) as client:
        token = _login(client, username=username, password=password)
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


def run(
    *,
    base_url: str,
    frontend_url: str,
    source_file: Path,
    timeout_seconds: int,
    output_path: Path,
    hold_seconds: int = 0,
) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:12]
    batch_id = f"content-review-scale-e2e-{suffix}"
    run_id = f"content-review-scale-run-{suffix}"
    workflow_run_id = f"content-review-scale-workflow-{suffix}"
    device_id = f"content-review-scale-device-{suffix}"
    snapshot_id = f"content-review-scale-snapshot-{suffix}"
    generator = f"content-scale-generator-{suffix}"
    reviewer = f"content-scale-reviewer-{suffix}"
    password = f"E2e-{uuid.uuid4().hex}!A1"
    users = [generator, reviewer]
    summary: dict[str, Any] = {
        "schema_version": "report-content-review-scale-full-stack-e2e/v1",
        "success": False,
        "batch_id": batch_id,
        "source_file": str(source_file),
        "scale_item_count": 1000,
    }
    users_created = False
    try:
        _create_user(generator, password, "operator")
        _create_user(reviewer, password, "operator")
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
        with _login_client(
            base_url,
            username=generator,
            password=password,
        ) as generator_client:
            _, manifest = _materialize(
                generator_client,
                batch_id=batch_id,
                idempotency_key=f"scale-report-{suffix}",
                timeout_seconds=timeout_seconds,
            )
        manifest_id = str(manifest["report_manifest_id"])

        with _login_client(
            base_url,
            username=reviewer,
            password=password,
        ) as reviewer_client:
            review = _json_response(
                reviewer_client.post(
                    _review_url(batch_id, manifest_id, "/start"),
                    headers={"Idempotency-Key": f"scale-start-{suffix}"},
                    json={},
                )
            )["review"]
        _replace_with_scale_items(str(review["id"]), count=1000)

        if hold_seconds > 0:
            _write_json(
                output_path,
                {
                    **summary,
                    "state": "browser_fixture_ready",
                    "manifest_id": manifest_id,
                    "browser_url": (
                        f"{frontend_url.rstrip('/')}/devices?tab=patrol"
                        f"&batch_search={batch_id}"
                    ),
                    "temporary_username": reviewer,
                    "temporary_password": password,
                    "hold_seconds": hold_seconds,
                },
            )
            time.sleep(hold_seconds)

        with _login_client(
            base_url,
            username=reviewer,
            password=password,
        ) as first_client:
            page_one = _json_response(
                first_client.get(
                    _review_url(batch_id, manifest_id, "/items"),
                    params={"page": 1, "page_size": 50},
                )
            )
            page_twenty = _json_response(
                first_client.get(
                    _review_url(batch_id, manifest_id, "/items"),
                    params={"page": 20, "page_size": 50},
                )
            )
            if page_one["total"] != 1000 or page_twenty["total"] != 1000:
                raise AssertionError("server pagination did not preserve the total")
            page_one_ids = [item["review_item_id"] for item in page_one["items"]]
            page_twenty_ids = [
                item["review_item_id"] for item in page_twenty["items"]
            ]
            if len(page_one_ids) != 50 or len(page_twenty_ids) != 50:
                raise AssertionError("scale pages are not bounded to 50 items")
            if set(page_one_ids) & set(page_twenty_ids):
                raise AssertionError("scale pagination returned overlapping pages")
            decision_payload = {
                "review_item_ids": page_one_ids,
                "decision": "accepted",
                "review_comment": "accepted after evidence review",
            }
            first_decision = _json_response(
                first_client.post(
                    _review_url(batch_id, manifest_id, "/items/decision"),
                    headers={"Idempotency-Key": f"scale-first-{suffix}"},
                    json=decision_payload,
                )
            )["review"]
            exact_retry = _json_response(
                first_client.post(
                    _review_url(batch_id, manifest_id, "/items/decision"),
                    headers={"Idempotency-Key": f"scale-first-{suffix}"},
                    json=decision_payload,
                )
            )["review"]
            if (
                int(first_decision["accepted_item_count"]) != 50
                or int(exact_retry["accepted_item_count"]) != 50
            ):
                raise AssertionError("idempotent decision retry changed review counts")
        with _login_client(
            base_url,
            username=reviewer,
            password=password,
        ) as resumed_client:
            chunk_count = 1
            while True:
                pending = _json_response(
                    resumed_client.get(
                        _review_url(batch_id, manifest_id, "/items"),
                        params={
                            "page": 1,
                            "page_size": 200,
                            "review_status": "pending",
                        },
                    )
                )
                item_ids = [
                    item["review_item_id"] for item in pending.get("items") or []
                ]
                if not item_ids:
                    break
                _json_response(
                    resumed_client.post(
                        _review_url(batch_id, manifest_id, "/items/decision"),
                        headers={
                            "Idempotency-Key": (
                                f"scale-resume-{suffix}-{chunk_count}"
                            )
                        },
                        json={
                            "review_item_ids": item_ids,
                            "decision": "accepted",
                            "review_comment": "accepted after evidence review",
                        },
                    )
                )
                chunk_count += 1
            final_review = _json_response(
                resumed_client.get(_review_url(batch_id, manifest_id))
            )["review"]
        if (
            int(final_review["accepted_item_count"]) != 1000
            or int(final_review["pending_item_count"]) != 0
            or final_review.get("can_accept") is not True
        ):
            raise AssertionError(f"scale review did not recover: {final_review}")

        summary.update(
            {
                "success": True,
                "manifest_id": manifest_id,
                "page_one_count": len(page_one_ids),
                "page_twenty_count": len(page_twenty_ids),
                "pages_disjoint": True,
                "idempotent_retry_count": int(
                    exact_retry["accepted_item_count"]
                ),
                "relogin_recovery": True,
                "final_review": final_review,
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
        _write_json(output_path, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify 1,000-item content review through the real stack"
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("NETOPS_BFF_BASE_URL", "http://localhost:8001"),
    )
    parser.add_argument(
        "--frontend-url",
        default=os.getenv("NETOPS_FRONTEND_BASE_URL", "http://localhost:3000"),
    )
    parser.add_argument(
        "--source-file",
        type=Path,
        default=(
            ROOT / "lixian" / "Q2" / "LAB-DEVICE-RT5620-1-198.51.2.219.txt"
        ),
    )
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--hold-seconds", type=int, default=0)
    parser.add_argument(
        "--output-path",
        type=Path,
        default=(
            ROOT
            / ".tmp"
            / "report-content-review-scale-full-stack-e2e"
            / "summary.json"
        ),
    )
    args = parser.parse_args()
    result = run(
        base_url=args.base_url,
        frontend_url=args.frontend_url,
        source_file=args.source_file.resolve(),
        timeout_seconds=args.timeout_seconds,
        output_path=args.output_path.resolve(),
        hold_seconds=max(0, args.hold_seconds),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("success") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())

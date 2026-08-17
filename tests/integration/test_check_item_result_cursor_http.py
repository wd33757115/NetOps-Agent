from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from time import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from src.auth.models import CurrentUser
from src.core.check_items.batch_results import (
    CheckItemBatchResultService,
    _cursor_scope,
    _encode_cursor,
)
from src.gateway import workflow_api
from src.infrastructure.db.models import (
    CheckExecutionRecord,
    CheckItemRecord,
    CheckItemVersionRecord,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
)
from src.infrastructure.db.postgres import engine

pytestmark = pytest.mark.integration


def test_check_result_cursor_over_http_uses_postgres_keyset_pagination(
    postgres_available,
    monkeypatch,
):
    del postgres_available
    suffix = uuid.uuid4().hex
    batch_id = f"cursor-http-{suffix}"
    check_item_id = f"test.cursor.{suffix}"
    check_item_version_id = f"check-version-{suffix}"
    result_count = 1_105
    page_size = 200
    now = datetime.now(timezone.utc)

    connection = engine.connect()
    transaction = connection.begin()
    session_factory = sessionmaker(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    @contextmanager
    def session_scope():
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    try:
        with session_scope() as db:
            db.add(
                CheckItemRecord(
                    id=check_item_id,
                    name="Cursor integration check",
                    category="integration",
                    description="HTTP and PostgreSQL cursor verification",
                    status="active",
                    current_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(
                CheckItemVersionRecord(
                    id=check_item_version_id,
                    check_item_id=check_item_id,
                    version=1,
                    result_mode="structured",
                    assessment_mode="health",
                    canonical_contract_json={},
                    report_projection="integration.cursor",
                    status="active",
                    digest=("a" * 32) + suffix,
                    created_by="integration-test",
                    created_at=now,
                    activated_at=now,
                )
            )
            db.add(
                PatrolBatchRecord(
                    patrol_batch_id=batch_id,
                    workflow_name="check-item-cursor-integration",
                    launch_mode="workflow",
                    patrol_mode="production",
                    report_purpose="shadow_validation",
                    target_type="devices",
                    target_label=f"{result_count} device cursor integration",
                    selector_json={"integration_test": True},
                    target_count=result_count,
                    launched_count=result_count,
                    skipped_count=0,
                    target_manifest_digest="b" * 64,
                    dispatch_status="completed",
                    dispatch_requested_at=now,
                    dispatch_started_at=now,
                    dispatch_completed_at=now,
                    report_status="not_requested",
                    archived=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.flush()
            for index in range(result_count):
                device_id = f"{suffix}-device-{index:04d}"
                db.add(
                    PatrolBatchDeviceRecord(
                        id=f"target-{suffix}-{index:04d}",
                        patrol_batch_id=batch_id,
                        device_id=device_id,
                        device_name=f"Switch {index:04d}",
                        management_ip=(
                            f"198.51.{index // 250}.{(index % 250) + 1}"
                        ),
                        selection_status="launched",
                        preflight_status="ready",
                        dispatch_status="dispatched",
                        dispatch_attempts=1,
                        dispatched_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )
                db.add(
                    CheckExecutionRecord(
                        id=f"execution-{suffix}-{index:04d}",
                        idempotency_key=f"cursor|{suffix}|{index:04d}",
                        patrol_batch_id=batch_id,
                        device_id=device_id,
                        check_item_version_id=check_item_version_id,
                        collection_action_ids_json=[],
                        execution_status="completed",
                        reason_codes_json=[],
                        observation_count=1,
                        observed_entity_count=1,
                        source_identity_status="confirmed",
                        output_quality_status="complete",
                        freshness_status="fresh",
                        empty_result_classification="not_empty",
                        coverage_status="complete",
                        coverage_basis="observed",
                        risk_verification_status="verified",
                        target_manifest_digest="b" * 64,
                        evidence_refs_json=[],
                        started_at=now,
                        completed_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )

        service = CheckItemBatchResultService(session_scope)
        monkeypatch.setattr(
            workflow_api,
            "CheckItemBatchResultService",
            lambda: service,
        )

        app = FastAPI()
        app.include_router(workflow_api.router)
        target_route = next(
            route
            for route in app.routes
            if getattr(route, "name", "")
            == "list_patrol_batch_check_results"
        )

        async def current_operator():
            return CurrentUser(
                user_id="integration-test",
                username="integration-test",
                role="operator",
            )

        for dependency in target_route.dependant.dependencies:
            app.dependency_overrides[dependency.call] = current_operator

        with TestClient(app) as client:
            first_response = client.get(
                f"/api/v1/workflows/runs/patrol-batches/{batch_id}/check-results",
                params={"limit": page_size},
            )
            assert first_response.status_code == 200
            first_page = first_response.json()
            assert first_page["total"] == result_count
            assert first_page["pagination_mode"] == "offset"
            assert len(first_page["items"]) == page_size
            assert first_page["has_more"] is True
            assert first_page["next_cursor"]

            count_queries = 0

            def track_sql(
                _connection,
                _cursor,
                statement,
                _parameters,
                _context,
                _executemany,
            ):
                nonlocal count_queries
                if "count(" in str(statement).lower():
                    count_queries += 1

            event.listen(connection, "before_cursor_execute", track_sql)
            try:
                cursor = str(first_page["next_cursor"])
                seen_ids = {
                    item["execution_id"] for item in first_page["items"]
                }
                cursor_page_count = 0
                while cursor:
                    response = client.get(
                        f"/api/v1/workflows/runs/patrol-batches/{batch_id}/check-results",
                        params={
                            "limit": page_size,
                            "cursor": cursor,
                        },
                    )
                    assert response.status_code == 200
                    cursor_page = response.json()
                    assert cursor_page["total"] == result_count
                    assert cursor_page["pagination_mode"] == "cursor"
                    page_ids = {
                        item["execution_id"]
                        for item in cursor_page["items"]
                    }
                    assert seen_ids.isdisjoint(page_ids)
                    seen_ids.update(page_ids)
                    cursor = cursor_page["next_cursor"]
                    cursor_page_count += 1
            finally:
                event.remove(connection, "before_cursor_execute", track_sql)

            assert count_queries == 0
            assert cursor_page_count >= 5
            assert len(seen_ids) == result_count

            first_cursor = str(first_page["next_cursor"])
            replacement = "A" if first_cursor[-1] != "A" else "B"
            tampered_response = client.get(
                f"/api/v1/workflows/runs/patrol-batches/{batch_id}/check-results",
                params={
                    "limit": page_size,
                    "cursor": first_cursor[:-1] + replacement,
                },
            )
            assert tampered_response.status_code == 400

            mismatched_scope_response = client.get(
                f"/api/v1/workflows/runs/patrol-batches/{batch_id}/check-results",
                params={
                    "limit": page_size,
                    "cursor": first_cursor,
                    "assessment_status": "healthy",
                },
            )
            assert mismatched_scope_response.status_code == 400

            expired_cursor = _encode_cursor(
                patrol_batch_id=batch_id,
                scope_digest=_cursor_scope(
                    search=None,
                    assessment_status=None,
                    completeness_status=None,
                    source_identity_status=None,
                    output_quality_status=None,
                    freshness_status=None,
                    coverage_status=None,
                ),
                values=(
                    first_page["items"][-1]["device_id"],
                    check_item_version_id,
                    first_page["items"][-1]["execution_id"],
                ),
                total=result_count,
                now=int(time()) - 120,
                ttl_seconds=60,
            )
            expired_response = client.get(
                f"/api/v1/workflows/runs/patrol-batches/{batch_id}/check-results",
                params={"limit": page_size, "cursor": expired_cursor},
            )
            assert expired_response.status_code == 400

            deep_offset_response = client.get(
                f"/api/v1/workflows/runs/patrol-batches/{batch_id}/check-results",
                params={"limit": 20, "offset": 1001},
            )
            assert deep_offset_response.status_code == 400
    finally:
        transaction.rollback()
        connection.close()

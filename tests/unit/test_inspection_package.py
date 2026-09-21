from contextlib import nullcontext
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_inspection_online_tasks import RUN_ID, deliver, runtime  # noqa: F401

from src.apps.inspection.backend import access, lifecycle, patrol_reports_api
from src.core.celery_tasks.celery_app import celery
from src.infrastructure.db import models as m
from src.platform.kernel.registry import discover, load_symbol


def test_package_owns_routes_tasks_and_unique_periodic_schedules():
    manifest = discover()["inspection"]
    router = load_symbol(manifest.data["backend"]["router"])
    paths = {r.path for r in router.routes}
    assert {
        "/devices/patrol/launch",
        "/batches/{patrol_batch_id}/retry",
        "/patrol/offline-jobs",
        "/patrol/report-templates",
    } <= paths
    celery.loader.import_default_modules()
    for task in manifest.data["backend"]["task_names"]:
        assert task in celery.tasks
    tasks = [row["task"] for row in celery.conf.beat_schedule.values()]
    for schedule in manifest.data["backend"]["schedules"]:
        assert tasks.count(schedule["task"]) == 1
    assert manifest.data["dependencies"] == {}


def test_inspection_report_routes_deny_missing_app_admission():
    app = FastAPI()
    app.include_router(patrol_reports_api.router)
    assert TestClient(app).get("/api/v1/patrol/report-templates").status_code == 401


def test_drain_preserves_rule_jobs_and_settles_fixed_execution(runtime, monkeypatch):  # noqa: F811
    scope, calls, settle, _ = runtime
    from test_inspection_online_tasks import TASK_ID

    from src.apps.inspection.backend.online_execution import OnlinePatrolExecution

    with scope() as db:
        engine = db.get_bind()
        for model in (
            m.OfflinePatrolJobRecord,
            m.PatrolCurrentAuthorityRequestRecord,
            m.WorkflowStepRecord,
        ):
            model.__table__.create(engine, checkfirst=True)
        for purpose in ("offline_patrol", "rule_asset_source"):
            db.add(
                m.OfflinePatrolJobRecord(
                    id=purpose,
                    idempotency_key=purpose,
                    purpose=purpose,
                    job_type="single_patrol",
                    name=purpose,
                    owner_id="owner",
                    status="queued",
                )
            )
        db.add(
            m.PatrolBatchRecord(
                patrol_batch_id="rule",
                workflow_name="rollout-live-capture",
                launch_mode="direct",
                patrol_mode="parser_validation",
                target_type="rollout_stage",
                target_label="rule",
                selector_json={"rollout_stage_execution_id": "stage"},
                dispatch_status="pending",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
    OnlinePatrolExecution().claim("batch", "plan", TASK_ID)
    monkeypatch.setattr(lifecycle, "get_db_session", scope)
    monkeypatch.setattr(lifecycle, "execution_lock", lambda **_: nullcontext())
    assert lifecycle.pending() > 0
    lifecycle.drain()
    assert lifecycle.pending() == 0
    with scope() as db:
        assert db.get(m.OfflinePatrolJobRecord, "offline_patrol").status == "failed"
        assert db.get(m.OfflinePatrolJobRecord, "rule_asset_source").status == "queued"
        assert db.get(m.PatrolBatchRecord, "rule").dispatch_status == "pending"
        assert db.get(m.PatrolRun, RUN_ID).owner_token is None
    assert deliver().get("ignored")
    assert not calls


def test_app_execution_and_drain_use_exclusive_postgres_lock():
    with access.execution_lock():
        with pytest.raises(ValueError, match="排空"):
            with access.execution_lock(exclusive=True):
                pytest.fail("drain entered while a worker is still executing")
    with access.execution_lock(exclusive=True):
        with pytest.raises(ValueError):
            with access.execution_lock():
                pytest.fail("worker entered during drain")


def test_report_recovery_never_claims_rule_governance_batches(monkeypatch):
    from src.apps.inspection.backend import tasks
    from src.core.patrol.batch_manifest import PatrolBatchManifestRepository

    monkeypatch.setattr(access, "execution_lock", lambda **_: nullcontext())
    monkeypatch.setattr(access, "require_running", lambda: None)
    monkeypatch.setattr(access, "inspection_batch", lambda key: key == "inspection")
    monkeypatch.setattr(
        PatrolBatchManifestRepository,
        "terminal_report_candidate_ids",
        lambda self, **_: ["rule", "inspection"],
    )
    claimed, published = [], []
    monkeypatch.setattr(
        PatrolBatchManifestRepository,
        "request_terminal_report",
        lambda self, key: claimed.append(key) or True,
    )
    monkeypatch.setattr(
        tasks.materialize_patrol_batch_report_task,
        "apply_async",
        lambda **kwargs: published.extend(kwargs["args"]),
    )
    result = tasks.reconcile_patrol_batch_reports_task.run()
    assert claimed == published == ["inspection"]
    assert result["candidate_count"] == result["requested_count"] == 1

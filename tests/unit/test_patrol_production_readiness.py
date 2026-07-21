from __future__ import annotations

from types import SimpleNamespace

from src.core.patrol.production_readiness import (
    PatrolProductionReadinessService,
    PatrolReadinessCheck,
)


def _check(
    check_id: str,
    status: str = "ok",
    *,
    detail: dict | None = None,
) -> PatrolReadinessCheck:
    return PatrolReadinessCheck(
        id=check_id,
        name=check_id,
        scope="test",
        status=status,
        message=f"{check_id}:{status}",
        remediation=f"repair {check_id}",
        latency_ms=1.0,
        detail=detail or {},
    )


def _stub_checks(monkeypatch, service, *, beat_status: str = "ok"):
    workflow = _check(
        "workflow",
        detail={"required_queues": ["netops.patrol"]},
    )
    monkeypatch.setattr(service, "_check_workflow", lambda *_args: workflow)
    monkeypatch.setattr(service, "_check_postgres", lambda: _check("postgres"))
    monkeypatch.setattr(
        service,
        "_check_database_schema",
        lambda: _check("database_schema"),
    )
    monkeypatch.setattr(service, "_check_redis", lambda: _check("redis"))
    monkeypatch.setattr(
        service,
        "_check_celery_broker",
        lambda: _check("celery_broker"),
    )

    def queue_check(queues):
        assert queues == ["netops.patrol"]
        return _check("celery_patrol_queues")

    monkeypatch.setattr(service, "_check_celery_queues", queue_check)
    monkeypatch.setattr(
        service,
        "_check_celery_beat",
        lambda: _check("celery_beat", beat_status),
    )
    monkeypatch.setattr(service, "_check_minio", lambda: _check("minio"))


def test_readiness_is_ready_only_when_all_hard_checks_pass(monkeypatch):
    service = PatrolProductionReadinessService()
    _stub_checks(monkeypatch, service)

    result = service.evaluate()

    assert result["status"] == "ready"
    assert result["can_launch"] is True
    assert result["blocking_count"] == 0
    assert len(result["checks"]) == 8


def test_readiness_blocks_launch_and_exposes_stable_failure_code(monkeypatch):
    service = PatrolProductionReadinessService()
    _stub_checks(monkeypatch, service, beat_status="blocked")

    result = service.evaluate()

    assert result["status"] == "blocked"
    assert result["can_launch"] is False
    assert result["blocking_count"] == 1
    assert result["blockers"][0]["failure_code"] == "patrol_celery_beat_not_ready"


def test_readiness_keeps_warnings_non_blocking(monkeypatch):
    service = PatrolProductionReadinessService()
    _stub_checks(monkeypatch, service)
    monkeypatch.setattr(service, "_check_minio", lambda: _check("minio", "warning"))

    result = service.evaluate()

    assert result["status"] == "degraded"
    assert result["can_launch"] is True
    assert result["warning_count"] == 1


def test_workflow_check_blocks_disabled_required_skill(monkeypatch):
    service = PatrolProductionReadinessService()
    template = SimpleNamespace(
        version="1.0",
        steps=[SimpleNamespace(skill_name="device-patrol", is_subworkflow=False)],
    )
    manager = SimpleNamespace(
        list_all_skills=lambda: [{"name": "device-patrol", "enabled": False}]
    )

    from src.core.skills import celery_routing
    from src.core.workflows import registry
    from src.skills import skill_manager

    monkeypatch.setattr(registry, "get_template", lambda _name: template)
    monkeypatch.setattr(skill_manager, "get_skill_manager", lambda: manager)
    monkeypatch.setattr(
        celery_routing,
        "resolve_skill_celery_queue",
        lambda _name: "netops.patrol",
    )

    result = service._check_workflow("patrol-change-event", "workflow")

    assert result.status == "blocked"
    assert result.detail["disabled_skills"] == ["device-patrol"]
    assert result.detail["required_queues"] == ["netops.patrol"]


def test_queue_check_requires_a_worker_for_every_workflow_queue(monkeypatch):
    service = PatrolProductionReadinessService()

    class Inspect:
        @staticmethod
        def active_queues():
            return {"control@host": [{"name": "netops.default"}]}

    class Control:
        @staticmethod
        def inspect(*, timeout):
            assert timeout > 0
            return Inspect()

    from src.core.celery_tasks import celery_app

    monkeypatch.setattr(celery_app, "celery", SimpleNamespace(control=Control()))

    result = service._check_celery_queues(["netops.patrol"])

    assert result.status == "blocked"
    assert result.detail["missing_queues"] == ["netops.patrol"]


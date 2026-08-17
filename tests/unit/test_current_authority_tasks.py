from __future__ import annotations

from src.core.celery_tasks.tasks import (
    reconcile_current_authority_dispatches_task,
)


def test_reconcile_current_authority_reuses_one_readiness_snapshot(monkeypatch):
    from src.core.check_items import current_authority
    from src.core.patrol import production_readiness

    readiness_calls: list[tuple[str, str]] = []
    prepared: list[str] = []

    class FakeReadinessService:
        def evaluate(
            self,
            *,
            workflow_name: str,
            launch_mode: str,
            executing_worker_queues: list[str],
        ):
            readiness_calls.append((workflow_name, launch_mode))
            assert executing_worker_queues == []
            return {
                "status": "ready",
                "can_launch": True,
                "blockers": [],
                "snapshot": "one-reconcile-round",
            }

    class FakeAuthorityService:
        def __init__(self, production_readiness=None):
            self.production_readiness = production_readiness

        def pending_dispatch_request_ids(self, *, limit: int):
            assert limit == 100
            return ["request-1", "request-2"]

        def prepare_dispatch(self, request_id: str):
            assert self.production_readiness is not None
            assert self.production_readiness()["snapshot"] == (
                "one-reconcile-round"
            )
            prepared.append(request_id)

    monkeypatch.setattr(
        current_authority,
        "PatrolCurrentAuthorityService",
        FakeAuthorityService,
    )
    monkeypatch.setattr(
        production_readiness,
        "PatrolProductionReadinessService",
        FakeReadinessService,
    )

    result = reconcile_current_authority_dispatches_task.run()

    assert readiness_calls == [
        ("patrol-attested-offline-current", "workflow")
    ]
    assert prepared == ["request-1", "request-2"]
    assert result == {
        "success": True,
        "candidate_count": 2,
        "prepared_count": 2,
        "deferred_count": 0,
        "failed_count": 0,
        "failures": [],
    }


def test_reconcile_current_authority_skips_readiness_without_candidates(
    monkeypatch,
):
    from src.core.check_items import current_authority
    from src.core.patrol import production_readiness

    class FakeAuthorityService:
        def __init__(self, production_readiness=None):
            assert production_readiness is None

        @staticmethod
        def pending_dispatch_request_ids(*, limit: int):
            assert limit == 100
            return []

    class UnexpectedReadinessService:
        def __init__(self):
            raise AssertionError("readiness should not run without candidates")

    monkeypatch.setattr(
        current_authority,
        "PatrolCurrentAuthorityService",
        FakeAuthorityService,
    )
    monkeypatch.setattr(
        production_readiness,
        "PatrolProductionReadinessService",
        UnexpectedReadinessService,
    )

    result = reconcile_current_authority_dispatches_task.run()

    assert result["candidate_count"] == 0
    assert result["success"] is True


def test_reconcile_current_authority_defers_all_candidates_when_blocked(
    monkeypatch,
):
    from src.core.check_items import current_authority
    from src.core.patrol import production_readiness

    prepare_calls: list[str] = []

    class FakeAuthorityService:
        def __init__(self, production_readiness=None):
            self.production_readiness = production_readiness

        @staticmethod
        def pending_dispatch_request_ids(*, limit: int):
            assert limit == 100
            return ["request-1", "request-2"]

        def prepare_dispatch(self, request_id: str):
            prepare_calls.append(request_id)

    class BlockedReadinessService:
        @staticmethod
        def evaluate(
            *,
            workflow_name: str,
            launch_mode: str,
            executing_worker_queues: list[str],
        ):
            assert workflow_name == "patrol-attested-offline-current"
            assert launch_mode == "workflow"
            assert executing_worker_queues == []
            return {
                "status": "blocked",
                "can_launch": False,
                "blockers": [{
                    "id": "celery_beat",
                    "failure_code": "patrol_celery_beat_not_ready",
                }],
            }

    monkeypatch.setattr(
        current_authority,
        "PatrolCurrentAuthorityService",
        FakeAuthorityService,
    )
    monkeypatch.setattr(
        production_readiness,
        "PatrolProductionReadinessService",
        BlockedReadinessService,
    )

    result = reconcile_current_authority_dispatches_task.run()

    assert prepare_calls == []
    assert result == {
        "success": True,
        "candidate_count": 2,
        "prepared_count": 0,
        "deferred_count": 2,
        "failed_count": 0,
        "failures": [],
        "readiness_status": "blocked",
        "blocker_codes": ["patrol_celery_beat_not_ready"],
    }

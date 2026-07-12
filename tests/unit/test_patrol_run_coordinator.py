from types import SimpleNamespace

from src.core.patrol.run_coordinator import PatrolRunCoordinator


class _Decision:
    def to_dict(self):
        return {
            "action": "resume_abandoned",
            "attempt_number": 2,
            "run_id": "patrol-task-1-attempt-1",
            "owner_token": "owner-2",
        }


class _Repository:
    def __init__(self):
        self.redeliveries = []
        self.completions = []

    def prepare_redelivery_attempt(self, run_id, **kwargs):
        self.redeliveries.append((run_id, kwargs))
        return _Decision()

    def mark_celery_task_completed(self, run_id, **kwargs):
        self.completions.append((run_id, kwargs))


def test_coordinator_allocates_fresh_owner_for_first_delivery():
    result = PatrolRunCoordinator(_Repository()).prepare_delivery(
        SimpleNamespace(retries=0, delivery_info={}, id="task-1")
    )

    assert result["action"] == "execute"
    assert result["attempt_number"] == 0
    assert len(result["owner_token"]) == 32


def test_coordinator_delegates_redelivery_takeover_as_one_repository_operation():
    repository = _Repository()
    result = PatrolRunCoordinator(repository).prepare_delivery(
        SimpleNamespace(
            retries=1,
            delivery_info={"redelivered": True},
            id="task-1",
            hostname="worker-2",
        ),
        mode="production",
    )

    assert result["action"] == "resume_abandoned"
    assert repository.redeliveries == [
        ("patrol-task-1", {"worker_id": "worker-2", "mode": "production"})
    ]


def test_coordinator_preserves_owner_token_when_marking_completion():
    repository = _Repository()
    PatrolRunCoordinator(repository).complete("run-1", owner_token="owner-1")

    assert repository.completions == [
        ("run-1", {"owner_token": "owner-1"})
    ]

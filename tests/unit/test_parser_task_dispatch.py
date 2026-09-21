from __future__ import annotations

from src.common.config import get_settings
from src.core.parser.tasks import dispatch_ready_parser_jobs


def test_parser_dispatch_uses_app_queue_and_parser_specific_time_limits():
    class Repository:
        def __init__(self):
            self.dispatched: list[tuple[str, str]] = []

        @staticmethod
        def list_dispatchable_jobs(**_kwargs):
            return [{"id": "parser-job-1"}]

        def mark_job_dispatched(self, job_id, task_id):
            self.dispatched.append((job_id, task_id))

    class Celery:
        def __init__(self):
            self.calls: list[dict] = []

        def send_task(self, name, **payload):
            self.calls.append({"name": name, **payload})

    repository = Repository()
    celery = Celery()

    result = dispatch_ready_parser_jobs(
        repository=repository,
        celery_app=celery,
    )

    settings = get_settings()
    assert result["dispatched"] == ["parser-job-1"]
    assert celery.calls[0]["name"] == "netops.rule_governance.execute_parser"
    assert "args" not in celery.calls[0]
    assert celery.calls[0]["queue"] == "app.rule-governance"
    assert celery.calls[0]["soft_time_limit"] == (
        settings.PARSER_GENERATION_TASK_SOFT_TIME_LIMIT_SECONDS
    )
    assert celery.calls[0]["time_limit"] == (settings.PARSER_GENERATION_TASK_TIME_LIMIT_SECONDS)


def test_dispatch_collision_never_marks_another_delivery_failed():
    from unittest.mock import Mock

    from src.apps.rule_governance.backend.parser.repository import ParserJobStateError

    repository, broker = Mock(), Mock()
    repository.list_dispatchable_jobs.return_value = [{"id": "job"}]
    repository.mark_job_dispatched.side_effect = ParserJobStateError("already dispatched")
    result = dispatch_ready_parser_jobs(repository=repository, celery_app=broker)
    assert result["failed"][0]["job_id"] == "job"
    broker.send_task.assert_not_called()
    repository.transition_job_status.assert_not_called()


def test_uncertain_broker_failure_only_fails_the_original_pending_delivery():
    from unittest.mock import Mock

    from src.apps.rule_governance.backend.parser.repository import ParserJobStateError

    repository, broker = Mock(), Mock()
    repository.list_dispatchable_jobs.return_value = [{"id": "job"}]
    broker.send_task.side_effect = RuntimeError("acknowledgement lost")
    repository.transition_job_status.side_effect = ParserJobStateError("already running")
    result = dispatch_ready_parser_jobs(repository=repository, celery_app=broker)
    assert result["failed"]
    expected = repository.transition_job_status.call_args.kwargs
    assert expected["expected_status"] == "pending"
    assert expected["expected_celery_task_id"] == broker.send_task.call_args.kwargs["task_id"]

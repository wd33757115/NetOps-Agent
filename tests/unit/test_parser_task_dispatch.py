from __future__ import annotations

from src.common.config import get_settings
from src.core.parser.tasks import dispatch_ready_parser_jobs


def test_parser_dispatch_uses_ai_queue_and_parser_specific_time_limits():
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
    assert celery.calls[0]["queue"] == "netops.ai"
    assert celery.calls[0]["soft_time_limit"] == (
        settings.PARSER_GENERATION_TASK_SOFT_TIME_LIMIT_SECONDS
    )
    assert celery.calls[0]["time_limit"] == (
        settings.PARSER_GENERATION_TASK_TIME_LIMIT_SECONDS
    )

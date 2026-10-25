"""Narrow persistence boundary for patrol run coordination."""

from __future__ import annotations

from typing import Any


class PatrolRunRepository:
    """Expose only run/lease/fencing operations to orchestration code."""

    def __init__(self, backend: Any | None = None) -> None:
        if backend is None:
            from src.core.patrol.repository import PatrolRunRepositoryBackend

            backend = PatrolRunRepositoryBackend()
        self._backend = backend

    def create_run(self, **kwargs):
        return self._backend.create_run(**kwargs)

    def ensure_dataset(self, **kwargs):
        return self._backend.ensure_dataset(**kwargs)

    def touch_run(self, run_id: str, **kwargs):
        return self._backend.touch_run(run_id, **kwargs)

    def finish_run(self, run_id: str, **kwargs):
        return self._backend.finish_run(run_id, **kwargs)

    def prepare_redelivery_attempt(self, logical_run_id: str, **kwargs):
        return self._backend.prepare_redelivery_attempt(logical_run_id, **kwargs)

    def mark_celery_task_completed(self, run_id: str, **kwargs):
        return self._backend.mark_celery_task_completed(run_id, **kwargs)

    def reconcile_stale_runs(self, **kwargs):
        return self._backend.reconcile_stale_runs(**kwargs)

    def get_run(self, run_id: str):
        return self._backend.get_run(run_id)

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.core.workflows.patrol_batch_projection import (
    MaterializePatrolBatchReportHandler,
    patrol_batch_source_digest,
)


def _run(*, updated_at=None):
    return SimpleNamespace(
        id="run-1",
        status="completed",
        current_step_index=4,
        updated_at=updated_at or datetime(2026, 7, 12, tzinfo=timezone.utc),
        completed_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )


class MemoryProjectionRepository:
    def __init__(self):
        self.rows = {}
        self.object_keys = []

    def get_markdown(self, patrol_batch_id, *, source_digest):
        row = self.rows.get(patrol_batch_id)
        if row and row[0] == source_digest:
            return row[1]
        return None

    def upsert(self, **kwargs):
        self.object_keys.append(kwargs.get("object_key"))
        self.rows[kwargs["patrol_batch_id"]] = (
            kwargs["source_digest"],
            kwargs["markdown"],
        )


def test_materialized_batch_report_avoids_rebuilding_unchanged_facts():
    repository = MemoryProjectionRepository()
    builds = []
    handler = MaterializePatrolBatchReportHandler(
        summary_builder=lambda batch_id, runs: builds.append((batch_id, runs))
        or {"status": "completed", "run_count": len(runs)},
        renderer=lambda batch_id, summary, **_kwargs: f"{batch_id}:{summary['run_count']}",
        repository=repository,
        source_revision_provider=lambda _runs: "facts-v1",
    )

    first = handler.handle("batch-1", [_run()])
    second = handler.handle("batch-1", [_run()])

    assert first == second == "batch-1:1"
    assert len(builds) == 1


def test_batch_source_digest_changes_with_workflow_progress():
    first = patrol_batch_source_digest([_run()])
    second = patrol_batch_source_digest(
        [_run(updated_at=datetime(2026, 7, 12, 0, 1, tzinfo=timezone.utc))]
    )

    assert first != second


def test_batch_source_digest_changes_with_persisted_fact_revision():
    first = patrol_batch_source_digest([_run()], fact_revision="facts-v1")
    second = patrol_batch_source_digest([_run()], fact_revision="facts-v2")

    assert first != second


def test_materialized_report_uploads_stable_object_when_storage_is_available():
    repository = MemoryProjectionRepository()
    uploads = []

    class Storage:
        def upload_file(self, object_key, content, *, content_type):
            uploads.append((object_key, content, content_type))
            return True

    handler = MaterializePatrolBatchReportHandler(
        summary_builder=lambda _batch_id, runs: {
            "status": "completed",
            "run_count": len(runs),
        },
        renderer=lambda *_args, **_kwargs: "# report\n",
        repository=repository,
        object_storage=Storage(),
        source_revision_provider=lambda _runs: "facts-v1",
    )

    handler.handle("batch-1", [_run()])

    assert uploads[0][0].startswith("reports/patrol/batch-1/")
    assert uploads[0][0].endswith(".md")
    assert uploads[0][1] == b"# report\n"
    assert uploads[0][2] == "text/markdown; charset=utf-8"
    assert repository.object_keys == [uploads[0][0]]


def test_async_materializer_backfills_object_for_existing_database_cache():
    run = _run()
    digest = patrol_batch_source_digest([run], fact_revision="facts-v1")
    writes = []

    class ExistingProjectionRepository:
        def get(self, patrol_batch_id, *, source_digest):
            assert patrol_batch_id == "batch-1"
            assert source_digest == digest
            return {
                "markdown": "# cached\n",
                "summary": {"status": "completed", "run_count": 1},
                "object_key": None,
                "generated_at": datetime(2026, 7, 12, tzinfo=timezone.utc),
            }

        def upsert(self, **kwargs):
            writes.append(kwargs)

    class Storage:
        def upload_file(self, *_args, **_kwargs):
            return True

    handler = MaterializePatrolBatchReportHandler(
        summary_builder=lambda *_args: (_ for _ in ()).throw(
            AssertionError("existing summary must not be rebuilt")
        ),
        renderer=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("existing markdown must not be rendered again")
        ),
        repository=ExistingProjectionRepository(),
        object_storage=Storage(),
        source_revision_provider=lambda _runs: "facts-v1",
    )

    assert handler.handle("batch-1", [run]) == "# cached\n"
    assert writes[0]["object_key"].startswith("reports/patrol/batch-1/")


def test_materializer_fails_closed_when_object_upload_fails():
    repository = MemoryProjectionRepository()

    class Storage:
        def upload_file(self, *_args, **_kwargs):
            return False

    handler = MaterializePatrolBatchReportHandler(
        summary_builder=lambda _batch_id, runs: {
            "status": "completed",
            "run_count": len(runs),
        },
        renderer=lambda *_args, **_kwargs: "# report\n",
        repository=repository,
        object_storage=Storage(),
        source_revision_provider=lambda _runs: "facts-v1",
    )

    with pytest.raises(
        RuntimeError,
        match="failed to upload patrol batch report artifact",
    ):
        handler.handle("batch-1", [_run()])

    assert repository.rows == {}


def test_materializer_propagates_projection_write_failure():
    deleted: list[str] = []

    class Repository:
        def get(self, *_args, **_kwargs):
            return None

        def upsert(self, **_kwargs):
            raise RuntimeError("projection unavailable")

    class Storage:
        def upload_file(self, *_args, **_kwargs):
            return True

        def delete_object(self, object_key):
            deleted.append(object_key)
            return True

    handler = MaterializePatrolBatchReportHandler(
        summary_builder=lambda _batch_id, runs: {
            "status": "completed",
            "run_count": len(runs),
        },
        renderer=lambda *_args, **_kwargs: "# report\n",
        repository=Repository(),
        object_storage=Storage(),
        source_revision_provider=lambda _runs: "facts-v1",
    )

    with pytest.raises(RuntimeError, match="projection unavailable"):
        handler.handle("batch-1", [_run()])
    assert deleted[0].startswith("reports/patrol/batch-1/")

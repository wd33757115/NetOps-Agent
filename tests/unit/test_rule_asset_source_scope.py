from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

import pytest

from src.core.parser.repository import ParserJobRepository, ParserJobStateError
from src.core.parser_generation import normalize_parser_spec_payload, parser_spec_digest
from src.core.rule_control.source_scope import (
    RuleAssetSourceScopeError,
    require_immutable_rule_asset_source_datasets,
)
from src.infrastructure.db.models import (
    CommandSnapshot,
    FileMetadata,
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    OfflinePatrolSourceFileRecord,
    ParserJobSample,
    ParserTemplateJob,
    PatrolDataset,
    PatrolRun,
)
from src.infrastructure.db.postgres import get_db_session


def _ids() -> dict[str, str]:
    suffix = uuid.uuid4().hex
    return {
        "suffix": suffix,
        "offline_job": f"offline-source-scope-{suffix}",
        "period": f"period-source-scope-{suffix}",
        "dataset": f"dataset-source-scope-{suffix}",
        "run": f"run-source-scope-{suffix}",
        "file": f"file-source-scope-{suffix}",
        "source": f"source-scope-{suffix}",
        "snapshot": f"snapshot-source-scope-{suffix}",
        "parser_job": f"parser-job-source-scope-{suffix}",
        "sample": f"parser-sample-source-scope-{suffix}",
    }


def _insert_scope(ids: dict[str, str], *, purpose: str = "rule_asset_source") -> None:
    observed_at = datetime.now(timezone.utc)
    parser_spec = normalize_parser_spec_payload(
        {
            "entity": "interface",
            "primary_keys": ["interface"],
            "fields": {
                "interface": {
                    "required": True,
                    "type": "text",
                    "source_labels": ["Interface"],
                }
            },
            "allow_empty": False,
            "empty_patterns": [],
        }
    )
    with get_db_session() as db:
        db.add(
            OfflinePatrolJobRecord(
                id=ids["offline_job"],
                idempotency_key=f"scope-idempotency-{ids['suffix']}",
                purpose=purpose,
                job_type="parser_validation",
                name="source scope test",
                owner_id="unit-test",
                status="completed",
            )
        )
        db.add(
            FileMetadata(
                id=ids["file"],
                name="capture.txt",
                object_key=f"tests/source-scope/{ids['suffix']}/capture.txt",
                owner_id="unit-test",
                visibility="private",
                size_bytes=64,
                status="active",
                is_deleted=False,
            )
        )
        db.add(
            PatrolDataset(
                id=ids["dataset"],
                name=ids["dataset"],
                mode="parser_validation",
                source="offline_import",
                status="active",
            )
        )
        db.flush()
        db.add(
            PatrolRun(
                id=ids["run"],
                logical_run_id=ids["run"],
                mode="parser_validation",
                source="offline_import",
                dataset_id=ids["dataset"],
                status="completed",
                collection_status="completed",
                run_purpose="parser_validation",
                metadata_json={
                    "offline_job_id": ids["offline_job"],
                    "period_role": "sample",
                },
            )
        )
        db.flush()
        db.add(
            OfflinePatrolPeriodRecord(
                id=ids["period"],
                job_id=ids["offline_job"],
                role="sample",
                label="sample",
                observed_at=observed_at,
                dataset_id=ids["dataset"],
                patrol_run_id=ids["run"],
                status="completed",
            )
        )
        db.add(
            OfflinePatrolSourceFileRecord(
                id=ids["source"],
                job_id=ids["offline_job"],
                period_role="sample",
                file_id=ids["file"],
                relative_path="capture.txt",
                sha256=hashlib.sha256(ids["suffix"].encode()).hexdigest(),
                size_bytes=64,
                status="verified",
            )
        )
        db.add(
            CommandSnapshot(
                id=ids["snapshot"],
                run_id=ids["run"],
                dataset_id=ids["dataset"],
                device_id="device-1",
                execution_item_id=f"execution-{ids['suffix']}",
                device_name="SW1",
                command="display interface brief",
                canonical_command="display interface brief",
                observed_at=observed_at,
                raw_text="Interface Link\nGE1/0/1 UP\n",
                raw_text_hash=hashlib.sha256(b"Interface Link\nGE1/0/1 UP\n").hexdigest(),
                parse_status="template_missing",
            )
        )
        db.add(
            ParserTemplateJob(
                id=ids["parser_job"],
                job_type="CREATE_TEMPLATE",
                source_mode="parser_validation",
                vendor="H3C",
                model="S5130S",
                profile="S5130S",
                canonical_command="display interface brief",
                output_signature="scope-signature",
                idempotency_key=f"scope-parser-asset-{ids['suffix']}",
                status="collecting",
                pipeline_version="simple_v2",
                parser_spec_status="confirmed",
                parser_spec_json=parser_spec,
                parser_spec_sha256=parser_spec_digest(parser_spec),
                sample_count=1,
            )
        )
        db.add(
            ParserJobSample(
                id=ids["sample"],
                job_id=ids["parser_job"],
                snapshot_id=ids["snapshot"],
                snapshot_source="postgresql",
                device_id="device-1",
                output_signature="scope-signature",
                dataset_id=ids["dataset"],
                sample_role="generation",
                sanitization_status="sanitized",
                sanitization_version="test-v1",
                sanitized_text="Interface Link\nGE1/0/1 UP\n",
                sanitized_hash=hashlib.sha256(
                    b"Interface Link\nGE1/0/1 UP\n"
                ).hexdigest(),
            )
        )


def _cleanup(ids: dict[str, str]) -> None:
    with get_db_session() as db:
        db.query(ParserJobSample).filter(
            ParserJobSample.job_id == ids["parser_job"]
        ).delete(synchronize_session=False)
        db.query(ParserTemplateJob).filter(
            ParserTemplateJob.id == ids["parser_job"]
        ).delete(synchronize_session=False)
        db.query(CommandSnapshot).filter(
            CommandSnapshot.id == ids["snapshot"]
        ).delete(synchronize_session=False)
        db.query(OfflinePatrolSourceFileRecord).filter(
            OfflinePatrolSourceFileRecord.id == ids["source"]
        ).delete(synchronize_session=False)
        db.query(OfflinePatrolPeriodRecord).filter(
            OfflinePatrolPeriodRecord.id == ids["period"]
        ).delete(synchronize_session=False)
        db.query(PatrolRun).filter(PatrolRun.id == ids["run"]).delete(
            synchronize_session=False
        )
        db.query(PatrolDataset).filter(PatrolDataset.id == ids["dataset"]).delete(
            synchronize_session=False
        )
        db.query(FileMetadata).filter(FileMetadata.id == ids["file"]).delete(
            synchronize_session=False
        )
        db.query(OfflinePatrolJobRecord).filter(
            OfflinePatrolJobRecord.id == ids["offline_job"]
        ).delete(synchronize_session=False)


def test_completed_verified_scope_is_dispatchable_and_digest_is_stable(monkeypatch) -> None:
    _confirmed_semantics(monkeypatch)
    ids = _ids()
    _insert_scope(ids)
    try:
        with get_db_session() as db:
            first = require_immutable_rule_asset_source_datasets(
                db,
                [ids["dataset"]],
                snapshot_ids=[ids["snapshot"]],
            )
            second = require_immutable_rule_asset_source_datasets(
                db,
                [ids["dataset"]],
                snapshot_ids=[ids["snapshot"]],
            )
        assert first["scope_digest"] == second["scope_digest"]

        repository = ParserJobRepository()
        job = repository.get_job(ids["parser_job"])
        assert job is not None
        assert job["generation_readiness"]["ready"] is True
        assert job["generation_readiness"]["source_scope_digest"] == first["scope_digest"]

        dispatched = repository.mark_job_dispatched(
            ids["parser_job"],
            f"task-{ids['suffix']}",
        )
        assert dispatched["status"] == "pending"
    finally:
        _cleanup(ids)


def test_source_mutation_between_readiness_and_dispatch_fails_closed(monkeypatch) -> None:
    _confirmed_semantics(monkeypatch)
    ids = _ids()
    _insert_scope(ids)
    try:
        repository = ParserJobRepository()
        assert repository.get_job(ids["parser_job"])["generation_readiness"]["ready"]
        with get_db_session() as db:
            source = db.get(OfflinePatrolSourceFileRecord, ids["source"])
            source.status = "attached"

        with pytest.raises(
            ParserJobStateError,
            match="rule_asset_source_source_file_unverified",
        ):
            repository.mark_job_dispatched(
                ids["parser_job"],
                f"task-{ids['suffix']}",
            )
    finally:
        _cleanup(ids)


def test_non_asset_source_dataset_is_rejected_even_when_sample_is_sanitized() -> None:
    ids = _ids()
    _insert_scope(ids, purpose="offline_patrol")
    try:
        with get_db_session() as db:
            with pytest.raises(
                RuleAssetSourceScopeError,
                match="Parser control asset-source jobs",
            ):
                require_immutable_rule_asset_source_datasets(
                    db,
                    [ids["dataset"]],
                    snapshot_ids=[ids["snapshot"]],
                )
        readiness = ParserJobRepository().get_job(ids["parser_job"])[
            "generation_readiness"
        ]
        assert readiness["ready"] is False
        assert "rule_asset_source_source_job_wrong_purpose" in readiness["blockers"]
    finally:
        _cleanup(ids)


def _confirmed_semantics(monkeypatch):
    # These tests isolate immutable source provenance from the separately tested association gate.
    from src.apps.rule_governance.backend.parser import repository
    monkeypatch.setattr(repository, "require_parser_check_item", lambda *args, **kwargs: None)

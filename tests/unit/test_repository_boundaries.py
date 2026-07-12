from pathlib import Path

from src.core.parser.outbox_repository import ParserOutboxRepository
from src.core.parser.repository import (
    ParserGovernanceProjectionRepository,
    ParserJobRepository,
    ParserMetadataRepository,
    ParserParseResultRepository,
    ParserSampleRepository,
    ParserTemplateRepository,
)
from src.core.parser.runtime_repository import ParserRuntimeRepository
from src.core.patrol.repository import (
    PatrolAnalysisAttemptRepository,
    PatrolCollectionResultRepository,
    PatrolRepository,
    PatrolRunRepositoryBackend,
    PatrolSnapshotRepository,
)
from src.core.patrol.run_repository import PatrolRunRepository


def test_runtime_repositories_expose_narrow_capabilities():
    assert not hasattr(ParserRuntimeRepository, "create_candidate")
    assert not hasattr(ParserRuntimeRepository, "activate_template")
    assert not hasattr(ParserRuntimeRepository, "claim_pending_outbox")
    assert hasattr(ParserOutboxRepository, "claim_pending")
    assert hasattr(ParserOutboxRepository, "mark_published")
    assert not hasattr(ParserOutboxRepository, "resolve_active")
    assert not hasattr(ParserOutboxRepository, "create_candidate")
    assert hasattr(ParserTemplateRepository, "activate_template")
    assert not hasattr(ParserTemplateRepository, "create_candidate")
    assert not hasattr(ParserTemplateRepository, "sanitize_pending_samples")
    assert hasattr(ParserJobRepository, "create_candidate")
    assert not hasattr(ParserJobRepository, "activate_template")
    assert not hasattr(ParserJobRepository, "sanitize_pending_samples")
    assert hasattr(ParserSampleRepository, "sanitize_pending_samples")
    assert not hasattr(ParserSampleRepository, "create_candidate")
    assert hasattr(ParserParseResultRepository, "save_parse_result")
    assert not hasattr(ParserParseResultRepository, "activate_template")
    assert hasattr(ParserMetadataRepository, "upsert_device_profile")
    assert not hasattr(ParserMetadataRepository, "activate_template")
    assert hasattr(ParserGovernanceProjectionRepository, "governance_alert_summary")
    assert not hasattr(ParserGovernanceProjectionRepository, "create_candidate")
    assert not hasattr(PatrolRunRepository, "save_snapshot_idempotent")
    assert not hasattr(PatrolRunRepository, "save_collection_result")


def test_patrol_repository_aggregates_have_physical_capability_boundaries():
    assert hasattr(PatrolRunRepositoryBackend, "prepare_redelivery_attempt")
    assert not hasattr(PatrolRunRepositoryBackend, "save_collection_result")
    assert not hasattr(PatrolRunRepositoryBackend, "save_snapshot_idempotent")

    assert hasattr(PatrolAnalysisAttemptRepository, "begin_analysis_attempt")
    assert not hasattr(PatrolAnalysisAttemptRepository, "create_run")
    assert not hasattr(PatrolAnalysisAttemptRepository, "save_collection_result")

    assert hasattr(PatrolCollectionResultRepository, "save_collection_result")
    assert not hasattr(PatrolCollectionResultRepository, "begin_analysis_attempt")
    assert not hasattr(PatrolCollectionResultRepository, "save_snapshot_idempotent")

    assert hasattr(PatrolSnapshotRepository, "save_snapshot_idempotent")
    assert not hasattr(PatrolSnapshotRepository, "finish_run")
    assert not hasattr(PatrolSnapshotRepository, "begin_analysis_attempt")

    assert hasattr(PatrolRepository, "finish_run")
    assert hasattr(PatrolRepository, "begin_analysis_attempt")
    assert hasattr(PatrolRepository, "save_collection_result")
    assert hasattr(PatrolRepository, "save_snapshot_idempotent")


def test_runtime_adapters_do_not_import_monolithic_repositories():
    sources = {
        "coordinator": Path("src/core/patrol/run_coordinator.py").read_text(
            encoding="utf-8"
        ),
        "celery": Path("src/core/celery_tasks/tasks.py").read_text(encoding="utf-8"),
        "parser_runtime": Path("src/core/parser/runtime.py").read_text(encoding="utf-8"),
        "parser_identity": Path("src/core/parser/identity.py").read_text(encoding="utf-8"),
        "patrol_tasks": Path("src/core/patrol/tasks.py").read_text(encoding="utf-8"),
    }

    assert "src.core.patrol.repository" not in sources["coordinator"]
    assert "src.core.patrol.repository" not in sources["celery"]
    assert "from .repository import ParserRepository" not in sources["parser_runtime"]
    assert "from .repository import ParserRepository" not in sources["parser_identity"]
    assert "PatrolRepository" not in sources["patrol_tasks"]


def test_production_code_does_not_instantiate_parser_compatibility_facade():
    offenders = []
    for path in Path("src").rglob("*.py"):
        if path.as_posix() == "src/core/parser/repository.py":
            continue
        if "ParserRepository()" in path.read_text(encoding="utf-8"):
            offenders.append(path.as_posix())

    assert offenders == []

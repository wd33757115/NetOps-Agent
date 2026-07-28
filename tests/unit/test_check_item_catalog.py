from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.check_items import (
    CatalogConflictError,
    CheckItemCatalog,
    CheckItemCatalogRepository,
    CheckItemImplementationResolver,
    load_canonical_catalog,
    load_catalog,
    load_entity_identity_policy,
    normalize_entity_identity,
    validate_check_item_contract,
)
from src.infrastructure.db.models import (
    Base,
    CheckAssessmentRecord,
    CheckExecutionRecord,
    CheckItemImplementationLinkRecord,
    CheckItemRecord,
    CheckItemVersionRecord,
    CollectionImplementationRecord,
    CollectionImplementationVersionRecord,
    ComparisonContextRecord,
)


@contextmanager
def _session_scope(session_factory):
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _repository(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'check-items.db'}")
    Base.metadata.create_all(
        engine,
        tables=[
            CheckItemRecord.__table__,
            CheckItemVersionRecord.__table__,
            CollectionImplementationRecord.__table__,
            CollectionImplementationVersionRecord.__table__,
            CheckItemImplementationLinkRecord.__table__,
            ComparisonContextRecord.__table__,
            CheckExecutionRecord.__table__,
            CheckAssessmentRecord.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine)
    return (
        CheckItemCatalogRepository(
            session_scope=lambda: _session_scope(session_factory)
        ),
        session_factory,
    )


def test_top_catalog_is_check_item_centered_and_complete():
    catalog = load_catalog()

    assert catalog.catalog_version == 3
    assert {item.id for item in catalog.check_items} == {
        "device.version",
        "system.cpu_usage",
        "system.memory_usage",
        "interface.oper_status",
        "interface.optical_power",
        "environment.fan",
        "environment.power",
        "configuration.running",
    }
    optical = catalog.check_item("interface.optical_power")
    assert optical is not None
    assert {
        item["observation_type"]
        for item in optical.canonical_contract["observations"]
    } >= {
        "interface.optical.rx_power",
        "interface.optical.tx_power",
    }
    configuration = catalog.check_item("configuration.running")
    assert configuration is not None
    assert configuration.result_mode == "normalized_diff"
    assert configuration.assessment_mode == "change"
    assert all(
        item.freshness_policy
        and item.empty_result_policy
        and item.coverage_policy
        for item in catalog.check_items
        if item.assessment_mode == "health"
    )
    assert all(
        implementation.output_quality_policy.get(
            "profile_version"
        ) == 1
        for implementation in catalog.implementations
        if set(implementation.check_item_ids)
        & {
            item.id
            for item in catalog.check_items
            if item.assessment_mode == "health"
        }
    )


def test_top_catalog_uses_only_approved_canonical_observations():
    catalog = load_catalog()
    canonical = load_canonical_catalog()

    assert canonical.status == "active"
    assert all(
        validate_check_item_contract(item, catalog=canonical) == ()
        for item in catalog.check_items
    )
    cpu = canonical.get("device.cpu.utilization")
    assert cpu is not None
    assert cpu.canonical_unit == "percent"
    assert cpu.dimensions == ("window",)


def test_entity_identity_policy_normalizes_aliases_without_vendor_branching():
    policy = load_entity_identity_policy()

    short = normalize_entity_identity(
        entity_type="interface",
        identity_values={"interface": "Gi 1/0/01", "channel": "01"},
        primary_keys=["interface", "channel"],
        policy=policy,
    )
    long = normalize_entity_identity(
        entity_type="interface",
        identity_values={"interface": "GigabitEthernet1/0/01", "channel": 1},
        primary_keys=["interface", "channel"],
        policy=policy,
    )

    assert short.entity_key == long.entity_key
    assert short.values == {
        "interface": "GigabitEthernet1/0/01",
        "channel": "1",
    }
    assert short.policy_version == 1
    assert len(short.policy_digest) == 64


def test_resolver_uses_vendor_family_and_does_not_guess_missing_identity():
    resolver = CheckItemImplementationResolver(load_catalog())

    h3c = resolver.resolve(
        device={"device_id": "h3c-1", "vendor": "H3C", "family": "Comware7"},
        check_item_id="system.cpu_usage",
    )
    assert h3c.status == "resolved"
    assert h3c.implementation_id == "h3c-cpu-usage"

    unknown_cisco_family = resolver.resolve(
        device={"device_id": "cisco-1", "vendor": "Cisco"},
        check_item_id="system.cpu_usage",
    )
    assert unknown_cisco_family.status == "unsupported"
    assert "identity_missing:family" in unknown_cisco_family.reason_codes


def test_resolver_reports_ambiguity_instead_of_choosing_by_id():
    catalog = load_catalog()
    source = next(
        item for item in catalog.implementations if item.id == "h3c-cpu-usage"
    )
    duplicate = replace(
        source,
        id="h3c-cpu-usage-alternative",
        version_id="h3c-cpu-usage-alternative-v1",
        digest="f" * 64,
    )
    ambiguous_catalog = CheckItemCatalog(
        catalog_version=catalog.catalog_version,
        check_items=catalog.check_items,
        implementations=(*catalog.implementations, duplicate),
    )

    decision = CheckItemImplementationResolver(ambiguous_catalog).resolve(
        device={"device_id": "h3c-1", "vendor": "H3C"},
        check_item_id="system.cpu_usage",
    )

    assert decision.status == "ambiguous"
    assert decision.implementation_id is None
    assert decision.candidate_ids == (
        "h3c-cpu-usage",
        "h3c-cpu-usage-alternative",
    )


def test_shadow_plan_collects_shared_nxos_command_once():
    resolver = CheckItemImplementationResolver(load_catalog())

    plan = resolver.build_shadow_plan(
        device={
            "device_id": "n9k-1",
            "vendor": "Cisco",
            "family": "NXOS",
            "model": "N9K-C93180YC-FX",
        },
        check_item_ids=["system.cpu_usage", "system.memory_usage"],
    )

    assert [item.status for item in plan.decisions] == ["resolved", "resolved"]
    assert len(plan.collection_actions) == 1
    action = plan.collection_actions[0]
    assert action["command"] == "show system resources"
    assert action["check_item_ids"] == ["system.cpu_usage", "system.memory_usage"]
    assert len(plan.digest) == 64


def test_catalog_bootstrap_is_idempotent_and_versions_are_immutable(tmp_path):
    repository, _session_factory = _repository(tmp_path)
    catalog = load_catalog()

    first = repository.bootstrap(catalog)
    second = repository.bootstrap(catalog)

    assert first["created_check_items"] == 8
    assert first["created_implementations"] == len(catalog.implementations)
    assert first["created_links"] > len(catalog.implementations)
    assert second == {
        "created_check_items": 0,
        "created_implementations": 0,
        "created_links": 0,
    }
    active = repository.load_active()
    assert len(active.check_items) == 8
    assert len(active.implementations) == len(catalog.implementations)

    changed_item = replace(catalog.check_items[0], digest="0" * 64)
    changed_catalog = CheckItemCatalog(
        catalog_version=1,
        check_items=(changed_item, *catalog.check_items[1:]),
        implementations=catalog.implementations,
    )
    with pytest.raises(CatalogConflictError, match="changed in place"):
        repository.bootstrap(changed_catalog)


def test_latest_catalog_transactionally_replaces_v1_without_mutating_history(
    tmp_path,
):
    repository, session_factory = _repository(tmp_path)
    v1_path = (
        Path(__file__).parents[2]
        / "src"
        / "core"
        / "check_items"
        / "assets"
        / "catalog.v1.yaml"
    )
    legacy = load_catalog(v1_path)
    current = load_catalog()

    repository.bootstrap(legacy, actor="test:v1")
    repository.bootstrap(current, actor="test:latest")
    active = repository.load_active()

    assert active.catalog_version == 3
    assert {item.version for item in active.check_items} == {2, 3}
    assert {
        implementation.version
        for implementation in active.implementations
    } == {2}
    assert all(
        item.freshness_policy and item.coverage_policy
        for item in active.check_items
        if item.assessment_mode == "health"
    )

    with _session_scope(session_factory) as db:
        legacy_items = (
            db.query(CheckItemVersionRecord)
            .filter(CheckItemVersionRecord.version == 1)
            .all()
        )
        legacy_implementations = (
            db.query(CollectionImplementationVersionRecord)
            .filter(CollectionImplementationVersionRecord.version == 1)
            .all()
        )
        assert legacy_items
        assert legacy_implementations
        assert {item.status for item in legacy_items} == {"deprecated"}
        assert {
            item.status for item in legacy_implementations
        } == {"deprecated"}
        assert all(item.deprecated_at is not None for item in legacy_items)

    with pytest.raises(CatalogConflictError, match="cannot be reactivated"):
        repository.bootstrap(legacy, actor="test:downgrade")


def test_execution_completion_is_distinct_from_health_assessment(tmp_path):
    repository, session_factory = _repository(tmp_path)
    repository.bootstrap(load_catalog())
    item = repository.load_active().check_item("system.cpu_usage")
    assert item is not None
    now = datetime.now(timezone.utc)

    with _session_scope(session_factory) as db:
        context = ComparisonContextRecord(
            id="context-1",
            digest="1" * 64,
            mode="no_baseline",
            device_id="device-1",
            check_item_version_id=item.version_id,
            selection_reason="first_observation",
            created_at=now,
        )
        execution = CheckExecutionRecord(
            id="execution-1",
            idempotency_key="batch-1|device-1|system.cpu_usage",
            patrol_batch_id="batch-1",
            device_id="device-1",
            check_item_version_id=item.version_id,
            collection_action_ids_json=["action-1"],
            comparison_context_id=context.id,
            execution_status="completed",
            reason_codes_json=[],
            target_manifest_digest="2" * 64,
            evidence_refs_json=["snapshot-1"],
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        assessment = CheckAssessmentRecord(
            id="assessment-1",
            idempotency_key="assessment|execution-1",
            patrol_batch_id="batch-1",
            device_id="device-1",
            check_item_version_id=item.version_id,
            execution_id=execution.id,
            assessment_status="unable_to_determine",
            completeness_status="insufficient",
            comparison_status="first_observation",
            observed_entity_count=0,
            evaluated_entity_count=0,
            threshold_unknown_count=1,
            current_finding_ids_json=[],
            risk_case_ids_json=[],
            blocking_reason_codes_json=["threshold_unknown"],
            evidence_digest="3" * 64,
            assessed_at=now,
            created_at=now,
        )
        execution.assessment_id = assessment.id
        db.add_all([context, execution, assessment])

    with _session_scope(session_factory) as db:
        execution = db.get(CheckExecutionRecord, "execution-1")
        assessment = db.get(CheckAssessmentRecord, "assessment-1")
        assert execution.execution_status == "completed"
        assert assessment.assessment_status == "unable_to_determine"
        assert assessment.blocking_reason_codes_json == ["threshold_unknown"]

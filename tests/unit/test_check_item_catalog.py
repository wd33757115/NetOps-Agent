from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.check_items import (
    CatalogConflictError,
    CheckItemCatalog,
    CheckItemCatalogRepository,
    CheckItemImplementationResolver,
    build_collection_implementation,
    load_canonical_catalog,
    load_catalog,
    load_entity_identity_policy,
    normalize_entity_identity,
    validate_check_item_contract,
)
from src.core.check_items.parser_capability import (
    build_parser_field_naming_context,
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


def _catalog_with_h3c_fixtures():
    catalog = load_catalog()
    fixtures = []
    for ident, check, command in (
        ("h3c-cpu-usage", "system.cpu_usage", "display cpu"),
        ("test-h3c-interface", "interface.runtime_statistics", "display interface"),
        ("test-h3c-optical", "interface.optical_power", "display transceiver diagnosis interface"),
    ):
        fixtures.append(build_collection_implementation({
            "id": ident, "version": 1, "name": ident,
            "check_item_ids": [check], "match": {"vendor": "H3C"},
            "commands": [{"command": command, "priority": 100}],
            "collector": "cli", "parser_requirement": "structured",
            "template_scope": "model", "selection_priority": 100, "status": "active",
        }, known_check_item_ids={c.id for c in catalog.check_items}))
    return replace(catalog, implementations=(*catalog.implementations, *fixtures))


def _catalog_with_family_scoped_shared_command():
    catalog = _catalog_with_h3c_fixtures()
    implementation = build_collection_implementation({
        "id": "test-shared-resources", "version": 1,
        "name": "Shared resources fixture",
        "check_item_ids": ["system.cpu_usage", "system.memory_usage"],
        "match": {"vendor": "Cisco", "families": ["NXOS"]},
        "commands": [{"command": "show system resources", "priority": 100}],
        "collector": "cli", "parser_requirement": "structured",
        "template_scope": "family", "selection_priority": 100, "status": "active",
    }, known_check_item_ids={c.id for c in catalog.check_items})
    return replace(catalog, implementations=(*catalog.implementations, implementation))


def test_production_catalog_is_check_item_centered_and_complete():
    catalog = load_catalog()

    assert catalog.catalog_version == 12
    assert {item.id for item in catalog.check_items} == {
        "device.version",
        "device.inventory",
        "system.cpu_usage",
        "system.memory_usage",
        "interface.runtime_statistics",
        "interface.optical_power",
        "interface.ip_assignment",
        "environment.fan",
        "environment.power",
        "environment.temperature",
        "redundancy.vrrp",
        "redundancy.stack",
        "redundancy.mlag",
        "network.vrf_inventory",
        "network.arp_table",
        "network.mac_table",
        "configuration.running",
        "configuration.startup",
        "operations.critical_logs",
    }
    assert len(catalog.check_items) == 19
    optical = catalog.check_item("interface.optical_power")
    assert optical is not None
    assert {
        item["observation_type"]
        for item in optical.canonical_contract["observations"]
    } >= {
        "interface.optical.rx_power",
        "interface.optical.tx_power",
    }
    optical_targets = {
        item["observation_type"]: item
        for item in optical.canonical_contract["observations"]
    }
    assert optical_targets["interface.optical.rx_power"][
        "event_constraints"
    ]["evidence_bound_roles"] is True
    assert optical_targets["interface.optical.tx_power"][
        "event_constraints"
    ]["evidence_bound_roles"] is True
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


def test_parser_field_naming_guidance_comes_from_resolved_check_item_contract():
    context = build_parser_field_naming_context(
        vendor="H3C",
        model="S6850-56HF",
        family="Comware7",
        profile="S6850-56HF",
        command="display interface",
        catalog=_catalog_with_h3c_fixtures(),
    )

    assert context["resolved"] is True
    interface = next(
        item
        for item in context["check_items"]
        if item["check_item_id"] == "interface.runtime_statistics"
    )
    assert interface["preferred_identity_fields"] == ["interface"]
    fields = {
        item["observation_type"]: item
        for item in interface["preferred_observation_fields"]
    }
    assert fields["interface.runtime_statistics.line_status"]["preferred_name"] == "line_status"
    assert fields["interface.runtime_statistics.input_errors"]["preferred_name"] == "input_errors"
    assert context["strict_value_names"] is True

    optical_context = build_parser_field_naming_context(
        vendor="H3C",
        model="S6850-56HF",
        family="Comware7",
        profile="S6850-56HF",
        command="display transceiver diagnosis interface",
        catalog=_catalog_with_h3c_fixtures(),
    )
    optical = next(
        item
        for item in optical_context["check_items"]
        if item["check_item_id"] == "interface.optical_power"
    )
    optical_fields = {
        item["observation_type"]: item
        for item in optical["preferred_observation_fields"]
    }
    assert optical_fields["interface.optical.rx_power"]["preferred_pattern"] == (
        "rx_power_dbm_{channel}"
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
    cpu_check = catalog.check_item("system.cpu_usage")
    assert cpu_check is not None
    cpu_target = next(r for r in cpu_check.canonical_contract["observations"]
                      if r["canonical_field"] == "cpu_usage_percent")
    assert cpu_check.version == 12
    assert cpu_target["canonical_field"] == "cpu_usage_percent"
    assert cpu_target["fixed_dimensions"] == {"window": "5m"}
    assert "projection_recipes" not in cpu_target
    assert cpu_check.canonical_contract["value_contract"] == "same-name/v1"
    assert "required_thresholds" not in cpu_target["policy_constraints"]
    assert cpu_target["threshold_references"] == {
        "high": {"field_name": "cpu_high_warning_percent"},
        "critical_high": {"field_name": "cpu_high_alarm_percent"},
    }
    assert cpu_target["event_constraints"]["role_event_types"] == {
        "high": "CpuUtilizationHigh",
        "critical_high": "CpuUtilizationCritical",
        "high_recovery": "CpuUtilizationRecovered",
    }
    memory_check = catalog.check_item("system.memory_usage")
    assert memory_check is not None
    assert memory_check.version == 11
    memory_utilization = next(
        item
        for item in memory_check.canonical_contract["observations"]
        if item["observation_type"] == "device.memory.utilization"
    )
    assert "projection_recipes" not in memory_utilization
    assert memory_utilization["fixed_dimensions"] == {"window": "5m"}
    assert memory_utilization["required"] is False
    interface_status = canonical.get("interface.runtime_statistics.line_status")
    assert interface_status is not None
    assert interface_status.semantic_type == "state"
    assert canonical.get("interface.oper_status") is None
    interface = catalog.check_item("interface.runtime_statistics")
    assert interface is not None
    requirements = {
        item["observation_type"]: item.get("required", True)
        for item in interface.canonical_contract["observations"]
    }
    assert len(requirements) == 23
    assert {name for name, required in requirements.items() if required} == {
        "interface.runtime_statistics.line_status",
        "interface.runtime_statistics.protocol_status",
    }
    oper_target = next(
        item
        for item in interface.canonical_contract["observations"]
        if item["observation_type"] == "interface.runtime_statistics.line_status"
    )
    assert oper_target["event_constraints"]["role_event_types"] == {
        "change": "interface.runtime_statistics.line_status.changed",
    }
    optical = catalog.check_item("interface.optical_power")
    assert optical is not None
    rx_target = next(
        item
        for item in optical.canonical_contract["observations"]
        if item["observation_type"] == "interface.optical.rx_power"
    )
    assert rx_target["policy_constraints"]["allowed_compare_modes"] == [
        "threshold_and_deadband"
    ]
    assert set(rx_target["threshold_references"]) == {
        "low", "critical_low", "high", "critical_high"
    }
    assert rx_target["baseline_comparison"] == {
        "kind": "linear_power_loss_ratio",
        "max_loss_ratio": 0.1,
        "identity_fields": ["part_number", "serial_number"],
    }
    assert rx_target["event_constraints"]["role_event_types"][
        "baseline_degradation"
    ] == "OpticalRxPowerDegraded"
    assert rx_target["event_constraints"]["required_exclusive_event_types"] == [
        "OpticalRxPowerCritical"
    ]
    assert rx_target["event_constraints"]["priority_order"] == [
        {
            "higher": "OpticalRxPowerCritical",
            "lower": "OpticalRxPowerLow",
        }
    ]
    assert rx_target["event_constraints"][
        "required_recovery_pairs"
    ] == [
        {
            "fault": "OpticalRxPowerLow",
            "recovery": "OpticalRxPowerRecovered",
        },
        {
            "fault": "OpticalRxPowerCritical",
            "recovery": "OpticalRxPowerRecovered",
        },
        {
            "fault": "OpticalRxPowerHigh",
            "recovery": "OpticalRxPowerRecoveredFromHigh",
        },
        {
            "fault": "OpticalRxPowerHighCritical",
            "recovery": "OpticalRxPowerRecoveredFromHigh",
        },
    ]


def test_catalog_contract_rejects_incomplete_event_semantics():
    catalog = load_catalog()
    canonical = load_canonical_catalog()
    cpu_check = catalog.check_item("system.cpu_usage")
    assert cpu_check is not None
    contract = copy.deepcopy(cpu_check.canonical_contract)
    constraints = next(r["event_constraints"] for r in contract["observations"]
                       if r["canonical_field"] == "cpu_usage_percent")
    constraints["required_severities"].pop(
        "CpuUtilizationRecovered"
    )
    constraints["required_recovery_pairs"] = []

    errors = validate_check_item_contract(
        replace(cpu_check, canonical_contract=contract),
        catalog=canonical,
    )

    assert any(
        "required_severities must cover exactly" in error
        for error in errors
    )
    assert any(
        "recovery events without fault pairs" in error
        for error in errors
    )


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
    resolver = CheckItemImplementationResolver(_catalog_with_family_scoped_shared_command())

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
    catalog = _catalog_with_h3c_fixtures()
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
    resolver = CheckItemImplementationResolver(_catalog_with_family_scoped_shared_command())

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
    catalog = _catalog_with_family_scoped_shared_command()

    first = repository.bootstrap(catalog)
    second = repository.bootstrap(catalog)

    assert first["created_check_items"] == 19
    assert first["created_implementations"] == len(catalog.implementations)
    assert first["created_links"] > len(catalog.implementations)
    assert second == {
        "created_check_items": 0,
        "created_implementations": 0,
        "created_links": 0,
    }
    active = repository.load_active()
    assert len(active.check_items) == 19
    assert len(active.implementations) == len(catalog.implementations)

    changed_item = replace(catalog.check_items[0], digest="0" * 64)
    changed_catalog = CheckItemCatalog(
        catalog_version=1,
        check_items=(changed_item, *catalog.check_items[1:]),
        implementations=catalog.implementations,
    )
    with pytest.raises(CatalogConflictError, match="changed in place"):
        repository.bootstrap(changed_catalog)


def test_empty_command_adapter_keeps_legacy_implementation_digest_stable():
    raw = {
        "id": "test-device-version",
        "version": 1,
        "name": "Test device version",
        "check_item_ids": ["device.version"],
        "match": {"vendor": "Test"},
        "commands": [{"command": "show version", "priority": 100}],
        "collector": "cli",
        "parser_requirement": "structured",
        "template_scope": "family",
        "selection_priority": 100,
        "status": "active",
    }
    without_adapter = build_collection_implementation(
        raw,
        known_check_item_ids={"device.version"},
    )
    with_empty_adapter = build_collection_implementation(
        {**raw, "adapter_spec": {}},
        known_check_item_ids={"device.version"},
    )

    assert with_empty_adapter.digest == without_adapter.digest


def test_current_seed_does_not_recreate_removed_interface_entries(tmp_path):
    repository, session_factory = _repository(tmp_path)
    current = load_catalog()
    repository.bootstrap(current, actor="test:current")
    repository.bootstrap(current, actor="test:restart")
    active = repository.load_active()
    assert {item.id for item in active.check_items} == {item.id for item in current.check_items}
    assert active.check_item("interface.runtime_statistics") is not None
    assert active.check_item("interface.oper_status") is None
    assert active.check_item("interface.statistics") is None
    assert not {
        "cisco-device-version", "cisco-fan-health", "cisco-ios-cpu-usage",
        "cisco-ios-memory-usage", "cisco-nxos-system-resources",
        "cisco-optical-power", "cisco-power-health", "cisco-running-configuration",
    } & {i.id for i in active.implementations}
    with _session_scope(session_factory) as db:
        assert db.query(CheckItemVersionRecord).count() == len(current.check_items)
        assert db.query(CollectionImplementationVersionRecord).count() == len(
            current.implementations
        )


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

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.check_items.command_adapter import (
    CommandAdapterError,
    direct_projection_manifest,
    direct_projection_source_field_errors,
    execute_adapter,
    validate_adapter_spec,
)
from src.core.check_items.comparison import build_comparison_fact_contract
from src.core.check_items.runtime_bundle_assembly import (
    derive_asset_requirement_profile,
)
from src.core.state import normalizer

INTERFACE_CHECK_ITEM = {
    "version_id": "interface-oper-v6",
    "canonical_contract": {
        "entity_type": "interface",
        "identity_dimensions": ["interface"],
        "observations": [
            {
                "canonical_field": "oper_status",
                "observation_type": "interface.oper_status",
                "semantic_type": "enum",
                "allowed_values": [
                    "up",
                    "down",
                    "admin_down",
                    "unknown",
                ],
                "required": True,
            }
        ],
    },
}


INTERFACE_SPEC = {
    "schema_version": "command-adapter/v2",
    "parser": {"template_id": "parser-interface-v1"},
    "outputs": [
        {
            "check_item_id": "interface.oper_status",
            "check_item_version_id": "interface-oper-v6",
            "source_record_set": "interfaces",
            "projection": {
                "fields": {
                    "interface": {"source": "name"},
                    "oper_status": {
                        "source": "link",
                        "value_mapping": {
                            "up": "up",
                            "down": "down",
                            "administratively down": "admin_down",
                        },
                        "on_unmapped": "reject",
                    },
                }
            },
        }
    ],
}


def test_v2_maps_vendor_enum_and_pins_check_item_version() -> None:
    batches = execute_adapter(
        INTERFACE_SPEC,
        record_sets={
            "interfaces": [
                {"name": "Eth1/1", "link": "UP"},
                {"name": "Eth1/2", "link": "administratively down"},
            ]
        },
        check_items={"interface.oper_status": INTERFACE_CHECK_ITEM},
        device_id="switch-1",
        adapter_version_id="adapter-v2",
        require_pinned_check_item_versions=True,
    )

    assert batches[0].quality_status == "complete"
    assert batches[0].records == (
        {"interface": "Eth1/1", "oper_status": "up"},
        {"interface": "Eth1/2", "oper_status": "admin_down"},
    )
    assert batches[0].evidence["adapter_schema_version"] == "command-adapter/v2"
    assert len(batches[0].evidence["adapter_projection_digest"]) == 64


def test_v2_allows_one_check_item_to_project_distinct_record_sets() -> None:
    spec = {
        **INTERFACE_SPEC,
        "outputs": [
            *INTERFACE_SPEC["outputs"],
            {
                **INTERFACE_SPEC["outputs"][0],
                "source_record_set": "logical_interfaces",
            },
        ],
    }

    batches = execute_adapter(
        spec,
        record_sets={
            "interfaces": [{"name": "Eth1/1", "link": "up"}],
            "logical_interfaces": [{"name": "Loop0", "link": "down"}],
        },
        check_items={"interface.oper_status": INTERFACE_CHECK_ITEM},
        device_id="switch-1",
        adapter_version_id="adapter-v2",
        require_pinned_check_item_versions=True,
    )

    assert [batch.records for batch in batches] == [
        ({"interface": "Eth1/1", "oper_status": "up"},),
        ({"interface": "Loop0", "oper_status": "down"},),
    ]
    manifest = direct_projection_manifest(
        spec,
        check_item_id="interface.oper_status",
        check_item=INTERFACE_CHECK_ITEM,
    )
    assert manifest["source_record_sets"] == ["interfaces", "logical_interfaces"]
    assert direct_projection_source_field_errors(
        manifest,
        parser_field_contract={
            "parser_spec_record_sets": {
                "interfaces": ["name", "link"],
                "logical_interfaces": ["name", "link"],
            }
        },
    ) == ()


def test_v2_rejects_unmapped_vendor_status_fail_closed() -> None:
    batches = execute_adapter(
        INTERFACE_SPEC,
        record_sets={"interfaces": [{"name": "Eth1/1", "link": "flapping"}]},
        check_items={"interface.oper_status": INTERFACE_CHECK_ITEM},
        device_id="switch-1",
        adapter_version_id="adapter-v2",
        require_pinned_check_item_versions=True,
    )

    assert batches[0].quality_status == "unavailable"
    assert "not covered by the approved value_mapping" in batches[0].errors[0]


def test_v2_converts_units_with_the_governed_projection_dsl() -> None:
    check_item = {
        "version_id": "memory-v2",
        "canonical_contract": {
            "entity_type": "device",
            "identity_dimensions": ["scope"],
            "observations": [
                {
                    "canonical_field": "memory_used_bytes",
                    "observation_type": "device.memory.used",
                    "value_type": "number",
                    "unit": "bytes",
                    "required": True,
                }
            ],
        },
    }
    spec = {
        "schema_version": "command-adapter/v2",
        "parser": {"template_id": "parser-memory-v1"},
        "outputs": [
            {
                "check_item_id": "system.memory_usage",
                "check_item_version_id": "memory-v2",
                "source_record_set": "memory",
                "projection": {
                    "fields": {
                        "scope": {"literal": "device"},
                        "memory_used_bytes": {
                            "source": "used_kb",
                            "source_unit": "kilobytes",
                            "canonical_unit": "bytes",
                        },
                    }
                },
            }
        ],
    }

    batches = execute_adapter(
        spec,
        record_sets={"memory": [{"used_kb": 2}]},
        check_items={"system.memory_usage": check_item},
        device_id="switch-1",
        adapter_version_id="adapter-v2",
        require_pinned_check_item_versions=True,
    )

    assert batches[0].records == ({"scope": "device", "memory_used_bytes": 2000.0},)


def test_v2_manifest_proves_parser_field_references() -> None:
    manifest = direct_projection_manifest(
        INTERFACE_SPEC,
        check_item_id="interface.oper_status",
        check_item=INTERFACE_CHECK_ITEM,
    )

    assert manifest["observation_types"] == ["interface.oper_status"]
    assert direct_projection_source_field_errors(
        manifest,
        parser_field_contract={
            "parser_spec_record_sets": {"interfaces": ["name"]}
        },
    ) == (
        "projection references a Parser field that is not declared in interfaces: link",
    )


def test_v2_joins_a_second_record_set_from_the_same_parser_bundle() -> None:
    check_item = {
        "version_id": "optical-v1",
        "canonical_contract": {
            "entity_type": "interface_optical",
            "identity_dimensions": ["interface", "channel"],
            "observations": [
                {
                    "canonical_field": "rx_power_dbm",
                    "observation_type": "interface.optical.rx_power",
                    "semantic_type": "gauge",
                    "unit": "dbm",
                    "required": True,
                },
                {
                    "canonical_field": "rx_low_alarm_dbm",
                    "observation_type": "interface.optical.rx_low_alarm",
                    "semantic_type": "threshold",
                    "unit": "dbm",
                    "required": False,
                },
            ],
        },
    }
    spec = {
        "schema_version": "command-adapter/v2",
        "parser": {"template_id": "parser-bundle-v1"},
        "outputs": [{
            "check_item_id": "interface.optical_power",
            "check_item_version_id": "optical-v1",
            "source_record_set": "optics",
            "record_set_joins": [{
                "source_record_set": "thresholds",
                "on": {"interface": "interface"},
                "fields": ["rx_low_alarm"],
            }],
            "projection": {"fields": {
                "interface": {"source": "interface"},
                "channel": {"source": "channel"},
                "rx_power_dbm": {"source": "rx_power"},
                "rx_low_alarm_dbm": {"source": "rx_low_alarm"},
            }},
        }],
    }
    manifest = direct_projection_manifest(
        spec,
        check_item_id="interface.optical_power",
        check_item=check_item,
    )

    assert direct_projection_source_field_errors(
        manifest,
        parser_field_contract={"template_record_sets": {
            "optics": ["interface", "channel", "rx_power"],
            "thresholds": ["interface", "rx_low_alarm"],
        }},
    ) == ()
    batch = execute_adapter(
        spec,
        record_sets={
            "optics": [
                {"interface": "XGE1/0/1", "channel": "1", "rx_power": -3.0},
                {"interface": "XGE1/0/1", "channel": "2", "rx_power": -3.2},
            ],
            "thresholds": [
                {"interface": "XGE1/0/1", "rx_low_alarm": -11.9},
            ],
        },
        check_items={"interface.optical_power": check_item},
        device_id="switch-1",
        adapter_version_id="adapter-v1",
        require_pinned_check_item_versions=True,
    )[0]

    assert batch.quality_status == "complete"
    assert [row["rx_low_alarm_dbm"] for row in batch.records] == [-11.9, -11.9]

def test_v2_requires_a_pinned_check_item_version_at_runtime() -> None:
    unpinned = {
        **INTERFACE_SPEC,
        "outputs": [
            {
                **INTERFACE_SPEC["outputs"][0],
                "check_item_version_id": "",
            }
        ],
    }

    with pytest.raises(CommandAdapterError, match="requires check_item_version_id"):
        validate_adapter_spec(
            unpinned,
            check_items={"interface.oper_status": INTERFACE_CHECK_ITEM},
            require_pinned_check_item_versions=True,
        )


def test_v2_normalizer_does_not_resolve_observation_contract(monkeypatch) -> None:
    implementation = SimpleNamespace(
        id="cisco-interface-status",
        version_id="adapter-v2",
        adapter_spec=INTERFACE_SPEC,
    )
    check_item = SimpleNamespace(
        id="interface.oper_status",
        version=6,
        version_id="interface-oper-v6",
        digest="a" * 64,
        canonical_contract=INTERFACE_CHECK_ITEM["canonical_contract"],
    )
    monkeypatch.setattr(
        normalizer.CheckItemCatalogRepository,
        "get_implementation_version_context",
        lambda _self, _version_id: (implementation, (check_item,)),
    )

    class ContractRepositoryShouldNotBeUsed:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("V2 adapter must bypass ObservationContract")

    monkeypatch.setattr(
        normalizer,
        "ObservationContractRepository",
        ContractRepositoryShouldNotBeUsed,
    )

    observations = normalizer.observations_from_snapshot(
        {
            "id": "snapshot-v2",
            "run_id": "run-v2",
            "device_id": "switch-1",
            "inventory_device_id": "inventory-1",
            "vendor": "Cisco",
            "model": "N9K",
            "canonical_command": "show interface status",
            "observed_at": "2026-08-27T00:00:00+00:00",
            "structured_json": {
                "record_sets": {"interfaces": [{"name": "Eth1/1", "link": "up"}]}
            },
            "implementation_version_id": "adapter-v2",
            "parser_template_id": "parser-interface-v1",
            "parser_version": "1",
            "raw_text_hash": "b" * 64,
            "metadata_json": {},
        },
        mode="production",
        source="test",
        dataset_id="dataset-1",
    )

    assert len(observations) == 1
    assert observations[0].observation_type == "interface.oper_status"
    assert observations[0].value == "up"
    assert observations[0].metadata["command_adapter_version_id"] == "adapter-v2"
    assert "observation_contract_id" not in observations[0].metadata


def test_v2_normalizer_drops_partial_adapter_batches(monkeypatch) -> None:
    implementation = SimpleNamespace(
        id="cisco-interface-status",
        version_id="adapter-v2",
        adapter_spec=INTERFACE_SPEC,
    )
    check_item = SimpleNamespace(
        id="interface.oper_status",
        version=6,
        version_id="interface-oper-v6",
        digest="a" * 64,
        canonical_contract=INTERFACE_CHECK_ITEM["canonical_contract"],
    )
    monkeypatch.setattr(
        normalizer.CheckItemCatalogRepository,
        "get_implementation_version_context",
        lambda _self, _version_id: (implementation, (check_item,)),
    )

    observations = normalizer.observations_from_snapshot(
        {
            "id": "partial-snapshot-v2",
            "run_id": "run-v2",
            "device_id": "switch-1",
            "inventory_device_id": "inventory-1",
            "canonical_command": "show interface status",
            "observed_at": "2026-08-27T00:00:00+00:00",
            "structured_json": {
                "record_sets": {
                    "interfaces": [
                        {"name": "Eth1/1", "link": "up"},
                        {"name": "Eth1/1", "link": "down"},
                    ]
                }
            },
            "implementation_version_id": "adapter-v2",
            "parser_template_id": "parser-interface-v1",
            "metadata_json": {},
        },
        mode="production",
        source="test",
        dataset_id="dataset-1",
    )

    assert observations == []


def test_post_cutover_production_rejects_legacy_contract_runtime(monkeypatch) -> None:
    monkeypatch.setattr(
        normalizer,
        "get_settings",
        lambda: SimpleNamespace(ADAPTER_PROJECTION_CUTOVER_ENFORCED=True),
    )

    with pytest.raises(
        normalizer.LegacyObservationRuntimeRetiredError,
        match="command-adapter/v2",
    ):
        normalizer.observations_from_snapshot(
            {
                "id": "legacy-snapshot",
                "device_id": "switch-1",
                "parser_template_id": "legacy-parser-v1",
                "structured_json": [{"interface": "Eth1/1", "status": "up"}],
            },
            mode="production",
            source="test",
            dataset_id=None,
        )

    assert normalizer._legacy_contract_runtime_retired("parser_validation") is False


def test_post_cutover_replay_does_not_recreate_a_missing_legacy_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        normalizer,
        "get_settings",
        lambda: SimpleNamespace(ADAPTER_PROJECTION_CUTOVER_ENFORCED=True),
    )

    class ContractRepository:
        def get_active_validation_dict(self, **_kwargs):
            return None

        def upsert_from_parser_template(self, *_args, **_kwargs):
            raise AssertionError("cutover replay must not write ObservationContract")

    monkeypatch.setattr(normalizer, "ObservationContractRepository", ContractRepository)
    monkeypatch.setattr(
        normalizer.ParserTemplateRepository,
        "get_template",
        lambda _self, _template_id: SimpleNamespace(
            id="legacy-parser-v1",
            status="active",
            category="interface",
            contract_version=4,
            validation_json={"state_semantics": {}},
        ),
    )

    assert normalizer._template_contract("legacy-parser-v1") == {
        "state_semantics": {},
        "entity_type": "interface",
        "contract_version": 4,
        "schema_id": "legacy-parser-v1",
        "source_type": "cli",
    }


def test_direct_projection_asset_profile_replaces_observation_contract() -> None:
    catalog = SimpleNamespace(
        get=lambda _id: SimpleNamespace(assessment_roles=("change",))
    )
    profile = derive_asset_requirement_profile(
        result_mode="structured",
        assessment_mode="change",
        canonical_rows=INTERFACE_CHECK_ITEM["canonical_contract"]["observations"],
        canonical_catalog=catalog,
        direct_projection=True,
    )

    assert profile["requirements"]["command_adapter_projection"]["required"] is True
    assert profile["requirements"]["observation_contract"]["required"] is False


def test_structured_comparison_contract_freezes_projection_not_observation_contract() -> None:
    contract = build_comparison_fact_contract(
        check_item_version_id="interface-oper-v6",
        check_item_digest="a" * 64,
        result_mode="structured",
        assessment_mode="change",
        implementation_version_id="adapter-v2",
        canonical_catalog_version=10,
        canonical_catalog_digest="b" * 64,
        entity_identity_policy_version_id="identity-v1",
        dependency_digests={
            "command_adapter_projection": {
                "implementation_version_id": "adapter-v2",
                "projection_digest": "c" * 64,
            },
            "observation_contracts": {"legacy-contract-v1": "d" * 64},
        },
        comparison_profile_version_id=None,
    )

    semantic = contract["semantic_contract"]
    assert semantic["command_adapter_projection"]["projection_digest"] == "c" * 64
    assert "observation_contract_digests" not in semantic


def test_v2_requires_and_records_declared_dimension_mapping() -> None:
    check_item = {
        "version_id": "cpu-v2",
        "canonical_contract": {
            "entity_type": "cpu_scope",
            "identity_dimensions": ["scope"],
            "observations": [
                {
                    "canonical_field": "cpu_usage_percent",
                    "observation_type": "device.cpu.utilization",
                    "semantic_type": "gauge",
                    "dimensions": ["window"],
                    "required": True,
                }
            ],
        },
    }
    spec = {
        "schema_version": "command-adapter/v2",
        "parser": {"template_id": "parser-cpu-v2"},
        "outputs": [
            {
                "check_item_id": "system.cpu_usage",
                "check_item_version_id": "cpu-v2",
                "source_record_set": "cpu",
                "projection": {
                    "fields": {
                        "scope": {"literal": "device"},
                        "cpu_usage_percent": {"source": "usage"},
                    }
                },
            }
        ],
    }

    with pytest.raises(CommandAdapterError, match="does not map required dimensions"):
        validate_adapter_spec(
            spec,
            check_items={"system.cpu_usage": check_item},
            require_pinned_check_item_versions=True,
        )

    mapped = {
        **spec,
        "outputs": [
            {
                **spec["outputs"][0],
                "projection": {
                    "fields": {
                        **spec["outputs"][0]["projection"]["fields"],
                        "cpu_usage_percent": {
                            "source": "usage",
                            "dimensions": {"window": {"field": "window_token"}},
                        },
                    }
                },
            }
        ],
    }
    batches = execute_adapter(
        mapped,
        record_sets={"cpu": [{"usage": 21, "window_token": "five_minutes"}]},
        check_items={"system.cpu_usage": check_item},
        device_id="switch-1",
        adapter_version_id="adapter-cpu-v2",
        require_pinned_check_item_versions=True,
    )
    assert batches[0].evidence["projection_dimensions_by_field"] == {
        "cpu_usage_percent": {"window": {"field": "window_token"}}
    }
    assert batches[0].projection_dimensions == (
        {"cpu_usage_percent": {"window": "five_minutes"}},
    )
    manifest = direct_projection_manifest(
        mapped,
        check_item_id="system.cpu_usage",
        check_item=check_item,
    )
    assert direct_projection_source_field_errors(
        manifest,
        parser_field_contract={"parser_spec_record_sets": {"cpu": ["usage"]}},
    ) == (
        "projection references a Parser field that is not declared in cpu: window_token",
    )


def test_v2_normalizer_emits_projection_declared_dimensions(monkeypatch) -> None:
    check_item_contract = {
        "entity_type": "cpu_scope",
        "identity_dimensions": ["scope"],
        "observations": [
            {
                "canonical_field": "cpu_usage_percent",
                "observation_type": "device.cpu.utilization",
                "semantic_type": "gauge",
                "dimensions": ["window"],
                "required": True,
            }
        ],
    }
    adapter = {
        "schema_version": "command-adapter/v2",
        "parser": {"template_id": "parser-cpu-v2"},
        "outputs": [
            {
                "check_item_id": "system.cpu_usage",
                "check_item_version_id": "cpu-v2",
                "source_record_set": "cpu",
                "projection": {
                    "fields": {
                        "scope": {"literal": "device"},
                        "cpu_usage_percent": {
                            "source": "usage",
                            "dimensions": {"window": {"field": "window_token"}},
                        },
                    }
                },
            }
        ],
    }
    implementation = SimpleNamespace(
        id="h3c-cpu-v2",
        version_id="adapter-cpu-v2",
        adapter_spec=adapter,
    )
    check_item = SimpleNamespace(
        id="system.cpu_usage",
        version=2,
        version_id="cpu-v2",
        digest="c" * 64,
        canonical_contract=check_item_contract,
    )
    monkeypatch.setattr(
        normalizer.CheckItemCatalogRepository,
        "get_implementation_version_context",
        lambda _self, _version_id: (implementation, (check_item,)),
    )

    observations = normalizer.observations_from_snapshot(
        {
            "id": "snapshot-cpu-v2",
            "run_id": "run-cpu-v2",
            "device_id": "switch-1",
            "inventory_device_id": "inventory-1",
            "vendor": "H3C",
            "canonical_command": "display cpu-usage",
            "observed_at": "2026-08-27T00:00:00+00:00",
            "structured_json": {
                "record_sets": {
                    "cpu": [{"usage": 21, "window_token": "five_minutes"}]
                }
            },
            "implementation_version_id": "adapter-cpu-v2",
            "parser_template_id": "parser-cpu-v2",
            "parser_version": "1",
            "raw_text_hash": "d" * 64,
            "metadata_json": {},
        },
        mode="production",
        source="test",
        dataset_id="dataset-1",
    )

    assert len(observations) == 1
    assert observations[0].dimensions == {"window": "five_minutes"}
    assert observations[0].value == 21


def test_cisco_h3c_huawei_interface_status_projections_share_canonical_domain() -> None:
    vendor_specs = {
        "Cisco": (
            "connected",
            {"connected": "up", "notconnect": "down", "disabled": "admin_down"},
        ),
        "H3C": (
            "UP",
            {"up": "up", "down": "down", "adm": "admin_down"},
        ),
        "Huawei": (
            "Administratively Down",
            {
                "up": "up",
                "down": "down",
                "administratively down": "admin_down",
            },
        ),
    }
    values = []
    for vendor, (raw_status, mapping) in vendor_specs.items():
        spec = {
            "schema_version": "command-adapter/v2",
            "parser": {"template_id": f"parser-{vendor.casefold()}-interface-v1"},
            "outputs": [
                {
                    "check_item_id": "interface.oper_status",
                    "check_item_version_id": "interface-oper-v6",
                    "source_record_set": "interfaces",
                    "projection": {
                        "fields": {
                            "interface": {"source": "name"},
                            "oper_status": {
                                "source": "vendor_oper_status",
                                "value_mapping": mapping,
                                "on_unmapped": "reject",
                            },
                        }
                    },
                }
            ],
        }
        batches = execute_adapter(
            spec,
            record_sets={
                "interfaces": [
                    {"name": "GigabitEthernet1/0/1", "vendor_oper_status": raw_status}
                ]
            },
            check_items={"interface.oper_status": INTERFACE_CHECK_ITEM},
            device_id=f"{vendor.casefold()}-switch",
            adapter_version_id=f"{vendor.casefold()}-adapter-v2",
            require_pinned_check_item_versions=True,
        )
        values.append(batches[0].records[0]["oper_status"])

    assert values == ["up", "up", "admin_down"]

from __future__ import annotations

import pytest

from src.core.check_items.command_adapter import (
    CommandAdapterError,
    evaluate_binding,
    execute_adapter,
    validate_adapter_spec,
)


SPEC = {
    "schema_version": "command-adapter/v1",
    "parser": {"template_id": "parser-system-resources-v1"},
    "outputs": [
        {
            "check_item_id": "system.cpu_usage",
            "source_record_set": "cpu_cores",
            "fields": {
                "scope": {"literal": "processor"},
                "scope_id": "cpu_id",
                "cpu_usage_percent": {
                    "expression": "100 - cpu_idle_percent"
                },
            },
        },
        {
            "check_item_id": "system.memory_usage",
            "source_record_set": "system_resources",
            "fields": {
                "scope": {"literal": "device"},
                "scope_id": {"literal": "system"},
                "memory_usage_percent": {
                    "expression": "100 * memory_used_kb / memory_total_kb"
                },
            },
        },
    ],
}


CHECK_ITEMS = {
    "system.cpu_usage": {
        "version_id": "cpu-v8",
        "canonical_contract": {
            "identity_dimensions": ["scope", "scope_id"],
            "observations": [
                {
                    "canonical_field": "cpu_usage_percent",
                    "value_type": "percent",
                    "required": True,
                }
            ],
        },
    },
    "system.memory_usage": {
        "version_id": "memory-v4",
        "canonical_contract": {
            "identity_dimensions": ["scope", "scope_id"],
            "observations": [
                {
                    "canonical_field": "memory_usage_percent",
                    "value_type": "percent",
                    "required": True,
                }
            ],
        },
    },
}


def test_execute_adapter_keeps_independent_outputs_independent() -> None:
    batches = execute_adapter(
        SPEC,
        record_sets={
            "cpu_cores": [
                {"cpu_id": "0", "cpu_idle_percent": "94.5"},
                {"cpu_id": "1", "cpu_idle_percent": "90"},
            ]
        },
        check_items=CHECK_ITEMS,
        device_id="device-1",
        adapter_version_id="adapter-v1",
    )

    cpu, memory = batches
    assert cpu.quality_status == "complete"
    assert cpu.records == (
        {"scope": "processor", "scope_id": "0", "cpu_usage_percent": 5.5},
        {"scope": "processor", "scope_id": "1", "cpu_usage_percent": 10.0},
    )
    assert memory.quality_status == "unavailable"
    assert memory.records == ()
    assert memory.errors == ("missing record_set: system_resources",)


def test_execute_adapter_reports_partial_rows_without_discarding_valid_rows() -> None:
    batches = execute_adapter(
        SPEC,
        record_sets={
            "cpu_cores": [
                {"cpu_id": "0", "cpu_idle_percent": "90"},
                {"cpu_id": "0", "cpu_idle_percent": "80"},
            ],
            "system_resources": [
                {"memory_used_kb": 25, "memory_total_kb": 100},
            ],
        },
        check_items=CHECK_ITEMS,
        device_id="device-1",
        adapter_version_id="adapter-v1",
    )

    cpu, memory = batches
    assert cpu.quality_status == "partial"
    assert len(cpu.records) == 1
    assert "duplicate primary key" in cpu.errors[0]
    assert memory.quality_status == "complete"
    assert memory.records[0]["memory_usage_percent"] == 25.0


def test_execute_adapter_preserves_confirmed_empty_semantics() -> None:
    spec = {
        **SPEC,
        "outputs": [
            {
                **SPEC["outputs"][0],
                "empty_status": "valid_empty",
            }
        ],
    }
    batches = execute_adapter(
        spec,
        record_sets={"cpu_cores": []},
        check_items={"system.cpu_usage": CHECK_ITEMS["system.cpu_usage"]},
        device_id="device-1",
        adapter_version_id="adapter-v1",
    )

    assert batches[0].quality_status == "valid_empty"
    assert batches[0].records == ()


def test_adapter_expression_rejects_calls_and_attribute_access() -> None:
    with pytest.raises(CommandAdapterError, match="basic arithmetic"):
        validate_adapter_spec(
            {
                "parser": {"template_id": "parser-cpu-v1"},
                "outputs": [
                    {
                        "check_item_id": "system.cpu_usage",
                        "source_record_set": "cpu",
                        "fields": {
                            "cpu_usage_percent": {
                                "expression": "__import__('os').system('whoami')"
                            }
                        },
                    }
                ]
            },
            check_item_ids=("system.cpu_usage",),
        )


def test_adapter_normalizes_legacy_single_parser_reference() -> None:
    normalized = validate_adapter_spec(
        {
            "parser": {
                "templates": [
                    {"record_set": "cpu", "template_id": "parser-bundle-v1"},
                    {"record_set": "memory", "template_id": "parser-bundle-v1"},
                ]
            },
            "outputs": SPEC["outputs"],
        },
        check_item_ids=("system.cpu_usage", "system.memory_usage"),
    )

    assert normalized["parser"] == {"template_id": "parser-bundle-v1"}


def test_adapter_rejects_multiple_parser_assets() -> None:
    with pytest.raises(CommandAdapterError, match="one Parser asset"):
        validate_adapter_spec(
            {
                "parser": {
                    "templates": [
                        {"record_set": "cpu", "template_id": "parser-cpu-v1"},
                        {"record_set": "memory", "template_id": "parser-memory-v1"},
                    ]
                },
                "outputs": SPEC["outputs"],
            },
            check_item_ids=("system.cpu_usage", "system.memory_usage"),
        )


def test_direct_source_binding_requires_the_source_field() -> None:
    with pytest.raises(CommandAdapterError, match="source field is missing"):
        evaluate_binding({"source": "cpu_usage_percent"}, {"idle": 90})


def test_adapter_rejects_a_value_that_violates_the_check_item_type() -> None:
    batches = execute_adapter(
        SPEC,
        record_sets={
            "cpu_cores": [{"cpu_id": "0", "cpu_idle_percent": "not-a-number"}],
            "system_resources": [],
        },
        check_items=CHECK_ITEMS,
        device_id="device-1",
        adapter_version_id="adapter-v1",
    )

    assert batches[0].quality_status == "unavailable"
    assert "source field is not numeric" in batches[0].errors[0]

from __future__ import annotations

import pytest

from src.core.observation.semantic_projection import (
    SemanticProjectionError,
    dimension_field_references,
    dimension_schema_signature,
    dimension_value_signature,
    project_value,
    resolve_dimensions,
    transform_field_references,
    validate_transform,
    validate_unit_conversion,
)


def test_governed_transform_derives_used_percent_from_free_percent():
    semantics = {
        "unit": "percent",
        "transform": {
            "op": "subtract",
            "args": [{"literal": 100}, {"field": "free_ratio"}],
        },
    }

    value = project_value(
        field_name="free_ratio",
        source_value=52.7,
        normalized_record={"free_ratio": 52.7},
        field_semantics=semantics,
    )

    assert value == pytest.approx(47.3)


def test_governed_transform_builds_one_canonical_version_from_parser_fields():
    transform = {
        "op": "concat",
        "args": [
            {"field": "software_version"},
            {"literal": ", Release "},
            {"field": "release"},
        ],
    }

    value = project_value(
        field_name="software_version",
        source_value="7.1.064",
        normalized_record={
            "software_version": "7.1.064",
            "release": "0821P18",
        },
        field_semantics={"transform": transform},
    )

    assert value == "7.1.064, Release 0821P18"
    assert transform_field_references(transform) == (
        "release",
        "software_version",
    )


def test_governed_transform_rejects_arbitrary_operations():
    transform = {
        "op": "python_eval",
        "args": [{"literal": "__import__('os').system('whoami')"}],
    }

    assert validate_transform(transform) == (
        "transform.op is unsupported: python_eval",
    )
    with pytest.raises(SemanticProjectionError, match="unsupported"):
        project_value(
            field_name="value",
            source_value=1,
            normalized_record={"value": 1},
            field_semantics={"transform": transform},
        )


def test_dimensions_resolve_literals_and_record_fields_deterministically():
    definition = {
        "scope": "system",
        "window": {"literal": "5s"},
        "slot": {"field": "slot"},
    }

    first = resolve_dimensions(definition, {"slot": "0"})
    second = resolve_dimensions(definition, {"slot": "0"})

    assert first == {"scope": "system", "slot": "0", "window": "5s"}
    assert dimension_value_signature(first) == dimension_value_signature(second)
    assert dimension_schema_signature(definition) != dimension_value_signature(first)


def test_unit_conversion_uses_platform_whitelist():
    value = project_value(
        field_name="memory",
        source_value=2,
        normalized_record={"memory": 2},
        field_semantics={
            "source_unit": "gib",
            "canonical_unit": "bytes",
        },
    )

    assert value == 2_147_483_648


def test_dimension_field_must_be_present():
    with pytest.raises(SemanticProjectionError, match="resolved to an empty value"):
        resolve_dimensions({"slot": {"field": "slot"}}, {})


def test_projection_static_analysis_reports_all_parser_field_dependencies():
    transform = {
        "op": "subtract",
        "args": [{"literal": 100}, {"field": "free_ratio"}],
    }
    dimensions = {
        "scope": {"literal": "slot"},
        "slot": {"field": "slot"},
    }

    assert transform_field_references(transform) == ("free_ratio",)
    assert dimension_field_references(dimensions) == ("slot",)
    assert validate_unit_conversion(source_unit="MiB", target_unit="bytes") == ()
    assert validate_unit_conversion(source_unit="celsius", target_unit="dbm") == (
        "unsupported unit conversion: celsius -> dbm",
    )


def test_dimension_expression_builds_stable_canonical_identity():
    dimensions = {
        "scope": {"literal": "processor"},
        "scope_id": {
            "op": "concat",
            "args": [
                {"literal": "slot:"},
                {"field": "slot"},
                {"literal": "/cpu:"},
                {"field": "cpu"},
            ],
        },
    }

    assert resolve_dimensions(
        dimensions,
        {"slot": "01", "cpu": "2"},
    ) == {
        "scope": "processor",
        "scope_id": "slot:01/cpu:2",
    }
    assert dimension_field_references(dimensions) == ("cpu", "slot")

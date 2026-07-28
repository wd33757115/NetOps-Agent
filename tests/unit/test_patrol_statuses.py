from __future__ import annotations

import pytest

from src.core.patrol.statuses import (
    PatrolCommandStatus,
    derive_command_status,
    validate_command_status,
)


@pytest.mark.parametrize(
    ("execution", "parsed", "expected"),
    [
        ("complete", "parsed", ("complete", "parsed", "ready", "pending")),
        ("complete", "parse_empty", ("complete", "parse_empty", "empty", "empty")),
        ("complete", "ignored", ("complete", "ignored", "ignored", "skipped")),
        ("complete", "raw_hashed", ("complete", "raw_hashed", "ready", "pending")),
        (
            "complete",
            "not_applicable",
            ("complete", "not_applicable", "not_applicable", "skipped"),
        ),
        (
            "complete",
            "empty_unverified",
            ("complete", "empty_unverified", "blocked_by_output", "blocked"),
        ),
        (
            "complete",
            "command_error",
            ("complete", "command_error", "blocked_by_output", "blocked"),
        ),
        (
            "complete",
            "truncated",
            ("complete", "truncated", "blocked_by_output", "blocked"),
        ),
        (
            "complete",
            "paged",
            ("complete", "paged", "blocked_by_output", "blocked"),
        ),
        (
            "complete",
            "template_missing",
            ("complete", "template_missing", "blocked_by_parser", "blocked"),
        ),
        (
            "complete",
            "parse_failed",
            ("complete", "parse_failed", "blocked_by_parser", "blocked"),
        ),
        (
            "complete",
            "identity_unknown",
            ("complete", "identity_unknown", "blocked_by_identity", "blocked"),
        ),
        (
            "failed",
            "parsed",
            ("failed", "not_attempted", "blocked_by_collection", "blocked"),
        ),
        (
            "timeout",
            "template_missing",
            ("timeout", "not_attempted", "blocked_by_collection", "blocked"),
        ),
    ],
)
def test_command_status_matrix(execution, parsed, expected):
    status = derive_command_status(
        execution_status=execution,
        parse_status=parsed,
    )

    assert (
        status.execution_status,
        status.parse_status,
        status.fact_status,
        status.normalization_status,
    ) == expected
    validate_command_status(status)


def test_status_validator_rejects_cross_dimension_contradiction():
    with pytest.raises(ValueError, match="inconsistent patrol command status"):
        validate_command_status(
            PatrolCommandStatus(
                execution_status="complete",
                parse_status="template_missing",
                fact_status="ready",
                normalization_status="pending",
            )
        )

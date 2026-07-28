"""Canonical command-level patrol status semantics."""

from __future__ import annotations

from dataclasses import dataclass

EXECUTION_STATUSES = {"complete", "failed", "timeout", "cancelled"}
PARSE_STATUSES = {
    "not_attempted",
    "parsed",
    "parse_empty",
    "ignored",
    "template_missing",
    "parse_failed",
    "identity_unknown",
    "raw_hashed",
    "not_applicable",
    "empty_unverified",
    "command_error",
    "truncated",
    "paged",
}
FACT_STATUSES = {
    "ready",
    "empty",
    "ignored",
    "blocked_by_collection",
    "blocked_by_parser",
    "blocked_by_identity",
    "blocked_by_contract",
    "normalization_failed",
    "not_applicable",
    "blocked_by_output",
}
NORMALIZATION_STATUSES = {
    "pending",
    "normalized",
    "empty",
    "skipped",
    "blocked",
    "failed",
}


@dataclass(frozen=True)
class PatrolCommandStatus:
    execution_status: str
    parse_status: str
    fact_status: str
    normalization_status: str


def normalize_execution_status(value: str | None) -> str:
    status = str(value or "failed").strip().lower()
    if status == "completed":
        status = "complete"
    return status if status in EXECUTION_STATUSES else "failed"


def derive_command_status(
    *, execution_status: str | None, parse_status: str | None
) -> PatrolCommandStatus:
    execution = normalize_execution_status(execution_status)
    parsed = str(parse_status or "not_attempted").strip().lower()
    if parsed not in PARSE_STATUSES:
        parsed = "parse_failed" if execution == "complete" else "not_attempted"

    if execution != "complete":
        return PatrolCommandStatus(
            execution_status=execution,
            parse_status="not_attempted",
            fact_status="blocked_by_collection",
            normalization_status="blocked",
        )
    mapping = {
        "parsed": ("ready", "pending"),
        "parse_empty": ("empty", "empty"),
        "ignored": ("ignored", "skipped"),
        "template_missing": ("blocked_by_parser", "blocked"),
        "parse_failed": ("blocked_by_parser", "blocked"),
        "identity_unknown": ("blocked_by_identity", "blocked"),
        "not_attempted": ("blocked_by_parser", "blocked"),
        "raw_hashed": ("ready", "pending"),
        "not_applicable": ("not_applicable", "skipped"),
        "empty_unverified": ("blocked_by_output", "blocked"),
        "command_error": ("blocked_by_output", "blocked"),
        "truncated": ("blocked_by_output", "blocked"),
        "paged": ("blocked_by_output", "blocked"),
    }
    fact_status, normalization_status = mapping[parsed]
    return PatrolCommandStatus(execution, parsed, fact_status, normalization_status)


def validate_command_status(status: PatrolCommandStatus) -> None:
    if status.execution_status not in EXECUTION_STATUSES:
        raise ValueError(f"unsupported execution_status: {status.execution_status}")
    if status.parse_status not in PARSE_STATUSES:
        raise ValueError(f"unsupported parse_status: {status.parse_status}")
    if status.fact_status not in FACT_STATUSES:
        raise ValueError(f"unsupported fact_status: {status.fact_status}")
    if status.normalization_status not in NORMALIZATION_STATUSES:
        raise ValueError(f"unsupported normalization_status: {status.normalization_status}")
    expected = derive_command_status(
        execution_status=status.execution_status,
        parse_status=status.parse_status,
    )
    if status != expected:
        raise ValueError(
            f"inconsistent patrol command status: received={status}, expected={expected}"
        )


def readiness_status(statuses: list[str], *, ready: set[str]) -> str:
    if not statuses:
        return "unknown"
    ready_count = sum(status in ready for status in statuses)
    if ready_count == len(statuses):
        return "ready"
    if ready_count == 0:
        return "blocked"
    return "partial"

"""Unified parser coverage decision for preview, launch, and collectors."""

from __future__ import annotations

from typing import Any, Literal

CoverageDecision = Literal["allow", "skip", "block"]
CoveragePolicy = Literal["block", "skip", "warn"]

VALID_POLICIES = {"block", "skip", "warn"}


def normalize_coverage_policy(value: str | None) -> CoveragePolicy:
    text = str(value or "skip").strip().lower()
    if text not in VALID_POLICIES:
        raise ValueError(
            f"invalid PATROL_PARSER_COVERAGE_POLICY: {value}; "
            "expected block, skip, or warn"
        )
    return text  # type: ignore[return-value]


def eligible_coverage_violations(
    coverage: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    return [
        item
        for item in list(coverage or [])
        if item.get("policy_status") == "eligible"
        and item.get("coverage_status") != "ready"
    ]


def decide_parser_coverage(
    *,
    coverage: list[dict[str, Any]] | None,
    policy: str | None = None,
    mode: str | None = None,
    execution_plan: Any | None = None,
) -> dict[str, Any]:
    """Return a single decision shared by preview and execution paths."""
    from src.core.patrol.modes import PRODUCTION_MODE, normalize_patrol_mode
    from src.common.config import get_settings

    normalized_mode = normalize_patrol_mode(mode) if mode else PRODUCTION_MODE
    # A human-bound command set owns collection scope. Missing parsers affect
    # analysis quality, never silently skip the device or change its commands.
    if (execution_plan is not None
        and getattr(execution_plan, "profile_version_id", None)
        and getattr(execution_plan, "commands", ())
        and not getattr(execution_plan, "check_item_ids", ())
        and not getattr(execution_plan, "runtime_manifest_id", None)):
        policy = "warn"
    resolved_policy = normalize_coverage_policy(
        policy if policy is not None else get_settings().PATROL_PARSER_COVERAGE_POLICY
    )
    violations = (
        eligible_coverage_violations(coverage)
        if normalized_mode == PRODUCTION_MODE
        else []
    )
    if not violations:
        decision: CoverageDecision = "allow"
    elif resolved_policy == "block":
        decision = "block"
    elif resolved_policy == "skip":
        decision = "skip"
    else:
        decision = "allow"

    failure_code = None
    if decision == "block":
        failure_code = "parser_coverage_not_ready"
    elif decision == "skip":
        failure_code = "skipped_parser_coverage"

    return {
        "policy": resolved_policy,
        "decision": decision,
        "allowed": decision == "allow",
        "violations": violations,
        "failure_code": failure_code,
        "mode": normalized_mode,
    }

"""Unit tests for patrol parser coverage policy."""

from __future__ import annotations

from src.core.patrol.coverage_policy import decide_parser_coverage, normalize_coverage_policy


def _violation() -> dict:
    return {
        "policy_status": "eligible",
        "coverage_status": "template_missing",
        "command": "display version",
    }


def test_normalize_coverage_policy_defaults_and_rejects() -> None:
    assert normalize_coverage_policy(None) == "skip"
    assert normalize_coverage_policy("WARN") == "warn"
    try:
        normalize_coverage_policy("strict")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_decide_allow_when_no_violations() -> None:
    result = decide_parser_coverage(
        coverage=[{"policy_status": "eligible", "coverage_status": "ready"}],
        policy="block",
        mode="production",
    )
    assert result["decision"] == "allow"
    assert result["allowed"] is True
    assert result["failure_code"] is None


def test_decide_block_skip_warn_policies() -> None:
    coverage = [_violation()]
    blocked = decide_parser_coverage(coverage=coverage, policy="block", mode="production")
    assert blocked["decision"] == "block"
    assert blocked["failure_code"] == "parser_coverage_not_ready"

    skipped = decide_parser_coverage(coverage=coverage, policy="skip", mode="production")
    assert skipped["decision"] == "skip"
    assert skipped["failure_code"] == "skipped_parser_coverage"

    warned = decide_parser_coverage(coverage=coverage, policy="warn", mode="production")
    assert warned["decision"] == "allow"
    assert warned["violations"]
    assert warned["allowed"] is True


def test_parser_validation_mode_ignores_violations() -> None:
    result = decide_parser_coverage(
        coverage=[_violation()],
        policy="block",
        mode="parser_validation",
    )
    assert result["decision"] == "allow"
    assert result["violations"] == []


def test_explicit_command_set_collects_missing_parser_without_hiding_quality_gap():
    from types import SimpleNamespace
    from src.core.patrol.coverage_policy import decide_parser_coverage
    gap = {"canonical_command": "display memory", "policy_status": "eligible", "coverage_status": "missing"}
    result = decide_parser_coverage(coverage=[gap], policy="skip", mode="production",
        execution_plan=SimpleNamespace(profile_version_id="v1", commands=["display memory"], check_item_ids=(), runtime_manifest_id=None))
    assert result["decision"] == "allow"
    assert result["policy"] == "warn"
    assert result["violations"] == [gap]
    assert decide_parser_coverage(coverage=[gap], policy="skip", mode="production")["decision"] == "skip"

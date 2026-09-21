"""Release gate for historical replay evidence."""

from __future__ import annotations

from typing import Any


def normalize_run_ids(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()})


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def evaluate_historical_replay_gate(
    replay: dict[str, Any] | None,
    context_gate: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate replay outcome and bind it to an immutable dataset context."""

    replay = dict(replay or {})
    context_gate = dict(context_gate or {})
    errors: list[str] = []
    outcome_success = (
        replay.get("success") is True
        and _non_negative_int(replay.get("collision_count")) == 0
    )
    if not outcome_success:
        errors.append("historical replay is missing, failed or has collisions")

    immutable_context = (
        context_gate.get("mode") == "immutable_dataset_context"
        or bool(context_gate.get("context_id"))
    )
    expected_run_ids = normalize_run_ids(context_gate.get("replay_run_ids"))
    scope = dict(replay.get("scope") or {})
    actual_run_ids = normalize_run_ids(scope.get("run_ids"))
    sampled_run_ids = normalize_run_ids(scope.get("sampled_run_ids"))
    missing_run_ids = normalize_run_ids(scope.get("missing_run_ids"))
    sampled_change_count = _non_negative_int(replay.get("sampled_change_count"))
    total_change_count = _non_negative_int(replay.get("total_change_count"))
    evidence_success = sampled_change_count > 0
    warnings: list[str] = []
    if not evidence_success:
        errors.append("historical replay contains no Change evidence")
    if total_change_count and sampled_change_count > total_change_count:
        evidence_success = False
        errors.append("historical replay sampled_change_count exceeds total_change_count")
    scope_success = True
    if immutable_context:
        if not expected_run_ids:
            scope_success = False
            errors.append("immutable context has no replay_run_ids")
        if scope.get("mode") != "run_ids" or not actual_run_ids:
            scope_success = False
            errors.append("historical replay must be scoped by immutable run_ids")
        if expected_run_ids and actual_run_ids != expected_run_ids:
            scope_success = False
            errors.append("historical replay run_ids do not match immutable context")
        if scope.get("run_id_count") != len(actual_run_ids):
            scope_success = False
            errors.append("historical replay run_id_count is inconsistent")
        if sampled_run_ids and not set(sampled_run_ids).issubset(actual_run_ids):
            scope_success = False
            errors.append("historical replay sampled_run_ids exceed immutable run scope")
        sampled_run_id_count = scope.get("sampled_run_id_count")
        if sampled_run_id_count is not None and _non_negative_int(sampled_run_id_count) != len(
            sampled_run_ids
        ):
            scope_success = False
            errors.append("historical replay sampled_run_id_count is inconsistent")
        if sampled_change_count > 0 and sampled_run_ids == [] and "sampled_run_ids" in scope:
            scope_success = False
            errors.append("historical replay Change evidence is not bound to an immutable run")
        if missing_run_ids:
            warnings.append("some immutable runs contain no Change evidence")

    return {
        "success": outcome_success and scope_success and evidence_success,
        "outcome_success": outcome_success,
        "scope_success": scope_success,
        "evidence_success": evidence_success,
        "scope_required": immutable_context,
        "scope_mode": scope.get("mode") or "missing",
        "expected_run_ids": expected_run_ids,
        "actual_run_ids": actual_run_ids,
        "sampled_run_ids": sampled_run_ids,
        "missing_run_ids": missing_run_ids,
        "sampled_change_count": sampled_change_count,
        "total_change_count": total_change_count,
        "warnings": warnings,
        "errors": list(dict.fromkeys(errors)),
    }

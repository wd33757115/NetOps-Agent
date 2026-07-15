"""Release gate for historical replay evidence."""

from __future__ import annotations

from typing import Any


def normalize_run_ids(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()})


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
        and int(replay.get("collision_count") or 0) == 0
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

    return {
        "success": outcome_success and scope_success,
        "outcome_success": outcome_success,
        "scope_success": scope_success,
        "scope_required": immutable_context,
        "scope_mode": scope.get("mode") or "missing",
        "expected_run_ids": expected_run_ids,
        "actual_run_ids": actual_run_ids,
        "errors": list(dict.fromkeys(errors)),
    }

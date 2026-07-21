"""Transactional projections for queryable rule-control history."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, insert, select, update


def _utc_datetime(value: Any, fallback: datetime | None = None) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return fallback
    else:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _stable_id(*parts: Any) -> str:
    payload = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def extract_historical_replay(
    validation_report: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(validation_report, dict):
        return None
    replay = validation_report.get("historical_replay")
    if isinstance(replay, dict):
        return replay
    governance = validation_report.get("governance")
    if isinstance(governance, dict):
        replay = governance.get("historical_replay")
        if isinstance(replay, dict):
            return replay
    return None


def extract_agent_loops(
    validation_report: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(validation_report, dict):
        return []
    loops: list[dict[str, Any]] = []
    for candidate in (
        validation_report.get("agent_loop"),
        (
            validation_report.get("synthetic_validation", {}).get("agent_loop")
            if isinstance(validation_report.get("synthetic_validation"), dict)
            else None
        ),
        (
            validation_report.get("validation", {}).get("agent_loop")
            if isinstance(validation_report.get("validation"), dict)
            else None
        ),
    ):
        if isinstance(candidate, dict):
            loops.append(candidate)
    return loops


def build_projection_rows(job: Any) -> dict[str, list[dict[str, Any]]]:
    """Build deterministic rows from one rule generation job."""

    report = (
        dict(job.validation_report_json) if isinstance(job.validation_report_json, dict) else {}
    )
    created_at = _utc_datetime(job.created_at)
    updated_at = _utc_datetime(job.updated_at, created_at)
    replay_rows: list[dict[str, Any]] = []
    replay = extract_historical_replay(report)
    if replay:
        replay_rows.append(
            {
                "job_id": str(job.id),
                "success": replay.get("success"),
                "sampled_change_count": _integer(replay.get("sampled_change_count")),
                "matched_change_count": _integer(replay.get("matched_change_count")),
                "estimated_daily_event_count": _number(replay.get("estimated_daily_event_count")),
                "collision_count": _integer(replay.get("collision_count")),
                "event_types_json": replay.get("event_types") or {},
                "matched_entity_types_json": replay.get("matched_entity_types") or {},
                "top_devices_json": replay.get("top_devices") or {},
                "collision_rules_json": replay.get("collision_rules") or {},
                "unexpected_matches_json": replay.get("unexpected_matches") or [],
                "recovery_pair_coverage_json": replay.get("recovery_pair_coverage"),
                "report_json": replay,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )

    governance = report.get("governance")
    governance = dict(governance) if isinstance(governance, dict) else {}
    synthetic = report.get("synthetic_validation")
    synthetic = dict(synthetic) if isinstance(synthetic, dict) else {}
    replay_success = replay.get("success") if replay else None
    approval_rows: list[dict[str, Any]] = []
    action_fields = (
        ("approved", "approved_by", "approved_at", "approval_reason"),
        ("rejected", "rejected_by", "rejected_at", "reason"),
        ("deprecated", "deprecated_by", "deprecated_at", "reason"),
    )
    for action, actor_key, time_key, reason_key in action_fields:
        actor = governance.get(actor_key)
        occurred_at = _utc_datetime(governance.get(time_key))
        if not actor and occurred_at is None:
            continue
        approval_rows.append(
            {
                "id": _stable_id("rule-approval", job.id, action),
                "job_id": str(job.id),
                "action": action,
                "actor": str(actor) if actor is not None else None,
                "reason": governance.get(reason_key),
                "occurred_at": occurred_at,
                "validation_success": report.get("success"),
                "synthetic_success": synthetic.get("success"),
                "replay_success": replay_success,
                "governance_json": governance,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )

    attempt_rows: list[dict[str, Any]] = []
    for loop_index, loop in enumerate(extract_agent_loops(report), start=1):
        loop_attempts = loop.get("attempts")
        if not isinstance(loop_attempts, list):
            continue
        for attempt in loop_attempts:
            if not isinstance(attempt, dict):
                continue
            ordinal = len(attempt_rows) + 1
            attempt_number = _integer(attempt.get("attempt")) or ordinal
            revision_request = attempt.get("revision_request")
            if not isinstance(revision_request, dict):
                revision_request = loop.get("revision_request")
            attempt_rows.append(
                {
                    "id": _stable_id(
                        "rule-agent-loop",
                        job.id,
                        loop_index,
                        attempt_number,
                        attempt.get("stage"),
                        attempt.get("generator"),
                        attempt.get("model_name"),
                    ),
                    "job_id": str(job.id),
                    "attempt_ordinal": ordinal,
                    "attempt_number": attempt_number,
                    "stage": attempt.get("stage"),
                    "generator": attempt.get("generator"),
                    "model_name": attempt.get("model_name"),
                    "mode": attempt.get("mode"),
                    "success": attempt.get("success"),
                    "input_summary_json": attempt.get("input_summary") or {},
                    "output_summary_json": attempt.get("output_summary") or {},
                    "validator_feedback_json": attempt.get("validator_feedback") or {},
                    "revision_request_json": (
                        revision_request if isinstance(revision_request, dict) else None
                    ),
                    "ready_for_approval": loop.get("ready_for_approval"),
                    "next_action": loop.get("next_action"),
                    "created_at": created_at,
                    "updated_at": updated_at,
                }
            )

    return {
        "replay": replay_rows,
        "approval": approval_rows,
        "attempt": attempt_rows,
    }


def synchronize_rule_job_projections(connection: Any, job: Any) -> None:
    """Replace one job's projections in the same transaction as the source row."""

    from src.infrastructure.db.models import (
        RuleAgentLoopAttemptRecord,
        RuleApprovalAuditRecord,
        RuleReplayReportRecord,
    )

    job_id = str(job.id)
    rows = build_projection_rows(job)
    connection.execute(
        delete(RuleReplayReportRecord.__table__).where(RuleReplayReportRecord.job_id == job_id)
    )
    if rows["replay"]:
        connection.execute(insert(RuleReplayReportRecord.__table__), rows["replay"])

    for model, projection_rows in (
        (RuleApprovalAuditRecord, rows["approval"]),
        (RuleAgentLoopAttemptRecord, rows["attempt"]),
    ):
        if not projection_rows:
            continue
        existing_ids = set(
            connection.execute(select(model.id).where(model.job_id == job_id)).scalars()
        )
        for row in projection_rows:
            row_id = str(row["id"])
            if row_id in existing_ids:
                values = {key: value for key, value in row.items() if key != "id"}
                connection.execute(
                    update(model.__table__).where(model.id == row_id).values(**values)
                )
            else:
                connection.execute(insert(model.__table__), row)

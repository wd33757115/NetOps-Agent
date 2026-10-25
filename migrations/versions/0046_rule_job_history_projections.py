"""Add queryable rule job history projections.

Revision ID: 0046_rule_job_projections
Revises: 0045_patrol_batch_report_state
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0046_rule_job_projections"
down_revision = "0045_patrol_batch_report_state"
branch_labels = None
depends_on = None


def _stable_id(*parts: Any) -> str:
    value = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _datetime(value: Any, fallback: datetime | None = None) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and value.strip():
        try:
            result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return fallback
    else:
        return fallback
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


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


def _historical_replay(report: dict[str, Any]) -> dict[str, Any] | None:
    replay = report.get("historical_replay")
    if isinstance(replay, dict):
        return replay
    governance = report.get("governance")
    if isinstance(governance, dict) and isinstance(governance.get("historical_replay"), dict):
        return governance["historical_replay"]
    return None


def _agent_loops(report: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [report.get("agent_loop")]
    for section in ("synthetic_validation", "validation"):
        section_value = report.get(section)
        candidates.append(
            section_value.get("agent_loop") if isinstance(section_value, dict) else None
        )
    return [candidate for candidate in candidates if isinstance(candidate, dict)]


def _backfill(bind: Any) -> None:
    replay_table = sa.table(
        "netops_rule_replay_reports",
        sa.column("job_id", sa.String()),
        sa.column("success", sa.Boolean()),
        sa.column("sampled_change_count", sa.Integer()),
        sa.column("matched_change_count", sa.Integer()),
        sa.column("estimated_daily_event_count", sa.Float()),
        sa.column("collision_count", sa.Integer()),
        sa.column("event_types_json", sa.JSON()),
        sa.column("matched_entity_types_json", sa.JSON()),
        sa.column("top_devices_json", sa.JSON()),
        sa.column("collision_rules_json", sa.JSON()),
        sa.column("unexpected_matches_json", sa.JSON()),
        sa.column("recovery_pair_coverage_json", sa.JSON()),
        sa.column("report_json", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    approval_table = sa.table(
        "netops_rule_approval_audit",
        sa.column("id", sa.String()),
        sa.column("job_id", sa.String()),
        sa.column("action", sa.String()),
        sa.column("actor", sa.String()),
        sa.column("reason", sa.Text()),
        sa.column("occurred_at", sa.DateTime(timezone=True)),
        sa.column("validation_success", sa.Boolean()),
        sa.column("synthetic_success", sa.Boolean()),
        sa.column("replay_success", sa.Boolean()),
        sa.column("governance_json", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    attempt_table = sa.table(
        "netops_rule_agent_loop_attempts",
        sa.column("id", sa.String()),
        sa.column("job_id", sa.String()),
        sa.column("attempt_ordinal", sa.Integer()),
        sa.column("attempt_number", sa.Integer()),
        sa.column("stage", sa.String()),
        sa.column("generator", sa.String()),
        sa.column("model_name", sa.String()),
        sa.column("mode", sa.String()),
        sa.column("success", sa.Boolean()),
        sa.column("input_summary_json", sa.JSON()),
        sa.column("output_summary_json", sa.JSON()),
        sa.column("validator_feedback_json", sa.JSON()),
        sa.column("revision_request_json", sa.JSON()),
        sa.column("ready_for_approval", sa.Boolean()),
        sa.column("next_action", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    replay_rows: list[dict[str, Any]] = []
    approval_rows: list[dict[str, Any]] = []
    attempt_rows: list[dict[str, Any]] = []
    jobs = bind.execute(
        sa.text(
            """
            SELECT id, validation_report_json, created_at, updated_at
            FROM netops_rule_generation_jobs
            ORDER BY id
            """
        )
    ).mappings()
    for job in jobs:
        report = job["validation_report_json"]
        report = dict(report) if isinstance(report, dict) else {}
        created_at = _datetime(job["created_at"])
        updated_at = _datetime(job["updated_at"], created_at)
        replay = _historical_replay(report)
        if replay:
            replay_rows.append(
                {
                    "job_id": str(job["id"]),
                    "success": replay.get("success"),
                    "sampled_change_count": _integer(replay.get("sampled_change_count")),
                    "matched_change_count": _integer(replay.get("matched_change_count")),
                    "estimated_daily_event_count": _number(
                        replay.get("estimated_daily_event_count")
                    ),
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
        for action, actor_key, time_key, reason_key in (
            ("approved", "approved_by", "approved_at", "approval_reason"),
            ("rejected", "rejected_by", "rejected_at", "reason"),
            ("deprecated", "deprecated_by", "deprecated_at", "reason"),
        ):
            actor = governance.get(actor_key)
            occurred_at = _datetime(governance.get(time_key))
            if not actor and occurred_at is None:
                continue
            approval_rows.append(
                {
                    "id": _stable_id("rule-approval", job["id"], action),
                    "job_id": str(job["id"]),
                    "action": action,
                    "actor": str(actor) if actor is not None else None,
                    "reason": governance.get(reason_key),
                    "occurred_at": occurred_at,
                    "validation_success": report.get("success"),
                    "synthetic_success": synthetic.get("success"),
                    "replay_success": replay.get("success") if replay else None,
                    "governance_json": governance,
                    "created_at": created_at,
                    "updated_at": updated_at,
                }
            )

        attempt_ordinal = 0
        for loop_index, loop in enumerate(_agent_loops(report), start=1):
            attempts = loop.get("attempts")
            if not isinstance(attempts, list):
                continue
            for attempt in attempts:
                if not isinstance(attempt, dict):
                    continue
                attempt_ordinal += 1
                ordinal = attempt_ordinal
                revision_request = attempt.get("revision_request")
                if not isinstance(revision_request, dict):
                    revision_request = loop.get("revision_request")
                attempt_rows.append(
                    {
                        "id": _stable_id(
                            "rule-agent-loop",
                            job["id"],
                            loop_index,
                            _integer(attempt.get("attempt")) or ordinal,
                            attempt.get("stage"),
                            attempt.get("generator"),
                            attempt.get("model_name"),
                        ),
                        "job_id": str(job["id"]),
                        "attempt_ordinal": ordinal,
                        "attempt_number": _integer(attempt.get("attempt")) or ordinal,
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

    if replay_rows:
        bind.execute(replay_table.insert(), replay_rows)
    if approval_rows:
        bind.execute(approval_table.insert(), approval_rows)
    if attempt_rows:
        bind.execute(attempt_table.insert(), attempt_rows)


def upgrade() -> None:
    op.create_table(
        "netops_rule_replay_reports",
        sa.Column(
            "job_id",
            sa.String(length=64),
            sa.ForeignKey("netops_rule_generation_jobs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("success", sa.Boolean(), nullable=True),
        sa.Column("sampled_change_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matched_change_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_daily_event_count", sa.Float(), nullable=False, server_default="0"),
        sa.Column("collision_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("event_types_json", sa.JSON(), nullable=False),
        sa.Column("matched_entity_types_json", sa.JSON(), nullable=False),
        sa.Column("top_devices_json", sa.JSON(), nullable=False),
        sa.Column("collision_rules_json", sa.JSON(), nullable=False),
        sa.Column("unexpected_matches_json", sa.JSON(), nullable=False),
        sa.Column("recovery_pair_coverage_json", sa.JSON(), nullable=True),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_netops_rule_replay_reports_success",
        "netops_rule_replay_reports",
        ["success"],
    )
    op.create_index(
        "ix_netops_rule_replay_reports_updated_at",
        "netops_rule_replay_reports",
        ["updated_at"],
    )
    op.create_index(
        "ix_rule_replay_success_updated",
        "netops_rule_replay_reports",
        ["success", "updated_at"],
    )

    op.create_table(
        "netops_rule_approval_audit",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(length=64),
            sa.ForeignKey("netops_rule_generation_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_success", sa.Boolean(), nullable=True),
        sa.Column("synthetic_success", sa.Boolean(), nullable=True),
        sa.Column("replay_success", sa.Boolean(), nullable=True),
        sa.Column("governance_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("job_id", "action", name="uq_rule_approval_job_action"),
    )
    op.create_index(
        "ix_netops_rule_approval_audit_job_id",
        "netops_rule_approval_audit",
        ["job_id"],
    )
    op.create_index(
        "ix_netops_rule_approval_audit_action",
        "netops_rule_approval_audit",
        ["action"],
    )
    op.create_index(
        "ix_netops_rule_approval_audit_actor",
        "netops_rule_approval_audit",
        ["actor"],
    )
    op.create_index(
        "ix_netops_rule_approval_audit_occurred_at",
        "netops_rule_approval_audit",
        ["occurred_at"],
    )
    op.create_index(
        "ix_rule_approval_action_occurred",
        "netops_rule_approval_audit",
        ["action", "occurred_at"],
    )

    op.create_table(
        "netops_rule_agent_loop_attempts",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(length=64),
            sa.ForeignKey("netops_rule_generation_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_ordinal", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=128), nullable=True),
        sa.Column("generator", sa.String(length=128), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("mode", sa.String(length=64), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=True),
        sa.Column("input_summary_json", sa.JSON(), nullable=False),
        sa.Column("output_summary_json", sa.JSON(), nullable=False),
        sa.Column("validator_feedback_json", sa.JSON(), nullable=False),
        sa.Column("revision_request_json", sa.JSON(), nullable=True),
        sa.Column("ready_for_approval", sa.Boolean(), nullable=True),
        sa.Column("next_action", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_netops_rule_agent_loop_attempts_job_id",
        "netops_rule_agent_loop_attempts",
        ["job_id"],
    )
    op.create_index(
        "ix_netops_rule_agent_loop_attempts_stage",
        "netops_rule_agent_loop_attempts",
        ["stage"],
    )
    op.create_index(
        "ix_netops_rule_agent_loop_attempts_model_name",
        "netops_rule_agent_loop_attempts",
        ["model_name"],
    )
    op.create_index(
        "ix_netops_rule_agent_loop_attempts_success",
        "netops_rule_agent_loop_attempts",
        ["success"],
    )
    op.create_index(
        "ix_netops_rule_agent_loop_attempts_updated_at",
        "netops_rule_agent_loop_attempts",
        ["updated_at"],
    )
    op.create_index(
        "ix_rule_agent_loop_job_ordinal",
        "netops_rule_agent_loop_attempts",
        ["job_id", "attempt_ordinal"],
    )
    op.create_index(
        "ix_rule_agent_loop_stage_success",
        "netops_rule_agent_loop_attempts",
        ["stage", "success"],
    )
    op.create_index(
        "ix_rule_agent_loop_updated_job",
        "netops_rule_agent_loop_attempts",
        ["updated_at", "job_id"],
    )

    _backfill(op.get_bind())


def downgrade() -> None:
    op.drop_table("netops_rule_agent_loop_attempts")
    op.drop_table("netops_rule_approval_audit")
    op.drop_table("netops_rule_replay_reports")

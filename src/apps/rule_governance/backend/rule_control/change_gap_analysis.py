"""Analyze production no-event evidence without triggering rule generation."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from src.core.patrol.event_builder import no_event_reason
from src.core.patrol.event_rules import EventRuleRepository
from src.infrastructure.db.models import NetworkChangeRecord
from src.infrastructure.db.postgres import get_db_session


def _change_dict(row: NetworkChangeRecord) -> dict[str, Any]:
    return {
        "change_id": row.id,
        "run_id": row.run_id,
        "device_id": row.device_id,
        "command": row.canonical_command,
        "entity_type": row.entity_type,
        "entity_key": row.entity_key,
        "field": row.field,
        "observation_type": row.observation_type,
        "old": row.old_value_json,
        "new": row.new_value_json,
        "change_type": row.change_type,
        "decision_reason": row.decision_reason,
        "event_status": row.event_status,
        "vendor": (row.dimensions_json or {}).get("vendor")
        if isinstance(row.dimensions_json, dict)
        else None,
    }


def _dataset_ids_for_runs(db, run_ids: list[str], *, limit: int = 5) -> list[str]:
    """Map change run_ids back to their patrol dataset_ids (newest first)."""
    if not run_ids:
        return []
    from src.infrastructure.db.models import PatrolRun

    rows = (
        db.query(PatrolRun.id, PatrolRun.dataset_id)
        .filter(PatrolRun.id.in_(run_ids), PatrolRun.dataset_id.isnot(None))
        .all()
    )
    by_run = {str(run_id): str(dataset_id) for run_id, dataset_id in rows if dataset_id}
    ordered: list[str] = []
    for run_id in run_ids:
        dataset_id = by_run.get(str(run_id))
        if dataset_id and dataset_id not in ordered:
            ordered.append(dataset_id)
        if len(ordered) >= limit:
            break
    return ordered


class ChangeGapAnalysisService:
    def analyze(
        self,
        *,
        days: int = 90,
        limit: int = 200,
    ) -> dict[str, Any]:
        since = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
        with get_db_session() as db:
            rows = (
                db.query(NetworkChangeRecord)
                .filter(
                    NetworkChangeRecord.event_status == "no_event",
                    NetworkChangeRecord.detected_at >= since,
                )
                .order_by(NetworkChangeRecord.detected_at.desc())
                .limit(max(1, min(int(limit), 2000)))
                .all()
            )
            changes = [_change_dict(row) for row in rows]
            # Backfill dataset candidates per observation type: the runs that
            # produced these changes are known, so seeded jobs can auto-select
            # datasets instead of requiring a manual pick.
            run_ids_by_observation: dict[str, list[str]] = {}
            for change in changes:
                observation = str(
                    change.get("observation_type") or change.get("entity_type") or "unknown"
                )
                run_id = str(change.get("run_id") or "").strip()
                if run_id and run_id not in run_ids_by_observation.setdefault(observation, []):
                    run_ids_by_observation[observation].append(run_id)
            dataset_ids_by_observation = {
                observation: _dataset_ids_for_runs(db, run_ids)
                for observation, run_ids in run_ids_by_observation.items()
            }
        active_rule_count = len(EventRuleRepository().snapshot_active_rules())
        by_reason = Counter(
            no_event_reason(change, active_rule_count=active_rule_count) for change in changes
        )
        by_field = Counter(str(change.get("field") or "unknown") for change in changes)
        by_command = Counter(str(change.get("command") or "unknown") for change in changes)
        by_observation = Counter(
            str(change.get("observation_type") or change.get("entity_type") or "unknown")
            for change in changes
        )
        gaps: list[dict[str, Any]] = []
        for observation_type, count in by_observation.most_common(50):
            reason_for_type = Counter(
                no_event_reason(change, active_rule_count=active_rule_count)
                for change in changes
                if str(change.get("observation_type") or change.get("entity_type") or "unknown")
                == observation_type
            )
            top_reason = reason_for_type.most_common(1)[0][0] if reason_for_type else "unknown"
            gap_kind = (
                "missing_event_rules"
                if top_reason == "no_active_event_rules"
                else "unmatched_event_rules"
                if top_reason == "active_rules_evaluated_no_match"
                else "policy_or_threshold"
            )
            sample = next(
                (
                    change
                    for change in changes
                    if str(change.get("observation_type") or change.get("entity_type") or "unknown")
                    == observation_type
                ),
                {},
            )
            gaps.append(
                {
                    "observation_type": observation_type,
                    "change_count": count,
                    "gap_kind": gap_kind,
                    "top_reason": top_reason,
                    "command": sample.get("command"),
                    "field": sample.get("field"),
                    "vendor": sample.get("vendor"),
                    "dataset_ids": list(dataset_ids_by_observation.get(observation_type) or []),
                    "suggested_job_type": (
                        "EVENT_RULE"
                        if gap_kind in {"missing_event_rules", "unmatched_event_rules"}
                        else "CHANGE_POLICY"
                    ),
                    "source_prefill": {
                        "action": "rule-gap-source",
                        "job_type": "parser_validation",
                        "command": sample.get("command"),
                        "vendor": sample.get("vendor"),
                        "observation_type": observation_type,
                        "suggested_job_type": (
                            "EVENT_RULE"
                            if gap_kind
                            in {"missing_event_rules", "unmatched_event_rules"}
                            else "CHANGE_POLICY"
                        ),
                    },
                }
            )
        return {
            "success": True,
            "days": days,
            "change_count": len(changes),
            "active_rule_count": active_rule_count,
            "by_reason": dict(by_reason.most_common()),
            "by_field": dict(by_field.most_common(20)),
            "by_command": dict(by_command.most_common(20)),
            "by_observation_type": dict(by_observation.most_common(20)),
            "gaps": gaps,
        }

    def seed_jobs(
        self,
        *,
        days: int = 90,
        limit: int = 20,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        del days, limit, created_by
        raise ValueError(
            "production Change gaps cannot create rule-generation jobs; "
            "import explicit offline evidence in Parser control first"
        )

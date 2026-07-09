"""PostgreSQL repository for LLM-assisted rule generation jobs."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from src.core.rule_control.jobs import (
    RULE_GENERATION_JOB_TYPES,
    RULE_GENERATION_STATUSES,
    JOB_TRANSITIONS,
    RuleGenerationJobStateError,
)
from src.infrastructure.db.models import RuleGenerationJobRecord
from src.infrastructure.db.postgres import get_db_session


TERMINAL_RULE_GENERATION_STATUSES = {"active", "rejected", "deprecated"}
PRIVATE_OR_LARGE_PAYLOAD_KEYS = {
    "command_output",
    "output",
    "raw",
    "raw_output",
    "raw_text",
    "records",
    "sample",
    "samples",
    "secret",
    "structured_records",
    "text_output",
    "token",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RuleGenerationJobRepository:
    def create_job(
        self,
        *,
        job_type: str,
        source_ref: str | None = None,
        asset_ref: str | None = None,
        asset_type: str | None = None,
        asset_id: str | None = None,
        asset_version_id: str | None = None,
        asset_digest: str | None = None,
        payload: dict[str, Any] | None = None,
        validation_report: dict[str, Any] | None = None,
        approval_required: bool = True,
        created_by: str | None = None,
        status: str = "draft",
    ) -> dict[str, Any]:
        if job_type not in RULE_GENERATION_JOB_TYPES:
            raise ValueError(f"unsupported rule generation job_type: {job_type}")
        if status not in RULE_GENERATION_STATUSES:
            raise ValueError(f"unsupported rule generation status: {status}")
        if status != "draft":
            raise ValueError("rule generation jobs must be created as draft")
        now = utcnow()
        row = RuleGenerationJobRecord(
            id=str(uuid.uuid4()),
            job_type=job_type,
            status=status,
            source_ref=source_ref,
            asset_ref=asset_ref,
            asset_type=asset_type,
            asset_id=asset_id,
            asset_version_id=asset_version_id,
            asset_digest=asset_digest,
            payload_json=dict(payload or {}),
            validation_report_json=validation_report,
            approval_required=bool(approval_required),
            created_by=created_by,
            created_at=now,
            updated_at=now,
            completed_at=now if status in TERMINAL_RULE_GENERATION_STATUSES else None,
        )
        with get_db_session() as db:
            db.add(row)
            db.flush()
            return row.to_dict()

    def list_jobs(
        self,
        *,
        job_type: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = db.query(RuleGenerationJobRecord)
            if job_type:
                query = query.filter(RuleGenerationJobRecord.job_type == job_type)
            if status:
                query = query.filter(RuleGenerationJobRecord.status == status)
            rows = (
                query.order_by(RuleGenerationJobRecord.updated_at.desc())
                .limit(max(1, min(int(limit), 1000)))
                .all()
            )
            return [row.to_dict() for row in rows]

    def list_replay_reports(
        self,
        *,
        success: bool | None = None,
        asset_type: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = db.query(RuleGenerationJobRecord)
            if asset_type:
                query = query.filter(RuleGenerationJobRecord.asset_type == asset_type)
            rows = (
                query.order_by(RuleGenerationJobRecord.updated_at.desc())
                .limit(max(1, min(int(limit) * 5, 5000)))
                .all()
            )

            reports: list[dict[str, Any]] = []
            for row in rows:
                replay = self._extract_historical_replay(row.validation_report_json)
                if not replay:
                    continue
                replay_success = replay.get("success")
                if success is not None and bool(replay_success) is not success:
                    continue
                reports.append(
                    {
                        "id": f"{row.id}:historical_replay",
                        "job_id": row.id,
                        "job_type": row.job_type,
                        "job_status": row.status,
                        "asset_type": row.asset_type,
                        "asset_id": row.asset_id,
                        "asset_version_id": row.asset_version_id,
                        "asset_digest": row.asset_digest,
                        "source_ref": row.source_ref,
                        "created_by": row.created_by,
                        "created_at": row.created_at.isoformat()
                        if row.created_at
                        else None,
                        "updated_at": row.updated_at.isoformat()
                        if row.updated_at
                        else None,
                        "success": replay_success,
                        "sampled_change_count": replay.get(
                            "sampled_change_count", 0
                        ),
                        "matched_change_count": replay.get("matched_change_count", 0),
                        "estimated_daily_event_count": replay.get(
                            "estimated_daily_event_count", 0
                        ),
                        "collision_count": replay.get("collision_count", 0),
                        "event_types": replay.get("event_types") or {},
                        "matched_entity_types": replay.get("matched_entity_types")
                        or {},
                        "top_devices": replay.get("top_devices") or {},
                        "collision_rules": replay.get("collision_rules") or {},
                        "unexpected_matches": replay.get("unexpected_matches") or [],
                        "recovery_pair_coverage": replay.get(
                            "recovery_pair_coverage"
                        ),
                        "report_json": replay,
                    }
                )
                if len(reports) >= max(1, min(int(limit), 1000)):
                    break
            return reports

    def list_approval_audit(
        self,
        *,
        action: str | None = None,
        asset_type: str | None = None,
        actor: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        expected_actions = {"approved", "rejected", "deprecated"}
        if action and action not in expected_actions:
            raise ValueError(f"unsupported approval audit action: {action}")
        with get_db_session() as db:
            query = db.query(RuleGenerationJobRecord)
            if asset_type:
                query = query.filter(RuleGenerationJobRecord.asset_type == asset_type)
            rows = (
                query.order_by(RuleGenerationJobRecord.updated_at.desc())
                .limit(max(1, min(int(limit) * 5, 5000)))
                .all()
            )

            events: list[dict[str, Any]] = []
            for row in rows:
                for event in self._approval_events_from_job(row):
                    if action and event["action"] != action:
                        continue
                    if actor and event.get("actor") != actor:
                        continue
                    events.append(event)
            events.sort(key=lambda item: str(item.get("occurred_at") or ""), reverse=True)
            return events[: max(1, min(int(limit), 1000))]

    def list_agent_loop_attempts(
        self,
        *,
        asset_type: str | None = None,
        stage: str | None = None,
        success: bool | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = db.query(RuleGenerationJobRecord)
            if asset_type:
                query = query.filter(RuleGenerationJobRecord.asset_type == asset_type)
            rows = (
                query.order_by(RuleGenerationJobRecord.updated_at.desc())
                .limit(max(1, min(int(limit) * 5, 5000)))
                .all()
            )

            attempts: list[dict[str, Any]] = []
            for row in rows:
                for item in self._agent_loop_attempts_from_job(row):
                    if stage and item.get("stage") != stage:
                        continue
                    if success is not None and bool(item.get("success")) is not success:
                        continue
                    attempts.append(item)
            attempts.sort(
                key=lambda item: (
                    str(item.get("updated_at") or ""),
                    int(item.get("attempt") or 0),
                ),
                reverse=True,
            )
            return attempts[: max(1, min(int(limit), 1000))]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with get_db_session() as db:
            row = db.get(RuleGenerationJobRecord, job_id)
            return row.to_dict() if row else None

    def find_job(
        self,
        *,
        job_type: str,
        source_ref: str,
        status_not_in: set[str] | None = None,
    ) -> dict[str, Any] | None:
        with get_db_session() as db:
            query = db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.job_type == job_type,
                RuleGenerationJobRecord.source_ref == source_ref,
            )
            if status_not_in:
                query = query.filter(~RuleGenerationJobRecord.status.in_(status_not_in))
            row = query.order_by(RuleGenerationJobRecord.created_at.desc()).first()
            return row.to_dict() if row else None

    def preview_job(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job is None:
            raise KeyError("rule generation job not found")
        report = (
            job.get("validation_report_json")
            if isinstance(job.get("validation_report_json"), dict)
            else {}
        )
        payload = (
            job.get("payload_json")
            if isinstance(job.get("payload_json"), dict)
            else {}
        )
        replay = self._extract_historical_replay(report)
        agent_loop = self._first_agent_loop_report(report)
        candidate = self._extract_candidate_preview(payload, report)
        blockers = self._approval_blockers(job, report)
        return {
            "success": True,
            "job": job,
            "asset_summary": {
                "asset_type": job.get("asset_type") or job.get("job_type"),
                "asset_ref": job.get("asset_ref"),
                "asset_id": job.get("asset_id"),
                "asset_version_id": job.get("asset_version_id"),
                "asset_digest": job.get("asset_digest"),
                "source_ref": job.get("source_ref"),
            },
            "candidate_preview": candidate,
            "payload_summary": self._safe_payload_summary(payload),
            "validation_summary": self._validation_summary(report),
            "historical_replay": self._replay_summary(replay),
            "agent_loop": self._agent_loop_summary(agent_loop),
            "approval_readiness": {
                "can_approve": not blockers,
                "blockers": blockers,
            },
            "validation_report": self._safe_json(report),
        }

    def transition_job(
        self,
        job_id: str,
        *,
        status: str,
        validation_report: dict[str, Any] | None = None,
        asset_ref: str | None = None,
        asset_type: str | None = None,
        asset_id: str | None = None,
        asset_version_id: str | None = None,
        asset_digest: str | None = None,
    ) -> dict[str, Any]:
        if status not in RULE_GENERATION_STATUSES:
            raise RuleGenerationJobStateError(f"unsupported target status: {status}")
        with get_db_session() as db:
            row = db.get(RuleGenerationJobRecord, job_id)
            if row is None:
                raise KeyError("rule generation job not found")
            allowed = JOB_TRANSITIONS.get(str(row.status), set())
            if status not in allowed:
                raise RuleGenerationJobStateError(
                    f"invalid rule generation transition: {row.status} -> {status}"
                )
            row.status = status
            row.updated_at = utcnow()
            if validation_report is not None:
                existing_report = dict(row.validation_report_json or {})
                if existing_report:
                    governance = dict(existing_report.get("governance") or {})
                    governance.update(validation_report)
                    existing_report["governance"] = governance
                    row.validation_report_json = existing_report
                else:
                    row.validation_report_json = validation_report
            if asset_ref is not None:
                row.asset_ref = asset_ref
            if asset_type is not None:
                row.asset_type = asset_type
            if asset_id is not None:
                row.asset_id = asset_id
            if asset_version_id is not None:
                row.asset_version_id = asset_version_id
            if asset_digest is not None:
                row.asset_digest = asset_digest
            if status in TERMINAL_RULE_GENERATION_STATUSES:
                row.completed_at = row.updated_at
            db.flush()
            return row.to_dict()

    def attach_asset(
        self,
        job_id: str,
        *,
        asset_type: str,
        asset_id: str,
        asset_version_id: str,
        asset_digest: str | None = None,
        asset_ref: str | None = None,
    ) -> dict[str, Any]:
        with get_db_session() as db:
            row = db.get(RuleGenerationJobRecord, job_id)
            if row is None:
                raise KeyError("rule generation job not found")
            row.asset_type = asset_type
            row.asset_id = asset_id
            row.asset_version_id = asset_version_id
            row.asset_digest = asset_digest
            row.asset_ref = asset_ref or f"{asset_type.lower()}:{asset_version_id}"
            row.updated_at = utcnow()
            db.flush()
            return row.to_dict()

    def attach_validation_report(
        self,
        job_id: str,
        *,
        validation_report: dict[str, Any],
    ) -> dict[str, Any]:
        with get_db_session() as db:
            row = db.get(RuleGenerationJobRecord, job_id)
            if row is None:
                raise KeyError("rule generation job not found")
            row.validation_report_json = validation_report
            row.updated_at = utcnow()
            db.flush()
            return row.to_dict()

    @classmethod
    def _approval_blockers(
        cls,
        job: dict[str, Any],
        validation_report: dict[str, Any],
    ) -> list[str]:
        blockers: list[str] = []
        if job.get("status") != "pending_approval":
            blockers.append("job is not pending approval")
        if not (job.get("asset_version_id") or job.get("asset_ref")):
            blockers.append("asset reference is missing")
        if validation_report.get("success") is not True:
            blockers.append("validation has not succeeded")
        replay = cls._extract_historical_replay(validation_report)
        if replay and replay.get("success") is False:
            blockers.append("historical replay failed")
        return blockers

    @classmethod
    def _validation_summary(cls, report: dict[str, Any]) -> dict[str, Any]:
        synthetic = (
            report.get("synthetic_validation")
            if isinstance(report.get("synthetic_validation"), dict)
            else {}
        )
        validation = (
            report.get("validation")
            if isinstance(report.get("validation"), dict)
            else {}
        )
        errors = report.get("errors")
        if not isinstance(errors, list):
            errors = validation.get("errors") if isinstance(validation.get("errors"), list) else []
        return {
            "success": report.get("success"),
            "generator": report.get("generator"),
            "synthetic_success": synthetic.get("success"),
            "case_count": synthetic.get("case_count") or validation.get("case_count"),
            "error_count": len(errors),
            "errors": errors[:10],
        }

    @staticmethod
    def _replay_summary(replay: dict[str, Any] | None) -> dict[str, Any] | None:
        if not replay:
            return None
        return {
            "success": replay.get("success"),
            "sampled_change_count": replay.get("sampled_change_count", 0),
            "matched_change_count": replay.get("matched_change_count", 0),
            "collision_count": replay.get("collision_count", 0),
            "estimated_daily_event_count": replay.get(
                "estimated_daily_event_count", 0
            ),
            "event_types": replay.get("event_types") or {},
            "matched_entity_types": replay.get("matched_entity_types") or {},
            "recovery_pair_coverage": replay.get("recovery_pair_coverage"),
        }

    @classmethod
    def _first_agent_loop_report(
        cls,
        validation_report: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        reports = cls._extract_agent_loop_reports(validation_report)
        return reports[0] if reports else None

    @staticmethod
    def _agent_loop_summary(agent_loop: dict[str, Any] | None) -> dict[str, Any] | None:
        if not agent_loop:
            return None
        attempts = agent_loop.get("attempts") if isinstance(agent_loop.get("attempts"), list) else []
        last_attempt = attempts[-1] if attempts and isinstance(attempts[-1], dict) else {}
        return {
            "ready_for_approval": agent_loop.get("ready_for_approval"),
            "next_action": agent_loop.get("next_action"),
            "attempt_count": agent_loop.get("attempt_count") or len(attempts),
            "last_stage": last_attempt.get("stage"),
            "last_success": last_attempt.get("success"),
            "revision_request": agent_loop.get("revision_request"),
        }

    @classmethod
    def _safe_payload_summary(cls, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "keys": sorted(str(key) for key in payload.keys()),
            "summary": cls._safe_json(payload, depth=0, max_depth=2),
        }

    @classmethod
    def _extract_candidate_preview(
        cls,
        payload: dict[str, Any],
        report: dict[str, Any],
    ) -> dict[str, Any]:
        result = report.get("skill_result") if isinstance(report.get("skill_result"), dict) else {}
        candidates: dict[str, Any] = {}
        for source_name, source in (("skill_result", result), ("payload", payload)):
            if not isinstance(source, dict):
                continue
            for key in (
                "candidate",
                "candidate_rule",
                "candidate_rules",
                "change_policy",
                "contract",
                "contract_proposal",
                "event_rules",
                "generated_templates",
                "policy",
                "proposal",
                "rule",
                "rules",
                "template",
            ):
                if key in source:
                    candidates[f"{source_name}.{key}"] = cls._safe_json(source[key])
        return candidates

    @classmethod
    def _safe_json(
        cls,
        value: Any,
        *,
        depth: int = 0,
        max_depth: int = 4,
    ) -> Any:
        if depth > max_depth:
            return {"type": type(value).__name__, "truncated": True}
        if isinstance(value, dict):
            safe: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if key_text.lower() in PRIVATE_OR_LARGE_PAYLOAD_KEYS:
                    safe[key_text] = {
                        "redacted": True,
                        "reason": "private_or_large_payload",
                    }
                else:
                    safe[key_text] = cls._safe_json(
                        item,
                        depth=depth + 1,
                        max_depth=max_depth,
                    )
            return safe
        if isinstance(value, list):
            return [
                cls._safe_json(item, depth=depth + 1, max_depth=max_depth)
                for item in value[:20]
            ] + (
                [{"truncated": True, "remaining": len(value) - 20}]
                if len(value) > 20
                else []
            )
        if isinstance(value, str) and len(value) > 500:
            return {"type": "string", "length": len(value), "truncated": True}
        return value

    def approve_job(self, job_id: str, *, approved_by: str | None = None) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job is None:
            raise KeyError("rule generation job not found")
        if not (job.get("asset_version_id") or job.get("asset_ref")):
            raise RuleGenerationJobStateError(
                "rule asset cannot be approved without asset_ref or asset_version_id"
            )
        validation_report = dict(job.get("validation_report_json") or {})
        if validation_report.get("success") is not True:
            raise RuleGenerationJobStateError(
                "rule asset cannot be approved without successful validation"
            )
        return self.transition_job(
            job_id,
            status="active",
            validation_report={"approved_by": approved_by} if approved_by else None,
        )

    def reject_job(
        self,
        job_id: str,
        *,
        rejected_by: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        report = {
            key: value
            for key, value in {
                "rejected_by": rejected_by,
                "reason": reason,
            }.items()
            if value
        }
        return self.transition_job(
            job_id,
            status="rejected",
            validation_report=report or None,
        )

    def deprecate_job(
        self,
        job_id: str,
        *,
        deprecated_by: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        report = {
            key: value
            for key, value in {
                "deprecated_by": deprecated_by,
                "reason": reason,
            }.items()
            if value
        }
        return self.transition_job(
            job_id,
            status="deprecated",
            validation_report=report or None,
        )

    @staticmethod
    def _extract_historical_replay(
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

    @staticmethod
    def _approval_events_from_job(row: RuleGenerationJobRecord) -> list[dict[str, Any]]:
        report = row.validation_report_json if isinstance(row.validation_report_json, dict) else {}
        governance = report.get("governance") if isinstance(report, dict) else {}
        if not isinstance(governance, dict):
            return []
        mapping = [
            ("approved", "approved_by", "approved_at"),
            ("rejected", "rejected_by", "rejected_at"),
            ("deprecated", "deprecated_by", "deprecated_at"),
        ]
        events: list[dict[str, Any]] = []
        for action, actor_key, time_key in mapping:
            actor = governance.get(actor_key)
            occurred_at = governance.get(time_key)
            if not actor and not occurred_at:
                continue
            events.append(
                {
                    "id": f"{row.id}:{action}",
                    "job_id": row.id,
                    "job_type": row.job_type,
                    "job_status": row.status,
                    "action": action,
                    "actor": actor,
                    "reason": governance.get("reason")
                    if action in {"rejected", "deprecated"}
                    else None,
                    "occurred_at": occurred_at,
                    "asset_type": row.asset_type,
                    "asset_id": row.asset_id,
                    "asset_version_id": row.asset_version_id,
                    "asset_ref": row.asset_ref,
                    "asset_digest": row.asset_digest,
                    "source_ref": row.source_ref,
                    "validation_success": report.get("success"),
                    "synthetic_success": (
                        report.get("synthetic_validation", {}).get("success")
                        if isinstance(report.get("synthetic_validation"), dict)
                        else None
                    ),
                    "replay_success": (
                        report.get("historical_replay", {}).get("success")
                        if isinstance(report.get("historical_replay"), dict)
                        else None
                    ),
                    "created_by": row.created_by,
                    "created_at": row.created_at.isoformat()
                    if row.created_at
                    else None,
                    "updated_at": row.updated_at.isoformat()
                    if row.updated_at
                    else None,
                    "governance": governance,
                }
            )
        return events

    @staticmethod
    def _extract_agent_loop_reports(
        validation_report: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not isinstance(validation_report, dict):
            return []
        loops: list[dict[str, Any]] = []
        for candidate in [
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
        ]:
            if isinstance(candidate, dict):
                loops.append(candidate)
        return loops

    @classmethod
    def _agent_loop_attempts_from_job(
        cls,
        row: RuleGenerationJobRecord,
    ) -> list[dict[str, Any]]:
        report = row.validation_report_json if isinstance(row.validation_report_json, dict) else {}
        loops = cls._extract_agent_loop_reports(report)
        attempts: list[dict[str, Any]] = []
        for loop in loops:
            for attempt in loop.get("attempts") or []:
                if not isinstance(attempt, dict):
                    continue
                attempt_number = int(attempt.get("attempt") or len(attempts) + 1)
                attempts.append(
                    {
                        "id": f"{row.id}:agent_loop:{len(attempts) + 1}",
                        "job_id": row.id,
                        "job_type": row.job_type,
                        "job_status": row.status,
                        "asset_type": row.asset_type,
                        "asset_id": row.asset_id,
                        "asset_version_id": row.asset_version_id,
                        "asset_ref": row.asset_ref,
                        "asset_digest": row.asset_digest,
                        "source_ref": row.source_ref,
                        "attempt": attempt_number,
                        "stage": attempt.get("stage"),
                        "generator": attempt.get("generator"),
                        "mode": attempt.get("mode"),
                        "success": attempt.get("success"),
                        "input_summary": attempt.get("input_summary") or {},
                        "output_summary": attempt.get("output_summary") or {},
                        "validator_feedback": attempt.get("validator_feedback") or {},
                        "revision_request": attempt.get("revision_request")
                        if isinstance(attempt.get("revision_request"), dict)
                        else (
                            loop.get("revision_request")
                            if isinstance(loop.get("revision_request"), dict)
                            else None
                        ),
                        "ready_for_approval": loop.get("ready_for_approval"),
                        "next_action": loop.get("next_action"),
                        "created_by": row.created_by,
                        "created_at": row.created_at.isoformat()
                        if row.created_at
                        else None,
                        "updated_at": row.updated_at.isoformat()
                        if row.updated_at
                        else None,
                    }
                )
        return attempts

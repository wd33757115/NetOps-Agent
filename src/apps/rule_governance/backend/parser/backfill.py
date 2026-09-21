"""Explicit, dry-run-first historical Parser backfill orchestration."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_

from src.apps.rule_governance.backend.parser.identity import DeviceIdentity
from src.apps.rule_governance.backend.parser.repository import (
    ParserParseResultRepository,
    ParserTemplateRepository,
)
from src.apps.rule_governance.backend.parser.runtime import ParserRuntime
from src.common.config import get_settings
from src.core.patrol.change_detector import detect_historical_backfill_changes
from src.core.patrol.handlers import BuildEventsHandler
from src.core.state.normalizer import observations_from_snapshot
from src.core.state.repository import StateRepository
from src.infrastructure.db.models import (
    CommandSnapshot,
    OfflinePatrolPeriodRecord,
    ParserBackfillJobRecord,
    ParserBackfillResultRecord,
    ParserTemplate,
    PatrolRun,
    StateObservationRecord,
)
from src.infrastructure.db.postgres import get_db_session

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _TemplateRegistry:
    def __init__(self, template: ParserTemplate) -> None:
        self.template = template

    def resolve_active(self, **_kwargs: Any) -> ParserTemplate:
        return self.template


def _canonical_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
    value = dict(filters or {})
    run_modes = sorted(
        {
            str(item).strip().lower()
            for item in value.get("run_modes") or ["production"]
            if str(item).strip()
        }
    )
    unsupported_modes = sorted(set(run_modes) - {"production", "parser_validation"})
    if unsupported_modes:
        raise ValueError(
            "unsupported Parser backfill run modes: " + ", ".join(unsupported_modes)
        )
    return {
        "device_ids": sorted({str(item) for item in value.get("device_ids") or []}),
        "dataset_ids": sorted({str(item) for item in value.get("dataset_ids") or []}),
        "run_ids": sorted({str(item) for item in value.get("run_ids") or []}),
        "run_modes": run_modes,
        "observed_from": value.get("observed_from"),
        "observed_to": value.get("observed_to"),
        "limit": min(max(int(value.get("limit") or 1000), 1), 5000),
    }


def _as_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _historical_analysis_pairs(
    run_ids: set[str],
) -> list[tuple[str, str | None]]:
    """Choose production runs and explicit offline baseline/current comparisons."""
    if not run_ids:
        return []
    with get_db_session() as db:
        rows = (
            db.query(PatrolRun)
            .filter(PatrolRun.id.in_(sorted(run_ids)))
            .order_by(PatrolRun.started_at, PatrolRun.id)
            .all()
        )
        production_pairs = [
            (str(row.id), None) for row in rows if str(row.mode) == "production"
        ]
        offline_groups: dict[str, dict[str, list[PatrolRun]]] = {}
        for row in rows:
            if str(row.mode) != "parser_validation":
                continue
            metadata = dict(row.metadata_json or {})
            offline_job_id = str(metadata.get("offline_job_id") or "").strip()
            period_role = str(metadata.get("period_role") or "").strip().lower()
            if not offline_job_id or period_role not in {"baseline", "current"}:
                continue
            offline_groups.setdefault(
                offline_job_id,
                {"baseline": [], "current": []},
            )[period_role].append(row)

        offline_pairs: list[tuple[str, str | None]] = []
        for group in offline_groups.values():
            baselines = sorted(
                group["baseline"],
                key=lambda row: (row.started_at or row.created_at, row.id),
            )
            currents = sorted(
                group["current"],
                key=lambda row: (row.started_at or row.created_at, row.id),
            )
            for current in currents:
                current_at = current.started_at or current.created_at
                eligible = [
                    baseline
                    for baseline in baselines
                    if (baseline.started_at or baseline.created_at) < current_at
                ]
                if eligible:
                    offline_pairs.append((str(current.id), str(eligible[-1].id)))
        return sorted(
            {*production_pairs, *offline_pairs},
            key=lambda item: (item[0], item[1] or ""),
        )


def _refresh_rule_asset_command_units(template_id: str) -> int:
    """Refresh materialized command units after append-only parse results change."""
    try:
        from src.apps.rule_governance.backend.rule_control.command_asset_units import (
            RuleAssetCommandUnitService,
        )

        result = RuleAssetCommandUnitService().reconcile_for_parser_template(template_id)
        return int(result.get("reconciled_count") or 0)
    except Exception:
        logger.exception(
            "Rule asset command unit refresh failed after Parser backfill template_id=%s",
            template_id,
        )
        return 0


class ParserBackfillService:
    def create_job(
        self,
        *,
        template_id: str,
        requested_by: str,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not str(requested_by).strip():
            raise ValueError("backfill job requires requested_by")
        canonical_filters = _canonical_filters(filters)
        encoded = json.dumps(
            [template_id, canonical_filters],
            sort_keys=True,
            separators=(",", ":"),
        )
        idempotency_key = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with get_db_session() as db:
            template = db.get(ParserTemplate, template_id)
            if template is None or template.status != "active":
                raise ValueError("backfill requires an active Parser template")
            existing = (
                db.query(ParserBackfillJobRecord)
                .filter(ParserBackfillJobRecord.idempotency_key == idempotency_key)
                .first()
            )
            if existing is not None:
                return existing.to_dict()
            row = ParserBackfillJobRecord(
                id=str(uuid.uuid4()),
                idempotency_key=idempotency_key,
                template_id=template_id,
                status="draft",
                dry_run=True,
                filters_json=canonical_filters,
                requested_by=str(requested_by).strip(),
                created_at=_now(),
                updated_at=_now(),
            )
            db.add(row)
            db.flush()
            return row.to_dict()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with get_db_session() as db:
            job = db.get(ParserBackfillJobRecord, job_id)
            if job is None:
                return None
            result = job.to_dict()
            result["results"] = [
                row.to_dict()
                for row in db.query(ParserBackfillResultRecord)
                .filter(ParserBackfillResultRecord.job_id == job_id)
                .order_by(ParserBackfillResultRecord.created_at)
                .all()
            ]
            return result

    def list_jobs(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = db.query(ParserBackfillJobRecord)
            if status:
                query = query.filter(ParserBackfillJobRecord.status == status)
            return [
                row.to_dict()
                for row in query.order_by(ParserBackfillJobRecord.created_at.desc()).all()
            ]

    def list_jobs_page(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        page_size = max(1, min(int(limit), 200))
        page_offset = max(0, int(offset))
        with get_db_session() as db:
            query = db.query(ParserBackfillJobRecord)
            if status:
                query = query.filter(ParserBackfillJobRecord.status == status)
            pattern = f"%{str(search).strip()}%" if str(search or "").strip() else None
            if pattern:
                query = query.filter(
                    or_(
                        ParserBackfillJobRecord.id.ilike(pattern),
                        ParserBackfillJobRecord.template_id.ilike(pattern),
                        ParserBackfillJobRecord.requested_by.ilike(pattern),
                    )
                )
            total = query.order_by(None).count()
            rows = (
                query.order_by(
                    ParserBackfillJobRecord.created_at.desc(),
                    ParserBackfillJobRecord.id.desc(),
                )
                .offset(page_offset)
                .limit(page_size)
                .all()
            )
            return {
                "items": [row.to_dict() for row in rows],
                "total": total,
                "limit": page_size,
                "offset": page_offset,
            }

    def status_counts(self) -> dict[str, int]:
        with get_db_session() as db:
            return {
                str(status): int(count)
                for status, count in db.query(
                    ParserBackfillJobRecord.status,
                    func.count(ParserBackfillJobRecord.id),
                )
                .group_by(ParserBackfillJobRecord.status)
                .all()
            }

    def execute_dry_run(self, job_id: str) -> dict[str, Any]:
        owner_token = uuid.uuid4().hex
        with get_db_session() as db:
            job = (
                db.query(ParserBackfillJobRecord)
                .filter(ParserBackfillJobRecord.id == job_id)
                .with_for_update()
                .one()
            )
            active_lease = (
                job.status == "running" and job.lease_until is not None and job.lease_until > _now()
            )
            if active_lease:
                raise ValueError("backfill dry-run is already running")
            if job.status not in {
                "draft",
                "validation_failed",
                "pending_approval",
                "running",
            }:
                raise ValueError(f"backfill dry-run cannot start from {job.status}")
            template = db.get(ParserTemplate, job.template_id)
            if template is None or template.status != "active":
                raise ValueError("backfill template is no longer active")
            job.status = "running"
            job.owner_token = owner_token
            job.lease_until = _now() + timedelta(minutes=10)
            job.started_at = _now()
            job.last_error = None
            job.updated_at = _now()
            db.query(ParserBackfillResultRecord).filter(
                ParserBackfillResultRecord.job_id == job_id
            ).delete(synchronize_session=False)
            template_id = template.id
            filters = dict(job.filters_json or {})

        snapshots = self._target_snapshots(template_id, filters)
        template = ParserTemplateRepository().get_template(template_id)
        if template is None:
            raise ValueError("backfill template disappeared")
        runtime = ParserRuntime(registry=_TemplateRegistry(template))
        rows: list[dict[str, Any]] = []
        for snapshot, run_mode in snapshots:
            identity = DeviceIdentity(
                vendor=template.vendor,
                model=template.model or template.profile,
                family=template.family,
                confidence=1.0,
                source="parser_backfill",
            )
            parsed = runtime.parse(
                identity=identity,
                command=snapshot.canonical_command,
                raw_text=snapshot.raw_text,
            )
            structured = parsed.structured_data
            parse_status = parsed.parser_status
            observation_count = 0
            failure_code = parsed.failure_code
            if parse_status in {"success", "empty"}:
                try:
                    observation_count = len(
                        self._observations(
                            snapshot,
                            template,
                            structured,
                            job_id=job_id,
                            mode=run_mode,
                        )
                    )
                except Exception as exc:
                    parse_status = "failed"
                    failure_code = f"normalization_preview:{type(exc).__name__}"
            with get_db_session() as db:
                existing_count = (
                    db.query(StateObservationRecord)
                    .filter(StateObservationRecord.snapshot_id == snapshot.id)
                    .count()
                )
                db.add(
                    ParserBackfillResultRecord(
                        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{job_id}|{snapshot.id}")),
                        job_id=job_id,
                        snapshot_id=snapshot.id,
                        parse_status=parse_status,
                        structured_json=structured,
                        failure_code=failure_code,
                        observation_count=observation_count,
                        existing_observation_count=existing_count,
                        created_at=_now(),
                        updated_at=_now(),
                    )
                )
            rows.append(
                {
                    "snapshot_id": snapshot.id,
                    "run_mode": run_mode,
                    "parse_status": parse_status,
                    "observation_count": observation_count,
                    "existing_observation_count": existing_count,
                    "failure_code": failure_code,
                }
            )
        failed_count = sum(item["parse_status"] not in {"success", "empty"} for item in rows)
        summary = {
            "snapshot_count": len(rows),
            "parsed_count": len(rows) - failed_count,
            "failed_count": failed_count,
            "observation_count": sum(item["observation_count"] for item in rows),
            "existing_observation_count": sum(item["existing_observation_count"] for item in rows),
            "current_state_updates": 0,
            "notifications": 0,
        }
        with get_db_session() as db:
            job = (
                db.query(ParserBackfillJobRecord)
                .filter(
                    ParserBackfillJobRecord.id == job_id,
                    ParserBackfillJobRecord.owner_token == owner_token,
                )
                .with_for_update()
                .one_or_none()
            )
            if job is None:
                raise ValueError("backfill dry-run owner was fenced")
            job.summary_json = summary
            final_status = "pending_approval" if rows and failed_count == 0 else "validation_failed"
            job.status = final_status
            job.completed_at = _now()
            job.owner_token = None
            job.lease_until = None
            job.updated_at = _now()
        return {
            "job_id": job_id,
            "status": final_status,
            "summary": summary,
            "results": rows,
        }

    def commit(
        self,
        job_id: str,
        *,
        actor: str,
        reason: str | None = None,
        analyze_changes: bool = False,
    ) -> dict[str, Any]:
        """Commit Parser evidence without implicitly running rule semantics.

        Change/Event analysis belongs to the separately approved historical
        replay flow. ``analyze_changes`` is retained for explicit legacy/admin
        callers and is intentionally disabled by default.
        """
        if not get_settings().PARSER_BACKFILL_ENABLED:
            raise ValueError("Parser backfill commit is disabled")
        if not str(actor).strip():
            raise ValueError("backfill commit requires actor")
        owner_token = uuid.uuid4().hex
        with get_db_session() as db:
            job = (
                db.query(ParserBackfillJobRecord)
                .filter(ParserBackfillJobRecord.id == job_id)
                .with_for_update()
                .one()
            )
            active_lease = (
                job.status == "committing"
                and job.lease_until is not None
                and job.lease_until > _now()
            )
            if active_lease:
                raise ValueError("backfill commit is already running")
            if job.status not in {"pending_approval", "committing"}:
                raise ValueError(f"backfill commit cannot start from {job.status}")
            results = (
                db.query(ParserBackfillResultRecord)
                .filter(ParserBackfillResultRecord.job_id == job_id)
                .order_by(ParserBackfillResultRecord.snapshot_id)
                .all()
            )
            if not results or any(
                result.parse_status not in {"success", "empty"} for result in results
            ):
                raise ValueError("backfill commit requires a fully passing dry-run")
            template_id = job.template_id
            job.status = "committing"
            job.owner_token = owner_token
            job.lease_until = _now() + timedelta(minutes=30)
            job.approved_by = str(actor).strip()
            job.approval_reason = str(reason or "").strip()
            job.updated_at = _now()
            result_payloads = [result.to_dict() for result in results]

        template = ParserTemplateRepository().get_template(template_id)
        if template is None or template.status != "active":
            raise ValueError("backfill template is no longer active")
        total_created = 0
        run_ids: set[str] = set()
        production_run_ids: set[str] = set()
        for item in result_payloads:
            with get_db_session() as db:
                snapshot_row = (
                    db.query(CommandSnapshot, PatrolRun.mode)
                    .join(PatrolRun, PatrolRun.id == CommandSnapshot.run_id)
                    .filter(CommandSnapshot.id == item["snapshot_id"])
                    .one_or_none()
                )
                if snapshot_row is None:
                    raise ValueError(f"backfill snapshot disappeared: {item['snapshot_id']}")
                snapshot, run_mode = snapshot_row
                db.expunge(snapshot)
            run_ids.add(snapshot.run_id)
            if str(run_mode) == "production":
                production_run_ids.add(snapshot.run_id)
            observations = self._observations(
                snapshot,
                template,
                item["structured_json"],
                job_id=job_id,
                mode=str(run_mode),
            )
            created, _ = StateRepository().save_observations(
                observations,
                update_current_state=False,
            )
            total_created += created
            ParserParseResultRepository().save_parse_result(
                snapshot_id=snapshot.id,
                template_id=template.id,
                template_version=template.version,
                status=item["parse_status"],
                structured_data=item["structured_json"],
                failure_code=item["failure_code"],
            )
            with get_db_session() as db:
                result = db.get(ParserBackfillResultRecord, item["id"])
                result.committed_observation_count = len(observations)
                result.committed_at = _now()
                result.updated_at = _now()
        projection_refresh_count = _refresh_rule_asset_command_units(template_id)
        change_count = 0
        event_count = 0
        analysis_pairs = _historical_analysis_pairs(run_ids) if analyze_changes else []
        if analyze_changes:
            for run_id, previous_run_id in analysis_pairs:
                change_result = detect_historical_backfill_changes(
                    run_id=run_id,
                    backfill_job_id=job_id,
                    previous_run_id=previous_run_id,
                )
                changes = list(change_result.get("changes") or [])
                change_count += len(changes)
                event_result = BuildEventsHandler().handle(
                    {
                        "run_id": run_id,
                        "changes": changes,
                        "persist": True,
                        "publish": False,
                        "analysis_revision": f"historical_backfill:{job_id}",
                        "_analysis_updates_run_status": False,
                    }
                )
                event_count += int(event_result.get("event_count") or 0)
        with get_db_session() as db:
            job = (
                db.query(ParserBackfillJobRecord)
                .filter(
                    ParserBackfillJobRecord.id == job_id,
                    ParserBackfillJobRecord.owner_token == owner_token,
                )
                .with_for_update()
                .one_or_none()
            )
            if job is None:
                raise ValueError("backfill commit owner was fenced")
            summary = {
                **dict(job.summary_json or {}),
                "created_observation_count": total_created,
                "current_state_updates": 0,
                "notifications": 0,
                "change_count": change_count,
                "event_count": event_count,
                "analysis_status": (
                    "completed" if analyze_changes else "not_requested"
                ),
                "production_run_count": len(production_run_ids),
                "offline_compare_run_count": sum(
                    previous_run_id is not None
                    for _, previous_run_id in analysis_pairs
                ),
                "rule_asset_projection_refresh_count": projection_refresh_count,
                "analysis_skipped_run_count": len(
                    run_ids - {run_id for run_id, _ in analysis_pairs}
                ),
            }
            job.summary_json = summary
            job.status = "completed"
            job.dry_run = False
            job.completed_at = _now()
            job.owner_token = None
            job.lease_until = None
            job.updated_at = _now()
        return {"job_id": job_id, "status": "completed", "summary": summary}

    @staticmethod
    def _target_snapshots(
        template_id: str,
        filters: dict[str, Any],
    ) -> list[tuple[CommandSnapshot, str]]:
        with get_db_session() as db:
            template = db.get(ParserTemplate, template_id)
            query = (
                db.query(CommandSnapshot, PatrolRun.mode)
                .join(PatrolRun, PatrolRun.id == CommandSnapshot.run_id)
                .outerjoin(
                    OfflinePatrolPeriodRecord,
                    OfflinePatrolPeriodRecord.dataset_id == CommandSnapshot.dataset_id,
                )
                .filter(
                    PatrolRun.mode.in_(filters.get("run_modes") or ["production"]),
                    or_(
                        OfflinePatrolPeriodRecord.id.is_(None),
                        and_(
                            OfflinePatrolPeriodRecord.status == "completed",
                            OfflinePatrolPeriodRecord.patrol_run_id.is_not(None),
                            CommandSnapshot.run_id
                            == OfflinePatrolPeriodRecord.patrol_run_id,
                        ),
                    ),
                    CommandSnapshot.canonical_command == template.canonical_command,
                    or_(
                        CommandSnapshot.parse_status.in_(
                            [
                                "template_missing",
                                "parse_failed",
                                "ignored",
                                "empty_unverified",
                                "identity_unknown",
                            ]
                        ),
                        and_(
                            CommandSnapshot.parse_status.in_(["parsed", "parse_empty"]),
                            or_(
                                CommandSnapshot.parser_template_id.is_(None),
                                CommandSnapshot.parser_template_id != template.id,
                            ),
                        ),
                    ),
                    CommandSnapshot.vendor == template.vendor,
                )
            )
            profile = template.model or template.profile
            if profile:
                query = query.filter(
                    or_(
                        CommandSnapshot.model == profile,
                        CommandSnapshot.model == template.model,
                    )
                )
            if filters.get("device_ids"):
                query = query.filter(
                    or_(
                        CommandSnapshot.device_id.in_(filters["device_ids"]),
                        CommandSnapshot.inventory_device_id.in_(filters["device_ids"]),
                    )
                )
            if filters.get("dataset_ids"):
                query = query.filter(CommandSnapshot.dataset_id.in_(filters["dataset_ids"]))
            if filters.get("run_ids"):
                query = query.filter(CommandSnapshot.run_id.in_(filters["run_ids"]))
            observed_from = _as_datetime(filters.get("observed_from"))
            observed_to = _as_datetime(filters.get("observed_to"))
            if observed_from:
                query = query.filter(CommandSnapshot.observed_at >= observed_from)
            if observed_to:
                query = query.filter(CommandSnapshot.observed_at <= observed_to)
            rows = (
                query.order_by(CommandSnapshot.observed_at, CommandSnapshot.id)
                .limit(int(filters.get("limit") or 1000))
                .all()
            )
            detached_rows: list[tuple[CommandSnapshot, str]] = []
            for snapshot, run_mode in rows:
                db.expunge(snapshot)
                detached_rows.append((snapshot, str(run_mode)))
            return detached_rows

    @staticmethod
    def _observations(
        snapshot: CommandSnapshot,
        template: ParserTemplate,
        structured: Any,
        *,
        job_id: str,
        mode: str,
    ):
        snapshot_dict = snapshot.to_dict()
        snapshot_dict.update(
            {
                "structured_json": structured,
                # The source snapshot is selected precisely because its old
                # Parser status is stale.  Once the approved backfill Parser
                # succeeds, normalize the newly extracted record set as
                # structured data rather than retaining the old
                # ``template_missing``/``parse_failed`` raw-hash path.
                "parse_status": "parsed" if structured else "parse_empty",
                "parser_template_id": template.id,
                "parser_version": template.version,
                "contract_version": template.contract_version,
                "schema_id": template.id,
                "metadata_json": {
                    **dict(snapshot.metadata_json or {}),
                    "historical_backfill": True,
                    "backfill_job_id": job_id,
                },
            }
        )
        return observations_from_snapshot(
            snapshot_dict,
            mode=mode,
            source="parser-backfill",
            dataset_id=snapshot.dataset_id,
        )

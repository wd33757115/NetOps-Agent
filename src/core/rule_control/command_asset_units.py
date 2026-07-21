"""Command-level read model for the rule asset production control plane."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import case, func, or_, select

from src.core.parser.policy import ParserPolicy
from src.core.parser.repository import parser_job_asset_key
from src.core.rule_control.source_scope import require_rule_asset_source_job
from src.core.rule_control.template_activation import RuleAssetTemplateActivationService
from src.infrastructure.db.models import (
    CommandSnapshot,
    ObservationContractRecord,
    OfflinePatrolPeriodRecord,
    ParserGenerationAttempt,
    ParserJobSample,
    ParserTemplate,
    ParserTemplateJob,
    RuleAssetCommandUnitRecord,
    RuleGenerationContextRecord,
    RuleGenerationJobRecord,
    SnapshotParseResult,
)
from src.infrastructure.db.postgres import get_db_session

PROJECTION_VERSION = 2
STAGE_ORDER = (
    "output_slicing",
    "identity_grouping",
    "parser_generation",
    "parser_validation",
    "structured_quality",
    "observation_contract",
    "change_policy",
    "event_rule",
    "validation_review",
    "release",
)
STAGE_LABELS = {
    "output_slicing": "命令输出切片",
    "identity_grouping": "身份识别与归组",
    "parser_generation": "Parser 生成",
    "parser_validation": "Parser 全样本验证",
    "structured_quality": "结构化数据质量",
    "observation_contract": "Observation Contract",
    "change_policy": "Change Policy",
    "event_rule": "Event Rule",
    "validation_review": "验证、回放与工程复核",
    "release": "审批发布",
}
RESOLVED_STAGE_STATUSES = {"passed", "skipped"}
ACTIVE_PARSER_JOB_STATUSES = {"collecting", "running", "validating"}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _iso(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _unit_id(offline_job_id: str, asset_key: str) -> str:
    digest = hashlib.sha256(f"{offline_job_id}|{asset_key}".encode()).hexdigest()
    return f"command-unit-{digest[:40]}"


def _action(code: str, label: str, description: str, target_url: str) -> dict[str, str]:
    return {
        "code": code,
        "label": label,
        "description": description,
        "target_url": target_url,
    }


def _stage(
    key: str,
    status: str,
    detail: str,
    *,
    metrics: dict[str, Any] | None = None,
    blockers: list[str] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    next_action: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": STAGE_LABELS[key],
        "status": status,
        "detail": detail,
        "metrics": metrics or {},
        "blockers": blockers or [],
        "artifacts": artifacts or [],
        "next_action": next_action,
    }


def _parser_url(command: str) -> str:
    return "/skills/parser?" + urlencode(
        {
            "workspace": "inbox",
            "subtab": "jobs",
            "search": command,
        }
    )


def _generation_url(
    offline_job_id: str,
    command: str,
    *,
    dataset_ids: list[str],
    seed_job_id: str | None = None,
) -> str:
    params = {
        "workspace": "generation",
        "subtab": "rule-generation",
        "source_job_id": offline_job_id,
        "offline_job_id": offline_job_id,
        "command": command,
    }
    if dataset_ids:
        params["dataset_ids"] = ",".join(dataset_ids)
    if seed_job_id:
        params["pc_rule_job_search"] = seed_job_id
    return "/skills/parser?" + urlencode(params)


def _backfill_url(
    offline_job_id: str,
    command: str,
    *,
    template_id: str | None,
    dataset_ids: list[str],
) -> str:
    params = {
        "workspace": "inbox",
        "subtab": "backfills",
        "source_job_id": offline_job_id,
        "offline_job_id": offline_job_id,
        "command": command,
        "open_backfill": "1",
    }
    if template_id:
        params["template_id"] = template_id
    if dataset_ids:
        params["dataset_ids"] = ",".join(dataset_ids)
        params["run_modes"] = "parser_validation"
    return "/skills/parser?" + urlencode(params)


def _bundle_url(offline_job_id: str, bundle_id: str | None = None) -> str:
    params = {
        "workspace": "release",
        "subtab": "asset-bundles",
        "source_job_id": offline_job_id,
        "offline_job_id": offline_job_id,
    }
    if bundle_id:
        params["bundle_id"] = bundle_id
    return "/skills/parser?" + urlencode(params)


def _child_stage_status(
    children: list[RuleGenerationJobRecord],
    asset_type: str,
    *,
    bundle_status: str | None,
) -> tuple[str, list[RuleGenerationJobRecord]]:
    matching = [item for item in children if item.asset_type == asset_type]
    if bundle_status == "active" and matching:
        return "passed", matching
    if not matching:
        return "not_started", []
    statuses = {str(item.status or "") for item in matching}
    if statuses.intersection({"failed", "rejected", "cancelled"}):
        return "blocked", matching
    if statuses.intersection({"running", "validating", "draft"}):
        return "running", matching
    if statuses == {"pending_approval"} and all(
        (item.validation_report_json or {}).get("success") is True for item in matching
    ):
        return "needs_review", matching
    return "blocked", matching


class RuleAssetCommandUnitService:
    """Reconcile and query one governed production flow per Parser asset identity."""

    def __init__(
        self,
        *,
        template_activation_service: RuleAssetTemplateActivationService | None = None,
    ) -> None:
        self.template_activation_service = (
            template_activation_service or RuleAssetTemplateActivationService()
        )

    def reconcile(self, offline_job_id: str) -> dict[str, Any]:
        policy = ParserPolicy()
        with get_db_session() as db:
            require_rule_asset_source_job(db, offline_job_id)
            periods = (
                db.query(OfflinePatrolPeriodRecord)
                .filter(OfflinePatrolPeriodRecord.job_id == offline_job_id)
                .all()
            )
            run_ids = [str(item.patrol_run_id) for item in periods if item.patrol_run_id]
            dataset_ids = sorted({str(item.dataset_id) for item in periods if item.dataset_id})
            if not run_ids:
                deleted = (
                    db.query(RuleAssetCommandUnitRecord)
                    .filter(RuleAssetCommandUnitRecord.offline_job_id == offline_job_id)
                    .delete(synchronize_session=False)
                )
                return {"offline_job_id": offline_job_id, "unit_count": 0, "deleted": deleted}

            target_snapshot_ids = select(CommandSnapshot.id).where(
                CommandSnapshot.run_id.in_(run_ids)
            )
            ranked_parse_results = (
                select(
                    SnapshotParseResult.snapshot_id.label("snapshot_id"),
                    SnapshotParseResult.template_id.label("template_id"),
                    SnapshotParseResult.parse_status.label("parse_status"),
                    SnapshotParseResult.structured_json.label("structured_json"),
                    func.row_number()
                    .over(
                        partition_by=SnapshotParseResult.snapshot_id,
                        order_by=(
                            SnapshotParseResult.parsed_at.desc(),
                            SnapshotParseResult.id.desc(),
                        ),
                    )
                    .label("result_rank"),
                )
                .where(
                    SnapshotParseResult.snapshot_id.in_(target_snapshot_ids),
                    SnapshotParseResult.parse_status.in_(["success", "empty"]),
                )
                .subquery()
            )
            latest_parse_result = (
                select(
                    ranked_parse_results.c.snapshot_id,
                    ranked_parse_results.c.template_id,
                    ranked_parse_results.c.parse_status,
                    ranked_parse_results.c.structured_json,
                )
                .where(ranked_parse_results.c.result_rank == 1)
                .subquery()
            )
            has_reparse = latest_parse_result.c.snapshot_id.is_not(None)
            effective_parse_status = case(
                (has_reparse, latest_parse_result.c.parse_status),
                else_=CommandSnapshot.parse_status,
            )
            effective_template_id = case(
                (has_reparse, latest_parse_result.c.template_id),
                else_=CommandSnapshot.parser_template_id,
            )
            effective_structured_json = case(
                (has_reparse, latest_parse_result.c.structured_json),
                else_=CommandSnapshot.structured_json,
            )
            structured_record_count = case(
                (
                    func.json_typeof(effective_structured_json) == "array",
                    func.json_array_length(effective_structured_json),
                ),
                (func.json_typeof(effective_structured_json) == "object", 1),
                else_=0,
            )
            status_rows = (
                db.query(
                    CommandSnapshot.vendor,
                    CommandSnapshot.model,
                    CommandSnapshot.canonical_command,
                    effective_parse_status,
                    effective_template_id,
                    func.count(CommandSnapshot.id),
                    func.sum(structured_record_count),
                )
                .outerjoin(
                    latest_parse_result,
                    latest_parse_result.c.snapshot_id == CommandSnapshot.id,
                )
                .filter(CommandSnapshot.run_id.in_(run_ids))
                .group_by(
                    CommandSnapshot.vendor,
                    CommandSnapshot.model,
                    CommandSnapshot.canonical_command,
                    effective_parse_status,
                    effective_template_id,
                )
                .all()
            )
            stable_device_key = func.coalesce(
                func.nullif(CommandSnapshot.inventory_device_id, ""),
                func.nullif(CommandSnapshot.management_ip, ""),
                func.nullif(func.lower(CommandSnapshot.device_name), ""),
                CommandSnapshot.device_id,
            )
            device_rows = (
                db.query(
                    CommandSnapshot.vendor,
                    CommandSnapshot.model,
                    CommandSnapshot.canonical_command,
                    stable_device_key,
                )
                .filter(CommandSnapshot.run_id.in_(run_ids))
                .distinct()
                .all()
            )

            base_groups: dict[tuple[str, str, str], dict[str, Any]] = {}
            for vendor, model, command, parse_status, template_id, count, structured in status_rows:
                key = (str(vendor or "Unknown"), str(model or "Unknown"), str(command or ""))
                group = base_groups.setdefault(
                    key,
                    {
                        "vendor": key[0],
                        "model": key[1],
                        "command": key[2],
                        "parse_counts": Counter(),
                        "structured_count": 0,
                        "template_ids": set(),
                        "device_keys": set(),
                    },
                )
                group["parse_counts"][str(parse_status or "unknown")] += int(count or 0)
                group["structured_count"] += int(structured or 0)
                if template_id:
                    group["template_ids"].add(str(template_id))
            for vendor, model, command, device_key in device_rows:
                key = (str(vendor or "Unknown"), str(model or "Unknown"), str(command or ""))
                if key in base_groups:
                    base_groups[key]["device_keys"].add(str(device_key or "unknown"))

            commands = {item[2] for item in base_groups}
            vendors = {item[0] for item in base_groups}
            referenced_template_ids = {
                template_id for item in base_groups.values() for template_id in item["template_ids"]
            }
            template_query = db.query(ParserTemplate).filter(
                ParserTemplate.canonical_command.in_(commands),
                func.lower(ParserTemplate.vendor).in_({_norm(item) for item in vendors}),
            )
            if referenced_template_ids:
                template_query = db.query(ParserTemplate).filter(
                    or_(
                        ParserTemplate.id.in_(referenced_template_ids),
                        (
                            ParserTemplate.canonical_command.in_(commands)
                            & func.lower(ParserTemplate.vendor).in_(
                                {_norm(item) for item in vendors}
                            )
                            & (ParserTemplate.status == "active")
                        ),
                    )
                )
            templates = template_query.all()
            templates_by_id = {str(item.id): item for item in templates}
            template_candidates: dict[tuple[str, str], list[ParserTemplate]] = defaultdict(list)
            for item in templates:
                template_candidates[(_norm(item.vendor), _norm(item.canonical_command))].append(
                    item
                )

            parser_jobs = (
                db.query(ParserTemplateJob)
                .filter(
                    ParserTemplateJob.canonical_command.in_(commands),
                    func.lower(ParserTemplateJob.vendor).in_({_norm(item) for item in vendors}),
                )
                .order_by(ParserTemplateJob.updated_at.desc(), ParserTemplateJob.id.desc())
                .all()
            )
            parser_job_candidates: dict[tuple[str, str], list[ParserTemplateJob]] = defaultdict(
                list
            )
            for item in parser_jobs:
                parser_job_candidates[(_norm(item.vendor), _norm(item.canonical_command))].append(
                    item
                )

            resolved_groups: dict[str, dict[str, Any]] = {}
            for group in base_groups.values():
                template = self._resolve_template(group, templates_by_id, template_candidates)
                parser_job = self._resolve_parser_job(group, parser_job_candidates)
                family = (
                    str(
                        (template.family if template else None)
                        or (parser_job.family if parser_job else None)
                        or ""
                    ).strip()
                    or None
                )
                decision = policy.decide(
                    vendor=group["vendor"],
                    model=group["model"],
                    family=family,
                    command=group["command"],
                )
                parse_total = sum(group["parse_counts"].values())
                is_not_applicable = bool(parse_total) and set(group["parse_counts"]).issubset(
                    {"not_applicable", "ignored"}
                )
                handling = "not_applicable" if is_not_applicable else decision.handling
                profile = str(
                    (template.profile if template else None)
                    or (parser_job.profile if parser_job else None)
                    or decision.template_profile
                    or group["model"]
                    or "Unknown"
                )
                asset_key = parser_job_asset_key(
                    vendor=group["vendor"],
                    profile=profile,
                    command=decision.canonical_command,
                )
                unit = resolved_groups.setdefault(
                    asset_key,
                    {
                        "asset_key": asset_key,
                        "vendor": group["vendor"],
                        "models": set(),
                        "family": family,
                        "profile": profile,
                        "command": decision.canonical_command,
                        "handling": handling,
                        "asset_mode": ("ignored" if is_not_applicable else decision.asset_mode),
                        "parse_counts": Counter(),
                        "structured_count": 0,
                        "sample_count": 0,
                        "device_keys": set(),
                        "template": template,
                        "parser_job": parser_job,
                    },
                )
                unit["models"].add(group["model"])
                unit["parse_counts"].update(group["parse_counts"])
                unit["structured_count"] += int(group["structured_count"])
                unit["sample_count"] += sum(group["parse_counts"].values())
                unit["device_keys"].update(group["device_keys"])
                if template and (unit["template"] is None or str(template.status) == "active"):
                    unit["template"] = template
                if parser_job and unit["parser_job"] is None:
                    unit["parser_job"] = parser_job

            parser_job_ids = {
                str(item["parser_job"].id)
                for item in resolved_groups.values()
                if item["parser_job"] is not None
            }
            signature_counts = dict(
                db.query(
                    ParserJobSample.job_id,
                    func.count(func.distinct(ParserJobSample.output_signature)),
                )
                .filter(ParserJobSample.job_id.in_(parser_job_ids))
                .group_by(ParserJobSample.job_id)
                .all()
                if parser_job_ids
                else []
            )

            active_template_ids = {
                str(item["template"].id)
                for item in resolved_groups.values()
                if item["template"] is not None and item["template"].status == "active"
            }
            contract_by_template = {
                str(item.source_template_id): item
                for item in (
                    db.query(ObservationContractRecord)
                    .filter(
                        ObservationContractRecord.source_template_id.in_(active_template_ids),
                        ObservationContractRecord.status == "active",
                    )
                    .order_by(ObservationContractRecord.version.desc())
                    .all()
                    if active_template_ids
                    else []
                )
            }
            source_refs = {f"parser_template:{item}" for item in active_template_ids}
            seeds = (
                db.query(RuleGenerationJobRecord)
                .filter(
                    RuleGenerationJobRecord.job_type == "OBSERVATION_CONTRACT",
                    RuleGenerationJobRecord.source_ref.in_(source_refs),
                )
                .order_by(RuleGenerationJobRecord.updated_at.desc())
                .all()
                if source_refs
                else []
            )
            seeded_source_refs = {str(item.source_ref) for item in seeds}
            missing_seed_template_ids = sorted(
                template_id
                for template_id in active_template_ids
                if f"parser_template:{template_id}" not in seeded_source_refs
            )
            for template_id in missing_seed_template_ids:
                self.template_activation_service.seed_from_template(template_id)
            if missing_seed_template_ids:
                seeds = (
                    db.query(RuleGenerationJobRecord)
                    .filter(
                        RuleGenerationJobRecord.job_type == "OBSERVATION_CONTRACT",
                        RuleGenerationJobRecord.source_ref.in_(source_refs),
                    )
                    .order_by(RuleGenerationJobRecord.updated_at.desc())
                    .all()
                )
            seed_by_ref: dict[str, RuleGenerationJobRecord] = {}
            for item in seeds:
                if (item.payload_json or {}).get("workflow") == "rule-asset-generation":
                    seed_by_ref.setdefault(str(item.source_ref), item)
            seed_ids = {str(item.id) for item in seed_by_ref.values()}
            contexts = (
                db.query(RuleGenerationContextRecord)
                .filter(
                    RuleGenerationContextRecord.offline_job_id == offline_job_id,
                    RuleGenerationContextRecord.job_id.in_(seed_ids),
                )
                .order_by(RuleGenerationContextRecord.created_at.desc())
                .all()
                if seed_ids
                else []
            )
            context_by_seed: dict[str, RuleGenerationContextRecord] = {}
            for item in contexts:
                context_by_seed.setdefault(str(item.job_id), item)
            context_ids = {str(item.id) for item in contexts}
            bundles = (
                db.query(RuleGenerationJobRecord)
                .filter(
                    RuleGenerationJobRecord.job_type == "RULE_ASSET_BUNDLE",
                    RuleGenerationJobRecord.payload_json["context_id"].as_string().in_(context_ids),
                )
                .order_by(RuleGenerationJobRecord.updated_at.desc())
                .all()
                if context_ids
                else []
            )
            bundle_by_context: dict[str, RuleGenerationJobRecord] = {}
            for item in bundles:
                context_id = str((item.payload_json or {}).get("context_id") or "")
                bundle_by_context.setdefault(context_id, item)
            child_ids = {
                str(child_id)
                for item in bundles
                for child_id in (item.payload_json or {}).get("child_job_ids") or []
            }
            children = (
                db.query(RuleGenerationJobRecord)
                .filter(RuleGenerationJobRecord.id.in_(child_ids))
                .all()
                if child_ids
                else []
            )
            child_by_id = {str(item.id): item for item in children}

            keep_ids: set[str] = set()
            for unit in resolved_groups.values():
                unit["device_count"] = len(unit["device_keys"])
                template = unit["template"]
                parser_job = unit["parser_job"]
                template_id = str(template.id) if template is not None else None
                seed = seed_by_ref.get(f"parser_template:{template_id}") if template_id else None
                context = context_by_seed.get(str(seed.id)) if seed is not None else None
                bundle = bundle_by_context.get(str(context.id)) if context is not None else None
                bundle_children = (
                    [
                        child_by_id[str(child_id)]
                        for child_id in (bundle.payload_json or {}).get("child_job_ids") or []
                        if str(child_id) in child_by_id
                    ]
                    if bundle is not None
                    else []
                )
                active_contract = contract_by_template.get(template_id or "")
                stages = self._build_stages(
                    offline_job_id=offline_job_id,
                    dataset_ids=dataset_ids,
                    unit=unit,
                    parser_job=parser_job,
                    template=template,
                    active_contract=active_contract,
                    seed=seed,
                    context=context,
                    bundle=bundle,
                    children=bundle_children,
                    output_signature_count=int(
                        signature_counts.get(str(parser_job.id), 0) if parser_job else 0
                    ),
                )
                current_stage, overall_status, progress, next_action = self._rollup(stages)
                blocker_count = sum(len(item["blockers"]) for item in stages)
                record_id = _unit_id(offline_job_id, unit["asset_key"])
                keep_ids.add(record_id)
                record = db.get(RuleAssetCommandUnitRecord, record_id)
                if record is None:
                    record = RuleAssetCommandUnitRecord(id=record_id)
                    db.add(record)
                models = sorted(str(item) for item in unit["models"] if item)
                record.offline_job_id = offline_job_id
                record.asset_key = unit["asset_key"]
                record.vendor = unit["vendor"]
                record.model = models[0] if len(models) == 1 else None
                record.models_json = models
                record.family = unit["family"]
                record.profile = unit["profile"]
                record.canonical_command = unit["command"]
                record.handling = unit["handling"]
                record.asset_mode = unit["asset_mode"]
                record.status = overall_status
                record.current_stage = current_stage
                record.progress_percent = progress
                record.sample_count = unit["sample_count"]
                record.device_count = len(unit["device_keys"])
                record.output_signature_count = int(
                    signature_counts.get(str(parser_job.id), 0) if parser_job else 0
                )
                record.blocker_count = blocker_count
                record.parse_counts_json = dict(unit["parse_counts"])
                record.stage_states_json = stages
                record.next_action_json = next_action or {}
                record.parser_job_id = str(parser_job.id) if parser_job else None
                record.parser_template_id = template_id
                record.rule_seed_job_id = str(seed.id) if seed else None
                record.context_id = str(context.id) if context else None
                record.bundle_id = str(bundle.id) if bundle else None
                record.projection_version = PROJECTION_VERSION

            stale_query = db.query(RuleAssetCommandUnitRecord).filter(
                RuleAssetCommandUnitRecord.offline_job_id == offline_job_id
            )
            if keep_ids:
                stale_query = stale_query.filter(~RuleAssetCommandUnitRecord.id.in_(keep_ids))
            deleted = stale_query.delete(synchronize_session=False)
            return {
                "offline_job_id": offline_job_id,
                "unit_count": len(keep_ids),
                "deleted": int(deleted or 0),
            }

    def list_units(
        self,
        offline_job_id: str,
        *,
        page: int = 1,
        page_size: int = 25,
        search: str | None = None,
        status: str | None = None,
        stage: str | None = None,
        vendor: str | None = None,
        model: str | None = None,
        asset_mode: str | None = None,
    ) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))
        with get_db_session() as db:
            require_rule_asset_source_job(db, offline_job_id)
            query = db.query(RuleAssetCommandUnitRecord).filter(
                RuleAssetCommandUnitRecord.offline_job_id == offline_job_id
            )
            if search and search.strip():
                pattern = f"%{search.strip()}%"
                query = query.filter(
                    or_(
                        RuleAssetCommandUnitRecord.canonical_command.ilike(pattern),
                        RuleAssetCommandUnitRecord.vendor.ilike(pattern),
                        RuleAssetCommandUnitRecord.model.ilike(pattern),
                        RuleAssetCommandUnitRecord.family.ilike(pattern),
                        RuleAssetCommandUnitRecord.profile.ilike(pattern),
                    )
                )
            if status:
                query = query.filter(RuleAssetCommandUnitRecord.status == status)
            if stage:
                query = query.filter(RuleAssetCommandUnitRecord.current_stage == stage)
            if vendor:
                query = query.filter(RuleAssetCommandUnitRecord.vendor == vendor)
            if model:
                query = query.filter(RuleAssetCommandUnitRecord.models_json.contains([model]))
            if asset_mode:
                query = query.filter(RuleAssetCommandUnitRecord.asset_mode == asset_mode)
            total = query.count()
            rows = (
                query.order_by(
                    case((RuleAssetCommandUnitRecord.status == "blocked", 0), else_=1),
                    RuleAssetCommandUnitRecord.progress_percent.asc(),
                    RuleAssetCommandUnitRecord.canonical_command.asc(),
                    RuleAssetCommandUnitRecord.id.asc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            summary = self._summary(db, offline_job_id)
            return {
                "items": [self._serialize_row(item) for item in rows],
                "page": page,
                "page_size": page_size,
                "total": int(total),
                "page_count": (int(total) + page_size - 1) // page_size,
                "summary": summary,
            }

    def get_unit(
        self,
        offline_job_id: str,
        unit_id: str,
        *,
        evidence_page: int = 1,
        evidence_page_size: int = 25,
    ) -> dict[str, Any]:
        evidence_page = max(1, int(evidence_page))
        evidence_page_size = max(1, min(int(evidence_page_size), 100))
        with get_db_session() as db:
            require_rule_asset_source_job(db, offline_job_id)
            row = (
                db.query(RuleAssetCommandUnitRecord)
                .filter(
                    RuleAssetCommandUnitRecord.id == unit_id,
                    RuleAssetCommandUnitRecord.offline_job_id == offline_job_id,
                )
                .first()
            )
            if row is None:
                raise KeyError("command asset unit not found")
            periods = (
                db.query(OfflinePatrolPeriodRecord)
                .filter(OfflinePatrolPeriodRecord.job_id == offline_job_id)
                .all()
            )
            run_to_period = {
                str(item.patrol_run_id): {
                    "role": item.role,
                    "label": item.label,
                    "dataset_id": item.dataset_id,
                    "observed_at": _iso(item.observed_at),
                }
                for item in periods
                if item.patrol_run_id
            }
            models = list(row.models_json or [])
            evidence_query = db.query(CommandSnapshot).filter(
                CommandSnapshot.run_id.in_(run_to_period),
                CommandSnapshot.vendor == row.vendor,
                CommandSnapshot.canonical_command == row.canonical_command,
            )
            if models:
                evidence_query = evidence_query.filter(CommandSnapshot.model.in_(models))
            evidence_total = evidence_query.count()
            evidence_rows = (
                evidence_query.order_by(
                    CommandSnapshot.observed_at.desc(), CommandSnapshot.id.desc()
                )
                .offset((evidence_page - 1) * evidence_page_size)
                .limit(evidence_page_size)
                .all()
            )
            evidence_ids = [str(item.id) for item in evidence_rows]
            parse_results = (
                db.query(SnapshotParseResult)
                .filter(
                    SnapshotParseResult.snapshot_id.in_(evidence_ids),
                    SnapshotParseResult.parse_status.in_(["success", "empty"]),
                )
                .order_by(
                    SnapshotParseResult.snapshot_id,
                    SnapshotParseResult.parsed_at.desc(),
                    SnapshotParseResult.id.desc(),
                )
                .all()
                if evidence_ids
                else []
            )
            effective_results: dict[str, SnapshotParseResult] = {}
            for parse_result in parse_results:
                effective_results.setdefault(str(parse_result.snapshot_id), parse_result)
            attempts = (
                db.query(ParserGenerationAttempt)
                .filter(ParserGenerationAttempt.job_id == row.parser_job_id)
                .order_by(ParserGenerationAttempt.attempt_number.desc())
                .limit(50)
                .all()
                if row.parser_job_id
                else []
            )
            bundle = db.get(RuleGenerationJobRecord, row.bundle_id) if row.bundle_id else None
            child_ids = (
                [str(item) for item in (bundle.payload_json or {}).get("child_job_ids") or []]
                if bundle
                else []
            )
            children = (
                db.query(RuleGenerationJobRecord)
                .filter(RuleGenerationJobRecord.id.in_(child_ids))
                .all()
                if child_ids
                else []
            )
            result = self._serialize_row(row, include_stages=True)
            evidence: list[dict[str, Any]] = []
            for item in evidence_rows:
                parse_result = effective_results.get(str(item.id))
                structured_json = (
                    parse_result.structured_json
                    if parse_result is not None
                    else item.structured_json
                )
                evidence.append(
                    {
                        "snapshot_id": item.id,
                        "period": run_to_period.get(str(item.run_id)),
                        "device_id": item.device_id,
                        "device_name": item.device_name,
                        "observed_at": _iso(item.observed_at),
                        "parse_status": (
                            parse_result.parse_status
                            if parse_result is not None
                            else item.parse_status
                        ),
                        "original_parse_status": item.parse_status,
                        "parse_source": "reparse" if parse_result is not None else "capture",
                        "output_status": item.output_status,
                        "parser_template_id": (
                            parse_result.template_id
                            if parse_result is not None
                            else item.parser_template_id
                        ),
                        "raw_text_hash": item.raw_text_hash,
                        "structured_record_count": (
                            len(structured_json)
                            if isinstance(structured_json, list)
                            else 1
                            if isinstance(structured_json, dict)
                            else 0
                        ),
                    }
                )
            result["evidence"] = evidence
            result["evidence_pagination"] = {
                "page": evidence_page,
                "page_size": evidence_page_size,
                "total": int(evidence_total),
                "page_count": (int(evidence_total) + evidence_page_size - 1) // evidence_page_size,
            }
            result["parser_attempts"] = [
                {
                    "id": item.id,
                    "attempt_number": item.attempt_number,
                    "stage": item.stage,
                    "action": item.action,
                    "status": item.status,
                    "model_provider": item.model_provider,
                    "model_name": item.model_name,
                    "candidate_rank": item.candidate_rank,
                    "failure_signature": item.failure_signature,
                    "created_at": _iso(item.created_at),
                }
                for item in attempts
            ]
            result["rule_assets"] = [
                {
                    "job_id": item.id,
                    "asset_type": item.asset_type,
                    "asset_id": item.asset_id,
                    "asset_version_id": item.asset_version_id,
                    "status": item.status,
                    "validation_success": (item.validation_report_json or {}).get("success")
                    is True,
                }
                for item in children
            ]
            if bundle:
                report = dict(bundle.validation_report_json or {})
                governance = dict(report.get("governance") or {})
                result["bundle"] = {
                    "id": bundle.id,
                    "status": bundle.status,
                    "validation_success": report.get("success") is True,
                    "historical_replay": report.get("historical_replay") or {},
                    "engineering_review": governance.get("engineering_review") or {},
                }
            else:
                result["bundle"] = None
            return result

    def summary(self, offline_job_id: str) -> dict[str, Any]:
        with get_db_session() as db:
            require_rule_asset_source_job(db, offline_job_id)
            return self._summary(db, offline_job_id)

    @staticmethod
    def _resolve_template(
        group: dict[str, Any],
        templates_by_id: dict[str, ParserTemplate],
        candidates: dict[tuple[str, str], list[ParserTemplate]],
    ) -> ParserTemplate | None:
        referenced = [
            templates_by_id[item] for item in group["template_ids"] if item in templates_by_id
        ]
        active = [item for item in referenced if item.status == "active"]
        if active:
            return active[0]
        matching = candidates.get((_norm(group["vendor"]), _norm(group["command"])), [])
        exact = [
            item
            for item in matching
            if item.status == "active"
            and _norm(group["model"]) in {_norm(item.model), _norm(item.profile)}
        ]
        return exact[0] if exact else referenced[0] if referenced else None

    @staticmethod
    def _resolve_parser_job(
        group: dict[str, Any],
        candidates: dict[tuple[str, str], list[ParserTemplateJob]],
    ) -> ParserTemplateJob | None:
        matching = candidates.get((_norm(group["vendor"]), _norm(group["command"])), [])
        exact = [
            item
            for item in matching
            if _norm(group["model"]) in {_norm(item.model), _norm(item.profile)}
        ]
        if exact:
            return exact[0]
        return None

    def _build_stages(
        self,
        *,
        offline_job_id: str,
        dataset_ids: list[str] | None = None,
        unit: dict[str, Any],
        parser_job: ParserTemplateJob | None,
        template: ParserTemplate | None,
        active_contract: ObservationContractRecord | None,
        seed: RuleGenerationJobRecord | None,
        context: RuleGenerationContextRecord | None,
        bundle: RuleGenerationJobRecord | None,
        children: list[RuleGenerationJobRecord],
        output_signature_count: int,
    ) -> list[dict[str, Any]]:
        command = unit["command"]
        source_job_url = "/skills/parser?" + urlencode(
            {
                "workspace": "production",
                "subtab": "source-jobs",
                "source_job_id": offline_job_id,
                "source_job_tab": "sources",
            }
        )
        parser_url = _parser_url(command)
        backfill_url = _backfill_url(
            offline_job_id,
            command,
            template_id=str(template.id) if template is not None else None,
            dataset_ids=dataset_ids or [],
        )
        generation_url = _generation_url(
            offline_job_id,
            command,
            dataset_ids=dataset_ids or [],
            seed_job_id=str(seed.id) if seed is not None else None,
        )
        bundle_url = _bundle_url(offline_job_id, str(bundle.id) if bundle else None)
        parse_counts = dict(unit["parse_counts"])
        stages = [
            _stage(
                "output_slicing",
                "passed" if unit["sample_count"] else "blocked",
                f"已归集 {unit['sample_count']} 个命令输出切片。",
                metrics={"sample_count": unit["sample_count"]},
                blockers=[] if unit["sample_count"] else ["没有可用于生产资产的命令输出"],
                next_action=(
                    None
                    if unit["sample_count"]
                    else _action(
                        "inspect_import", "检查样本导入", "确认文件切片结果。", source_job_url
                    )
                ),
            )
        ]
        identity_ready = all(
            _norm(value) not in {"", "unknown", "auto-discovered"}
            for value in (unit["vendor"], unit["profile"], command)
        )
        stages.append(
            _stage(
                "identity_grouping",
                "passed" if identity_ready else "blocked",
                (
                    f"资产键为 {unit['vendor']} / {unit['profile']} / {command}，"
                    f"覆盖 {len(unit['models'])} 个型号。"
                ),
                metrics={
                    "device_count": unit["device_count"],
                    "model_count": len(unit["models"]),
                    "output_signature_count": output_signature_count,
                },
                blockers=[] if identity_ready else ["厂家、适配 profile 或规范命令未识别"],
                next_action=(
                    None
                    if identity_ready
                    else _action(
                        "confirm_identity", "修正设备身份", "确认设备归属与型号。", source_job_url
                    )
                ),
            )
        )

        semantic = unit["asset_mode"] in {"full_chain", "observation_only", "change_only"}
        if not semantic:
            reason = "命令按原始哈希比较" if unit["asset_mode"] == "raw_hash" else "命令策略为忽略"
            for key in STAGE_ORDER[2:]:
                stages.append(_stage(key, "skipped", reason))
            return stages

        template_active = template is not None and template.status == "active"
        parser_artifacts = []
        if parser_job is not None:
            parser_artifacts.append(
                {"type": "parser_job", "id": parser_job.id, "status": parser_job.status}
            )
        if template is not None:
            parser_artifacts.append(
                {"type": "parser_template", "id": template.id, "status": template.status}
            )
        if template_active:
            parser_generation_status = "passed"
            parser_generation_blockers: list[str] = []
        elif parser_job is None:
            parser_generation_status = "not_started"
            parser_generation_blockers = ["尚未创建 Parser 资产任务"]
        elif parser_job.status in ACTIVE_PARSER_JOB_STATUSES:
            parser_generation_status = "running"
            parser_generation_blockers = []
        elif parser_job.status == "pending":
            parser_generation_status = "ready"
            parser_generation_blockers = []
        elif parser_job.status == "succeeded":
            parser_generation_status = "needs_review"
            parser_generation_blockers = []
        else:
            parser_generation_status = "failed" if parser_job.status == "failed" else "blocked"
            parser_generation_blockers = [
                str(parser_job.last_error or parser_job.failure_code or "Parser 生成未完成")
            ]
        stages.append(
            _stage(
                "parser_generation",
                parser_generation_status,
                "生成并治理适用于该资产键的 TextFSM Parser。",
                metrics={
                    "attempt_count": int(parser_job.template_attempt_count or 0)
                    if parser_job
                    else 0,
                    "sample_count": int(parser_job.sample_count or 0) if parser_job else 0,
                },
                blockers=parser_generation_blockers,
                artifacts=parser_artifacts,
                next_action=(
                    None
                    if template_active
                    else _action(
                        "open_parser_job", "处理 Parser", "生成、修订或审批 Parser。", parser_url
                    )
                ),
            )
        )
        if template_active:
            validation_status = "passed"
            validation_blockers: list[str] = []
        elif parser_job and parser_job.status == "succeeded":
            validation_status = "needs_review"
            validation_blockers = []
        elif parser_job and parser_job.status == "failed":
            validation_status = "failed"
            validation_blockers = ["Parser Agent 未通过全样本验证"]
        else:
            validation_status = "not_started"
            validation_blockers = []
        validation = dict(template.validation_json or {}) if template else {}
        parser_agent = dict(validation.get("parser_agent") or {})
        stages.append(
            _stage(
                "parser_validation",
                validation_status,
                "编译、训练样本、隐藏 holdout、回归与实体覆盖门禁。",
                metrics={
                    "output_signature_count": output_signature_count,
                    "hard_gates": parser_agent.get("hard_gates") or {},
                },
                blockers=validation_blockers,
                artifacts=parser_artifacts,
                next_action=(
                    None
                    if validation_status == "passed"
                    else _action(
                        "inspect_parser_validation", "查看验证", "检查失败样本和门禁。", parser_url
                    )
                ),
            )
        )
        gap_count = sum(
            int(parse_counts.get(key) or 0)
            for key in ("template_missing", "parse_failed", "identity_unknown")
        )
        if not template_active:
            quality_status = "not_started"
            quality_blockers = []
        elif gap_count:
            quality_status = "blocked"
            quality_blockers = [f"仍有 {gap_count} 个输出未形成可靠结构化结果"]
        elif unit["structured_count"] <= 0:
            quality_status = "blocked"
            quality_blockers = ["没有可供规则生成使用的结构化记录"]
        else:
            quality_status = "passed"
            quality_blockers = []
        stages.append(
            _stage(
                "structured_quality",
                quality_status,
                "检查解析覆盖、关键实体覆盖和结构化记录规模。",
                metrics={
                    "parse_counts": parse_counts,
                    "structured_record_count": unit["structured_count"],
                    "gap_count": gap_count,
                },
                blockers=quality_blockers,
                next_action=(
                    None
                    if quality_status == "passed"
                    else _action(
                        "repair_or_backfill",
                        "修复并回填",
                        "修复 Parser 后回填历史快照。",
                        backfill_url,
                    )
                ),
            )
        )

        bundle_status = str(bundle.status) if bundle else None
        contract_status, contract_children = _child_stage_status(
            children, "OBSERVATION_CONTRACT", bundle_status=bundle_status
        )
        if active_contract is not None:
            contract_status = "passed"
        elif not context and contract_status == "not_started":
            contract_status = "ready" if seed and quality_status == "passed" else "not_started"
        contract_artifacts = (
            [{"type": "observation_contract", "id": active_contract.id, "status": "active"}]
            if active_contract
            else []
        )
        contract_artifacts.extend(
            {"type": "rule_job", "id": item.id, "status": item.status} for item in contract_children
        )
        stages.append(
            _stage(
                "observation_contract",
                contract_status,
                "定义实体、主键、字段类型、单位、空值和状态语义。",
                metrics={"candidate_count": len(contract_children)},
                artifacts=contract_artifacts,
                next_action=(
                    None
                    if contract_status == "passed"
                    else _action(
                        "generate_contract",
                        "生成并修订 Contract",
                        "使用脱敏结构化上下文生成候选。",
                        generation_url,
                    )
                ),
            )
        )

        policy_status, policy_children = _child_stage_status(
            children, "CHANGE_POLICY", bundle_status=bundle_status
        )
        if unit["asset_mode"] == "observation_only":
            policy_status = "skipped"
        elif policy_status == "not_started" and context:
            policy_status = "ready"
        stages.append(
            _stage(
                "change_policy",
                policy_status,
                "声明比较字段、变化类型、阈值、滞回和噪声抑制策略。",
                metrics={"candidate_count": len(policy_children)},
                artifacts=[
                    {"type": "rule_job", "id": item.id, "status": item.status}
                    for item in policy_children
                ],
                next_action=(
                    None
                    if policy_status in RESOLVED_STAGE_STATUSES
                    else _action(
                        "generate_policy",
                        "生成并修订 Policy",
                        "从多周期结构化数据生成变化策略。",
                        generation_url,
                    )
                ),
            )
        )

        event_status, event_children = _child_stage_status(
            children, "EVENT_RULE", bundle_status=bundle_status
        )
        if unit["asset_mode"] in {"observation_only", "change_only"}:
            event_status = "skipped"
        elif event_status == "not_started" and context:
            event_status = "ready"
        stages.append(
            _stage(
                "event_rule",
                event_status,
                "将有运营意义的变化映射为事件、恢复和严重级别。",
                metrics={"candidate_count": len(event_children)},
                artifacts=[
                    {"type": "rule_job", "id": item.id, "status": item.status}
                    for item in event_children
                ],
                next_action=(
                    None
                    if event_status in RESOLVED_STAGE_STATUSES
                    else _action(
                        "generate_event_rule",
                        "生成并修订 Event Rule",
                        "生成事件与恢复规则。",
                        generation_url,
                    )
                ),
            )
        )

        report = dict(bundle.validation_report_json or {}) if bundle else {}
        replay = dict(report.get("historical_replay") or {})
        review = dict((report.get("governance") or {}).get("engineering_review") or {})
        validation_success = report.get("success") is True
        replay_success = (
            replay.get("success") is True and int(replay.get("collision_count") or 0) == 0
        )
        review_accepted = review.get("decision") == "accepted"
        if bundle_status == "active":
            review_status = "passed"
        elif bundle is None:
            review_status = "not_started"
        elif not validation_success or not replay_success:
            review_status = "blocked"
        elif review_accepted:
            review_status = "passed"
        else:
            review_status = "needs_review"
        review_blockers = []
        if bundle and not validation_success:
            review_blockers.append("确定性验证未通过")
        if bundle and not replay_success:
            review_blockers.append("历史回放未通过或存在规则碰撞")
        stages.append(
            _stage(
                "validation_review",
                review_status,
                "执行合成验证、历史回放、碰撞检查和工程师修订复核。",
                metrics={
                    "matched_change_count": replay.get("matched_change_count"),
                    "collision_count": replay.get("collision_count"),
                    "estimated_daily_event_count": replay.get("estimated_daily_event_count"),
                    "engineering_review_decision": review.get("decision"),
                },
                blockers=review_blockers,
                next_action=(
                    None
                    if review_status == "passed"
                    else _action(
                        "review_bundle", "验证并复核资产包", "查看回放并修订候选资产。", bundle_url
                    )
                ),
            )
        )
        if bundle_status == "active":
            release_status = "passed"
        elif bundle_status == "pending_approval" and review_accepted and validation_success:
            release_status = "ready"
        elif bundle is None:
            release_status = "not_started"
        elif review_status == "blocked":
            release_status = "blocked"
        else:
            release_status = "not_started"
        stages.append(
            _stage(
                "release",
                release_status,
                "审批后原子激活 Contract、Policy 和 Rule，保留回滚与审计记录。",
                artifacts=(
                    [{"type": "rule_asset_bundle", "id": bundle.id, "status": bundle.status}]
                    if bundle
                    else []
                ),
                next_action=(
                    None
                    if release_status == "passed"
                    else _action(
                        "release_bundle", "审批发布", "完成工程复核后发布资产包。", bundle_url
                    )
                ),
            )
        )
        return stages

    @staticmethod
    def _rollup(
        stages: list[dict[str, Any]],
    ) -> tuple[str, str, int, dict[str, Any] | None]:
        resolved = sum(item["status"] in RESOLVED_STAGE_STATUSES for item in stages)
        progress = round((resolved / len(stages)) * 100) if stages else 0
        current = next(
            (item for item in stages if item["status"] not in RESOLVED_STAGE_STATUSES),
            None,
        )
        if current is None:
            return "release", "completed", 100, None
        status = str(current["status"])
        overall = {
            "failed": "failed",
            "blocked": "blocked",
            "needs_review": "needs_review",
            "running": "running",
            "ready": "ready",
            "not_started": "not_started",
        }.get(status, "not_started")
        return str(current["key"]), overall, progress, current.get("next_action")

    @staticmethod
    def _summary(db: Any, offline_job_id: str) -> dict[str, Any]:
        status_rows = (
            db.query(RuleAssetCommandUnitRecord.status, func.count(RuleAssetCommandUnitRecord.id))
            .filter(RuleAssetCommandUnitRecord.offline_job_id == offline_job_id)
            .group_by(RuleAssetCommandUnitRecord.status)
            .all()
        )
        stage_rows = (
            db.query(
                RuleAssetCommandUnitRecord.current_stage,
                func.count(RuleAssetCommandUnitRecord.id),
            )
            .filter(RuleAssetCommandUnitRecord.offline_job_id == offline_job_id)
            .group_by(RuleAssetCommandUnitRecord.current_stage)
            .all()
        )
        mode_rows = (
            db.query(
                RuleAssetCommandUnitRecord.asset_mode,
                func.count(RuleAssetCommandUnitRecord.id),
            )
            .filter(RuleAssetCommandUnitRecord.offline_job_id == offline_job_id)
            .group_by(RuleAssetCommandUnitRecord.asset_mode)
            .all()
        )
        statuses = {str(key): int(count) for key, count in status_rows}
        return {
            "total": sum(statuses.values()),
            "by_status": statuses,
            "by_stage": {str(key): int(count) for key, count in stage_rows},
            "by_mode": {str(key): int(count) for key, count in mode_rows},
            "completed": int(statuses.get("completed", 0)),
            "running": int(statuses.get("running", 0)),
            "needs_review": int(statuses.get("needs_review", 0)),
            "blocked": int(statuses.get("blocked", 0) + statuses.get("failed", 0)),
        }

    @staticmethod
    def _serialize_row(
        row: RuleAssetCommandUnitRecord,
        *,
        include_stages: bool = False,
    ) -> dict[str, Any]:
        result = {
            "id": row.id,
            "offline_job_id": row.offline_job_id,
            "asset_key": row.asset_key,
            "vendor": row.vendor,
            "model": row.model,
            "models": list(row.models_json or []),
            "family": row.family,
            "profile": row.profile,
            "canonical_command": row.canonical_command,
            "handling": row.handling,
            "asset_mode": row.asset_mode,
            "status": row.status,
            "current_stage": row.current_stage,
            "current_stage_label": STAGE_LABELS.get(row.current_stage, row.current_stage),
            "progress_percent": row.progress_percent,
            "sample_count": row.sample_count,
            "device_count": row.device_count,
            "output_signature_count": row.output_signature_count,
            "blocker_count": row.blocker_count,
            "parse_counts": dict(row.parse_counts_json or {}),
            "stage_statuses": {
                str(item.get("key")): str(item.get("status"))
                for item in (row.stage_states_json or [])
                if item.get("key")
            },
            "next_action": dict(row.next_action_json or {}),
            "parser_job_id": row.parser_job_id,
            "parser_template_id": row.parser_template_id,
            "rule_seed_job_id": row.rule_seed_job_id,
            "context_id": row.context_id,
            "bundle_id": row.bundle_id,
            "projection_version": row.projection_version,
            "updated_at": _iso(row.updated_at),
        }
        if include_stages:
            result["stages"] = list(row.stage_states_json or [])
        return result

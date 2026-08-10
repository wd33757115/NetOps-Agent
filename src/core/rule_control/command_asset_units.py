"""Command-level read model for the rule asset production control plane."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import case, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from src.core.check_items.catalog import CheckItemCatalog, CheckItemCatalogRepository
from src.core.check_items.presentation import (
    check_item_display_description,
    check_item_display_name,
)
from src.core.check_items.resolver import CheckItemImplementationResolver
from src.core.check_items.runtime_bundle_assembly import (
    RuntimeBundleAssemblyPlanner,
)
from src.core.parser.policy import DEFAULT_COMMAND_ALIASES, ParserPolicy
from src.core.parser.repository import (
    ParserJobRepository,
    ParserMetadataRepository,
    ParserTemplateRepository,
    output_signature,
    parser_job_asset_key,
)
from src.core.rule_control.asset_requirements import (
    evaluate_requirement_replay_gate,
    normalize_rule_asset_requirements,
)
from src.core.rule_control.source_scope import require_rule_asset_source_job
from src.core.rule_control.template_activation import RuleAssetTemplateActivationService
from src.infrastructure.db.models import (
    CommandSnapshot,
    ObservationContractRecord,
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    ParserGenerationAttempt,
    ParserJobSample,
    ParserTemplate,
    ParserTemplateJob,
    RuleAssetCommandUnitCheckItemRecord,
    RuleAssetCommandUnitRecord,
    RuleGenerationContextRecord,
    RuleGenerationJobRecord,
    SnapshotParseResult,
)
from src.infrastructure.db.postgres import get_db_session

PROJECTION_VERSION = 9
logger = logging.getLogger(__name__)

CHECK_ITEM_COMPONENT_ORDER = (
    "identity",
    "parser",
    "contract",
    "current_state",
    "policy",
    "event",
    "comparison",
    "runtime_bundle",
)
CHECK_ITEM_COMPONENT_REQUIREMENTS = {
    "identity": "identity_policy",
    "parser": "parser_template",
    "contract": "observation_contract",
    "current_state": "current_state_rule",
    "policy": "change_policy",
    "event": "event_rule",
    "comparison": "comparison_profile",
}


def _catalog_projection_version(catalog: CheckItemCatalog) -> int:
    """Fingerprint every active catalog dependency used by the persisted projection."""

    payload = {
        "schema_version": PROJECTION_VERSION,
        "check_items": sorted(
            (
                item.id,
                item.version_id,
                item.digest,
                item.status,
            )
            for item in catalog.check_items
        ),
        "implementations": sorted(
            (
                item.id,
                item.version_id,
                item.digest,
                item.status,
            )
            for item in catalog.implementations
        ),
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return (int(digest[:8], 16) & 0x7FFFFFFF) or 1


def _models_contain(model: str):
    """Use PostgreSQL JSONB containment instead of invalid JSON string matching."""

    return cast(RuleAssetCommandUnitRecord.models_json, JSONB).contains([str(model)])


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
ACTIVE_PARSER_JOB_STATUSES = {"running", "validating"}
GOVERNANCE_BUCKETS = {
    "action_required",
    "in_progress",
    "awaiting_approval",
    "published",
    "not_applicable",
    "exception",
}
GOVERNANCE_STATUSES = {
    "preparing",
    "ready_to_generate",
    "generating",
    "engineering_review",
    "release_approval",
    "published",
    "not_applicable",
    "blocked",
    "failed",
}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _canonical_command_key(value: Any) -> str:
    normalized = ParserPolicy._basic_normalize(str(value or ""))
    return ParserPolicy._basic_normalize(
        DEFAULT_COMMAND_ALIASES.get(normalized, normalized)
    )


def _iso(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _latest_bundle_by_context(
    bundles: list[RuleGenerationJobRecord],
) -> dict[str, RuleGenerationJobRecord]:
    """Select the newest immutable bundle version, not the last metadata update."""

    ordered = sorted(
        bundles,
        key=lambda item: (
            item.created_at.timestamp() if item.created_at else 0,
            str(item.id),
        ),
        reverse=True,
    )
    selected: dict[str, RuleGenerationJobRecord] = {}
    for item in ordered:
        context_id = str((item.payload_json or {}).get("context_id") or "")
        if context_id:
            selected.setdefault(context_id, item)
    return selected


def _latest_context_by_seed_for_source(
    contexts: list[RuleGenerationContextRecord],
    offline_job_id: str,
) -> dict[str, RuleGenerationContextRecord]:
    """Keep generation evidence scoped to the current explicit source job."""

    selected: dict[str, RuleGenerationContextRecord] = {}
    for item in contexts:
        if str(item.offline_job_id or "") != offline_job_id:
            continue
        selected.setdefault(str(item.job_id), item)
    return selected


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


def _runtime_bundle_url(
    *,
    check_item_id: str,
    check_item_version_id: str,
    implementation_version_id: str,
) -> str:
    query = urlencode(
        {
            "workspace": "runtime",
            "subtab": "coverage-backfill",
            "check_item_version_id": check_item_version_id,
            "implementation_version_id": implementation_version_id,
            "search": check_item_id,
        }
    )
    return f"/skills/parser?{query}"


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


def _governance_status(
    status: str,
    current_stage: str,
    asset_mode: str | None = None,
) -> str:
    if status == "completed":
        if asset_mode in {"raw_hash", "ignored"}:
            return "not_applicable"
        return "published"
    if status == "failed":
        return "failed"
    if status == "blocked":
        return "blocked"
    if status == "running":
        return "generating"
    if status == "needs_review":
        return "release_approval" if current_stage == "release" else "engineering_review"
    if current_stage in {"output_slicing", "identity_grouping"}:
        return "preparing"
    return "ready_to_generate"


def _governance_bucket(
    status: str,
    current_stage: str,
    asset_mode: str | None = None,
) -> str:
    governance_status = _governance_status(status, current_stage, asset_mode)
    if governance_status in {"preparing", "ready_to_generate"}:
        return "action_required"
    if governance_status == "generating":
        return "in_progress"
    if governance_status in {"engineering_review", "release_approval"}:
        return "awaiting_approval"
    if governance_status == "published":
        return "published"
    if governance_status == "not_applicable":
        return "not_applicable"
    return "exception"


def _component_status(status: str | None) -> str:
    return {
        "passed": "published",
        "skipped": "not_applicable",
        "needs_review": "engineering_review",
        "running": "generating",
        "ready": "ready_to_generate",
        "blocked": "blocked",
        "failed": "failed",
        "not_started": "not_started",
    }.get(str(status or ""), "not_started")


def _parser_url(
    offline_job_id: str,
    command: str,
    *,
    dataset_ids: list[str],
) -> str:
    params = {
        "workspace": "production",
        "subtab": "asset-production-units",
        "action": "parser",
        "source_job_id": offline_job_id,
        "offline_job_id": offline_job_id,
        "command": command,
    }
    if dataset_ids:
        params["dataset_ids"] = ",".join(dataset_ids)
    return "/skills/parser?" + urlencode(params)


def _generation_url(
    offline_job_id: str,
    command: str,
    *,
    dataset_ids: list[str],
    seed_job_id: str | None = None,
) -> str:
    params = {
        "workspace": "production",
        "subtab": "asset-production-units",
        "action": "rule-generation",
        "source_job_id": offline_job_id,
        "offline_job_id": offline_job_id,
        "command": command,
    }
    if dataset_ids:
        params["dataset_ids"] = ",".join(dataset_ids)
    if seed_job_id:
        params["rule_seed_job_id"] = seed_job_id
    return "/skills/parser?" + urlencode(params)


def _backfill_url(
    offline_job_id: str,
    command: str,
    *,
    template_id: str | None,
    dataset_ids: list[str],
) -> str:
    params = {
        "workspace": "runtime",
        "subtab": "coverage-backfill",
        "action": "backfill",
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
        "workspace": "production",
        "subtab": "asset-production-units",
        "action": "release",
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
    if bundle_status in {"failed", "rejected", "cancelled"}:
        return "blocked", matching
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
        catalog_repository: CheckItemCatalogRepository | None = None,
    ) -> None:
        self.template_activation_service = (
            template_activation_service or RuleAssetTemplateActivationService()
        )
        self.catalog_repository = catalog_repository or CheckItemCatalogRepository()

    @staticmethod
    def _resolve_check_item_mappings(
        unit: dict[str, Any],
        *,
        catalog: CheckItemCatalog,
    ) -> list[dict[str, str]]:
        """Resolve catalog mappings without inferring business identity from command text."""

        resolver = CheckItemImplementationResolver(catalog)
        implementations = {
            item.version_id: item for item in catalog.implementations
        }
        command_key = _canonical_command_key(unit.get("command"))
        models = sorted(
            {
                str(item).strip()
                for item in unit.get("models") or ()
                if str(item).strip()
            }
        )
        if not models:
            models = [str(unit.get("profile") or "").strip()]
        mappings: dict[tuple[str, str], dict[str, str]] = {}
        for model in models:
            device = {
                "device_id": f"rule-asset:{unit.get('vendor')}:{model}",
                "vendor": unit.get("vendor"),
                "model": model,
                "family": unit.get("family"),
                "platform": unit.get("profile"),
            }
            for check_item in catalog.check_items:
                decision = resolver.resolve(
                    device=device,
                    check_item_id=check_item.id,
                )
                implementation = implementations.get(
                    str(decision.implementation_version_id or "")
                )
                if decision.status != "resolved" or implementation is None:
                    continue
                implementation_commands = {
                    _canonical_command_key(item.command)
                    for item in implementation.commands
                }
                if command_key not in implementation_commands:
                    continue
                key = (check_item.id, implementation.version_id)
                mappings[key] = {
                    "check_item_id": check_item.id,
                    "check_item_version_id": check_item.version_id,
                    "implementation_id": implementation.id,
                    "implementation_version_id": implementation.version_id,
                }
        return [
            mappings[key]
            for key in sorted(mappings)
        ]

    @staticmethod
    def _apply_catalog_asset_mode(
        unit: dict[str, Any],
        mappings: list[dict[str, str]],
        *,
        catalog: CheckItemCatalog,
    ) -> None:
        """Let approved check-item contracts override legacy command policy."""

        if not mappings or not unit.get("has_non_empty_output"):
            return
        check_items = {item.id: item for item in catalog.check_items}
        result_modes = {
            check_items[item["check_item_id"]].result_mode
            for item in mappings
            if item["check_item_id"] in check_items
        }
        if "structured" in result_modes:
            unit["handling"] = "structured"
            unit["asset_mode"] = "full_chain"
        elif result_modes.intersection({"normalized_diff", "hash_only"}):
            unit["handling"] = "raw_hash_diff"
            unit["asset_mode"] = "raw_hash"

    def _ensure_catalog_parser_jobs(
        self,
        offline_job_id: str,
        *,
        catalog: CheckItemCatalog,
    ) -> dict[str, int]:
        """Create missing Parser jobs from approved structured implementations.

        Import-time command policy only describes built-in knowledge. Once an
        engineer publishes a catalog binding, that immutable binding becomes the
        source of truth for whether a command requires structured parsing.
        """

        with get_db_session() as db:
            require_rule_asset_source_job(db, offline_job_id)
            run_ids = [
                str(item.patrol_run_id)
                for item in (
                    db.query(OfflinePatrolPeriodRecord)
                    .filter(OfflinePatrolPeriodRecord.job_id == offline_job_id)
                    .all()
                )
                if item.patrol_run_id
            ]
            if not run_ids:
                return {"created_or_updated": 0, "eligible_groups": 0}
            source_rows = [
                {
                    "snapshot_id": str(item.id),
                    "device_id": str(item.device_id),
                    "vendor": str(item.vendor or "Unknown"),
                    "model": str(item.model or "Unknown"),
                    "command": str(item.canonical_command or ""),
                    "parse_status": str(item.parse_status or ""),
                    "output_status": str(item.output_status or ""),
                    "raw_text": str(item.raw_text or ""),
                    "dataset_id": str(item.dataset_id or "") or None,
                    "metadata": dict(item.metadata_json or {}),
                }
                for item in (
                    db.query(CommandSnapshot)
                    .filter(CommandSnapshot.run_id.in_(run_ids))
                    .order_by(CommandSnapshot.observed_at, CommandSnapshot.id)
                    .all()
                )
            ]
            source_commands = {row["command"] for row in source_rows}
            source_vendors = {row["vendor"].casefold() for row in source_rows}
            existing_job_rows = [
                {
                    "vendor": str(item.vendor or "Unknown"),
                    "model": str(item.model or "Unknown"),
                    "family": str(item.family or "").strip() or None,
                    "profile": str(item.profile or item.model or "Unknown"),
                    "command": str(item.canonical_command or ""),
                    "sample_count": int(item.sample_count or 0),
                    "updated_at": item.updated_at,
                }
                for item in (
                    db.query(ParserTemplateJob)
                    .filter(
                        ParserTemplateJob.canonical_command.in_(source_commands),
                        func.lower(ParserTemplateJob.vendor).in_(source_vendors),
                    )
                    .all()
                )
            ]

        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in source_rows:
            grouped[(row["vendor"], row["model"], row["command"])].append(row)
        existing_jobs: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in existing_job_rows:
            existing_jobs[
                (
                    row["vendor"].casefold(),
                    row["model"].casefold(),
                    row["command"].casefold(),
                )
            ].append(row)

        implementations = {
            item.version_id: item for item in catalog.implementations
        }
        metadata_repository = ParserMetadataRepository()
        template_repository = ParserTemplateRepository()
        job_repository = ParserJobRepository()
        created_or_updated = 0
        eligible_groups = 0
        for (vendor, model, command), rows in grouped.items():
            profile_record = metadata_repository.find_device_profile(vendor, model) or {}
            family = str(profile_record.get("family") or "").strip() or None
            provisional_profile = model
            unit = {
                "vendor": vendor,
                "models": {model},
                "family": family,
                "profile": provisional_profile,
                "command": command,
            }
            mappings = self._resolve_check_item_mappings(unit, catalog=catalog)
            mapped_implementations = [
                implementations[item["implementation_version_id"]]
                for item in mappings
                if item["implementation_version_id"] in implementations
            ]
            structured_implementations = [
                item
                for item in mapped_implementations
                if item.parser_requirement == "structured"
            ]
            if not structured_implementations:
                continue
            eligible_groups += 1
            scopes = {
                str(item.template_scope or "model")
                for item in structured_implementations
            }
            existing = sorted(
                existing_jobs.get(
                    (vendor.casefold(), model.casefold(), command.casefold()),
                    [],
                ),
                key=lambda item: (
                    int(item["sample_count"]),
                    str(item["updated_at"] or ""),
                ),
                reverse=True,
            )
            if existing:
                profile = str(existing[0]["profile"])
                family = existing[0]["family"] or family
            else:
                profile = family if scopes == {"family"} and family else model
            if template_repository.resolve_active(
                vendor=vendor,
                model=model,
                family=family,
                command=command,
            ) is not None:
                continue
            for row in rows:
                # Empty, unsupported and failed command outputs are evidence for
                # applicability review, not material for synthesizing a Parser.
                if not row["raw_text"].strip() or row["output_status"] not in {
                    "data",
                    "content",
                    "not_classified",
                }:
                    continue
                failure_code = str(
                    row["metadata"].get("parser_failure_code")
                    or row["parse_status"]
                    or "template_missing"
                )
                if failure_code in {"ignored", "raw_hashed", "success", "empty"}:
                    failure_code = "template_missing"
                job_repository.create_candidate(
                    job_type="CREATE_TEMPLATE",
                    vendor=vendor,
                    model=model,
                    family=family,
                    profile=profile,
                    command=command,
                    snapshot_id=row["snapshot_id"],
                    device_id=row["device_id"],
                    failure_code=failure_code,
                    dataset_id=row["dataset_id"],
                )
                created_or_updated += 1
        return {
            "created_or_updated": created_or_updated,
            "eligible_groups": eligible_groups,
        }

    def _ensure_projection_current(self, offline_job_id: str) -> None:
        """Rebuild persisted command units when the projection contract changes."""
        self.ensure_projection_current(offline_job_id)

    def ensure_projection_current(self, offline_job_id: str) -> bool:
        """Synchronize a source job with the active check-item catalog.

        Context construction calls this public boundary before freezing version
        references. This prevents a catalog activation and a simultaneous
        context rebuild from producing an immutable but unclassified context.
        """

        expected_projection_version = _catalog_projection_version(
            self.catalog_repository.load_active()
        )
        with get_db_session() as db:
            require_rule_asset_source_job(db, offline_job_id)
            versions = (
                db.query(RuleAssetCommandUnitRecord.projection_version)
                .filter(RuleAssetCommandUnitRecord.offline_job_id == offline_job_id)
                .distinct()
                .all()
            )
        stale = not versions or any(
            int(version or 0) != expected_projection_version
            for (version,) in versions
        )
        if stale:
            self.reconcile(offline_job_id)
        return stale

    def reconcile_for_parser_job(self, parser_job_id: str) -> dict[str, Any]:
        """Refresh every offline source whose command units depend on a Parser job."""

        normalized_job_id = str(parser_job_id or "").strip()
        if not normalized_job_id:
            raise ValueError("parser_job_id is required")
        with get_db_session() as db:
            projected_job_ids = {
                str(job_id)
                for (job_id,) in (
                    db.query(RuleAssetCommandUnitRecord.offline_job_id)
                    .filter(RuleAssetCommandUnitRecord.parser_job_id == normalized_job_id)
                    .distinct()
                    .all()
                )
                if job_id
            }
            sample_job_ids = {
                str(job_id)
                for (job_id,) in (
                    db.query(OfflinePatrolPeriodRecord.job_id)
                    .join(
                        CommandSnapshot,
                        CommandSnapshot.run_id == OfflinePatrolPeriodRecord.patrol_run_id,
                    )
                    .join(
                        ParserJobSample,
                        ParserJobSample.snapshot_id == CommandSnapshot.id,
                    )
                    .filter(ParserJobSample.job_id == normalized_job_id)
                    .distinct()
                    .all()
                )
                if job_id
            }

        source_job_ids = sorted(projected_job_ids | sample_job_ids)
        reconciled = [self.reconcile(job_id) for job_id in source_job_ids]
        return {
            "parser_job_id": normalized_job_id,
            "offline_job_ids": source_job_ids,
            "reconciled_count": len(reconciled),
            "results": reconciled,
        }

    def reconcile_for_parser_template(self, template_id: str) -> dict[str, Any]:
        """Refresh command units after a Parser candidate changes governance state."""

        normalized_template_id = str(template_id or "").strip()
        if not normalized_template_id:
            raise ValueError("template_id is required")
        with get_db_session() as db:
            template = db.get(ParserTemplate, normalized_template_id)
            source_job_id = (
                str(template.source_job_id).strip()
                if template is not None and template.source_job_id
                else ""
            )
            projected_job_ids = {
                str(job_id)
                for (job_id,) in (
                    db.query(RuleAssetCommandUnitRecord.offline_job_id)
                    .filter(
                        RuleAssetCommandUnitRecord.parser_template_id
                        == normalized_template_id
                    )
                    .distinct()
                    .all()
                )
                if job_id
            }

        result = (
            self.reconcile_for_parser_job(source_job_id)
            if source_job_id
            else {
                "parser_job_id": None,
                "offline_job_ids": [],
                "reconciled_count": 0,
                "results": [],
            }
        )
        source_job_ids = sorted(
            set(result["offline_job_ids"]) | projected_job_ids
        )
        already_reconciled = set(result["offline_job_ids"])
        additional_results = [
            self.reconcile(job_id)
            for job_id in source_job_ids
            if job_id not in already_reconciled
        ]
        return {
            "template_id": normalized_template_id,
            "parser_job_id": result["parser_job_id"],
            "offline_job_ids": source_job_ids,
            "reconciled_count": len(source_job_ids),
            "results": [*result["results"], *additional_results],
        }

    def reconcile(self, offline_job_id: str) -> dict[str, Any]:
        policy = ParserPolicy()
        catalog = self.catalog_repository.load_active()
        if not catalog.check_items:
            self.catalog_repository.bootstrap(actor="rule-asset-projection")
            catalog = self.catalog_repository.load_active()
        parser_job_sync = self._ensure_catalog_parser_jobs(
            offline_job_id,
            catalog=catalog,
        )
        projection_version = _catalog_projection_version(catalog)
        with get_db_session() as db:
            # Parser completions for one source job can arrive concurrently. Lock
            # the source row for the whole delete-and-rebuild transaction so two
            # projections cannot insert the same deterministic mapping IDs.
            require_rule_asset_source_job(db, offline_job_id, for_update=True)
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
            signature_rows = (
                db.query(
                    CommandSnapshot.vendor,
                    CommandSnapshot.model,
                    CommandSnapshot.canonical_command,
                    CommandSnapshot.raw_text,
                    CommandSnapshot.output_status,
                )
                .filter(CommandSnapshot.run_id.in_(run_ids))
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
                        "output_signatures": set(),
                        "has_non_empty_output": False,
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
            for vendor, model, command, raw_text, output_status in signature_rows:
                key = (str(vendor or "Unknown"), str(model or "Unknown"), str(command or ""))
                if key in base_groups:
                    base_groups[key]["output_signatures"].add(
                        output_signature(str(raw_text or ""))
                    )
                    if (
                        str(raw_text or "").strip()
                        and str(output_status or "")
                        in {"data", "content", "not_classified"}
                    ):
                        base_groups[key]["has_non_empty_output"] = True

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
                        "output_signatures": set(),
                        "has_non_empty_output": False,
                        "template": template,
                        "parser_job": parser_job,
                    },
                )
                unit["models"].add(group["model"])
                unit["parse_counts"].update(group["parse_counts"])
                unit["structured_count"] += int(group["structured_count"])
                unit["sample_count"] += sum(group["parse_counts"].values())
                unit["device_keys"].update(group["device_keys"])
                unit["output_signatures"].update(group["output_signatures"])
                unit["has_non_empty_output"] = bool(
                    unit["has_non_empty_output"]
                    or group["has_non_empty_output"]
                )
                if template and (unit["template"] is None or str(template.status) == "active"):
                    unit["template"] = template
                if parser_job and unit["parser_job"] is None:
                    unit["parser_job"] = parser_job

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
                .filter(RuleGenerationContextRecord.job_id.in_(seed_ids))
                .order_by(RuleGenerationContextRecord.created_at.desc())
                .all()
                if seed_ids
                else []
            )
            context_by_seed = _latest_context_by_seed_for_source(
                contexts,
                offline_job_id,
            )
            context_ids = {str(item.id) for item in context_by_seed.values()}
            # A seed is shared by compatible Parser implementations, but its
            # generated context and candidate bundle are source-job evidence.
            for seed in seed_by_ref.values():
                seed_key = str(seed.id)
                if seed_key in context_by_seed:
                    continue
                payload = dict(seed.payload_json or {})
                for key in (
                    "auto_context_id",
                    "latest_generation_context_id",
                    "context_id",
                ):
                    context_id = str(payload.get(key) or "").strip()
                    if not context_id:
                        continue
                    row = db.get(RuleGenerationContextRecord, context_id)
                    if row is None or str(row.job_id) != seed_key:
                        continue
                    if str(row.offline_job_id or "") != offline_job_id:
                        continue
                    context_by_seed[seed_key] = row
                    context_ids.add(str(row.id))
                    break
            bundles = (
                db.query(RuleGenerationJobRecord)
                .filter(
                    RuleGenerationJobRecord.job_type == "RULE_ASSET_BUNDLE",
                    RuleGenerationJobRecord.payload_json["context_id"].as_string().in_(context_ids),
                )
                # A revision updates its parent review metadata after the replacement
                # bundle is created. Ordering by updated_at would therefore select the
                # superseded parent again. Bundle creation order is the immutable
                # version order for a single context.
                .order_by(
                    RuleGenerationJobRecord.created_at.desc(),
                    RuleGenerationJobRecord.id.desc(),
                )
                .all()
                if context_ids
                else []
            )
            bundle_by_context = _latest_bundle_by_context(bundles)
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
            mapped_link_count = 0
            for unit in resolved_groups.values():
                unit["device_count"] = len(unit["device_keys"])
                mappings = self._resolve_check_item_mappings(
                    unit,
                    catalog=catalog,
                )
                self._apply_catalog_asset_mode(
                    unit,
                    mappings,
                    catalog=catalog,
                )
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
                    output_signature_count=len(unit["output_signatures"]),
                )
                current_stage, overall_status, progress, next_action = self._rollup(stages)
                # Expose one authoritative next action. Future-stage actions stay
                # hidden until that stage becomes current.
                for stage in stages:
                    if stage["key"] != current_stage:
                        stage["next_action"] = None
                blocker_count = sum(len(item["blockers"]) for item in stages)
                record_id = _unit_id(offline_job_id, unit["asset_key"])
                keep_ids.add(record_id)
                record = db.get(RuleAssetCommandUnitRecord, record_id)
                if record is None:
                    record = RuleAssetCommandUnitRecord(id=record_id)
                    db.add(record)
                models = sorted(str(item) for item in unit["models"] if item)
                record.offline_job_id = offline_job_id
                mapped_check_item_ids = sorted(
                    {item["check_item_id"] for item in mappings}
                )
                record.check_item_id = (
                    mapped_check_item_ids[0]
                    if len(mapped_check_item_ids) == 1
                    else None
                )
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
                record.output_signature_count = len(unit["output_signatures"])
                record.blocker_count = blocker_count
                record.parse_counts_json = dict(unit["parse_counts"])
                record.stage_states_json = stages
                record.next_action_json = next_action or {}
                record.parser_job_id = str(parser_job.id) if parser_job else None
                record.parser_template_id = template_id
                record.rule_seed_job_id = str(seed.id) if seed else None
                record.context_id = str(context.id) if context else None
                record.bundle_id = str(bundle.id) if bundle else None
                record.projection_version = projection_version
                db.flush([record])
                db.query(RuleAssetCommandUnitCheckItemRecord).filter(
                    RuleAssetCommandUnitCheckItemRecord.command_unit_id == record_id
                ).delete(synchronize_session=False)
                for mapping in mappings:
                    link_id = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            "netops/rule-asset-command-check-item/"
                            f"{record_id}/{mapping['check_item_id']}/"
                            f"{mapping['implementation_version_id']}",
                        )
                    )
                    db.add(
                        RuleAssetCommandUnitCheckItemRecord(
                            id=link_id,
                            command_unit_id=record_id,
                            **mapping,
                            mapping_source="catalog_resolver",
                        )
                    )
                    mapped_link_count += 1

            stale_query = db.query(RuleAssetCommandUnitRecord).filter(
                RuleAssetCommandUnitRecord.offline_job_id == offline_job_id
            )
            if keep_ids:
                stale_query = stale_query.filter(~RuleAssetCommandUnitRecord.id.in_(keep_ids))
            deleted = stale_query.delete(synchronize_session=False)
            return {
                "offline_job_id": offline_job_id,
                "unit_count": len(keep_ids),
                "check_item_link_count": mapped_link_count,
                "parser_job_sync": parser_job_sync,
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
        self._ensure_projection_current(offline_job_id)
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
                query = query.filter(_models_contain(model))
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

    def list_production_units(
        self,
        *,
        owner_id: str,
        is_admin: bool = False,
        page: int = 1,
        page_size: int = 25,
        search: str | None = None,
        bucket: str | None = None,
        governance_status: str | None = None,
        stage: str | None = None,
        vendor: str | None = None,
        model: str | None = None,
        asset_mode: str | None = None,
        offline_job_id: str | None = None,
    ) -> dict[str, Any]:
        """List the latest product-facing command asset for each stable asset key."""

        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))
        if bucket and bucket not in GOVERNANCE_BUCKETS:
            raise ValueError(f"unsupported governance bucket: {bucket}")
        if governance_status and governance_status not in GOVERNANCE_STATUSES:
            raise ValueError(f"unsupported governance status: {governance_status}")

        with get_db_session() as db:
            ranked_query = (
                db.query(
                    RuleAssetCommandUnitRecord.id.label("unit_id"),
                    func.row_number()
                    .over(
                        partition_by=RuleAssetCommandUnitRecord.asset_key,
                        order_by=(
                            RuleAssetCommandUnitRecord.updated_at.desc(),
                            RuleAssetCommandUnitRecord.id.desc(),
                        ),
                    )
                    .label("asset_rank"),
                )
                .join(
                    OfflinePatrolJobRecord,
                    OfflinePatrolJobRecord.id == RuleAssetCommandUnitRecord.offline_job_id,
                )
                .filter(
                    OfflinePatrolJobRecord.purpose == "rule_asset_source",
                    OfflinePatrolJobRecord.archived.is_(False),
                )
            )
            if not is_admin:
                ranked_query = ranked_query.filter(OfflinePatrolJobRecord.owner_id == owner_id)
            if offline_job_id:
                ranked_query = ranked_query.filter(
                    RuleAssetCommandUnitRecord.offline_job_id == offline_job_id
                )
            ranked = ranked_query.subquery()

            base_query = (
                db.query(RuleAssetCommandUnitRecord, OfflinePatrolJobRecord)
                .join(ranked, ranked.c.unit_id == RuleAssetCommandUnitRecord.id)
                .join(
                    OfflinePatrolJobRecord,
                    OfflinePatrolJobRecord.id == RuleAssetCommandUnitRecord.offline_job_id,
                )
                .filter(ranked.c.asset_rank == 1)
            )
            summary_rows = (
                base_query.with_entities(
                    RuleAssetCommandUnitRecord.status,
                    RuleAssetCommandUnitRecord.current_stage,
                    RuleAssetCommandUnitRecord.asset_mode,
                    func.count(RuleAssetCommandUnitRecord.id),
                )
                .group_by(
                    RuleAssetCommandUnitRecord.status,
                    RuleAssetCommandUnitRecord.current_stage,
                    RuleAssetCommandUnitRecord.asset_mode,
                )
                .all()
            )
            summary = self._production_summary(summary_rows)

            query = base_query
            if search and search.strip():
                pattern = f"%{search.strip()}%"
                query = query.filter(
                    or_(
                        RuleAssetCommandUnitRecord.canonical_command.ilike(pattern),
                        RuleAssetCommandUnitRecord.vendor.ilike(pattern),
                        RuleAssetCommandUnitRecord.model.ilike(pattern),
                        RuleAssetCommandUnitRecord.family.ilike(pattern),
                        RuleAssetCommandUnitRecord.profile.ilike(pattern),
                        OfflinePatrolJobRecord.name.ilike(pattern),
                    )
                )
            query = self._apply_governance_filter(
                query,
                bucket=bucket,
                governance_status=governance_status,
            )
            if stage:
                query = query.filter(RuleAssetCommandUnitRecord.current_stage == stage)
            if vendor:
                query = query.filter(RuleAssetCommandUnitRecord.vendor == vendor)
            if model:
                query = query.filter(_models_contain(model))
            if asset_mode:
                query = query.filter(RuleAssetCommandUnitRecord.asset_mode == asset_mode)

            total = query.count()
            rows = (
                query.order_by(
                    case(
                        (
                            RuleAssetCommandUnitRecord.status.in_(["blocked", "failed"]),
                            0,
                        ),
                        (RuleAssetCommandUnitRecord.status == "needs_review", 1),
                        (
                            RuleAssetCommandUnitRecord.status.in_(["ready", "not_started"]),
                            2,
                        ),
                        (RuleAssetCommandUnitRecord.status == "running", 3),
                        else_=4,
                    ),
                    RuleAssetCommandUnitRecord.updated_at.desc(),
                    RuleAssetCommandUnitRecord.canonical_command.asc(),
                    RuleAssetCommandUnitRecord.id.asc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return {
                "items": [
                    self._serialize_production_row(unit, source_job)
                    for unit, source_job in rows
                ],
                "page": page,
                "page_size": page_size,
                "total": int(total),
                "page_count": (int(total) + page_size - 1) // page_size,
                "summary": summary,
            }

    def list_check_item_production_units(
        self,
        *,
        owner_id: str,
        is_admin: bool = False,
        page: int = 1,
        page_size: int = 25,
        search: str | None = None,
        bucket: str | None = None,
        governance_status: str | None = None,
        stage: str | None = None,
        vendor: str | None = None,
        model: str | None = None,
        asset_mode: str | None = None,
        offline_job_id: str | None = None,
    ) -> dict[str, Any]:
        """Return logical check items with expandable physical command implementations."""

        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))
        if bucket and bucket not in GOVERNANCE_BUCKETS:
            raise ValueError(f"unsupported governance bucket: {bucket}")
        if governance_status and governance_status not in GOVERNANCE_STATUSES:
            raise ValueError(f"unsupported governance status: {governance_status}")
        self._refresh_stale_product_sources(
            owner_id=owner_id,
            is_admin=is_admin,
            offline_job_id=offline_job_id,
        )
        catalog = self.catalog_repository.load_active()
        check_items = {item.id: item for item in catalog.check_items}
        implementations = {
            item.version_id: item for item in catalog.implementations
        }

        with get_db_session() as db:
            ranked_query = (
                db.query(
                    RuleAssetCommandUnitRecord.id.label("unit_id"),
                    func.row_number()
                    .over(
                        partition_by=RuleAssetCommandUnitRecord.asset_key,
                        order_by=(
                            RuleAssetCommandUnitRecord.updated_at.desc(),
                            RuleAssetCommandUnitRecord.id.desc(),
                        ),
                    )
                    .label("asset_rank"),
                )
                .join(
                    OfflinePatrolJobRecord,
                    OfflinePatrolJobRecord.id == RuleAssetCommandUnitRecord.offline_job_id,
                )
                .filter(
                    OfflinePatrolJobRecord.purpose == "rule_asset_source",
                    OfflinePatrolJobRecord.archived.is_(False),
                )
            )
            if not is_admin:
                ranked_query = ranked_query.filter(
                    OfflinePatrolJobRecord.owner_id == owner_id
                )
            if offline_job_id:
                ranked_query = ranked_query.filter(
                    RuleAssetCommandUnitRecord.offline_job_id == offline_job_id
                )
            ranked = ranked_query.subquery()
            query = (
                db.query(RuleAssetCommandUnitRecord, OfflinePatrolJobRecord)
                .join(ranked, ranked.c.unit_id == RuleAssetCommandUnitRecord.id)
                .join(
                    OfflinePatrolJobRecord,
                    OfflinePatrolJobRecord.id == RuleAssetCommandUnitRecord.offline_job_id,
                )
                .filter(ranked.c.asset_rank == 1)
            )
            if vendor:
                query = query.filter(RuleAssetCommandUnitRecord.vendor == vendor)
            if model:
                query = query.filter(_models_contain(model))
            if asset_mode:
                query = query.filter(
                    RuleAssetCommandUnitRecord.asset_mode == asset_mode
                )
            rows = query.all()
            unit_ids = [str(unit.id) for unit, _source in rows]
            link_rows = (
                [
                    {
                        "command_unit_id": str(row.command_unit_id),
                        "check_item_id": str(row.check_item_id),
                        "check_item_version_id": str(row.check_item_version_id),
                        "implementation_id": str(row.implementation_id),
                        "implementation_version_id": str(
                            row.implementation_version_id
                        ),
                    }
                    for row in (
                        db.query(RuleAssetCommandUnitCheckItemRecord)
                        .filter(
                            RuleAssetCommandUnitCheckItemRecord.command_unit_id.in_(
                                unit_ids
                            )
                        )
                        .order_by(
                            RuleAssetCommandUnitCheckItemRecord.check_item_id,
                            RuleAssetCommandUnitCheckItemRecord.implementation_version_id,
                        )
                        .all()
                    )
                ]
                if unit_ids
                else []
            )
            serialized_rows = [
                self._serialize_production_row(unit, source_job)
                for unit, source_job in rows
            ]
            assembly_planner = RuntimeBundleAssemblyPlanner(db)
            assembly_plans: dict[tuple[str, str], dict[str, Any]] = {}
            for link in link_rows:
                plan_key = (
                    link["check_item_version_id"],
                    link["implementation_version_id"],
                )
                if plan_key in assembly_plans:
                    continue
                try:
                    assembly_plans[plan_key] = assembly_planner.build(
                        check_item_version_id=plan_key[0],
                        implementation_version_id=plan_key[1],
                    )
                except Exception as exc:
                    logger.exception(
                        "runtime bundle projection failed for %s / %s",
                        *plan_key,
                    )
                    assembly_plans[plan_key] = {
                        "check_item": {
                            "version_id": plan_key[0],
                        },
                        "implementation": {
                            "version_id": plan_key[1],
                        },
                        "asset_requirement_profile": {},
                        "selected": {},
                        "compatibility": {},
                        "candidates": {},
                        "operationally_ready": False,
                        "active_bundle": None,
                        "blockers": [
                            {
                                "code": "runtime_bundle_projection_failed",
                                "message": (
                                    "运行包装配预检失败，不能判定生产就绪状态。"
                                ),
                                "context": {
                                    "error_type": type(exc).__name__,
                                },
                            }
                        ],
                        "warnings": [],
                    }

        links_by_unit: dict[str, list[dict[str, str]]] = defaultdict(list)
        for link in link_rows:
            links_by_unit[link["command_unit_id"]].append(link)

        grouped: dict[str, dict[str, Any]] = {}
        for serialized in serialized_rows:
            links = links_by_unit.get(str(serialized["id"]), [])
            if not links:
                links = [None]
            for link in links:
                check_item_id = link["check_item_id"] if link is not None else ""
                ignored_without_catalog = bool(
                    link is None
                    and str(serialized.get("asset_mode") or "") == "ignored"
                )
                group_key = (
                    check_item_id
                    or (
                        "__not_applicable__"
                        if ignored_without_catalog
                        else "__unclassified__"
                    )
                )
                check_item = check_items.get(check_item_id)
                implementation = (
                    implementations.get(link["implementation_version_id"])
                    if link is not None
                    else None
                )
                assembly_plan = (
                    assembly_plans.get(
                        (
                            link["check_item_version_id"],
                            link["implementation_version_id"],
                        )
                    )
                    if link is not None
                    else None
                )
                group = grouped.setdefault(
                    group_key,
                    {
                        "check_item_id": check_item_id or None,
                        "check_item_version_id": (
                            link["check_item_version_id"] if link is not None else None
                        ),
                        "name": (
                            check_item.name if check_item is not None else "待归类检查项"
                        ),
                        "display_name": (
                            check_item_display_name(
                                check_item.id,
                                check_item.name,
                            )
                            if check_item is not None
                            else "待归类检查项"
                        ),
                        "category": (
                            check_item.category if check_item is not None else "unclassified"
                        ),
                        "description": (
                            check_item.description
                            if check_item is not None
                            else "命令输出尚未绑定已审批的检查项目录，不能进入生产运行包。"
                        ),
                        "display_description": (
                            check_item_display_description(
                                check_item.id,
                                check_item.description,
                            )
                            if check_item is not None
                            else "命令输出尚未绑定已审批的检查项目录，不能进入生产运行包。"
                        ),
                        "result_mode": (
                            check_item.result_mode if check_item is not None else None
                        ),
                        "assessment_mode": (
                            check_item.assessment_mode if check_item is not None else None
                        ),
                        "asset_requirement_profile": (
                            dict(
                                (assembly_plan or {}).get(
                                    "asset_requirement_profile"
                                )
                                or {}
                            )
                            if assembly_plan is not None
                            else {}
                        ),
                        "implementations": [],
                    },
                )
                if check_item is None and ignored_without_catalog:
                    group.update(
                        {
                            "name": "No rule required",
                            "display_name": "无需规则命令",
                            "category": "not_applicable",
                            "description": (
                                "The collection policy explicitly ignores these "
                                "commands, so Parser and rule assets are not required."
                            ),
                            "display_description": (
                                "采集策略已明确忽略这些命令，不需要 Parser 或规则资产。"
                            ),
                        }
                    )
                child = {
                    **serialized,
                    "implementation_id": (
                        implementation.id if implementation is not None else None
                    ),
                    "implementation_version_id": (
                        implementation.version_id
                        if implementation is not None
                        else None
                    ),
                    "implementation_name": (
                        implementation.name if implementation is not None else None
                    ),
                    "mapping_status": (
                        "catalog_resolved"
                        if link is not None
                        else "not_required"
                        if ignored_without_catalog
                        else "unclassified"
                    ),
                }
                group["implementations"].append(
                    self._project_check_item_implementation(
                        child,
                        assembly_plan,
                    )
                    if assembly_plan is not None
                    else child
                )

        groups = [
            self._rollup_check_item_group(key, value)
            for key, value in grouped.items()
        ]
        query_text = _norm(search)
        if query_text:
            groups = [
                item
                for item in groups
                if query_text
                in _norm(
                    " ".join(
                        [
                            str(item.get("check_item_id") or ""),
                            str(item.get("name") or ""),
                            str(item.get("display_name") or ""),
                            str(item.get("display_description") or ""),
                            str(item.get("category") or ""),
                            *[
                                " ".join(
                                    [
                                        str(child.get("canonical_command") or ""),
                                        str(child.get("vendor") or ""),
                                        str(child.get("profile") or ""),
                                        " ".join(child.get("models") or []),
                                        str(
                                            (child.get("source_job") or {}).get("name")
                                            or ""
                                        ),
                                    ]
                                )
                                for child in item["implementations"]
                            ],
                        ]
                    )
                )
            ]
        summary = self._check_item_production_summary(groups)
        if bucket:
            groups = [
                item for item in groups if item["governance_bucket"] == bucket
            ]
        if governance_status:
            groups = [
                item
                for item in groups
                if item["governance_status"] == governance_status
            ]
        if stage:
            groups = [
                item
                for item in groups
                if item["current_stage"] == stage
                or any(
                    child.get("current_stage") == stage
                    for child in item["implementations"]
                )
            ]
        urgency = {
            "exception": 0,
            "awaiting_approval": 1,
            "action_required": 2,
            "in_progress": 3,
            "published": 4,
            "not_applicable": 5,
        }
        groups.sort(
            key=lambda item: (
                urgency.get(str(item["governance_bucket"]), 9),
                -float(item.get("updated_timestamp") or 0),
                str(item.get("check_item_id") or "~unclassified"),
            )
        )
        total = len(groups)
        start = (page - 1) * page_size
        return {
            "items": groups[start : start + page_size],
            "page": page,
            "page_size": page_size,
            "total": total,
            "page_count": (total + page_size - 1) // page_size,
            "summary": summary,
        }

    def _refresh_stale_product_sources(
        self,
        *,
        owner_id: str,
        is_admin: bool,
        offline_job_id: str | None,
    ) -> None:
        expected_projection_version = _catalog_projection_version(
            self.catalog_repository.load_active()
        )
        with get_db_session() as db:
            query = (
                db.query(RuleAssetCommandUnitRecord.offline_job_id)
                .join(
                    OfflinePatrolJobRecord,
                    OfflinePatrolJobRecord.id == RuleAssetCommandUnitRecord.offline_job_id,
                )
                .filter(
                    OfflinePatrolJobRecord.purpose == "rule_asset_source",
                    OfflinePatrolJobRecord.archived.is_(False),
                    RuleAssetCommandUnitRecord.projection_version
                    != expected_projection_version,
                )
            )
            if not is_admin:
                query = query.filter(OfflinePatrolJobRecord.owner_id == owner_id)
            if offline_job_id:
                query = query.filter(
                    RuleAssetCommandUnitRecord.offline_job_id == offline_job_id
                )
            stale_job_ids = sorted(
                {str(job_id) for (job_id,) in query.distinct().all() if job_id}
            )
        for job_id in stale_job_ids:
            self.reconcile(job_id)

    @staticmethod
    def _plan_blocker_matches(component: str, code: str) -> bool:
        normalized = str(code or "").casefold()
        if component == "event" and "current_state" in normalized:
            return False
        markers = {
            "identity": ("identity_policy", "entity_identity_policy"),
            "parser": (
                "parser",
                "structured_command",
                "command_route",
                "no_executable_structured_command",
                "implementation_scope",
            ),
            "contract": (
                "observation_contract",
                "canonical_observation",
                "contract_",
                "entity_type",
                "identity_dimension",
            ),
            "current_state": ("current_state",),
            "policy": ("change_policy",),
            "event": (
                "event_rule",
                "event_type",
                "event_class",
                "event_severity",
                "recovery_pair",
            ),
            "comparison": ("comparison_profile",),
        }
        return any(marker in normalized for marker in markers.get(component, ()))

    @classmethod
    def _project_check_item_implementation(
        cls,
        child: dict[str, Any],
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        """Project one command source through the check item's immutable asset contract."""

        projected = dict(child)
        source_component_statuses = dict(child.get("component_statuses") or {})
        profile = dict(plan.get("asset_requirement_profile") or {})
        requirements = dict(profile.get("requirements") or {})
        selected = dict(plan.get("selected") or {})
        compatibility = dict(plan.get("compatibility") or {})
        candidates = dict(plan.get("candidates") or {})
        blockers = [
            dict(item)
            for item in plan.get("blockers") or []
            if isinstance(item, dict)
        ]
        if (
            child.get("handling") == "not_applicable"
            and child.get("asset_mode") == "ignored"
        ):
            component_statuses = {
                component: "not_applicable"
                for component in CHECK_ITEM_COMPONENT_ORDER
            }
            projected.update(
                {
                    "source_governance_status": child.get("governance_status"),
                    "source_governance_bucket": child.get("governance_bucket"),
                    "source_component_statuses": dict(
                        child.get("component_statuses") or {}
                    ),
                    "governance_status": "not_applicable",
                    "governance_bucket": "not_applicable",
                    "component_statuses": component_statuses,
                    "current_stage": "release",
                    "current_stage_label": "无需规则",
                    "progress_percent": 100,
                    "blocker_count": 0,
                    "next_action": None,
                    "asset_requirement_profile": profile,
                    "runtime_readiness": {
                        "operationally_ready": False,
                        "active_bundle": None,
                        "blockers": [],
                        "warnings": [],
                        "preview_digest": plan.get("preview_digest"),
                        "applicability": "not_applicable",
                    },
                }
            )
            return projected
        blocker_codes = {
            str(item.get("code") or "").strip()
            for item in blockers
            if str(item.get("code") or "").strip()
        }
        required_current_types = set(
            requirements.get("current_state_rule", {}).get(
                "required_observation_types"
            )
            or []
        )
        covered_current_types = set(
            compatibility.get("current_state_rule_types") or []
        )
        required_policy_types = set(
            requirements.get("change_policy", {}).get(
                "required_observation_types"
            )
            or []
        )
        covered_policy_types = set(
            compatibility.get("change_policy_types") or []
        )
        candidate_rows = {
            "parser": list(candidates.get("parsers") or []),
            "contract": list(candidates.get("observation_contracts") or []),
            "current_state": list(candidates.get("event_rules") or []),
            "policy": list(candidates.get("change_policies") or []),
            "event": list(candidates.get("event_rules") or []),
            "comparison": list(candidates.get("comparison_profiles") or []),
        }
        selected_assets = {
            "identity": bool(selected.get("entity_identity_policy_version_id")),
            "parser": bool(selected.get("parser_template_ids")),
            "contract": bool(selected.get("observation_contract_ids")),
            "current_state": bool(
                required_current_types
                and required_current_types <= covered_current_types
            ),
            "policy": bool(
                required_policy_types
                and required_policy_types <= covered_policy_types
            ),
            "event": bool(selected.get("event_rule_version_ids")),
            "comparison": bool(selected.get("comparison_profile_version_id")),
        }
        fallback_keys = {
            "parser": "parser",
            "contract": "contract",
            "current_state": "event",
            "policy": "policy",
            "event": "event",
        }

        component_statuses: dict[str, str] = {}
        for component, requirement_key in CHECK_ITEM_COMPONENT_REQUIREMENTS.items():
            requirement = dict(requirements.get(requirement_key) or {})
            if requirement.get("required") is not True:
                component_statuses[component] = "not_applicable"
                continue
            has_component_blocker = any(
                cls._plan_blocker_matches(component, code)
                for code in blocker_codes
            )
            if selected_assets.get(component) and not has_component_blocker:
                component_statuses[component] = "published"
                continue
            fallback_status = str(
                source_component_statuses.get(
                    fallback_keys.get(component, ""),
                    "",
                )
                or ""
            )
            if fallback_status in {
                "generating",
                "engineering_review",
                "failed",
                "ready_to_generate",
            }:
                component_statuses[component] = fallback_status
            elif candidate_rows.get(component) or fallback_status == "published":
                component_statuses[component] = "blocked"
            elif component == "identity":
                component_statuses[component] = "blocked"
            else:
                component_statuses[component] = "ready_to_generate"

        active_bundle = dict(plan.get("active_bundle") or {})
        operationally_ready = plan.get("operationally_ready") is True
        if active_bundle and operationally_ready:
            runtime_status = "published"
        elif operationally_ready:
            runtime_status = "ready_to_generate"
        else:
            runtime_status = "blocked"
        component_statuses["runtime_bundle"] = runtime_status

        relevant_statuses = [
            status
            for status in component_statuses.values()
            if status != "not_applicable"
        ]
        if runtime_status == "published":
            governance_status = "published"
            governance_bucket = "published"
        elif "failed" in relevant_statuses:
            governance_status = "failed"
            governance_bucket = "exception"
        elif "generating" in relevant_statuses:
            governance_status = "generating"
            governance_bucket = "in_progress"
        elif "engineering_review" in relevant_statuses:
            governance_status = "engineering_review"
            governance_bucket = "awaiting_approval"
        elif "blocked" in relevant_statuses:
            governance_status = "blocked"
            governance_bucket = "action_required"
        else:
            governance_status = "ready_to_generate"
            governance_bucket = "action_required"

        runtime_url = _runtime_bundle_url(
            check_item_id=str(plan.get("check_item", {}).get("id") or ""),
            check_item_version_id=str(
                plan.get("check_item", {}).get("version_id") or ""
            ),
            implementation_version_id=str(
                plan.get("implementation", {}).get("version_id") or ""
            ),
        )
        source_next_action = child.get("next_action")
        source_action_pending = bool(source_next_action) and child.get(
            "governance_bucket"
        ) not in {"published", "not_applicable"}
        if runtime_status == "published":
            next_action = None
        elif source_action_pending:
            # Runtime assembly is the final production step. Keep the direct
            # Parser/rule-production action visible while an upstream asset is
            # unresolved so the operator lands where the blocker can be fixed.
            next_action = dict(source_next_action)
        elif runtime_status == "ready_to_generate":
            next_action = _action(
                "assemble_runtime_bundle",
                "装配运行包",
                "所需资产已齐备；复核预检结果并原子发布冻结运行包。",
                runtime_url,
            )
        else:
            next_action = _action(
                "inspect_runtime_bundle",
                "处理运行包缺口",
                "查看该检查项与采集实现的精确资产缺口和修复入口。",
                runtime_url,
            )

        resolved_count = sum(
            status in {"published", "not_applicable"}
            for status in component_statuses.values()
        )
        current_stage = (
            str(child.get("current_stage") or "runtime_bundle")
            if source_action_pending
            else "runtime_bundle"
        )
        current_stage_label = (
            str(child.get("current_stage_label") or "")
            if source_action_pending
            else "生产运行包"
        )
        projected.update(
            {
                "source_governance_status": child.get("governance_status"),
                "source_governance_bucket": child.get("governance_bucket"),
                "source_component_statuses": source_component_statuses,
                "governance_status": governance_status,
                "governance_bucket": governance_bucket,
                "component_statuses": component_statuses,
                "current_stage": current_stage,
                "current_stage_label": current_stage_label,
                "progress_percent": round(
                    resolved_count * 100 / max(len(component_statuses), 1)
                ),
                "blocker_count": len(blockers),
                "next_action": next_action,
                "asset_requirement_profile": profile,
                "runtime_readiness": {
                    "operationally_ready": operationally_ready,
                    "active_bundle": active_bundle or None,
                    "blockers": blockers,
                    "warnings": [
                        dict(item)
                        for item in plan.get("warnings") or []
                        if isinstance(item, dict)
                    ],
                    "preview_digest": plan.get("preview_digest"),
                },
            }
        )
        return projected

    @classmethod
    def _rollup_check_item_group(
        cls,
        group_key: str,
        group: dict[str, Any],
    ) -> dict[str, Any]:
        children = list(group["implementations"])
        urgency = {
            "exception": 0,
            "awaiting_approval": 1,
            "action_required": 2,
            "in_progress": 3,
            "published": 4,
            "not_applicable": 5,
        }
        lead = min(
            children,
            key=lambda item: (
                urgency.get(str(item.get("governance_bucket")), 9),
                int(item.get("progress_percent") or 0),
                str(item.get("id") or ""),
            ),
        )
        component_rank = {
            "failed": 0,
            "blocked": 1,
            "engineering_review": 2,
            "ready_to_generate": 3,
            "generating": 4,
            "not_started": 5,
            "published": 6,
            "not_applicable": 7,
        }
        component_statuses = {
            component: min(
                (
                    str(child.get("component_statuses", {}).get(component) or "not_started")
                    for child in children
                ),
                key=lambda status: component_rank.get(status, 99),
            )
            for component in CHECK_ITEM_COMPONENT_ORDER
        }
        updated_values = [
            datetime.fromisoformat(str(child["updated_at"]).replace("Z", "+00:00"))
            for child in children
            if child.get("updated_at")
        ]
        updated_at = max(updated_values).isoformat() if updated_values else None
        published_count = sum(
            child.get("governance_bucket") == "published" for child in children
        )
        unclassified = group_key == "__unclassified__"
        all_not_applicable = bool(children) and all(
            child.get("governance_bucket") == "not_applicable"
            for child in children
        )
        classification_required = unclassified and not all_not_applicable
        governance_status = (
            "blocked" if classification_required else lead["governance_status"]
        )
        governance_bucket = (
            "exception" if classification_required else lead["governance_bucket"]
        )
        current_stage = (
            "identity_grouping" if classification_required else lead["current_stage"]
        )
        current_stage_label = (
            "检查项归类" if classification_required else lead["current_stage_label"]
        )
        blocker_count = sum(
            int(child.get("blocker_count") or 0) for child in children
        ) + (1 if classification_required else 0)
        next_action = None if all_not_applicable else lead.get("next_action")
        if classification_required:
            first_child = children[0]
            next_action = _action(
                "classify_check_item",
                "归类检查项",
                "将该物理采集实现绑定到已审批的巡检检查项后，才能进入生产运行包。",
                _parser_url(
                    str(first_child.get("offline_job_id") or ""),
                    str(first_child.get("canonical_command") or ""),
                    dataset_ids=[],
                ),
            )
        return {
            "id": f"check-item:{group_key}",
            **{key: value for key, value in group.items() if key != "implementations"},
            "classification_required": classification_required,
            "governance_status": governance_status,
            "governance_bucket": governance_bucket,
            "current_stage": current_stage,
            "current_stage_label": current_stage_label,
            "progress_percent": min(
                int(child.get("progress_percent") or 0) for child in children
            ),
            "sample_count": sum(int(child.get("sample_count") or 0) for child in children),
            "evidence_device_count": sum(
                int(child.get("device_count") or 0) for child in children
            ),
            "output_signature_count": sum(
                int(child.get("output_signature_count") or 0) for child in children
            ),
            "blocker_count": blocker_count,
            "implementation_count": len(children),
            "command_count": len(
                {str(child.get("canonical_command") or "") for child in children}
            ),
            "vendors": sorted(
                {str(child.get("vendor") or "") for child in children if child.get("vendor")}
            ),
            "component_statuses": component_statuses,
            "next_action": next_action,
            "updated_at": updated_at,
            "updated_timestamp": (
                max(value.timestamp() for value in updated_values)
                if updated_values
                else 0
            ),
            "coverage": {
                "total": len(children),
                "published": published_count,
                "action_required": sum(
                    child.get("governance_bucket") == "action_required"
                    for child in children
                ),
                "in_progress": sum(
                    child.get("governance_bucket") == "in_progress"
                    for child in children
                ),
                "awaiting_approval": sum(
                    child.get("governance_bucket") == "awaiting_approval"
                    for child in children
                ),
                "exception": sum(
                    child.get("governance_bucket") == "exception"
                    for child in children
                ),
            },
            "implementations": sorted(
                children,
                key=lambda child: (
                    str(child.get("vendor") or ""),
                    str(child.get("profile") or ""),
                    str(child.get("canonical_command") or ""),
                    str(child.get("id") or ""),
                ),
            ),
        }

    @staticmethod
    def _check_item_production_summary(
        groups: list[dict[str, Any]],
    ) -> dict[str, Any]:
        by_bucket = Counter(str(item["governance_bucket"]) for item in groups)
        by_status = Counter(str(item["governance_status"]) for item in groups)
        return {
            "total": len(groups),
            "action_required": int(by_bucket["action_required"]),
            "in_progress": int(by_bucket["in_progress"]),
            "awaiting_approval": int(by_bucket["awaiting_approval"]),
            "published": int(by_bucket["published"]),
            "not_applicable": int(by_bucket["not_applicable"]),
            "exception": int(by_bucket["exception"]),
            "unclassified": sum(
                bool(item.get("classification_required")) for item in groups
            ),
            "by_bucket": dict(by_bucket),
            "by_status": dict(by_status),
        }

    def get_production_unit(
        self,
        unit_id: str,
        *,
        owner_id: str,
        is_admin: bool = False,
        evidence_page: int = 1,
        evidence_page_size: int = 25,
    ) -> dict[str, Any]:
        with get_db_session() as db:
            query = (
                db.query(RuleAssetCommandUnitRecord, OfflinePatrolJobRecord)
                .join(
                    OfflinePatrolJobRecord,
                    OfflinePatrolJobRecord.id == RuleAssetCommandUnitRecord.offline_job_id,
                )
                .filter(
                    RuleAssetCommandUnitRecord.id == unit_id,
                    OfflinePatrolJobRecord.purpose == "rule_asset_source",
                    OfflinePatrolJobRecord.archived.is_(False),
                )
            )
            if not is_admin:
                query = query.filter(OfflinePatrolJobRecord.owner_id == owner_id)
            match = query.first()
            if match is None:
                raise KeyError("command asset unit not found")
            row, source_job = match
            offline_job_id = str(row.offline_job_id)
            source_metadata = {
                "id": source_job.id,
                "name": source_job.name,
                "status": source_job.status,
                "created_at": _iso(source_job.created_at),
            }

        result = self.get_unit(
            offline_job_id,
            unit_id,
            evidence_page=evidence_page,
            evidence_page_size=evidence_page_size,
        )
        result.update(self._serialize_product_state(result))
        result["source_job"] = source_metadata
        return result

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
        self._ensure_projection_current(offline_job_id)
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
        self._ensure_projection_current(offline_job_id)
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
        parser_url = _parser_url(
            offline_job_id,
            command,
            dataset_ids=dataset_ids or [],
        )
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
        bundle_payload = dict(bundle.payload_json or {}) if bundle else {}
        bundle_report = dict(bundle.validation_report_json or {}) if bundle else {}
        bundle_gate = dict(bundle_report.get("bundle_gate") or {})
        context_payload = dict(context.context_json or {}) if context else {}
        explicit_requirements = (
            bundle_gate.get("asset_requirements")
            or bundle_report.get("asset_requirements")
            or bundle_payload.get("asset_requirements")
            or context_payload.get("rule_asset_requirements")
        )
        if not explicit_requirements:
            asset_mode = str(unit.get("asset_mode") or "full_chain")
            explicit_requirements = {
                "schema_version": "rule-asset-requirements/command-mode-v1",
                "requires_observation_contract": True,
                "requires_change_policy": asset_mode in {"change_only", "full_chain"},
                "requires_current_state_rule": False,
                "requires_event_rule": asset_mode == "full_chain",
                "requires_event_rule_assets": asset_mode == "full_chain",
                "requires_historical_replay": asset_mode == "full_chain",
            }
            requirements_are_governed = False
        else:
            requirements_are_governed = True
        requirements = normalize_rule_asset_requirements(explicit_requirements)
        if requirements_are_governed:
            if requirements["requires_event_rule_assets"]:
                unit["asset_mode"] = "full_chain"
            elif requirements["requires_change_policy"]:
                unit["asset_mode"] = "change_only"
            elif requirements["requires_observation_contract"]:
                unit["asset_mode"] = "observation_only"
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
        elif parser_job.status == "collecting":
            parser_generation_status = "ready"
            parser_generation_blockers = []
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
                        "open_parser_job",
                        (
                            "配置并生成 Parser"
                            if parser_job is not None and parser_job.status == "collecting"
                            else "处理 Parser"
                        ),
                        "确认 ParserSpec 与样本期望，生成、验证并审批 Parser。",
                        parser_url,
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
        stages.append(
            _stage(
                "parser_validation",
                validation_status,
                "展示 TextFSM 编译、逐样本执行与解析错误等客观证据，不替代语义审批。",
                metrics={
                    "output_signature_count": output_signature_count,
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
                "确认历史快照已使用 Active Parser 回填，并显式暴露缺模板、解析失败或身份未知。",
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
        elif contract_status == "not_started":
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
                    if contract_status == "passed" or bundle is not None
                    else _action(
                        "generate_rule_asset_bundle",
                        "生成完整规则资产包",
                        "从离线结构化样本生成 Contract、Policy 和 EventRule 候选。",
                        generation_url,
                    )
                ),
            )
        )

        policy_status, policy_children = _child_stage_status(
            children, "CHANGE_POLICY", bundle_status=bundle_status
        )
        if not requirements["requires_change_policy"]:
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
                    if (
                        policy_status in RESOLVED_STAGE_STATUSES
                        or contract_status != "passed"
                        or bundle is not None
                    )
                    else _action(
                        "generate_rule_asset_bundle",
                        "生成完整规则资产包",
                        "从离线结构化样本生成缺失的变化策略和事件规则候选。",
                        generation_url,
                    )
                ),
            )
        )

        event_status, event_children = _child_stage_status(
            children, "EVENT_RULE", bundle_status=bundle_status
        )
        if not requirements["requires_event_rule_assets"]:
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
                    if (
                        event_status in RESOLVED_STAGE_STATUSES
                        or contract_status != "passed"
                        or policy_status not in RESOLVED_STAGE_STATUSES
                        or bundle is not None
                    )
                    else _action(
                        "generate_rule_asset_bundle",
                        "生成完整规则资产包",
                        "从离线结构化样本补齐事件与恢复规则候选。",
                        generation_url,
                    )
                ),
            )
        )

        report = bundle_report
        replay = dict(report.get("historical_replay") or {})
        context_gate = dict(report.get("context_gate") or {})
        replay_gate = evaluate_requirement_replay_gate(
            requirements=requirements,
            replay=replay,
            context_gate=context_gate,
        )
        review = dict((report.get("governance") or {}).get("engineering_review") or {})
        validation_success = report.get("success") is True
        replay_success = replay_gate.get("success") is True
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
            review_blockers.extend(str(item) for item in replay_gate.get("errors") or [])
        stages.append(
            _stage(
                "validation_review",
                review_status,
                "执行合成验证、历史回放、碰撞检查和工程师修订复核。",
                metrics={
                    "matched_change_count": replay.get("matched_change_count"),
                    "sampled_change_count": replay.get("sampled_change_count"),
                    "collision_count": replay.get("collision_count"),
                    "estimated_daily_event_count": replay.get("estimated_daily_event_count"),
                    "engineering_review_decision": review.get("decision"),
                },
                blockers=review_blockers,
                next_action=(
                    None
                    if review_status == "passed" or bundle is None
                    else _action(
                        "review_bundle", "验证并复核资产包", "查看回放并修订候选资产。", bundle_url
                    )
                ),
            )
        )
        if bundle_status == "active":
            release_status = "passed"
        elif (
            bundle_status == "pending_approval"
            and review_accepted
            and validation_success
            and replay_success
        ):
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
                    if release_status == "passed" or bundle is None
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
        unresolved = [
            item for item in stages if item["status"] not in RESOLVED_STAGE_STATUSES
        ]
        current = next(
            (
                item
                for item in unresolved
                if item["status"] in {"failed", "blocked"} and item.get("next_action")
            ),
            None,
        ) or next(
            (item for item in unresolved if item["status"] in {"failed", "blocked"}),
            None,
        ) or next(
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
    def _production_summary(rows: list[tuple[str, str, str, int]]) -> dict[str, Any]:
        by_bucket: Counter[str] = Counter()
        by_status: Counter[str] = Counter()
        total = 0
        for status, current_stage, asset_mode, count in rows:
            amount = int(count)
            total += amount
            by_bucket[
                _governance_bucket(str(status), str(current_stage), str(asset_mode))
            ] += amount
            by_status[
                _governance_status(str(status), str(current_stage), str(asset_mode))
            ] += amount
        return {
            "total": total,
            "action_required": int(by_bucket["action_required"]),
            "in_progress": int(by_bucket["in_progress"]),
            "awaiting_approval": int(by_bucket["awaiting_approval"]),
            "published": int(by_bucket["published"]),
            "not_applicable": int(by_bucket["not_applicable"]),
            "exception": int(by_bucket["exception"]),
            "by_bucket": dict(by_bucket),
            "by_status": dict(by_status),
        }

    @staticmethod
    def _apply_governance_filter(
        query: Any,
        *,
        bucket: str | None,
        governance_status: str | None,
    ) -> Any:
        status_column = RuleAssetCommandUnitRecord.status
        stage_column = RuleAssetCommandUnitRecord.current_stage
        if bucket == "action_required":
            query = query.filter(status_column.in_(["ready", "not_started"]))
        elif bucket == "in_progress":
            query = query.filter(status_column == "running")
        elif bucket == "awaiting_approval":
            query = query.filter(status_column == "needs_review")
        elif bucket == "published":
            query = query.filter(
                status_column == "completed",
                ~RuleAssetCommandUnitRecord.asset_mode.in_(["raw_hash", "ignored"]),
            )
        elif bucket == "not_applicable":
            query = query.filter(
                status_column == "completed",
                RuleAssetCommandUnitRecord.asset_mode.in_(["raw_hash", "ignored"]),
            )
        elif bucket == "exception":
            query = query.filter(status_column.in_(["blocked", "failed"]))

        if governance_status == "preparing":
            query = query.filter(
                status_column.in_(["ready", "not_started"]),
                stage_column.in_(["output_slicing", "identity_grouping"]),
            )
        elif governance_status == "ready_to_generate":
            query = query.filter(
                status_column.in_(["ready", "not_started"]),
                ~stage_column.in_(["output_slicing", "identity_grouping"]),
            )
        elif governance_status == "generating":
            query = query.filter(status_column == "running")
        elif governance_status == "engineering_review":
            query = query.filter(
                status_column == "needs_review",
                stage_column != "release",
            )
        elif governance_status == "release_approval":
            query = query.filter(
                status_column == "needs_review",
                stage_column == "release",
            )
        elif governance_status == "published":
            query = query.filter(
                status_column == "completed",
                ~RuleAssetCommandUnitRecord.asset_mode.in_(["raw_hash", "ignored"]),
            )
        elif governance_status == "not_applicable":
            query = query.filter(
                status_column == "completed",
                RuleAssetCommandUnitRecord.asset_mode.in_(["raw_hash", "ignored"]),
            )
        elif governance_status == "blocked":
            query = query.filter(status_column == "blocked")
        elif governance_status == "failed":
            query = query.filter(status_column == "failed")
        return query

    @classmethod
    def _serialize_product_state(cls, row: dict[str, Any]) -> dict[str, Any]:
        status = str(row.get("status") or "")
        current_stage = str(row.get("current_stage") or "")
        asset_mode = str(row.get("asset_mode") or "")
        stage_statuses = dict(row.get("stage_statuses") or {})
        return {
            "governance_status": _governance_status(status, current_stage, asset_mode),
            "governance_bucket": _governance_bucket(status, current_stage, asset_mode),
            "component_statuses": {
                "parser": _component_status(stage_statuses.get("parser_generation")),
                "contract": _component_status(stage_statuses.get("observation_contract")),
                "policy": _component_status(stage_statuses.get("change_policy")),
                "event": _component_status(stage_statuses.get("event_rule")),
            },
        }

    @classmethod
    def _serialize_production_row(
        cls,
        row: RuleAssetCommandUnitRecord,
        source_job: OfflinePatrolJobRecord,
    ) -> dict[str, Any]:
        result = cls._serialize_row(row)
        result.update(cls._serialize_product_state(result))
        result["source_job"] = {
            "id": source_job.id,
            "name": source_job.name,
            "status": source_job.status,
            "created_at": _iso(source_job.created_at),
        }
        return result

    @staticmethod
    def _serialize_row(
        row: RuleAssetCommandUnitRecord,
        *,
        include_stages: bool = False,
    ) -> dict[str, Any]:
        result = {
            "id": row.id,
            "offline_job_id": row.offline_job_id,
            "check_item_id": row.check_item_id,
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

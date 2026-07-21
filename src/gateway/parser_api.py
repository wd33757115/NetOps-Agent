"""Parser control-plane management API."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.auth.dependencies import require_role
from src.auth.models import CurrentUser
from src.core.celery_tasks.celery_app import celery
from src.core.collection.policy import (
    CollectionItem,
    CollectionPolicyResolver,
    CommandProfileRepository,
    ExecutionPlan,
)
from src.core.device_ops.repository import DeviceRepository
from src.core.device_ops.tools import PatrolTool
from src.core.observation.repository import ObservationContractRepository
from src.core.parser.assets import TemplateAssetStore
from src.core.parser.backfill import ParserBackfillService
from src.core.parser.bootstrap import bootstrap_shared_templates
from src.core.parser.dialect_audit import TextFSMDialectAuditService
from src.core.parser.discovery_assets import bootstrap_discovery_assets
from src.core.parser.identity import DeviceIdentity
from src.core.parser.repository import (
    OPEN_JOB_STATUSES,
    ParserGovernanceProjectionRepository,
    ParserJobRepository,
    ParserJobStateError,
    ParserMetadataRepository,
    ParserSampleRepository,
    ParserTemplateRepository,
)
from src.core.parser.runtime import ParserRuntime
from src.core.parser.tasks import dispatch_ready_parser_jobs
from src.core.parser.work_queue import ParserWorkQueueService
from src.core.patrol.change_policy_repository import ChangePolicyRepository
from src.core.patrol.event_rules import (
    EventRuleDefinition,
    EventRuleRepository,
    condition_matches,
)
from src.core.patrol.modes import PRODUCTION_MODE, normalize_patrol_mode
from src.core.patrol.raw_importer import import_raw_path, preview_raw_path
from src.core.patrol.repository import PatrolRepository
from src.core.patrol.rule_replay import replay_event_rule_definitions
from src.core.patrol.rule_validation import (
    EventRuleValidationCase,
    validate_event_rule_pack,
    validate_event_rule_set,
)
from src.core.rule_control import (
    AgentLoopRevisionExecutor,
    AgentLoopRevisionService,
    RuleAssetApprovalService,
    RuleAssetSubmissionService,
    RuleAssetTemplateActivationService,
    RuleGenerationJobRepository,
    RuleGenerationJobStateError,
    RuntimeRuleAssetResolver,
    RuntimeRuleAssetSeedService,
)
from src.core.rule_control.bundles import RuleAssetBundleService
from src.core.rule_control.engineering_revision import (
    RuleAssetEngineeringRevisionError,
    RuleAssetEngineeringRevisionService,
)

router = APIRouter(
    prefix="/api/v1/parser",
    tags=["Parser Control Plane"],
    dependencies=[Depends(require_role(["admin", "operator"]))],
)


def _authenticated_actor(request: Request) -> str:
    user = getattr(getattr(request, "state", None), "current_user", None)
    if not isinstance(user, CurrentUser):
        raise HTTPException(status_code=401, detail="authenticated user context is required")
    actor = str(user.username or user.user_id).strip()
    if not actor:
        raise HTTPException(status_code=401, detail="authenticated user identity is empty")
    return actor


def _required_reason(value: str | None, *, action: str) -> str:
    reason = str(value or "").strip()
    if not reason:
        raise HTTPException(status_code=422, detail=f"{action} requires a reason")
    return reason


class ParserDispatchControlRequest(BaseModel):
    enabled: bool
    reason: str


class ParserTemplateApprovalRequest(BaseModel):
    reason: str


class ParserTemplateForceActivationRequest(BaseModel):
    reason: str


class ParserTemplateStatusRequest(BaseModel):
    reason: str


class ParserBackfillCreateRequest(BaseModel):
    template_id: str
    filters: dict[str, Any] | None = None


class ParserBackfillCommitRequest(BaseModel):
    reason: str


class ParserBulkJobDeleteRequest(BaseModel):
    job_ids: list[str]
    reason: str


class ParserJobDeleteRequest(BaseModel):
    reason: str


class ParserImportRequest(BaseModel):
    source_path: str
    dataset_id: str | None = None
    vendor: str | None = None
    model: str | None = None


class EventRuleUpsertRequest(BaseModel):
    name: str
    event_type: str
    severity: str
    condition: dict[str, Any]
    description: str | None = None
    status: str = "draft"


class EventRuleTestRequest(BaseModel):
    condition: dict[str, Any]
    samples: list[dict[str, Any]]


class EventRuleValidationCaseRequest(BaseModel):
    name: str
    change: dict[str, Any]
    expected_event_type: str | None = None
    expected_rule_id: str | None = None


class EventRuleValidationRequest(BaseModel):
    cases: list[EventRuleValidationCaseRequest] | None = None


class EventRuleCandidateRequest(BaseModel):
    name: str | None = None
    rule_id: str | None = None
    version: int = 1
    event_type: str
    severity: str = "warning"
    condition: dict[str, Any]
    description: str | None = None


class EventRuleReplayRequest(BaseModel):
    candidate_rules: list[EventRuleCandidateRequest]
    limit: int = 1000
    include_active_collisions: bool = True


class RuleGenerationJobCreateRequest(BaseModel):
    job_type: str
    source_ref: str | None = None
    asset_ref: str | None = None
    asset_type: str | None = None
    asset_id: str | None = None
    asset_version_id: str | None = None
    asset_digest: str | None = None
    payload: dict[str, Any] | None = None
    validation_report: dict[str, Any] | None = None
    approval_required: bool = True
    status: str = "draft"


class RuleGenerationJobTransitionRequest(BaseModel):
    status: str
    validation_report: dict[str, Any] | None = None
    asset_ref: str | None = None
    asset_type: str | None = None
    asset_id: str | None = None
    asset_version_id: str | None = None
    asset_digest: str | None = None


class RuleGenerationJobValidationRequest(BaseModel):
    validation_report: dict[str, Any]


class RuleGenerationJobApprovalRequest(BaseModel):
    reason: str | None = None


class EventRuleForceActivationRequest(BaseModel):
    reason: str


class RuleAssetGenerationContextRequest(BaseModel):
    context_overrides: dict[str, Any] | None = None
    context_id: str | None = None
    dataset_ids: list[str] | None = None
    max_records: int = 120
    field_name: str | None = None
    observation_type: str | None = None


class RuleAssetEngineeringReviewRequest(BaseModel):
    decision: Literal["accepted", "revision_requested"]
    reason: str


class RuleAssetEngineeringRevisionRequest(BaseModel):
    contract: dict[str, Any]
    change_policies: list[dict[str, Any]]
    event_rules: list[dict[str, Any]]
    reason: str
    idempotency_key: str


class RuntimeRuleAssetResolveRequest(BaseModel):
    observation_schemas: list[tuple[str, str]] = []
    observation_types: list[str] = []
    include_event_rules: bool = True


class RuntimeRuleAssetSeedRequest(BaseModel):
    reason: str


class CommandProfileUpsertRequest(BaseModel):
    name: str
    action: str = "patrol"
    commands: list[str]
    status: str = "draft"


class CommandProfileLifecycleRequest(BaseModel):
    reason: str


class CommandProfileBindingUpsertRequest(BaseModel):
    profile_version_id: str
    selector: dict[str, Any]
    priority: int = 100
    status: str = "disabled"


class EffectivePlanPreviewRequest(BaseModel):
    device: dict[str, Any]
    action: str = "patrol"
    mode: str = "production"


class CommandProfileImpactPreviewRequest(BaseModel):
    profile_version_id: str | None = None
    profile_name: str | None = None
    action: str = "patrol"
    commands: list[str] | None = None
    selector: dict[str, Any] | None = None
    mode: str = "production"
    limit: int = 100
    save_evidence: bool = False


@router.get("/templates")
def list_templates(
    status: str | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    return ParserTemplateRepository().list_templates_page(
        status=status,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/templates/export")
def export_templates(status: str | None = "active"):
    repository = ParserTemplateRepository()
    asset_store = TemplateAssetStore()
    asset_root = asset_store.root.resolve()
    templates = repository.list_templates(status=status if status != "all" else None)
    manifest: list[dict[str, Any]] = []
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in templates:
            target = Path(str(item.get("asset_path") or "")).resolve()
            if not target.is_file() or not target.is_relative_to(asset_root):
                manifest.append({**item, "export_status": "missing_asset"})
                continue
            content = target.read_bytes()
            actual_sha256 = hashlib.sha256(content).hexdigest()
            export_path = f"templates/{item.get('vendor')}/{item.get('profile')}/{target.name}"
            archive.writestr(export_path, content)
            validation = item.get("validation_json") or {}
            archive.writestr(
                f"contracts/{item.get('id')}.json",
                json.dumps(validation, ensure_ascii=False, indent=2),
            )
            manifest.append(
                {
                    "id": item.get("id"),
                    "vendor": item.get("vendor"),
                    "model": item.get("model"),
                    "family": item.get("family"),
                    "profile": item.get("profile"),
                    "canonical_command": item.get("canonical_command"),
                    "category": item.get("category"),
                    "version": item.get("version"),
                    "status": item.get("status"),
                    "sha256": item.get("sha256"),
                    "actual_sha256": actual_sha256,
                    "hash_valid": actual_sha256 == item.get("sha256"),
                    "template_path": export_path,
                    "contract_path": f"contracts/{item.get('id')}.json",
                    "export_status": "exported",
                }
            )
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                    "status_filter": status,
                    "template_count": len(manifest),
                    "templates": manifest,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    payload = buffer.getvalue()
    return {
        "success": True,
        "filename": f"parser-templates-{status or 'all'}.zip",
        "content_base64": base64.b64encode(payload).decode("ascii"),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "template_count": len(manifest),
        "exported_count": sum(1 for item in manifest if item["export_status"] == "exported"),
        "manifest": manifest,
    }


@router.get("/templates/{template_id}/preview")
def preview_template(template_id: str):
    template = ParserTemplateRepository().get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="parser template not found")

    asset_store = TemplateAssetStore()
    asset_root = asset_store.root.resolve()
    target = Path(template.asset_path).resolve()
    if not target.is_relative_to(asset_root):
        raise HTTPException(
            status_code=400,
            detail="template asset path is outside the shared asset store",
        )
    if not target.is_file():
        raise HTTPException(status_code=404, detail="template asset file not found")

    return {
        "id": template.id,
        "vendor": template.vendor,
        "profile": template.profile,
        "canonical_command": template.canonical_command,
        "category": template.category,
        "version": template.version,
        "status": template.status,
        "sha256": template.sha256,
        "hash_valid": asset_store.verify(target, template.sha256),
        "content": target.read_text(encoding="utf-8"),
        "validation": template.validation_json or {},
    }


class _TemplateRegistry:
    def __init__(self, template):
        self.template = template

    def resolve_active(self, **_: Any):
        return self.template


def _parse_snapshot_with_template(template, snapshot):
    identity = DeviceIdentity(
        vendor=template.vendor,
        model=template.model or template.profile,
        family=template.family,
        confidence=1.0,
        source="template_comparison",
    )
    result = ParserRuntime(registry=_TemplateRegistry(template)).parse(
        identity=identity,
        command=template.canonical_command,
        raw_text=snapshot.raw_text,
    )
    return {
        "status": result.parser_status,
        "failure_code": result.failure_code,
        "records": result.structured_data,
    }


@router.get("/templates/{template_id}/comparison")
def compare_template(template_id: str, limit: int = 20):
    repository = ParserTemplateRepository()
    candidate = repository.get_template(template_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="parser template not found")
    active = repository.resolve_active(
        vendor=candidate.vendor,
        model=candidate.model or candidate.profile,
        family=candidate.family,
        command=candidate.canonical_command,
    )
    snapshots = [
        item
        for item in PatrolRepository().list_dataset_snapshots(mode="parser_validation")
        if (item.vendor or "").lower() == candidate.vendor.lower()
        and (item.model or "").lower() == (candidate.model or candidate.profile).lower()
        and item.command_canonical == candidate.canonical_command
    ][: max(1, min(limit, 100))]
    rows = []
    for snapshot in snapshots:
        proposed = _parse_snapshot_with_template(candidate, snapshot)
        current = _parse_snapshot_with_template(active, snapshot) if active else None
        rows.append(
            {
                "snapshot_id": snapshot.snapshot_id,
                "dataset_id": snapshot.dataset_id or "legacy",
                "device_id": snapshot.device_id,
                "current": current,
                "proposed": proposed,
                "changed": current != proposed,
            }
        )
    passed_samples = sum(row["proposed"]["status"] in {"success", "empty"} for row in rows)
    repository.record_regression_validation(
        candidate.id,
        passed=bool(rows) and passed_samples == len(rows),
        sample_count=len(rows),
        passed_samples=passed_samples,
    )
    candidate_contract = dict(candidate.validation_json or {})
    active_contract = dict(active.validation_json or {}) if active else {}
    return {
        "template_id": candidate.id,
        "active_template_id": active.id if active else None,
        "sample_count": len(rows),
        "passed_samples": passed_samples,
        "candidate_contract_sha256": candidate.contract_sha256,
        "active_contract_sha256": active.contract_sha256 if active else None,
        "contract_changed": candidate.contract_sha256
        != (active.contract_sha256 if active else None),
        "candidate_contract": candidate_contract,
        "active_contract": active_contract,
        "rows": rows,
    }


@router.get("/jobs")
def list_jobs(
    status: str | None = None,
    job_type: str | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    return ParserJobRepository().list_jobs_page(
        status=status,
        job_type=job_type,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    item = ParserJobRepository().get_job(job_id)
    if not item:
        raise HTTPException(status_code=404, detail="parser job not found")
    return item


@router.get("/jobs/{job_id}/attempts")
def list_job_generation_attempts(job_id: str, include_template: bool = False):
    repository = ParserJobRepository()
    if not repository.get_job(job_id):
        raise HTTPException(status_code=404, detail="parser job not found")
    return {
        "items": repository.list_generation_attempts(
            job_id,
            include_template=include_template,
        )
    }


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str):
    try:
        ParserJobRepository().retry_job(job_id)
    except ParserJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "job_id": job_id, "status": "pending"}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    try:
        item = ParserJobRepository().cancel_job(job_id)
    except ParserJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if item.get("celery_task_id"):
        celery.control.revoke(item["celery_task_id"], terminate=False)
    return {"success": True, "job_id": job_id, "status": "cancelled"}


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str, request: Request, payload: ParserJobDeleteRequest):
    try:
        result = ParserJobRepository().delete_job(
            job_id,
            actor=_authenticated_actor(request),
            reason=_required_reason(payload.reason, action="parser job deletion"),
        )
    except ParserJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "job_id": job_id, **result}


@router.post("/jobs/bulk-delete")
def bulk_delete_jobs(request: Request, payload: ParserBulkJobDeleteRequest):
    result = ParserJobRepository().delete_jobs(
        payload.job_ids,
        actor=_authenticated_actor(request),
        reason=_required_reason(payload.reason, action="parser job bulk deletion"),
    )
    return {"success": result["failed_count"] == 0, **result}


@router.post("/jobs/{job_id}/dispatch")
def dispatch_job(job_id: str):
    if not ParserJobRepository().get_job(job_id):
        raise HTTPException(status_code=404, detail="parser job not found")
    result = dispatch_ready_parser_jobs(
        job_id=job_id,
        limit=1,
        allow_production=True,
    )
    if not result["dispatched"]:
        raise HTTPException(
            status_code=409,
            detail=result["failed"][0]["error"]
            if result["failed"]
            else "parser job is not ready for dispatch",
        )
    return {"success": True, **result}


@router.post("/jobs/{job_id}/sanitize")
def sanitize_parser_job(job_id: str):
    samples = ParserSampleRepository().sanitize_pending_samples(job_id=job_id)
    return {
        "success": all(bool(item.get("success")) for item in samples),
        "processed_count": len(samples),
        "samples": samples,
    }


@router.post("/dispatch")
def dispatch_jobs():
    return {"success": True, **dispatch_ready_parser_jobs(limit=1)}


@router.get("/dispatch-control")
def get_dispatch_control():
    return {"auto_dispatch_enabled": ParserJobRepository().get_auto_dispatch_enabled()}


@router.post("/dispatch-control")
def set_dispatch_control(request: Request, payload: ParserDispatchControlRequest):
    enabled = ParserJobRepository().set_auto_dispatch_enabled(
        payload.enabled,
        actor=_authenticated_actor(request),
        reason=_required_reason(payload.reason, action="parser automatic dispatch change"),
    )
    return {"success": True, "auto_dispatch_enabled": enabled}


@router.post("/templates/{template_id}/activate")
def activate_template(
    template_id: str,
    request: Request,
    payload: ParserTemplateApprovalRequest,
):
    try:
        item = ParserTemplateRepository().activate_template(
            template_id,
            approved_by=_authenticated_actor(request),
            reason=_required_reason(payload.reason, action="template activation"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item.to_dict()


@router.post(
    "/templates/{template_id}/force-activate",
    dependencies=[Depends(require_role(["admin"]))],
)
def force_activate_template(
    template_id: str,
    request: Request,
    payload: ParserTemplateForceActivationRequest,
):
    try:
        item = ParserTemplateRepository().activate_template(
            template_id,
            approved_by=_authenticated_actor(request),
            reason=_required_reason(payload.reason, action="template force activation"),
            force=True,
            approval_kind="force_activation",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item.to_dict()


@router.post("/templates/{template_id}/rollback")
def rollback_template(
    template_id: str,
    request: Request,
    payload: ParserTemplateForceActivationRequest,
):
    try:
        item = ParserTemplateRepository().rollback_template(
            template_id,
            actor=_authenticated_actor(request),
            reason=_required_reason(payload.reason, action="template rollback"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item.to_dict()


@router.get("/backfills")
def list_parser_backfills(
    status: str | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    return ParserBackfillService().list_jobs_page(
        status=status,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/backfills/{job_id}")
def get_parser_backfill(job_id: str):
    item = ParserBackfillService().get_job(job_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Parser backfill job not found")
    return item


@router.post("/backfills")
def create_parser_backfill(request: Request, payload: ParserBackfillCreateRequest):
    try:
        return ParserBackfillService().create_job(
            template_id=payload.template_id,
            requested_by=_authenticated_actor(request),
            filters=payload.filters,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/backfills/{job_id}/dry-run")
def execute_parser_backfill_dry_run(job_id: str):
    try:
        return ParserBackfillService().execute_dry_run(job_id)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/backfills/{job_id}/commit")
def commit_parser_backfill(
    job_id: str,
    request: Request,
    payload: ParserBackfillCommitRequest,
):
    try:
        return ParserBackfillService().commit(
            job_id,
            actor=_authenticated_actor(request),
            reason=_required_reason(payload.reason, action="backfill commit"),
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/templates/{template_id}/reject")
def reject_template(template_id: str, request: Request, payload: ParserTemplateStatusRequest):
    ParserTemplateRepository().set_template_status(
        template_id,
        "rejected",
        actor=_authenticated_actor(request),
        reason=_required_reason(payload.reason, action="template rejection"),
    )
    return {"success": True, "template_id": template_id, "status": "rejected"}


@router.post("/templates/{template_id}/deprecate")
def deprecate_template(template_id: str, request: Request, payload: ParserTemplateStatusRequest):
    ParserTemplateRepository().set_template_status(
        template_id,
        "deprecated",
        actor=_authenticated_actor(request),
        reason=_required_reason(payload.reason, action="template deprecation"),
    )
    return {"success": True, "template_id": template_id, "status": "deprecated"}


@router.post("/bootstrap")
def bootstrap_templates():
    return {
        "templates": bootstrap_shared_templates(),
        "discovery_assets": bootstrap_discovery_assets(),
    }


@router.post("/import-preview")
def preview_validation_import(payload: ParserImportRequest):
    try:
        return preview_raw_path(
            file_path=payload.source_path,
            metadata={"dataset_id": payload.dataset_id} if payload.dataset_id else None,
            vendor=payload.vendor,
            model=payload.model,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/import-execute")
def execute_validation_import(payload: ParserImportRequest):
    try:
        retired = ParserJobRepository().supersede_legacy_jobs()
        result = import_raw_path(
            file_path=payload.source_path,
            mode="parser_validation",
            metadata={"dataset_id": payload.dataset_id} if payload.dataset_id else None,
            vendor=payload.vendor,
            model=payload.model,
        )
        result["superseded_legacy_jobs"] = retired
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/datasets")
def dataset_coverage(dataset_ids: str | None = None):
    patrol_repository = PatrolRepository()
    requested_dataset_ids = sorted(
        {item.strip() for item in str(dataset_ids or "").split(",") if item.strip()}
    )
    if len(requested_dataset_ids) > 50:
        raise HTTPException(
            status_code=400,
            detail="at most 50 dataset_ids may be inspected at once",
        )
    datasets = patrol_repository.list_datasets(
        dataset_ids=requested_dataset_ids or None,
        mode=None if requested_dataset_ids else "parser_validation",
    )
    offline_scopes = patrol_repository.get_offline_dataset_run_scopes(requested_dataset_ids)
    output = []
    for dataset in datasets:
        scope = offline_scopes.get(dataset["id"], {})
        patrol_run_id = scope.get("patrol_run_id")
        snapshots = (
            patrol_repository.list_run_snapshots(patrol_run_id)
            if patrol_run_id
            else patrol_repository.list_dataset_snapshots(
                dataset["id"],
                mode=None if requested_dataset_ids else "parser_validation",
            )
        )
        counts = {
            "parsed": 0,
            "empty": 0,
            "ignored": 0,
            "missing": 0,
            "failed": 0,
            "identity_unknown": 0,
        }
        for item in snapshots:
            if item.parse_status == "parsed":
                counts["parsed"] += 1
            elif item.parse_status == "parse_empty":
                counts["empty"] += 1
            elif item.parse_status in {"ignored", "not_applicable", "raw_hashed"}:
                counts["ignored"] += 1
            elif item.parse_status == "template_missing":
                counts["missing"] += 1
            elif item.parse_status == "identity_unknown":
                counts["identity_unknown"] += 1
            elif item.parse_status == "parse_failed":
                counts["failed"] += 1
        eligible = len(snapshots) - counts["ignored"]
        successful = counts["parsed"] + counts["empty"]
        parsed_snapshot_ids = [
            item.snapshot_id for item in snapshots if item.parse_status == "parsed"
        ]
        key_stable_snapshot_ids = (
            patrol_repository.list_snapshots_with_entity_keys(parsed_snapshot_ids)
            if patrol_run_id
            else set()
        )
        primary_key_stability_rate = (
            len(key_stable_snapshot_ids) / len(parsed_snapshot_ids)
            if patrol_run_id and parsed_snapshot_ids
            else None
        )
        output.append(
            {
                **dataset,
                "dataset_id": dataset["id"],
                "snapshot_count": len(snapshots),
                "device_count": len({item.device_id for item in snapshots}),
                "command_count": len({item.command_canonical for item in snapshots}),
                "scope_type": "offline_patrol_run" if patrol_run_id else "dataset",
                **scope,
                **counts,
                "eligible_count": eligible,
                "structured_coverage_rate": (successful / eligible if eligible else 1.0),
                "parse_success_rate": (counts["parsed"] / eligible if eligible else 1.0),
                "legal_empty_rate": counts["empty"] / eligible if eligible else 0.0,
                "missing_template_rate": (counts["missing"] / eligible if eligible else 0.0),
                "parse_failure_rate": (counts["failed"] / eligible if eligible else 0.0),
                "primary_key_stability_rate": primary_key_stability_rate,
                "primary_key_evaluated_count": (len(parsed_snapshot_ids) if patrol_run_id else 0),
            }
        )
    found_ids = {item["dataset_id"] for item in output}
    return {
        "items": output,
        "requested_dataset_ids": requested_dataset_ids,
        "missing_dataset_ids": [item for item in requested_dataset_ids if item not in found_ids],
    }


@router.get("/aliases")
def list_aliases(
    status: str | None = "confirmed",
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    return ParserMetadataRepository().list_command_aliases_page(
        status=status,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/work-items")
def list_parser_work_items(
    task_type: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    try:
        return ParserWorkQueueService().list_items(
            task_type=task_type,
            status=status,
            search=search,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/device-profiles")
def list_device_profiles(
    status: str | None = "confirmed",
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    return ParserMetadataRepository().list_device_profiles_page(
        status=status,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/identity-signatures")
def list_identity_signatures(
    status: str | None = "confirmed",
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    return ParserMetadataRepository().list_identity_signatures_page(
        status=status,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/coverage")
def parser_coverage(by_effective_plan: bool = False):
    templates_repository = ParserTemplateRepository()
    jobs_repository = ParserJobRepository()
    metadata_repository = ParserMetadataRepository()
    templates_repository.backfill_contract_metadata()
    template_status_counts = templates_repository.template_status_counts()
    job_status_counts = jobs_repository.job_status_counts()
    open_job_type_counts = jobs_repository.open_job_type_counts()
    open_job_count = sum(job_status_counts.get(status, 0) for status in OPEN_JOB_STATUSES)
    backlog = jobs_repository.backlog_summary()
    backfill_status_counts = ParserBackfillService().status_counts()
    rule_generation_status_counts = RuleGenerationJobRepository().status_counts()
    governance = ParserGovernanceProjectionRepository().governance_alert_summary()
    dialect_audit = TextFSMDialectAuditService().audit()
    payload = {
        "templates_total": sum(template_status_counts.values()),
        "templates_active": template_status_counts.get("active", 0),
        "template_status_counts": template_status_counts,
        "jobs_total": sum(job_status_counts.values()),
        "jobs_pending": open_job_count,
        "job_status_counts": job_status_counts,
        "missing_candidates": open_job_type_counts.get("CREATE_TEMPLATE", 0),
        "repair_candidates": open_job_type_counts.get("REPAIR_TEMPLATE", 0),
        "rule_generation_status_counts": rule_generation_status_counts,
        "rule_generation_total": sum(rule_generation_status_counts.values()),
        "command_aliases": len(metadata_repository.list_command_aliases()),
        "device_profiles": len(metadata_repository.list_device_profiles()),
        "identity_signatures": len(metadata_repository.list_identity_signatures()),
        "auto_dispatch_enabled": jobs_repository.get_auto_dispatch_enabled(),
        "dialect_version": dialect_audit["dialect_version"],
        "dialect_active_templates": dialect_audit["template_count"],
        "dialect_platform_violations": dialect_audit["platform_violation_count"],
        "dialect_runtime_blocked": dialect_audit["runtime_blocked_count"],
        "dialect_regression_evidence_missing": dialect_audit["regression_evidence_missing_count"],
        **backlog,
        "backfills_pending_approval": backfill_status_counts.get("pending_approval", 0),
        "backfills_failed": sum(
            backfill_status_counts.get(status, 0) for status in {"validation_failed", "failed"}
        ),
        **governance,
    }
    if by_effective_plan:
        from src.core.parser.production_impact import effective_plan_coverage_summary

        payload["effective_plan_coverage"] = effective_plan_coverage_summary()
    return payload


@router.get("/dialect-audit")
def textfsm_dialect_audit(status: str = "active"):
    return TextFSMDialectAuditService().audit(status=status)


@router.get("/rule-gaps/from-changes")
def rule_gaps_from_changes(days: int = 90, limit: int = 200):
    from src.core.rule_control.change_gap_analysis import ChangeGapAnalysisService

    return ChangeGapAnalysisService().analyze(days=days, limit=limit)


@router.post("/rule-gaps/seed-jobs")
def seed_rule_gaps_from_changes(
    http_request: Request,
    days: int = 90,
    limit: int = 20,
):
    from src.core.rule_control.change_gap_analysis import ChangeGapAnalysisService

    return ChangeGapAnalysisService().seed_jobs(
        days=days,
        limit=limit,
        created_by=_authenticated_actor(http_request),
    )


@router.get("/rule-generation-jobs")
def list_rule_generation_jobs(
    job_type: str | None = None,
    status: str | None = None,
    action: str | None = None,
    asset_type: str | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    return RuleGenerationJobRepository().list_jobs_page(
        job_type=job_type,
        status=status,
        action=action,
        asset_type=asset_type,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/rule-asset-bundles")
def list_rule_asset_bundles(
    status: str | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    return RuleAssetBundleService().list_bundles(
        status=status,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/rule-asset-bundles/{bundle_id}")
def get_rule_asset_bundle(bundle_id: str):
    try:
        return RuleAssetBundleService().get_bundle(bundle_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/rule-asset-bundles/{bundle_id}/engineering-draft")
def get_rule_asset_bundle_engineering_draft(bundle_id: str):
    try:
        return RuleAssetEngineeringRevisionService().get_draft(bundle_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/rule-asset-bundles/{bundle_id}/engineering-review")
def review_rule_asset_bundle(
    bundle_id: str,
    request: Request,
    payload: RuleAssetEngineeringReviewRequest,
):
    try:
        return RuleAssetEngineeringRevisionService().review(
            bundle_id,
            decision=payload.decision,
            actor=_authenticated_actor(request),
            reason=payload.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/rule-asset-bundles/{bundle_id}/engineering-revision")
def revise_rule_asset_bundle(
    bundle_id: str,
    request: Request,
    payload: RuleAssetEngineeringRevisionRequest,
    async_mode: bool = False,
):
    try:
        if async_mode:
            from src.core.rule_control.engineering_revision_async import (
                enqueue_engineering_revision,
            )

            return enqueue_engineering_revision(
                bundle_id,
                contract=payload.contract,
                change_policies=list(payload.change_policies or []),
                event_rules=list(payload.event_rules or []),
                actor=_authenticated_actor(request),
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
            )
        return RuleAssetEngineeringRevisionService().revise(
            bundle_id,
            contract=payload.contract,
            change_policies=payload.change_policies,
            event_rules=payload.event_rules,
            actor=_authenticated_actor(request),
            reason=payload.reason,
            idempotency_key=payload.idempotency_key,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuleAssetEngineeringRevisionError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": str(exc), "validation": exc.validation},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/engineering-revision-tasks/{task_id}")
def get_engineering_revision_task(task_id: str):
    from celery.result import AsyncResult

    from src.core.celery_tasks.celery_app import celery

    result = AsyncResult(task_id, app=celery)
    payload = {
        "task_id": task_id,
        "status": result.status,
        "ready": result.ready(),
    }
    if result.successful():
        payload["result"] = result.result
    elif result.failed():
        payload["error"] = str(result.result)
    return payload


@router.get("/replay-reports")
def list_replay_reports(
    success: bool | None = None,
    asset_type: str | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    return RuleGenerationJobRepository().list_replay_reports_page(
        success=success,
        asset_type=asset_type,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/approval-audit")
def list_rule_asset_approval_audit(
    action: str | None = None,
    asset_type: str | None = None,
    actor: str | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    try:
        return RuleGenerationJobRepository().list_approval_audit_page(
            action=action,
            asset_type=asset_type,
            actor=actor,
            search=search,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/agent-loop-attempts")
def list_rule_asset_agent_loop_attempts(
    asset_type: str | None = None,
    stage: str | None = None,
    success: bool | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    return RuleGenerationJobRepository().list_agent_loop_attempts_page(
        asset_type=asset_type,
        stage=stage,
        success=success,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/observation-contracts")
def list_observation_contracts(
    source_type: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    return ObservationContractRepository().list_contracts_page(
        source_type=source_type,
        status=status,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/change-policies")
def list_change_policies(
    status: str | None = None,
    observation_type: str | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    return ChangePolicyRepository().list_versions_page(
        status=status,
        observation_type=observation_type,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.post("/runtime-assets/resolve")
def resolve_runtime_rule_assets(payload: RuntimeRuleAssetResolveRequest):
    return (
        RuntimeRuleAssetResolver()
        .load_bundle(
            observation_schemas=payload.observation_schemas,
            observation_types=payload.observation_types,
            include_event_rules=payload.include_event_rules,
        )
        .to_dict()
    )


@router.get("/runtime-assets/health")
def get_runtime_rule_asset_health():
    return RuntimeRuleAssetResolver().load_inventory_bundle().to_dict()


@router.post("/runtime-assets/seed-builtin")
def seed_builtin_runtime_rule_assets(
    request: Request,
    payload: RuntimeRuleAssetSeedRequest | None = None,
):
    if payload is None:
        raise HTTPException(status_code=422, detail="runtime asset seed requires a reason")
    return RuntimeRuleAssetSeedService().seed_builtin_assets(
        actor=_authenticated_actor(request),
        reason=_required_reason(payload.reason, action="builtin runtime asset seed"),
    )


@router.post("/rule-generation-jobs")
def create_rule_generation_job(request: Request, payload: RuleGenerationJobCreateRequest):
    try:
        if payload.status != "draft":
            raise ValueError("rule generation jobs must be created as draft")
        return RuleGenerationJobRepository().create_job(
            job_type=payload.job_type,
            source_ref=payload.source_ref,
            asset_ref=payload.asset_ref,
            asset_type=payload.asset_type,
            asset_id=payload.asset_id,
            asset_version_id=payload.asset_version_id,
            asset_digest=payload.asset_digest,
            payload=payload.payload,
            validation_report=payload.validation_report,
            approval_required=payload.approval_required,
            created_by=_authenticated_actor(request),
            status=payload.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/rule-generation-jobs/{job_id}")
def get_rule_generation_job(job_id: str):
    item = RuleGenerationJobRepository().get_job(job_id)
    if not item:
        raise HTTPException(status_code=404, detail="rule generation job not found")
    return item


@router.get("/rule-generation-jobs/{job_id}/preview")
def preview_rule_generation_job(job_id: str):
    try:
        return RuleGenerationJobRepository().preview_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/rule-generation-jobs/{job_id}/transition")
def transition_rule_generation_job(
    job_id: str,
    payload: RuleGenerationJobTransitionRequest,
):
    raise HTTPException(
        status_code=409,
        detail=(
            "direct rule generation job transitions are disabled; use generation, "
            "validation, submission, and Rule Asset Bundle approval workflows"
        ),
    )


@router.post("/rule-generation-jobs/{job_id}/validation-report")
def attach_rule_generation_validation_report(
    job_id: str,
    payload: RuleGenerationJobValidationRequest,
):
    raise HTTPException(
        status_code=409,
        detail=(
            "direct validation report attachment is disabled; validation evidence "
            "must be produced by the governed validation workflow"
        ),
    )


@router.post(
    "/rule-generation-jobs/{job_id}/approve",
    dependencies=[Depends(require_role(["admin"]))],
)
def approve_rule_generation_job(
    job_id: str,
    request: Request,
    payload: RuleGenerationJobApprovalRequest | None = None,
):
    reason = _required_reason(payload.reason if payload else None, action="rule asset approval")
    try:
        return RuleAssetApprovalService().approve(
            job_id,
            actor=_authenticated_actor(request),
            reason=reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuleGenerationJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/rule-generation-jobs/{job_id}/revise")
def revise_rule_generation_job(
    job_id: str,
    request: Request,
    payload: RuleGenerationJobApprovalRequest | None = None,
):
    try:
        return AgentLoopRevisionService().create_revision_job(
            job_id,
            actor=_authenticated_actor(request),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/rule-generation-jobs/{job_id}/execute-revision")
def execute_rule_generation_revision_job(
    job_id: str,
    request: Request,
    payload: RuleGenerationJobApprovalRequest | None = None,
):
    try:
        return AgentLoopRevisionExecutor().execute(
            job_id,
            actor=_authenticated_actor(request),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuleGenerationJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/rule-generation-jobs/{job_id}/submit")
def submit_rule_generation_job_candidate(
    job_id: str,
    request: Request,
    payload: RuleGenerationJobApprovalRequest | None = None,
):
    try:
        return RuleAssetSubmissionService().submit_candidate(
            job_id,
            actor=_authenticated_actor(request),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuleGenerationJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/rule-generation-jobs/{job_id}/auto-context")
def auto_build_rule_generation_context(job_id: str, http_request: Request):
    try:
        return RuleAssetTemplateActivationService().auto_build_context(
            job_id,
            actor=_authenticated_actor(http_request),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/rule-generation-jobs/{job_id}/generation-context")
def preview_rule_asset_generation_context(
    job_id: str,
    http_request: Request,
    payload: RuleAssetGenerationContextRequest | None = None,
):
    try:
        request = payload or RuleAssetGenerationContextRequest()
        if not request.context_id and not request.dataset_ids:
            raise ValueError(
                "select an offline patrol dataset before previewing rule generation context"
            )
        overrides = dict(request.context_overrides or {})
        prepared_dataset = None
        if request.context_id or request.dataset_ids:
            from src.core.rule_control.dataset_context import DatasetRuleGenerationContextService

            context_service = DatasetRuleGenerationContextService()
            prepared_dataset = (
                context_service.load(context_id=request.context_id, job_id=job_id)
                if request.context_id
                else context_service.prepare(
                    job_id=job_id,
                    dataset_ids=list(request.dataset_ids or []),
                    created_by=_authenticated_actor(http_request),
                    max_records=request.max_records,
                    field_name=request.field_name,
                    observation_type=request.observation_type,
                )
            )
            protected = {
                "records",
                "record_groups",
                "historical_values",
                "historical_values_by_field",
                "field_name",
                "observation_type",
                "replay_run_ids",
            }.intersection(overrides)
            if protected:
                raise ValueError(
                    "immutable dataset context cannot override: " + ", ".join(sorted(protected))
                )
            overrides = {**prepared_dataset["context"], **overrides}
        result = RuleAssetTemplateActivationService().build_generation_context(
            job_id,
            context_overrides=overrides,
        )
        if prepared_dataset:
            result.update(
                context_id=prepared_dataset["context_id"],
                context_digest=prepared_dataset["context_digest"],
                dataset_ids=prepared_dataset["dataset_ids"],
                privacy=prepared_dataset["privacy"],
            )
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/rule-generation-jobs/{job_id}/start-generation")
def start_rule_asset_generation_workflow(
    job_id: str,
    http_request: Request,
    payload: RuleAssetGenerationContextRequest | None = None,
):
    try:
        request = payload or RuleAssetGenerationContextRequest()
        if not request.context_id and not request.dataset_ids:
            raise ValueError(
                "select an offline patrol dataset before starting rule asset generation"
            )
        overrides = dict(request.context_overrides or {})
        context_id = request.context_id
        if context_id or request.dataset_ids:
            from src.core.rule_control.dataset_context import DatasetRuleGenerationContextService

            context_service = DatasetRuleGenerationContextService()
            prepared_dataset = (
                context_service.load(context_id=context_id, job_id=job_id)
                if context_id
                else context_service.prepare(
                    job_id=job_id,
                    dataset_ids=list(request.dataset_ids or []),
                    created_by=_authenticated_actor(http_request),
                    max_records=request.max_records,
                    field_name=request.field_name,
                    observation_type=request.observation_type,
                )
            )
            protected = {
                "records",
                "record_groups",
                "historical_values",
                "historical_values_by_field",
                "field_name",
                "observation_type",
                "replay_run_ids",
            }.intersection(overrides)
            if protected:
                raise ValueError(
                    "immutable dataset context cannot override: " + ", ".join(sorted(protected))
                )
            overrides = {**prepared_dataset["context"], **overrides}
            context_id = prepared_dataset["context_id"]
        generation_kwargs = {
            "context_overrides": overrides,
            "actor": _authenticated_actor(http_request),
        }
        if context_id:
            generation_kwargs["context_id"] = context_id
        return RuleAssetTemplateActivationService().start_generation_workflow(
            job_id,
            **generation_kwargs,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/rule-generation-jobs/{job_id}/reject")
def reject_rule_generation_job(
    job_id: str,
    request: Request,
    payload: RuleGenerationJobApprovalRequest | None = None,
):
    reason = _required_reason(payload.reason if payload else None, action="rule asset rejection")
    try:
        return RuleAssetApprovalService().reject(
            job_id,
            actor=_authenticated_actor(request),
            reason=reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuleGenerationJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/rule-generation-jobs/{job_id}/deprecate")
def deprecate_rule_generation_job(
    job_id: str,
    request: Request,
    payload: RuleGenerationJobApprovalRequest | None = None,
):
    reason = _required_reason(payload.reason if payload else None, action="rule asset deprecation")
    try:
        return RuleAssetApprovalService().deprecate(
            job_id,
            actor=_authenticated_actor(request),
            reason=reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuleGenerationJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/event-rules")
def list_event_rules(
    status: str | None = None,
    event_type: str | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    return EventRuleRepository().list_versions_page(
        status=status,
        event_type=event_type,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.post("/event-rules")
def upsert_event_rule(request: Request, payload: EventRuleUpsertRequest):
    try:
        if payload.status != "draft":
            raise ValueError("manually created event rule versions must start as draft")
        version_id = EventRuleRepository().create_rule_version(
            name=payload.name,
            event_type=payload.event_type,
            severity=payload.severity,
            condition=payload.condition,
            description=payload.description,
            created_by=_authenticated_actor(request),
            status=payload.status,
        )
        return {"success": True, "version_id": version_id}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/event-rules/{version_id}/activate")
def activate_event_rule(version_id: str):
    raise HTTPException(
        status_code=409,
        detail=(
            "direct EventRule activation is disabled; publish a validated Rule Asset "
            "Bundle or use the audited force-activate endpoint for an emergency"
        ),
    )


@router.post("/event-rules/{version_id}/force-activate")
def force_activate_event_rule(
    version_id: str,
    request: Request,
    payload: EventRuleForceActivationRequest,
):
    try:
        return EventRuleRepository().force_activate_rule_version(
            version_id,
            actor=_authenticated_actor(request),
            reason=_required_reason(payload.reason, action="event rule force activation"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/event-rules/{version_id}/deprecate")
def deprecate_event_rule(
    version_id: str,
    request: Request,
    payload: EventRuleForceActivationRequest,
):
    try:
        return EventRuleRepository().deprecate_rule_version(
            version_id,
            actor=_authenticated_actor(request),
            reason=_required_reason(payload.reason, action="event rule deprecation"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/event-rules/test")
def test_event_rule(payload: EventRuleTestRequest):
    rows: list[dict[str, Any]] = []
    matched_count = 0
    for index, sample in enumerate(payload.samples, start=1):
        try:
            matched = condition_matches(payload.condition, sample)
            error = None
        except Exception as exc:
            matched = False
            error = str(exc)
        if matched:
            matched_count += 1
        rows.append(
            {
                "index": index,
                "matched": matched,
                "error": error,
                "sample": sample,
            }
        )
    return {
        "success": True,
        "sample_count": len(payload.samples),
        "matched_count": matched_count,
        "unmatched_count": len(payload.samples) - matched_count,
        "rows": rows,
    }


@router.post("/event-rules/validate")
def validate_event_rules(payload: EventRuleValidationRequest | None = None):
    case_payloads = payload.cases if payload and payload.cases is not None else None
    if case_payloads is None:
        return validate_event_rule_pack()
    cases = [
        EventRuleValidationCase(
            name=item.name,
            change=item.change,
            expected_event_type=item.expected_event_type,
            expected_rule_id=item.expected_rule_id,
        )
        for item in case_payloads
    ]
    return validate_event_rule_set(cases)


def _candidate_rule_definitions(
    candidates: list[EventRuleCandidateRequest],
) -> list[EventRuleDefinition]:
    return [
        EventRuleDefinition(
            rule_id=str(item.rule_id or item.name or f"candidate-rule-{index}"),
            version=int(item.version or 1),
            event_type=item.event_type,
            severity=item.severity,
            condition=dict(item.condition),
            description=item.description,
        )
        for index, item in enumerate(candidates, start=1)
    ]


@router.post("/event-rules/replay")
def replay_event_rules(payload: EventRuleReplayRequest):
    if not payload.candidate_rules:
        raise HTTPException(status_code=400, detail="candidate_rules is required")
    return replay_event_rule_definitions(
        _candidate_rule_definitions(payload.candidate_rules),
        limit=payload.limit,
        include_active_collisions=payload.include_active_collisions,
    )


@router.post("/event-rules/bootstrap")
def bootstrap_event_rules(
    request: Request,
    payload: CommandProfileLifecycleRequest,
):
    actor = _authenticated_actor(request)
    reason = _required_reason(payload.reason, action="builtin event rule bootstrap")
    return {
        "success": True,
        "created": EventRuleRepository().ensure_default_rules(actor=actor, reason=reason),
    }


@router.get("/command-profiles")
def list_command_profiles(
    status: str | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    return CommandProfileRepository().list_profiles_page(
        status=status,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.post("/command-profiles")
def upsert_command_profile(request: Request, payload: CommandProfileUpsertRequest):
    try:
        if payload.status == "active":
            raise ValueError("command profile versions must be created as draft")
        version_id = CommandProfileRepository().create_profile_version(
            name=payload.name,
            action=payload.action,
            commands=payload.commands,
            created_by=_authenticated_actor(request),
            status="draft",
        )
        return {"success": True, "profile_version_id": version_id, "status": payload.status}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/command-profiles/{profile_version_id}/activate")
def activate_command_profile(
    profile_version_id: str,
    request: Request,
    payload: CommandProfileLifecycleRequest,
):
    try:
        repository = CommandProfileRepository()
        repository.validate_profile_activation(profile_version_id)
        return repository.activate_profile_version(
            profile_version_id,
            actor=_authenticated_actor(request),
            reason=_required_reason(payload.reason, action="command profile activation"),
        )
    except Exception as exc:
        message = str(exc)
        if message == "command profile impact evidence is required before activation":
            message = "激活前需要先完成影响预览并保存证据"
        raise HTTPException(status_code=400, detail=message) from exc


@router.delete("/command-profiles/{profile_version_id}")
def delete_command_profile(
    profile_version_id: str,
    request: Request,
    payload: CommandProfileLifecycleRequest,
):
    try:
        return CommandProfileRepository().delete_profile_version(
            profile_version_id,
            actor=_authenticated_actor(request),
            reason=_required_reason(payload.reason, action="command profile deletion"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/command-profiles/{profile_version_id}/deprecate")
def deprecate_command_profile(
    profile_version_id: str,
    request: Request,
    payload: CommandProfileLifecycleRequest,
):
    try:
        return CommandProfileRepository().deprecate_profile_version(
            profile_version_id,
            actor=_authenticated_actor(request),
            reason=_required_reason(payload.reason, action="command profile deprecation"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/command-profiles/{profile_version_id}/rollback")
def rollback_command_profile(
    profile_version_id: str,
    request: Request,
    payload: CommandProfileLifecycleRequest,
):
    try:
        return CommandProfileRepository().rollback_profile_version(
            profile_version_id,
            actor=_authenticated_actor(request),
            reason=_required_reason(payload.reason, action="command profile rollback"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/command-profiles/activate")
def activate_command_profile_legacy(payload: CommandProfileUpsertRequest):
    raise HTTPException(
        status_code=409,
        detail=(
            "legacy command profile activation is disabled; create a draft, save impact "
            "evidence, then activate the version with an audited reason"
        ),
    )


@router.get("/command-profile-bindings")
def list_command_profile_bindings(
    status: str | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    return CommandProfileRepository().list_bindings_page(
        status=status,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.post("/command-profile-bindings")
def upsert_command_profile_binding(
    request: Request,
    payload: CommandProfileBindingUpsertRequest,
):
    try:
        _authenticated_actor(request)
        if payload.status != "disabled":
            raise ValueError(
                "command profile bindings must be created disabled and enabled "
                "through the audited lifecycle endpoint"
            )
        binding_id = CommandProfileRepository().upsert_binding(
            profile_version_id=payload.profile_version_id,
            selector=payload.selector,
            priority=payload.priority,
            status="disabled",
        )
        return {"success": True, "binding_id": binding_id}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/command-profile-bindings/{binding_id}/enable")
def enable_command_profile_binding(
    binding_id: str,
    request: Request,
    payload: CommandProfileLifecycleRequest,
):
    try:
        return CommandProfileRepository().set_binding_status(
            binding_id,
            "active",
            actor=_authenticated_actor(request),
            reason=_required_reason(payload.reason, action="command profile binding enable"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/command-profile-bindings/{binding_id}/disable")
def disable_command_profile_binding(
    binding_id: str,
    request: Request,
    payload: CommandProfileLifecycleRequest,
):
    try:
        return CommandProfileRepository().set_binding_status(
            binding_id,
            "disabled",
            actor=_authenticated_actor(request),
            reason=_required_reason(payload.reason, action="command profile binding disable"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _profile_impact_commands(
    payload: CommandProfileImpactPreviewRequest,
) -> tuple[str, str | None, list[str]]:
    if payload.commands:
        commands = [str(item).strip() for item in payload.commands if str(item).strip()]
        if not commands:
            raise ValueError("commands cannot be empty")
        return payload.profile_name or "proposed-profile", payload.profile_version_id, commands
    if not payload.profile_version_id:
        raise ValueError("profile_version_id or commands is required")
    item = CommandProfileRepository().get_profile_version(payload.profile_version_id)
    if not item:
        raise ValueError(f"command profile version not found: {payload.profile_version_id}")
    commands = [
        str(command_item["command"])
        for command_item in item.get("items") or []
        if str(command_item.get("action") or payload.action) == payload.action
        and command_item.get("command")
    ]
    if not commands:
        raise ValueError("profile version has no commands for requested action")
    return str(item["profile_name"]), payload.profile_version_id, commands


@router.post("/command-profiles/impact-preview")
def preview_command_profile_impact(payload: CommandProfileImpactPreviewRequest):
    try:
        repository = CommandProfileRepository()
        mode = normalize_patrol_mode(payload.mode)
        profile_name, profile_version_id, commands = _profile_impact_commands(payload)
        selector = dict(payload.selector or {})
        devices = [
            device
            for device in DeviceRepository().get_devices_by_filter(status="active")
            if CollectionPolicyResolver._selector_matches(device, selector)
        ]
        limit = max(1, min(int(payload.limit or 100), 500))
        rows: list[dict[str, Any]] = []
        blocked_devices = 0
        eligible_commands = 0
        ready_commands = 0
        blocked_commands = 0
        for device in devices[:limit]:
            plan = ExecutionPlan(
                device_id=str(device.get("device_id") or ""),
                action=payload.action,
                mode=mode,
                profile_name=profile_name,
                profile_version_id=profile_version_id,
                commands=tuple(
                    CollectionItem(command=command, action=payload.action) for command in commands
                ),
                policy_source="impact_preview",
                selector=selector,
            )
            coverage = (
                PatrolTool._production_coverage(device, plan.command_texts())
                if mode == PRODUCTION_MODE
                else []
            )
            violations = [
                item
                for item in coverage
                if item.get("policy_status") == "eligible"
                and item.get("coverage_status") != "ready"
            ]
            device_eligible = sum(1 for item in coverage if item.get("policy_status") == "eligible")
            device_ready = sum(
                1
                for item in coverage
                if item.get("policy_status") == "eligible"
                and item.get("coverage_status") == "ready"
            )
            eligible_commands += device_eligible
            ready_commands += device_ready
            blocked_commands += len(violations)
            if violations:
                blocked_devices += 1
            rows.append(
                {
                    "device_id": device.get("device_id"),
                    "device_name": device.get("device_name"),
                    "ip": device.get("ip") or device.get("management_ip"),
                    "vendor": device.get("vendor"),
                    "model": device.get("model"),
                    "family": device.get("family"),
                    "platform": device.get("platform"),
                    "device_type": device.get("device_type"),
                    "site": device.get("site"),
                    "role": device.get("role"),
                    "groups": device.get("groups") or [],
                    "command_count": len(commands),
                    "eligible_count": device_eligible,
                    "ready_count": device_ready,
                    "blocked_count": len(violations),
                    "status": "blocked" if violations else "ready",
                    "violations": violations,
                    "plan": plan.to_metadata(),
                }
            )
        result = {
            "success": True,
            "mode": mode,
            "profile_name": profile_name,
            "profile_version_id": profile_version_id,
            "selector": selector,
            "command_count": len(commands),
            "matched_device_count": len(devices),
            "returned_device_count": len(rows),
            "blocked_device_count": blocked_devices,
            "ready_device_count": len(rows) - blocked_devices,
            "eligible_command_count": eligible_commands,
            "ready_command_count": ready_commands,
            "blocked_command_count": blocked_commands,
            "coverage_rate": ready_commands / eligible_commands if eligible_commands else 1.0,
            "rows": rows,
        }
        if payload.save_evidence and profile_version_id:
            repository.record_profile_coverage(profile_version_id, result)
            result["evidence_saved"] = True
        else:
            result["evidence_saved"] = False
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/effective-plan-preview")
def effective_plan_preview(payload: EffectivePlanPreviewRequest):
    plan = CollectionPolicyResolver().build_plan(
        device=payload.device,
        action=payload.action,
        mode=payload.mode,
    )
    return {"success": True, "plan": plan.to_metadata(), "commands": plan.command_texts()}

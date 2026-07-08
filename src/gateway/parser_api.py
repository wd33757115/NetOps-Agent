"""Parser control-plane management API."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.auth.dependencies import require_role
from src.core.collection.policy import (
    CollectionItem,
    CollectionPolicyResolver,
    CommandProfileRepository,
    ExecutionPlan,
)
from src.core.device_ops.repository import DeviceRepository
from src.core.device_ops.tools import PatrolTool
from src.core.patrol.modes import PRODUCTION_MODE, normalize_patrol_mode
from src.core.parser.assets import TemplateAssetStore
from src.core.parser.bootstrap import bootstrap_shared_templates
from src.core.parser.discovery_assets import bootstrap_discovery_assets
from src.core.parser.repository import (
    OPEN_JOB_STATUSES,
    ParserJobStateError,
    ParserRepository,
)
from src.core.parser.identity import DeviceIdentity
from src.core.parser.runtime import ParserRuntime
from src.core.patrol.raw_importer import import_raw_path, preview_raw_path
from src.core.patrol.repository import PatrolRepository
from src.core.parser.tasks import dispatch_ready_parser_jobs
from src.core.observation.repository import ObservationContractRepository
from src.core.patrol.change_policy_repository import ChangePolicyRepository
from src.core.patrol.event_rules import (
    EventRuleDefinition,
    EventRuleRepository,
    condition_matches,
)
from src.core.patrol.rule_validation import (
    EventRuleValidationCase,
    validate_event_rule_pack,
    validate_event_rule_set,
)
from src.core.patrol.rule_replay import replay_event_rule_definitions
from src.core.rule_control import (
    AgentLoopRevisionExecutor,
    AgentLoopRevisionService,
    RuleAssetApprovalService,
    RuleAssetSubmissionService,
    RuleGenerationJobRepository,
    RuleGenerationJobStateError,
    RuntimeRuleAssetResolver,
)
from src.core.celery_tasks.celery_app import celery

router = APIRouter(
    prefix="/api/v1/parser",
    tags=["Parser Control Plane"],
    dependencies=[Depends(require_role(["admin"]))],
)


class ParserDispatchControlRequest(BaseModel):
    enabled: bool


class ParserBulkJobDeleteRequest(BaseModel):
    job_ids: list[str]


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
    created_by: str | None = None
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
    created_by: str | None = None
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
    actor: str | None = None
    reason: str | None = None


class RuntimeRuleAssetResolveRequest(BaseModel):
    observation_schemas: list[tuple[str, str]] = []
    observation_types: list[str] = []
    include_event_rules: bool = True


class CommandProfileUpsertRequest(BaseModel):
    name: str
    action: str = "patrol"
    commands: list[str]
    status: str = "draft"
    created_by: str | None = None


class CommandProfileBindingUpsertRequest(BaseModel):
    profile_version_id: str
    selector: dict[str, Any]
    priority: int = 100
    status: str = "active"


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
def list_templates(status: str | None = None):
    return {"items": ParserRepository().list_templates(status=status)}


@router.get("/templates/export")
def export_templates(status: str | None = "active"):
    repository = ParserRepository()
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
            export_path = (
                f"templates/{item.get('vendor')}/{item.get('profile')}/"
                f"{target.name}"
            )
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
    template = ParserRepository().get_template(template_id)
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
    repository = ParserRepository()
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
        for item in PatrolRepository().list_dataset_snapshots(
            mode="parser_validation"
        )
        if (item.vendor or "").lower() == candidate.vendor.lower()
        and (item.model or "").lower()
        == (candidate.model or candidate.profile).lower()
        and item.command_canonical == candidate.canonical_command
    ][: max(1, min(limit, 100))]
    rows = []
    for snapshot in snapshots:
        proposed = _parse_snapshot_with_template(candidate, snapshot)
        current = (
            _parse_snapshot_with_template(active, snapshot) if active else None
        )
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
    passed_samples = sum(
        row["proposed"]["status"] in {"success", "empty"} for row in rows
    )
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
def list_jobs(status: str | None = None):
    return {"items": ParserRepository().list_jobs(status=status)}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    item = ParserRepository().get_job(job_id)
    if not item:
        raise HTTPException(status_code=404, detail="parser job not found")
    return item


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str):
    try:
        ParserRepository().retry_job(job_id)
    except ParserJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "job_id": job_id, "status": "pending"}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    try:
        item = ParserRepository().cancel_job(job_id)
    except ParserJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if item.get("celery_task_id"):
        celery.control.revoke(item["celery_task_id"], terminate=False)
    return {"success": True, "job_id": job_id, "status": "cancelled"}


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str):
    try:
        result = ParserRepository().delete_job(job_id)
    except ParserJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "job_id": job_id, **result}


@router.post("/jobs/bulk-delete")
def bulk_delete_jobs(payload: ParserBulkJobDeleteRequest):
    result = ParserRepository().delete_jobs(payload.job_ids)
    return {"success": result["failed_count"] == 0, **result}


@router.post("/jobs/{job_id}/dispatch")
def dispatch_job(job_id: str):
    if not ParserRepository().get_job(job_id):
        raise HTTPException(status_code=404, detail="parser job not found")
    result = dispatch_ready_parser_jobs(job_id=job_id, limit=1)
    if not result["dispatched"]:
        raise HTTPException(
            status_code=409,
            detail=result["failed"][0]["error"]
            if result["failed"]
            else "parser job is not ready for dispatch",
        )
    return {"success": True, **result}


@router.post("/dispatch")
def dispatch_jobs():
    return {"success": True, **dispatch_ready_parser_jobs(limit=1)}


@router.get("/dispatch-control")
def get_dispatch_control():
    return {
        "auto_dispatch_enabled": ParserRepository().get_auto_dispatch_enabled()
    }


@router.post("/dispatch-control")
def set_dispatch_control(payload: ParserDispatchControlRequest):
    enabled = ParserRepository().set_auto_dispatch_enabled(payload.enabled)
    return {"success": True, "auto_dispatch_enabled": enabled}


@router.post("/templates/{template_id}/activate")
def activate_template(template_id: str):
    try:
        item = ParserRepository().activate_template(template_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item.to_dict()


@router.post("/templates/{template_id}/reject")
def reject_template(template_id: str):
    ParserRepository().set_template_status(template_id, "rejected")
    return {"success": True, "template_id": template_id, "status": "rejected"}


@router.post("/templates/{template_id}/deprecate")
def deprecate_template(template_id: str):
    ParserRepository().set_template_status(template_id, "deprecated")
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
        retired = ParserRepository().supersede_legacy_jobs()
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
def dataset_coverage():
    patrol_repository = PatrolRepository()
    output = []
    for dataset in patrol_repository.list_datasets(mode="parser_validation"):
        snapshots = patrol_repository.list_dataset_snapshots(
            dataset["id"],
            mode="parser_validation",
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
            elif item.parse_status == "ignored":
                counts["ignored"] += 1
            elif item.parse_status == "template_missing":
                counts["missing"] += 1
            elif item.parse_status == "identity_unknown":
                counts["identity_unknown"] += 1
            elif item.parse_status == "parse_failed":
                counts["failed"] += 1
        eligible = len(snapshots) - counts["ignored"]
        successful = counts["parsed"] + counts["empty"]
        output.append(
            {
                **dataset,
                "dataset_id": dataset["id"],
                **counts,
                "eligible_count": eligible,
                "structured_coverage_rate": (
                    successful / eligible if eligible else 1.0
                ),
                "parse_success_rate": (
                    counts["parsed"] / eligible if eligible else 1.0
                ),
                "legal_empty_rate": counts["empty"] / eligible if eligible else 0.0,
                "missing_template_rate": (
                    counts["missing"] / eligible if eligible else 0.0
                ),
                "parse_failure_rate": (
                    counts["failed"] / eligible if eligible else 0.0
                ),
                "primary_key_stability_rate": (
                    successful / eligible if eligible else 1.0
                ),
            }
        )
    return {"items": output}


@router.get("/aliases")
def list_aliases():
    return {"items": ParserRepository().list_command_aliases()}


@router.get("/device-profiles")
def list_device_profiles():
    return {"items": ParserRepository().list_device_profiles()}


@router.get("/identity-signatures")
def list_identity_signatures():
    return {"items": ParserRepository().list_identity_signatures()}


@router.get("/coverage")
def parser_coverage():
    repository = ParserRepository()
    repository.backfill_contract_metadata()
    templates = repository.list_templates()
    jobs = repository.list_jobs()
    active = sum(item["status"] == "active" for item in templates)
    open_jobs = [item for item in jobs if item["status"] in OPEN_JOB_STATUSES]
    return {
        "templates_total": len(templates),
        "templates_active": active,
        "jobs_total": len(jobs),
        "jobs_pending": len(open_jobs),
        "missing_candidates": sum(
            item["job_type"] == "CREATE_TEMPLATE" for item in open_jobs
        ),
        "repair_candidates": sum(
            item["job_type"] == "REPAIR_TEMPLATE" for item in open_jobs
        ),
        "command_aliases": len(repository.list_command_aliases()),
        "device_profiles": len(repository.list_device_profiles()),
        "identity_signatures": len(repository.list_identity_signatures()),
        "auto_dispatch_enabled": repository.get_auto_dispatch_enabled(),
    }


@router.get("/rule-generation-jobs")
def list_rule_generation_jobs(
    job_type: str | None = None,
    status: str | None = None,
    limit: int = 200,
):
    return {
        "items": RuleGenerationJobRepository().list_jobs(
            job_type=job_type,
            status=status,
            limit=limit,
        )
    }


@router.get("/replay-reports")
def list_replay_reports(
    success: bool | None = None,
    asset_type: str | None = None,
    limit: int = 200,
):
    return {
        "items": RuleGenerationJobRepository().list_replay_reports(
            success=success,
            asset_type=asset_type,
            limit=limit,
        )
    }


@router.get("/approval-audit")
def list_rule_asset_approval_audit(
    action: str | None = None,
    asset_type: str | None = None,
    actor: str | None = None,
    limit: int = 200,
):
    try:
        return {
            "items": RuleGenerationJobRepository().list_approval_audit(
                action=action,
                asset_type=asset_type,
                actor=actor,
                limit=limit,
            )
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/agent-loop-attempts")
def list_rule_asset_agent_loop_attempts(
    asset_type: str | None = None,
    stage: str | None = None,
    success: bool | None = None,
    limit: int = 200,
):
    return {
        "items": RuleGenerationJobRepository().list_agent_loop_attempts(
            asset_type=asset_type,
            stage=stage,
            success=success,
            limit=limit,
        )
    }


@router.get("/observation-contracts")
def list_observation_contracts(
    source_type: str | None = None,
    status: str | None = None,
    limit: int = 200,
):
    return {
        "items": ObservationContractRepository().list_contracts(
            source_type=source_type,
            status=status,
            limit=limit,
        )
    }


@router.get("/change-policies")
def list_change_policies(
    status: str | None = None,
    observation_type: str | None = None,
    limit: int = 200,
):
    return {
        "items": ChangePolicyRepository().list_versions(
            status=status,
            observation_type=observation_type,
            limit=limit,
        )
    }


@router.post("/runtime-assets/resolve")
def resolve_runtime_rule_assets(payload: RuntimeRuleAssetResolveRequest):
    return RuntimeRuleAssetResolver().load_bundle(
        observation_schemas=payload.observation_schemas,
        observation_types=payload.observation_types,
        include_event_rules=payload.include_event_rules,
    ).to_dict()


@router.post("/rule-generation-jobs")
def create_rule_generation_job(payload: RuleGenerationJobCreateRequest):
    try:
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
            created_by=payload.created_by,
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
    try:
        return RuleGenerationJobRepository().transition_job(
            job_id,
            status=payload.status,
            validation_report=payload.validation_report,
            asset_ref=payload.asset_ref,
            asset_type=payload.asset_type,
            asset_id=payload.asset_id,
            asset_version_id=payload.asset_version_id,
            asset_digest=payload.asset_digest,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuleGenerationJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/rule-generation-jobs/{job_id}/validation-report")
def attach_rule_generation_validation_report(
    job_id: str,
    payload: RuleGenerationJobValidationRequest,
):
    try:
        return RuleGenerationJobRepository().attach_validation_report(
            job_id,
            validation_report=payload.validation_report,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/rule-generation-jobs/{job_id}/approve")
def approve_rule_generation_job(
    job_id: str,
    payload: RuleGenerationJobApprovalRequest | None = None,
):
    try:
        return RuleAssetApprovalService().approve(
            job_id,
            actor=payload.actor if payload else None,
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
    payload: RuleGenerationJobApprovalRequest | None = None,
):
    try:
        return AgentLoopRevisionService().create_revision_job(
            job_id,
            actor=payload.actor if payload else None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/rule-generation-jobs/{job_id}/execute-revision")
def execute_rule_generation_revision_job(
    job_id: str,
    payload: RuleGenerationJobApprovalRequest | None = None,
):
    try:
        return AgentLoopRevisionExecutor().execute(
            job_id,
            actor=payload.actor if payload else None,
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
    payload: RuleGenerationJobApprovalRequest | None = None,
):
    try:
        return RuleAssetSubmissionService().submit_candidate(
            job_id,
            actor=payload.actor if payload else None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuleGenerationJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/rule-generation-jobs/{job_id}/reject")
def reject_rule_generation_job(
    job_id: str,
    payload: RuleGenerationJobApprovalRequest | None = None,
):
    try:
        return RuleAssetApprovalService().reject(
            job_id,
            actor=payload.actor if payload else None,
            reason=payload.reason if payload else None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuleGenerationJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/rule-generation-jobs/{job_id}/deprecate")
def deprecate_rule_generation_job(
    job_id: str,
    payload: RuleGenerationJobApprovalRequest | None = None,
):
    try:
        return RuleAssetApprovalService().deprecate(
            job_id,
            actor=payload.actor if payload else None,
            reason=payload.reason if payload else None,
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
    limit: int = 500,
):
    return {
        "items": EventRuleRepository().list_versions(
            status=status,
            event_type=event_type,
            limit=limit,
        )
    }


@router.post("/event-rules")
def upsert_event_rule(payload: EventRuleUpsertRequest):
    try:
        if payload.status == "active":
            raise ValueError("event rule versions must be created as draft or pending_approval")
        version_id = EventRuleRepository().create_rule_version(
            name=payload.name,
            event_type=payload.event_type,
            severity=payload.severity,
            condition=payload.condition,
            description=payload.description,
            created_by=payload.created_by,
            status=payload.status,
        )
        return {"success": True, "version_id": version_id}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/event-rules/{version_id}/activate")
def activate_event_rule(version_id: str):
    try:
        return EventRuleRepository().activate_rule_version(version_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/event-rules/{version_id}/deprecate")
def deprecate_event_rule(version_id: str):
    try:
        return EventRuleRepository().deprecate_rule_version(version_id)
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
    cases = (
        [
            EventRuleValidationCase(
                name=item.name,
                change=item.change,
                expected_event_type=item.expected_event_type,
                expected_rule_id=item.expected_rule_id,
            )
            for item in case_payloads
        ]
    )
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
def bootstrap_event_rules():
    return {"success": True, "created": EventRuleRepository().ensure_default_rules()}


@router.get("/command-profiles")
def list_command_profiles():
    return {"items": CommandProfileRepository().list_profiles()}


@router.post("/command-profiles")
def upsert_command_profile(payload: CommandProfileUpsertRequest):
    try:
        if payload.status == "active":
            version_id = CommandProfileRepository().upsert_active_profile(
                name=payload.name,
                action=payload.action,
                commands=payload.commands,
                created_by=payload.created_by,
            )
        else:
            version_id = CommandProfileRepository().create_profile_version(
                name=payload.name,
                action=payload.action,
                commands=payload.commands,
                created_by=payload.created_by,
                status="draft",
            )
        return {"success": True, "profile_version_id": version_id, "status": payload.status}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/command-profiles/{profile_version_id}/activate")
def activate_command_profile(profile_version_id: str):
    try:
        repository = CommandProfileRepository()
        repository.validate_profile_activation(profile_version_id)
        return repository.activate_profile_version(profile_version_id)
    except Exception as exc:
        message = str(exc)
        if message == "command profile impact evidence is required before activation":
            message = "激活前需要先完成影响预览并保存证据"
        raise HTTPException(status_code=400, detail=message) from exc


@router.delete("/command-profiles/{profile_version_id}")
def delete_command_profile(profile_version_id: str):
    try:
        return CommandProfileRepository().delete_profile_version(profile_version_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/command-profiles/{profile_version_id}/deprecate")
def deprecate_command_profile(profile_version_id: str):
    try:
        return CommandProfileRepository().deprecate_profile_version(profile_version_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/command-profiles/{profile_version_id}/rollback")
def rollback_command_profile(profile_version_id: str):
    try:
        return CommandProfileRepository().rollback_profile_version(profile_version_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/command-profiles/activate")
def activate_command_profile_legacy(payload: CommandProfileUpsertRequest):
    try:
        version_id = CommandProfileRepository().upsert_active_profile(
            name=payload.name,
            action=payload.action,
            commands=payload.commands,
            created_by=payload.created_by,
        )
        return {"success": True, "profile_version_id": version_id, "status": "active"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/command-profile-bindings")
def list_command_profile_bindings():
    return {"items": CommandProfileRepository().list_bindings()}


@router.post("/command-profile-bindings")
def upsert_command_profile_binding(payload: CommandProfileBindingUpsertRequest):
    try:
        binding_id = CommandProfileRepository().upsert_binding(
            profile_version_id=payload.profile_version_id,
            selector=payload.selector,
            priority=payload.priority,
            status=payload.status,
        )
        return {"success": True, "binding_id": binding_id}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/command-profile-bindings/{binding_id}/enable")
def enable_command_profile_binding(binding_id: str):
    try:
        return CommandProfileRepository().set_binding_status(binding_id, "active")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/command-profile-bindings/{binding_id}/disable")
def disable_command_profile_binding(binding_id: str):
    try:
        return CommandProfileRepository().set_binding_status(binding_id, "disabled")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _profile_impact_commands(payload: CommandProfileImpactPreviewRequest) -> tuple[str, str | None, list[str]]:
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
                    CollectionItem(command=command, action=payload.action)
                    for command in commands
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

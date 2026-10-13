"""Parser control-plane management API."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from src.apps.rule_governance.backend.check_items.adapter_projection_migration import (
    AdapterProjectionCutoverInventoryService,
    generate_projection_candidate,
)
from src.apps.rule_governance.backend.check_items.rollout_execution import (
    RolloutStageExecutionService,
)
from src.apps.rule_governance.backend.check_items.rollout_legacy import (
    LegacyRouteError,
    RolloutLegacyRouteService,
)
from src.apps.rule_governance.backend.parser.assets import TemplateAssetStore, safe_slug
from src.apps.rule_governance.backend.parser.backfill import ParserBackfillService
from src.apps.rule_governance.backend.parser.bootstrap import bootstrap_shared_templates
from src.apps.rule_governance.backend.parser.dialect_audit import TextFSMDialectAuditService
from src.apps.rule_governance.backend.parser.discovery_assets import bootstrap_discovery_assets
from src.apps.rule_governance.backend.parser.field_contract import build_parser_field_contract
from src.apps.rule_governance.backend.parser.identity import DeviceIdentity
from src.apps.rule_governance.backend.parser.manual_candidate import (
    ManualParserCandidateSubmissionService,
    ManualParserCandidateValidationError,
)
from src.apps.rule_governance.backend.parser.policy import PolicyDecision
from src.apps.rule_governance.backend.parser.repository import (
    OPEN_JOB_STATUSES,
    ParserGovernanceProjectionRepository,
    ParserJobRepository,
    ParserJobStateError,
    ParserMetadataRepository,
    ParserSampleRepository,
    ParserTemplateRepository,
    ParserTemplateStateError,
    generated_template_activation_errors,
    parser_semantic_review_activation_errors,
)
from src.apps.rule_governance.backend.parser.runtime import ParserRuntime
from src.apps.rule_governance.backend.parser.semantic_catalog import load_semantic_catalog
from src.apps.rule_governance.backend.parser.tasks import dispatch_ready_parser_jobs
from src.apps.rule_governance.backend.parser.worker import PARSER_EXECUTION_TASK
from src.apps.rule_governance.backend.parser.value_mapping_config import (
    parser_value_mapping_view,
    save_value_mapping_document,
)
from src.apps.rule_governance.backend.parser.work_queue import ParserWorkQueueService
from src.apps.rule_governance.backend.rule_control import (
    RuleAssetApprovalService,
    RuleGenerationJobRepository,
    RuleGenerationJobStateError,
    RuntimeRuleAssetSeedService,
)
from src.apps.rule_governance.backend.rule_control.bundles import RuleAssetBundleService
from src.apps.rule_governance.backend.rule_control.command_asset_units import (
    RuleAssetCommandUnitService,
)
from src.apps.rule_governance.backend.rule_control.engineering_revision import (
    RuleAssetEngineeringRevisionError,
    RuleAssetEngineeringRevisionService,
)
from src.apps.rule_governance.backend.access import app_user
from src.auth.models import CurrentUser
from src.core.celery_tasks.celery_app import celery
from src.core.check_items import (
    AssessmentPolicyAssetService,
    AssessmentPolicyConflictError,
    AssessmentPolicyMigrationService,
    AssetGapRepository,
    CanonicalRuleContextService,
    CatalogGovernanceConflictError,
    CheckItemCatalogGovernanceService,
    CheckItemCatalogRepository,
    CheckItemEffectivePlanService,
    CheckItemRuntimeBundleService,
    EffectivePlanRequest,
    PatrolProfileCheckItemRepository,
    RolloutCampaignService,
    RolloutValidationError,
    RuntimeBundleValidationError,
    build_inventory_snapshot,
    build_target_manifest,
    check_item_display_description,
    check_item_display_name,
    load_canonical_catalog,
)
from src.core.check_items.parser_capability import (
    build_parser_check_item_capability,
    parser_output_profile,
    resolve_parser_check_item_ids,
)
from src.core.collection.policy import (
    CollectionItem,
    CollectionPolicyResolver,
    CommandProfileRepository,
    ExecutionPlan,
)
from src.core.device_ops.tools import PatrolTool
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
from src.observability.deprecations import observe_legacy_path
from src.platform.inventory.repository import DeviceRepository

logger = logging.getLogger(__name__)

from .published_assets import PublishedAssetService

router = APIRouter(
    prefix="/parser",
    tags=["Parser Control Plane"],
    dependencies=[Depends(app_user)],
)


def _refresh_rule_asset_projection_for_template(template_id: str) -> None:
    try:
        RuleAssetCommandUnitService().reconcile_for_parser_template(template_id)
    except Exception:
        logger.exception(
            "Rule asset projection refresh failed template_id=%s",
            template_id,
        )


class PublishedAssetBulkRequest(BaseModel):
    kind: Literal['parser', 'policy', 'event']
    action: Literal['deprecate', 'delete']
    ids: list[str] = Field(min_length=1, max_length=100)
    reason: str = Field(default='', max_length=2000)


@router.post('/published-assets/bulk-action')
def bulk_action_published_assets(request: Request, payload: PublishedAssetBulkRequest):
    from .access import execution_lock
    try:
        with execution_lock():
            result = PublishedAssetService().bulk(**payload.model_dump(), actor=_authenticated_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if payload.kind == 'parser':
        for item in result['results']:
            if item['success']:
                _refresh_rule_asset_projection_for_template(item['id'])
    return result


def _authenticated_user(request: Request) -> CurrentUser:
    user = getattr(getattr(request, "state", None), "current_user", None)
    if not isinstance(user, CurrentUser):
        raise HTTPException(status_code=401, detail="authenticated user context is required")
    return user


def _authenticated_actor(request: Request) -> str:
    user = _authenticated_user(request)
    actor = str(user.username or user.user_id).strip()
    if not actor:
        raise HTTPException(status_code=401, detail="authenticated user identity is empty")
    return actor


def _required_reason(value: str | None, *, action: str) -> str:
    reason = str(value or "").strip()
    if not reason:
        raise HTTPException(status_code=422, detail=f"{action} requires a reason")
    return reason


def _raise_assessment_policy_error(exc: ValueError) -> None:
    if isinstance(exc, AssessmentPolicyConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    status_code = 404 if "not found" in str(exc).casefold() else 422
    raise HTTPException(status_code=status_code, detail=str(exc)) from exc


class ParserDispatchControlRequest(BaseModel):
    enabled: bool
    reason: str


class RuleAssetAutomationControlRequest(BaseModel):
    mode: Literal["disabled", "generate_only"]
    max_concurrency: int = 1
    max_generation_attempts: int = 3
    retry_delay_seconds: int = 300
    stale_run_seconds: int = 1800
    min_period_count: int = 2
    min_device_count: int = 2
    min_record_count: int = 10
    max_estimated_daily_events: int = 500
    allow_critical: bool = False
    reason: str


class ParserTemplateApprovalRequest(BaseModel):
    reason: str | None = None


class ParserTemplateSemanticReviewRequest(BaseModel):
    reason: str | None = None
    result_confirmed: bool
    primary_key_confirmed: bool


class ParserTemplateForceActivationRequest(BaseModel):
    reason: str


class ParserTemplateStatusRequest(BaseModel):
    reason: str


class ParserBackfillCreateRequest(BaseModel):
    template_id: str
    filters: dict[str, Any] | None = None


class ParserBackfillCommitRequest(BaseModel):
    reason: str | None = None
    analyze_changes: bool = False


class ParserBulkJobDeleteRequest(BaseModel):
    job_ids: list[str]
    reason: str


class ParserJobDeleteRequest(BaseModel):
    reason: str


class ParserJobRecoverRequest(BaseModel):
    reason: str | None = None


class ParserSpecUpdateRequest(BaseModel):
    parser_spec: dict[str, Any]
    confirmed: bool = False
    reason: str


class ParserSpecSuggestionRequest(BaseModel):
    reason: str | None = None
    revision_instruction: str | None = Field(default=None, max_length=4000)
    check_item_id: str | None = Field(default=None, max_length=128)


class ParserValueMappingUpdateRequest(BaseModel):
    scoped_value_mappings: list[dict[str, Any]] = Field(default_factory=list)


class ParserManualCandidateRequest(BaseModel):
    template_text: str = Field(min_length=1, max_length=2_000_000)
    based_on_template_id: str | None = None
    reason: str | None = None


class ParserSampleRoleUpdateRequest(BaseModel):
    role: Literal["unassigned", "generation", "acceptance", "negative"]
    expectation: dict[str, Any] | None = None
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
    reason: str | None = None


class RuleAssetEngineeringRevisionRequest(BaseModel):
    contract: dict[str, Any]
    change_policies: list[dict[str, Any]]
    event_rules: list[dict[str, Any]]
    reason: str
    idempotency_key: str


class EngineeringRevisionResolutionRequest(BaseModel):
    evidence_digest: str = Field(min_length=64, max_length=64)
    note: str = Field(min_length=5, max_length=2000)


class RuntimeRuleAssetResolveRequest(BaseModel):
    observation_schemas: list[tuple[str, str]] = []
    observation_types: list[str] = []
    include_event_rules: bool = True


class RuntimeRuleAssetSeedRequest(BaseModel):
    reason: str


class CommandProfileUpsertRequest(BaseModel):
    name: str
    action: str = "patrol"
    commands: list[str] = Field(default_factory=list)
    check_item_ids: list[str] = Field(default_factory=list)
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


class PatrolProfileCheckItemSelectionRequest(BaseModel):
    check_item_ids: list[str] = Field(default_factory=list)


class CollectionImplementationDraftRequest(BaseModel):
    id: str = Field(..., min_length=3, max_length=128)
    name: str = Field(..., min_length=2, max_length=256)
    description: str | None = None
    check_item_ids: list[str] = Field(..., min_length=1)
    match: dict[str, Any]
    commands: list[dict[str, Any]] = Field(..., min_length=1)
    collector: str = "cli"
    parser_requirement: Literal[
        "structured",
        "normalized_diff",
        "hash_only",
        "evidence_only",
    ]
    template_scope: str | None = None
    adapter_spec: dict[str, Any] | None = None
    output_quality_policy: dict[str, Any] | None = None
    selection_priority: int = Field(default=100, ge=0, le=10000)
    reason: str = Field(default="", max_length=2000)


class CollectionImplementationDeleteRequest(BaseModel):
    reason: str = Field(default="", max_length=2000)


class CommandAdapterPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter_spec: dict[str, Any]
    record_sets: dict[str, list[dict[str, Any]]]
    device_id: str = "preview-device"
    adapter_version_id: str = "preview"


class AdapterProjectionCandidateRequest(BaseModel):
    """Bounded candidate generation for exactly one CheckItem/Parser output."""

    model_config = ConfigDict(extra="forbid")

    check_item_id: str = Field(..., min_length=1, max_length=128)
    parser_template_id: str = Field(..., min_length=1, max_length=128)
    source_record_set: str = Field(..., min_length=1, max_length=128)
    generation_provider: Literal["deterministic", "llm"] = "deterministic"
    max_agent_attempts: int = Field(default=3, ge=1, le=3)
    proposal: dict[str, Any] | None = None


class AdapterProjectionValidationRequest(BaseModel):
    """Validate an edited Projection against the selected immutable context."""

    model_config = ConfigDict(extra="forbid")

    check_item_id: str = Field(..., min_length=1, max_length=128)
    parser_template_id: str = Field(..., min_length=1, max_length=128)
    source_record_set: str = Field(..., min_length=1, max_length=128)
    proposal: dict[str, Any]


class AdapterProjectionReplayAttestation(BaseModel):
    """Sanitized pre-release proof; raw CLI stays in the evidence store."""

    model_config = ConfigDict(extra="forbid")

    success: bool = False
    status: Literal["passed", "complete", "ready", "failed", "blocked", "pending"] = "pending"
    collected_sample_count: int = Field(default=0, ge=0)
    sample_count: int = Field(default=0, ge=0)
    standard_records_verified: bool = False
    primary_keys_verified: bool = False
    quality_status_verified: bool = False
    rule_results_verified: bool = False
    evidence_summary_verified: bool = False
    evidence_digest: str | None = Field(default=None, min_length=64, max_length=64)


class AdapterProjectionCutoverReadinessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replay_by_implementation_version: dict[
        str, AdapterProjectionReplayAttestation
    ] = Field(default_factory=dict)


class AdapterProjectionCutoverAuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_readiness_digest: str = Field(..., min_length=64, max_length=64)
    reason: str = Field(..., min_length=1, max_length=2000)


class CheckItemDraftRequest(BaseModel):
    id: str = Field(..., min_length=3, max_length=128)
    name: str = Field(..., min_length=2, max_length=256)
    category: str = Field(..., min_length=1, max_length=64)
    description: str | None = None
    result_mode: Literal[
        "structured",
        "normalized_diff",
        "hash_only",
        "evidence_only",
    ]
    assessment_mode: Literal["health", "change", "evidence"]
    canonical_contract: dict[str, Any]
    report_projection: str = Field(..., min_length=1, max_length=128)
    applicability: dict[str, Any] = Field(default_factory=dict)
    freshness_policy: dict[str, Any] = Field(default_factory=dict)
    empty_result_policy: dict[str, Any] = Field(default_factory=dict)
    coverage_policy: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=2000)


class CheckItemRulePreviewRequest(BaseModel):
    id: str = Field(default="", max_length=128)
    result_mode: Literal[
        "structured",
        "normalized_diff",
        "hash_only",
        "evidence_only",
    ]
    canonical_contract: dict[str, Any]


class CheckItemDeleteRequest(BaseModel):
    reason: str = Field(default="", max_length=2000)


class CatalogChangeLifecycleRequest(BaseModel):
    reason: str = Field(default="", max_length=2000)
    expected_impact_digest: str = Field(..., min_length=64, max_length=64)
    rollout_campaign_id: str | None = None


class RuntimeBundleAssemblyRequest(BaseModel):
    check_item_version_id: str
    implementation_version_id: str
    parser_template_ids: list[str] = Field(default_factory=list)
    change_policy_version_ids: list[str] = Field(default_factory=list)
    event_rule_version_ids: list[str] = Field(default_factory=list)
    comparison_profile_version_id: str | None = None


class RuntimeBundleAssembleRequest(RuntimeBundleAssemblyRequest):
    preview_digest: str


class RuntimeBundleLifecycleRequest(BaseModel):
    reason: str = Field(default="", max_length=2000)


class AssessmentPolicyVersionCreateRequest(BaseModel):
    check_item_id: str
    check_item_version_id: str
    observation_type: str
    policy_role: Literal[
        "high",
        "critical_high",
        "low",
        "critical_low",
        "expected_state",
        "applicability",
    ]
    name: str
    description: str | None = None
    profile_id: str | None = None
    policy: dict[str, Any]
    evidence_refs: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class AssessmentPolicyLifecycleRequest(BaseModel):
    reason: str


class AssessmentPolicyReviewRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    reason: str


class AssessmentPolicyBindingRequest(BaseModel):
    binding_type: Literal[
        "inventory_profile", "scope_override", "catalog_default"
    ]
    scope: dict[str, Any] = Field(default_factory=dict)
    reason: str
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    replaces_binding_id: str | None = None


class AssessmentPolicyResolutionPreviewRequest(BaseModel):
    check_item_version_id: str
    device: dict[str, Any]
    observation: dict[str, Any] | None = None
    rule: dict[str, Any] | None = None
    policy_effective_at: datetime | None = None
    include_legacy: bool = False
    runtime_bundle_version_id: str = "assessment-policy-preview"
    runtime_bundle_digest: str = "0" * 64


class AssessmentPolicyImpactPreviewRequest(BaseModel):
    binding_type: Literal[
        "inventory_profile", "scope_override", "catalog_default"
    ]
    proposed_scope: dict[str, Any] = Field(default_factory=dict)
    target_selector: dict[str, Any] = Field(default_factory=dict)
    policy_effective_at: datetime | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)


class AssessmentPolicyMigrationInventoryRequest(BaseModel):
    check_item_ids: list[str] = Field(default_factory=list)
    include_execution_snapshots: bool = True
    after_execution_id: str | None = None
    execution_page_size: int = Field(default=1000, ge=1, le=5000)


class AssessmentPolicyShadowCompareRequest(BaseModel):
    check_item_version_id: str
    device: dict[str, Any]
    observation: dict[str, Any]
    rule: dict[str, Any]
    policy_effective_at: datetime
    runtime_bundle_version_id: str = "assessment-policy-shadow"
    runtime_bundle_digest: str = "0" * 64


class AssessmentPolicyCutoverRequest(BaseModel):
    check_item_version_id: str
    device_id: str
    shadow_result_ids: list[str] = Field(..., min_length=1)
    reason: str = Field(..., min_length=8, max_length=2000)


class AssessmentPolicyCutoverRollbackRequest(BaseModel):
    reason: str = Field(..., min_length=8, max_length=2000)


class RolloutCampaignCreateRequest(BaseModel):
    name: str
    description: str | None = None


class RolloutCampaignFreezeRequest(BaseModel):
    target_manifest: dict[str, Any]
    runtime_bundle_refs: list[dict[str, Any]] = Field(default_factory=list)
    comparison_context_ids: list[str] = Field(default_factory=list)
    baseline_window: dict[str, Any]
    thresholds: dict[str, Any] = Field(default_factory=dict)
    rollback_plan: dict[str, Any]
    reason: str


class RolloutCampaignLifecycleRequest(BaseModel):
    reason: str
    execution_mode: Literal[
        "snapshot_replay",
        "live_capture",
    ] = "snapshot_replay"


class RolloutLegacyRouteCreateRequest(BaseModel):
    route_name: str = Field(..., min_length=1, max_length=256)
    runtime_bundle_version_id: str = Field(..., min_length=1, max_length=64)


class RolloutLegacyRouteLifecycleRequest(BaseModel):
    reason: str


class RolloutCampaignApprovalRequest(BaseModel):
    comment: str


class RolloutCampaignResumeRequest(BaseModel):
    observed_digests: dict[str, str]
    reason: str


class RolloutCampaignRollbackRequest(BaseModel):
    evidence_refs: list[dict[str, Any] | str] = Field(default_factory=list)
    reason: str


class RolloutRoutingDecisionRequest(BaseModel):
    device_id: str
    check_item_version_id: str
    target_manifest_digest: str
    runtime_bundle_digest: str
    comparison_context_digest: str


def _raise_rollout_error(exc: RolloutValidationError) -> None:
    status_code = {
        "not_found": 404,
        "separation_of_duties": 409,
        "idempotency_conflict": 409,
        "new_campaign_required": 409,
        "rollout_blocked": 409,
    }.get(exc.code, 422)
    raise HTTPException(
        status_code=status_code,
        detail={
            "message": "渐进发布操作被阻断",
            "code": exc.code,
            "blockers": list(exc.blockers),
        },
    ) from exc


def _raise_catalog_governance_error(exc: ValueError) -> None:
    if isinstance(exc, CatalogGovernanceConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    status_code = 404 if "not found" in str(exc).casefold() else 422
    raise HTTPException(status_code=status_code, detail=str(exc)) from exc


def _raise_adapter_projection_error(exc: ValueError) -> None:
    message = str(exc)
    status_code = 404 if any(
        marker in message.casefold()
        for marker in ("not found", "unknown check item", "parser template")
    ) else 422
    raise HTTPException(status_code=status_code, detail=message) from exc


def _adapter_projection_context(
    *,
    check_item_id: str,
    parser_template_id: str,
) -> dict[str, Any]:
    """Load only the frozen fields a Projection candidate may use."""

    normalized_check_item_id = str(check_item_id or "").strip()
    normalized_template_id = str(parser_template_id or "").strip()
    if not normalized_check_item_id:
        raise ValueError("selected CheckItem id is required")
    if not normalized_template_id:
        raise ValueError("Parser template_id is required")

    catalog = CheckItemCatalogRepository().load_active()
    check_item = next(
        (
            item
            for item in catalog.check_items
            if str(item.id) == normalized_check_item_id
        ),
        None,
    )
    if check_item is None:
        raise ValueError(
            f"unknown active CheckItem: {normalized_check_item_id}"
        )
    template = ParserTemplateRepository().get_template(normalized_template_id)
    if template is None:
        raise ValueError(f"Parser template not found: {normalized_template_id}")
    template_status = str(template.status or "").strip().casefold()
    if template_status not in {"active", "pending_approval"}:
        raise ValueError(
            "Adapter Projection requires an active or pending-approval Parser template: "
            f"{normalized_template_id}"
        )
    field_contract = build_parser_field_contract(
        validation=dict(template.validation_json or {}),
        template_text=str(template.template_text or ""),
    )
    if field_contract.get("status") != "aligned":
        raise ValueError(
            "Parser field contract must be aligned before Projection generation: "
            + "; ".join(
                str(item) for item in field_contract.get("errors") or []
            )
        )
    record_sets = {
        str(name): [str(field) for field in fields or []]
        for name, fields in dict(
            field_contract.get("template_record_sets") or {}
        ).items()
        if str(name).strip()
    }
    if len(record_sets) == 1:
        fields = next(iter(record_sets.values()))
        validation = dict(template.validation_json or {})
        parser_spec = dict(validation.get("parser_spec") or {})
        source_parser_spec = dict(validation.get("source_parser_spec") or {})
        source_record_sets = source_parser_spec.get("record_sets") or []
        aliases = {
            str(validation.get("entity_type") or "").strip(),
            str(parser_spec.get("entity") or "").strip(),
        }
        if len(source_record_sets) == 1 and isinstance(source_record_sets[0], dict):
            aliases.update(
                {
                    str(source_record_sets[0].get("name") or "").strip(),
                    str(source_record_sets[0].get("entity") or "").strip(),
                }
            )
        for alias in aliases - {""}:
            record_sets.setdefault(alias, fields)
    if not record_sets:
        raise ValueError("Parser field contract has no executable record set")
    return {
        "selected_check_item": {
            "id": str(check_item.id),
            "version_id": str(check_item.version_id),
            "canonical_contract": dict(check_item.canonical_contract or {}),
        },
        "parser": {
            "template_id": normalized_template_id,
            "record_sets": record_sets,
            "field_contract": field_contract,
        },
    }


@router.get("/value-mappings")
def get_parser_value_mappings(
    _user: CurrentUser = Depends(app_user),
):
    try:
        return parser_value_mapping_view()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.put("/value-mappings")
def update_parser_value_mappings(
    payload: ParserValueMappingUpdateRequest,
    _user: CurrentUser = Depends(app_user),
):
    try:
        save_value_mapping_document(
            {
                "version": 2,
                "scoped_value_mappings": payload.scoped_value_mappings,
            }
        )
        load_semantic_catalog.cache_clear()
        return parser_value_mapping_view()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/asset-production/units")
def list_asset_production_units(
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
    user: CurrentUser = Depends(app_user),
):
    observe_legacy_path(
        "command-centered-rule-asset-workspace",
        "list_command_units",
        outcome="read",
    )
    try:
        return RuleAssetCommandUnitService().list_production_units(
            owner_id=user.user_id,
            is_admin=user.is_admin(),
            page=page,
            page_size=page_size,
            search=search,
            bucket=bucket,
            governance_status=governance_status,
            stage=stage,
            vendor=vendor,
            model=model,
            asset_mode=asset_mode,
            offline_job_id=offline_job_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/asset-production/check-items")
def list_asset_production_check_items(
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
    user: CurrentUser = Depends(app_user),
):
    try:
        return RuleAssetCommandUnitService().list_check_item_production_units(
            owner_id=user.user_id,
            is_admin=user.is_admin(),
            page=page,
            page_size=page_size,
            search=search,
            bucket=bucket,
            governance_status=governance_status,
            stage=stage,
            vendor=vendor,
            model=model,
            asset_mode=asset_mode,
            offline_job_id=offline_job_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc








@router.get("/asset-production/units/{unit_id}")
def get_asset_production_unit(
    unit_id: str,
    evidence_page: int = 1,
    evidence_page_size: int = 25,
    user: CurrentUser = Depends(app_user),
):
    try:
        return RuleAssetCommandUnitService().get_production_unit(
            unit_id,
            owner_id=user.user_id,
            is_admin=user.is_admin(),
            evidence_page=evidence_page,
            evidence_page_size=evidence_page_size,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
            template = repository.get_template(str(item.get("id") or ""))
            if template is None:
                manifest.append({**item, "export_status": "missing_asset"})
                continue
            embedded_text = getattr(template, "template_text", None)
            if embedded_text is None:
                target = Path(str(template.asset_path or "")).resolve()
                if not target.is_file() or not target.is_relative_to(asset_root):
                    manifest.append({**item, "export_status": "missing_asset"})
                    continue
            try:
                text_content = asset_store.read(
                    template.asset_path,
                    template.sha256,
                    embedded_text,
                )
            except ValueError:
                manifest.append({**item, "export_status": "invalid_asset"})
                continue
            content = text_content.encode("utf-8")
            actual_sha256 = hashlib.sha256(content).hexdigest()
            filename = (
                f"{safe_slug(str(item.get('canonical_command') or '').lower())}__"
                f"{safe_slug(str(item.get('version') or 'unknown'))}.textfsm"
            )
            export_path = (
                f"templates/{item.get('vendor')}/{item.get('profile')}/{filename}"
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
    template = ParserTemplateRepository().get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="parser template not found")

    return _template_asset_evidence(template)


def _template_asset_evidence(template) -> dict[str, Any]:
    """Load the immutable template body used by preview and human approval."""

    asset_store = TemplateAssetStore()
    embedded_text = getattr(template, "template_text", None)
    if embedded_text is None:
        asset_root = asset_store.root.resolve()
        target = Path(template.asset_path).resolve()
        if not target.is_relative_to(asset_root):
            raise HTTPException(
                status_code=400,
                detail="template asset path is outside the shared asset store",
            )
        if not target.is_file():
            raise HTTPException(status_code=404, detail="template asset file not found")
    try:
        content = asset_store.read(
            template.asset_path,
            template.sha256,
            embedded_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "id": template.id,
        "vendor": template.vendor,
        "profile": template.profile,
        "canonical_command": template.canonical_command,
        "category": template.category,
        "version": template.version,
        "status": template.status,
        "sha256": template.sha256,
        "hash_valid": True,
        "content": content,
        "validation": template.validation_json or {},
    }


class _TemplateRegistry:
    def __init__(self, template):
        self.template = template

    def resolve_active(self, **_: Any):
        return self.template


class _CandidateComparisonPolicy:
    """Run the immutable candidate without requiring a production policy entry."""

    def __init__(self, template):
        self.template = template

    def decide(self, **_: Any) -> PolicyDecision:
        return PolicyDecision(
            enabled=True,
            canonical_command=str(self.template.canonical_command),
            handling="structured",
            asset_mode="full_chain",
            category=str(self.template.category or "candidate"),
            capability=str(self.template.category or "candidate"),
            capability_status="candidate",
            template_scope="model",
            template_profile=str(self.template.profile or "") or None,
        )


class _CandidateComparisonClassifier:
    """Leave empty/error semantics to the candidate's frozen ParserSpec."""

    @staticmethod
    def classify(*_: Any, **__: Any):
        return SimpleNamespace(status="data", reason="candidate_comparison")


def _parse_snapshot_with_template(template, snapshot):
    identity = DeviceIdentity(
        vendor=template.vendor,
        model=template.model or template.profile,
        family=template.family,
        confidence=1.0,
        source="template_comparison",
    )
    result = ParserRuntime(
        registry=_TemplateRegistry(template),
        policy=_CandidateComparisonPolicy(template),
        output_classifier=_CandidateComparisonClassifier(),
    ).parse(
        identity=identity,
        command=template.canonical_command,
        raw_text=snapshot.raw_text,
    )
    return {
        "status": result.parser_status,
        "failure_code": result.failure_code,
        "records": result.structured_data,
    }


def _comparison_record_sets(
    parse_result: dict[str, Any],
    contract: dict[str, Any],
    *,
    fallback_name: str,
) -> list[dict[str, Any]]:
    """Project runtime output into stable approval tables."""

    structured = parse_result.get("records")
    configured_sets = [
        dict(item)
        for item in contract.get("record_sets") or []
        if isinstance(item, dict)
    ]
    if (
        isinstance(structured, dict)
        and structured.get("format") == "netops.structured_record_sets/v1"
        and isinstance(structured.get("record_sets"), dict)
    ):
        runtime_sets = dict(structured["record_sets"])
        configured_by_name = {
            str(item.get("name") or ""): item for item in configured_sets
        }
        ordered_names = [
            str(item.get("name") or "")
            for item in configured_sets
            if str(item.get("name") or "")
        ]
        ordered_names.extend(
            name for name in runtime_sets if name not in configured_by_name
        )
        return [
            {
                "name": name,
                "entity_type": configured_by_name.get(name, {}).get("entity_type"),
                "primary_keys": list(
                    configured_by_name.get(name, {}).get("primary_keys") or []
                ),
                "required_fields": list(
                    configured_by_name.get(name, {}).get("required_fields") or []
                ),
                "records": [
                    dict(record)
                    for record in runtime_sets.get(name) or []
                    if isinstance(record, dict)
                ],
            }
            for name in ordered_names
        ]
    records = (
        [dict(item) for item in structured if isinstance(item, dict)]
        if isinstance(structured, list)
        else []
    )
    # A plain TextFSM template produces one unnamed list at runtime.  Its
    # executable record-set key is therefore always ``default``; ``category``
    # and ParserSpec entity names describe the records but are not runtime
    # keys.  Returning a semantic alias here let the Projection editor publish
    # an Adapter that replayed locally but could never be selected in runtime.
    return [
        {
            "name": "default",
            "entity_type": contract.get("entity_type") or fallback_name,
            "primary_keys": list(contract.get("primary_keys") or []),
            "required_fields": list(contract.get("required_fields") or []),
            "records": records,
        }
    ]


def _comparison_parse_result(template, snapshot) -> dict[str, Any]:
    result = _parse_snapshot_with_template(template, snapshot)
    contract = dict(getattr(template, "validation_json", None) or {})
    record_sets = _comparison_record_sets(
        result,
        contract,
        fallback_name=str(getattr(template, "category", None) or "records"),
    )
    return {
        **result,
        "record_sets": record_sets,
        "record_count": sum(len(item["records"]) for item in record_sets),
    }


@router.get("/templates/{template_id}/comparison")
def compare_template(template_id: str, request: Request, limit: int = 20):
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
    source_evidence_errors: list[str] = []
    frozen_samples: list[dict[str, Any]] = []
    if (candidate.validation_json or {}).get("import_package"):
        from .transfer_service import TransferService
        try:
            frozen_samples = TransferService().parser_samples(candidate)
        except ValueError as exc:
            source_evidence_errors.append(str(exc))
    evidence_loader = getattr(repository, "get_candidate_sample_evidence", None)
    if candidate.source_job_id and callable(evidence_loader):
        evidence = evidence_loader(
            candidate.id,
            include_sanitized_payload=True,
        )
        source_evidence_errors = [str(item) for item in evidence.get("errors") or []]
        frozen_samples = [dict(item) for item in evidence.get("items") or []]

    snapshots = []
    if not candidate.source_job_id or not callable(evidence_loader):
        snapshots = [
            item
            for item in PatrolRepository().list_dataset_snapshots(mode="parser_validation")
            if (item.vendor or "").lower() == candidate.vendor.lower()
            and (item.model or "").lower()
            == (candidate.model or candidate.profile).lower()
            and item.command_canonical == candidate.canonical_command
        ]
        if candidate.source_job_id:
            source_job = ParserJobRepository().get_job(candidate.source_job_id)
            source_snapshot_ids = [
                str(item.get("snapshot_id") or "")
                for item in (source_job or {}).get("samples", [])
                if item.get("snapshot_id")
            ]
            snapshots_by_id = {str(item.snapshot_id): item for item in snapshots}
            snapshots = [
                snapshots_by_id[snapshot_id]
                for snapshot_id in source_snapshot_ids
                if snapshot_id in snapshots_by_id
            ]
        else:
            snapshots = snapshots[: max(1, min(limit, 100))]
    rows = []
    for sample in frozen_samples:
        sanitized_text = str(sample.get("sanitized_text") or "")
        source_context = dict(sample.get("source_context") or {})
        snapshot_view = SimpleNamespace(raw_text=sanitized_text)
        proposed = _comparison_parse_result(candidate, snapshot_view)
        current = (
            _comparison_parse_result(active, snapshot_view) if active else None
        )
        rows.append(
            {
                "sample_id": sample.get("sample_id"),
                "snapshot_id": sample.get("snapshot_id"),
                "dataset_id": sample.get("dataset_id") or "legacy",
                "device_id": sample.get("device_id"),
                "command": source_context.get("canonical_command")
                or candidate.canonical_command,
                # Kept for API compatibility; this value is the frozen sanitized
                # evidence, never CommandSnapshot.raw_text.
                "raw_text": sanitized_text,
                "raw_text_sha256": sample.get("sanitized_hash")
                or hashlib.sha256(sanitized_text.encode("utf-8")).hexdigest(),
                "raw_text_redacted": sample.get("sample_role") != "package_replay",
                "evidence_kind": "rule_package_sample" if sample.get("sample_role") == "package_replay" else "sanitized_candidate_sample",
                "sample_role": sample.get("sample_role"),
                "expectation": sample.get("expectation") or {},
                "source_context": source_context,
                "current": current,
                "proposed": proposed,
                "changed": current != proposed,
            }
        )
    for snapshot in snapshots:
        proposed = _comparison_parse_result(candidate, snapshot)
        current = _comparison_parse_result(active, snapshot) if active else None
        raw_text = str(snapshot.raw_text or "")
        rows.append(
            {
                "snapshot_id": snapshot.snapshot_id,
                "dataset_id": snapshot.dataset_id or "legacy",
                "device_id": snapshot.device_id,
                "command": snapshot.command_canonical,
                "raw_text": raw_text,
                "raw_text_sha256": snapshot.raw_text_hash
                or hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
                "raw_text_redacted": False,
                "evidence_kind": "legacy_snapshot",
                "current": current,
                "proposed": proposed,
                "changed": current != proposed,
            }
        )
    passed_samples = sum(row["proposed"]["status"] in {"success", "empty"} for row in rows)
    candidate_contract = dict(candidate.validation_json or {})
    primary_keys = [
        str(item) for item in candidate_contract.get("primary_keys") or []
    ]
    if not source_evidence_errors and rows:
        repository.record_regression_validation(
            candidate.id,
            passed=passed_samples == len(rows),
            sample_count=len(rows),
            passed_samples=passed_samples,
            result_preview=[
                {
                    **(
                        {"sample_id": row["sample_id"]}
                        if row.get("sample_id")
                        else {}
                    ),
                    "snapshot_id": row["snapshot_id"],
                    "raw_text_sha256": row["raw_text_sha256"],
                    "status": row["proposed"]["status"],
                    "records": row["proposed"].get("records") or [],
                    "record_sets": row["proposed"].get("record_sets") or [],
                    "primary_keys": [
                        [str(record.get(key, "")).strip() for key in primary_keys]
                        for record in (row["proposed"].get("records") or [])
                        if isinstance(record, dict)
                    ],
                    "record_set_primary_keys": {
                        str(record_set.get("name") or "records"): [
                            [
                                str(record.get(key, "")).strip()
                                for key in record_set.get("primary_keys") or []
                            ]
                            for record in record_set.get("records") or []
                            if isinstance(record, dict)
                        ]
                        for record_set in row["proposed"].get("record_sets") or []
                        if isinstance(record_set, dict)
                    },
                }
                for row in rows
            ],
        )
    candidate = repository.get_template(template_id) or candidate
    candidate_contract = dict(candidate.validation_json or {})
    active_contract = dict(active.validation_json or {}) if active else {}
    candidate_template = _template_asset_evidence(candidate)
    parser_field_contract = build_parser_field_contract(
        validation=candidate_contract,
        template_text=candidate_template.get("content"),
    )
    review_blockers: list[str] = []
    if parser_field_contract["status"] in {"schema_drift", "template_invalid"}:
        review_blockers.append(
            "ParserSpec field inventory does not match the executable TextFSM schema"
        )
    elif (
        parser_field_contract["status"] == "parser_spec_missing"
        and candidate.source_job_id
    ):
        review_blockers.append(
            "Generated Parser candidate is missing its frozen ParserSpec field inventory"
        )
    review_blockers.extend(source_evidence_errors)
    if not candidate_template["hash_valid"]:
        review_blockers.append("候选模板内容与不可变资产摘要不一致")
    if not rows:
        review_blockers.append("没有可用于审批的解析验证样本")
    elif passed_samples != len(rows):
        review_blockers.append(
            f"候选模板仅通过 {passed_samples}/{len(rows)} 个解析验证样本"
        )
    review_blockers.extend(
        generated_template_activation_errors(
            candidate_contract,
            source_job_id=candidate.source_job_id,
        )
    )
    activation_blockers = [
        *review_blockers,
        *parser_semantic_review_activation_errors(candidate, candidate_contract),
    ]
    semantic_review = candidate_contract.get("human_semantic_review")
    current_user = _authenticated_user(request)
    current_actor = str(current_user.username or current_user.user_id).strip()
    semantic_reviewer = str(
        (
            (semantic_review or {}).get("reviewed_by")
            if isinstance(semantic_review, dict)
            else ""
        )
        or ""
    ).strip()
    same_as_semantic_reviewer = bool(
        semantic_reviewer
        and semantic_reviewer.casefold() == current_actor.casefold()
    )
    admin_override_applies = bool(
        same_as_semantic_reviewer
    )
    actor_blockers: list[str] = []
    activation_blockers.extend(actor_blockers)
    parser_spec = (
        candidate_contract.get("source_parser_spec")
        or candidate_contract.get("parser_spec")
        or candidate_contract
    )
    catalog = CheckItemCatalogRepository().load_active()
    target_check_item_ids = resolve_parser_check_item_ids(
        vendor=str(candidate.vendor or ""),
        model=str(candidate.model or "") or None,
        family=str(candidate.family or "") or None,
        profile=str(candidate.profile or "") or None,
        command=str(candidate.canonical_command or ""),
        catalog=catalog,
    )
    check_item_capabilities = [
        build_parser_check_item_capability(
            check_item_id=check_item_id,
            parser_spec=parser_spec,
            vendor=str(candidate.vendor or ""),
            model=str(candidate.model or "") or None,
            family=str(candidate.family or "") or None,
            profile=str(candidate.profile or "") or None,
            command=str(candidate.canonical_command or ""),
            catalog=catalog,
        )
        for check_item_id in target_check_item_ids
    ]
    for capability in check_item_capabilities:
        capability["claim_boundary"]["parser_structure_proven"] = not review_blockers
        capability["claim_boundary"]["parser_candidate_status"] = str(
            candidate.status
        )
    return {
        "template_id": candidate.id,
        "active_template_id": active.id if active else None,
        "sample_count": len(rows),
        "passed_samples": passed_samples,
        "candidate_contract_sha256": candidate.contract_sha256,
        "active_contract_sha256": active.contract_sha256 if active else None,
        "contract_changed": candidate.contract_sha256
        != (active.contract_sha256 if active else None),
        "candidate_template": candidate_template,
        "candidate_contract": candidate_contract,
        "active_contract": active_contract,
        "parser_field_contract": parser_field_contract,
        "parser_output_profile": parser_output_profile(parser_spec),
        "check_item_capabilities": check_item_capabilities,
        "capability_claim": (
            "Parser approval proves structured extraction only. Canonical check-item "
            "mapping requires an approved CheckItem / CommandAdapter V2, and production readiness "
            "requires a complete RuntimeBundle."
        ),
        "review_readiness": {
            "can_review": not review_blockers,
            "blockers": review_blockers,
        },
        "semantic_review": semantic_review if isinstance(semantic_review, dict) else None,
        "release_actor": {
            "username": current_actor,
            "role": current_user.role,
            "same_as_semantic_reviewer": same_as_semantic_reviewer,
            "admin_override_applies": admin_override_applies,
            "can_activate": not actor_blockers,
            "blockers": actor_blockers,
        },
        "activation_readiness": {
            "can_activate": not activation_blockers,
            "blockers": activation_blockers,
        },
        "approval_readiness": {
            "can_approve": not activation_blockers,
            "blockers": activation_blockers,
        },
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
    # This endpoint is restricted to the Parser control plane. Only the separately
    # sanitized sample is returned; CommandSnapshot.raw_text never crosses this boundary.
    item = ParserJobRepository().get_job(job_id, include_sanitized_payload=True)
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


@router.post("/jobs/{job_id}/manual-candidate")
def submit_manual_parser_candidate(
    job_id: str,
    request: Request,
    payload: ParserManualCandidateRequest,
):
    """Execute an engineer revision on frozen evidence and create an approval candidate."""

    try:
        result = ManualParserCandidateSubmissionService().submit(
            job_id=job_id,
            template_text=payload.template_text,
            actor=_authenticated_actor(request),
            actor_user_id=_authenticated_user(request).user_id,
            reason=payload.reason,
            based_on_template_id=payload.based_on_template_id,
        )
    except ManualParserCandidateValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc
    except ParserJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _refresh_rule_asset_projection_for_template(result.template_id)
    return result.to_dict()


@router.put("/jobs/{job_id}/spec")
def update_parser_spec(
    job_id: str,
    request: Request,
    payload: ParserSpecUpdateRequest,
):
    try:
        item = ParserJobRepository().set_parser_spec(
            job_id,
            parser_spec=payload.parser_spec,
            confirmed=payload.confirmed,
            actor=_authenticated_actor(request),
            reason=_required_reason(payload.reason, action="ParserSpec change"),
        )
    except ParserJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "job": item}


@router.post("/jobs/{job_id}/suggest-spec")
def suggest_parser_spec(
    job_id: str,
    request: Request,
    payload: ParserSpecSuggestionRequest,
):
    task_id = str(uuid.uuid4())
    repository = ParserJobRepository()
    revision_instruction = str(payload.revision_instruction or "").strip()
    check_item_id = str(payload.check_item_id or "").strip()
    try:
        # Association is an explicit catalog action, never a side effect of generation.
        repository.queue_parser_spec_suggestion(
            job_id,
            task_id=task_id,
            actor=_authenticated_actor(request),
            actor_user_id=_authenticated_user(request).user_id,
            reason=str(payload.reason or "Generate an engineer-reviewable ParserSpec draft"),
            revision_instruction=revision_instruction,
            check_item_id=check_item_id,
        )
        celery.send_task(
            PARSER_EXECUTION_TASK,
            kwargs={
                "job_id": job_id,
                "operation": "suggest_spec",
                "suggestion_task_id": task_id,
            },
            queue="netops.ai",
            task_id=task_id,
        )
    except ParserJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        try:
            repository.record_parser_spec_suggestion(
                job_id,
                task_id=task_id,
                status="failed",
                error=str(exc),
            )
        except ParserJobStateError:
            pass
        raise HTTPException(
            status_code=503,
            detail="ParserSpec suggestion dispatch failed",
        ) from exc
    return {"success": True, "job_id": job_id, "task_id": task_id, "status": "queued"}


@router.put("/jobs/{job_id}/samples/{sample_id}/role")
def update_parser_sample_role(
    job_id: str,
    sample_id: str,
    request: Request,
    payload: ParserSampleRoleUpdateRequest,
):
    try:
        item = ParserJobRepository().set_sample_role(
            job_id,
            sample_id,
            role=payload.role,
            expectation=payload.expectation,
            actor=_authenticated_actor(request),
            reason=_required_reason(payload.reason, action="Parser sample role change"),
        )
    except ParserJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "sample": item}


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, request: Request):
    try:
        ParserJobRepository().retry_job(job_id, actor=_authenticated_actor(request),
            actor_user_id=_authenticated_user(request).user_id, reason="operator requested Parser retry")
    except ParserJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "job_id": job_id, "status": "pending"}


@router.post("/jobs/{job_id}/retry-dispatch")
def retry_and_dispatch_job(job_id: str, request: Request):
    """Retry and dispatch a governed Parser job as one operator command."""

    repository = ParserJobRepository()
    item = repository.get_job(job_id)
    if not item:
        raise HTTPException(status_code=404, detail="parser job not found")
    readiness = item.get("generation_readiness") or {}
    if item.get("pipeline_version") == "simple_v2" and not readiness.get("ready"):
        blockers = ", ".join(str(value) for value in readiness.get("blockers") or [])
        raise HTTPException(
            status_code=409,
            detail=f"simple Parser job is not ready: {blockers or 'readiness_unknown'}",
        )
    try:
        repository.retry_job(
            job_id,
            actor=_authenticated_actor(request),
            actor_user_id=_authenticated_user(request).user_id,
            reason="operator requested Parser regeneration from the control plane",
        )
    except ParserJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    result = dispatch_ready_parser_jobs(job_id=job_id, limit=1, repository=repository)
    if not result["dispatched"]:
        current = repository.get_job(job_id) or {}
        if current.get("status") == "pending" and not current.get("celery_task_id"):
            repository.transition_job_status(
                job_id,
                "failed",
                "Parser regeneration could not be dispatched after retry",
                failure_code="redispatch_failed",
            )
        raise HTTPException(
            status_code=409,
            detail=(
                result["failed"][0]["error"]
                if result["failed"]
                else "parser job readiness changed before redispatch"
            ),
        )
    return {"success": True, **result}


@router.post("/jobs/{job_id}/recover")
def recover_parser_job(
    job_id: str,
    request: Request,
    payload: ParserJobRecoverRequest | None = None,
):
    """Fail a job stuck in pending/running/validating past the stale timeout."""
    try:
        item = ParserJobRepository().recover_stale_job(
            job_id,
            actor=_authenticated_actor(request),
            reason=str(
                (payload.reason if payload else None)
                or "operator recovered a stale parser job"
            ),
        )
    except ParserJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    stale_task_id = item.pop("recovered_celery_task_id", None)
    if stale_task_id:
        celery.control.revoke(stale_task_id, terminate=False)
    return {"success": True, "job_id": job_id, "status": item["status"], "job": item}


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
def dispatch_job(job_id: str, request: Request):
    item = ParserJobRepository().get_job(job_id)
    if not item:
        raise HTTPException(status_code=404, detail="parser job not found")
    readiness = item.get("generation_readiness") or {}
    if item.get("pipeline_version") == "simple_v2" and not readiness.get("ready"):
        blockers = ", ".join(str(value) for value in readiness.get("blockers") or [])
        raise HTTPException(
            status_code=409,
            detail=f"simple Parser job is not ready: {blockers or 'readiness_unknown'}",
        )
    result = dispatch_ready_parser_jobs(
        job_id=job_id,
        limit=1,
        actor=_authenticated_actor(request),
        actor_user_id=_authenticated_user(request).user_id,
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
def dispatch_jobs(request: Request):
    return {"success": True, **dispatch_ready_parser_jobs(limit=1,
        actor=_authenticated_actor(request), actor_user_id=_authenticated_user(request).user_id)}


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
    current_user = _authenticated_user(request)
    try:
        item = ParserTemplateRepository().activate_template(
            template_id,
            approved_by=str(current_user.username or current_user.user_id).strip(),
            reason=str(payload.reason or "").strip(),
            allow_same_reviewer=True,
        )
    except ParserTemplateStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _refresh_rule_asset_projection_for_template(template_id)
    return item.to_dict()


@router.post("/templates/{template_id}/semantic-review")
def review_template_semantics(
    template_id: str,
    request: Request,
    payload: ParserTemplateSemanticReviewRequest,
):
    try:
        review = ParserTemplateRepository().record_semantic_review(
            template_id,
            actor=_authenticated_actor(request),
            reason=str(payload.reason or "").strip(),
            result_confirmed=payload.result_confirmed,
            primary_key_confirmed=payload.primary_key_confirmed,
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "success": True,
        "template_id": template_id,
        "template_status": "pending_approval",
        "semantic_review": review,
    }


@router.post(
    "/templates/{template_id}/force-activate",
    dependencies=[Depends(app_user)],
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
    except ParserTemplateStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _refresh_rule_asset_projection_for_template(template_id)
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
    _refresh_rule_asset_projection_for_template(template_id)
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
            reason=str(payload.reason or "").strip(),
            analyze_changes=payload.analyze_changes,
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
    _refresh_rule_asset_projection_for_template(template_id)
    return {"success": True, "template_id": template_id, "status": "rejected"}


@router.post("/templates/{template_id}/deprecate")
def deprecate_template(template_id: str, request: Request, payload: ParserTemplateStatusRequest):
    ParserTemplateRepository().set_template_status(
        template_id,
        "deprecated",
        actor=_authenticated_actor(request),
        reason=_required_reason(payload.reason, action="template deprecation"),
    )
    _refresh_rule_asset_projection_for_template(template_id)
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
        from src.apps.rule_governance.backend.parser.production_impact import (
            effective_plan_coverage_summary,
        )

        payload["effective_plan_coverage"] = effective_plan_coverage_summary()
    return payload


@router.get("/dialect-audit")
def textfsm_dialect_audit(status: str = "active"):
    return TextFSMDialectAuditService().audit(status=status)


def _project_asset_gap(item: dict[str, Any]) -> dict[str, Any]:
    params = {
        "workspace": "production",
        "subtab": "source-jobs",
        "action": "asset-gap-source",
        "asset_gap_id": str(item.get("id") or ""),
        "check_item_id": str(item.get("check_item_id") or ""),
        "implementation_version_id": str(
            item.get("implementation_version_id") or ""
        ),
        "command": str(item.get("canonical_command") or ""),
    }
    target_url = "/skills/parser?" + urlencode(
        {key: value for key, value in params.items() if value}
    )
    return {
        **item,
        "recommended_action": {
            "code": "import_rule_samples",
            "label": "导入规则样本",
            "description": (
                "该缺口只提供治理上下文，不会直接创建 Parser 或规则生成任务。"
                "请导入并确认离线原始输出后再启动资产生产。"
            ),
            "target_url": target_url,
        },
    }


@router.get("/asset-gaps")
def list_asset_gaps(
    status: str | None = "open",
    check_item_id: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    page = AssetGapRepository().list_page(
        status=status,
        check_item_id=check_item_id,
        search=search,
        limit=limit,
        offset=offset,
    )
    page["items"] = [
        _project_asset_gap(dict(item)) for item in page.get("items") or []
    ]
    return page


@router.get("/asset-gaps/{gap_id}")
def get_asset_gap(gap_id: str):
    item = AssetGapRepository().get(gap_id)
    if item is None:
        raise HTTPException(status_code=404, detail="asset gap not found")
    return _project_asset_gap(item)


@router.post("/asset-gaps/{gap_id}/resolve")
def resolve_asset_gap(
    gap_id: str,
    request: Request,
    payload: CommandProfileLifecycleRequest,
):
    try:
        return {
            "success": True,
            "item": _project_asset_gap(
                AssetGapRepository().resolve(
                    gap_id,
                    actor=_authenticated_actor(request),
                    note=_required_reason(
                        payload.reason,
                        action="asset gap resolution",
                    ),
                )
            ),
        }
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/rule-gaps/from-changes")
def rule_gaps_from_changes(days: int = 90, limit: int = 200):
    from src.apps.rule_governance.backend.rule_control.change_gap_analysis import (
        ChangeGapAnalysisService,
    )

    return ChangeGapAnalysisService().analyze(days=days, limit=limit)


@router.post("/rule-gaps/seed-jobs")
def seed_rule_gaps_from_changes(
    http_request: Request,
    days: int = 90,
    limit: int = 20,
):
    del http_request, days, limit
    raise HTTPException(
        status_code=409,
        detail=(
            "生产 Change 缺口只用于定位证据，不能直接创建规则生成任务；"
            "请先在 Parser 控制面导入明确的离线规则样本。"
        ),
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
        current_user = _authenticated_user(request)
        return RuleAssetEngineeringRevisionService().review(
            bundle_id,
            decision=payload.decision,
            actor=str(current_user.username or current_user.user_id).strip(),
            reason=payload.reason,
            allow_same_actor=True,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc












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




@router.get("/catalog/adapter-projections/cutover-readiness")
def get_adapter_projection_cutover_readiness(
    _user: CurrentUser = Depends(app_user),
):
    """Read-only hard-gate report for the all-at-once V2 cutover."""

    return AdapterProjectionCutoverInventoryService().readiness()


@router.post("/catalog/adapter-projections/candidates")
def generate_adapter_projection_candidate(
    payload: AdapterProjectionCandidateRequest,
    _user: CurrentUser = Depends(app_user),
):
    """Return a bounded V2 candidate; it is never published automatically."""

    try:
        context = _adapter_projection_context(
            check_item_id=payload.check_item_id,
            parser_template_id=payload.parser_template_id,
        )
        record_sets = dict(context["parser"]["record_sets"])
        source_fields = record_sets.get(payload.source_record_set)
        if source_fields is None:
            raise ValueError(
                "selected Parser has no record set: "
                f"{payload.source_record_set}"
            )
        return generate_projection_candidate(
            {
                "mode": "projection_candidate",
                "selected_check_item": context["selected_check_item"],
                "parser": {
                    "template_id": payload.parser_template_id,
                    "source_record_set": payload.source_record_set,
                    "source_fields": source_fields,
                },
                "proposal": payload.proposal,
                "generation_provider": payload.generation_provider,
                "max_agent_attempts": payload.max_agent_attempts,
            }
        )
    except ValueError as exc:
        _raise_adapter_projection_error(exc)


@router.post("/catalog/adapter-projections/validate")
def validate_adapter_projection_candidate(
    payload: AdapterProjectionValidationRequest,
    _user: CurrentUser = Depends(app_user),
):
    """Validate an edited V2 Projection against server-side immutable assets."""

    try:
        context = _adapter_projection_context(
            check_item_id=payload.check_item_id,
            parser_template_id=payload.parser_template_id,
        )
        record_sets = dict(context["parser"]["record_sets"])
        source_fields = record_sets.get(payload.source_record_set)
        if source_fields is None:
            raise ValueError(
                "selected Parser has no record set: "
                f"{payload.source_record_set}"
            )
        return generate_projection_candidate(
            {
                "mode": "projection_validate",
                "selected_check_item": context["selected_check_item"],
                "parser": {
                    "template_id": payload.parser_template_id,
                    "source_record_set": payload.source_record_set,
                    "source_fields": source_fields,
                },
                "proposal": payload.proposal,
            }
        )
    except ValueError as exc:
        _raise_adapter_projection_error(exc)


@router.post("/catalog/adapter-projections/cutover-readiness")
def verify_adapter_projection_cutover_readiness(
    payload: AdapterProjectionCutoverReadinessRequest,
    _user: CurrentUser = Depends(app_user),
):
    """Evaluate a complete replay attestation without persisting a mapping asset."""

    return AdapterProjectionCutoverInventoryService().readiness(
        replay_by_implementation_version={
            implementation_version_id: attestation.model_dump()
            for implementation_version_id, attestation in (
                payload.replay_by_implementation_version.items()
            )
        }
    )


@router.post("/catalog/adapter-projections/replay-attestations")
def record_adapter_projection_replay_attestations(
    payload: AdapterProjectionCutoverReadinessRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    """Persist sanitized all-route replay proof in the existing audit chain."""

    try:
        return AdapterProjectionCutoverInventoryService().record_replay_attestations(
            replay_by_implementation_version={
                implementation_version_id: attestation.model_dump()
                for implementation_version_id, attestation in (
                    payload.replay_by_implementation_version.items()
                )
            },
            actor=_authenticated_actor(request),
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_adapter_projection_error(exc)


@router.post(
    "/catalog/adapter-projections/cutover-authorization",
    dependencies=[Depends(app_user)],
)
def authorize_adapter_projection_cutover(
    payload: AdapterProjectionCutoverAuthorizationRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    """Record the final human authorization after every route passes."""

    try:
        return AdapterProjectionCutoverInventoryService().authorize_cutover(
            actor=_authenticated_actor(request),
            reason=payload.reason,
            expected_readiness_digest=payload.expected_readiness_digest,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_adapter_projection_error(exc)


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






@router.get("/assessment-policies")
def list_assessment_policy_profiles(
    page: int = 1,
    page_size: int = 25,
    search: str | None = None,
    check_item_id: str | None = None,
    observation_type: str | None = None,
    policy_role: str | None = None,
    status: str | None = None,
    _user: CurrentUser = Depends(app_user),
):
    try:
        return AssessmentPolicyAssetService().list_profiles(
            page=page,
            page_size=page_size,
            search=search,
            check_item_id=check_item_id,
            observation_type=observation_type,
            policy_role=policy_role,
            status=status,
        )
    except ValueError as exc:
        _raise_assessment_policy_error(exc)


@router.post("/assessment-policies/versions")
def create_assessment_policy_version(
    payload: AssessmentPolicyVersionCreateRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return AssessmentPolicyAssetService().create_version(
            check_item_id=payload.check_item_id,
            check_item_version_id=payload.check_item_version_id,
            observation_type=payload.observation_type,
            policy_role=payload.policy_role,
            name=payload.name,
            description=payload.description,
            profile_id=payload.profile_id,
            policy=payload.policy,
            evidence_refs=payload.evidence_refs,
            provenance=payload.provenance,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            created_by=_authenticated_actor(request),
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_assessment_policy_error(exc)


@router.get("/assessment-policies/versions/{version_id}")
def get_assessment_policy_version(
    version_id: str,
    _user: CurrentUser = Depends(app_user),
):
    item = AssessmentPolicyAssetService().get_version(version_id)
    if item is None:
        raise HTTPException(status_code=404, detail="assessment policy version not found")
    return item


@router.get("/assessment-policies/profiles/{profile_id}/versions")
def list_assessment_policy_profile_versions(
    profile_id: str,
    page: int = 1,
    page_size: int = 25,
    status: str | None = None,
    _user: CurrentUser = Depends(app_user),
):
    try:
        return AssessmentPolicyAssetService().list_profile_versions(
            profile_id,
            page=page,
            page_size=page_size,
            status=status,
        )
    except ValueError as exc:
        _raise_assessment_policy_error(exc)


@router.post("/assessment-policies/versions/{version_id}/submit")
def submit_assessment_policy_version(
    version_id: str,
    payload: AssessmentPolicyLifecycleRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return AssessmentPolicyAssetService().submit(
            version_id,
            actor=_authenticated_actor(request),
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_assessment_policy_error(exc)


@router.post(
    "/assessment-policies/versions/{version_id}/semantic-review",
    dependencies=[Depends(app_user)],
)
def review_assessment_policy_semantics(
    version_id: str,
    payload: AssessmentPolicyReviewRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return AssessmentPolicyAssetService().semantic_review(
            version_id,
            decision=payload.decision,
            actor=_authenticated_actor(request),
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_assessment_policy_error(exc)


@router.post(
    "/assessment-policies/versions/{version_id}/publication-review",
    dependencies=[Depends(app_user)],
)
def review_assessment_policy_publication(
    version_id: str,
    payload: AssessmentPolicyReviewRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return AssessmentPolicyAssetService().publication_review(
            version_id,
            decision=payload.decision,
            actor=_authenticated_actor(request),
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_assessment_policy_error(exc)


@router.post(
    "/assessment-policies/versions/{version_id}/revoke",
    dependencies=[Depends(app_user)],
)
def revoke_assessment_policy_version(
    version_id: str,
    payload: AssessmentPolicyLifecycleRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return AssessmentPolicyAssetService().revoke(
            version_id,
            actor=_authenticated_actor(request),
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_assessment_policy_error(exc)


@router.post(
    "/assessment-policies/versions/{version_id}/bindings",
    dependencies=[Depends(app_user)],
)
def bind_assessment_policy_version(
    version_id: str,
    payload: AssessmentPolicyBindingRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return AssessmentPolicyAssetService().bind(
            version_id,
            binding_type=payload.binding_type,
            scope=payload.scope,
            actor=_authenticated_actor(request),
            reason=payload.reason,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            replaces_binding_id=payload.replaces_binding_id,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_assessment_policy_error(exc)


@router.post(
    "/assessment-policy-bindings/{binding_id}/end",
    dependencies=[Depends(app_user)],
)
def end_assessment_policy_binding(
    binding_id: str,
    payload: AssessmentPolicyLifecycleRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return AssessmentPolicyAssetService().end_binding(
            binding_id,
            actor=_authenticated_actor(request),
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_assessment_policy_error(exc)


@router.post("/assessment-policies/resolution-preview")
def preview_assessment_policy_resolution(
    payload: AssessmentPolicyResolutionPreviewRequest,
    _user: CurrentUser = Depends(app_user),
):
    try:
        return AssessmentPolicyAssetService().preview_resolution(
            check_item_version_id=payload.check_item_version_id,
            device=payload.device,
            observation=payload.observation,
            rule=payload.rule,
            policy_effective_at=payload.policy_effective_at,
            include_legacy=payload.include_legacy,
            runtime_bundle_version_id=payload.runtime_bundle_version_id,
            runtime_bundle_digest=payload.runtime_bundle_digest,
        )
    except ValueError as exc:
        _raise_assessment_policy_error(exc)


@router.post("/assessment-policies/versions/{version_id}/impact-preview")
def preview_assessment_policy_target_impact(
    version_id: str,
    payload: AssessmentPolicyImpactPreviewRequest,
    _user: CurrentUser = Depends(app_user),
):
    try:
        return AssessmentPolicyAssetService().preview_target_impact(
            version_id,
            binding_type=payload.binding_type,
            proposed_scope=payload.proposed_scope,
            target_selector=payload.target_selector,
            policy_effective_at=payload.policy_effective_at,
            page=payload.page,
            page_size=payload.page_size,
        )
    except ValueError as exc:
        _raise_assessment_policy_error(exc)


@router.post(
    "/assessment-policies/migration/inventory",
    dependencies=[Depends(app_user)],
)
def inventory_legacy_assessment_policies(
    payload: AssessmentPolicyMigrationInventoryRequest,
    request: Request,
    _idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return AssessmentPolicyMigrationService().inventory_legacy_sources(
            actor=_authenticated_actor(request),
            check_item_ids=payload.check_item_ids,
            include_execution_snapshots=payload.include_execution_snapshots,
            after_execution_id=payload.after_execution_id,
            execution_page_size=payload.execution_page_size,
        )
    except ValueError as exc:
        _raise_assessment_policy_error(exc)


@router.get("/assessment-policies/migration/sources")
def list_legacy_assessment_policy_sources(
    page: int = 1,
    page_size: int = 25,
    search: str | None = None,
    conversion_status: str | None = None,
    source_type: str | None = None,
    check_item_id: str | None = None,
    _user: CurrentUser = Depends(app_user),
):
    try:
        return AssessmentPolicyMigrationService().list_sources(
            page=page,
            page_size=page_size,
            search=search,
            conversion_status=conversion_status,
            source_type=source_type,
            check_item_id=check_item_id,
        )
    except ValueError as exc:
        _raise_assessment_policy_error(exc)


@router.post("/assessment-policies/migration/sources/{source_id}/draft")
def draft_legacy_assessment_policy_source(
    source_id: str,
    request: Request,
    _idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return AssessmentPolicyMigrationService().create_draft_from_source(
            source_id,
            actor=_authenticated_actor(request),
        )
    except ValueError as exc:
        _raise_assessment_policy_error(exc)


@router.post("/assessment-policies/migration/shadow-compare")
def compare_assessment_policy_shadow(
    payload: AssessmentPolicyShadowCompareRequest,
    request: Request,
    _idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return AssessmentPolicyMigrationService().compare_shadow(
            check_item_version_id=payload.check_item_version_id,
            device=payload.device,
            observation=payload.observation,
            rule=payload.rule,
            actor=_authenticated_actor(request),
            policy_effective_at=payload.policy_effective_at,
            runtime_bundle_version_id=payload.runtime_bundle_version_id,
            runtime_bundle_digest=payload.runtime_bundle_digest,
        )
    except ValueError as exc:
        _raise_assessment_policy_error(exc)


@router.get("/assessment-policies/migration/shadow-results")
def list_assessment_policy_shadow_results(
    page: int = 1,
    page_size: int = 25,
    comparison_status: str | None = None,
    check_item_id: str | None = None,
    device_id: str | None = None,
    _user: CurrentUser = Depends(app_user),
):
    try:
        return AssessmentPolicyMigrationService().list_shadow_results(
            page=page,
            page_size=page_size,
            comparison_status=comparison_status,
            check_item_id=check_item_id,
            device_id=device_id,
        )
    except ValueError as exc:
        _raise_assessment_policy_error(exc)


@router.post(
    "/assessment-policies/migration/cutovers",
    dependencies=[Depends(app_user)],
)
def activate_assessment_policy_cutover(
    payload: AssessmentPolicyCutoverRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return AssessmentPolicyMigrationService().activate_cutover(
            check_item_version_id=payload.check_item_version_id,
            device_id=payload.device_id,
            shadow_result_ids=payload.shadow_result_ids,
            actor=_authenticated_actor(request),
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_assessment_policy_error(exc)


@router.post(
    "/assessment-policies/migration/cutovers/{cutover_id}/rollback",
    dependencies=[Depends(app_user)],
)
def rollback_assessment_policy_cutover(
    cutover_id: str,
    payload: AssessmentPolicyCutoverRollbackRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return AssessmentPolicyMigrationService().rollback_cutover(
            cutover_id,
            actor=_authenticated_actor(request),
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_assessment_policy_error(exc)


@router.get("/assessment-policies/migration/cutovers")
def list_assessment_policy_cutovers(
    page: int = 1,
    page_size: int = 25,
    status: str | None = None,
    resolver_mode: str | None = None,
    check_item_id: str | None = None,
    device_id: str | None = None,
    _user: CurrentUser = Depends(app_user),
):
    try:
        return AssessmentPolicyMigrationService().list_cutovers(
            page=page,
            page_size=page_size,
            status=status,
            resolver_mode=resolver_mode,
            check_item_id=check_item_id,
            device_id=device_id,
        )
    except ValueError as exc:
        _raise_assessment_policy_error(exc)


@router.get("/check-item-runtime-bundles/health")
def get_check_item_runtime_bundle_health(
    page: int = 1,
    page_size: int = 25,
    search: str | None = None,
    status: str | None = None,
    _user: CurrentUser = Depends(app_user),
):
    try:
        return CheckItemRuntimeBundleService().catalog_health(
            page=page,
            page_size=page_size,
            search=search,
            status=status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/check-item-runtime-bundles/assembly-preview")
def preview_check_item_runtime_bundle_assembly(
    payload: RuntimeBundleAssemblyRequest,
    _user: CurrentUser = Depends(app_user),
):
    try:
        return CheckItemRuntimeBundleService().preview_assembly(
            check_item_version_id=payload.check_item_version_id,
            implementation_version_id=payload.implementation_version_id,
            parser_template_ids=payload.parser_template_ids,
            change_policy_version_ids=payload.change_policy_version_ids,
            event_rule_version_ids=payload.event_rule_version_ids,
            comparison_profile_version_id=payload.comparison_profile_version_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/check-item-runtime-bundles/assemble")
def assemble_check_item_runtime_bundle(
    payload: RuntimeBundleAssembleRequest,
    request: Request,
    _idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return CheckItemRuntimeBundleService().assemble(
            check_item_version_id=payload.check_item_version_id,
            implementation_version_id=payload.implementation_version_id,
            preview_digest=payload.preview_digest,
            actor=_authenticated_actor(request),
            parser_template_ids=payload.parser_template_ids,
            change_policy_version_ids=payload.change_policy_version_ids,
            event_rule_version_ids=payload.event_rule_version_ids,
            comparison_profile_version_id=payload.comparison_profile_version_id,
        )
    except RuntimeBundleValidationError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "运行包装配被阻断",
                "blockers": list(exc.blockers),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/check-item-runtime-bundles/{version_id}")
def get_check_item_runtime_bundle(
    version_id: str,
    _user: CurrentUser = Depends(app_user),
):
    try:
        item = CheckItemRuntimeBundleService().get_version(version_id)
    except RuntimeBundleValidationError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "运行包依赖已失效",
                "blockers": list(exc.blockers),
            },
        ) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="runtime bundle version not found")
    return item


@router.post(
    "/check-item-runtime-bundles/{version_id}/activate",
    dependencies=[Depends(app_user)],
)
def activate_check_item_runtime_bundle(
    version_id: str,
    payload: RuntimeBundleLifecycleRequest,
    request: Request,
    _idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    _authenticated_user(request)
    try:
        return CheckItemRuntimeBundleService().activate(
            version_id,
            actor=_authenticated_actor(request),
            reason=payload.reason.strip(),
            allow_same_actor=True,
        )
    except RuntimeBundleValidationError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "运行包发布被阻断",
                "blockers": list(exc.blockers),
            },
        ) from exc
    except ValueError as exc:
        status_code = 404 if "not found" in str(exc).casefold() else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.post(
    "/check-item-runtime-bundles/{version_id}/rollback",
    dependencies=[Depends(app_user)],
)
def rollback_check_item_runtime_bundle(
    version_id: str,
    payload: RuntimeBundleLifecycleRequest,
    request: Request,
    _idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return CheckItemRuntimeBundleService().rollback(
            version_id,
            actor=_authenticated_actor(request),
            reason=_required_reason(
                payload.reason,
                action="runtime bundle rollback",
            ),
        )
    except RuntimeBundleValidationError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "运行包回滚被阻断",
                "blockers": list(exc.blockers),
            },
        ) from exc
    except ValueError as exc:
        status_code = 404 if "not found" in str(exc).casefold() else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get("/check-item-rollout-campaigns")
def list_check_item_rollout_campaigns(
    page: int = 1,
    page_size: int = 25,
    status: str | None = None,
    search: str | None = None,
    _user: CurrentUser = Depends(app_user),
):
    return RolloutCampaignService().list(
        page=page,
        page_size=page_size,
        status=status,
        search=search,
    )


@router.get("/check-item-rollout-legacy-routes")
def list_check_item_rollout_legacy_routes(
    page: int = 1,
    page_size: int = 25,
    status: Literal["active", "deprecated"] | None = None,
    _user: CurrentUser = Depends(app_user),
):
    return RolloutLegacyRouteService().list(
        page=page,
        page_size=page_size,
        status=status,
    )


@router.post(
    "/check-item-rollout-legacy-routes",
    status_code=201,
    dependencies=[Depends(app_user)],
)
def create_check_item_rollout_legacy_route(
    payload: RolloutLegacyRouteCreateRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return RolloutLegacyRouteService().create_runtime_bundle_route(
            route_name=payload.route_name,
            runtime_bundle_version_id=payload.runtime_bundle_version_id,
            actor=_authenticated_actor(request),
            idempotency_key=idempotency_key,
        )
    except LegacyRouteError as exc:
        raise HTTPException(
            status_code=(
                404 if exc.code.endswith("_missing") else 409
            ),
            detail={
                "message": "旧链路基线适配器登记被阻断",
                "code": exc.code,
                "blockers": [str(exc)],
            },
        ) from exc


@router.post(
    "/check-item-rollout-legacy-routes/{route_id}/deprecate",
    dependencies=[Depends(app_user)],
)
def deprecate_check_item_rollout_legacy_route(
    route_id: str,
    payload: RolloutLegacyRouteLifecycleRequest,
    request: Request,
):
    try:
        return RolloutLegacyRouteService().deprecate(
            route_id,
            actor=_authenticated_actor(request),
            reason=_required_reason(
                payload.reason,
                action="legacy rollout route deprecation",
            ),
        )
    except LegacyRouteError as exc:
        raise HTTPException(
            status_code=(
                404 if exc.code.endswith("_missing") else 409
            ),
            detail={
                "message": "旧链路基线适配器停用被阻断",
                "code": exc.code,
                "blockers": [str(exc)],
            },
        ) from exc


@router.post("/check-item-rollout-campaigns")
def create_check_item_rollout_campaign(
    payload: RolloutCampaignCreateRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return RolloutCampaignService().create(
            name=payload.name,
            description=payload.description,
            actor=_authenticated_actor(request),
            idempotency_key=idempotency_key,
        )
    except RolloutValidationError as exc:
        _raise_rollout_error(exc)


@router.get("/check-item-rollout-campaigns/{campaign_id}")
def get_check_item_rollout_campaign(
    campaign_id: str,
    _user: CurrentUser = Depends(app_user),
):
    item = RolloutCampaignService().get(campaign_id)
    if item is None:
        raise HTTPException(status_code=404, detail="rollout campaign not found")
    return item


@router.post("/check-item-rollout-campaigns/{campaign_id}/freeze")
def freeze_check_item_rollout_campaign(
    campaign_id: str,
    payload: RolloutCampaignFreezeRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return RolloutCampaignService().freeze_target(
            campaign_id,
            target_manifest=payload.target_manifest,
            runtime_bundle_refs=payload.runtime_bundle_refs,
            comparison_context_ids=payload.comparison_context_ids,
            baseline_window=payload.baseline_window,
            thresholds=payload.thresholds,
            rollback_plan=payload.rollback_plan,
            actor=_authenticated_actor(request),
            reason=_required_reason(
                payload.reason,
                action="rollout target freeze",
            ),
            idempotency_key=idempotency_key,
        )
    except RolloutValidationError as exc:
        _raise_rollout_error(exc)


@router.post(
    "/check-item-rollout-campaigns/{campaign_id}/start",
    status_code=202,
)
def start_check_item_rollout_stage(
    campaign_id: str,
    payload: RolloutCampaignLifecycleRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return RolloutCampaignService().start_stage(
            campaign_id,
            actor=_authenticated_actor(request),
            actor_user_id=_authenticated_user(request).user_id,
            reason=_required_reason(
                payload.reason,
                action="rollout stage start",
            ),
            idempotency_key=idempotency_key,
            execution_mode=payload.execution_mode,
        )
    except RolloutValidationError as exc:
        _raise_rollout_error(exc)


@router.get("/check-item-rollout-campaigns/{campaign_id}/executions")
def list_check_item_rollout_stage_executions(
    campaign_id: str,
    _user: CurrentUser = Depends(app_user),
):
    if RolloutCampaignService().get(campaign_id) is None:
        raise HTTPException(status_code=404, detail="rollout campaign not found")
    return {
        "items": RolloutStageExecutionService().list_for_campaign(
            campaign_id
        )
    }


@router.get(
    "/check-item-rollout-campaigns/{campaign_id}/executions/{execution_id}"
)
def get_check_item_rollout_stage_execution(
    campaign_id: str,
    execution_id: str,
    _user: CurrentUser = Depends(app_user),
):
    item = RolloutStageExecutionService().get(execution_id)
    if item is None or str(item.get("campaign_id") or "") != campaign_id:
        raise HTTPException(
            status_code=404,
            detail="rollout stage execution not found",
        )
    return item


@router.post(
    "/check-item-rollout-campaigns/{campaign_id}/approve",
    dependencies=[Depends(app_user)],
)
def approve_check_item_rollout_stage(
    campaign_id: str,
    payload: RolloutCampaignApprovalRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return RolloutCampaignService().approve_stage(
            campaign_id,
            actor=_authenticated_actor(request),
            comment=_required_reason(
                payload.comment,
                action="rollout stage approval",
            ),
            idempotency_key=idempotency_key,
        )
    except RolloutValidationError as exc:
        _raise_rollout_error(exc)


@router.post("/check-item-rollout-campaigns/{campaign_id}/pause")
def pause_check_item_rollout_campaign(
    campaign_id: str,
    payload: RolloutCampaignLifecycleRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return RolloutCampaignService().pause(
            campaign_id,
            actor=_authenticated_actor(request),
            reason=_required_reason(
                payload.reason,
                action="rollout campaign pause",
            ),
            idempotency_key=idempotency_key,
        )
    except RolloutValidationError as exc:
        _raise_rollout_error(exc)


@router.post("/check-item-rollout-campaigns/{campaign_id}/resume")
def resume_check_item_rollout_campaign(
    campaign_id: str,
    payload: RolloutCampaignResumeRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return RolloutCampaignService().resume(
            campaign_id,
            observed_digests=payload.observed_digests,
            actor=_authenticated_actor(request),
            reason=_required_reason(
                payload.reason,
                action="rollout campaign resume",
            ),
            idempotency_key=idempotency_key,
        )
    except RolloutValidationError as exc:
        _raise_rollout_error(exc)


@router.post(
    "/check-item-rollout-campaigns/{campaign_id}/rollback",
    dependencies=[Depends(app_user)],
)
def rollback_check_item_rollout_campaign(
    campaign_id: str,
    payload: RolloutCampaignRollbackRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
):
    try:
        return RolloutCampaignService().rollback(
            campaign_id,
            evidence_refs=payload.evidence_refs,
            actor=_authenticated_actor(request),
            reason=_required_reason(
                payload.reason,
                action="rollout campaign rollback",
            ),
            idempotency_key=idempotency_key,
        )
    except RolloutValidationError as exc:
        _raise_rollout_error(exc)


@router.post(
    "/check-item-rollout-campaigns/{campaign_id}/routing-decision"
)
def resolve_check_item_rollout_routing(
    campaign_id: str,
    payload: RolloutRoutingDecisionRequest,
    _user: CurrentUser = Depends(app_user),
):
    try:
        return RolloutCampaignService().routing_decision(
            campaign_id,
            device_id=payload.device_id,
            check_item_version_id=payload.check_item_version_id,
            target_manifest_digest=payload.target_manifest_digest,
            runtime_bundle_digest=payload.runtime_bundle_digest,
            comparison_context_digest=payload.comparison_context_digest,
        )
    except RolloutValidationError as exc:
        _raise_rollout_error(exc)


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
    dependencies=[Depends(app_user)],
)
def approve_rule_generation_job(
    job_id: str,
    request: Request,
    payload: RuleGenerationJobApprovalRequest | None = None,
):
    reason = str(payload.reason or "").strip() if payload else ""
    try:
        current_user = _authenticated_user(request)
        return RuleAssetApprovalService().approve(
            job_id,
            actor=str(current_user.username or current_user.user_id).strip(),
            reason=reason,
            allow_same_actor=True,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuleGenerationJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc














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


def _enrich_command_profile_check_items(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    version_ids = [
        str(item["profile_version_id"])
        for item in rows
        if item.get("profile_version_id")
    ]
    selections = PatrolProfileCheckItemRepository().list_selections(
        version_ids,
        active_only=False,
    )
    enriched: list[dict[str, Any]] = []
    for item in rows:
        version_id = str(item.get("profile_version_id") or "")
        selected = list(selections.get(version_id, []))
        enriched.append(
            {
                **item,
                "profile_mode": (
                    "check_items" if selected else "legacy_commands"
                ),
                "check_item_ids": [
                    str(row["check_item_id"]) for row in selected
                ],
                "check_items": selected,
            }
        )
    return enriched


@router.get("/catalog/canonical-observations")
def list_canonical_observations():
    catalog = load_canonical_catalog()
    return {
        "catalog_id": catalog.catalog_id,
        "version": catalog.version,
        "status": catalog.status,
        "digest": catalog.digest,
        "items": [
            {
                "id": item.id,
                "description": item.description,
                "semantic_type": item.semantic_type,
                "value_types": list(item.value_types),
                "canonical_unit": item.canonical_unit,
                "dimensions": list(item.dimensions),
                "allowed_dimension_values": {
                    key: list(values)
                    for key, values in item.allowed_dimension_values.items()
                },
                "allowed_values": list(item.allowed_values),
                "state_groups": {
                    key: list(values)
                    for key, values in item.state_groups.items()
                },
                "assessment_roles": list(item.assessment_roles),
            }
            for item in catalog.observations
        ],
    }


@router.get("/catalog/changes")
def list_check_item_catalog_changes(
    status: str | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    try:
        return CheckItemCatalogGovernanceService().list_changes(
            status=status,
            search=search,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        _raise_catalog_governance_error(exc)


@router.get("/catalog/changes/{change_id}")
def get_check_item_catalog_change(change_id: str):
    try:
        return CheckItemCatalogGovernanceService().get_change(change_id)
    except ValueError as exc:
        _raise_catalog_governance_error(exc)


@router.post("/catalog/check-items/drafts")
def create_check_item_draft(
    request: Request,
    payload: CheckItemDraftRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    try:
        body = payload.model_dump(exclude={"reason"})
        return CheckItemCatalogGovernanceService().create_check_item_draft(
            body,
            actor=_authenticated_actor(request),
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_catalog_governance_error(exc)


@router.post("/catalog/check-items/rule-preview")
def preview_check_item_draft_rules(payload: CheckItemRulePreviewRequest):
    """Explain what an unsaved check-item draft will judge; stores nothing."""

    from src.apps.rule_governance.backend.check_items.rule_preview import (
        preview_check_item_rules,
    )

    preview = preview_check_item_rules(
        check_item_id=payload.id,
        result_mode=payload.result_mode,
        canonical_contract=payload.canonical_contract,
    )
    if preview["valid"] and payload.result_mode == "structured":
        preview["graph"] = _draft_relations(payload, str(preview["assessment_mode"]))
    return preview


def _draft_relations(payload: "CheckItemRulePreviewRequest", assessment_mode: str) -> dict[str, Any]:
    """The draft as field -> judgment -> outcomes, set against the published version when there is one."""

    from src.apps.rule_governance.backend.check_items.relations import compile_relations, diff_relations
    from src.core.check_items.canonical import freeze_rule_semantics
    from src.infrastructure.db.models import CheckItemVersionRecord
    from src.infrastructure.db.postgres import get_db_session

    draft = compile_relations(
        check_item_id=payload.id or "draft",
        result_mode=payload.result_mode,
        assessment_mode=assessment_mode,
        canonical_contract=freeze_rule_semantics(dict(payload.canonical_contract or {})),
    )
    published = None
    if payload.id:
        with get_db_session() as db:
            version = (
                db.query(CheckItemVersionRecord)
                .filter(
                    CheckItemVersionRecord.check_item_id == payload.id,
                    CheckItemVersionRecord.status == "active",
                )
                .first()
            )
            if version is not None and str(version.result_mode) == "structured":
                published = compile_relations(
                    check_item_id=payload.id,
                    result_mode=str(version.result_mode),
                    assessment_mode=str(version.assessment_mode),
                    canonical_contract=dict(version.canonical_contract_json or {}),
                )
    if published is None:
        return {**draft, "mode": "draft"}
    return diff_relations(published, draft)


@router.get("/catalog/rule-generation-guide")
def get_rule_generation_guide():
    """How a field's settings become change rules and event rules."""

    from src.apps.rule_governance.backend.check_items.rule_guide import rule_guide

    return rule_guide()


@router.get("/catalog/check-items/{check_item_id}/active-rules")
def get_check_item_rules_in_force(check_item_id: str):
    """The rules the patrol applies today to the published version of a check item."""

    from src.apps.rule_governance.backend.check_items.rule_preview import rules_in_force

    try:
        return rules_in_force(check_item_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/catalog/check-items/{check_item_id}/overview")
def get_check_item_overview(check_item_id: str):
    """One check item with the rules it produces and the adapters that run it."""

    from src.apps.rule_governance.backend.check_items.patrol_overview import check_item_overview

    try:
        return check_item_overview(check_item_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class AdapterFieldsRequest(BaseModel):
    check_item_id: str = Field(min_length=1, max_length=128)
    fields: list[str] = Field(default_factory=list, max_length=64)


def _adapter_fields(call):
    """Run one adapter field operation; what cannot be done is said in the engineer's words."""

    from src.apps.rule_governance.backend.check_items.adapter_fields import AdapterFieldsError

    try:
        return call()
    except AdapterFieldsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/catalog/adapters/{implementation_id}/fields")
def get_adapter_fields(implementation_id: str, check_item_id: str):
    """The fields an adapter does not provide yet, and the last trial of adding some."""

    from src.apps.rule_governance.backend.check_items.adapter_field_service import AdapterFieldService

    return _adapter_fields(lambda: AdapterFieldService().get(implementation_id, check_item_id))


@router.post("/catalog/adapters/{implementation_id}/fields/trial", dependencies=[Depends(app_user)])
def try_adapter_fields(implementation_id: str, payload: AdapterFieldsRequest, request: Request):
    """Start a trial in the background. It saves nothing."""

    from src.apps.rule_governance.backend.check_items.adapter_field_service import AdapterFieldService

    user = _authenticated_user(request)
    return _adapter_fields(lambda: AdapterFieldService().start(
        implementation_id,
        payload.check_item_id,
        fields=payload.fields or None,
        actor=_authenticated_actor(request),
        actor_user_id=str(user.user_id or "") or None,
    ))


@router.post("/catalog/adapters/{implementation_id}/fields/save", dependencies=[Depends(app_user)])
def save_adapter_fields(implementation_id: str, payload: AdapterFieldsRequest, request: Request):
    """Put the fields a person confirmed into force: template, adapter version and runtime bundles together."""

    from src.apps.rule_governance.backend.check_items.adapter_field_service import AdapterFieldService

    _authenticated_user(request)
    return _adapter_fields(lambda: AdapterFieldService().save(
        implementation_id,
        payload.check_item_id,
        fields=payload.fields,
        actor=_authenticated_actor(request),
    ))


@router.get("/catalog/check-items/{check_item_id}/events")
def list_check_item_events(check_item_id: str, event_type: str, limit: int = 10):
    """The latest stored events of one event type a rule of this check item produces."""

    from src.apps.rule_governance.backend.check_items.relations import recent_events
    from src.infrastructure.db.postgres import get_db_session

    try:
        with get_db_session() as db:
            return recent_events(db, check_item_id, event_type, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/catalog/check-items/{check_item_id}/retired-events/close",
    dependencies=[Depends(app_user)],
)
def close_check_item_retired_events(request: Request, check_item_id: str):
    """Close the events left open by rules of this check item that no longer run."""

    try:
        return CheckItemCatalogGovernanceService().close_retired_rule_events(
            check_item_id,
            actor=_authenticated_actor(request),
        )
    except ValueError as exc:
        _raise_catalog_governance_error(exc)


@router.post(
    "/catalog/check-items/{check_item_id}/rollback-preview",
    dependencies=[Depends(app_user)],
)
def preview_check_item_rollback(request: Request, check_item_id: str):
    """Draft the previously released version again and say what publishing it would switch."""

    try:
        return CheckItemCatalogGovernanceService().preview_rollback(
            check_item_id,
            actor=_authenticated_actor(request),
        )
    except ValueError as exc:
        _raise_catalog_governance_error(exc)


@router.post(
    "/catalog/changes/{change_id}/discard",
    dependencies=[Depends(app_user)],
)
def discard_check_item_catalog_draft(request: Request, change_id: str):
    """Withdraw a draft that was never submitted."""

    try:
        return CheckItemCatalogGovernanceService().discard_draft(
            change_id,
            actor=_authenticated_actor(request),
        )
    except ValueError as exc:
        _raise_catalog_governance_error(exc)


@router.post("/catalog/check-items/{check_item_id}/delete-preview")
def preview_check_item_delete(request: Request, check_item_id: str):
    """What deleting a check item would take with it. The delete is tried and rolled back."""

    try:
        return CheckItemCatalogGovernanceService().preview_delete(
            check_item_id,
            actor=_authenticated_actor(request),
        )
    except ValueError as exc:
        _raise_catalog_governance_error(exc)


@router.delete(
    "/catalog/check-items/{check_item_id}",
    dependencies=[Depends(app_user)],
)
def delete_check_item(
    request: Request,
    check_item_id: str,
    payload: CheckItemDeleteRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    try:
        return CheckItemCatalogGovernanceService().delete_check_item(
            check_item_id,
            actor=_authenticated_actor(request),
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_catalog_governance_error(exc)


@router.post("/catalog/implementations/drafts")
def create_collection_implementation_draft(
    request: Request,
    payload: CollectionImplementationDraftRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    try:
        body = payload.model_dump(exclude={"reason"})
        return CheckItemCatalogGovernanceService().create_implementation_draft(
            body,
            actor=_authenticated_actor(request),
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_catalog_governance_error(exc)


@router.delete(
    "/catalog/implementations/{implementation_id}",
    dependencies=[Depends(app_user)],
)
def delete_collection_implementation(
    request: Request,
    implementation_id: str,
    payload: CollectionImplementationDeleteRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    try:
        return CheckItemCatalogGovernanceService().delete_implementation(
            implementation_id,
            actor=_authenticated_actor(request),
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_catalog_governance_error(exc)


@router.post("/catalog/changes/{change_id}/impact-preview")
def preview_check_item_catalog_change(change_id: str):
    try:
        return CheckItemCatalogGovernanceService().preview_impact(change_id)
    except ValueError as exc:
        _raise_catalog_governance_error(exc)


@router.post("/catalog/changes/{change_id}/release-preview")
def preview_check_item_release(request: Request, change_id: str):
    """What publishing a check item change would switch. The switch is tried and rolled back."""

    try:
        return CheckItemCatalogGovernanceService().preview_release(
            change_id,
            actor=_authenticated_actor(request),
        )
    except ValueError as exc:
        _raise_catalog_governance_error(exc)


@router.get("/catalog/changes/{change_id}/publication-options")
def check_item_catalog_publication_options(change_id: str):
    try:
        return CheckItemCatalogGovernanceService().publication_options(change_id)
    except ValueError as exc:
        _raise_catalog_governance_error(exc)


@router.post("/catalog/changes/{change_id}/submit")
def submit_check_item_catalog_change(
    request: Request,
    change_id: str,
    payload: CatalogChangeLifecycleRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    try:
        return CheckItemCatalogGovernanceService().submit(
            change_id,
            actor=_authenticated_actor(request),
            reason=payload.reason,
            expected_impact_digest=payload.expected_impact_digest,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        _raise_catalog_governance_error(exc)


@router.post(
    "/catalog/changes/{change_id}/approve",
    dependencies=[Depends(app_user)],
)
def approve_check_item_catalog_change(
    request: Request,
    change_id: str,
    payload: CatalogChangeLifecycleRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    _authenticated_user(request)
    try:
        return CheckItemCatalogGovernanceService().decide(
            change_id,
            decision="approved",
            actor=_authenticated_actor(request),
            reason=payload.reason,
            expected_impact_digest=payload.expected_impact_digest,
            idempotency_key=idempotency_key,
            rollout_campaign_id=payload.rollout_campaign_id,
            allow_same_actor=True,
        )
    except ValueError as exc:
        _raise_catalog_governance_error(exc)


@router.post(
    "/catalog/changes/{change_id}/reject",
    dependencies=[Depends(app_user)],
)
def reject_check_item_catalog_change(
    request: Request,
    change_id: str,
    payload: CatalogChangeLifecycleRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    _authenticated_user(request)
    try:
        return CheckItemCatalogGovernanceService().decide(
            change_id,
            decision="rejected",
            actor=_authenticated_actor(request),
            reason=payload.reason,
            expected_impact_digest=payload.expected_impact_digest,
            idempotency_key=idempotency_key,
            allow_same_actor=True,
        )
    except ValueError as exc:
        _raise_catalog_governance_error(exc)


@router.get("/check-items")
def list_check_items(
    search: str | None = None,
    category: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    try:
        catalog = CheckItemCatalogRepository().load_active()
        search_value = str(search or "").strip().casefold()
        category_value = str(category or "").strip().casefold()
        implementations_by_item: dict[str, list[dict[str, Any]]] = {}
        for implementation in catalog.implementations:
            payload = {
                "implementation_id": implementation.id,
                "implementation_version_id": implementation.version_id,
                "name": implementation.name,
                "description": implementation.description,
                "match": implementation.match,
                "commands": [
                    {
                        "command": command.command,
                        "priority": command.priority,
                    }
                    for command in implementation.commands
                ],
                "collector": implementation.collector,
                "parser_requirement": implementation.parser_requirement,
                "template_scope": implementation.template_scope,
                "adapter_spec": implementation.adapter_spec,
                "output_quality_policy": implementation.output_quality_policy,
                "selection_priority": implementation.selection_priority,
                "status": implementation.status,
            }
            for check_item_id in implementation.check_item_ids:
                implementations_by_item.setdefault(
                    check_item_id,
                    [],
                ).append(payload)
        items = []
        for item in catalog.check_items:
            if category_value and item.category.casefold() != category_value:
                continue
            display_name = check_item_display_name(item.id, item.name)
            display_description = check_item_display_description(
                item.id,
                item.description,
            )
            implementations = sorted(
                implementations_by_item.get(item.id, []),
                key=lambda row: (
                    -int(row["selection_priority"]),
                    str(row["implementation_id"]),
                ),
            )
            searchable = " ".join(
                [
                    item.id,
                    item.name,
                    display_name,
                    item.category,
                    item.description,
                    display_description,
                    json.dumps(
                        implementations,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ]
            ).casefold()
            if search_value and search_value not in searchable:
                continue
            items.append(
                {
                    "check_item_id": item.id,
                    "check_item_version_id": item.version_id,
                    "version": item.version,
                    "name": item.name,
                    "display_name": display_name,
                    "category": item.category,
                    "description": item.description,
                    "display_description": display_description,
                    "result_mode": item.result_mode,
                    "assessment_mode": item.assessment_mode,
                    "canonical_contract": item.canonical_contract,
                    "report_projection": item.report_projection,
                    "applicability": item.applicability,
                    "freshness_policy": item.freshness_policy,
                    "empty_result_policy": item.empty_result_policy,
                    "coverage_policy": item.coverage_policy,
                    "status": item.status,
                    "digest": item.digest,
                    "implementation_count": len(implementations),
                    "implementations": implementations,
                }
            )
        page_size = max(1, min(int(limit), 200))
        page_offset = max(0, int(offset))
        page = items[page_offset : page_offset + page_size]
        from src.apps.rule_governance.backend.check_items.patrol_overview import patrol_status

        statuses = patrol_status(row["check_item_id"] for row in page)
        for row in page:
            row["patrol_status"] = statuses.get(row["check_item_id"])
        return {
            "items": page,
            "total": len(items),
            "limit": page_size,
            "offset": page_offset,
            "catalog_version": catalog.catalog_version,
        }


    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/catalog/check-item-versions/{check_item_version_id}/rule-context")
def get_check_item_rule_context(check_item_version_id: str):
    """Preview the sample-free semantic input used to generate rule assets."""

    try:
        return CanonicalRuleContextService().get(check_item_version_id)
    except ValueError as exc:
        status_code = 404 if "not found" in str(exc).casefold() else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.post("/catalog/check-item-versions/{check_item_version_id}/rule-candidates")
def generate_check_item_rule_candidates(check_item_version_id: str):
    """Generate deterministic candidates without Parser or sample inputs."""

    try:
        return CanonicalRuleContextService().generate_candidates(
            check_item_version_id
        )
    except ValueError as exc:
        status_code = 404 if "not found" in str(exc).casefold() else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.post(
    "/catalog/check-item-versions/{check_item_version_id}/rule-candidates/drafts"
)
def create_check_item_rule_candidate_drafts(
    request: Request,
    check_item_version_id: str,
):
    try:
        return CanonicalRuleContextService().create_drafts(
            check_item_version_id,
            actor=_authenticated_actor(request),
        )
    except ValueError as exc:
        status_code = 404 if "not found" in str(exc).casefold() else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.post("/catalog/command-adapters/preview")
def preview_command_adapter(
    payload: CommandAdapterPreviewRequest,
    _user: CurrentUser = Depends(app_user),
):
    from src.core.check_items.adapter_projection import (
        project_adapter_batches_to_observations,
    )
    from src.core.check_items.command_adapter import (
        execute_adapter,
        is_direct_projection_adapter,
    )

    catalog = CheckItemCatalogRepository().load_active()
    referenced = {
        str(item.get("check_item_id") or item.get("check_item") or "")
        for item in payload.adapter_spec.get("outputs") or []
        if isinstance(item, dict)
    }
    check_items = {
        item.id: {
            "version_id": item.version_id,
            "canonical_contract": item.canonical_contract,
        }
        for item in catalog.check_items
        if item.id in referenced
    }
    missing = sorted(referenced - set(check_items))
    if missing:
        raise HTTPException(
            status_code=400,
            detail="unknown check items: " + ", ".join(missing),
        )
    try:
        batches = execute_adapter(
            payload.adapter_spec,
            record_sets=payload.record_sets,
            check_items=check_items,
            device_id=payload.device_id,
            adapter_version_id=payload.adapter_version_id,
            require_pinned_check_item_versions=is_direct_projection_adapter(
                payload.adapter_spec
            ),
        )
        observations = (
            project_adapter_batches_to_observations(
                batches,
                check_items={
                    item.id: item
                    for item in catalog.check_items
                    if item.id in referenced
                },
                snapshot={
                    "id": f"adapter-preview:{payload.adapter_version_id}",
                    "device_id": payload.device_id,
                    "source_type": "cli",
                    "canonical_command": "adapter-preview",
                    "parser_template_id": str(
                        (payload.adapter_spec.get("parser") or {}).get(
                            "template_id"
                        )
                        or ""
                    ),
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                },
                mode="parser_validation",
                source="adapter_projection_preview",
                dataset_id=None,
                command_adapter_id="preview",
                command_adapter_version_id=payload.adapter_version_id,
            )
            if is_direct_projection_adapter(payload.adapter_spec)
            else []
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "success": all(
            item.quality_status in {"complete", "valid_empty", "not_applicable"}
            for item in batches
        ),
        "batches": [item.to_dict() for item in batches],
        "observations": [item.to_dict() for item in observations],
    }


@router.get("/command-profiles")
def list_command_profiles(
    status: str | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    page = CommandProfileRepository().list_profiles_page(
        status=status,
        search=search,
        limit=limit,
        offset=offset,
    )
    page["items"] = _enrich_command_profile_check_items(page["items"])
    return page


@router.post("/command-profiles")
def upsert_command_profile(request: Request, payload: CommandProfileUpsertRequest):
    try:
        if payload.status == "active":
            raise ValueError("command profile versions must be created as draft")
        commands = list(dict.fromkeys(
            str(item).strip() for item in payload.commands if str(item).strip()
        ))
        if any(any(ord(char) < 32 for char in command) for command in commands):
            raise ValueError("each command must be a single line without control characters")
        check_item_ids = sorted(
            {
                str(item).strip()
                for item in payload.check_item_ids
                if str(item).strip()
            }
        )
        if commands and check_item_ids:
            raise ValueError(
                "a patrol profile cannot mix check items with legacy commands"
            )
        if not commands and not check_item_ids:
            raise ValueError(
                "a patrol profile requires at least one command or check item"
            )
        if check_item_ids and payload.action != "patrol":
            raise ValueError("check-item profiles only support patrol action")
        actor = _authenticated_actor(request)
        version_id = CommandProfileRepository().create_profile_version(
            name=payload.name,
            action=payload.action,
            commands=commands,
            created_by=actor,
            status="draft",
        )
        if check_item_ids:
            PatrolProfileCheckItemRepository().set_draft_selection(
                version_id,
                check_item_ids,
                actor=actor,
            )
        result = {
            "success": True,
            "profile_version_id": version_id,
            "status": payload.status,
        }
        if check_item_ids:
            result.update(
                {
                    "profile_mode": "check_items",
                    "check_item_ids": check_item_ids,
                }
            )
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/command-profiles/{profile_version_id}/check-items")
def get_patrol_profile_check_items(profile_version_id: str):
    if CommandProfileRepository().get_profile_version(profile_version_id) is None:
        raise HTTPException(status_code=404, detail="patrol profile version not found")
    rows = PatrolProfileCheckItemRepository().list_selection(
        profile_version_id,
        active_only=False,
    )
    return {
        "profile_version_id": profile_version_id,
        "profile_mode": "check_items" if rows else "legacy_commands",
        "check_item_ids": [str(item["check_item_id"]) for item in rows],
        "items": rows,
    }


@router.put("/command-profiles/{profile_version_id}/check-items")
def set_patrol_profile_check_items(
    profile_version_id: str,
    request: Request,
    payload: PatrolProfileCheckItemSelectionRequest,
):
    try:
        profile = CommandProfileRepository().get_profile_version(
            profile_version_id
        )
        if profile is None:
            raise ValueError("patrol profile version not found")
        if profile.get("items"):
            raise ValueError(
                "legacy command profile cannot be converted in place; create a new version"
            )
        selection_repository = PatrolProfileCheckItemRepository()
        selection_repository.set_draft_selection(
            profile_version_id,
            payload.check_item_ids,
            actor=_authenticated_actor(request),
        )
        rows = selection_repository.list_selection(
            profile_version_id,
            active_only=False,
        )
        return {
            "success": True,
            "profile_version_id": profile_version_id,
            "profile_mode": "check_items",
            "check_item_ids": [str(item["check_item_id"]) for item in rows],
            "items": rows,
        }
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
    observe_legacy_path(
        "legacy-command-profile-activation",
        "activate_command_profile",
        outcome="rejected",
    )
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


def _profile_check_item_ids(profile_version_id: str | None) -> tuple[str, ...]:
    if not profile_version_id:
        return ()
    return PatrolProfileCheckItemRepository().active_check_item_ids(
        profile_version_id
    )


def _preview_check_item_profile_impact(
    *,
    profile_name: str,
    profile_version_id: str,
    check_item_ids: tuple[str, ...],
    selector: dict[str, Any],
    devices: list[dict[str, Any]],
    row_limit: int,
    mode: str,
) -> dict[str, Any]:
    target = build_target_manifest(devices=devices, selector=selector)
    inventory = build_inventory_snapshot(devices)
    preview_id = f"profile-impact-{uuid.uuid4()}"
    plan = CheckItemEffectivePlanService().preview(
        EffectivePlanRequest(
            patrol_batch_id=preview_id,
            devices=tuple(devices),
            check_item_ids=check_item_ids,
            profile_version_id=profile_version_id,
            profile_version_ids_by_device={
                str(device.get("device_id") or device.get("id") or ""): (
                    profile_version_id
                )
                for device in devices
            },
            target_manifest_digest=str(target["digest"]),
            inventory_snapshot_digest=str(inventory["digest"]),
            current_observed_at=datetime.now(timezone.utc),
            actor="profile-impact-preview",
        )
    )
    decisions_by_device: dict[str, list[dict[str, Any]]] = {}
    actions_by_device: dict[str, list[dict[str, Any]]] = {}
    for decision in plan.decisions:
        decisions_by_device.setdefault(
            str(decision.get("device_id") or ""),
            [],
        ).append(dict(decision))
    for action in plan.collection_actions:
        actions_by_device.setdefault(
            str(action.get("device_id") or ""),
            [],
        ).append(dict(action))

    rows: list[dict[str, Any]] = []
    ready_total = 0
    blocked_total = 0
    not_applicable_total = 0
    blocked_devices = 0
    for device in devices:
        device_id = str(device.get("device_id") or device.get("id") or "")
        decisions = decisions_by_device.get(device_id, [])
        ready = [item for item in decisions if item.get("status") == "resolved"]
        not_applicable = [
            item for item in decisions if item.get("status") == "not_applicable"
        ]
        blocked = [
            item
            for item in decisions
            if item.get("status") not in {"resolved", "not_applicable"}
        ]
        ready_total += len(ready)
        blocked_total += len(blocked)
        not_applicable_total += len(not_applicable)
        if blocked:
            blocked_devices += 1
        if len(rows) >= row_limit:
            continue
        actions = actions_by_device.get(device_id, [])
        rows.append(
            {
                "device_id": device_id,
                "device_name": device.get("device_name") or device.get("name"),
                "ip": device.get("ip") or device.get("management_ip"),
                "vendor": device.get("vendor"),
                "model": device.get("model"),
                "family": device.get("family"),
                "platform": device.get("platform"),
                "device_type": device.get("device_type"),
                "site": device.get("site"),
                "role": device.get("role"),
                "groups": device.get("groups") or [],
                "command_count": len(actions),
                "check_item_count": len(decisions),
                "eligible_count": len(decisions) - len(not_applicable),
                "ready_count": len(ready),
                "blocked_count": len(blocked),
                "not_applicable_count": len(not_applicable),
                "status": "blocked" if blocked else "ready",
                "violations": [
                    {
                        "check_item_id": item.get("check_item_id"),
                        "status": item.get("status"),
                        "reason_codes": item.get("reason_codes") or [],
                        "implementation_version_id": item.get(
                            "implementation_version_id"
                        ),
                    }
                    for item in blocked
                ],
                "plan": {
                    "profile_name": profile_name,
                    "profile_version_id": profile_version_id,
                    "profile_mode": "check_items",
                    "check_item_ids": list(check_item_ids),
                    "decisions": decisions,
                    "collection_items": actions,
                    "runtime_manifest_digest": plan.manifest_digest,
                },
            }
        )
    denominator = ready_total + blocked_total
    return {
        "success": True,
        "mode": mode,
        "profile_mode": "check_items",
        "profile_name": profile_name,
        "profile_version_id": profile_version_id,
        "selector": selector,
        "command_count": len(plan.collection_actions),
        "check_item_count": len(check_item_ids),
        "matched_device_count": len(devices),
        "evaluated_device_count": len(devices),
        "returned_device_count": len(rows),
        "blocked_device_count": blocked_devices,
        "ready_device_count": len(devices) - blocked_devices,
        "eligible_command_count": denominator,
        "ready_command_count": ready_total,
        "blocked_command_count": blocked_total,
        "ready_check_item_count": ready_total,
        "blocked_check_item_count": blocked_total,
        "not_applicable_check_item_count": not_applicable_total,
        "coverage_rate": ready_total / denominator if denominator else 1.0,
        "runtime_manifest_digest": plan.manifest_digest,
        "rows": rows,
    }


@router.post("/command-profiles/impact-preview")
def preview_command_profile_impact(payload: CommandProfileImpactPreviewRequest):
    try:
        repository = CommandProfileRepository()
        mode = normalize_patrol_mode(payload.mode)
        selector = dict(payload.selector or {})
        devices = [
            device
            for device in DeviceRepository().get_devices_by_filter(status="active")
            if CollectionPolicyResolver._selector_matches(device, selector)
        ]
        limit = max(1, min(int(payload.limit or 100), 500))
        profile = (
            repository.get_profile_version(str(payload.profile_version_id))
            if payload.profile_version_id
            else None
        )
        check_item_ids = (
            ()
            if profile and profile.get("items")
            else _profile_check_item_ids(payload.profile_version_id)
        )
        if check_item_ids:
            if not devices:
                raise ValueError(
                    "no active devices match the impact-preview selector"
                )
            if profile is None:
                raise ValueError("patrol profile version not found")
            result = _preview_check_item_profile_impact(
                profile_name=str(profile["profile_name"]),
                profile_version_id=str(payload.profile_version_id),
                check_item_ids=check_item_ids,
                selector=selector,
                devices=devices,
                row_limit=limit,
                mode=mode,
            )
            if payload.save_evidence:
                repository.record_profile_coverage(
                    str(payload.profile_version_id),
                    result,
                )
                result["evidence_saved"] = True
            else:
                result["evidence_saved"] = False
            return result

        profile_name, profile_version_id, commands = _profile_impact_commands(payload)
        rows: list[dict[str, Any]] = []
        blocked_devices = 0
        eligible_commands = 0
        ready_commands = 0
        blocked_commands = 0
        if not devices:
            raise ValueError("no active devices match the impact-preview selector")
        for device in devices:
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
            "profile_mode": "legacy_commands",
            "profile_name": profile_name,
            "profile_version_id": profile_version_id,
            "selector": selector,
            "command_count": len(commands),
            "matched_device_count": len(devices),
            "evaluated_device_count": len(rows),
            "returned_device_count": min(len(rows), limit),
            "blocked_device_count": blocked_devices,
            "ready_device_count": len(rows) - blocked_devices,
            "eligible_command_count": eligible_commands,
            "ready_command_count": ready_commands,
            "blocked_command_count": blocked_commands,
            "coverage_rate": ready_commands / eligible_commands if eligible_commands else 1.0,
            "rows": rows[:limit],
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

"""Check-item catalog, compatibility resolution and shadow planning."""

from src.core.check_items.assessment import (
    CheckAssessmentDecision,
    CheckAssessmentInput,
    CheckAssessmentRepository,
    CheckAssessmentService,
)
from src.core.check_items.assessment_policy_assets import (
    AssessmentPolicyAssetService,
    AssessmentPolicyConflictError,
)
from src.core.check_items.assessment_policy_migration import (
    AssessmentPolicyMigrationService,
)
from src.core.check_items.batch_results import CheckItemBatchResultService
from src.core.check_items.canonical import (
    DEFAULT_CANONICAL_CATALOG_PATH,
    CanonicalObservationCatalog,
    CanonicalObservationDefinition,
    load_canonical_catalog,
    validate_check_item_catalog_contracts,
    validate_check_item_contract,
)
from src.core.check_items.catalog import (
    DEFAULT_CATALOG_PATH,
    CatalogConflictError,
    CheckItemCatalog,
    CheckItemCatalogRepository,
    CheckItemDefinition,
    CollectionCommand,
    CollectionImplementation,
    build_check_item_definition,
    build_collection_implementation,
    load_catalog,
    load_catalog_defaults,
    validate_check_item_definition,
)
from src.core.check_items.canonical_rule_context import (
    CanonicalRuleContextService,
    build_canonical_rule_context,
    generate_canonical_rule_candidates,
)
from src.core.check_items.catalog_governance import (
    CatalogGovernanceConflictError,
    CheckItemCatalogGovernanceService,
)
from src.core.check_items.comparison import (
    BaselineCandidate,
    ComparisonContextRepository,
    ComparisonContextService,
    ComparisonDecision,
    ProductionBaselineCandidateRepository,
    build_comparison_fact_contract,
)
from src.core.check_items.current_authority import (
    COMPLETENESS_EVIDENCE_OPTIONS,
    CURRENT_AUTHORITY_REQUEST_SCHEMA,
    OBSERVED_AT_EVIDENCE_OPTIONS,
    CurrentAuthorityError,
    PatrolCurrentAuthorityService,
)
from src.core.check_items.current_state import (
    CurrentStateEvaluationInput,
    CurrentStateEvaluationResult,
    CurrentStateEvaluationService,
    CurrentStateFindingRepository,
)
from src.core.check_items.evidence_quality import (
    CoverageDecision,
    CoverageDecisionInput,
    CoverageDecisionService,
    EvidenceQualificationDecision,
    EvidenceQualificationInput,
    EvidenceQualificationService,
    EvidenceQualityRepository,
)
from src.core.check_items.execution import (
    CheckItemExecutionService,
    EvidenceExecutionResultInput,
    StructuredExecutionResultInput,
    TextExecutionResultInput,
)
from src.core.check_items.gaps import AssetGapRepository
from src.core.check_items.identity import (
    DEFAULT_IDENTITY_POLICY_PATH,
    EntityIdentityDecision,
    EntityIdentityPolicy,
    EntityIdentityPolicyRepository,
    load_entity_identity_policy,
    normalize_entity_identity,
)
from src.core.check_items.planning import (
    CheckItemEffectivePlanService,
    EffectivePlan,
    EffectivePlanRequest,
    build_inventory_snapshot,
    build_target_manifest,
)
from src.core.check_items.presentation import (
    CheckItemPresentation,
    check_item_display_description,
    check_item_display_name,
    supported_check_item_presentations,
)
from src.core.check_items.profile import PatrolProfileCheckItemRepository
from src.core.check_items.report_claims import (
    REPORT_CLAIM_LEDGER_SCHEMA_VERSION,
    ReportClaimService,
    build_report_claim_ledger,
    validate_report_claim_ledger,
)
from src.core.check_items.report_manifest import ReportManifestService
from src.core.check_items.report_release import (
    REQUIRED_DISCLOSURE_CONFIRMATIONS,
    ReportReleaseService,
)
from src.core.check_items.reporting import (
    CheckItemReportMaterializer,
    ReportArtifact,
    build_check_item_report_view,
    render_check_item_artifact,
    render_check_item_artifacts,
)
from src.core.check_items.resolver import (
    CheckItemImplementationResolver,
    ImplementationDecision,
    ShadowCheckItemPlan,
)
from src.core.check_items.risk import RiskCaseService
from src.core.check_items.risk_association import (
    RiskAssociationConflictError,
    RiskEpisodeAssociationService,
)
from src.core.check_items.risk_issue_projection import (
    build_risk_issue_projection,
)
from src.core.check_items.production_feedback import (
    ProductionFeedbackConflictError,
    ProductionFeedbackService,
    aggregate_feedback_metrics,
)
from src.core.check_items.rollout import (
    ROLLOUT_STAGES,
    RolloutCampaignService,
    RolloutValidationError,
)
from src.core.check_items.rollout_capture import (
    ProductionPatrolCaptureAdapter,
    RolloutCaptureError,
)
from src.core.check_items.rollout_legacy import (
    LegacyRouteError,
    RolloutLegacyRouteService,
)
from src.core.check_items.run_finalizer import CheckItemRunFinalizer
from src.core.check_items.runtime_bundle import (
    CheckItemRuntimeBundleService,
    RuntimeBundleValidationError,
)
from src.core.check_items.scope import (
    ScopeResolution,
    build_ad_hoc_scope_contract,
    resolve_patrol_scope,
)
from src.core.check_items.scope_repository import (
    PatrolScopeRepository,
    PersistedPatrolScope,
)
from src.core.check_items.source_qualification import (
    ATTESTED_OFFLINE_CURRENT,
    PatrolSourceQualificationService,
    build_attested_offline_current_qualification,
    source_qualification_is_valid,
)
from src.core.check_items.text_comparison import (
    DEFAULT_TEXT_COMPARISON_PROFILE_PATH,
    TextComparisonProfile,
    TextComparisonProfileRepository,
    TextComparisonResult,
    compare_text,
    load_text_comparison_profiles,
)

__all__ = [
    "CanonicalRuleContextService",
    "AssetGapRepository",
    "BaselineCandidate",
    "DEFAULT_CATALOG_PATH",
    "DEFAULT_CANONICAL_CATALOG_PATH",
    "DEFAULT_IDENTITY_POLICY_PATH",
    "DEFAULT_TEXT_COMPARISON_PROFILE_PATH",
    "CatalogConflictError",
    "CatalogGovernanceConflictError",
    "CanonicalObservationCatalog",
    "CanonicalObservationDefinition",
    "CheckAssessmentDecision",
    "CheckAssessmentInput",
    "CheckAssessmentRepository",
    "CheckAssessmentService",
    "AssessmentPolicyAssetService",
    "AssessmentPolicyConflictError",
    "AssessmentPolicyMigrationService",
    "CheckItemCatalog",
    "CheckItemBatchResultService",
    "CheckItemCatalogRepository",
    "CheckItemCatalogGovernanceService",
    "CheckItemDefinition",
    "CheckItemImplementationResolver",
    "CheckItemPresentation",
    "CheckItemExecutionService",
    "CoverageDecision",
    "CoverageDecisionInput",
    "CoverageDecisionService",
    "EvidenceQualificationDecision",
    "EvidenceQualificationInput",
    "EvidenceQualificationService",
    "EvidenceQualityRepository",
    "CheckItemRuntimeBundleService",
    "CheckItemRunFinalizer",
    "CheckItemReportMaterializer",
    "CheckItemEffectivePlanService",
    "CollectionCommand",
    "CollectionImplementation",
    "ComparisonContextRepository",
    "ComparisonContextService",
    "ComparisonDecision",
    "ProductionBaselineCandidateRepository",
    "COMPLETENESS_EVIDENCE_OPTIONS",
    "CURRENT_AUTHORITY_REQUEST_SCHEMA",
    "CurrentAuthorityError",
    "CurrentStateEvaluationInput",
    "CurrentStateEvaluationResult",
    "CurrentStateEvaluationService",
    "CurrentStateFindingRepository",
    "ImplementationDecision",
    "PatrolProfileCheckItemRepository",
    "PatrolCurrentAuthorityService",
    "RiskCaseService",
    "ProductionFeedbackConflictError",
    "ProductionFeedbackService",
    "aggregate_feedback_metrics",
    "RiskAssociationConflictError",
    "RiskEpisodeAssociationService",
    "ReportManifestService",
    "REPORT_CLAIM_LEDGER_SCHEMA_VERSION",
    "ReportClaimService",
    "REQUIRED_DISCLOSURE_CONFIRMATIONS",
    "ReportReleaseService",
    "ReportArtifact",
    "RuntimeBundleValidationError",
    "ROLLOUT_STAGES",
    "RolloutCampaignService",
    "RolloutCaptureError",
    "ProductionPatrolCaptureAdapter",
    "LegacyRouteError",
    "RolloutLegacyRouteService",
    "RolloutValidationError",
    "ShadowCheckItemPlan",
    "ScopeResolution",
    "PatrolScopeRepository",
    "PersistedPatrolScope",
    "ATTESTED_OFFLINE_CURRENT",
    "PatrolSourceQualificationService",
    "EntityIdentityDecision",
    "EntityIdentityPolicy",
    "EntityIdentityPolicyRepository",
    "EffectivePlan",
    "EffectivePlanRequest",
    "EvidenceExecutionResultInput",
    "TextComparisonProfile",
    "TextComparisonProfileRepository",
    "TextComparisonResult",
    "StructuredExecutionResultInput",
    "TextExecutionResultInput",
    "compare_text",
    "build_inventory_snapshot",
    "build_check_item_definition",
    "build_canonical_rule_context",
    "generate_canonical_rule_candidates",
    "validate_check_item_definition",
    "build_collection_implementation",
    "build_comparison_fact_contract",
    "build_ad_hoc_scope_contract",
    "build_attested_offline_current_qualification",
    "check_item_display_description",
    "check_item_display_name",
    "build_check_item_report_view",
    "build_report_claim_ledger",
    "build_risk_issue_projection",
    "build_target_manifest",
    "load_catalog",
    "load_catalog_defaults",
    "load_canonical_catalog",
    "load_entity_identity_policy",
    "load_text_comparison_profiles",
    "normalize_entity_identity",
    "OBSERVED_AT_EVIDENCE_OPTIONS",
    "render_check_item_artifacts",
    "render_check_item_artifact",
    "resolve_patrol_scope",
    "source_qualification_is_valid",
    "supported_check_item_presentations",
    "validate_check_item_catalog_contracts",
    "validate_check_item_contract",
    "validate_report_claim_ledger",
]

"""Check-item catalog, compatibility resolution and shadow planning."""

from src.core.check_items.catalog import (
    DEFAULT_CATALOG_PATH,
    CatalogConflictError,
    CheckItemCatalog,
    CheckItemCatalogRepository,
    CheckItemDefinition,
    CollectionCommand,
    CollectionImplementation,
    load_catalog,
)
from src.core.check_items.canonical import (
    DEFAULT_CANONICAL_CATALOG_PATH,
    CanonicalObservationCatalog,
    CanonicalObservationDefinition,
    load_canonical_catalog,
    validate_check_item_catalog_contracts,
    validate_check_item_contract,
)
from src.core.check_items.identity import (
    DEFAULT_IDENTITY_POLICY_PATH,
    EntityIdentityDecision,
    EntityIdentityPolicy,
    EntityIdentityPolicyRepository,
    load_entity_identity_policy,
    normalize_entity_identity,
)
from src.core.check_items.assessment import (
    CheckAssessmentDecision,
    CheckAssessmentInput,
    CheckAssessmentRepository,
    CheckAssessmentService,
)
from src.core.check_items.batch_results import CheckItemBatchResultService
from src.core.check_items.comparison import (
    BaselineCandidate,
    ComparisonContextRepository,
    ComparisonContextService,
    ComparisonDecision,
)
from src.core.check_items.current_state import (
    CurrentStateEvaluationInput,
    CurrentStateEvaluationResult,
    CurrentStateEvaluationService,
    CurrentStateFindingRepository,
)
from src.core.check_items.execution import (
    CheckItemExecutionService,
    EvidenceExecutionResultInput,
    StructuredExecutionResultInput,
    TextExecutionResultInput,
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
from src.core.check_items.gaps import AssetGapRepository
from src.core.check_items.profile import PatrolProfileCheckItemRepository
from src.core.check_items.planning import (
    CheckItemEffectivePlanService,
    EffectivePlan,
    EffectivePlanRequest,
    build_inventory_snapshot,
    build_target_manifest,
)
from src.core.check_items.risk import RiskCaseService
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
from src.core.check_items.runtime_bundle import (
    CheckItemRuntimeBundleService,
    RuntimeBundleValidationError,
)
from src.core.check_items.run_finalizer import CheckItemRunFinalizer
from src.core.check_items.text_comparison import (
    DEFAULT_TEXT_COMPARISON_PROFILE_PATH,
    TextComparisonProfile,
    TextComparisonProfileRepository,
    TextComparisonResult,
    compare_text,
    load_text_comparison_profiles,
)

__all__ = [
    "AssetGapRepository",
    "BaselineCandidate",
    "DEFAULT_CATALOG_PATH",
    "DEFAULT_CANONICAL_CATALOG_PATH",
    "DEFAULT_IDENTITY_POLICY_PATH",
    "DEFAULT_TEXT_COMPARISON_PROFILE_PATH",
    "CatalogConflictError",
    "CanonicalObservationCatalog",
    "CanonicalObservationDefinition",
    "CheckAssessmentDecision",
    "CheckAssessmentInput",
    "CheckAssessmentRepository",
    "CheckAssessmentService",
    "CheckItemCatalog",
    "CheckItemBatchResultService",
    "CheckItemCatalogRepository",
    "CheckItemDefinition",
    "CheckItemImplementationResolver",
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
    "CurrentStateEvaluationInput",
    "CurrentStateEvaluationResult",
    "CurrentStateEvaluationService",
    "CurrentStateFindingRepository",
    "ImplementationDecision",
    "PatrolProfileCheckItemRepository",
    "RiskCaseService",
    "ReportManifestService",
    "REQUIRED_DISCLOSURE_CONFIRMATIONS",
    "ReportReleaseService",
    "ReportArtifact",
    "RuntimeBundleValidationError",
    "ShadowCheckItemPlan",
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
    "build_check_item_report_view",
    "build_target_manifest",
    "load_catalog",
    "load_canonical_catalog",
    "load_entity_identity_policy",
    "load_text_comparison_profiles",
    "normalize_entity_identity",
    "render_check_item_artifacts",
    "render_check_item_artifact",
    "validate_check_item_catalog_contracts",
    "validate_check_item_contract",
]

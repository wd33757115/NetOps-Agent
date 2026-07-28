"""Bounded, job-oriented Parser generation primitives."""

from .compilers import KeyValueCompiler, RepeatedBlockCompiler, TableCompiler
from .publishing import (
    PersistedGeneration,
    RepositoryModelCallController,
    latest_reusable_candidate,
    outcome_records,
    persist_generation,
    sample_manifest,
    sample_set_digest,
)
from .samples import ParserSample, SampleExpectation, SampleRole, SampleSet
from .ntc_resolver import (
    NtcPlatformDecision,
    NtcPlatformMapper,
    NtcResolution,
    NtcTemplateResolver,
)
from .service import (
    MAX_PARSER_MODEL_CALLS,
    PARSER_TOOL_PROTOCOL_VERSION,
    GenerationAttempt,
    GenerationOutcome,
    InMemoryModelCallController,
    ModelCallBudgetExhausted,
    ParserCandidateReviewer,
    ParserModelTurn,
    ParserReviewReport,
    SimpleParserGenerationService,
    TemplateCandidate,
)
from .spec import FieldSpec, ParserSpec, ParserSpecDocument
from .validation import (
    TextFSMValidationBackend,
    ValidationReport,
    execute_template_candidate,
    validate_template_candidate,
    validate_template_execution,
)

__all__ = [
    "FieldSpec",
    "GenerationAttempt",
    "GenerationOutcome",
    "InMemoryModelCallController",
    "MAX_PARSER_MODEL_CALLS",
    "PARSER_TOOL_PROTOCOL_VERSION",
    "ParserSample",
    "ParserCandidateReviewer",
    "ParserModelTurn",
    "ParserReviewReport",
    "ParserSpec",
    "ParserSpecDocument",
    "PersistedGeneration",
    "RepositoryModelCallController",
    "ModelCallBudgetExhausted",
    "NtcPlatformDecision",
    "NtcPlatformMapper",
    "NtcResolution",
    "NtcTemplateResolver",
    "SampleExpectation",
    "SampleRole",
    "SampleSet",
    "SimpleParserGenerationService",
    "TableCompiler",
    "KeyValueCompiler",
    "RepeatedBlockCompiler",
    "TemplateCandidate",
    "TextFSMValidationBackend",
    "ValidationReport",
    "execute_template_candidate",
    "validate_template_candidate",
    "validate_template_execution",
    "latest_reusable_candidate",
    "outcome_records",
    "persist_generation",
    "sample_manifest",
    "sample_set_digest",
]

"""Rule control-plane public API with side-effect-free lazy imports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "RULE_GENERATION_JOB_TYPES",
    "RULE_GENERATION_STATUSES",
    "RuleGenerationJob",
    "RuleGenerationJobStateError",
    "RuleAssetApprovalService",
    "AgentLoopRevisionService",
    "AgentLoopRevisionExecutor",
    "RuleAssetSubmissionService",
    "RuleGenerationJobRepository",
    "RuntimeRuleAssetBundle",
    "RuntimeRuleAssetResolver",
    "RuntimeRuleAssetSeedService",
    "RuleAssetTemplateActivationService",
    "transition_job",
]


_EXPORTS = {
    "RULE_GENERATION_JOB_TYPES": ("src.apps.rule_governance.backend.rule_control.jobs", "RULE_GENERATION_JOB_TYPES"),
    "RULE_GENERATION_STATUSES": ("src.apps.rule_governance.backend.rule_control.jobs", "RULE_GENERATION_STATUSES"),
    "RuleGenerationJob": ("src.apps.rule_governance.backend.rule_control.jobs", "RuleGenerationJob"),
    "RuleGenerationJobStateError": (
        "src.apps.rule_governance.backend.rule_control.jobs",
        "RuleGenerationJobStateError",
    ),
    "transition_job": ("src.apps.rule_governance.backend.rule_control.jobs", "transition_job"),
    "RuleAssetApprovalService": (
        "src.apps.rule_governance.backend.rule_control.approval",
        "RuleAssetApprovalService",
    ),
    "AgentLoopRevisionService": (
        "src.apps.rule_governance.backend.rule_control.revision",
        "AgentLoopRevisionService",
    ),
    "AgentLoopRevisionExecutor": (
        "src.apps.rule_governance.backend.rule_control.executor",
        "AgentLoopRevisionExecutor",
    ),
    "RuleAssetSubmissionService": (
        "src.apps.rule_governance.backend.rule_control.submission",
        "RuleAssetSubmissionService",
    ),
    "RuleGenerationJobRepository": (
        "src.apps.rule_governance.backend.rule_control.repository",
        "RuleGenerationJobRepository",
    ),
    "RuntimeRuleAssetBundle": (
        "src.apps.rule_governance.backend.rule_control.runtime_assets",
        "RuntimeRuleAssetBundle",
    ),
    "RuntimeRuleAssetResolver": (
        "src.apps.rule_governance.backend.rule_control.runtime_assets",
        "RuntimeRuleAssetResolver",
    ),
    "RuntimeRuleAssetSeedService": (
        "src.apps.rule_governance.backend.rule_control.runtime_seed",
        "RuntimeRuleAssetSeedService",
    ),
    "RuleAssetTemplateActivationService": (
        "src.apps.rule_governance.backend.rule_control.template_activation",
        "RuleAssetTemplateActivationService",
    ),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value

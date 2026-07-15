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
    "RULE_GENERATION_JOB_TYPES": ("src.core.rule_control.jobs", "RULE_GENERATION_JOB_TYPES"),
    "RULE_GENERATION_STATUSES": ("src.core.rule_control.jobs", "RULE_GENERATION_STATUSES"),
    "RuleGenerationJob": ("src.core.rule_control.jobs", "RuleGenerationJob"),
    "RuleGenerationJobStateError": (
        "src.core.rule_control.jobs",
        "RuleGenerationJobStateError",
    ),
    "transition_job": ("src.core.rule_control.jobs", "transition_job"),
    "RuleAssetApprovalService": (
        "src.core.rule_control.approval",
        "RuleAssetApprovalService",
    ),
    "AgentLoopRevisionService": (
        "src.core.rule_control.revision",
        "AgentLoopRevisionService",
    ),
    "AgentLoopRevisionExecutor": (
        "src.core.rule_control.executor",
        "AgentLoopRevisionExecutor",
    ),
    "RuleAssetSubmissionService": (
        "src.core.rule_control.submission",
        "RuleAssetSubmissionService",
    ),
    "RuleGenerationJobRepository": (
        "src.core.rule_control.repository",
        "RuleGenerationJobRepository",
    ),
    "RuntimeRuleAssetBundle": (
        "src.core.rule_control.runtime_assets",
        "RuntimeRuleAssetBundle",
    ),
    "RuntimeRuleAssetResolver": (
        "src.core.rule_control.runtime_assets",
        "RuntimeRuleAssetResolver",
    ),
    "RuntimeRuleAssetSeedService": (
        "src.core.rule_control.runtime_seed",
        "RuntimeRuleAssetSeedService",
    ),
    "RuleAssetTemplateActivationService": (
        "src.core.rule_control.template_activation",
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

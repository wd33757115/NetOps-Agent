"""LLM candidate generation with deterministic, bounded validator feedback loops."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

FORBIDDEN_CONTEXT_KEYS = {
    "command_output",
    "community",
    "password",
    "raw",
    "raw_cli",
    "raw_output",
    "raw_text",
    "secret",
    "snmp_community",
    "token",
}

SUPPORTED_CONDITION_KINDS = (
    "transition",
    "bgp_transition",
    "threshold",
    "threshold_recovery",
    "threshold_lte",
    "threshold_low_recovery",
    "threshold_field_gte",
    "threshold_field_high_recovery",
    "threshold_field_lte",
    "threshold_field_low_recovery",
    "entity_status_down",
    "raw_config_hash",
)








class AdapterProjectionOutputCandidate(BaseModel):
    """A candidate output that remains bounded by the control-plane context."""

    model_config = ConfigDict(extra="forbid")

    check_item_id: str
    check_item_version_id: str
    source_record_set: str
    projection: dict[str, Any]


class AdapterProjectionCandidate(BaseModel):
    """Candidate-only CommandAdapter V2 projection; never a semantic catalog."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["command-adapter/v2"] = "command-adapter/v2"
    parser: dict[str, Any]
    outputs: list[AdapterProjectionOutputCandidate]


class ChangePolicyCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_type: str
    semantic_type: str
    compare_mode: str
    ignore_fields: list[str] = Field(default_factory=list)
    deadband: float | None = None
    thresholds: dict[str, float] = Field(default_factory=dict)
    threshold_references: dict[str, dict[str, Any]] = Field(default_factory=dict)
    recovery_threshold: float | None = None
    minimum_duration: str | None = None
    source: str = "llm_agent"
    confidence: float = 0.8
    reason: str


class EventRuleCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    event_type: str
    severity: str = "warning"
    trigger_role: str | None = None
    condition: dict[str, Any] = Field(default_factory=dict)
    description: str
    priority: int = 100
    exclusive: bool = False
    event_class: str = "fault"
    evaluation_scope: str = "both"
    recovery: dict[str, Any] = Field(default_factory=dict)
    correlation_keys: list[str] = Field(
        default_factory=lambda: ["device_id", "entity_type", "entity_key", "field"]
    )


class EventRuleSetCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[EventRuleCandidate]


@dataclass(frozen=True)
class ProviderResult:
    candidate: dict[str, Any]
    provider: str
    model_name: str | None = None
    token_usage: dict[str, Any] | None = None


class RuleGenerationCandidateProvider(Protocol):
    name: str

    def propose(
        self,
        *,
        stage: str,
        context: dict[str, Any],
        attempt: int,
        previous_candidate: dict[str, Any] | None,
        validator_feedback: dict[str, Any] | None,
    ) -> ProviderResult: ...


def _find_forbidden_keys(value: Any, *, path: str = "") -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            nested_path = f"{path}.{key_text}" if path else key_text
            if key_text.casefold() in FORBIDDEN_CONTEXT_KEYS:
                found.add(nested_path)
            found.update(_find_forbidden_keys(nested, path=nested_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found.update(_find_forbidden_keys(nested, path=f"{path}[{index}]"))
    return found


def assert_sanitized_generation_context(context: dict[str, Any]) -> None:
    forbidden = sorted(_find_forbidden_keys(context))
    if forbidden:
        raise ValueError(
            "rule generation context contains forbidden private fields: " + ", ".join(forbidden)
        )


def _system_prompt(stage: str) -> str:
    common = """You generate candidate network inspection rule assets.
The input contains sanitized structured records only. Never request or invent raw
CLI, credentials, device names, management IPs, Python code, regex execution, or
approval decisions. Return only the requested structured object. A candidate is
not production configuration and will be checked by deterministic validators."""
    if stage == "adapter_projection":
        return (
            common
            + """

Create only a CommandAdapter V2 projection candidate.  The selected_check_item
is already authoritative: do not create, rename or suggest an observation type,
entity type, canonical field, unit, or CheckItem version.  Return exactly one
output for that selected CheckItem and copy its id, version_id, Parser template
and source_record_set exactly as supplied.  Every projection.fields key must
be one of allowed_target_fields and every source/transform field reference must
be one of parser.source_fields.  Every required_target_field must be present.

For an enum target with allowed_values, provide an explicit value_mapping from
source-specific values to the allowed canonical values and set
on_unmapped=reject.  Do not add a mapping when a source value has not been
provided in the context; leave the candidate incomplete so deterministic
validation can request a human edit.  Use only source, literal, expression and
the approved transform/value_mapping/unit fields.  Dimensions belong inside the
binding for their canonical field, for example
{"cpu_usage_percent": {"source": "cpu_5m", "dimensions":
{"window": {"literal": "5m"}}}}.  Never put dimensions beside fields or
use a metric value as a dimension.  Source identity fields must not be mapped
as a metric merely because their names are short or numerically shaped.  When a
Parser record exposes interval-specific metrics (such as cpu_5s/cpu_1m/cpu_5m)
but no standalone interval field, choose an evidence-supported metric and bind
the matching canonical interval with a literal dimension.  If the observation
target carries policy_constraints.applies_to_dimensions, bind only a listed
dimension value; for example a window policy limited to ["5m"] must use the
cpu_5m source value and a window literal of "5m", not cpu_5s/"5s".  Never
return code, regex, raw CLI, credentials, approval decisions, a new standard
meaning, or a runtime judgment.  This is a candidate for local validation and
human confirmation only."""
        )
    if stage == "change_policy":
        return (
            common
            + """

Create one ChangePolicy for the supplied observation field. Supported compare
modes are state_transition, threshold_and_deadband,
threshold_low_and_deadband, numeric_delta, counter_delta, hash_diff,
enum_transition and exact. The mode must agree with semantic_type. Derive numeric
thresholds from historical_stats or explicit policy_intent; explain the basis in
reason. Do not speculate about physical causes or operator actions. Recovery
thresholds must implement hysteresis in the safe direction. When the control
plane supplies threshold_references, preserve those structured reference fields
and prefer them over inferred global constants. policy_constraints are approved
business policy, not suggestions. When required_thresholds,
required_recovery_threshold, or required_deadband are supplied, copy those
values exactly and do not infer replacements from the sample distribution."""
        )
    if stage == "event_rules":
        kinds = ", ".join(SUPPORTED_CONDITION_KINDS)
        return (
            common
            + f"""

Create a small EventRule set for the supplied Contract and ChangePolicy.
An approved health_policy in CheckItem semantics is executed separately as current
health assessment. Preserve its device threshold sources, measurement window and
declared baseline mode. In trend_only mode, device thresholds determine health;
linear_power_drop compares directional optical power loss, and utilization_increase
only reports sufficient load increases. Trends never become fault events, and an
abnormal baseline returning inside device bounds represents recovery. Preserve
legacy frozen policies when replaying historical versions. Never invent thresholds.
State transitions alone do not establish
that an unused interface is faulty; expected-state requirements remain mandatory.
Allowed condition.kind values are: {kinds}. Conditions may reference only declared
fields and entity types. When canonical_entity_type is present, all EventRule
conditions must use it because StateObservation runtime identity has already
been projected from the parser-native entity_type. Generate fault and recovery
rules together when the
policy has recovery semantics. Recovery rules must use event_class=recovery,
declare recovery.resolves using fault event_type values (not rule names), and
must use evaluation_scope=change. Fault rules must set evaluation_scope to
current_state, change, or both. Use current_state or both when the present value
alone can prove a risk (for example high utilization, interface down, or optical
power below an approved threshold); use change only when history is essential.
Fault rules should explicitly decide whether an initial abnormal value is actionable.
When rule_asset_requirements requires a current-state rule but does not require
an event rule, generate only non-recovery fault rules with
evaluation_scope=current_state. In that profile, do not generate recovery rules
and do not use evaluation_scope=change or both because the catalog has not
approved change-event outcomes. Use evaluation_scope=both only when the same
observation explicitly requires both current-state assessment and change events.
Use stable English event_type identifiers and concise Chinese descriptions.
Set trigger_role to high, low, critical_low, high_recovery, low_recovery,
down, up, or change as appropriate. Use change only for an approved lifecycle
or version field whose ChangePolicy compares exact/hash differences. The control
plane compiles trigger_role against the
validated ChangePolicy; condition is only an optional intent hint. Never invent
threshold values that disagree with the ChangePolicy. Do not add condition keys
that the selected DSL kind does not execute. Never reuse a fault event_type
listed in reserved_fault_event_types; choose a stable event type specific to the
current observation target. Severity critical is governance-controlled: use it
only when event_constraints explicitly authorizes it through allow_critical,
allow_critical_event_types, or required_severities. Otherwise use major or
warning for fault rules."""
        )
    raise ValueError(f"unsupported rule generation stage: {stage}")


def _response_model(stage: str) -> type[BaseModel]:
    if stage == "adapter_projection":
        return AdapterProjectionCandidate
    if stage == "change_policy":
        return ChangePolicyCandidate
    if stage == "event_rules":
        return EventRuleSetCandidate
    raise ValueError(f"unsupported rule generation stage: {stage}")


class DeepSeekRuleGenerationProvider:
    """Structured-output provider used by the automatic generation workflow."""

    name = "llm"

    def __init__(self, llm: Any | None = None) -> None:
        self._llm = llm

    def _client(self) -> Any:
        if self._llm is not None:
            return self._llm
        from langchain_deepseek import ChatDeepSeek

        from src.common.config import get_settings

        settings = get_settings()
        if not str(settings.DEEPSEEK_API_KEY or "").strip():
            raise RuntimeError("DeepSeek API key is not configured")
        # deepseek-v4-* 默认 Thinking mode，会拒绝 with_structured_output 的
        # tool_choice=required/any；关闭 thinking 才能稳定做 function calling。
        return ChatDeepSeek(
            model=settings.LLM_MODEL,
            temperature=0.1,
            api_key=settings.DEEPSEEK_API_KEY,
            request_timeout=120,
            extra_body={"thinking": {"type": "disabled"}},
        )

    def propose(
        self,
        *,
        stage: str,
        context: dict[str, Any],
        attempt: int,
        previous_candidate: dict[str, Any] | None,
        validator_feedback: dict[str, Any] | None,
    ) -> ProviderResult:
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = self._client()
        structured = llm.with_structured_output(
            _response_model(stage),
            method="function_calling",
        )
        payload = {
            "stage": stage,
            "attempt": attempt,
            "context": context,
            "previous_candidate": previous_candidate,
            "validator_feedback": validator_feedback,
        }
        result = structured.invoke(
            [
                SystemMessage(content=_system_prompt(stage)),
                HumanMessage(
                    content=json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    )
                ),
            ]
        )
        model = (
            result
            if isinstance(result, BaseModel)
            else _response_model(stage).model_validate(result)
        )
        model_name = getattr(getattr(llm, "model_name", None), "strip", lambda: None)()
        return ProviderResult(
            candidate=model.model_dump(),
            provider=self.name,
            model_name=model_name,
        )


class RuleGenerationProviderRegistry:
    """Registry keeps model providers replaceable without branching by vendor/model."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], RuleGenerationCandidateProvider]] = {
            "llm": DeepSeekRuleGenerationProvider,
        }

    def register(
        self,
        name: str,
        factory: Callable[[], RuleGenerationCandidateProvider],
    ) -> None:
        self._factories[str(name).strip().casefold()] = factory

    def create(self, name: str) -> RuleGenerationCandidateProvider:
        normalized = str(name or "").strip().casefold()
        factory = self._factories.get(normalized)
        if factory is None:
            raise ValueError(f"unknown rule generation provider: {name}")
        return factory()


DEFAULT_RULE_GENERATION_PROVIDERS = RuleGenerationProviderRegistry()


class RuleGenerationAgent:
    """Run propose -> validate -> feedback until success or a bounded stop."""

    def __init__(
        self,
        provider: RuleGenerationCandidateProvider,
        *,
        max_attempts: int = 3,
    ) -> None:
        self.provider = provider
        self.max_attempts = max(1, min(int(max_attempts), 5))

    def run(
        self,
        *,
        stage: str,
        context: dict[str, Any],
        validator: Callable[[dict[str, Any]], dict[str, Any]],
        candidate_governor: Callable[
            [dict[str, Any], dict[str, Any]],
            tuple[dict[str, Any], list[dict[str, Any]]],
        ]
        | None = None,
    ) -> dict[str, Any]:
        assert_sanitized_generation_context(context)
        attempts: list[dict[str, Any]] = []
        previous: dict[str, Any] | None = None
        feedback: dict[str, Any] | None = None
        final_report: dict[str, Any] = {"success": False, "errors": ["not_started"]}

        for attempt in range(1, self.max_attempts + 1):
            candidate: dict[str, Any] | None = None
            governance_repairs: list[dict[str, Any]] = []
            try:
                proposed = self.provider.propose(
                    stage=stage,
                    context=context,
                    attempt=attempt,
                    previous_candidate=previous,
                    validator_feedback=feedback,
                )
                candidate = proposed.candidate
                if candidate_governor is not None:
                    candidate, governance_repairs = candidate_governor(candidate, context)
                final_report = dict(validator(candidate) or {})
                reported_repairs = [
                    dict(item)
                    for item in final_report.get("governance_repairs") or []
                    if isinstance(item, dict)
                ]
                combined_repairs: list[dict[str, Any]] = []
                seen_repairs: set[str] = set()
                for item in [*governance_repairs, *reported_repairs]:
                    fingerprint = json.dumps(item, sort_keys=True, default=str)
                    if fingerprint in seen_repairs:
                        continue
                    seen_repairs.add(fingerprint)
                    combined_repairs.append(item)
                if combined_repairs:
                    final_report["governance_repairs"] = combined_repairs
                success = final_report.get("success") is True
                attempt_report = {
                    "attempt": attempt,
                    "stage": stage,
                    "generator": proposed.provider,
                    "model_name": proposed.model_name,
                    "token_usage": dict(proposed.token_usage or {}),
                    "success": success,
                    "output_summary": _candidate_summary(stage, candidate),
                    "validator_feedback": _feedback_summary(final_report),
                }
                if combined_repairs:
                    attempt_report["governance_repairs"] = combined_repairs
                attempts.append(attempt_report)
                previous = candidate
                feedback = _feedback_summary(final_report)
                if success:
                    return {
                        "success": True,
                        "candidate": candidate,
                        "validation": final_report,
                        "agent_loop": _loop_summary(attempts, ready=True),
                    }
            except Exception as exc:
                feedback = {
                    "errors": [f"{type(exc).__name__}: {exc}"],
                    "failure_type": "provider_or_schema_error",
                }
                final_report = {"success": False, **feedback}
                if candidate is not None:
                    previous = candidate
                attempts.append(
                    {
                        "attempt": attempt,
                        "stage": stage,
                        "generator": getattr(self.provider, "name", "unknown"),
                        "success": False,
                        "output_summary": (
                            _candidate_summary(stage, candidate) if candidate else {}
                        ),
                        "validator_feedback": feedback,
                    }
                )

        revision = {
            "target_generator": stage,
            "reason": list((feedback or {}).get("errors") or ["validation failed"]),
            "required_fixes": feedback or {},
        }
        return {
            "success": False,
            "candidate": previous,
            "validation": final_report,
            "agent_loop": _loop_summary(
                attempts,
                ready=False,
                revision_request=revision,
            ),
        }


def _feedback_summary(report: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "errors",
        "missing_fields",
        "primary_key_status",
        "duplicate_key_count",
        "key_error_count",
        "value_error_count",
        "value_errors",
        "unknown_fields",
        "invalid_condition_fields",
        "collision_count",
    )
    summary = {key: report.get(key) for key in keys if report.get(key) not in (None, [], {})}
    if not summary and report.get("success") is not True:
        summary["errors"] = ["deterministic validation failed"]
    return summary


def _candidate_summary(stage: str, candidate: dict[str, Any]) -> dict[str, Any]:
    if stage == "change_policy":
        return {
            key: candidate.get(key)
            for key in (
                "observation_type",
                "semantic_type",
                "compare_mode",
                "thresholds",
                "recovery_threshold",
            )
        }
    if stage == "adapter_projection":
        outputs = candidate.get("outputs") or []
        return {
            "parser_template_id": (candidate.get("parser") or {}).get("template_id"),
            "output_count": len(outputs),
            "check_item_ids": [
                str(item.get("check_item_id"))
                for item in outputs
                if isinstance(item, dict)
            ],
            "mapped_field_count": sum(
                len((item.get("projection") or {}).get("fields") or {})
                for item in outputs
                if isinstance(item, dict)
            ),
        }
    rules = candidate.get("rules") or []
    return {
        "generated_rule_count": len(rules),
        "event_types": [str(item.get("event_type")) for item in rules if isinstance(item, dict)],
    }


def _loop_summary(
    attempts: list[dict[str, Any]],
    *,
    ready: bool,
    revision_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = {
        "ready_for_approval": ready,
        "next_action": "submit_for_approval" if ready else "revise_candidate",
        "attempt_count": len(attempts),
        "attempts": attempts,
    }
    if revision_request:
        value["revision_request"] = revision_request
    return value

"""Bounded, durable StateGraph for TextFSM parser asset generation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, StateGraph

from .generation_diagnostics import ParserDiagnostic, primary_diagnostic
from .textfsm_dialect import (
    DEFAULT_TEXTFSM_DIALECT,
    dialect_hash,
    installed_textfsm_version,
)


class AttemptRepository(Protocol):
    def create_generation_attempt(self, **kwargs: Any) -> dict[str, Any]: ...

    def save_agent_checkpoint(self, job_id: str, **kwargs: Any) -> dict[str, Any]: ...


class ParserAgentState(TypedDict, total=False):
    job_id: str
    thread_id: str
    train_sample_set_hash: str | None
    holdout_sample_set_hash: str | None
    contract: dict[str, Any]
    contract_valid: bool
    contract_reuse: dict[str, Any]
    contract_revision: int
    template_attempt: int
    current_template: str
    generated_meta: dict[str, Any]
    validation_report: dict[str, Any]
    diagnostics: list[dict[str, Any]]
    primary_diagnostic: dict[str, Any] | None
    best_template: str
    best_generated_meta: dict[str, Any]
    best_rank: float
    best_validation_report: dict[str, Any]
    best_diagnostics: list[dict[str, Any]]
    best_primary_diagnostic: dict[str, Any] | None
    best_attempt_id: str | None
    parent_attempt_id: str | None
    failure_signatures: list[str]
    diagnostic_history: list[str]
    stagnant_rounds: int
    template_mode: str
    started_at: str
    token_usage_total: int
    initial_candidates: list[dict[str, Any]]
    status: str
    stop_reason: str | None


@dataclass(frozen=True)
class ParserAgentLimits:
    max_template_attempts: int = 8
    max_contract_revisions: int = 3
    max_stagnant_rounds: int = 8
    max_repeated_failure: int = 3
    token_budget: int | None = None
    time_budget_seconds: int | None = None


@dataclass(frozen=True)
class GeneratedTemplate:
    template_text: str
    provider: str
    model_name: str | None = None
    prompt_version: str | None = None
    prompt_hash: str | None = None
    token_usage: dict[str, Any] = field(default_factory=dict)
    latency_ms: int | None = None


@dataclass(frozen=True)
class AgentEvaluation:
    success: bool
    rank: float
    report: dict[str, Any]
    diagnostics: tuple[ParserDiagnostic, ...] = ()


ContractBuilder = Callable[[ParserAgentState], tuple[dict[str, Any], bool, list[ParserDiagnostic]]]
CandidateBuilder = Callable[[ParserAgentState], list[dict[str, Any]]]
TemplateBuilder = Callable[[ParserAgentState], GeneratedTemplate]
TemplateValidator = Callable[[str, dict[str, Any]], AgentEvaluation]


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _trailing_signature_count(signatures: list[str]) -> int:
    if not signatures:
        return 0
    latest = signatures[-1]
    count = 0
    for signature in reversed(signatures):
        if signature != latest:
            break
        count += 1
    return count


def _generated_token_count(generated: GeneratedTemplate) -> int:
    usage = dict(generated.token_usage or {})
    total = usage.get("total_tokens")
    if total is None:
        total = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
    try:
        return max(0, int(total or 0))
    except (TypeError, ValueError):
        return 0


class ParserTemplateAgentGraph:
    """Run Contract and Template loops while deterministic validators own success."""

    def __init__(
        self,
        *,
        contract_builder: ContractBuilder,
        candidate_builder: CandidateBuilder | None = None,
        template_builder: TemplateBuilder,
        validator: TemplateValidator,
        repository: AttemptRepository | None = None,
        limits: ParserAgentLimits | None = None,
        model_provider: str = "deepseek",
        train_sample_set_hash: str | None = None,
        holdout_sample_set_hash: str | None = None,
        checkpointer: Any = None,
    ) -> None:
        self.contract_builder = contract_builder
        self.candidate_builder = candidate_builder or (lambda _state: [])
        self.template_builder = template_builder
        self.validator = validator
        self.repository = repository
        self.limits = limits or ParserAgentLimits()
        self.model_provider = model_provider
        self.train_sample_set_hash = train_sample_set_hash
        self.holdout_sample_set_hash = holdout_sample_set_hash
        self.graph = self._build_graph(checkpointer=checkpointer)

    def _build_graph(self, *, checkpointer: Any = None):
        graph = StateGraph(ParserAgentState)
        graph.add_node("start", lambda state: state)
        graph.add_node("contract", self._contract_node)
        graph.add_node("candidates", self._candidate_node)
        graph.add_node("template", self._template_node)
        graph.add_node("validate", self._validation_node)
        graph.add_node("finalize", self._finalize_node)
        graph.set_entry_point("start")
        graph.add_conditional_edges(
            "start",
            self._route_start,
            {
                "contract": "contract",
                "candidates": "candidates",
                "template": "template",
                "finalize": "finalize",
            },
        )
        graph.add_conditional_edges(
            "contract",
            self._route_contract,
            {
                "contract": "contract",
                "candidates": "candidates",
                "finalize": "finalize",
            },
        )
        graph.add_conditional_edges(
            "candidates",
            self._route_candidates,
            {"template": "template", "finalize": "finalize"},
        )
        graph.add_conditional_edges(
            "template",
            self._route_template,
            {"validate": "validate", "template": "template", "finalize": "finalize"},
        )
        graph.add_conditional_edges(
            "validate",
            self._route_validation,
            {"contract": "contract", "template": "template", "finalize": "finalize"},
        )
        graph.add_edge("finalize", END)
        return graph.compile(checkpointer=checkpointer)

    def _checkpoint(self, state: ParserAgentState) -> None:
        job_id = state.get("job_id")
        if not self.repository or not job_id:
            return
        checkpoint = {
            key: value
            for key, value in state.items()
            if key not in {"current_template", "best_template", "generated_meta"}
        }
        self.repository.save_agent_checkpoint(
            job_id,
            state=checkpoint,
            budget=asdict(self.limits),
            best_attempt_id=state.get("best_attempt_id"),
        )

    def _record_attempt(
        self,
        state: ParserAgentState,
        *,
        stage: str,
        action: str,
        status: str,
        report: dict[str, Any],
        template_text: str | None = None,
        generated: GeneratedTemplate | None = None,
        failure_signature: str | None = None,
        candidate_rank: float | None = None,
    ) -> str | None:
        job_id = state.get("job_id")
        if not self.repository or not job_id:
            return None
        profile = DEFAULT_TEXTFSM_DIALECT
        row = self.repository.create_generation_attempt(
            job_id=job_id,
            stage=stage,
            action=action,
            status=status,
            parent_attempt_id=state.get("parent_attempt_id"),
            model_provider=self.model_provider if generated else None,
            model_name=generated.model_name if generated else None,
            textfsm_runtime_version=installed_textfsm_version(),
            dialect_version=profile.dialect_version,
            dialect_hash=dialect_hash(profile),
            prompt_version=generated.prompt_version if generated else None,
            prompt_hash=generated.prompt_hash if generated else None,
            contract_hash=_stable_hash(state.get("contract") or {}),
            train_sample_set_hash=self.train_sample_set_hash,
            holdout_sample_set_hash=self.holdout_sample_set_hash,
            template_text=template_text,
            validation_report=report,
            failure_signature=failure_signature,
            candidate_rank=candidate_rank,
            token_usage=generated.token_usage if generated else None,
            latency_ms=generated.latency_ms if generated else None,
        )
        return str(row["id"])

    def _contract_node(self, state: ParserAgentState) -> ParserAgentState:
        revision = int(state.get("contract_revision") or 0) + 1
        try:
            contract, valid, diagnostics = self.contract_builder(state)
        except Exception as exc:
            contract = dict(state.get("contract") or {})
            valid = False
            diagnostics = [
                ParserDiagnostic(
                    code="contract_evidence_conflict",
                    message=str(exc),
                    allowed_action="request_contract_review",
                )
            ]
        primary = primary_diagnostic(diagnostics)
        output: ParserAgentState = {
            **state,
            "contract": contract,
            "contract_valid": bool(valid),
            "contract_revision": revision,
            "diagnostics": [item.to_dict() for item in diagnostics],
            "primary_diagnostic": primary.to_dict() if primary else None,
            "status": "contract_ready" if valid else "contract_review",
        }
        if valid and revision > 1:
            output["failure_signatures"] = []
            output["diagnostic_history"] = []
            output["stagnant_rounds"] = 0
            output["template_mode"] = "generate"
            # A template rank is meaningful only under the Contract that was
            # used to validate it. Never repair an old-schema candidate after
            # the Contract has changed.
            output["current_template"] = ""
            output["best_template"] = ""
            output["best_generated_meta"] = {}
            output["best_rank"] = -1.0
            output["best_attempt_id"] = None
            output["best_validation_report"] = {}
            output["best_diagnostics"] = []
            output["best_primary_diagnostic"] = None
        attempt_id = self._record_attempt(
            output,
            stage="contract",
            action="propose_contract" if revision == 1 else "revise_contract",
            status="passed" if valid else "failed",
            report={
                "contract": contract,
                "valid": bool(valid),
                "diagnostics": output["diagnostics"],
            },
            failure_signature=primary.failure_signature if primary else None,
        )
        if attempt_id:
            output["parent_attempt_id"] = attempt_id
        self._checkpoint(output)
        return output

    def _candidate_node(self, state: ParserAgentState) -> ParserAgentState:
        try:
            candidates = list(self.candidate_builder(state))
        except Exception:
            candidates = []
        return {**state, "initial_candidates": candidates}

    def _template_node(self, state: ParserAgentState) -> ParserAgentState:
        attempt = int(state.get("template_attempt") or 0) + 1
        initial = list(state.get("initial_candidates") or [])
        diagnostic = state.get("primary_diagnostic") or {}
        action = str(diagnostic.get("allowed_action") or "generate_template")
        try:
            if initial:
                item = initial.pop(0)
                generated = GeneratedTemplate(
                    template_text=str(item.get("template_text") or ""),
                    provider=str(item.get("provider") or "builtin"),
                )
            else:
                generated = self.template_builder(state)
            return {
                **state,
                "template_attempt": attempt,
                "current_template": generated.template_text,
                "generated_meta": asdict(generated),
                "token_usage_total": int(state.get("token_usage_total") or 0)
                + _generated_token_count(generated),
                "initial_candidates": initial,
                "status": "validating",
            }
        except Exception as exc:
            failure = ParserDiagnostic(code="model_call_failed", message=str(exc))
            signatures = [*state.get("failure_signatures", []), failure.failure_signature]
            output: ParserAgentState = {
                **state,
                "template_attempt": attempt,
                "diagnostics": [failure.to_dict()],
                "primary_diagnostic": failure.to_dict(),
                "failure_signatures": signatures,
                "status": "template_generation_failed",
            }
            attempt_id = self._record_attempt(
                output,
                stage="template",
                action=action,
                status="failed",
                report={"error": str(exc), "diagnostics": output["diagnostics"]},
                failure_signature=failure.failure_signature,
            )
            if attempt_id:
                output["parent_attempt_id"] = attempt_id
            self._checkpoint(output)
            return output

    def _validation_node(self, state: ParserAgentState) -> ParserAgentState:
        generated_meta = dict(state.get("generated_meta") or {})
        generated = GeneratedTemplate(
            template_text=str(generated_meta.get("template_text") or state["current_template"]),
            provider=str(generated_meta.get("provider") or "llm"),
            model_name=generated_meta.get("model_name"),
            prompt_version=generated_meta.get("prompt_version"),
            prompt_hash=generated_meta.get("prompt_hash"),
            token_usage=dict(generated_meta.get("token_usage") or {}),
            latency_ms=generated_meta.get("latency_ms"),
        )
        evaluation = self.validator(state["current_template"], state["contract"])
        diagnostic = primary_diagnostic(evaluation.diagnostics)
        current_diagnostics = [item.to_dict() for item in evaluation.diagnostics]
        current_primary = diagnostic.to_dict() if diagnostic else None
        previous_rank = float(state.get("best_rank") or -1.0)
        improved = evaluation.rank > previous_rank
        stagnant = 0 if improved else int(state.get("stagnant_rounds") or 0) + 1
        selected_report = (
            evaluation.report
            if improved
            else dict(state.get("best_validation_report") or evaluation.report)
        )
        selected_diagnostics = (
            current_diagnostics
            if improved
            else list(state.get("best_diagnostics") or current_diagnostics)
        )
        selected_primary = (
            current_primary
            if improved
            else state.get("best_primary_diagnostic") or current_primary
        )
        signatures = list(state.get("failure_signatures") or [])
        if selected_primary:
            signatures.append(str(selected_primary.get("failure_signature") or ""))
        history = list(state.get("diagnostic_history") or [])
        if selected_primary:
            history.append(str(selected_primary.get("code") or "generation_blocked"))
        repeated = _trailing_signature_count(signatures)
        output: ParserAgentState = {
            **state,
            "validation_report": selected_report,
            "diagnostics": selected_diagnostics,
            "primary_diagnostic": selected_primary,
            "failure_signatures": signatures,
            "diagnostic_history": history,
            "stagnant_rounds": stagnant,
            "template_mode": (
                "regenerate" if not evaluation.success and repeated >= 2 else "repair"
            ),
            "status": "passed" if evaluation.success else "repair_required",
        }
        if improved:
            output["best_rank"] = evaluation.rank
            output["best_template"] = state["current_template"]
            output["best_generated_meta"] = {
                key: value
                for key, value in generated_meta.items()
                if key != "template_text"
            }
            output["best_validation_report"] = evaluation.report
            output["best_diagnostics"] = current_diagnostics
            output["best_primary_diagnostic"] = current_primary
        action = str(
            (state.get("primary_diagnostic") or {}).get("allowed_action")
            or (
                "generate_template"
                if int(state.get("template_attempt") or 0) == 1
                else "repair_template"
            )
        )
        attempt_id = self._record_attempt(
            output,
            stage="validation",
            action=action,
            status="passed" if evaluation.success else "failed",
            report={
                **evaluation.report,
                # Every immutable attempt must explain that candidate, even
                # when the loop keeps an older higher-ranked candidate as best.
                "diagnostics": current_diagnostics,
                "best_candidate_diagnostics": selected_diagnostics,
                "hard_gates_passed": evaluation.success,
            },
            template_text=state["current_template"],
            generated=generated,
            failure_signature=diagnostic.failure_signature if diagnostic else None,
            candidate_rank=evaluation.rank,
        )
        if attempt_id:
            output["parent_attempt_id"] = attempt_id
            if improved:
                output["best_attempt_id"] = attempt_id
        self._checkpoint(output)
        return output

    def _route_start(self, state: ParserAgentState) -> str:
        if state.get("status") == "passed":
            return "finalize"
        if self._budget_stop_reason(state):
            return "finalize"
        if not state.get("contract_valid") or not state.get("contract"):
            return "contract"
        if int(state.get("template_attempt") or 0) == 0 and not state.get("initial_candidates"):
            return "candidates"
        if (
            int(state.get("template_attempt") or 0) >= self.limits.max_template_attempts
            or int(state.get("stagnant_rounds") or 0) >= self.limits.max_stagnant_rounds
        ):
            return "finalize"
        return "template"

    def _route_contract(self, state: ParserAgentState) -> str:
        if self._budget_stop_reason(state):
            return "finalize"
        if state.get("contract_valid"):
            return "candidates"
        if int(state.get("contract_revision") or 0) < self.limits.max_contract_revisions:
            return "contract"
        return "finalize"

    def _route_template(self, state: ParserAgentState) -> str:
        if state.get("current_template") and state.get("status") == "validating":
            return "validate"
        if self._budget_stop_reason(state):
            return "finalize"
        if int(state.get("template_attempt") or 0) < self.limits.max_template_attempts:
            return "template"
        return "finalize"

    def _route_candidates(self, state: ParserAgentState) -> str:
        if self._budget_stop_reason(state):
            return "finalize"
        if int(state.get("template_attempt") or 0) >= self.limits.max_template_attempts:
            return "finalize"
        return "template"

    def _route_validation(self, state: ParserAgentState) -> str:
        if state.get("status") == "passed":
            return "finalize"
        if self._budget_stop_reason(state):
            return "finalize"
        diagnostic = state.get("primary_diagnostic") or {}
        if diagnostic.get("code") == "dialect_version_mismatch":
            return "finalize"
        if diagnostic.get("allowed_action") == "request_contract_review":
            if int(state.get("contract_revision") or 0) < self.limits.max_contract_revisions:
                return "contract"
            return "finalize"
        # Parser-output failures are repaired in the Template loop. Contract
        # revision is reserved for diagnostics that explicitly request it;
        # otherwise a weak model can destabilize a valid schema to compensate
        # for a bad regex or Record boundary.
        if (
            int(state.get("template_attempt") or 0) >= self.limits.max_template_attempts
            or int(state.get("stagnant_rounds") or 0) >= self.limits.max_stagnant_rounds
        ):
            return "finalize"
        return "template"

    def _finalize_node(self, state: ParserAgentState) -> ParserAgentState:
        passed = state.get("status") == "passed"
        reason = None if passed else self._stop_reason(state)
        output: ParserAgentState = {
            **state,
            "status": "passed" if passed else "blocked",
            "stop_reason": reason,
        }
        job_id = state.get("job_id")
        if self.repository and job_id:
            self._checkpoint(output)
        return output

    def _stop_reason(self, state: ParserAgentState) -> str:
        budget_reason = self._budget_stop_reason(state)
        if budget_reason:
            return budget_reason
        diagnostic = state.get("primary_diagnostic") or {}
        if diagnostic.get("code") == "dialect_version_mismatch":
            return "dialect_version_mismatch"
        if not state.get("contract_valid"):
            return "contract_evidence_unresolved"
        if int(state.get("stagnant_rounds") or 0) >= self.limits.max_stagnant_rounds:
            return "validation_stagnated"
        if int(state.get("template_attempt") or 0) >= self.limits.max_template_attempts:
            return "template_attempt_budget_exhausted"
        return str(diagnostic.get("code") or "generation_blocked")

    def _budget_stop_reason(self, state: ParserAgentState) -> str | None:
        if self.limits.token_budget is not None and int(state.get("token_usage_total") or 0) >= int(
            self.limits.token_budget
        ):
            return "token_budget_exhausted"
        if self.limits.time_budget_seconds is None:
            return None
        started_at = str(state.get("started_at") or "").strip()
        if not started_at:
            return None
        try:
            started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
        except ValueError:
            return "time_budget_state_invalid"
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        if elapsed >= float(self.limits.time_budget_seconds):
            return "time_budget_exhausted"
        return None

    def invoke(self, state: ParserAgentState) -> ParserAgentState:
        initial: ParserAgentState = {
            "contract_revision": 0,
            "template_attempt": 0,
            "best_rank": -1.0,
            "best_validation_report": {},
            "best_generated_meta": {},
            "best_diagnostics": [],
            "best_primary_diagnostic": None,
            "failure_signatures": [],
            "diagnostic_history": [],
            "stagnant_rounds": 0,
            "template_mode": "generate",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "token_usage_total": 0,
            "initial_candidates": [],
            "status": "pending",
            **state,
        }
        if initial.get("job_id") and not initial.get("thread_id"):
            initial["thread_id"] = f"parser-template:{initial['job_id']}"
        config: dict[str, Any] = {"recursion_limit": 32}
        if initial.get("thread_id"):
            config["configurable"] = {"thread_id": initial["thread_id"]}
        return self.graph.invoke(initial, config=config)

from __future__ import annotations

import uuid

import pytest

from src.core.parser.generation_agent import (
    AgentEvaluation,
    GeneratedTemplate,
    ParserAgentLimits,
    ParserTemplateAgentGraph,
    _trailing_signature_count,
)
from src.core.parser.generation_diagnostics import ParserDiagnostic
from src.core.parser.repository import (
    PARSER_AGENT_HARD_GATES,
    ParserJobRepository,
    ParserJobStateError,
    _deprecate_rule_generation_seeds,
    generated_template_activation_errors,
)
from src.core.parser.textfsm_dialect import (
    DEFAULT_TEXTFSM_DIALECT,
    dialect_hash,
    installed_textfsm_version,
)
from src.infrastructure.db.models import (
    ParserGenerationAttempt,
    ParserTemplate,
    ParserTemplateJob,
    RuleGenerationJobRecord,
)
from src.infrastructure.db.postgres import get_db_session


def _valid_contract(_state):
    return (
        {
            "category": "interface_l2",
            "entity_type": "interface_l2",
            "primary_keys": ["interface"],
            "required_fields": ["interface", "status"],
            "optional_fields": [],
            "validators": {},
        },
        True,
        [],
    )


def test_agent_repairs_from_deterministic_diagnostic():
    templates = iter(
        (
            "Value interface (\\S+)\n\nStart\n  ^${interface} -> Set value\n",
            "Value interface (\\S+)\nValue status (\\S+)\n\n"
            "Start\n  ^${interface}\\s+${status} -> Record\n",
        )
    )

    def generate(_state):
        return GeneratedTemplate(next(templates), provider="mock-llm")

    def validate(template, _contract):
        if "Set value" in template:
            diagnostic = ParserDiagnostic(
                code="unsupported_platform_construct",
                message="Set is not supported",
            )
            return AgentEvaluation(False, 1000, {"success": False}, (diagnostic,))
        return AgentEvaluation(True, 9000, {"success": True}, ())

    result = ParserTemplateAgentGraph(
        contract_builder=_valid_contract,
        template_builder=generate,
        validator=validate,
    ).invoke({"job_id": ""})

    assert result["status"] == "passed"
    assert result["template_attempt"] == 2
    assert "Set value" not in result["best_template"]


def test_agent_compares_distinct_provider_candidates_before_calling_llm():
    llm_calls = 0

    def call_llm(_state):
        nonlocal llm_calls
        llm_calls += 1
        raise AssertionError("provider candidates should be exhausted first")

    def validate(template, _contract):
        if template == "valid-provider-template":
            return AgentEvaluation(True, 9000, {"success": True}, ())
        diagnostic = ParserDiagnostic(code="zero_records", message="no records")
        return AgentEvaluation(False, 1000, {"success": False}, (diagnostic,))

    result = ParserTemplateAgentGraph(
        contract_builder=_valid_contract,
        candidate_builder=lambda _state: [
            {"template_text": "invalid-provider-template", "provider": "generic"},
            {"template_text": "valid-provider-template", "provider": "legacy"},
        ],
        template_builder=call_llm,
        validator=validate,
    ).invoke({"job_id": ""})

    assert result["status"] == "passed"
    assert result["template_attempt"] == 2
    assert result["best_template"] == "valid-provider-template"
    assert result["best_generated_meta"]["provider"] == "legacy"
    assert "template_text" not in result["best_generated_meta"]
    assert llm_calls == 0


def test_agent_revises_contract_before_template_loop():
    def contract(state):
        if not state.get("contract_revision"):
            diagnostic = ParserDiagnostic(
                code="contract_evidence_conflict",
                message="multi entity output has no key",
            )
            return ({"required_fields": ["status"], "primary_keys": []}, False, [diagnostic])
        return _valid_contract(state)

    result = ParserTemplateAgentGraph(
        contract_builder=contract,
        template_builder=lambda _state: GeneratedTemplate(
            "Value interface (\\S+)\nValue status (\\S+)\n\n"
            "Start\n  ^${interface}\\s+${status} -> Record\n",
            provider="mock-llm",
        ),
        validator=lambda _template, _contract: AgentEvaluation(
            True, 9000, {"success": True}, ()
        ),
    ).invoke({"job_id": ""})

    assert result["status"] == "passed"
    assert result["contract_revision"] == 2


def test_agent_resumes_after_contract_without_repeating_contract_call():
    contract_calls = 0

    def contract(_state):
        nonlocal contract_calls
        contract_calls += 1
        return _valid_contract(_state)

    result = ParserTemplateAgentGraph(
        contract_builder=contract,
        template_builder=lambda _state: GeneratedTemplate(
            "Value interface (\\S+)\nValue status (\\S+)\n\n"
            "Start\n  ^${interface}\\s+${status} -> Record\n",
            provider="mock-llm",
        ),
        validator=lambda _template, _contract: AgentEvaluation(
            True, 9000, {"success": True}, ()
        ),
    ).invoke(
        {
            "job_id": "",
            "contract": _valid_contract({})[0],
            "contract_valid": True,
            "contract_revision": 1,
            "template_attempt": 1,
            "status": "repair_required",
        }
    )

    assert contract_calls == 0
    assert result["status"] == "passed"
    assert result["template_attempt"] == 2


def test_repeated_failure_limit_counts_only_consecutive_signatures():
    assert _trailing_signature_count(["syntax", "empty", "syntax"]) == 1
    assert _trailing_signature_count(["empty", "syntax", "syntax"]) == 2


def test_generated_template_activation_requires_every_agent_gate_and_dialect():
    profile = DEFAULT_TEXTFSM_DIALECT
    validation = {
        "parser_agent": {
            "status": "passed",
            "best_attempt_id": "attempt-1",
            "hard_gates": {key: True for key in PARSER_AGENT_HARD_GATES},
        },
        "textfsm_dialect": {
            "runtime_version": installed_textfsm_version(),
            "dialect_version": profile.dialect_version,
            "dialect_hash": dialect_hash(profile),
            "prompt_version": profile.prompt_version,
        },
    }

    assert generated_template_activation_errors(
        validation,
        source_job_id="job-1",
    ) == []
    validation["parser_agent"]["hard_gates"]["holdout_passed"] = False
    assert "holdout_passed" in " ".join(
        generated_template_activation_errors(validation, source_job_id="job-1")
    )


def test_agent_uses_full_budget_without_rewriting_valid_contract_for_parser_failure():
    diagnostic = ParserDiagnostic(code="zero_records", message="no records")
    result = ParserTemplateAgentGraph(
        contract_builder=_valid_contract,
        template_builder=lambda _state: GeneratedTemplate(
            "Value interface (\\S+)\n\nStart\n  ^never${interface} -> Record\n",
            provider="mock-llm",
        ),
        validator=lambda _template, _contract: AgentEvaluation(
            False, 1000, {"success": False}, (diagnostic,)
        ),
        limits=ParserAgentLimits(max_template_attempts=6),
    ).invoke({"job_id": ""})

    assert result["status"] == "blocked"
    assert result["template_attempt"] == 6
    assert result["contract_revision"] == 1
    assert result["stop_reason"] == "template_attempt_budget_exhausted"


def test_agent_discards_stagnant_candidate_before_exhausting_budget():
    modes: list[str] = []
    diagnostic = ParserDiagnostic(code="extra_entities", message="captured noise")

    def generate(state):
        modes.append(str(state.get("template_mode")))
        return GeneratedTemplate("candidate", provider="mock-llm")

    result = ParserTemplateAgentGraph(
        contract_builder=_valid_contract,
        template_builder=generate,
        validator=lambda _template, _contract: AgentEvaluation(
            False, 1000, {"success": False}, (diagnostic,)
        ),
        limits=ParserAgentLimits(
            max_template_attempts=3,
            max_contract_revisions=1,
            max_stagnant_rounds=3,
            max_repeated_failure=99,
        ),
    ).invoke({"job_id": ""})

    assert result["status"] == "blocked"
    assert modes == ["generate", "repair", "regenerate"]


def test_agent_stops_after_token_budget_without_starting_another_model_round():
    calls = 0

    def generate(_state):
        nonlocal calls
        calls += 1
        return GeneratedTemplate(
            "Value interface (\\S+)\n\nStart\n  ^never${interface} -> Record\n",
            provider="mock-llm",
            token_usage={"input_tokens": 7, "output_tokens": 5},
        )

    result = ParserTemplateAgentGraph(
        contract_builder=_valid_contract,
        template_builder=generate,
        validator=lambda _template, _contract: AgentEvaluation(
            False,
            1000,
            {"success": False},
            (ParserDiagnostic(code="zero_records", message="no records"),),
        ),
        limits=ParserAgentLimits(max_template_attempts=6, token_budget=10),
    ).invoke({"job_id": ""})

    assert result["status"] == "blocked"
    assert result["stop_reason"] == "token_budget_exhausted"
    assert result["token_usage_total"] == 12
    assert calls == 1


def test_generation_attempts_are_immutable_and_best_candidate_is_projected():
    token = uuid.uuid4().hex
    job_id = f"parser-agent-{token}"
    with get_db_session() as db:
        db.add(
            ParserTemplateJob(
                id=job_id,
                job_type="CREATE_TEMPLATE",
                source_mode="parser_validation",
                vendor="H3C",
                model="Generic",
                profile=f"test-{token}",
                canonical_command="display test",
                output_signature=token[:24],
                idempotency_key=token,
                status="running",
                sample_count=1,
            )
        )

    repository = ParserJobRepository()
    profile = DEFAULT_TEXTFSM_DIALECT
    try:
        first = repository.create_generation_attempt(
            job_id=job_id,
            stage="validation",
            action="generate_template",
            status="failed",
            textfsm_runtime_version="2.1.0",
            dialect_version=profile.dialect_version,
            dialect_hash=dialect_hash(profile),
            template_text="bad",
            validation_report={"success": False},
            candidate_rank=1000,
        )
        second = repository.create_generation_attempt(
            job_id=job_id,
            parent_attempt_id=first["id"],
            stage="validation",
            action="repair_syntax",
            status="passed",
            textfsm_runtime_version="2.1.0",
            dialect_version=profile.dialect_version,
            dialect_hash=dialect_hash(profile),
            template_text="good",
            validation_report={"success": True},
            candidate_rank=9000,
        )
        repository.save_agent_checkpoint(
            job_id,
            state={"status": "passed"},
            best_attempt_id=second["id"],
        )

        attempts = repository.list_generation_attempts(job_id)
        detail = repository.get_job(job_id)
        resume = repository.get_generation_resume_state(job_id)
        assert [item["attempt_number"] for item in attempts] == [1, 2]
        assert all("template_text" not in item for item in attempts)
        assert detail["best_attempt_id"] == second["id"]
        assert detail["attempt_summary"]["total"] == 2
        assert len(detail["generation_attempts"]) == 2
        assert resume["best_attempt_id"] == second["id"]
        assert resume["best_template"] == "good"
        assert resume["best_rank"] == 9000
    finally:
        with get_db_session() as db:
            db.query(ParserGenerationAttempt).filter_by(job_id=job_id).delete(
                synchronize_session=False
            )
            db.query(ParserTemplateJob).filter_by(id=job_id).delete(
                synchronize_session=False
            )


def test_attempt_stage_is_rejected_before_database_write():
    with pytest.raises(ValueError, match="unsupported parser generation stage"):
        ParserJobRepository().create_generation_attempt(
            job_id="missing",
            stage="unknown",
            action="noop",
            status="failed",
            textfsm_runtime_version="2.1.0",
            dialect_version="v1",
            dialect_hash="hash",
            validation_report={},
        )


def test_explicit_retry_resets_loop_state_but_preserves_attempt_history():
    token = uuid.uuid4().hex
    job_id = f"parser-retry-{token}"
    with get_db_session() as db:
        db.add(
            ParserTemplateJob(
                id=job_id,
                job_type="CREATE_TEMPLATE",
                source_mode="parser_validation",
                vendor="Generic",
                model="Generic",
                profile=f"test-{token}",
                canonical_command="inspect component",
                output_signature=token[:24],
                idempotency_key=token,
                status="failed",
                sample_count=1,
                agent_state_json={"status": "blocked", "template_attempt": 8},
                agent_budget_json={"token_usage_total": 100},
                contract_revision_count=2,
                template_attempt_count=8,
                failure_code="skill_execution_failed",
                last_error="budget exhausted",
            )
        )

    repository = ParserJobRepository()
    profile = DEFAULT_TEXTFSM_DIALECT
    try:
        attempt = repository.create_generation_attempt(
            job_id=job_id,
            stage="validation",
            action="generate_template",
            status="failed",
            textfsm_runtime_version="2.1.0",
            dialect_version=profile.dialect_version,
            dialect_hash=dialect_hash(profile),
            template_text="bad",
            validation_report={"success": False},
            candidate_rank=1000,
        )
        repository.save_agent_checkpoint(
            job_id,
            state={"status": "blocked", "template_attempt": 8},
            budget={"token_usage_total": 100},
            best_attempt_id=attempt["id"],
        )

        retried = repository.retry_job(job_id)

        assert retried["status"] == "pending"
        assert retried["agent_state_json"] is None
        assert retried["agent_budget_json"] is None
        assert retried["best_attempt_id"] is None
        assert retried["contract_revision_count"] == 0
        assert retried["template_attempt_count"] == 0
        assert retried["failure_code"] is None
        assert len(repository.list_generation_attempts(job_id)) == 1
    finally:
        with get_db_session() as db:
            db.query(ParserGenerationAttempt).filter_by(job_id=job_id).delete(
                synchronize_session=False
            )
            db.query(ParserTemplateJob).filter_by(id=job_id).delete(
                synchronize_session=False
            )


def test_succeeded_job_retry_requires_every_generated_candidate_deprecated():
    token = uuid.uuid4().hex
    job_id = f"parser-regenerate-{token}"
    template_id = f"template-regenerate-{token}"
    with get_db_session() as db:
        db.add(
            ParserTemplateJob(
                id=job_id,
                job_type="CREATE_TEMPLATE",
                source_mode="parser_validation",
                vendor="Generic",
                model="Generic",
                profile=f"test-{token}",
                canonical_command="inspect component",
                output_signature=token[:24],
                idempotency_key=token,
                status="succeeded",
                sample_count=1,
            )
        )
        db.add(
            ParserTemplate(
                id=template_id,
                vendor="Generic",
                model="Generic",
                profile=f"test-{token}",
                canonical_command="inspect component",
                category="record",
                version="candidate-1",
                asset_path="candidate.textfsm",
                sha256="0" * 64,
                status="pending_approval",
                validation_json={},
                source_job_id=job_id,
                contract_version=1,
            )
        )

    repository = ParserJobRepository()
    try:
        with pytest.raises(ParserJobStateError, match="every generated candidate"):
            repository.retry_job(job_id)

        with get_db_session() as db:
            db.query(ParserTemplate).filter_by(id=template_id).update(
                {"status": "deprecated"},
                synchronize_session=False,
            )

        retried = repository.retry_job(job_id)

        assert retried["status"] == "pending"
        assert retried["agent_state_json"] is None
        assert retried["best_attempt_id"] is None
    finally:
        with get_db_session() as db:
            db.query(ParserTemplate).filter_by(id=template_id).delete(
                synchronize_session=False
            )
            db.query(ParserTemplateJob).filter_by(id=job_id).delete(
                synchronize_session=False
            )


def test_deprecating_parser_source_retires_unpublished_rule_seed():
    token = uuid.uuid4().hex
    template_id = f"template-{token}"
    rule_job_id = f"rule-job-{token}"
    with get_db_session() as db:
        db.add(
            RuleGenerationJobRecord(
                id=rule_job_id,
                job_type="OBSERVATION_CONTRACT",
                status="draft",
                source_ref=f"parser_template:{template_id}",
                payload_json={},
                validation_report_json={},
                approval_required=True,
            )
        )

    try:
        with get_db_session() as db:
            count = _deprecate_rule_generation_seeds(
                db,
                template_id=template_id,
                actor="unit-test",
                reason="source candidate rejected",
            )
        assert count == 1

        with get_db_session() as db:
            row = db.get(RuleGenerationJobRecord, rule_job_id)
            assert row.status == "deprecated"
            assert row.validation_report_json["source_asset_lifecycle"]["actor"] == (
                "unit-test"
            )
            assert row.completed_at is not None
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter_by(id=rule_job_id).delete(
                synchronize_session=False
            )


def test_latest_valid_contract_is_reused_only_for_identical_sample_sets():
    token = uuid.uuid4().hex
    job_id = f"parser-contract-reuse-{token}"
    train_hash = "a" * 64
    holdout_hash = "b" * 64
    with get_db_session() as db:
        db.add(
            ParserTemplateJob(
                id=job_id,
                job_type="CREATE_TEMPLATE",
                source_mode="parser_validation",
                vendor="Generic",
                model="Generic",
                profile=f"test-{token}",
                canonical_command="inspect component",
                output_signature=token[:24],
                idempotency_key=token,
                status="failed",
                sample_count=1,
            )
        )

    repository = ParserJobRepository()
    profile = DEFAULT_TEXTFSM_DIALECT
    contract = _valid_contract({})[0]
    try:
        passed = repository.create_generation_attempt(
            job_id=job_id,
            stage="contract",
            action="propose_contract",
            status="passed",
            textfsm_runtime_version="2.1.0",
            dialect_version=profile.dialect_version,
            dialect_hash=dialect_hash(profile),
            train_sample_set_hash=train_hash,
            holdout_sample_set_hash=holdout_hash,
            validation_report={"contract": contract, "valid": True},
        )
        repository.create_generation_attempt(
            job_id=job_id,
            stage="contract",
            action="revise_contract",
            status="failed",
            textfsm_runtime_version="2.1.0",
            dialect_version=profile.dialect_version,
            dialect_hash=dialect_hash(profile),
            train_sample_set_hash=train_hash,
            holdout_sample_set_hash=holdout_hash,
            validation_report={"contract": {}, "valid": False},
        )

        reusable = repository.get_latest_valid_contract_attempt(
            job_id,
            train_sample_set_hash=train_hash,
            holdout_sample_set_hash=holdout_hash,
        )

        assert reusable["attempt_id"] == passed["id"]
        assert reusable["contract"] == contract
        assert (
            repository.get_latest_valid_contract_attempt(
                job_id,
                train_sample_set_hash="c" * 64,
                holdout_sample_set_hash=holdout_hash,
            )
            is None
        )
    finally:
        with get_db_session() as db:
            db.query(ParserGenerationAttempt).filter_by(job_id=job_id).delete(
                synchronize_session=False
            )
            db.query(ParserTemplateJob).filter_by(id=job_id).delete(
                synchronize_session=False
            )

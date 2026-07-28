from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.core.parser.assets import TemplateAssetStore
from src.core.parser.parser_bundle import build_parser_bundle
from src.core.parser.textfsm_input import TEXTFSM_END_OF_INPUT
from src.core.parser_generation import (
    FieldSpec,
    InMemoryModelCallController,
    ParserModelTurn,
    ParserReviewReport,
    ParserSample,
    ParserSpec,
    ParserSpecDocument,
    SampleExpectation,
    SampleRole,
    SampleSet,
    SimpleParserGenerationService,
    TemplateCandidate,
    TextFSMValidationBackend,
    latest_reusable_candidate,
    persist_generation,
    sample_set_digest,
    validate_template_candidate,
)

SPEC = ParserSpec(
    entity="interface",
    primary_keys=("interface",),
    fields=(
        FieldSpec("interface", True, "interface_name"),
        FieldSpec("status", True, "enum", values=("UP", "DOWN")),
    ),
)


@dataclass
class FakeBackend:
    outputs: dict[tuple[str, str], list[dict[str, str]]]

    def compile(self, template_text: str) -> tuple[str, ...]:
        return ("invalid template",) if template_text == "syntax-error" else ()

    def parse(self, template_text: str, raw_text: str) -> list[dict[str, str]]:
        return self.outputs.get((template_text, raw_text), [])


@dataclass
class FakeCompiler:
    name: str
    template: str | None

    def compile(self, spec, generation_samples):
        del spec, generation_samples
        return TemplateCandidate(self.template, self.name) if self.template else None


class FakeModelSession:
    def __init__(self, templates: tuple[str, ...]) -> None:
        self.templates = templates
        self.feedback: list[dict[str, object] | None] = []

    def next(self, validation_feedback=None):
        self.feedback.append(validation_feedback)
        template = self.templates[len(self.feedback) - 1]
        return ParserModelTurn(
            TemplateCandidate(template, "llm:native_tool_call"),
            "native_tool_call",
        )


class FakeModel:
    def __init__(self, *templates: str) -> None:
        self.templates = templates
        self.opened_samples: tuple[str, ...] | None = None
        self.session: FakeModelSession | None = None

    def open_session(self, spec, samples):
        del spec
        self.opened_samples = samples
        self.session = FakeModelSession(self.templates)
        return self.session


def _samples(*, with_acceptance: bool = True) -> SampleSet:
    rows = [
        ParserSample(
            "generation-1",
            "GEN RAW",
            SampleRole.GENERATION,
            prompt_text="GEN SANITIZED",
            expectation=SampleExpectation(1, frozenset({("GE1/0/1",)})),
        ),
        ParserSample("negative-1", "ERROR", SampleRole.NEGATIVE),
    ]
    if with_acceptance:
        rows.append(
            ParserSample(
                "acceptance-1",
                "ACCEPTANCE SECRET",
                SampleRole.ACCEPTANCE,
                expectation=SampleExpectation(1, frozenset({("GE1/0/2",)})),
            )
        )
    return SampleSet(tuple(rows))


def _records(interface: str) -> list[dict[str, str]]:
    return [{"interface": interface, "status": "UP"}]


def test_multi_record_parser_bundle_validates_each_record_set():
    document = ParserSpecDocument(
        business_purpose="Parse device and slot inventory",
        record_sets=(
            ParserSpec(
                name="device_version",
                entity="device",
                cardinality="singleton",
                primary_keys=(),
                fields=(FieldSpec("software_version", True),),
                expected_records=(
                    {
                        "sample_id": "generation-1",
                        "records": [{"software_version": "7.1"}],
                    },
                ),
            ),
            ParserSpec(
                name="slot_inventory",
                entity="device_slot",
                cardinality="repeated",
                primary_keys=("slot",),
                fields=(
                    FieldSpec("slot", True),
                    FieldSpec("board_type", True),
                ),
                expected_records=(
                    {
                        "sample_id": "generation-1",
                        "records": [
                            {"slot": "0", "board_type": "MPU"},
                            {"slot": "1", "board_type": "MPU"},
                        ],
                    },
                ),
            ),
        ),
    )
    bundle = build_parser_bundle(
        {
            "device_version": "device-template",
            "slot_inventory": "slot-template",
        }
    ).serialize()
    backend = FakeBackend(
        {
            ("device-template\n", "RAW"): [{"software_version": "7.1"}],
            ("slot-template\n", "RAW"): [
                {"slot": "0", "board_type": "MPU"},
                {"slot": "1", "board_type": "MPU"},
            ],
        }
    )

    report = validate_template_candidate(
        bundle,
        document,
        (
            ParserSample(
                "generation-1",
                "RAW",
                SampleRole.GENERATION,
                prompt_text="SANITIZED",
            ),
        ),
        backend,
    )

    assert report.passed is True
    assert report.hard_gates["bundle_complete"] is True
    assert {sample.sample_id for sample in report.samples} == {
        "generation-1::device_version",
        "generation-1::slot_inventory",
    }
    assert {
        record["__record_set__"]
        for sample in report.samples
        for record in sample.records
    } == {"device_version", "slot_inventory"}


def test_deterministic_candidate_passes_without_model_calls():
    backend = FakeBackend(
        {
            ("deterministic", "GEN RAW"): _records("GE1/0/1"),
            ("deterministic", "ACCEPTANCE SECRET"): _records("GE1/0/2"),
        }
    )
    model = FakeModel("unused", "unused")
    service = SimpleParserGenerationService(
        compilers=(FakeCompiler("table", "deterministic"),),
        model=model,
        backend=backend,
    )

    result = service.generate(job_id="job-1", spec=SPEC, sample_set=_samples())

    assert result.passed is True
    assert result.model_calls == 0
    assert model.opened_samples is None


def test_persisted_candidate_is_revalidated_before_opening_a_model_session():
    backend = FakeBackend(
        {
            ("persisted", "GEN RAW"): _records("GE1/0/1"),
            ("persisted", "ACCEPTANCE SECRET"): _records("GE1/0/2"),
        }
    )
    model = FakeModel("must-not-run")

    class Reviewer:
        def review(self, **kwargs):
            assert kwargs["candidate"].template_text == "persisted"
            return ParserReviewReport(
                passed=True,
                summary="Persisted candidate still matches the frozen evidence.",
            )

    result = SimpleParserGenerationService(
        compilers=(),
        model=model,
        backend=backend,
        reviewer=Reviewer(),
        seed_candidates=(
            TemplateCandidate(
                "persisted",
                "llm:persisted_revalidation",
                protocol_version="deepseek-thinking-tools-v1",
            ),
        ),
    ).generate(job_id="job-1", spec=SPEC, sample_set=_samples())

    assert result.passed is True
    assert result.stop_reason == "persisted_candidate_revalidated"
    assert result.model_calls == 0
    assert model.opened_samples is None
    assert result.attempts[0].stage == "llm_revalidate_persisted"


def test_reusable_candidate_requires_same_contract_and_sample_manifest():
    sample_set = _samples()
    generation_hash = sample_set_digest(sample_set.generation)
    acceptance_hash = sample_set_digest(sample_set.acceptance)

    class Repository:
        @staticmethod
        def list_generation_attempts(_job_id, *, include_template):
            assert include_template is True
            return [
                {
                    "template_text": "persisted",
                    "model_name": "deepseek-v4-pro",
                    "contract_hash": "spec-sha",
                    "train_sample_set_hash": generation_hash,
                    "holdout_sample_set_hash": acceptance_hash,
                    "validation_report_json": {
                        "generator_protocol_version": "deepseek-thinking-tools-v1",
                        "generation": {"passed": True},
                        "acceptance": {"passed": True},
                    },
                }
            ]

    candidate = latest_reusable_candidate(
        job={"id": "job-1", "parser_spec_sha256": "spec-sha"},
        sample_set=sample_set,
        repository=Repository(),
    )
    changed_contract = latest_reusable_candidate(
        job={"id": "job-1", "parser_spec_sha256": "different-spec"},
        sample_set=sample_set,
        repository=Repository(),
    )

    assert candidate is not None
    assert candidate.template_text == "persisted"
    assert candidate.provider == "llm:persisted_revalidation"
    assert changed_contract is None


def test_deterministic_acceptance_failure_does_not_select_another_candidate():
    backend = FakeBackend(
        {
            ("first", "GEN RAW"): _records("GE1/0/1"),
            ("second", "GEN RAW"): _records("GE1/0/1"),
            ("second", "ACCEPTANCE SECRET"): _records("GE1/0/2"),
        }
    )
    model = FakeModel("must-not-run")
    service = SimpleParserGenerationService(
        compilers=(
            FakeCompiler("table", "first"),
            FakeCompiler("key_value", "second"),
        ),
        model=model,
        backend=backend,
    )

    result = service.generate(job_id="job-1", spec=SPEC, sample_set=_samples())

    assert result.stop_reason == "acceptance_failed"
    assert [attempt.candidate.template_text for attempt in result.attempts] == ["first"]
    assert model.opened_samples is None


def test_model_tool_loop_is_limited_to_three_calls():
    backend = FakeBackend(
        {
            ("repaired", "GEN RAW"): _records("GE1/0/1"),
            ("repaired", "ACCEPTANCE SECRET"): _records("GE1/0/2"),
        }
    )
    model = FakeModel("bad", "still-bad", "repaired")
    service = SimpleParserGenerationService(
        compilers=(FakeCompiler("table", None),), model=model, backend=backend
    )

    result = service.generate(job_id="job-1", spec=SPEC, sample_set=_samples())

    assert result.passed is True
    assert result.model_calls == 3
    assert model.opened_samples == ("GEN SANITIZED",)
    assert "acceptance" not in " ".join(model.opened_samples).lower()
    assert model.session is not None
    assert model.session.feedback[0] is None
    assert "positive_samples_parsed" in model.session.feedback[1]["failed_gates"]
    assert model.session.feedback[1]["samples"][0]["expected_record_count"] == 1
    assert model.session.feedback[1]["samples"][0]["expected_primary_keys"] == [
        ["GE1/0/1"]
    ]
    assert model.session.feedback[1]["samples"][0]["actual_primary_keys"] == []
    assert model.session.feedback[2]["round"] == 2


def test_model_tool_loop_stops_after_three_failed_candidates():
    model = FakeModel("bad-1", "bad-2", "bad-3", "must-not-run")
    service = SimpleParserGenerationService(
        compilers=(),
        model=model,
        backend=FakeBackend({}),
    )

    result = service.generate(job_id="job-1", spec=SPEC, sample_set=_samples())

    assert result.passed is False
    assert result.stop_reason == "generation_validation_failed"
    assert result.model_calls == 3
    assert len(result.attempts) == 3
    assert model.session is not None
    assert len(model.session.feedback) == 3


def test_model_call_budget_is_shared_across_process_retries():
    controller = InMemoryModelCallController()
    first_model = FakeModel("bad-1", "bad-2", "bad-3")
    first = SimpleParserGenerationService(
        compilers=(),
        model=first_model,
        backend=FakeBackend({}),
        model_call_controller=controller,
    ).generate(job_id="job-1", spec=SPEC, sample_set=_samples())

    second_model = FakeModel("must-not-run")
    second = SimpleParserGenerationService(
        compilers=(),
        model=second_model,
        backend=FakeBackend({}),
        model_call_controller=controller,
    ).generate(job_id="job-1", spec=SPEC, sample_set=_samples())

    assert first.model_calls == 3
    assert second.model_calls == 3
    assert second.stop_reason == "model_call_budget_exhausted"
    assert second_model.session is not None
    assert second_model.session.feedback == []


def test_non_retryable_provider_error_stops_after_one_call():
    class ProviderSession:
        def next(self, _feedback=None):
            return ParserModelTurn(
                candidate=None,
                submission_mode="provider_error",
                protocol_error="provider_request_failed:BadRequestError: invalid request",
                retryable=False,
            )

    class ProviderModel:
        def open_session(self, _spec, _samples):
            return ProviderSession()

    outcome = SimpleParserGenerationService(
        compilers=(),
        model=ProviderModel(),
        backend=FakeBackend({}),
    ).generate(job_id="job-1", spec=SPEC, sample_set=_samples())

    assert outcome.stop_reason == "provider_request_failed"
    assert outcome.model_calls == 1


def test_retryable_provider_error_can_recover_within_budget():
    class ProviderSession:
        def __init__(self):
            self.calls = 0

        def next(self, _feedback=None):
            self.calls += 1
            if self.calls == 1:
                return ParserModelTurn(
                    candidate=None,
                    submission_mode="provider_error",
                    protocol_error="provider_request_failed:APITimeoutError: timed out",
                    retryable=True,
                )
            return ParserModelTurn(
                TemplateCandidate("good", "llm:native_tool_call"),
                "native_tool_call",
            )

    class ProviderModel:
        def __init__(self):
            self.session = ProviderSession()

        def open_session(self, _spec, _samples):
            return self.session

    model = ProviderModel()
    backend = FakeBackend(
        {
            ("good", "GEN RAW"): _records("GE1/0/1"),
            ("good", "ACCEPTANCE SECRET"): _records("GE1/0/2"),
        }
    )
    outcome = SimpleParserGenerationService(
        compilers=(),
        model=model,
        backend=backend,
    ).generate(job_id="job-1", spec=SPEC, sample_set=_samples())

    assert outcome.passed is True
    assert outcome.model_calls == 1
    assert outcome.provider_retries == 1
    assert model.session.calls == 2


def test_tool_loop_attempts_are_persisted_as_one_parent_chain():
    backend = FakeBackend(
        {
            ("good", "GEN RAW"): _records("GE1/0/1"),
            ("good", "ACCEPTANCE SECRET"): _records("GE1/0/2"),
        }
    )
    sample_set = _samples()
    outcome = SimpleParserGenerationService(
        compilers=(),
        model=FakeModel("bad-1", "bad-2", "good"),
        backend=backend,
    ).generate(job_id="job-1", spec=SPEC, sample_set=sample_set)
    payloads: list[dict] = []

    class Jobs:
        def create_generation_attempt(self, **payload):
            payloads.append(payload)
            return {"id": f"attempt-{len(payloads)}"}

    class Templates:
        pass

    persist_generation(
        job={
            "id": "job-1",
            "vendor": "Generic",
            "model": "Model",
            "profile": "Model",
            "family": None,
            "canonical_command": "inspect interface",
            "parser_spec_sha256": "spec-sha",
        },
        spec=SPEC,
        sample_set=sample_set,
        outcome=outcome,
        publish=False,
        dry_run=True,
        job_repository=Jobs(),
        template_repository=Templates(),
    )

    assert [item["parent_attempt_id"] for item in payloads] == [
        None,
        "attempt-1",
        "attempt-2",
    ]
    assert all(
        item["validation_report"]["generator_protocol_version"] == "deepseek-thinking-tools-v1"
        for item in payloads
    )


def test_model_protocol_failures_count_toward_the_three_call_budget():
    class ProtocolSession:
        def __init__(self):
            self.calls = 0

        def next(self, _feedback=None):
            self.calls += 1
            return ParserModelTurn(
                candidate=None,
                submission_mode="tool_protocol_error",
                protocol_error="invalid tool arguments",
                model_name="deepseek-v4-pro",
                token_usage={"total_tokens": 10},
                latency_ms=5,
            )

    class ProtocolModel:
        def __init__(self):
            self.session = ProtocolSession()

        def open_session(self, _spec, _samples):
            return self.session

    model = ProtocolModel()
    outcome = SimpleParserGenerationService(
        compilers=(),
        model=model,
        backend=FakeBackend({}),
    ).generate(job_id="job-1", spec=SPEC, sample_set=_samples())

    assert outcome.stop_reason == "model_protocol_failed"
    assert outcome.model_calls == 3
    assert len(outcome.attempts) == 3
    assert all(
        item.generation_validation.diagnostics == ("invalid tool arguments",)
        for item in outcome.attempts
    )


def test_acceptance_failure_is_never_sent_back_for_repair():
    backend = FakeBackend({("generated", "GEN RAW"): _records("GE1/0/1")})
    model = FakeModel("generated", "must-not-run")
    service = SimpleParserGenerationService(compilers=(), model=model, backend=backend)

    result = service.generate(job_id="job-1", spec=SPEC, sample_set=_samples())

    assert result.passed is False
    assert result.stop_reason == "acceptance_failed"
    assert result.model_calls == 1
    assert model.session is not None
    assert len(model.session.feedback) == 1


def test_single_generation_sample_can_produce_a_candidate():
    backend = FakeBackend({("generated", "GEN RAW"): _records("GE1/0/1")})
    service = SimpleParserGenerationService(
        compilers=(), model=FakeModel("generated", "unused"), backend=backend
    )

    result = service.generate(job_id="job-1", spec=SPEC, sample_set=_samples(with_acceptance=False))

    assert result.passed is True
    assert result.stop_reason == "llm_tool_candidate_passed"
    assert result.candidate is not None
    assert result.model_calls == 1


def test_independent_review_failure_is_sent_back_to_generator_before_approval():
    backend = FakeBackend(
        {
            ("first", "GEN RAW"): _records("GE1/0/1"),
            ("repaired", "GEN RAW"): _records("GE1/0/1"),
        }
    )
    model = FakeModel("first", "repaired")

    class Reviewer:
        def __init__(self):
            self.calls = 0

        def review(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return ParserReviewReport(
                    passed=False,
                    summary="A visible multiline value was truncated.",
                    diagnostics=(
                        {
                            "code": "truncated_multiline_value",
                            "sample_id": "generation-1",
                            "target": "record_boundary",
                            "message": "Capture the continuation line.",
                        },
                    ),
                )
            return ParserReviewReport(
                passed=True,
                summary="Final result is complete.",
                primary_key_assessment={"passed": True},
                completeness_assessment={"passed": True},
            )

    reviewer = Reviewer()
    result = SimpleParserGenerationService(
        compilers=(),
        model=model,
        backend=backend,
        reviewer=reviewer,
    ).generate(
        job_id="job-1",
        spec=SPEC,
        sample_set=_samples(with_acceptance=False),
    )

    assert result.passed is True
    assert result.model_calls == 2
    assert reviewer.calls == 2
    assert model.session is not None
    assert model.session.feedback[1]["failure_type"] == (
        "independent_semantic_review_failed"
    )
    assert model.session.feedback[1]["primary_diagnostic"]["code"] == (
        "truncated_multiline_value"
    )
    assert model.session.feedback[1]["parsed_results"][0]["records"] == [
        {
            "record_index": 1,
            "values": {"interface": "GE1/0/1", "status": "UP"},
        }
    ]
    assert result.attempts[0].review_report is not None
    assert result.attempts[0].review_report.passed is False
    assert result.attempts[1].review_report is not None
    assert result.attempts[1].review_report.passed is True


def test_independent_review_failure_blocks_when_generation_budget_is_exhausted():
    backend = FakeBackend(
        {
            (template, "GEN RAW"): _records("GE1/0/1")
            for template in ("first", "second", "third")
        }
    )

    class Reviewer:
        def review(self, **_kwargs):
            return ParserReviewReport(
                passed=False,
                summary="The selected primary key is not stable.",
                diagnostics=(
                    {
                        "code": "unstable_primary_key",
                        "sample_id": "generation-1",
                        "target": "primary_key",
                        "message": "Use the visible interface identifier.",
                    },
                ),
            )

    result = SimpleParserGenerationService(
        compilers=(),
        model=FakeModel("first", "second", "third"),
        backend=backend,
        reviewer=Reviewer(),
    ).generate(
        job_id="job-1",
        spec=SPEC,
        sample_set=_samples(with_acceptance=False),
    )

    assert result.passed is False
    assert result.stop_reason == "independent_semantic_review_failed"
    assert result.model_calls == 3


def test_single_sample_candidate_is_persisted_with_explicit_validation_mode():
    sample_set = _samples(with_acceptance=False)
    backend = FakeBackend({("generated", "GEN RAW"): _records("GE1/0/1")})
    outcome = SimpleParserGenerationService(
        compilers=(), model=FakeModel("generated", "unused"), backend=backend
    ).generate(job_id="job-1", spec=SPEC, sample_set=sample_set)
    registrations: list[dict] = []

    class Jobs:
        def create_generation_attempt(self, **payload):
            return {"id": "attempt-1"}

    class Templates:
        def register_template(self, **payload):
            registrations.append(payload)
            return "template-1"

    persisted = persist_generation(
        job={
            "id": "job-1",
            "vendor": "Generic",
            "model": "Model",
            "profile": "Model",
            "family": None,
            "canonical_command": "inspect interface",
            "parser_spec_sha256": "spec-sha",
        },
        spec=SPEC,
        sample_set=sample_set,
        outcome=outcome,
        publish=True,
        dry_run=False,
        job_repository=Jobs(),
        template_repository=Templates(),
    )

    validation = registrations[0]["validation"]
    assert persisted.template_generated is True
    assert validation["simple_parser"]["validation_mode"] == "generation_sample_only"
    assert validation["simple_parser"]["independent_acceptance"] is False
    assert all(
        not key.startswith("acceptance_") for key in validation["simple_parser"]["hard_gates"]
    )


def test_job_id_is_mandatory():
    service = SimpleParserGenerationService(
        compilers=(), model=FakeModel("generated", "repaired"), backend=FakeBackend({})
    )
    with pytest.raises(ValueError, match="job_id is required"):
        service.generate(job_id="", spec=SPEC, sample_set=_samples())


def test_validation_uses_the_same_explicit_eof_framing_as_parser_runtime():
    template = f"""Value Filldown interface (\\S+)
Value status ((?i:UP|DOWN))

Start
  ^${{interface}}$$ -> Block

Block
  ^Status:\\s+${{status}}$$
  ^{TEXTFSM_END_OF_INPUT}$$ -> Record EOF

EOF
"""
    sample = ParserSample(
        "generation-eof",
        "GE1/0/1\nStatus: UP\n",
        SampleRole.GENERATION,
        expectation=SampleExpectation(1, frozenset({("GE1/0/1",)})),
    )

    report = validate_template_candidate(
        template,
        SPEC,
        (sample,),
        TextFSMValidationBackend(),
    )

    assert report.passed, report.diagnostics


def test_validation_rejects_constructs_outside_the_platform_dialect():
    template = """Value interface (\\S+)
Value status (\\S+)

Start
  ^${interface}\\s+${status} -> Record
  ^. -> Error
"""
    sample = ParserSample(
        "generation-invalid-dialect",
        "GE1/0/1 UP\n",
        SampleRole.GENERATION,
        expectation=SampleExpectation(1, frozenset({("GE1/0/1",)})),
    )

    report = validate_template_candidate(
        template,
        SPEC,
        (sample,),
        TextFSMValidationBackend(),
    )

    assert report.passed is False
    assert report.hard_gates["compiled"] is False
    assert any("outside the platform dialect" in item for item in report.diagnostics)


def test_validation_rejects_cli_alignment_whitespace_in_parsed_values():
    sample = ParserSample(
        "generation-padding",
        "Peer status : peer adjacency formed ok\n",
        SampleRole.GENERATION,
    )
    backend = FakeBackend(
        {
            ("candidate", sample.raw_text): [
                {
                    "interface": "Po330",
                    "status": "UP   ",
                }
            ]
        }
    )

    report = validate_template_candidate(
        "candidate",
        SPEC,
        (sample,),
        backend,
    )

    assert report.passed is False
    assert report.hard_gates["values_normalized"] is False
    assert report.repair_diagnostic()["primary_diagnostic"]["code"] == (
        "non_normalized_field_value"
    )
    assert any("trailing whitespace" in item for item in report.diagnostics)


def test_validation_feedback_deduplicates_errors_and_includes_parsed_records():
    sample = ParserSample(
        "generation-duplicate-keys",
        "RAW",
        SampleRole.GENERATION,
    )
    records = [
        {
            "interface": "GE1/0/1",
            "status": "UP",
        }
        for _ in range(25)
    ]
    backend = FakeBackend({("candidate", sample.raw_text): records})

    report = validate_template_candidate(
        "candidate",
        SPEC,
        (sample,),
        backend,
    )

    assert report.passed is False
    assert report.samples[0].errors == ("primary key is empty or duplicated",)
    feedback = report.repair_diagnostic()
    parsed_result = feedback["parsed_results"][0]
    assert parsed_result["record_count"] == 25
    assert len(parsed_result["records"]) == 20
    assert parsed_result["records_omitted"] == 5
    assert parsed_result["records"][0] == {
        "record_index": 1,
        "values": {"interface": "GE1/0/1", "status": "UP"},
    }
    assert parsed_result["records"][-1] == {
        "record_index": 25,
        "values": {"interface": "GE1/0/1", "status": "UP"},
    }


def test_published_candidate_is_embedded_and_never_written_to_a_worker_path():
    sample_set = _samples()
    backend = FakeBackend(
        {
            ("deterministic", "GEN RAW"): _records("GE1/0/1"),
            ("deterministic", "ACCEPTANCE SECRET"): _records("GE1/0/2"),
        }
    )
    outcome = SimpleParserGenerationService(
        compilers=(FakeCompiler("table", "deterministic"),),
        model=FakeModel("unused", "unused"),
        backend=backend,
    ).generate(job_id="job-1", spec=SPEC, sample_set=sample_set)
    attempts: list[dict] = []
    registrations: list[dict] = []

    class Jobs:
        def create_generation_attempt(self, **payload):
            attempts.append(payload)
            return {"id": f"attempt-{len(attempts)}"}

    class Templates:
        def register_template(self, **payload):
            registrations.append(payload)
            return "template-1"

    persisted = persist_generation(
        job={
            "id": "job-1",
            "vendor": "Generic",
            "model": "Model",
            "profile": "Model",
            "family": None,
            "canonical_command": "inspect interface",
            "parser_spec_sha256": "spec-sha",
        },
        spec=SPEC,
        sample_set=sample_set,
        outcome=outcome,
        publish=True,
        dry_run=False,
        job_repository=Jobs(),
        template_repository=Templates(),
    )

    assert persisted.template_generated is True
    assert persisted.template_path.startswith("postgresql://parser-template/")
    assert registrations[0]["template_text"] == "deterministic"
    assert registrations[0]["asset_path"] == persisted.template_path


def test_embedded_asset_is_verified_without_a_local_file(tmp_path):
    content = "Value item (\\S+)\n\nStart\n  ^${item} -> Record\n"
    candidate = TemplateCandidate(content, "test")
    store = TemplateAssetStore(tmp_path)

    assert store.verify("postgresql://parser-template/test", candidate.sha256, content)
    assert store.read("postgresql://parser-template/test", candidate.sha256, content) == content
    assert not any(tmp_path.iterdir())

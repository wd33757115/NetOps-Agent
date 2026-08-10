from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pytest

from src.core.parser.assets import TemplateAssetStore
from src.core.parser.parser_bundle import build_parser_bundle
from src.core.parser.repository import ParserJobRepository, SIMPLE_PARSER_BASE_GATES
from src.core.parser.textfsm_input import TEXTFSM_END_OF_INPUT
from src.core.parser_generation import (
    FieldSpec,
    GenerationAttempt,
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
    normalize_parser_spec_payload,
    parser_spec_digest,
    persist_generation,
    sample_set_digest,
    validate_template_candidate,
    validate_template_execution,
    validate_template_runtime_compatibility,
)

SPEC = ParserSpec(
    entity="interface",
    primary_keys=("interface",),
    fields=(
        FieldSpec("interface", True, "interface_name"),
        FieldSpec("status", True, "enum", values=("UP", "DOWN")),
    ),
)


def _source_spec_document() -> dict:
    return ParserSpecDocument(
        business_purpose="Inspect interface operational status.",
        record_sets=(SPEC,),
    ).to_dict()


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


def test_model_prompt_uses_bounded_structural_representatives_only():
    repeated_up = "\n".join(f"Port {index} value {index} UP" for index in range(3000))
    repeated_up_changed = "\n".join(
        f"Port {index + 10} value {index + 20} UP" for index in range(3000)
    )
    repeated_down = "\n".join(
        f"Port {index} value {index} DOWN" for index in range(3000)
    )
    sample_set = SampleSet(
        (
            ParserSample("up-1", repeated_up, SampleRole.GENERATION),
            ParserSample("up-2", repeated_up_changed, SampleRole.GENERATION),
            ParserSample("down-1", repeated_down, SampleRole.GENERATION),
        )
    )

    assert len(sample_set.generation) == 3
    assert len(sample_set.model_samples) == 2
    assert all(len(item) <= 24_100 for item in sample_set.model_samples)
    assert all("CLI_SAMPLE_EXCERPT_OMITTED" in item for item in sample_set.model_samples)


def test_validation_uses_sanitized_evidence_and_checks_raw_source_shape():
    sample = ParserSample(
        "generation-1",
        "GE1/0/1 10.34.2.1 UP",
        SampleRole.GENERATION,
        prompt_text="GE1/0/1 192.0.2.1 UP",
        validation_text_override="GE1/0/1 192.0.2.1 UP",
    )
    backend = FakeBackend(
        {
            ("template", sample.validation_text): [
                {"interface": "GE1/0/1", "address": "192.0.2.1", "status": "UP"}
            ],
            ("template", sample.raw_text): [
                {"interface": "GE1/0/1", "address": "10.34.2.1", "status": "UP"}
            ],
        }
    )

    report = validate_template_execution(
        "template",
        SPEC,
        (sample,),
        backend,
    )

    assert report.passed is True
    assert report.hard_gates["source_executed"] is True
    assert report.hard_gates["source_shape_compatible"] is True
    assert report.samples[0].records[0]["address"] == "192.0.2.1"


def test_validation_rejects_raw_source_structure_drift_without_comparing_values():
    sample = ParserSample(
        "generation-1",
        "GE1/0/1 10.34.2.1 UP\nGE1/0/2 10.34.2.2 UP",
        SampleRole.GENERATION,
        prompt_text="GE1/0/1 192.0.2.1 UP",
        validation_text_override="GE1/0/1 192.0.2.1 UP",
    )
    backend = FakeBackend(
        {
            ("template", sample.validation_text): [
                {"interface": "GE1/0/1", "address": "192.0.2.1", "status": "UP"}
            ],
            ("template", sample.raw_text): [
                {"interface": "GE1/0/1", "address": "10.34.2.1", "status": "UP"},
                {"interface": "GE1/0/2", "address": "10.34.2.2", "status": "UP"},
            ],
        }
    )

    report = validate_template_execution(
        "template",
        SPEC,
        (sample,),
        backend,
    )

    assert report.passed is False
    assert report.hard_gates["source_executed"] is True
    assert report.hard_gates["source_shape_compatible"] is False
    assert "source record count differs" in report.primary_diagnostic


def test_runtime_compatibility_ignores_advisory_record_expectations():
    sample = ParserSample(
        "generation-1",
        "GE1/0/1 UP",
        SampleRole.GENERATION,
        expectation=SampleExpectation(
            99,
            frozenset((f"GE1/0/{index}",) for index in range(1, 100)),
        ),
    )
    backend = FakeBackend(
        {
            ("template", sample.raw_text): [
                {"interface": "GE1/0/1", "status": "UP"}
            ]
        }
    )

    report = validate_template_runtime_compatibility(
        "template",
        SPEC,
        (sample,),
        backend,
    )

    assert report.passed is True
    assert SIMPLE_PARSER_BASE_GATES <= set(report.hard_gates)
    assert report.hard_gates["executed"] is True
    assert report.hard_gates["expectations_matched"] is True


def test_runtime_compatibility_rejects_fields_parser_runtime_cannot_read():
    sample = ParserSample(
        "generation-1",
        "GE1/0/1 UP",
        SampleRole.GENERATION,
    )
    backend = FakeBackend(
        {
            ("template", sample.raw_text): [
                {"INTERFACE": "GE1/0/1", "STATUS": "UP"}
            ]
        }
    )

    report = validate_template_runtime_compatibility(
        "template",
        SPEC,
        (sample,),
        backend,
    )

    assert report.passed is False
    assert report.hard_gates["required_fields_complete"] is False
    assert "missing required fields: interface, status" in report.primary_diagnostic


def test_validation_rejects_present_but_empty_required_fields():
    sample = ParserSample(
        "generation-empty-required",
        "GE1/0/1",
        SampleRole.GENERATION,
    )
    backend = FakeBackend(
        {
            ("template", sample.raw_text): [
                {"interface": "GE1/0/1", "status": ""}
            ]
        }
    )

    report = validate_template_candidate(
        "template",
        SPEC,
        (sample,),
        backend,
    )

    assert report.passed is False
    assert report.hard_gates["required_fields_complete"] is False
    assert report.samples[0].errors == ("missing required fields: status",)


def test_execution_rejects_parser_spec_and_textfsm_field_inventory_drift():
    spec = ParserSpec(
        entity="interface_l3",
        primary_keys=("interface",),
        fields=(
            FieldSpec("interface", True, "interface_name"),
            FieldSpec("status", True, "text"),
            FieldSpec("description", False, "text"),
        ),
    )
    template = """Value interface (\\S+)
Value status (\\S+)

Start
  ^${interface}\\s+${status} -> Record
"""
    sample = ParserSample(
        "generation-field-inventory",
        "Ethernet1/1 up",
        SampleRole.GENERATION,
    )

    report = validate_template_execution(
        template,
        spec,
        (sample,),
        TextFSMValidationBackend(),
    )

    assert report.passed is False
    assert report.hard_gates["compiled"] is True
    assert report.hard_gates["executed"] is True
    assert report.hard_gates["field_inventory_aligned"] is False
    assert "missing=['description']" in report.primary_diagnostic


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
    assert {record["__record_set__"] for sample in report.samples for record in sample.records} == {
        "device_version",
        "slot_inventory",
    }


def test_optional_record_set_may_be_absent_but_bundle_must_cover_each_positive_sample():
    document = ParserSpecDocument(
        business_purpose="Classify mutually exclusive transceiver output branches",
        record_sets=(
            ParserSpec(
                name="transceiver_absent",
                entity="interface_transceiver_absent",
                cardinality="repeated",
                presence="optional",
                primary_keys=("interface",),
                fields=(FieldSpec("interface", True, "interface_name"),),
            ),
            ParserSpec(
                name="transceiver_channels",
                entity="interface_transceiver_channel",
                cardinality="repeated",
                presence="optional",
                primary_keys=("interface", "channel"),
                fields=(
                    FieldSpec("interface", True, "interface_name"),
                    FieldSpec("channel", True, "number"),
                    FieldSpec(
                        "rx_power_dbm",
                        True,
                        "number",
                        unavailable_tokens=("N/A",),
                    ),
                ),
            ),
        ),
    )
    bundle = build_parser_bundle(
        {
            "transceiver_absent": "absent-template",
            "transceiver_channels": "channel-template",
        }
    ).serialize()
    sample = ParserSample("generation-optional", "RAW", SampleRole.GENERATION)
    backend = FakeBackend(
        {
            ("absent-template\n", "RAW"): [],
            ("channel-template\n", "RAW"): [
                {"interface": "FortyGigE1/0/1", "channel": "1", "rx_power_dbm": "N/A"}
            ],
        }
    )

    report = validate_template_candidate(bundle, document, (sample,), backend)

    assert report.passed is True
    assert report.hard_gates["positive_samples_parsed"] is True
    assert report.hard_gates["field_types_valid"] is True

    empty_report = validate_template_candidate(
        bundle,
        document,
        (sample,),
        FakeBackend({}),
    )

    assert empty_report.passed is False
    assert empty_report.hard_gates["positive_samples_parsed"] is False
    assert "no records across the Parser bundle" in empty_report.primary_diagnostic


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


def test_reusable_candidate_skips_content_rejected_by_engineer():
    sample_set = _samples()
    generation_hash = sample_set_digest(sample_set.generation)
    acceptance_hash = sample_set_digest(sample_set.acceptance)
    rejected_template = "persisted-but-rejected"

    class Repository:
        @staticmethod
        def list_rejected_candidate_hashes(_job_id):
            return [hashlib.sha256(rejected_template.encode("utf-8")).hexdigest()]

        @staticmethod
        def list_generation_attempts(_job_id, *, include_template):
            assert include_template is True
            return [
                {
                    "template_text": rejected_template,
                    "contract_hash": "spec-sha",
                    "train_sample_set_hash": generation_hash,
                    "holdout_sample_set_hash": acceptance_hash,
                    "validation_report_json": {
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

    assert candidate is None


def test_parser_job_repository_exposes_rejected_candidate_hashes():
    assert callable(ParserJobRepository().list_rejected_candidate_hashes)


def test_deterministic_candidate_with_bad_final_result_falls_through_to_next_candidate():
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

    assert result.stop_reason == "deterministic_candidate_passed"
    assert [attempt.candidate.template_text for attempt in result.attempts] == [
        "first",
        "second",
    ]
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
    assert model.session.feedback[1]["samples"][0]["expected_primary_keys"] == [["GE1/0/1"]]
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


def test_each_completed_round_is_observed_before_the_next_model_call():
    class Observer:
        def __init__(self):
            self.attempts: list[GenerationAttempt] = []
            self.progress_events: list[str] = []
            self.progress_payloads: list[dict] = []

        def progress(self, **payload):
            self.progress_events.append(payload["stage"])
            self.progress_payloads.append(payload)

        def record_attempt(self, **payload):
            self.attempts.append(payload["attempt"])
            return f"durable-attempt-{len(self.attempts)}"

    observer = Observer()

    class CheckingSession(FakeModelSession):
        def next(self, validation_feedback=None):
            if self.feedback:
                assert len(observer.attempts) == len(self.feedback)
            return super().next(validation_feedback)

    class CheckingModel(FakeModel):
        def open_session(self, spec, samples):
            del spec
            self.opened_samples = samples
            self.session = CheckingSession(self.templates)
            return self.session

    model = CheckingModel("bad-1", "bad-2", "bad-3")
    result = SimpleParserGenerationService(
        compilers=(),
        model=model,
        backend=FakeBackend({}),
        observer=observer,
    ).generate(job_id="job-1", spec=SPEC, sample_set=_samples())

    assert result.passed is False
    assert [item.persisted_id for item in result.attempts] == [
        "durable-attempt-1",
        "durable-attempt-2",
        "durable-attempt-3",
    ]
    assert "attempt_persisted" in observer.progress_events
    assert observer.progress_payloads[-1]["attempt_number"] == 3


def test_total_generation_deadline_stops_before_a_new_model_call(monkeypatch):
    import src.core.parser_generation.service as generation_service

    clock = iter((0.0, 2.0))
    monkeypatch.setattr(generation_service.time, "monotonic", lambda: next(clock))
    model = FakeModel("must-not-run")

    result = SimpleParserGenerationService(
        compilers=(),
        model=model,
        backend=FakeBackend({}),
        total_timeout_seconds=1,
    ).generate(job_id="job-1", spec=SPEC, sample_set=_samples())

    assert result.passed is False
    assert result.stop_reason == "generation_deadline_exceeded"
    assert result.model_calls == 0
    assert model.session is not None
    assert model.session.feedback == []


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


def test_acceptance_execution_failure_is_sent_back_for_repair():
    backend = FakeBackend(
        {
            ("generated", "GEN RAW"): _records("GE1/0/1"),
            ("repaired", "GEN RAW"): _records("GE1/0/1"),
            ("repaired", "ACCEPTANCE SECRET"): _records("GE1/0/2"),
        }
    )
    model = FakeModel("generated", "repaired")
    service = SimpleParserGenerationService(compilers=(), model=model, backend=backend)

    result = service.generate(job_id="job-1", spec=SPEC, sample_set=_samples())

    assert result.passed is True
    assert result.stop_reason == "llm_tool_candidate_passed"
    assert result.model_calls == 2
    assert model.session is not None
    assert model.session.feedback[1]["failure_type"] == "hard_validation_failed"
    assert model.session.feedback[1]["current_template"] == "generated"


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
    assert model.session.feedback[1]["failure_type"] == ("independent_semantic_review_failed")
    assert model.session.feedback[1]["primary_diagnostic"]["code"] == ("truncated_multiline_value")
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


def test_independent_review_failure_routes_to_human_when_budget_is_exhausted():
    backend = FakeBackend(
        {(template, "GEN RAW"): _records("GE1/0/1") for template in ("first", "second", "third")}
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

    assert result.passed is True
    assert result.stop_reason == (
        "human_review_required:independent_semantic_review_failed"
    )
    assert result.model_calls == 3


def test_objectively_valid_ai_rejected_candidate_is_persisted_for_human_review():
    sample_set = _samples(with_acceptance=False)
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
                summary="The result needs an engineer to confirm its record boundary.",
                diagnostics=(
                    {
                        "code": "record_boundary_uncertain",
                        "sample_id": "generation-1",
                        "target": "record_boundary",
                        "message": "Confirm the final parsed table.",
                    },
                ),
            )

    outcome = SimpleParserGenerationService(
        compilers=(),
        model=FakeModel("first", "second", "third"),
        backend=backend,
        reviewer=Reviewer(),
    ).generate(
        job_id="job-human-review",
        spec=SPEC,
        sample_set=sample_set,
    )
    attempts: list[dict] = []
    registrations: list[dict] = []

    class Jobs:
        def create_generation_attempt(self, **payload):
            attempts.append(payload)
            return {"id": f"attempt-{len(attempts)}"}

    class Templates:
        def register_template(self, **payload):
            registrations.append(payload)
            return "template-human-review"

    source_spec = _source_spec_document()
    source_spec_sha256 = parser_spec_digest(
        normalize_parser_spec_payload(source_spec)
    )
    persisted = persist_generation(
        job={
            "id": "job-human-review",
            "vendor": "Generic",
            "model": "Model",
            "profile": "Model",
            "family": None,
            "canonical_command": "inspect interface",
            "parser_spec_json": source_spec,
            "parser_spec_sha256": source_spec_sha256,
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
    assert attempts[-1]["status"] == "needs_review"
    validation = registrations[0]["validation"]
    assert registrations[0]["status"] == "pending_approval"
    assert validation["human_direct_review_required"] is True
    assert validation["ai_review"]["passed"] is False
    assert validation["simple_parser"]["hard_gates"][
        "independent_semantic_review"
    ] is False


def test_parser_spec_review_failure_is_returned_for_all_three_repair_rounds():
    backend = FakeBackend({("first", "GEN RAW"): _records("GE1/0/1")})

    class Reviewer:
        def review(self, **_kwargs):
            return ParserReviewReport(
                passed=False,
                summary="The declared value type contradicts the source values.",
                diagnostics=(
                    {
                        "code": "contract_value_mismatch",
                        "sample_id": "generation-1",
                        "target": "parser_spec",
                        "message": "Use ip_or_unassigned for the placeholder.",
                    },
                ),
            )

    backend.outputs.update(
        {
            (template, "GEN RAW"): _records("GE1/0/1")
            for template in ("first", "second", "third")
        }
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

    assert result.passed is True
    assert result.stop_reason == (
        "human_review_required:independent_semantic_review_failed"
    )
    assert result.model_calls == 3
    assert len(result.attempts) == 3
    assert all(
        attempt.review_report is not None and not attempt.review_report.passed
        for attempt in result.attempts
    )
    session = result.attempts
    assert session[0].review_report.diagnostics[0]["target"] == "parser_spec"


def test_execution_only_generation_sends_zero_record_result_to_llm_reviewer():
    backend = FakeBackend(
        {
            ("repaired", "GEN RAW"): _records("GE1/0/1"),
        }
    )

    class Reviewer:
        def __init__(self):
            self.calls = 0

        def review(self, **kwargs):
            self.calls += 1
            validation = kwargs["validation"]
            assert validation.passed is True
            if self.calls == 1:
                assert validation.samples[0].record_count == 0
                return ParserReviewReport(
                    passed=False,
                    summary="Visible records were not captured.",
                    diagnostics=(
                        {
                            "code": "missing_records",
                            "sample_id": "generation-1",
                            "target": "record_boundary",
                            "message": "Capture the visible interface row.",
                        },
                    ),
                )
            return ParserReviewReport(
                passed=True,
                summary="The final records match the evidence.",
            )

    model = FakeModel("empty", "repaired")
    reviewer = Reviewer()
    result = SimpleParserGenerationService(
        compilers=(),
        model=model,
        backend=backend,
        reviewer=reviewer,
        validator=validate_template_execution,
    ).generate(
        job_id="job-execution-evidence",
        spec=SPEC,
        sample_set=_samples(with_acceptance=False),
    )

    assert result.passed is True
    assert result.model_calls == 2
    assert reviewer.calls == 2
    assert model.session is not None
    assert model.session.feedback[1]["primary_diagnostic"]["code"] == "missing_records"
    assert model.session.feedback[1]["parsed_results"][0]["record_count"] == 0


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

    source_spec = _source_spec_document()
    source_spec_sha256 = parser_spec_digest(normalize_parser_spec_payload(source_spec))
    persisted = persist_generation(
        job={
            "id": "job-1",
            "vendor": "Generic",
            "model": "Model",
            "profile": "Model",
            "family": None,
            "canonical_command": "inspect interface",
            "parser_spec_json": source_spec,
            "parser_spec_sha256": source_spec_sha256,
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
    assert validation["parser_spec_evidence_version"] == 2
    assert validation["parser_spec"] == SPEC.to_dict()
    assert validation["parser_spec_sha256"] == parser_spec_digest(SPEC.to_dict())
    assert validation["source_parser_spec"] == source_spec
    assert validation["source_parser_spec_sha256"] == source_spec_sha256
    assert validation["parser_spec_sha256"] != validation["source_parser_spec_sha256"]
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


def test_validation_executes_native_textfsm_constructs_before_semantic_review():
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

    assert report.passed is True
    assert report.hard_gates["compiled"] is True
    assert report.samples[0].records == (
        {"interface": "GE1/0/1", "status": "UP"},
    )


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

    source_spec = _source_spec_document()
    source_spec_sha256 = parser_spec_digest(normalize_parser_spec_payload(source_spec))
    persisted = persist_generation(
        job={
            "id": "job-1",
            "vendor": "Generic",
            "model": "Model",
            "profile": "Model",
            "family": None,
            "canonical_command": "inspect interface",
            "parser_spec_json": source_spec,
            "parser_spec_sha256": source_spec_sha256,
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

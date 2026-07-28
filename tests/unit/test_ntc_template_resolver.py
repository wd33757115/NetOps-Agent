from __future__ import annotations

from src.core.parser_generation import (
    NtcPlatformMapper,
    NtcTemplateResolver,
    ParserReviewReport,
    ParserSample,
    ParserSpec,
    SampleRole,
    SampleSet,
    SimpleParserGenerationService,
    TextFSMValidationBackend,
    validate_template_execution,
)


def _sample(text: str = "400 Po400 up\n") -> ParserSample:
    return ParserSample(
        sample_id="sample-1",
        raw_text=text,
        prompt_text=text,
        role=SampleRole.GENERATION,
    )


def _spec() -> ParserSpec:
    return ParserSpec.from_dict(
        {
            "entity": "vpc",
            "primary_keys": ["id"],
            "fields": {
                "id": {"required": True, "type": "number"},
                "port": {"required": True, "type": "text"},
                "status": {"required": True, "type": "text"},
            },
            "allow_empty": False,
            "empty_patterns": [],
        }
    )


class _NoModel:
    def open_session(self, *_args, **_kwargs):
        raise AssertionError("NTC candidate should not call the generation model")


class _AcceptingReviewer:
    def review(self, **_kwargs):
        return ParserReviewReport(
            passed=True,
            summary="Parsed rows match the sample.",
            primary_key_assessment={"passed": True},
            completeness_assessment={"passed": True},
        )


def test_ntc_platform_mapping_requires_enough_cisco_identity():
    mapper = NtcPlatformMapper()

    missing_family = mapper.resolve({"vendor": "Cisco"})
    nxos = mapper.resolve({"vendor": "Cisco", "family": "NXOS"})

    assert missing_family.status == "unresolved"
    assert missing_family.reason_codes == ("identity_missing:family",)
    assert nxos.status == "resolved"
    assert nxos.platforms == ("cisco_nxos",)


def test_ntc_resolver_returns_provenance_and_objective_execution():
    decision, resolutions = NtcTemplateResolver().resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show vpc",
        samples=(_sample(),),
    )

    assert decision.status == "resolved"
    assert len(resolutions) == 1
    result = resolutions[0]
    assert result.status == "resolved"
    assert result.template_name == "cisco_nxos_show_vpc.textfsm"
    assert result.candidate is not None
    assert result.candidate.provenance == {
        "provider": "ntc_templates",
        "package_version": "9.1.0",
        "source_template": "cisco_nxos_show_vpc.textfsm",
        "content_sha256": result.candidate.sha256,
        "platform": "cisco_nxos",
        "command": "show vpc",
        "license": "Apache-2.0",
        "platform_mapping_version": 1,
        "platform_mapping_ids": ["cisco-nxos"],
    }
    assert result.execution_evidence["compile_status"] == "compiled"
    assert result.execution_evidence["samples"][0]["record_count"] == 1
    assert result.execution_evidence["samples"][0]["field_names"] == [
        "ID",
        "PORT",
        "STATUS",
    ]


def test_ntc_not_found_is_explicit():
    _decision, resolutions = NtcTemplateResolver().resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show command that does not exist",
        samples=(_sample(),),
    )

    assert resolutions[0].status == "not_found"
    assert resolutions[0].candidate is None


def test_objective_execution_does_not_turn_zero_records_into_semantic_failure():
    _decision, resolutions = NtcTemplateResolver().resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show vpc",
        samples=(_sample("vPC domain id : 10\n"),),
    )

    assert resolutions[0].status == "resolved"
    assert resolutions[0].execution_evidence["samples"][0]["record_count"] == 0


def test_reviewed_ntc_candidate_bypasses_generation_model():
    _decision, resolutions = NtcTemplateResolver().resolve(
        device={"vendor": "Cisco", "family": "NXOS"},
        command="show vpc",
        samples=(_sample(),),
    )
    candidate = resolutions[0].candidate
    assert candidate is not None

    outcome = SimpleParserGenerationService(
        compilers=(),
        model=_NoModel(),
        backend=TextFSMValidationBackend(),
        reviewer=_AcceptingReviewer(),
        seed_candidates=(candidate,),
        validator=validate_template_execution,
    ).generate(
        job_id="job-1",
        spec=_spec(),
        sample_set=SampleSet((_sample(),)),
    )

    assert outcome.passed
    assert outcome.stop_reason == "ntc_candidate_reviewed"
    assert outcome.model_calls == 0
    assert outcome.candidate == candidate

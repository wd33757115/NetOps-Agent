from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "src" / "skills" / "textfsm-generator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from result_reviewer import (  # noqa: E402
    IndependentParserResultReviewer,
    _is_non_blocking_sample_scope_diagnostic,
)

from src.core.parser_generation import (  # noqa: E402
    FieldSpec,
    ParserSample,
    ParserSpec,
    SampleRole,
    TemplateCandidate,
    ValidationReport,
)
from src.core.parser_generation.validation import SampleValidation  # noqa: E402

SPEC = ParserSpec(
    entity="interface",
    primary_keys=("interface",),
    fields=(
        FieldSpec("interface", True, "interface_name"),
        FieldSpec("status", True, "text"),
    ),
)


def _validation() -> ValidationReport:
    return ValidationReport(
        passed=True,
        hard_gates={
            "compiled": True,
            "positive_samples_parsed": True,
            "required_fields_complete": True,
            "primary_keys_valid": True,
            "field_types_valid": True,
            "expectations_matched": True,
            "negative_samples_rejected": True,
        },
        samples=(
            SampleValidation(
                sample_id="sample-1",
                role=SampleRole.GENERATION,
                passed=True,
                record_count=1,
                records=({"interface": "Eth1/1", "status": "up"},),
                errors=(),
                actual_primary_keys=(("Eth1/1",),),
            ),
        ),
        diagnostics=(),
    )


def test_reviewer_uses_fresh_thinking_call_without_forced_tools():
    captured = {}

    class Completions:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                model="deepseek-v4-pro",
                usage=SimpleNamespace(
                    prompt_tokens=100,
                    completion_tokens=20,
                    total_tokens=120,
                    prompt_cache_hit_tokens=80,
                    prompt_cache_miss_tokens=20,
                ),
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            reasoning_content="reviewed the evidence",
                            content=(
                                '{"passed":true,"summary":"complete",'
                                '"diagnostics":[],"primary_key_assessment":'
                                '{"passed":true,"reason":"stable"},'
                                '"completeness_assessment":'
                                '{"passed":true,"reason":"all rows represented"},'
                                '"record_set_assessments":['
                                '{"name":"interface","passed":true,'
                                '"reason":"current values match"}]}'
                            )
                        )
                    )
                ],
            )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions()),
    )
    report = IndependentParserResultReviewer(
        client=client,
        model_name="deepseek-v4-pro",
    ).review(
        spec=SPEC,
        samples=(
            ParserSample(
                "sample-1",
                "RAW MUST NOT BE SENT",
                SampleRole.GENERATION,
                prompt_text="Eth1/1 up",
            ),
        ),
        candidate=TemplateCandidate("Value interface (\\S+)", "llm"),
        validation=_validation(),
    )

    assert report.passed is True
    assert report.token_usage == {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "prompt_cache_hit_tokens": 80,
        "prompt_cache_miss_tokens": 20,
    }
    assert captured["extra_body"] == {"thinking": {"type": "enabled"}}
    assert captured["reasoning_effort"] == "high"
    assert "tools" not in captured
    assert captured["messages"][0]["role"] == "system"
    assert "Eth1/1 up" in captured["messages"][0]["content"]
    assert '"sample_role": "generation"' in captured["messages"][0]["content"]
    assert "RAW MUST NOT BE SENT" not in captured["messages"][0]["content"]
    assert captured["messages"][1]["role"] == "user"
    assert '"record_count": 1' in captured["messages"][1]["content"]
    assert "`ip` field cannot contain `--`" in captured["messages"][0]["content"]
    assert "regenerating only the TextFSM template cannot repair it" in captured[
        "messages"
    ][0]["content"]
    assert "fields that are operationally valuable" in captured["messages"][0]["content"]
    assert "generation guidance, not ground truth" in captured["messages"][0]["content"]
    assert "Local execution evidence (not a semantic verdict)" in captured["messages"][1][
        "content"
    ]


def test_reviewer_reuses_only_stable_evidence_prefix_across_candidate_revisions():
    requests = []

    class Completions:
        @staticmethod
        def create(**kwargs):
            requests.append(kwargs["messages"])
            return SimpleNamespace(
                model="deepseek-v4-pro",
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            reasoning_content="checked",
                            content=(
                                '{"passed":true,"summary":"complete",'
                                '"diagnostics":[],"primary_key_assessment":'
                                '{"passed":true,"reason":"stable"},'
                                '"completeness_assessment":'
                                '{"passed":true,"reason":"all rows represented"},'
                                '"record_set_assessments":['
                                '{"name":"interface","passed":true,'
                                '"reason":"current values match"}]}'
                            ),
                        )
                    )
                ],
            )

    reviewer = IndependentParserResultReviewer(
        client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        model_name="deepseek-v4-pro",
    )
    samples = (
        ParserSample(
            "sample-1",
            "raw",
            SampleRole.GENERATION,
            prompt_text="Eth1/1 up",
        ),
    )

    reviewer.review(
        spec=SPEC,
        samples=samples,
        candidate=TemplateCandidate("candidate-1", "llm"),
        validation=_validation(),
    )
    reviewer.review(
        spec=SPEC,
        samples=samples,
        candidate=TemplateCandidate("candidate-2", "llm"),
        validation=_validation(),
    )

    assert len(requests[0]) == 2
    assert len(requests[1]) == 2
    assert requests[1][0] == requests[0][0]
    assert "candidate-1" in requests[0][1]["content"]
    assert "candidate-1" not in requests[1][1]["content"]
    assert "candidate-2" in requests[1][1]["content"]
    assert "Parsed record-set coverage summary" in requests[1][1]["content"]
    assert '"default": 1' in requests[1][1]["content"]


def test_reviewer_protocol_failure_becomes_a_blocking_report():
    class Completions:
        @staticmethod
        def create(**_kwargs):
            return SimpleNamespace(
                model="deepseek-v4-pro",
                usage=None,
                choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))],
            )

    report = IndependentParserResultReviewer(
        client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        model_name="deepseek-v4-pro",
    ).review(
        spec=SPEC,
        samples=(
            ParserSample(
                "sample-1",
                "raw",
                SampleRole.GENERATION,
                prompt_text="sanitized",
            ),
        ),
        candidate=TemplateCandidate("template", "llm"),
        validation=_validation(),
    )

    assert report.passed is False
    assert report.diagnostics[0]["code"] == "review_provider_or_protocol_error"


def test_reviewer_does_not_regenerate_a_valid_template_for_omitted_per_set_format():
    class Completions:
        @staticmethod
        def create(**_kwargs):
            return SimpleNamespace(
                model="deepseek-v4-pro",
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                '{"passed":true,'
                                '"summary":"All interface records are correctly parsed.",'
                                '"diagnostics":[],'
                                '"primary_key_assessment":'
                                '{"passed":true,"reason":"stable interface key"},'
                                '"completeness_assessment":'
                                '{"passed":true,"reason":"all rows represented"}}'
                            )
                        )
                    )
                ],
            )

    report = IndependentParserResultReviewer(
        client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        model_name="deepseek-v4-pro",
    ).review(
        spec=SPEC,
        samples=(
            ParserSample(
                "sample-1",
                "raw",
                SampleRole.GENERATION,
                prompt_text="Eth1/1 up",
            ),
        ),
        candidate=TemplateCandidate("template", "llm"),
        validation=_validation(),
    )

    assert report.passed is True
    assert report.diagnostics == ()
    assert report.record_set_assessments == (
        {
            "name": "interface",
            "passed": True,
            "reason": (
                "Overall independent review passed; the provider omitted the "
                "per-record-set entry. All interface records are correctly parsed."
            ),
            "normalized_from_overall_review": True,
        },
    )


def test_reviewer_treats_single_sample_risk_as_non_blocking_when_result_is_correct():
    class Completions:
        @staticmethod
        def create(**_kwargs):
            return SimpleNamespace(
                model="deepseek-v4-pro",
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                '{"passed":false,'
                                '"summary":"All 59 interface rows are correctly parsed.",'
                                '"diagnostics":[{'
                                '"code":"other","sample_id":"sample-1",'
                                '"target":"template",'
                                '"message":"Only one frozen sample was provided; '
                                'cross-sample risk remains."}],'
                                '"primary_key_assessment":'
                                '{"passed":true,"reason":"stable interface key"},'
                                '"completeness_assessment":'
                                '{"passed":true,"reason":"all rows represented"}}'
                            )
                        )
                    )
                ],
            )

    report = IndependentParserResultReviewer(
        client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        model_name="deepseek-v4-pro",
    ).review(
        spec=SPEC,
        samples=(
            ParserSample(
                "sample-1",
                "raw",
                SampleRole.GENERATION,
                prompt_text="Eth1/1 up",
            ),
        ),
        candidate=TemplateCandidate("template", "ntc_templates"),
        validation=_validation(),
    )

    assert report.passed is True
    assert report.diagnostics == ()
    assert report.completeness_assessment["non_blocking_warnings"] == [
        "Only one frozen sample was provided; cross-sample risk remains."
    ]
    assert report.record_set_assessments[0]["normalized_from_overall_review"] is True


def test_reviewer_recognizes_structural_variant_coverage_wording():
    assert _is_non_blocking_sample_scope_diagnostic(
        {
            "code": "other",
            "target": "template",
            "message": (
                "Only one structural CLI variant is available; the template may "
                "need additional robustness testing for different column spacing."
            ),
        }
    )


def test_reviewer_preserves_textfsm_list_values_as_json_arrays():
    captured = {}

    class Completions:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                model="deepseek-v4-pro",
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            reasoning_content="checked the ordered list values",
                            content=(
                                '{"passed":true,"summary":"complete",'
                                '"diagnostics":[],"primary_key_assessment":'
                                '{"passed":true,"reason":"stable"},'
                                '"completeness_assessment":'
                                '{"passed":true,"reason":"all values represented"},'
                                '"record_set_assessments":['
                                '{"name":"vpc","passed":true,'
                                '"reason":"current values match"}]}'
                            ),
                        )
                    )
                ],
            )

    validation = ValidationReport(
        passed=True,
        hard_gates={"compiled": True},
        samples=(
            SampleValidation(
                sample_id="sample-vpc",
                role=SampleRole.GENERATION,
                passed=True,
                record_count=1,
                records=(
                    {
                        "id": "400",
                        "active_vlans": ["1-3967", "3969-4093"],
                    },
                ),
                errors=(),
                actual_primary_keys=(("400",),),
            ),
        ),
        diagnostics=(),
    )
    spec = ParserSpec(
        entity="vpc",
        primary_keys=("id",),
        fields=(
            FieldSpec("id", True, "number"),
            FieldSpec("active_vlans", True, "text"),
        ),
    )
    candidate = TemplateCandidate(
        (
            "Value id (\\d+)\n"
            "Value List active_vlans ([\\d,-]+)\n\n"
            "Start\n"
            "  ^${id}\\s+${active_vlans}$$ -> Record\n"
        ),
        "llm",
    )

    report = IndependentParserResultReviewer(
        client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        model_name="deepseek-v4-pro",
    ).review(
        spec=spec,
        samples=(
            ParserSample(
                "sample-vpc",
                "raw",
                SampleRole.GENERATION,
                prompt_text="400 1-3967\n    3969-4093",
            ),
        ),
        candidate=candidate,
        validation=validation,
    )

    prompt = captured["messages"][1]["content"]
    assert report.passed is True
    assert '["active_vlans"]' in prompt
    assert '"active_vlans": [' in prompt
    assert '"1-3967"' in prompt
    assert "['1-3967', '3969-4093']" not in prompt
    assert "one-based `value_index`" in prompt


def test_reviewer_does_not_rewrite_list_field_only_because_element_type_is_text():
    class Completions:
        @staticmethod
        def create(**_kwargs):
            return SimpleNamespace(
                model="deepseek-v4-pro",
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                '{"passed":false,'
                                '"summary":"All MAC records are complete.",'
                                '"diagnostics":[{'
                                '"code":"field_misaligned","sample_id":"",'
                                '"target":"template",'
                                '"message":"The interfaces field is declared as '
                                'Value List interfaces, but ParserSpec expects a scalar '
                                'text field."}],'
                                '"primary_key_assessment":'
                                '{"passed":true,"reason":"stable MAC key"},'
                                '"completeness_assessment":'
                                '{"passed":true,"reason":"all ports preserved"}}'
                            )
                        )
                    )
                ],
            )

    spec = ParserSpec(
        entity="mac_entry",
        primary_keys=("mac_address",),
        fields=(
            FieldSpec("mac_address", True, "text"),
            FieldSpec("interfaces", False, "text"),
        ),
    )
    validation = ValidationReport(
        passed=True,
        hard_gates={"compiled": True},
        samples=(
            SampleValidation(
                sample_id="sample-mac",
                role=SampleRole.GENERATION,
                passed=True,
                record_count=1,
                records=(
                    {
                        "mac_address": "0011.2233.4455",
                        "interfaces": ["Gi1/0/1", "Gi1/0/2"],
                    },
                ),
                errors=(),
                actual_primary_keys=(("0011.2233.4455",),),
            ),
        ),
        diagnostics=(),
    )
    candidate = TemplateCandidate(
        (
            "Value mac_address (\\S+)\n"
            "Value List interfaces (\\S+)\n\n"
            "Start\n"
            "  ^${mac_address}\\s+${interfaces}$$ -> Record\n"
        ),
        "ntc_templates",
    )

    report = IndependentParserResultReviewer(
        client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        model_name="deepseek-v4-pro",
    ).review(
        spec=spec,
        samples=(
            ParserSample(
                "sample-mac",
                "raw",
                SampleRole.GENERATION,
                prompt_text="0011.2233.4455 Gi1/0/1 Gi1/0/2",
            ),
        ),
        candidate=candidate,
        validation=validation,
    )

    assert report.passed is True
    assert report.diagnostics == ()
    assert report.completeness_assessment["non_blocking_warnings"] == [
        (
            "The interfaces field is declared as Value List interfaces, but "
            "ParserSpec expects a scalar text field."
        )
    ]

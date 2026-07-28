from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "src" / "skills" / "textfsm-generator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from result_reviewer import IndependentParserResultReviewer  # noqa: E402
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
                                '{"passed":true,"reason":"all rows represented"}}'
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
    assert "RAW MUST NOT BE SENT" not in captured["messages"][0]["content"]
    assert captured["messages"][1]["role"] == "user"
    assert '"record_count": 1' in captured["messages"][1]["content"]


def test_reviewer_reuses_stable_evidence_prefix_across_candidate_revisions():
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
                                '{"passed":true,"reason":"all rows represented"}}'
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

    assert requests[1][:3] == [
        requests[0][0],
        requests[0][1],
        {
            "role": "assistant",
            "content": requests[0][1]["content"].replace(
                requests[0][1]["content"],
                (
                    '{"passed":true,"summary":"complete",'
                    '"diagnostics":[],"primary_key_assessment":'
                    '{"passed":true,"reason":"stable"},'
                    '"completeness_assessment":'
                    '{"passed":true,"reason":"all rows represented"}}'
                ),
            ),
            "reasoning_content": "checked",
        },
    ]
    assert "candidate-2" in requests[1][-1]["content"]


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
                                '{"passed":true,"reason":"all values represented"}}'
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

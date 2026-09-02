from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "src" / "skills" / "textfsm-generator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from llm_generator import (  # noqa: E402
    DeepSeekTextFSMToolSession,
    ParserGenerationToolbox,
    PromptBundle,
)

from src.core.parser_generation import FieldSpec, ParserSample, ParserSpec, SampleRole  # noqa: E402


class FakeUsage:
    def __init__(self, **payload: int) -> None:
        self.payload = payload

    def model_dump(self, **_kwargs):
        return dict(self.payload)


class FakeToolCall:
    def __init__(self, call_id: str, name: str, arguments: dict) -> None:
        self.id = call_id
        self.type = "function"
        self.function = SimpleNamespace(
            name=name,
            arguments=json.dumps(arguments),
        )

    def model_dump(self, **_kwargs):
        return {
            "id": self.id,
            "type": self.type,
            "function": {
                "name": self.function.name,
                "arguments": self.function.arguments,
            },
        }


class FakeMessage:
    def __init__(
        self,
        *,
        content: str | None,
        reasoning_content: str,
        tool_calls: list[FakeToolCall] | None = None,
    ) -> None:
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = tool_calls


class FakeCompletions:
    def __init__(self, responses: list[SimpleNamespace | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _response(message: FakeMessage, *, tokens: int) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=FakeUsage(prompt_tokens=tokens, completion_tokens=3, total_tokens=tokens + 3),
        model="deepseek-v4-pro",
    )


def _bundle() -> PromptBundle:
    return PromptBundle(
        system="TextFSM dialect",
        task="Generate a parser from sanitized samples.",
        prompt_version="test",
        dialect_hash="test",
    )


def test_tool_loop_replays_reasoning_and_tool_result_without_forced_tool_choice():
    first_template = "Value item (\\S+)\n\nStart\n  ^${item} -> Record\n"
    second_template = "Value item (\\S+)\n\nStart\n  ^OK\\s+${item} -> Record\n"
    completions = FakeCompletions(
        [
            _response(
                FakeMessage(
                    content="",
                    reasoning_content="first reasoning",
                    tool_calls=[
                        FakeToolCall(
                            "call-1",
                            "validate_textfsm_candidate",
                            {"template_text": first_template},
                        )
                    ],
                ),
                tokens=10,
            ),
            _response(
                FakeMessage(
                    content="",
                    reasoning_content="repair reasoning",
                    tool_calls=[
                        FakeToolCall(
                            "call-2",
                            "validate_textfsm_candidate",
                            {"template_text": second_template},
                        )
                    ],
                ),
                tokens=20,
            ),
        ]
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    session = DeepSeekTextFSMToolSession(
        bundle=_bundle(),
        client=client,
        model_name="deepseek-v4-pro",
    )

    first = session.next()
    second = session.next(
        {
            "failure_type": "hard_validation_failed",
            "primary_diagnostic": {
                "code": "zero_records",
                "message": "sample produced no records",
            },
        }
    )

    assert first.candidate_text == first_template.strip()
    assert second.candidate_text == second_template.strip()
    assert len(completions.calls) == 2
    assert "tool_choice" not in completions.calls[0]
    assert "temperature" not in completions.calls[0]
    assert "top_p" not in completions.calls[0]
    assert completions.calls[0]["reasoning_effort"] == "high"
    assert completions.calls[0]["extra_body"] == {"thinking": {"type": "enabled"}}
    replayed = completions.calls[1]["messages"]
    assistant = next(item for item in replayed if item.get("role") == "assistant")
    assert assistant["reasoning_content"] == "first reasoning"
    assert assistant["tool_calls"][0]["id"] == "call-1"
    tool_result = next(item for item in replayed if item.get("role") == "tool")
    assert tool_result["tool_call_id"] == "call-1"
    assert "zero_records" in tool_result["content"]


def test_tool_loop_accepts_complete_template_from_content_as_safe_fallback():
    template = "Value item (\\S+)\n\nStart\n  ^${item} -> Record\n"
    completions = FakeCompletions(
        [
            _response(
                FakeMessage(
                    content=template,
                    reasoning_content="content fallback",
                    tool_calls=None,
                ),
                tokens=5,
            )
        ]
    )
    session = DeepSeekTextFSMToolSession(
        bundle=_bundle(),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model_name="deepseek-v4-pro",
    )

    turn = session.next()

    assert turn.candidate_text == template.strip()
    assert turn.submission_mode == "implicit_content"


def test_tool_loop_accepts_named_multi_record_parser_bundle():
    completions = FakeCompletions(
        [
            _response(
                FakeMessage(
                    content="",
                    reasoning_content="bundle reasoning",
                    tool_calls=[
                        FakeToolCall(
                            "call-bundle",
                            "validate_parser_bundle_candidate",
                            {
                                "record_sets": [
                                    {
                                        "name": "device_version",
                                        "template_text": (
                                            "Value version (\\S+)\n\n"
                                            "Start\n  ^Version: ${version} -> Record\n"
                                        ),
                                    },
                                    {
                                        "name": "slot_inventory",
                                        "template_text": (
                                            "Value slot (\\d+)\n\n"
                                            "Start\n  ^Slot ${slot} -> Record\n"
                                        ),
                                    },
                                ]
                            },
                        )
                    ],
                ),
                tokens=12,
            )
        ]
    )
    session = DeepSeekTextFSMToolSession(
        bundle=_bundle(),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model_name="deepseek-v4-pro",
        bundle_mode=True,
    )

    turn = session.next()

    payload = json.loads(turn.candidate_text)
    assert payload["format"] == "netops.parser_bundle/v1"
    assert [item["name"] for item in payload["record_sets"]] == [
        "device_version",
        "slot_inventory",
    ]
    assert turn.submission_mode == "native_bundle_tool_call"
    assert completions.calls[0]["tools"][0]["function"]["name"] == (
        "validate_parser_bundle_candidate"
    )


def test_tool_loop_rejects_conflicting_terminal_tools_in_one_turn():
    completions = FakeCompletions(
        [
            _response(
                FakeMessage(
                    content="",
                    reasoning_content="ambiguous",
                    tool_calls=[
                        FakeToolCall(
                            "call-1",
                            "validate_textfsm_candidate",
                            {"template_text": "first"},
                        ),
                        FakeToolCall(
                            "call-2",
                            "request_engineer_review",
                            {
                                "reason_code": "contract_conflict",
                                "explanation": "conflict",
                            },
                        ),
                    ],
                ),
                tokens=5,
            )
        ]
    )
    session = DeepSeekTextFSMToolSession(
        bundle=_bundle(),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model_name="deepseek-v4-pro",
    )

    turn = session.next()

    assert turn.candidate_text is None
    assert turn.protocol_error == "model requested conflicting terminal tools"


def test_tool_loop_executes_multiple_evidence_tools_before_submission():
    template = "Value item (\\S+)\n\nStart\n  ^OK\\s+${item} -> Record\n"

    class Toolbox:
        def __init__(self):
            self.calls: list[tuple[str, dict]] = []

        def execute(self, name, arguments):
            self.calls.append((name, arguments))
            return {"tool": name, "passed": True}

    toolbox = Toolbox()
    completions = FakeCompletions(
        [
            _response(
                FakeMessage(
                    content="",
                    reasoning_content="inspect then test",
                    tool_calls=[
                        FakeToolCall(
                            "read-1",
                            "read_sample_fragment",
                            {"sample_id": "generation-1", "start_line": 1, "end_line": 3},
                        ),
                        FakeToolCall(
                            "compile-1",
                            "compile_textfsm_candidate",
                            {"candidate_text": template},
                        ),
                    ],
                ),
                tokens=7,
            ),
            _response(
                FakeMessage(
                    content="",
                    reasoning_content="submit verified candidate",
                    tool_calls=[
                        FakeToolCall(
                            "submit-1",
                            "validate_textfsm_candidate",
                            {"template_text": template},
                        )
                    ],
                ),
                tokens=9,
            ),
        ]
    )
    session = DeepSeekTextFSMToolSession(
        bundle=_bundle(),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model_name="deepseek-v4-pro",
        toolbox=toolbox,
    )

    turn = session.next()

    assert turn.candidate_text == template.strip()
    assert [name for name, _arguments in toolbox.calls] == [
        "read_sample_fragment",
        "compile_textfsm_candidate",
    ]
    replayed = completions.calls[1]["messages"]
    tool_results = [item for item in replayed if item.get("role") == "tool"]
    assert [item["tool_call_id"] for item in tool_results] == ["read-1", "compile-1"]


def test_toolbox_compiles_and_replays_frozen_samples_with_production_runtime():
    spec = ParserSpec(
        entity="item_state",
        primary_keys=("item",),
        fields=(FieldSpec("item", True),),
    )
    sample = ParserSample("sample-1", "OK xe0\n", SampleRole.GENERATION)
    toolbox = ParserGenerationToolbox(
        spec=spec,
        samples=(sample,),
        vendor="H3C",
        command="display item",
    )
    candidate = "Value item (\\S+)\n\nStart\n  ^OK\\s+${item} -> Record\n"

    compiled = toolbox.execute("compile_textfsm_candidate", {"candidate_text": candidate})
    replayed = toolbox.execute("replay_textfsm_candidate", {"candidate_text": candidate})

    assert compiled["compiled"] is True
    assert replayed["passed"] is True
    assert replayed["samples"][0]["record_count"] == 1


def test_generic_provider_does_not_receive_deepseek_thinking_parameters():
    template = "Value item (\\S+)\n\nStart\n  ^OK\\s+${item} -> Record\n"
    completions = FakeCompletions(
        [
            _response(
                FakeMessage(
                    content=template,
                    reasoning_content="",
                    tool_calls=None,
                ),
                tokens=5,
            )
        ]
    )
    session = DeepSeekTextFSMToolSession(
        bundle=_bundle(),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model_name="gpt-test",
        provider_name="openai",
    )

    assert session.next().candidate_text == template.strip()
    assert "reasoning_effort" not in completions.calls[0]
    assert "extra_body" not in completions.calls[0]


def test_provider_error_is_returned_to_the_bounded_outer_loop():
    completions = FakeCompletions([RuntimeError("temporary provider failure")])
    session = DeepSeekTextFSMToolSession(
        bundle=_bundle(),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model_name="deepseek-v4-pro",
    )

    turn = session.next()

    assert turn.candidate_text is None
    assert turn.submission_mode == "provider_error"
    assert turn.protocol_error == (
        "provider_request_failed:RuntimeError: temporary provider failure"
    )
    assert turn.retryable is False
    assert len(completions.calls) == 1


def test_timeout_is_retryable_but_bad_request_is_not():
    class FakeProviderError(RuntimeError):
        def __init__(self, message: str, status_code: int) -> None:
            super().__init__(message)
            self.status_code = status_code

    timeout_session = DeepSeekTextFSMToolSession(
        bundle=_bundle(),
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions([FakeProviderError("timed out", 408)]))
        ),
        model_name="deepseek-v4-pro",
    )
    bad_request_session = DeepSeekTextFSMToolSession(
        bundle=_bundle(),
        client=SimpleNamespace(
            chat=SimpleNamespace(
                completions=FakeCompletions([FakeProviderError("invalid request", 400)])
            )
        ),
        model_name="deepseek-v4-pro",
    )

    assert timeout_session.next().retryable is True
    assert bad_request_session.next().retryable is False

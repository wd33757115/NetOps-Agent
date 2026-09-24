# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Generate and repair TextFSM templates with version-bound prompts."""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _ensure_import_path() -> None:
    root = Path(__file__).resolve().parents[7]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


_ensure_import_path()

from src.common.deadline import (  # noqa: E402
    WallClockTimeoutError,
    call_with_wall_clock_timeout,
)
from src.apps.rule_governance.backend.parser.parser_bundle import (  # noqa: E402
    ParserBundleError,
    build_parser_bundle,
    parse_parser_bundle,
)
from src.apps.rule_governance.backend.parser.textfsm_dialect import (  # noqa: E402
    DEFAULT_TEXTFSM_DIALECT,
    build_textfsm_system_prompt,
    dialect_hash,
)
from src.apps.rule_governance.backend.parser_generation import (  # noqa: E402
    ParserSample,
    ParserSpec,
    ParserSpecDocument,
    TextFSMValidationBackend,
    validate_template_execution,
)


@dataclass(frozen=True)
class PromptBundle:
    system: str
    task: str
    prompt_version: str
    dialect_hash: str

    def render(self) -> str:
        return (
            "<SYSTEM_INSTRUCTIONS>\n"
            f"{self.system}\n"
            "</SYSTEM_INSTRUCTIONS>\n\n"
            "<TASK>\n"
            f"{self.task}\n"
            "</TASK>"
        )


class LLMText(str):
    """String-compatible model output carrying provider usage metadata."""

    model_name: str | None
    token_usage: dict[str, Any]

    def __new__(
        cls,
        value: str,
        *,
        model_name: str | None = None,
        token_usage: dict[str, Any] | None = None,
    ) -> LLMText:
        instance = super().__new__(cls, value)
        instance.model_name = model_name
        instance.token_usage = dict(token_usage or {})
        return instance


MAX_TEMPLATE_LENGTH = 128_000
TEXTFSM_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "validate_textfsm_candidate",
            "description": (
                "Submit one complete TextFSM template to the local platform validator. "
                "The validator compiles it and checks all frozen generation and negative "
                "samples. It does not change the ParserSpec."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "template_text": {
                        "type": "string",
                        "description": "The complete TextFSM template without Markdown.",
                    }
                },
                "required": ["template_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_engineer_review",
            "description": (
                "Stop automatic generation when the frozen evidence cannot support a "
                "reliable TextFSM template."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason_code": {
                        "type": "string",
                        "enum": [
                            "insufficient_sample",
                            "contract_conflict",
                            "unsupported_layout",
                            "repeated_validation_failure",
                        ],
                    },
                    "explanation": {
                        "type": "string",
                        "description": "A concise engineering reason for stopping.",
                    },
                },
                "required": ["reason_code", "explanation"],
                "additionalProperties": False,
            },
        },
    },
]
PARSER_BUNDLE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "validate_parser_bundle_candidate",
            "description": (
                "Submit one complete TextFSM template for every frozen record set. "
                "The local platform compiles and validates every template against all "
                "samples as one immutable Parser bundle."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "record_sets": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 16,
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "template_text": {"type": "string"},
                            },
                            "required": ["name", "template_text"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["record_sets"],
                "additionalProperties": False,
            },
        },
    },
    TEXTFSM_TOOLS[1],
]

# These tools are deliberately evidence-only.  The agent can inspect and test a
# candidate but it cannot alter the frozen ParserSpec, business assets, or the
# production template registry.
AGENT_EVIDENCE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_sample_fragment",
            "description": "Read a bounded line range from a frozen sanitized CLI sample.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sample_id": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                "required": ["sample_id", "start_line", "end_line"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_published_templates",
            "description": (
                "Find up to three active or bundled TextFSM templates similar to the command."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compile_textfsm_candidate",
            "description": (
                "Compile a TextFSM template or Parser bundle using the production runtime."
            ),
            "parameters": {
                "type": "object",
                "properties": {"candidate_text": {"type": "string"}},
                "required": ["candidate_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replay_textfsm_candidate",
            "description": (
                "Run a candidate against every frozen sanitized sample and return field, key, "
                "and parse evidence."
            ),
            "parameters": {
                "type": "object",
                "properties": {"candidate_text": {"type": "string"}},
                "required": ["candidate_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_last_validation",
            "description": "Read the compact result of the latest compile or replay tool call.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
]


@dataclass(frozen=True)
class TextFSMToolTurn:
    candidate_text: str | None
    submission_mode: str
    model_name: str | None
    token_usage: dict[str, int]
    latency_ms: int
    review_reason: str | None = None
    protocol_error: str | None = None
    retryable: bool = False


class ParserGenerationToolbox:
    """Small local evidence surface exposed to a parser-generation model."""

    def __init__(
        self,
        *,
        spec: ParserSpec | ParserSpecDocument,
        samples: tuple[ParserSample, ...],
        vendor: str,
        command: str,
    ) -> None:
        self.spec = spec
        self.samples = samples
        self.vendor = vendor
        self.command = command
        self.backend = TextFSMValidationBackend()
        self.last_validation: dict[str, Any] | None = None

    @staticmethod
    def _compact_report(report: Any) -> dict[str, Any]:
        return {
            "passed": bool(report.passed),
            "hard_gates": dict(report.hard_gates),
            "diagnostics": list(report.diagnostics)[:20],
            "samples": [
                {
                    "sample_id": sample.sample_id,
                    "role": sample.role.value,
                    "record_count": sample.record_count,
                    "primary_keys": [list(key) for key in sample.actual_primary_keys[:20]],
                    "errors": list(sample.errors)[:10],
                }
                for sample in report.samples[:20]
            ],
        }

    def _read_sample_fragment(self, arguments: dict[str, Any]) -> dict[str, Any]:
        sample_id = str(arguments.get("sample_id") or "")
        sample = next((item for item in self.samples if item.sample_id == sample_id), None)
        if sample is None:
            return {"error": f"unknown sample_id: {sample_id}"}
        start = max(1, int(arguments.get("start_line") or 1))
        end = min(start + 199, max(start, int(arguments.get("end_line") or start)))
        lines = sample.validation_text.splitlines()
        return {
            "sample_id": sample.sample_id,
            "line_start": start,
            "line_end": min(end, len(lines)),
            "text": "\n".join(lines[start - 1 : end]),
        }

    def _search_published_templates(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        tokens = {
            token.casefold()
            for token in re.findall(r"[A-Za-z0-9_/-]+", f"{self.vendor} {self.command} {query}")
            if len(token) > 2
        }
        matches: list[dict[str, Any]] = []
        try:
            from src.apps.rule_governance.backend.parser.repository import ParserTemplateRepository

            active = ParserTemplateRepository().list_templates(status="active")
        except Exception:
            active = []
        for item in active:
            haystack = " ".join(
                str(item.get(name) or "")
                for name in ("vendor", "profile", "canonical_command", "category")
            ).casefold()
            if tokens and not any(token in haystack for token in tokens):
                continue
            text = str(item.get("template_text") or "")
            matches.append(
                {
                    "source": "published",
                    "id": str(item.get("id") or ""),
                    "vendor": item.get("vendor"),
                    "profile": item.get("profile"),
                    "command": item.get("canonical_command"),
                    "template_excerpt": text[:6_000],
                }
            )
            if len(matches) >= 3:
                return {"query": query, "matches": matches}
        from src.apps.rule_governance.paths import TEMPLATE_STORE

        template_root = TEMPLATE_STORE
        if template_root.exists():
            for path in template_root.rglob("*.textfsm"):
                haystack = path.as_posix().casefold()
                if tokens and not any(token in haystack for token in tokens):
                    continue
                matches.append(
                    {
                        "source": "bundled",
                        "path": str(path.relative_to(template_root)),
                        "template_excerpt": path.read_text(encoding="utf-8")[:6_000],
                    }
                )
                if len(matches) >= 3:
                    break
        return {"query": query, "matches": matches}

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "read_sample_fragment":
            return self._read_sample_fragment(arguments)
        if name == "search_published_templates":
            return self._search_published_templates(arguments)
        if name in {"compile_textfsm_candidate", "replay_textfsm_candidate"}:
            candidate_text = str(arguments.get("candidate_text") or "").strip()
            if not candidate_text:
                return {"error": f"{name} requires non-empty candidate_text"}
            report = validate_template_execution(
                candidate_text,
                self.spec,
                self.samples,
                self.backend,
            )
            self.last_validation = self._compact_report(report)
            if name == "compile_textfsm_candidate":
                return {
                    "compiled": bool(report.hard_gates.get("compiled")),
                    "diagnostics": list(report.diagnostics)[:20],
                }
            return self.last_validation
        if name == "inspect_last_validation":
            return self.last_validation or {"error": "no compile or replay result is available"}
        return {"error": f"unsupported evidence tool: {name}"}


def _provider_error_is_retryable(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        return status_code in {408, 409, 425, 429} or status_code >= 500
    error_name = type(exc).__name__.casefold()
    if any(
        marker in error_name
        for marker in ("badrequest", "authentication", "permission", "notfound", "unprocessable")
    ):
        return False
    return any(
        marker in error_name
        for marker in ("timeout", "connection", "ratelimit", "internalserver", "apiconnection")
    )


def _tool_call_payload(tool_call: Any) -> dict[str, Any]:
    dump = getattr(tool_call, "model_dump", None)
    if callable(dump):
        return dict(dump(exclude_none=True))
    function = getattr(tool_call, "function", None)
    return {
        "id": str(getattr(tool_call, "id", "")),
        "type": str(getattr(tool_call, "type", "function")),
        "function": {
            "name": str(getattr(function, "name", "")),
            "arguments": str(getattr(function, "arguments", "{}")),
        },
    }


def _assistant_payload(message: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "role": "assistant",
        "content": getattr(message, "content", None),
    }
    reasoning_content = getattr(message, "reasoning_content", None)
    if reasoning_content is not None:
        payload["reasoning_content"] = reasoning_content
    tool_calls = list(getattr(message, "tool_calls", None) or [])
    if tool_calls:
        payload["tool_calls"] = [_tool_call_payload(item) for item in tool_calls]
    return payload


class ProviderTextFSMToolSession:
    """Provider-neutral, bounded TextFSM evidence and repair session."""

    def __init__(
        self,
        *,
        bundle: PromptBundle,
        client: Any,
        model_name: str,
        provider_name: str = "deepseek",
        bundle_mode: bool = False,
        toolbox: ParserGenerationToolbox | None = None,
        hard_timeout_seconds: int = 480,
        max_completion_tokens: int = 32768,
        max_tool_rounds: int = 6,
    ) -> None:
        self.client = client
        self.model_name = model_name
        self.provider_name = str(provider_name or "generic").casefold()
        self.bundle_mode = bool(bundle_mode)
        self.toolbox = toolbox
        self.hard_timeout_seconds = max(1, int(hard_timeout_seconds))
        self.max_completion_tokens = max(1024, int(max_completion_tokens))
        self.max_tool_rounds = max(1, int(max_tool_rounds))
        self.tools = [
            *(PARSER_BUNDLE_TOOLS if self.bundle_mode else TEXTFSM_TOOLS),
            *(AGENT_EVIDENCE_TOOLS if self.toolbox is not None else ()),
        ]
        submission_instruction = (
            "Generate one complete TextFSM template for every frozen record set, "
            "then call validate_parser_bundle_candidate with all templates."
            if self.bundle_mode
            else (
                "Generate a complete template, then call "
                "validate_textfsm_candidate with that template."
            )
        )
        self.messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    f"{bundle.system}\n\n"
                    "You are in a bounded local evidence-and-validation loop. "
                    f"{submission_instruction} "
                    "Use evidence tools to inspect samples, find templates, compile, "
                    "and replay candidates. "
                    "Before submitting a candidate, compile and replay it against frozen samples. "
                    "Tool results are evidence; repair the same frozen contract after failures. "
                    "Never invent or modify fields. If the evidence "
                    "cannot support a reliable parser, call request_engineer_review. "
                    "Do not return explanations or Markdown."
                ),
            },
            {"role": "user", "content": bundle.task},
        ]
        self._pending_tool_call_ids: list[str] = []

    def _append_feedback(self, feedback: dict[str, object]) -> None:
        content = json.dumps(
            feedback,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        if self._pending_tool_call_ids:
            for tool_call_id in self._pending_tool_call_ids:
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": content,
                    }
                )
            self._pending_tool_call_ids = []
            return
        self.messages.append(
            {
                "role": "user",
                "content": f"<LOCAL_VALIDATION_RESULT>{content}</LOCAL_VALIDATION_RESULT>",
            }
        )

    def _append_tool_results(self, results: list[tuple[str, dict[str, Any]]]) -> None:
        for tool_call_id, result in results:
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(
                        result,
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ),
                }
            )

    @staticmethod
    def _arguments(tool_call: Any) -> dict[str, Any]:
        function = getattr(tool_call, "function", None)
        raw = str(getattr(function, "arguments", "") or "")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("tool arguments must be a JSON object")
        return parsed

    def _request(self) -> Any:
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": self.messages,
            "tools": self.tools,
            "max_tokens": self.max_completion_tokens,
        }
        # DeepSeek's thinking fields are not part of the OpenAI-compatible
        # baseline.  Keep them isolated so OpenAI or another compatible
        # provider can use the same agent without receiving vendor parameters.
        if self.provider_name == "deepseek":
            payload["reasoning_effort"] = "high"
            payload["extra_body"] = {"thinking": {"type": "enabled"}}
        return call_with_wall_clock_timeout(
            lambda: self.client.chat.completions.create(**payload),
            timeout_seconds=self.hard_timeout_seconds,
            operation="Parser model request",
        )

    def _submission_turn(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        response_model: str,
        token_usage: dict[str, int],
        latency_ms: int,
    ) -> TextFSMToolTurn:
        if tool_name == "validate_textfsm_candidate":
            if self.bundle_mode:
                return TextFSMToolTurn(
                    None,
                    "tool_protocol_error",
                    response_model,
                    token_usage,
                    latency_ms,
                    protocol_error="single-template submission is not valid for a Parser bundle",
                )
            template_text = str(arguments.get("template_text") or "").strip()
            if not template_text:
                error = "validate_textfsm_candidate requires a non-empty template"
            elif len(template_text) > MAX_TEMPLATE_LENGTH:
                error = (
                    f"validate_textfsm_candidate template exceeds {MAX_TEMPLATE_LENGTH} characters"
                )
            else:
                return TextFSMToolTurn(
                    template_text, "native_tool_call", response_model, token_usage, latency_ms
                )
            return TextFSMToolTurn(
                None,
                "tool_protocol_error",
                response_model,
                token_usage,
                latency_ms,
                protocol_error=error,
            )
        if tool_name == "validate_parser_bundle_candidate":
            if not self.bundle_mode:
                return TextFSMToolTurn(
                    None,
                    "tool_protocol_error",
                    response_model,
                    token_usage,
                    latency_ms,
                    protocol_error="Parser bundle submission is not valid for a single record set",
                )
            raw_sets = arguments.get("record_sets")
            if not isinstance(raw_sets, list):
                return TextFSMToolTurn(
                    None,
                    "tool_protocol_error",
                    response_model,
                    token_usage,
                    latency_ms,
                    protocol_error="validate_parser_bundle_candidate requires record_sets",
                )
            try:
                if len(raw_sets) != len([item for item in raw_sets if isinstance(item, dict)]):
                    raise ParserBundleError("record_sets must contain only objects")
                names = [str(item.get("name") or "") for item in raw_sets]
                if len(names) != len(set(names)):
                    raise ParserBundleError("record_sets contains duplicate names")
                candidate = build_parser_bundle(
                    {
                        str(item.get("name") or ""): str(item.get("template_text") or "")
                        for item in raw_sets
                    }
                ).serialize()
            except (ParserBundleError, ValueError) as exc:
                return TextFSMToolTurn(
                    None,
                    "tool_protocol_error",
                    response_model,
                    token_usage,
                    latency_ms,
                    protocol_error=str(exc),
                )
            return TextFSMToolTurn(
                candidate, "native_bundle_tool_call", response_model, token_usage, latency_ms
            )
        reason_code = str(arguments.get("reason_code") or "").strip()
        explanation = str(arguments.get("explanation") or "").strip()
        reason = f"{reason_code}: {explanation}" if explanation else reason_code
        return TextFSMToolTurn(
            None,
            "engineer_review",
            response_model,
            token_usage,
            latency_ms,
            review_reason=reason or "unspecified",
        )

    def next(
        self,
        validation_feedback: dict[str, object] | None = None,
    ) -> TextFSMToolTurn:
        if validation_feedback is not None:
            self._append_feedback(validation_feedback)

        started = time.monotonic()
        token_usage: dict[str, int] = {}
        response_model = self.model_name
        for _round in range(self.max_tool_rounds):
            try:
                response = self._request()
            except WallClockTimeoutError as exc:
                return TextFSMToolTurn(
                    None,
                    "provider_error",
                    self.model_name,
                    token_usage,
                    round((time.monotonic() - started) * 1000),
                    protocol_error=f"model_timeout: {exc}",
                )
            except Exception as exc:
                detail = " ".join(str(exc).split())[:500]
                return TextFSMToolTurn(
                    None,
                    "provider_error",
                    self.model_name,
                    token_usage,
                    round((time.monotonic() - started) * 1000),
                    protocol_error=f"provider_request_failed:{type(exc).__name__}: {detail}",
                    retryable=_provider_error_is_retryable(exc),
                )
            choices = list(getattr(response, "choices", None) or [])
            latency_ms = round((time.monotonic() - started) * 1000)
            if not choices or getattr(choices[0], "message", None) is None:
                return TextFSMToolTurn(
                    None,
                    "tool_protocol_error",
                    str(getattr(response, "model", "") or self.model_name),
                    token_usage,
                    latency_ms,
                    protocol_error="model response contains no assistant message",
                )
            message = choices[0].message
            self.messages.append(_assistant_payload(message))
            usage_payload = (
                response.usage.model_dump(exclude_none=True)
                if getattr(response, "usage", None) is not None
                else {}
            )
            for key, value in dict(usage_payload).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    token_usage[str(key)] = token_usage.get(str(key), 0) + int(value)
            response_model = str(getattr(response, "model", "") or self.model_name)
            tool_calls = list(getattr(message, "tool_calls", None) or [])
            if not tool_calls:
                content = str(getattr(message, "content", "") or "").strip()
                if not content:
                    return TextFSMToolTurn(
                        None,
                        "implicit_content",
                        response_model,
                        token_usage,
                        latency_ms,
                        protocol_error="model returned no template or tool call",
                    )
                if len(content) > MAX_TEMPLATE_LENGTH:
                    return TextFSMToolTurn(
                        None,
                        "tool_protocol_error",
                        response_model,
                        token_usage,
                        latency_ms,
                        protocol_error=(
                            f"implicit template exceeds {MAX_TEMPLATE_LENGTH} characters"
                        ),
                    )
                if self.bundle_mode:
                    try:
                        content = parse_parser_bundle(content).serialize()
                    except ParserBundleError as exc:
                        return TextFSMToolTurn(
                            None,
                            "tool_protocol_error",
                            response_model,
                            token_usage,
                            latency_ms,
                            protocol_error=str(exc)
                            or "implicit Parser bundle must use netops.parser_bundle/v1 JSON",
                        )
                return TextFSMToolTurn(
                    content, "implicit_content", response_model, token_usage, latency_ms
                )

            terminal: list[tuple[Any, str, dict[str, Any]]] = []
            evidence_results: list[tuple[str, dict[str, Any]]] = []
            for tool_call in tool_calls:
                call_id = str(getattr(tool_call, "id", "") or "")
                function = getattr(tool_call, "function", None)
                tool_name = str(getattr(function, "name", "") or "")
                try:
                    arguments = self._arguments(tool_call)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    evidence_results.append(
                        (call_id, {"error": f"invalid {tool_name or 'tool'} arguments: {exc}"})
                    )
                    continue
                if tool_name in {
                    "validate_textfsm_candidate",
                    "validate_parser_bundle_candidate",
                    "request_engineer_review",
                }:
                    terminal.append((tool_call, tool_name, arguments))
                elif self.toolbox is not None and tool_name in {
                    item["function"]["name"] for item in AGENT_EVIDENCE_TOOLS
                }:
                    evidence_results.append((call_id, self.toolbox.execute(tool_name, arguments)))
                else:
                    display_name = tool_name or "(missing name)"
                    evidence_results.append(
                        (call_id, {"error": f"unsupported tool: {display_name}"})
                    )
            if len(terminal) > 1:
                return TextFSMToolTurn(
                    None,
                    "tool_protocol_error",
                    response_model,
                    token_usage,
                    latency_ms,
                    protocol_error="model requested conflicting terminal tools",
                )
            if evidence_results:
                self._append_tool_results(evidence_results)
            if terminal:
                tool_call, tool_name, arguments = terminal[0]
                self._pending_tool_call_ids = [str(getattr(tool_call, "id", "") or "")]
                return self._submission_turn(
                    tool_name=tool_name,
                    arguments=arguments,
                    response_model=response_model,
                    token_usage=token_usage,
                    latency_ms=latency_ms,
                )
            # Every non-terminal tool call now has a concrete local result in
            # the conversation. Continue immediately so the model can repair
            # from evidence instead of forcing the outer service to invent a prompt.
        return TextFSMToolTurn(
            None,
            "tool_protocol_error",
            response_model,
            token_usage,
            round((time.monotonic() - started) * 1000),
            protocol_error="agent evidence tool-round budget exhausted",
        )


def create_textfsm_tool_session(
    bundle: PromptBundle,
    *,
    client: Any | None = None,
    bundle_mode: bool = False,
    toolbox: ParserGenerationToolbox | None = None,
) -> ProviderTextFSMToolSession:
    _ensure_import_path()
    from src.common.config import get_settings

    settings = get_settings()
    provider_name = str(settings.LLM_PROVIDER or "deepseek").strip().casefold()
    if client is None:
        from openai import OpenAI

        if provider_name == "deepseek":
            api_key = str(settings.DEEPSEEK_API_KEY or "").strip()
            base_url = str(settings.DEEPSEEK_BASE_URL or "").strip()
        elif provider_name == "openai":
            api_key = str(settings.OPENAI_API_KEY or "").strip()
            base_url = str(settings.OPENAI_BASE_URL or "").strip()
        else:
            api_key = str(settings.LLM_API_KEY or "").strip()
            base_url = str(settings.LLM_BASE_URL or "").strip()
        if not api_key:
            raise RuntimeError(f"{provider_name} API key is not configured")
        client = OpenAI(
            api_key=api_key,
            base_url=base_url or None,
            timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )
    return ProviderTextFSMToolSession(
        bundle=bundle,
        client=client,
        model_name=settings.LLM_MODEL,
        provider_name=provider_name,
        bundle_mode=bundle_mode,
        toolbox=toolbox,
        hard_timeout_seconds=settings.PARSER_MODEL_REQUEST_HARD_TIMEOUT_SECONDS,
        max_completion_tokens=settings.PARSER_LLM_MAX_COMPLETION_TOKENS,
        max_tool_rounds=settings.PARSER_AGENT_MAX_TOOL_ROUNDS,
    )


# Backward-compatible import name for integrations and historical tests.  New
# code must use ProviderTextFSMToolSession; no runtime behavior is DeepSeek-only.
DeepSeekTextFSMToolSession = ProviderTextFSMToolSession


def _compact_parse_plan(parse_plan: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(parse_plan or {})
    samples: list[dict[str, Any]] = []
    for sample in raw.get("samples") or []:
        item = dict(sample or {})
        samples.append(
            {
                key: item.get(key)
                for key in (
                    "layout_kind",
                    "confidence",
                    "record_boundary",
                    "estimated_entities",
                    "header_tokens",
                    "negative_classification",
                    "plan_hash",
                )
                if item.get(key) is not None
            }
            | {
                "evidence": list(item.get("evidence") or [])[:8],
                "table_regions": list(item.get("table_regions") or [])[:8],
            }
        )
    return {
        key: raw.get(key)
        for key in ("profiler_version", "dominant_layout", "layout_counts")
        if raw.get(key) is not None
    } | {"samples": samples}


def _compact_field_bindings(
    field_bindings: dict[str, Any] | None,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for field_name, raw in dict(field_bindings or {}).items():
        items = raw if isinstance(raw, list) else [raw]
        output[str(field_name)] = [dict(item) for item in items[:4] if isinstance(item, dict)]
    return output


def build_task_prompt(
    *,
    vendor: str,
    model: str,
    command: str,
    cli_output: str,
    required_fields: list[str],
    optional_fields: list[str] | None = None,
    primary_keys: list[str] | None = None,
    validators: dict[str, dict[str, Any]] | None = None,
    parse_plan: dict[str, Any] | None = None,
    field_bindings: dict[str, Any] | None = None,
    allow_empty: bool = False,
    empty_patterns: list[str] | None = None,
) -> str:
    contract = {
        "required_fields": list(required_fields),
        "optional_fields": list(optional_fields or []),
        "primary_keys": list(primary_keys or []),
        "validators": dict(validators or {}),
        "field_bindings": _compact_field_bindings(field_bindings),
        "allow_empty": bool(allow_empty),
        "empty_patterns": list(empty_patterns or []),
    }
    return f"""Create one TextFSM template for the supplied contract and samples.

Vendor hint: {vendor}
Profile hint: {model}
Canonical command: {command}
Entity primary keys: {", ".join(primary_keys or []) or "(none)"}
Frozen contract:
{json.dumps(contract, ensure_ascii=True, sort_keys=True, indent=2)}

Structure evidence:
{json.dumps(_compact_parse_plan(parse_plan), ensure_ascii=True, sort_keys=True, indent=2)}

The contract is immutable during this template action. Every emitted record must
contain all required fields and every primary key. Parse all evidenced entities,
including entities whose optional measurements are absent. Ignore headings,
legends, prompts, totals, and process-detail rows unless the contract needs them.
A zero-record result is valid only when the validator supplies a proven legal-empty
classification.

Sanitized training samples:
{cli_output}

The platform may replace a repeated middle section with a
<CLI_SAMPLE_EXCERPT_OMITTED .../> marker to bound prompt size. That marker is
metadata, not CLI evidence: ignore it and never create a TextFSM rule for it.
"""


def build_prompt_bundle(
    *,
    vendor: str,
    model: str,
    command: str,
    cli_output: str,
    required_fields: list[str],
    optional_fields: list[str] | None = None,
    primary_keys: list[str] | None = None,
    validators: dict[str, dict[str, Any]] | None = None,
    parse_plan: dict[str, Any] | None = None,
    field_bindings: dict[str, Any] | None = None,
    allow_empty: bool = False,
    empty_patterns: list[str] | None = None,
    repair_diagnostic: dict[str, Any] | None = None,
    current_template: str | None = None,
) -> PromptBundle:
    task = build_task_prompt(
        vendor=vendor,
        model=model,
        command=command,
        cli_output=cli_output,
        required_fields=required_fields,
        optional_fields=optional_fields,
        primary_keys=primary_keys,
        validators=validators,
        parse_plan=parse_plan,
        field_bindings=field_bindings,
        allow_empty=allow_empty,
        empty_patterns=empty_patterns,
    )
    if current_template:
        diagnostic = dict(repair_diagnostic or {})
        task += f"""

Repair the current best candidate. Change only what the diagnostic permits.
Do not weaken or change the frozen contract.

<CURRENT_TEMPLATE>
{current_template}
</CURRENT_TEMPLATE>

<VALIDATION_DIAGNOSTIC>
{json.dumps(diagnostic, ensure_ascii=True, sort_keys=True, indent=2)}
</VALIDATION_DIAGNOSTIC>
"""
    profile = DEFAULT_TEXTFSM_DIALECT
    return PromptBundle(
        system=build_textfsm_system_prompt(profile),
        task=task.strip(),
        prompt_version=profile.prompt_version,
        dialect_hash=dialect_hash(profile),
    )


def build_parser_bundle_prompt_bundle(
    *,
    vendor: str,
    model: str,
    command: str,
    cli_output: str,
    parser_spec: dict[str, Any],
    parse_plan: dict[str, Any] | None = None,
) -> PromptBundle:
    task = f"""Create one immutable Parser bundle for the supplied command.

Vendor hint: {vendor}
Profile hint: {model}
Canonical command: {command}

Frozen ParserSpec v2:
{json.dumps(parser_spec, ensure_ascii=True, sort_keys=True, indent=2)}

Structure evidence:
{json.dumps(_compact_parse_plan(parse_plan), ensure_ascii=True, sort_keys=True, indent=2)}

Each record_set is an independent business table over the same CLI output. Generate
exactly one complete TextFSM template for every record_set and submit all of them in
one validate_parser_bundle_candidate call. Record-set names must match the frozen
ParserSpec exactly. A template may ignore sections owned by another record set.
Every emitted record must contain that record set's required fields and primary keys.
For presence=optional record sets, emit zero records when that source branch is absent;
never fabricate a placeholder record. For presence=required record sets, every positive
sample must produce a record unless its explicit valid-empty pattern matches.
Capture every primary-key value from the current source record. Never synthesize a
missing key and never let a Filldown child key leak from a previous parent block. Do
not emit a record whose required values are empty merely to flush state. If the frozen
ParserSpec requires a value that the source record does not contain, call
request_engineer_review with reason_code=contract_conflict instead of inventing data.
Expected values are literal capture targets, not semantic labels. Do not emit normalized
constants such as present, healthy, unsupported, primary, or active unless that exact
token occurs in the corresponding source record. Capture the visible source phrase and
leave enum normalization to the later CheckItem / CommandAdapter V2.
For number or percent fields with unavailable_tokens, capture both numeric values and
the declared source tokens exactly. The runtime maps those tokens to unavailable/null
semantics after parsing; do not replace them with 0 and do not change the field to text.
Do not pivot columns into synthetic rows or use a column heading as a captured record
value. TextFSM output must preserve a source-backed row or repeated-block grain;
cross-record-set unions and unpivoting happen after Parser approval.
Do not flatten different entity grains into one sparse record. Do not rename, add or
remove fields, and do not return Markdown or explanations.

Sanitized training samples:
{cli_output}

The platform may replace a repeated middle section with a
<CLI_SAMPLE_EXCERPT_OMITTED .../> marker to bound prompt size. That marker is
metadata, not CLI evidence: ignore it and never create a TextFSM rule for it.
"""
    profile = DEFAULT_TEXTFSM_DIALECT
    return PromptBundle(
        system=build_textfsm_system_prompt(profile),
        task=task.strip(),
        prompt_version=profile.prompt_version,
        dialect_hash=dialect_hash(profile),
    )


def call_llm(prompt: str | PromptBundle) -> str:
    _ensure_import_path()
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_deepseek import ChatDeepSeek

    from src.common.config import get_settings

    settings = get_settings()
    llm = ChatDeepSeek(
        model=settings.LLM_MODEL,
        api_key=settings.DEEPSEEK_API_KEY,
        request_timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
        max_retries=0,
    )
    if isinstance(prompt, PromptBundle):
        request = [SystemMessage(content=prompt.system), HumanMessage(content=prompt.task)]
    else:
        request = prompt
    response = llm.invoke(request)
    response_metadata = dict(getattr(response, "response_metadata", None) or {})
    token_usage = dict(getattr(response, "usage_metadata", None) or {})
    if not token_usage:
        token_usage = dict(response_metadata.get("token_usage") or {})
    return LLMText(
        str(response.content or "").strip(),
        model_name=str(response_metadata.get("model_name") or settings.LLM_MODEL),
        token_usage=token_usage,
    )

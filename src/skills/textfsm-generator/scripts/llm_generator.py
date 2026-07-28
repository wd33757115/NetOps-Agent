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
    root = Path(__file__).resolve().parents[4]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


_ensure_import_path()

from src.core.parser.textfsm_dialect import (  # noqa: E402
    DEFAULT_TEXTFSM_DIALECT,
    build_textfsm_system_prompt,
    dialect_hash,
)
from src.core.parser.parser_bundle import (  # noqa: E402
    ParserBundleError,
    build_parser_bundle,
    parse_parser_bundle,
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


class DeepSeekTextFSMToolSession:
    """Explicit DeepSeek thinking/tool loop that preserves assistant messages."""

    def __init__(
        self,
        *,
        bundle: PromptBundle,
        client: Any,
        model_name: str,
        bundle_mode: bool = False,
    ) -> None:
        self.client = client
        self.model_name = model_name
        self.bundle_mode = bool(bundle_mode)
        self.tools = PARSER_BUNDLE_TOOLS if self.bundle_mode else TEXTFSM_TOOLS
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
                    "You are in a bounded local-validation loop. "
                    f"{submission_instruction} "
                    "After a failed tool result, repair the same frozen contract and call "
                    "the validator again. Never invent or modify fields. If the evidence "
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

    @staticmethod
    def _arguments(tool_call: Any) -> dict[str, Any]:
        function = getattr(tool_call, "function", None)
        raw = str(getattr(function, "arguments", "") or "")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("tool arguments must be a JSON object")
        return parsed

    def next(
        self,
        validation_feedback: dict[str, object] | None = None,
    ) -> TextFSMToolTurn:
        if validation_feedback is not None:
            self._append_feedback(validation_feedback)

        started = time.monotonic()
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=self.messages,
                tools=self.tools,
                reasoning_effort="high",
                extra_body={"thinking": {"type": "enabled"}},
            )
        except Exception as exc:
            latency_ms = round((time.monotonic() - started) * 1000)
            detail = " ".join(str(exc).split())[:500]
            return TextFSMToolTurn(
                None,
                "provider_error",
                self.model_name,
                {},
                latency_ms,
                protocol_error=f"provider_request_failed:{type(exc).__name__}: {detail}",
                retryable=_provider_error_is_retryable(exc),
            )
        latency_ms = round((time.monotonic() - started) * 1000)
        choices = list(getattr(response, "choices", None) or [])
        if not choices or getattr(choices[0], "message", None) is None:
            return TextFSMToolTurn(
                None,
                "tool_protocol_error",
                str(getattr(response, "model", "") or self.model_name),
                {},
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
        token_usage = {
            str(key): int(value)
            for key, value in dict(usage_payload).items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        response_model = str(getattr(response, "model", "") or self.model_name)
        tool_calls = list(getattr(message, "tool_calls", None) or [])
        self._pending_tool_call_ids = [str(getattr(item, "id", "") or "") for item in tool_calls]
        if len(tool_calls) > 1:
            return TextFSMToolTurn(
                None,
                "tool_protocol_error",
                response_model,
                token_usage,
                latency_ms,
                protocol_error="model requested multiple tools in one turn",
            )
        if tool_calls:
            tool_call = tool_calls[0]
            function = getattr(tool_call, "function", None)
            tool_name = str(getattr(function, "name", "") or "")
            try:
                arguments = self._arguments(tool_call)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                return TextFSMToolTurn(
                    None,
                    "tool_protocol_error",
                    response_model,
                    token_usage,
                    latency_ms,
                    protocol_error=f"invalid {tool_name or 'tool'} arguments: {exc}",
                )
            if tool_name == "validate_textfsm_candidate":
                if self.bundle_mode:
                    error = "single-template submission is not valid for a Parser bundle"
                    return TextFSMToolTurn(
                        None,
                        "tool_protocol_error",
                        response_model,
                        token_usage,
                        latency_ms,
                        protocol_error=error,
                    )
                template_text = str(arguments.get("template_text") or "").strip()
                if not template_text:
                    error = "validate_textfsm_candidate requires a non-empty template"
                elif len(template_text) > MAX_TEMPLATE_LENGTH:
                    error = (
                        "validate_textfsm_candidate template exceeds "
                        f"{MAX_TEMPLATE_LENGTH} characters"
                    )
                else:
                    return TextFSMToolTurn(
                        template_text,
                        "native_tool_call",
                        response_model,
                        token_usage,
                        latency_ms,
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
                        protocol_error=(
                            "Parser bundle submission is not valid for a single record set"
                        ),
                    )
                raw_sets = arguments.get("record_sets")
                if not isinstance(raw_sets, list):
                    error = "validate_parser_bundle_candidate requires record_sets"
                else:
                    try:
                        if len(raw_sets) != len(
                            [
                                item for item in raw_sets if isinstance(item, dict)
                            ]
                        ):
                            raise ParserBundleError(
                                "record_sets must contain only objects"
                            )
                        names = [
                            str(item.get("name") or "")
                            for item in raw_sets
                        ]
                        if len(names) != len(set(names)):
                            raise ParserBundleError(
                                "record_sets contains duplicate names"
                            )
                        candidate = build_parser_bundle(
                            {
                                str(item.get("name") or ""): str(
                                    item.get("template_text") or ""
                                )
                                for item in raw_sets
                            }
                        ).serialize()
                    except (ParserBundleError, ValueError) as exc:
                        error = str(exc)
                    else:
                        return TextFSMToolTurn(
                            candidate,
                            "native_bundle_tool_call",
                            response_model,
                            token_usage,
                            latency_ms,
                        )
                return TextFSMToolTurn(
                    None,
                    "tool_protocol_error",
                    response_model,
                    token_usage,
                    latency_ms,
                    protocol_error=error,
                )
            if tool_name == "request_engineer_review":
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
            return TextFSMToolTurn(
                None,
                "tool_protocol_error",
                response_model,
                token_usage,
                latency_ms,
                protocol_error=f"unsupported tool: {tool_name or '(missing name)'}",
            )

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
                bundle = parse_parser_bundle(content)
            except ParserBundleError as exc:
                bundle = None
                bundle_error = str(exc)
            else:
                bundle_error = ""
            if bundle is None:
                return TextFSMToolTurn(
                    None,
                    "tool_protocol_error",
                    response_model,
                    token_usage,
                    latency_ms,
                    protocol_error=(
                        bundle_error
                        or "implicit Parser bundle must use netops.parser_bundle/v1 JSON"
                    ),
                )
            content = bundle.serialize()
        return TextFSMToolTurn(
            content,
            "implicit_content",
            response_model,
            token_usage,
            latency_ms,
        )


def create_textfsm_tool_session(
    bundle: PromptBundle,
    *,
    client: Any | None = None,
    bundle_mode: bool = False,
) -> DeepSeekTextFSMToolSession:
    _ensure_import_path()
    from src.common.config import get_settings

    settings = get_settings()
    if client is None:
        from openai import OpenAI

        if not str(settings.DEEPSEEK_API_KEY or "").strip():
            raise RuntimeError("DeepSeek API key is not configured")
        client = OpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
            timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )
    return DeepSeekTextFSMToolSession(
        bundle=bundle,
        client=client,
        model_name=settings.LLM_MODEL,
        bundle_mode=bundle_mode,
    )


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
Do not flatten different entity grains into one sparse record. Do not rename, add or
remove fields, and do not return Markdown or explanations.

Sanitized training samples:
{cli_output}
"""
    profile = DEFAULT_TEXTFSM_DIALECT
    return PromptBundle(
        system=build_textfsm_system_prompt(profile),
        task=task.strip(),
        prompt_version=profile.prompt_version,
        dialect_hash=dialect_hash(profile),
    )


def build_generation_prompt(
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
    """Compatibility renderer used by tests and non-chat model adapters."""
    return build_prompt_bundle(
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
    ).render()


def build_repair_prompt(
    *,
    vendor: str,
    model: str,
    command: str,
    cli_output: str,
    required_fields: list[str],
    optional_fields: list[str] | None,
    primary_keys: list[str] | None,
    current_template: str,
    validation_errors: list[str] | None = None,
    missing_fields: list[str] | None = None,
    value_errors: list[str] | None = None,
    repair_diagnostic: dict[str, Any] | None = None,
    validators: dict[str, dict[str, Any]] | None = None,
    parse_plan: dict[str, Any] | None = None,
    field_bindings: dict[str, Any] | None = None,
    allow_empty: bool = False,
    empty_patterns: list[str] | None = None,
) -> str:
    diagnostic = repair_diagnostic or {
        "failure_type": "legacy_validation_failure",
        "errors": list(validation_errors or []),
        "missing_fields": list(missing_fields or []),
        "value_errors": list(value_errors or []),
        "allowed_action": "repair_template",
    }
    return build_prompt_bundle(
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
        repair_diagnostic=diagnostic,
        current_template=current_template,
    ).render()


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


def generate_template_text(
    *,
    vendor: str,
    model: str,
    command: str,
    cli_output: str,
    required_fields: list[str],
    optional_fields: list[str] | None = None,
    primary_keys: list[str] | None = None,
    previous_template: str | None = None,
    validation_errors: list[str] | None = None,
    missing_fields: list[str] | None = None,
    value_errors: list[str] | None = None,
    repair_diagnostic: dict[str, Any] | None = None,
    validators: dict[str, dict[str, Any]] | None = None,
    parse_plan: dict[str, Any] | None = None,
    field_bindings: dict[str, Any] | None = None,
    allow_empty: bool = False,
    empty_patterns: list[str] | None = None,
) -> str:
    if repair_diagnostic is None and previous_template:
        repair_diagnostic = {
            "failure_type": "legacy_validation_failure",
            "errors": list(validation_errors or []),
            "missing_fields": list(missing_fields or []),
            "value_errors": list(value_errors or []),
            "allowed_action": "repair_template",
        }
    bundle = build_prompt_bundle(
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
        repair_diagnostic=repair_diagnostic,
        current_template=previous_template,
    )
    response = call_llm(bundle)
    return LLMText(
        _strip_fences(response),
        model_name=getattr(response, "model_name", None),
        token_usage=getattr(response, "token_usage", None),
    )


def _strip_fences(text: str) -> str:
    fence = re.search(r"```(?:textfsm|text|)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    return fence.group(1).strip() if fence else text.strip()

# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Generate and repair TextFSM templates with version-bound prompts."""

from __future__ import annotations

import json
import re
import sys
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
        output[str(field_name)] = [
            dict(item) for item in items[:4] if isinstance(item, dict)
        ]
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
        temperature=0.1,
        api_key=settings.DEEPSEEK_API_KEY,
        request_timeout=120,
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

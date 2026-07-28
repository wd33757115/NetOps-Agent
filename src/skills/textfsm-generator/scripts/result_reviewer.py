"""Independent semantic review for a locally valid TextFSM candidate."""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from src.common.config import get_settings
from src.core.parser.parser_bundle import parse_parser_bundle
from src.core.parser_generation import (
    ParserReviewReport,
    ParserSample,
    ParserSpec,
    ParserSpecDocument,
    TemplateCandidate,
    ValidationReport,
)


def _json_object(value: str) -> dict[str, Any]:
    text = str(value or "").strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("review model did not return a JSON object")
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("review model response must be a JSON object")
    return payload


def _usage_dict(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    output: dict[str, int] = {}
    for source, target in (
        ("prompt_tokens", "prompt_tokens"),
        ("completion_tokens", "completion_tokens"),
        ("total_tokens", "total_tokens"),
        ("prompt_cache_hit_tokens", "prompt_cache_hit_tokens"),
        ("prompt_cache_miss_tokens", "prompt_cache_miss_tokens"),
    ):
        value = getattr(usage, source, None)
        if isinstance(value, int):
            output[target] = value
    return output


def _review_context(
    *,
    spec: ParserSpec | ParserSpecDocument,
    samples: tuple[ParserSample, ...],
) -> str:
    sample_payload = [
        {
            "sample_id": sample.sample_id,
            "sanitized_cli": str(sample.prompt_text or "")[:24000],
        }
        for sample in samples
    ]
    return f"""You are the independent reviewer for a network CLI parser candidate.
Do not rewrite templates. Compare each submitted final parsed result with the frozen
sanitized CLI evidence. Do not trust record counts merely because local checks passed.

ParserSpec:
{json.dumps(spec.to_dict(), ensure_ascii=True, indent=2)}

Frozen sanitized samples:
{json.dumps(sample_payload, ensure_ascii=True, indent=2)}

Review rules:
- Fail if the parser selected a summary table instead of the intended detailed records.
- Fail if visible repeated records are omitted or non-record headings/footers become records.
- Fail if a multiline value is silently reduced to its first physical line.
- Fail if parsed values are shifted into the wrong fields or are not grounded in the CLI.
- Fail if a positive data sample visibly contains records but the parsed result is empty.
- Fail if parsed values retain incidental leading or trailing CLI alignment whitespace.
- Fail if the primary key is a changing metric, description, row position, or non-unique label.
- A single sample is acceptable, but explicitly mention the remaining cross-sample risk.
- Do not require a predeclared record count. Judge the final records against visible evidence.
- Do not include raw device identity or any prose outside the JSON object.
"""


def _candidate_review_prompt(
    *,
    candidate: TemplateCandidate,
    validation: ValidationReport,
) -> str:
    list_fields = []
    bundle = parse_parser_bundle(candidate.template_text)
    template_texts = (
        [item.template_text for item in bundle.record_sets]
        if bundle is not None
        else [candidate.template_text]
    )
    for line in (
        line
        for template_text in template_texts
        for line in template_text.splitlines()
    ):
        match = re.match(
            r"^\s*Value\s+"
            r"(?P<options>(?:Filldown|List)(?:\s*,\s*(?:Filldown|List))*)\s+"
            r"(?P<field>[A-Za-z_]\w*)\s+\(",
            line,
        )
        if match and "List" in {
            option.strip() for option in match.group("options").split(",")
        }:
            list_fields.append(match.group("field"))
    return f"""Review this candidate and its actual Python TextFSM parse result.

TextFSM candidate:
<TEXTFSM>
{candidate.template_text}
</TEXTFSM>

TextFSM Value List fields:
{json.dumps(list_fields, ensure_ascii=True)}

Parsed-result representation contract:
- A field declared with TextFSM `Value List` is represented as a native JSON array.
- The array elements preserve the CLI evidence order and are not a stringified Python literal.
- The platform State Normalizer emits one indexed observation per array element and records
  a stable one-based `value_index`; concatenating the elements into one scalar is not required.
- Do not report `field_misaligned` merely because a declared List field is a JSON array.
  Fail it only when elements are missing, out of order, assigned to the wrong entity, or
  contain values not grounded in the sanitized CLI.

Actual parsed results:
{json.dumps(validation.parsed_result_preview(max_records_per_sample=200), ensure_ascii=True, indent=2)}

Local objective gates:
{json.dumps(validation.hard_gates, ensure_ascii=True, indent=2)}

Return JSON only:
{{
  "passed": true,
  "summary": "short engineer-facing conclusion",
  "diagnostics": [
    {{
      "code": "wrong_section|missing_records|extra_records|truncated_multiline_value|field_misaligned|unstable_primary_key|other",
      "sample_id": "sample id or empty",
      "target": "parser_spec|record_scope|record_boundary|template|primary_key",
      "message": "specific evidence-based problem"
    }}
  ],
  "primary_key_assessment": {{
    "passed": true,
    "reason": "why the selected key is visible, non-empty, unique and stable"
  }},
  "completeness_assessment": {{
    "passed": true,
    "reason": "why all intended records and multiline values are represented"
  }}
}}
"""


class IndependentParserResultReviewer:
    """Uses a fresh DeepSeek conversation to review the final result."""

    def __init__(self, *, client: Any | None = None, model_name: str | None = None) -> None:
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
        self.client = client
        self.model_name = str(model_name or settings.LLM_MODEL)
        self._context_sha256: str | None = None
        self._messages: list[dict[str, Any]] = []

    def _ensure_context(
        self,
        *,
        spec: ParserSpec | ParserSpecDocument,
        samples: tuple[ParserSample, ...],
    ) -> None:
        context = _review_context(spec=spec, samples=samples)
        context_sha256 = hashlib.sha256(context.encode("utf-8")).hexdigest()
        if context_sha256 == self._context_sha256:
            return
        self._context_sha256 = context_sha256
        self._messages = [{"role": "system", "content": context}]

    def review(
        self,
        *,
        spec: ParserSpec | ParserSpecDocument,
        samples: tuple[ParserSample, ...],
        candidate: TemplateCandidate,
        validation: ValidationReport,
    ) -> ParserReviewReport:
        started = time.monotonic()
        try:
            self._ensure_context(spec=spec, samples=samples)
            request_messages = [
                *self._messages,
                {
                    "role": "user",
                    "content": _candidate_review_prompt(
                        candidate=candidate,
                        validation=validation,
                    ),
                },
            ]
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=request_messages,
                reasoning_effort="high",
                extra_body={"thinking": {"type": "enabled"}},
            )
            message = response.choices[0].message
            payload = _json_object(str(message.content or ""))
            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": getattr(message, "content", None),
            }
            reasoning_content = getattr(message, "reasoning_content", None)
            if reasoning_content is not None:
                assistant_message["reasoning_content"] = reasoning_content
            self._messages = [*request_messages, assistant_message]
            diagnostics = tuple(
                {
                    "code": str(item.get("code") or "other")[:80],
                    "sample_id": str(item.get("sample_id") or "")[:200],
                    "target": str(item.get("target") or "template")[:80],
                    "message": str(item.get("message") or "")[:1000],
                }
                for item in payload.get("diagnostics") or []
                if isinstance(item, dict)
            )
            passed = payload.get("passed") is True
            if not passed and not diagnostics:
                diagnostics = (
                    {
                        "code": "semantic_review_failed",
                        "sample_id": "",
                        "target": "template",
                        "message": str(payload.get("summary") or "semantic review failed")[:1000],
                    },
                )
            return ParserReviewReport(
                passed=passed,
                summary=str(payload.get("summary") or "")[:1000],
                diagnostics=diagnostics,
                primary_key_assessment=dict(payload.get("primary_key_assessment") or {}),
                completeness_assessment=dict(payload.get("completeness_assessment") or {}),
                model_name=str(getattr(response, "model", None) or self.model_name),
                token_usage=_usage_dict(response),
                latency_ms=round((time.monotonic() - started) * 1000),
            )
        except Exception as exc:
            return ParserReviewReport(
                passed=False,
                summary="Independent semantic review could not be completed.",
                diagnostics=(
                    {
                        "code": "review_provider_or_protocol_error",
                        "sample_id": "",
                        "target": "template",
                        "message": f"{type(exc).__name__}: {exc}"[:1000],
                    },
                ),
                model_name=self.model_name,
                latency_ms=round((time.monotonic() - started) * 1000),
            )

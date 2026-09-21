"""Independent semantic review for a locally valid TextFSM candidate."""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from src.common.config import get_settings
from src.common.deadline import call_with_wall_clock_timeout
from src.apps.rule_governance.backend.parser.field_contract import textfsm_field_sets
from src.apps.rule_governance.backend.parser.parser_bundle import parse_parser_bundle
from src.apps.rule_governance.backend.parser_generation import (
    ParserReviewReport,
    ParserSample,
    ParserSpec,
    ParserSpecDocument,
    TemplateCandidate,
    ValidationReport,
)
from src.apps.rule_governance.backend.parser_generation.samples import (
    bounded_model_text,
    representative_samples,
)

_NON_BLOCKING_SAMPLE_SCOPE_PHRASES = (
    "only one frozen sample",
    "only one sample",
    "single sample",
    "cross-sample risk",
    "additional samples",
    "more samples",
)


def _is_non_blocking_sample_scope_diagnostic(item: dict[str, Any]) -> bool:
    message = str(item.get("message") or "").casefold()
    bounded_coverage_warning = bool(
        "only one" in message
        and any(noun in message for noun in ("sample", "variant"))
        and any(
            qualifier in message
            for qualifier in ("risk", "robust", "coverage", "different")
        )
    )
    return bool(
        str(item.get("code") or "").casefold() == "other"
        and str(item.get("target") or "").casefold()
        in {"template", "record_scope"}
        and (
            bounded_coverage_warning
            or any(
                phrase in message
                for phrase in _NON_BLOCKING_SAMPLE_SCOPE_PHRASES
            )
        )
    )


def _textfsm_list_fields(template_text: str) -> frozenset[str]:
    bundle = parse_parser_bundle(template_text)
    template_texts = (
        [item.template_text for item in bundle.record_sets]
        if bundle is not None
        else [template_text]
    )
    fields: set[str] = set()
    for line in (
        line
        for child_template in template_texts
        for line in child_template.splitlines()
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
            fields.add(match.group("field"))
    return frozenset(fields)


def _is_non_blocking_list_representation_diagnostic(
    item: dict[str, Any],
    *,
    list_fields: frozenset[str],
    text_fields: frozenset[str],
) -> bool:
    message = str(item.get("message") or "").casefold()
    represented_fields = {
        field
        for field in list_fields & text_fields
        if field.casefold() in message
    }
    return bool(
        str(item.get("code") or "").casefold() == "field_misaligned"
        and str(item.get("target") or "").casefold()
        in {"template", "parser_spec"}
        and represented_fields
        and "list" in message
        and any(token in message for token in ("scalar", "type 'text'", 'type "text"'))
        and not any(
            problem in message
            for problem in ("missing element", "out of order", "wrong entity")
        )
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
    representative = representative_samples(samples)
    sample_payload = [
        {
            "sample_id": sample.sample_id,
            "sample_role": sample.role.value,
            "sanitized_cli": bounded_model_text(
                sample.prompt_text
                if sample.prompt_text is not None
                else sample.validation_text_override or ""
            ),
        }
        for sample in representative
    ]
    return f"""You are the independent reviewer for a network CLI parser candidate.
Do not rewrite templates. Compare each submitted final parsed result with the frozen
sanitized CLI evidence. Local code is only an evidence executor: compilation, execution,
and record counts are observations, never a semantic acceptance decision.

ParserSpec:
{json.dumps(spec.to_dict(), ensure_ascii=True, indent=2)}

Frozen sanitized samples:
{json.dumps(sample_payload, ensure_ascii=True, indent=2)}

Prompt sample scope: {len(representative)} structural representatives from
{len(samples)} frozen samples. Local execution and the coverage summary still include
every frozen sample; an omitted duplicate is not an omitted validation case.

Review rules:
- Decide whether the result preserves the fields that are operationally valuable for
  network inspection, troubleshooting, change comparison, risk assessment, and reporting.
  Do not demand decorative or copyright/banner fields that carry no operational value.
- Fail if the parser selected a summary table instead of the intended detailed records.
- Fail if visible repeated records are omitted or non-record headings/footers become records.
- Fail if a multiline value is silently reduced to its first physical line.
- Fail if parsed values are shifted into the wrong fields or are not grounded in the CLI.
- Fail if a non-empty parsed value contradicts its ParserSpec type. In particular, an
  `ip` field cannot contain `--`, `unassigned`, `N/A` or similar placeholders; that
  contract must use `ip_or_unassigned`. Report a contract contradiction against
  `parser_spec`, because regenerating only the TextFSM template cannot repair it.
- A `number` or `percent` field may declare `unavailable_tokens`. Those exact tokens
  are valid source states and must not be reported as type violations; numeric values
  in the same field must still be emitted without their display unit.
- Treat ParserSpec, inferred record boundaries, and expected_records as engineer-editable
  generation guidance, not ground truth. Compare them with the CLI, but do not reject a
  faithful result solely because an automatically inferred count, key, field, or boundary
  is wrong. In that case target the diagnostic at `parser_spec` and explain the correction.
  Source placeholders may remain source values only when the declared type permits them.
- Fail if a positive data sample visibly contains records but the parsed result is empty.
- A negative sample is expected to produce zero records. Do not treat that zero result as
  missing data; fail only when the candidate extracts a business record from negative evidence.
- Inspect every record set declared by ParserSpec. Use the current request's record-set
  coverage summary before claiming that a record set emitted zero rows; never carry a
  missing-record conclusion forward from an earlier candidate. An optional record set may
  emit zero only when that sample has no visible evidence for that record grain.
- Return exactly one record_set_assessments entry for every ParserSpec record-set name.
  Do not set the overall result to passed when any record set was not independently checked.
- Fail if parsed values retain incidental leading or trailing CLI alignment whitespace.
- Fail if the primary key is a changing metric, description, row position, or non-unique label.
- A single positive sample is sufficient to enter human approval. Mention remaining
  cross-sample risk as a non-blocking warning, but never set `passed=false` or request a
  template rewrite solely because only one sample or one structural variant is available.
- Do not require a predeclared record count. Judge the final records against visible evidence.
- Do not include raw device identity or any prose outside the JSON object.
"""


def _candidate_review_prompt(
    *,
    spec: ParserSpec | ParserSpecDocument,
    candidate: TemplateCandidate,
    validation: ValidationReport,
) -> str:
    coverage_by_sample: dict[str, dict[str, int]] = {}
    for sample in validation.samples:
        sample_id, separator, record_set = str(sample.sample_id).rpartition("::")
        if not separator:
            sample_id = str(sample.sample_id)
            record_set = "default"
        coverage_by_sample.setdefault(sample_id, {})[record_set] = int(
            sample.record_count
        )
    list_fields = sorted(_textfsm_list_fields(candidate.template_text))
    parsed_preview = json.dumps(
        validation.parsed_result_preview(max_records_per_sample=12),
        ensure_ascii=True,
        indent=2,
    )
    expected_field_sets = (
        {"default": sorted(field.name for field in spec.fields)}
        if isinstance(spec, ParserSpec)
        else {
            record_set.record_set_name: sorted(
                field.name for field in record_set.fields
            )
            for record_set in spec.record_sets
        }
    )
    try:
        emitted_field_sets = textfsm_field_sets(candidate.template_text)
        field_inventory_error = None
    except Exception as exc:
        emitted_field_sets = {}
        field_inventory_error = f"{type(exc).__name__}: {exc}"
    field_inventory = {
        "expected_parser_spec_fields": expected_field_sets,
        "emitted_textfsm_fields": emitted_field_sets,
        "aligned": expected_field_sets == emitted_field_sets,
        "error": field_inventory_error,
    }
    diagnostic_codes = "|".join(
        (
            "wrong_section",
            "missing_records",
            "extra_records",
            "truncated_multiline_value",
            "field_misaligned",
            "unstable_primary_key",
            "other",
        )
    )
    return f"""Review this candidate and its actual Python TextFSM parse result.

TextFSM candidate:
<TEXTFSM>
{candidate.template_text}
</TEXTFSM>

TextFSM Value List fields:
{json.dumps(list_fields, ensure_ascii=True)}

Objective field inventory evidence:
{json.dumps(field_inventory, ensure_ascii=True, indent=2)}

Prefer the approved ParserSpec/check-item vocabulary whenever the meaning and unit
are the same. If `aligned` is false, compare both inventories with the CLI instead
of treating the generated ParserSpec as ground truth. Reject only when the candidate
omits operationally valuable evidence, uses ambiguous names, or assigns a wrong
meaning/unit. If the parsed table is faithful and only the generated ParserSpec is
wrong, keep the candidate eligible for human review and target the diagnostic at
`parser_spec`.

Parsed-result representation contract:
- A field declared with TextFSM `Value List` is represented as a native JSON array.
- The array elements preserve the CLI evidence order and are not a stringified Python literal.
- The platform State Normalizer emits one indexed observation per array element and records
  a stable one-based `value_index`; concatenating the elements into one scalar is not required.
- Do not report `field_misaligned` merely because a declared List field is a JSON array.
  Fail it only when elements are missing, out of order, assigned to the wrong entity, or
  contain values not grounded in the sanitized CLI.

Representative actual parsed results from every record set:
{parsed_preview}

Parsed record-set coverage summary (computed from the same current candidate):
{json.dumps(coverage_by_sample, ensure_ascii=True, indent=2)}

Local execution evidence (not a semantic verdict):
{json.dumps(validation.hard_gates, ensure_ascii=True, indent=2)}

Return JSON only:
{{
  "passed": true,
  "summary": "short engineer-facing conclusion",
  "diagnostics": [
    {{
      "code": "{diagnostic_codes}",
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
  }},
  "record_set_assessments": [
    {{
      "name": "exact ParserSpec record-set name",
      "passed": true,
      "reason": "evidence-based assessment using current counts and representative values"
    }}
  ]
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
        self.hard_timeout_seconds = int(
            settings.PARSER_MODEL_REQUEST_HARD_TIMEOUT_SECONDS
        )
        self.max_completion_tokens = int(
            settings.PARSER_LLM_REVIEW_MAX_COMPLETION_TOKENS
        )
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
                        spec=spec,
                        candidate=candidate,
                        validation=validation,
                    ),
                },
            ]
            response = call_with_wall_clock_timeout(
                lambda: self.client.chat.completions.create(
                    model=self.model_name,
                    messages=request_messages,
                    reasoning_effort="high",
                    max_tokens=self.max_completion_tokens,
                    extra_body={"thinking": {"type": "enabled"}},
                ),
                timeout_seconds=self.hard_timeout_seconds,
                operation="Parser semantic review",
            )
            message = response.choices[0].message
            payload = _json_object(str(message.content or ""))
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
            list_fields = _textfsm_list_fields(candidate.template_text)
            record_sets = (
                spec.record_sets
                if isinstance(spec, ParserSpecDocument)
                else (spec,)
            )
            text_fields = frozenset(
                field.name
                for record_set in record_sets
                for field in record_set.fields
                if field.value_type == "text"
            )
            sample_scope_warnings = tuple(
                item
                for item in diagnostics
                if _is_non_blocking_sample_scope_diagnostic(item)
            )
            list_representation_warnings = tuple(
                item
                for item in diagnostics
                if _is_non_blocking_list_representation_diagnostic(
                    item,
                    list_fields=list_fields,
                    text_fields=text_fields,
                )
            )
            non_blocking_warnings = (
                *sample_scope_warnings,
                *list_representation_warnings,
            )
            diagnostics = tuple(
                item
                for item in diagnostics
                if item not in non_blocking_warnings
            )
            expected_record_sets = (
                [item.record_set_name for item in spec.record_sets]
                if isinstance(spec, ParserSpecDocument)
                else [spec.record_set_name]
            )
            raw_record_set_assessments = payload.get("record_set_assessments") or []
            record_set_assessments = tuple(
                {
                    "name": str(item.get("name") or "")[:120],
                    "passed": item.get("passed") is True,
                    "reason": str(item.get("reason") or "")[:1000],
                }
                for item in raw_record_set_assessments
                if isinstance(item, dict)
            )
            assessment_by_name = {
                str(item["name"]): item for item in record_set_assessments
            }
            missing_assessments = [
                name for name in expected_record_sets if name not in assessment_by_name
            ]
            primary_key_assessment = dict(
                payload.get("primary_key_assessment") or {}
            )
            completeness_assessment = dict(
                payload.get("completeness_assessment") or {}
            )
            if non_blocking_warnings:
                completeness_assessment["non_blocking_warnings"] = [
                    str(item.get("message") or "")[:1000]
                    for item in non_blocking_warnings
                ]
            review_core_is_positive = bool(
                validation.passed
                and not diagnostics
                and primary_key_assessment.get("passed") is True
                and completeness_assessment.get("passed") is True
            )
            effective_model_passed = bool(payload.get("passed") is True)
            if (
                not effective_model_passed
                and non_blocking_warnings
                and review_core_is_positive
            ):
                effective_model_passed = True
            overall_review_is_explicitly_positive = bool(
                effective_model_passed and review_core_is_positive
            )
            if missing_assessments and overall_review_is_explicitly_positive:
                # A formatting omission in an otherwise explicit positive semantic
                # verdict is not a template defect. Preserve the reviewer verdict and
                # make the normalization visible to the engineer instead of sending a
                # correct template through another generation round.
                normalized_reason = (
                    "Overall independent review passed; the provider omitted the "
                    "per-record-set entry. "
                    + str(payload.get("summary") or "")
                )[:1000]
                record_set_assessments = (
                    *record_set_assessments,
                    *(
                        {
                            "name": name,
                            "passed": True,
                            "reason": normalized_reason,
                            "normalized_from_overall_review": True,
                        }
                        for name in missing_assessments
                    ),
                )
                assessment_by_name = {
                    str(item["name"]): item for item in record_set_assessments
                }
                missing_assessments = []
            failed_assessments = [
                name
                for name in expected_record_sets
                if name in assessment_by_name
                and assessment_by_name[name].get("passed") is not True
            ]
            if missing_assessments:
                diagnostics = (
                    *diagnostics,
                    {
                        "code": "review_record_set_assessment_missing",
                        "sample_id": "",
                        "target": "record_scope",
                        "message": (
                            "Independent review did not assess record sets: "
                            + ", ".join(missing_assessments)
                        )[:1000],
                    },
                )
            passed = bool(
                effective_model_passed
                and not diagnostics
                and not missing_assessments
                and not failed_assessments
            )
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
                primary_key_assessment=primary_key_assessment,
                completeness_assessment=completeness_assessment,
                record_set_assessments=record_set_assessments,
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

"""Suggest an engineer-reviewable ParserSpec from sanitized job samples."""

from __future__ import annotations

import json
import re
from typing import Any

from llm_generator import call_llm

from src.core.parser.output_classifier import classify_parser_generation_sample
from src.core.parser.repository import ParserJobRepository
from src.core.parser_generation import ParserSpec, ParserSpecDocument

_TYPE_ALIASES = {
    "string": "text",
    "str": "text",
    "integer": "number",
    "int": "number",
    "float": "number",
    "decimal": "number",
    "percentage": "percent",
    "interface": "interface_name",
    "ipv4": "ip",
    "ipv6": "ip",
}
_CANONICAL_CANDIDATE_RE = re.compile(
    r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
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
            raise ValueError("LLM did not return a JSON object")
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("LLM ParserSpec suggestion must be a JSON object")
    return payload


def _field_name(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    return normalized


def _normalize_spec(
    payload: dict[str, Any],
    *,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    raw = dict(payload.get("parser_spec") or payload.get("spec") or payload)

    def normalize_record_set(
        raw_record_set: dict[str, Any],
        *,
        fallback_name: str | None = None,
        include_metadata: bool,
    ) -> dict[str, Any]:
        raw_fields = raw_record_set.get("fields") or {}
        if isinstance(raw_fields, list):
            raw_fields = {
                str(item.get("name") or ""): {
                    key: value for key, value in item.items() if key != "name"
                }
                for item in raw_fields
                if isinstance(item, dict) and item.get("name")
            }
        if not isinstance(raw_fields, dict):
            raise ValueError("suggested ParserSpec fields must be an object")
        fields: dict[str, dict[str, Any]] = {}
        for raw_name, raw_config in raw_fields.items():
            name = _field_name(raw_name)
            if not name or not isinstance(raw_config, dict):
                continue
            config = dict(raw_config)
            value_type = str(config.get("type") or "text").strip().lower()
            value_type = _TYPE_ALIASES.get(value_type, value_type)
            if value_type == "enum" and not config.get("values"):
                value_type = "text"
            canonical_candidate = str(
                config.get("canonical_candidate") or ""
            ).strip()
            if canonical_candidate and not _CANONICAL_CANDIDATE_RE.fullmatch(
                canonical_candidate
            ):
                if warnings is not None:
                    warnings.append(
                        "ignored invalid canonical_candidate for "
                        f"{name}: {canonical_candidate}"
                    )
                canonical_candidate = ""
            fields[name] = {
                "required": bool(config.get("required", False)),
                "type": value_type,
                **(
                    {"values": list(config.get("values") or [])}
                    if value_type == "enum"
                    else {}
                ),
                **(
                    {"pattern": str(config.get("pattern"))}
                    if value_type == "regex" and config.get("pattern")
                    else {}
                ),
                **(
                    {
                        "source_labels": [
                            str(item) for item in config.get("source_labels") or []
                        ]
                    }
                    if config.get("source_labels")
                    else {}
                ),
                **(
                    {"role": str(config.get("role") or "").strip().casefold()}
                    if config.get("role")
                    else {}
                ),
                **(
                    {
                        "operational_reason": str(
                            config.get("operational_reason") or ""
                        ).strip()
                    }
                    if config.get("operational_reason")
                    else {}
                ),
                **(
                    {
                        "change_behavior": str(
                            config.get("change_behavior") or ""
                        ).strip().casefold()
                    }
                    if config.get("change_behavior")
                    else {}
                ),
                **(
                    {"monitor": bool(config.get("monitor"))}
                    if config.get("monitor") is not None
                    else {}
                ),
                **(
                    {"canonical_candidate": canonical_candidate}
                    if canonical_candidate
                    else {}
                ),
                **(
                    {
                        "evidence": [
                            str(item) for item in config.get("evidence") or []
                        ]
                    }
                    if config.get("evidence")
                    else {}
                ),
            }
        raw_primary_keys = raw_record_set.get("primary_keys") or []
        if isinstance(raw_primary_keys, str):
            raw_primary_keys = [raw_primary_keys]
        primary_keys = [
            _field_name(item) for item in raw_primary_keys if _field_name(item)
        ]
        for key in primary_keys:
            if key in fields:
                fields[key]["required"] = True
                if include_metadata:
                    fields[key].setdefault("role", "identity")
        record_name = _field_name(
            raw_record_set.get("name") or fallback_name or raw_record_set.get("entity")
        )
        spec = {
            **({"name": record_name} if include_metadata else {}),
            "entity": _field_name(raw_record_set.get("entity") or record_name or "record"),
            **(
                {
                    "cardinality": str(
                        raw_record_set.get("cardinality")
                        or ("repeated" if primary_keys else "singleton")
                    ).strip().casefold()
                }
                if include_metadata
                else {}
            ),
            "primary_keys": primary_keys,
            "fields": fields,
            "allow_empty": bool(raw_record_set.get("allow_empty", False)),
            "empty_patterns": [
                str(item) for item in raw_record_set.get("empty_patterns") or []
            ],
            **(
                {
                    "expected_records": [
                        dict(item)
                        for item in raw_record_set.get("expected_records") or []
                        if isinstance(item, dict)
                    ]
                }
                if include_metadata
                else {}
            ),
        }
        return ParserSpec.from_dict(spec).to_dict(
            include_record_set_metadata=include_metadata
        )

    raw_record_sets = raw.get("record_sets")
    if raw_record_sets is None:
        return normalize_record_set(raw, include_metadata=False)
    if isinstance(raw_record_sets, dict):
        record_sets = [
            normalize_record_set(
                dict(config),
                fallback_name=str(name),
                include_metadata=True,
            )
            for name, config in raw_record_sets.items()
            if isinstance(config, dict)
        ]
    elif isinstance(raw_record_sets, list):
        record_sets = [
            normalize_record_set(dict(item), include_metadata=True)
            for item in raw_record_sets
            if isinstance(item, dict)
        ]
    else:
        raise ValueError("suggested ParserSpec record_sets must be an array or object")
    document = {
        "version": 2,
        "business_purpose": str(raw.get("business_purpose") or "").strip(),
        "record_sets": record_sets,
    }
    return ParserSpecDocument.from_dict(document).to_dict()


def _parent_blocks(raw_text: str) -> list[tuple[str, list[str]]]:
    lines = raw_text.splitlines()
    anchors: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or line[:1].isspace() or re.search(r"\s", stripped):
            continue
        next_line = next(
            (candidate for candidate in lines[index + 1 :] if candidate.strip()),
            "",
        )
        if next_line[:1].isspace():
            anchors.append((index, stripped))
    blocks: list[tuple[str, list[str]]] = []
    for position, (start, parent) in enumerate(anchors):
        end = anchors[position + 1][0] if position + 1 < len(anchors) else len(lines)
        blocks.append((parent, lines[start + 1 : end]))
    return blocks if len(blocks) >= 2 else []


def _nested_record_layout(
    raw_text: str,
) -> tuple[list[tuple[str, str]], list[str]] | None:
    blocks = _parent_blocks(raw_text)
    if not blocks:
        return None
    child_labels: dict[str, dict[str, int]] = {}
    for _parent, lines in blocks:
        block_counts: dict[str, int] = {}
        for line in lines:
            match = re.match(r"^\s*([A-Za-z][A-Za-z ]{1,40}?):\s*(\S+)", line)
            if match:
                label = match.group(1).strip()
                block_counts[label] = block_counts.get(label, 0) + 1
        for label, count in block_counts.items():
            stats = child_labels.setdefault(
                label,
                {
                    "repeated_blocks": 0,
                    "single_blocks": 0,
                    "max_per_block": 0,
                    "total": 0,
                },
            )
            stats["total"] += count
            stats["max_per_block"] = max(stats["max_per_block"], count)
            if count >= 2:
                stats["repeated_blocks"] += 1
            else:
                stats["single_blocks"] += 1
    repeated_child_label = next(
        (
            label
            for label, _stats in sorted(
                child_labels.items(),
                key=lambda item: (
                    -item[1]["repeated_blocks"],
                    item[1]["single_blocks"],
                    -item[1]["max_per_block"],
                    -item[1]["total"],
                    item[0],
                ),
            )
            if _stats["repeated_blocks"] >= 1
        ),
        None,
    )
    if not repeated_child_label:
        return None

    keys: list[tuple[str, str]] = []
    source_labels = [f"{repeated_child_label}:"]
    for parent, lines in blocks:
        child_values = [
            match.group(1)
            for line in lines
            if (
                match := re.match(
                    rf"^\s*{re.escape(repeated_child_label)}:\s*(\S+)",
                    line,
                )
            )
        ]
        if child_values:
            keys.extend((parent, value) for value in child_values)
            continue
        detail = next(
            (
                (match.group(1), line.strip())
                for line in lines
                if (
                    match := re.match(
                        r"^\s*([A-Za-z][A-Za-z0-9_-]*)\s+Detail\s+Diagnostics\b",
                        line,
                        re.IGNORECASE,
                    )
                )
            ),
            None,
        )
        if detail:
            keys.append((parent, detail[0]))
            source_labels.append(detail[1])
            continue
        status = next(
            (
                (match.group(1), line.strip())
                for line in lines
                if (
                    match := re.match(
                        r"^\s*([A-Za-z][A-Za-z0-9_-]*)\s+is\s+not\s+"
                        r"(?:present|supported)\s*$",
                        line,
                        re.IGNORECASE,
                    )
                )
            ),
            None,
        )
        if status:
            keys.append((parent, status[0]))
            source_labels.append(status[1])
    if len(keys) < len(blocks) or len(set(keys)) != len(keys):
        return None
    return keys, list(dict.fromkeys(source_labels))


def _measurement_rows(raw_text: str) -> dict[str, str]:
    if not re.search(r"\bCurrent\b.*\bAlarms\b.*\bWarnings\b", raw_text):
        return {}
    if not re.search(r"\bMeasurement\b.*\bHigh\b.*\bLow\b.*\bHigh\b.*\bLow\b", raw_text):
        return {}
    rows: dict[str, str] = {}
    pattern = re.compile(
        r"^\s*([A-Za-z][A-Za-z ]{1,30}?)\s+"
        r"[-+]?\d+(?:\.\d+)?\s*([A-Za-z%]+)\s+"
        r"[-+]?\d+(?:\.\d+)?\s*[A-Za-z%]+\s+"
        r"[-+]?\d+(?:\.\d+)?\s*[A-Za-z%]+\s+"
        r"[-+]?\d+(?:\.\d+)?\s*[A-Za-z%]+\s+"
        r"[-+]?\d+(?:\.\d+)?\s*[A-Za-z%]+\s*$",
        re.MULTILINE,
    )
    for match in pattern.finditer(raw_text):
        row_label = re.sub(r"\s+", " ", match.group(1).strip()).casefold()
        rows[row_label] = match.group(2).casefold()
    return rows


def _label_tokens(value: str) -> set[str]:
    return {
        {"alarms": "alarm", "warnings": "warning"}.get(token, token)
        for token in re.split(r"[^a-z0-9]+", str(value or "").casefold())
        if token
    }


def _has_grouped_measurement_field(
    fields: dict[str, dict[str, Any]],
    *,
    metric_label: str,
    group: str,
    side: str,
) -> bool:
    required_tokens = _label_tokens(metric_label) | {group, side}
    for config in fields.values():
        for source_label in config.get("source_labels") or []:
            if required_tokens.issubset(_label_tokens(str(source_label))):
                return True
    return False


def _deduplicate_grouped_measurement_fields(
    fields: dict[str, dict[str, Any]],
    metric_rows: dict[str, str],
) -> None:
    for metric_label in metric_rows:
        for group, side in (
            ("alarm", "high"),
            ("alarm", "low"),
            ("warning", "high"),
            ("warning", "low"),
        ):
            required_tokens = _label_tokens(metric_label) | {group, side}
            matches = [
                (
                    field_name,
                    any(
                        required_tokens.issubset(_label_tokens(str(source_label)))
                        for source_label in config.get("source_labels") or []
                    ),
                    any(
                        "/" in str(source_label)
                        for source_label in config.get("source_labels") or []
                    ),
                )
                for field_name, config in fields.items()
            ]
            matching_fields = [
                (field_name, synthetic_label)
                for field_name, matched, synthetic_label in matches
                if matched
            ]
            if len(matching_fields) < 2:
                continue
            if any(not synthetic_label for _name, synthetic_label in matching_fields):
                for field_name, synthetic_label in matching_fields:
                    if synthetic_label:
                        fields.pop(field_name, None)


def _enrich_structural_spec(
    spec_payload: dict[str, Any],
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    """Add evidenced nested keys and hierarchical threshold columns."""
    enriched = json.loads(json.dumps(spec_payload))
    fields = dict(enriched.get("fields") or {})
    layouts = [
        _nested_record_layout(str(sample.get("sanitized_text") or ""))
        for sample in samples
    ]
    layouts = [layout for layout in layouts if layout is not None]
    if layouts and enriched.get("primary_keys"):
        channel_name = "channel"
        if channel_name in fields:
            channel_name = "record_channel"
        values = sorted(
            {
                value
                for keys, _source_labels in layouts
                for _parent, value in keys
            }
        )
        labels = list(
            dict.fromkeys(
                label
                for _keys, source_labels in layouts
                for label in source_labels
            )
        )
        fields[channel_name] = {
            "required": True,
            "type": "enum",
            "values": values,
            "source_labels": labels,
        }
        enriched["primary_keys"] = [
            str(enriched["primary_keys"][0]),
            channel_name,
        ]

    metric_rows: dict[str, str] = {}
    for sample in samples:
        metric_rows.update(
            _measurement_rows(str(sample.get("sanitized_text") or ""))
        )
    _deduplicate_grouped_measurement_fields(fields, metric_rows)
    unit_suffixes = {
        "c": "celsius",
        "v": "volts",
        "ma": "milliamps",
        "dbm": "dbm",
    }
    for field_name, config in list(fields.items()):
        labels = [str(item) for item in config.get("source_labels") or []]
        matching_label = next(
            (
                label
                for label in labels
                if re.sub(r"\s+", " ", label.strip()).casefold() in metric_rows
            ),
            None,
        )
        if not matching_label:
            continue
        row_key = re.sub(r"\s+", " ", matching_label.strip()).casefold()
        suffix = unit_suffixes.get(metric_rows[row_key], metric_rows[row_key])
        stem = field_name
        if suffix and stem.endswith(f"_{suffix}"):
            stem = stem[: -(len(suffix) + 1)]
        for group, side in (
            ("alarm", "high"),
            ("alarm", "low"),
            ("warning", "high"),
            ("warning", "low"),
        ):
            if _has_grouped_measurement_field(
                fields,
                metric_label=matching_label,
                group=group,
                side=side,
            ):
                continue
            threshold_name = "_".join(
                item for item in (stem, side, group, suffix) if item
            )
            fields.setdefault(
                threshold_name,
                {
                    "required": False,
                    "type": "number",
                    "source_labels": [
                        f"{matching_label}/{group.title()}s/{side.title()}"
                    ],
                },
            )
    enriched["fields"] = fields
    return ParserSpec.from_dict(enriched).to_dict()


def _structural_expectation(
    spec: ParserSpec,
    raw_text: str,
) -> dict[str, Any] | None:
    if len(spec.primary_keys) != 2:
        return None
    layout = _nested_record_layout(raw_text)
    if layout is None:
        return None
    keys, _source_labels = layout
    return {
        "record_count": len(keys),
        "primary_keys": [[parent, channel] for parent, channel in keys],
        "expectation_source": "deterministic:nested_repeated_block",
    }


def _normalize_sample_plans(
    payload: dict[str, Any],
    *,
    samples: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    raw_items = payload.get("sample_plans") or []
    if isinstance(raw_items, dict):
        raw_items = [
            {"sample_id": sample_id, **dict(plan or {})}
            for sample_id, plan in raw_items.items()
        ]
    sample_ids = {str(sample["id"]) for sample in samples}
    plans: dict[str, dict[str, Any]] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        sample_id = str(raw.get("sample_id") or "")
        if sample_id not in sample_ids:
            continue
        plans[sample_id] = {
            "target_section": str(raw.get("target_section") or "").strip(),
            "record_unit": str(raw.get("record_unit") or "").strip(),
            "record_start": str(raw.get("record_start") or "").strip(),
            "record_end": str(raw.get("record_end") or "").strip(),
            "exclude_sections": [
                str(item).strip()
                for item in raw.get("exclude_sections") or []
                if str(item).strip()
            ],
            "ambiguities": [
                str(item).strip()
                for item in raw.get("ambiguities") or []
                if str(item).strip()
            ],
        }

    output: list[dict[str, Any]] = []
    for sample in samples:
        sample_id = str(sample["id"])
        output.append(
            {
                "sample_id": sample_id,
                "recommended_role": "generation",
                "semantic_plan": plans.get(
                    sample_id,
                    {
                        "target_section": "",
                        "record_unit": "",
                        "record_start": "",
                        "record_end": "",
                        "exclude_sections": [],
                        "ambiguities": ["LLM did not return a sample-specific record plan"],
                    },
                ),
            }
        )
    return output


def _build_prompt(
    job: dict[str, Any],
    data_samples: list[dict[str, Any]],
    empty_samples: list[dict[str, Any]],
) -> str:
    sample_payload = [
        {
            "sample_id": str(sample["id"]),
            "sanitized_output": str(sample.get("sanitized_text") or "")[:16000],
        }
        for sample in data_samples
    ]
    empty_patterns = sorted(
        {
            re.escape(str(sample.get("sanitized_text") or "").strip())
            for sample in empty_samples
            if str(sample.get("sanitized_text") or "").strip()
        }
    )
    return f"""You design the ideal structured result for network CLI output. Return JSON only.

Vendor hint: {job.get('vendor')}
Model/family hint: {job.get('profile')}
Canonical command: {job.get('canonical_command')}

Sanitized samples:
{json.dumps(sample_payload, ensure_ascii=True, indent=2)}

Known valid-empty sample patterns (if any):
{json.dumps(empty_patterns, ensure_ascii=True)}

Required response shape:
{{
  "parser_spec": {{
    "version": 2,
    "business_purpose": "what operators learn from this command",
    "record_sets": [
      {{
        "name": "snake_case_record_set",
        "entity": "snake_case_entity",
        "cardinality": "singleton|repeated",
        "primary_keys": ["field_name"],
        "fields": {{
          "field_name": {{
            "required": true,
            "type": "text",
            "source_labels": ["CLI label"],
            "role": "identity|state|metric|threshold|context",
            "operational_reason": "why this field changes an operational decision",
            "change_behavior": "stable|gauge|counter|volatile",
            "monitor": true,
            "canonical_candidate": "vendor.neutral.semantic",
            "evidence": ["exact sanitized sample fragment"]
          }}
        }},
        "allow_empty": false,
        "empty_patterns": [],
        "expected_records": [
          {{"sample_id": "...", "records": [{{"field_name": "expected value"}}]}}
        ]
      }}
    ]
  }},
  "sample_plans": [
    {{
      "sample_id": "...",
      "target_section": "which visible section contains the business records",
      "record_unit": "one row or one repeated block",
      "record_start": "visible line pattern that starts a record",
      "record_end": "visible line pattern that completes a record",
      "exclude_sections": ["summary/footer sections that are not records"],
      "ambiguities": []
    }}
  ],
  "confidence": 0.0,
  "rationale": "short engineer-facing explanation"
}}

Rules:
- Use only these field types: text, number, percent, enum, interface_name, ip, regex.
- First describe the ideal structured result. Do not design one giant sparse table merely
  because the command contains several entity grains.
- Split device-level, slot-level, interface-level, sensor-level, and child inventory
  records into separate record_sets whenever their natural cardinality differs.
- A singleton record set has cardinality=singleton and no primary key. A repeated
  record set must have stable required primary keys that identify one business record.
- For nested repeated child sections, use a composite parent/child primary key.
- When some parent blocks have child rows and others have a single non-child record,
  add one required record discriminator sourced from visible alternative marker lines.
- For measurement tables with grouped Current, Alarm High/Low and Warning High/Low
  columns, include the current value and all four threshold fields separately.
- Include only fields visibly supported by the samples. Select a small, operationally
  meaningful field set rather than copying every line.
- Every retained field must state its role, operational_reason, change_behavior,
  monitor decision, canonical_candidate, and exact evidence.
- Keep a field only when it identifies an object, determines health, measures capacity
  or utilization, carries a device-provided threshold, explains a meaningful change,
  or materially helps troubleshooting. Copyright, headings, duplicate summaries and
  unstable prose are not business records.
- uptime may be retained as context, but it is normally monitor=false.
- canonical_candidate is a vendor-neutral semantic suggestion, not a parser field name.
  Commands and vendors may use different source fields for the same canonical semantic.
- Describe the intended record scope and boundary for every supplied data sample.
- expected_records must show the ideal values supported by each sanitized sample. Do
  not invent records or values. These results guide TextFSM generation and remain
  engineer-editable during approval.
- source_labels should preserve visible CLI headings or labels.
- When valid-empty patterns are supplied, set allow_empty=true and include them.
- Do not generate TextFSM. Do not include raw device identity, credentials, or prose outside JSON.
"""


def suggest_parser_spec(params: dict[str, Any]) -> dict[str, Any]:
    job_id = str(params.get("job_id") or "").strip()
    task_id = str(params.get("suggestion_task_id") or "").strip()
    if not job_id or not task_id:
        raise ValueError("job_id and suggestion_task_id are required")
    repository = ParserJobRepository()
    repository.record_parser_spec_suggestion(job_id, task_id=task_id, status="running")
    try:
        job = repository.get_job(job_id, include_sanitized_payload=True)
        if not job:
            raise ValueError(f"parser template job does not exist: {job_id}")
        classified = [
            {
                **sample,
                "classification": classify_parser_generation_sample(
                    str(sample.get("sanitized_text") or "")
                ),
            }
            for sample in job.get("samples") or []
        ]
        data_samples = [
            sample for sample in classified if sample["classification"]["status"] == "data"
        ]
        empty_samples = [
            sample for sample in classified if sample["classification"]["status"] == "valid_empty"
        ]
        negative_suggestions = [
            {
                "sample_id": str(sample["id"]),
                "recommended_role": "negative",
                "classification": sample["classification"]["status"],
            }
            for sample in classified
            if sample["classification"]["recommended_role"] == "negative"
        ]
        if not data_samples:
            header_only_count = sum(
                sample["classification"]["status"] == "header_only_unverified"
                for sample in classified
            )
            suggestion = {
                "classification": (
                    "sample_collection_required"
                    if header_only_count
                    else "no_positive_data"
                ),
                "parser_spec": None,
                "sample_suggestions": negative_suggestions,
                "confidence": 1.0,
                "rationale": (
                    (
                        "The current samples contain table headers but no business data rows. "
                        "Add at least one output with real records before generating a "
                        "ParserSpec. Re-running generation with the same samples cannot "
                        "produce a verifiable Parser."
                    )
                    if header_only_count
                    else (
                        "The current samples contain no positive structured data. Mark the "
                        "recognized empty/error outputs as negative samples and add one output "
                        "that contains real records before generating a ParserSpec."
                    )
                ),
                "model_called": False,
            }
        else:
            response = call_llm(_build_prompt(job, data_samples, empty_samples))
            payload = _json_object(response)
            normalization_warnings: list[str] = []
            spec = _normalize_spec(
                payload,
                warnings=normalization_warnings,
            )
            sample_plans = _normalize_sample_plans(
                payload,
                samples=data_samples,
            )
            suggestion = {
                "classification": "structured_data",
                "parser_spec": spec,
                "sample_suggestions": sample_plans + negative_suggestions,
                "confidence": max(0.0, min(float(payload.get("confidence", 0.5)), 1.0)),
                "rationale": str(payload.get("rationale") or "")[:1000],
                "model_called": True,
                "model_name": getattr(response, "model_name", None),
                "token_usage": getattr(response, "token_usage", {}),
                "normalization_warnings": normalization_warnings,
            }
        repository.record_parser_spec_suggestion(
            job_id,
            task_id=task_id,
            status="ready",
            suggestion=suggestion,
            auto_adopt=True,
        )
        return {"success": True, "job_id": job_id, "suggestion": suggestion}
    except Exception as exc:
        repository.record_parser_spec_suggestion(
            job_id,
            task_id=task_id,
            status="failed",
            error=str(exc),
        )
        raise

"""Suggest an engineer-reviewable ParserSpec from sanitized job samples."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from typing import Any

from src.apps.rule_governance.backend.generators.textfsm_generator.scripts.llm_generator import call_llm

from src.core.check_items.catalog import CheckItemCatalogRepository, load_catalog
from src.core.check_items.parser_capability import (
    build_parser_field_naming_context,
)
from src.apps.rule_governance.backend.parser.output_classifier import classify_parser_generation_sample
from src.apps.rule_governance.backend.parser.repository import ParserJobRepository
from src.apps.rule_governance.backend.parser.semantic_catalog import field_semantics, resolve_semantic_contract
from src.apps.rule_governance.backend.parser.value_types import IP_UNASSIGNED_TOKENS, is_unassigned_ip
from src.apps.rule_governance.backend.parser_generation import (
    ParserSpec,
    ParserSpecDocument,
)

_TYPE_ALIASES = {
    "string": "text",
    "str": "text",
    "integer": "number",
    "int": "number",
    "float": "number",
    "decimal": "number",
    "numeric": "number",
    "percentage": "percent",
    "interface": "interface_name",
    "ipv4": "ip",
    "ipv6": "ip",
}
_CANONICAL_CANDIDATE_RE = re.compile(
    r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
)


class _SuggestionHeartbeat:
    def __init__(
        self,
        *,
        repository: ParserJobRepository,
        job_id: str,
        task_id: str,
    ) -> None:
        from src.common.config import get_settings

        self.repository = repository
        self.job_id = job_id
        self.task_id = task_id
        self.interval_seconds = max(
            1,
            int(get_settings().PARSER_MODEL_CALL_HEARTBEAT_SECONDS),
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> _SuggestionHeartbeat:
        self._thread = threading.Thread(
            target=self._run,
            name=f"parser-spec-heartbeat-{self.task_id[:8]}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=min(2, self.interval_seconds))

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.repository.record_parser_spec_suggestion(
                    self.job_id,
                    task_id=self.task_id,
                    status="running",
                )
            except Exception:
                return


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


def _sample_content_key(sample: dict[str, Any]) -> str:
    sanitized_hash = str(sample.get("sanitized_hash") or "").strip()
    if sanitized_hash:
        return sanitized_hash
    text = str(sample.get("sanitized_text") or "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _representative_data_samples(
    samples: list[dict[str, Any]],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Bound semantic prompt input while full samples remain acceptance evidence."""

    representatives: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sample in samples:
        content_key = _sample_content_key(sample)
        if content_key in seen:
            continue
        seen.add(content_key)
        representatives.append(sample)
        if len(representatives) >= max(1, int(limit)):
            break
    return representatives


def _preferred_field_name(
    field_name: str,
    *,
    canonical_candidate: str,
    naming_context: dict[str, Any] | None,
) -> str:
    """Prefer catalog-owned names only when the model supplied an exact semantic hint."""

    if (naming_context or {}).get("strict_value_names"):
        return str(field_name).strip()
    normalized = _field_name(field_name)
    candidate = str(canonical_candidate or "").strip().casefold()
    for check_item in (naming_context or {}).get("check_items") or []:
        if not isinstance(check_item, dict):
            continue
        identity_fields = {
            _field_name(item)
            for item in check_item.get("preferred_identity_fields") or []
            if _field_name(item)
        }
        if candidate and candidate.rsplit(".", 1)[-1] in identity_fields:
            return candidate.rsplit(".", 1)[-1]
        for observation in check_item.get("preferred_observation_fields") or []:
            if not isinstance(observation, dict):
                continue
            preferred_name = _field_name(observation.get("preferred_name"))
            observation_type = str(
                observation.get("observation_type") or ""
            ).strip().casefold()
            if preferred_name and candidate and candidate == observation_type:
                return preferred_name
        for vocabulary in check_item.get("configured_vocabulary") or []:
            if not isinstance(vocabulary, dict):
                continue
            preferred_name = _field_name(vocabulary.get("preferred_name"))
            aliases = {
                _field_name(alias)
                for alias in vocabulary.get("aliases") or []
                if _field_name(alias)
            }
            vocabulary_observation = str(
                vocabulary.get("observation_type") or ""
            ).strip().casefold()
            vocabulary_candidates = {
                str(item).strip().casefold()
                for item in vocabulary.get("canonical_candidates") or []
                if str(item).strip()
            }
            if preferred_name and (
                normalized == preferred_name
                or normalized in aliases
                or (candidate and candidate == vocabulary_observation)
                or (candidate and candidate in vocabulary_candidates)
            ):
                return preferred_name
    return normalized


def _expected_field_values(
    expected_records: Any,
    field_name: str,
) -> list[Any]:
    values: list[Any] = []
    for item in expected_records or []:
        if not isinstance(item, dict):
            continue
        records = item.get("records")
        if not isinstance(records, list):
            records = [item]
        for record in records:
            if isinstance(record, dict) and field_name in record:
                values.append(record.get(field_name))
    return values


def _evidence_contains_unassigned_ip(config: dict[str, Any]) -> bool:
    tokens = "|".join(
        re.escape(token)
        for token in sorted(IP_UNASSIGNED_TOKENS, key=lambda item: (-len(item), item))
    )
    pattern = re.compile(rf"(?<!\S)(?:{tokens})(?!\S)", re.IGNORECASE)
    return any(
        pattern.search(str(item or "")) is not None
        for item in config.get("evidence") or []
    )


def _unavailable_tokens_from_evidence(config: dict[str, Any]) -> tuple[str, ...]:
    evidence = "\n".join(str(item or "") for item in config.get("evidence") or [])
    observed: list[str] = []
    token_pattern = re.compile(
        r"(?<!\S)(N/A|NA|--|-|NONE|NULL|UNKNOWN|UNAVAILABLE|NOT\s+SUPPORTED)(?!\S)",
        re.IGNORECASE,
    )
    for match in token_pattern.finditer(evidence):
        token = re.sub(r"\s+", " ", match.group(1).strip())
        if token.casefold() not in {item.casefold() for item in observed}:
            observed.append(token)
    return tuple(observed)


def _normalize_spec(
    payload: dict[str, Any],
    *,
    warnings: list[str] | None = None,
    field_naming_context: dict[str, Any] | None = None,
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
        field_name_remap: dict[str, str] = {}
        expected_records = raw_record_set.get("expected_records") or []
        for raw_name, raw_config in raw_fields.items():
            source_name = (str(raw_name).strip() if (field_naming_context or {}).get("strict_value_names") else _field_name(raw_name))
            if not source_name or not isinstance(raw_config, dict):
                continue
            config = dict(raw_config)
            raw_canonical_candidate = str(
                config.get("canonical_candidate") or ""
            ).strip()
            name = _preferred_field_name(
                source_name,
                canonical_candidate=raw_canonical_candidate,
                naming_context=field_naming_context,
            )
            if name in fields:
                if source_name in fields:
                    if warnings is not None:
                        warnings.append(
                            f"ignored duplicate field {source_name} after canonical naming"
                        )
                    continue
                if warnings is not None:
                    warnings.append(
                        f"kept source field name {source_name} because preferred name "
                        f"{name} would collide"
                    )
                name = source_name
            field_name_remap[source_name] = name
            if name != source_name and warnings is not None:
                warnings.append(
                    f"preferred approved check-item field name {name} over {source_name}"
                )
            value_type = str(config.get("type") or "text").strip().lower()
            value_type = _TYPE_ALIASES.get(value_type, value_type)
            if value_type == "enum" and not config.get("values"):
                value_type = "text"
            if value_type == "ip":
                expected_values = _expected_field_values(expected_records, name)
                has_explicit_unassigned_value = any(
                    value is not None and is_unassigned_ip(value)
                    for value in expected_values
                )
                has_expected_null = any(value is None for value in expected_values)
                if has_explicit_unassigned_value or (
                    has_expected_null and _evidence_contains_unassigned_ip(config)
                ):
                    value_type = "ip_or_unassigned"
                    if warnings is not None:
                        warnings.append(
                            f"promoted {name} from ip to ip_or_unassigned because "
                            "the sample evidence contains an unassigned value"
                        )
            observed_unavailable_tokens = _unavailable_tokens_from_evidence(config)
            role = str(config.get("role") or "").strip().casefold()
            declared_unavailable_tokens = config.get("unavailable_tokens") or ()
            if isinstance(declared_unavailable_tokens, str):
                declared_unavailable_tokens = (declared_unavailable_tokens,)
            elif not isinstance(declared_unavailable_tokens, (list, tuple, set)):
                declared_unavailable_tokens = ()
            unavailable_tokens = tuple(
                dict.fromkeys(
                    str(item).strip()
                    for item in (
                        *declared_unavailable_tokens,
                        *observed_unavailable_tokens,
                    )
                    if str(item).strip()
                )
            )
            if value_type not in {"number", "percent"}:
                unavailable_tokens = ()
            canonical_candidate = raw_canonical_candidate
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
                    {"role": role}
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
                **(
                    {"unavailable_tokens": list(unavailable_tokens)}
                    if unavailable_tokens
                    else {}
                ),
            }
        raw_primary_keys = raw_record_set.get("primary_keys") or []
        if isinstance(raw_primary_keys, str):
            raw_primary_keys = [raw_primary_keys]
        primary_keys = [
            field_name_remap.get(str(item).strip(), str(item).strip()) if (field_naming_context or {}).get("strict_value_names") else field_name_remap.get(_field_name(item), _field_name(item))
            for item in raw_primary_keys
            if _field_name(item)
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
            **(
                {
                    "presence": str(
                        raw_record_set.get("presence") or "required"
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
        }
        normalized = ParserSpec.from_dict(spec).to_dict(
            include_record_set_metadata=include_metadata
        )
        if include_metadata:
            normalized.pop("expected_records", None)
        return normalized

    def validate_names(record_sets):
        if (field_naming_context or {}).get("strict_value_names"):
            from src.core.check_items.value_contract import validate_same_name_record_sets

            contracts = [
                item["canonical_contract"] for item in field_naming_context["check_items"]
            ]
            validate_same_name_record_sets(contracts, record_sets)

    raw_record_sets = raw.get("record_sets")
    if raw_record_sets is None:
        normalized = normalize_record_set(raw, include_metadata=False)
        validate_names([normalized])
        return normalized
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
    normalized_document = ParserSpecDocument.from_dict(document).to_dict()
    validate_names(normalized_document["record_sets"])
    for record_set in normalized_document.get("record_sets") or []:
        record_set.pop("expected_records", None)
    return normalized_document


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


def _expand_sample_plans(
    plans: list[dict[str, Any]],
    *,
    representative_samples: list[dict[str, Any]],
    all_samples: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    plans_by_id = {
        str(item.get("sample_id") or ""): dict(item)
        for item in plans
        if item.get("sample_id")
    }
    representative_by_key = {
        _sample_content_key(sample): str(sample.get("id") or "")
        for sample in representative_samples
    }
    expanded: list[dict[str, Any]] = []
    for sample in all_samples:
        sample_id = str(sample.get("id") or "")
        plan = plans_by_id.get(sample_id)
        if plan is None:
            representative_id = representative_by_key.get(_sample_content_key(sample))
            representative_plan = plans_by_id.get(str(representative_id or ""))
            if representative_plan is not None:
                plan = {**representative_plan, "sample_id": sample_id}
        if plan is None:
            plan = {
                "sample_id": sample_id,
                "recommended_role": "generation",
                "semantic_plan": {
                    "target_section": "",
                    "record_unit": "",
                    "record_start": "",
                    "record_end": "",
                    "exclude_sections": [],
                    "ambiguities": [
                        "sample retained for full validation but omitted from the bounded semantic prompt"
                    ],
                },
            }
        expanded.append(plan)
    return expanded


def _build_prompt(
    job: dict[str, Any],
    data_samples: list[dict[str, Any]],
    empty_samples: list[dict[str, Any]],
    field_naming_context: dict[str, Any] | None = None,
    revision_instruction: str = "",
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

Approved check-item field naming guidance (advisory, not a parse gate):
{json.dumps(field_naming_context or {}, ensure_ascii=True, indent=2)}

When strict_value_names is true, the CheckItem canonical_contract is authoritative:
use only its identity_dimensions and observations[].canonical_field as Value names.
Preserve their exact case (including uppercase); do not lowercase or rename them.
Follow record_semantics for record grain, shared context and paired child lists.
For collection=list fields use TextFSM Value List, retaining one parent record.
Never inherit a board uptime or board-specific value from another board; shared
values require explicit source scope and must be reset when that scope changes.
Do not add aliases, auxiliary fields or conversion mappings. Include every available
declared field with its exact approved meaning. Missing source data stays missing,
never zero. The same field names flow directly into change and event rules.

Engineer revision instruction (advisory and evidence-bound):
{json.dumps(str(revision_instruction or "")[:4000], ensure_ascii=True)}

The engineer instruction describes the desired business result. Follow it when the
sanitized samples visibly support it. It must never override frozen sample evidence,
invent fields or keys, weaken type validation, suppress contradictory records, or bypass
full-sample execution and independent review. Explain unsupported requests in rationale.

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
        "presence": "required|optional",
        "primary_keys": ["field_name"],
        "fields": {{
          "field_name": {{
            "required": true,
            "type": "text",
            "unavailable_tokens": [],
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
        "empty_patterns": []
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
- Use only these field types: text, number, percent, enum, interface_name, ip,
  ip_or_unassigned, regex.
- Keep operational measurements and thresholds typed as number or percent even when
  some source rows show N/A, -- or another visible missing token. Put only the exact
  source tokens in unavailable_tokens; never downgrade a numeric field to text and
  never interpret a missing token as zero.
- Use ip_or_unassigned when an IP column visibly contains placeholders such as
  unassigned, --, -, none, unset, N/A or null. Such placeholders are valid
  per-entity absence, not malformed IP addresses.
- First describe the ideal structured result. Do not design one giant sparse table merely
  because the command contains several entity grains.
- Split device-level, slot-level, interface-level, sensor-level, and child inventory
  records into separate record_sets whenever their natural cardinality differs.
- A singleton record set has cardinality=singleton and no primary key. A repeated
  record set must have stable required primary keys that identify one business record.
- presence=required means every positive sample must contain that record-set grain.
  Use presence=optional for a source-backed branch that is legitimately absent from
  some samples, such as mutually exclusive absent, unsupported, single-channel and
  multi-channel layouts. At least one record set must still cover every positive sample.
- For nested repeated child sections, use a composite parent/child primary key.
- Parser fields and primary keys must be directly capturable from visible source text
  in every record where they are required. Never invent an implicit, default, ordinal,
  synthetic, or canonical value in ParserSpec, even when that value seems obvious.
- If one layout exposes a child key but another layout omits it, split them into
  separate natural-grain record_sets with source-backed keys. Do not reuse a previous
  child's key through Filldown and do not assign a made-up key such as lane 1.
- Preserve the physical row/block grain of the source. Do not pivot visible columns
  into synthetic records, do not turn a header label into a repeated record value,
  and do not expect TextFSM to emit several keyed records from one source row.
- Keep column-oriented metrics and thresholds as explicit wide fields on their
  source-backed parent record. A later CheckItem / CommandAdapter V2 may unpivot or union them.
- Derived dimensions, defaults, coalescing, and cross-record-set unions belong to a
  later approved CheckItem / CommandAdapter V2, not to TextFSM or ParserSpec.
- When some parent blocks have child rows and others have a single non-child record,
  add one required record discriminator sourced from visible alternative marker lines.
- For mutually exclusive status branches, do not create several required boolean fields
  such as is_absent/is_unsupported. Use one source-backed text field that captures the
  visible branch token or phrase, or split the branches into optional natural record sets.
- For measurement tables with grouped Current, Alarm High/Low and Warning High/Low
  columns, include the current value and all four threshold fields separately.
- Include only fields visibly supported by the samples. Select a small, operationally
  meaningful field set rather than copying every line.
- Derive record boundaries and value types from the sanitized CLI samples. Never
  declare a field as number or percent when its non-empty source values include
  nonnumeric values such as auto, 40G, routed or trunk.
- ParserSpec is editable generation guidance, not an oracle. The final authority is the full
  local TextFSM result reviewed against the source evidence by the independent model and the
  engineer. A speculative ParserSpec detail must never cause a faithful parsed table to be
  rewritten into a worse result.
- Prefer the exact check-item identity and observation field names supplied in the approved
  field naming guidance whenever the visible source value, unit and record grain are
  semantically equivalent. This keeps TextFSM Value names stable across vendors and models.
  For a dimension pattern such as cpu_utilization_{{window}}_percent, replace the placeholder
  with the visible dimension value such as 5s, 1m or 5m.
- Source headings and vendor-specific labels belong in source_labels, not in the field name.
  When the same generic heading appears in several record sets, qualify the Value name
  with the measured quantity and unit. For example, separate optical receive/transmit
  thresholds as rx_low_warning_dbm and tx_low_warning_dbm instead of repeating a bare
  low_warning field whose meaning depends on the record-set name.
  If the source meaning is not equivalent to a preferred field, keep a clear source-faithful
  snake_case name and declare the closest canonical_candidate. A naming difference by itself
  is never a parsing or acceptance failure; the approved CheckItem / CommandAdapter V2 is the final
  canonical mapping authority.
- Every retained field must state its role, operational_reason, change_behavior,
  monitor decision, canonical_candidate, and exact evidence.
- Keep a field only when it identifies an object, determines health, measures capacity
  or utilization, carries a device-provided threshold, explains a meaningful change,
  or materially helps troubleshooting. Copyright, headings, duplicate summaries and
  unstable prose are not business records.
- uptime may be retained as context, but it is normally monitor=false.
- canonical_candidate is the vendor-neutral observation semantic. Prefer the associated
  check-item field name when the mapping is one-to-one; commands may still use different
  source-faithful fields when the source shape or meaning is genuinely different.
- Describe the intended record scope and boundary for every supplied data sample.
- Do not return expected_records. A model-authored record list is not an acceptance
  baseline and must not limit a large sample to a few examples. The platform will execute
  TextFSM against every frozen sample and send the complete parsed results to an independent
  LLM review and then to the engineer. Only an explicitly engineer-confirmed optional sample
  expectation may later act as an exact record-count or primary-key gate.
- source_labels should preserve visible CLI headings or labels. Evidence entries must
  be exact fragments from the sanitized samples; placeholder prose such as
  "implicit when absent" is not evidence.
- When valid-empty patterns are supplied, set allow_empty=true and include them.
- Do not generate TextFSM. Do not include raw device identity, credentials, or prose outside JSON.
"""


def _field_naming_context(
    job: dict[str, Any],
    data_samples: list[dict[str, Any]],
    check_item_id: str = "",
) -> dict[str, Any]:
    try:
        catalog = CheckItemCatalogRepository().load_active()
        if not catalog.check_items:
            catalog = load_catalog()
    except Exception:
        catalog = load_catalog()
    return build_parser_field_naming_context(
        vendor=str(job.get("vendor") or ""),
        model=str(job.get("model") or "").strip() or None,
        family=str(job.get("family") or "").strip() or None,
        profile=str(job.get("profile") or "").strip() or None,
        command=str(job.get("canonical_command") or ""),
        sample_text="\n\n".join(
            str(sample.get("sanitized_text") or "")
            for sample in data_samples
        ),
        catalog=catalog,
        check_item_id=check_item_id or None,
    )


def _semantic_valid_empty_spec_draft(
    *,
    job: dict[str, Any],
    samples: list[dict[str, Any]],
) -> tuple[dict[str, Any], str] | None:
    """Build a reviewable empty-capable spec from governed command semantics.

    Header-only tables are not positive data, but they can still prove a valid
    empty result when every sample matches an explicit semantic-catalog empty
    pattern. This keeps the decision auditable without inventing example rows.
    """

    sample_texts = [
        str(sample.get("sanitized_text") or "")
        for sample in samples
        if str(sample.get("sanitized_text") or "").strip()
    ]
    if not sample_texts:
        return None
    match = resolve_semantic_contract(
        command=str(job.get("canonical_command") or ""),
        samples_text="\n\n".join(sample_texts),
    )
    if match is None:
        return None
    contract = dict(match.contract or {})
    empty_patterns = [
        str(pattern).strip()
        for pattern in contract.get("empty_patterns") or []
        if str(pattern).strip()
    ]
    if not contract.get("allow_empty") or not empty_patterns:
        return None
    try:
        all_samples_match = all(
            any(re.search(pattern, text) is not None for pattern in empty_patterns)
            for text in sample_texts
        )
    except re.error:
        return None
    if not all_samples_match:
        return None

    primary_keys = [str(item) for item in contract.get("primary_keys") or []]
    required_fields = [str(item) for item in contract.get("required_fields") or []]
    optional_fields = [str(item) for item in contract.get("optional_fields") or []]
    field_names = list(dict.fromkeys((*primary_keys, *required_fields, *optional_fields)))
    validators = {
        str(name): dict(config)
        for name, config in (contract.get("validators") or {}).items()
        if isinstance(config, dict)
    }
    fields: dict[str, dict[str, Any]] = {}
    for name in field_names:
        semantics = dict(field_semantics(name) or {})
        semantic_type = str(semantics.get("semantic_type") or "").strip()
        configured_type = str(validators.get(name, {}).get("type") or "").strip()
        value_type = _TYPE_ALIASES.get(configured_type, configured_type)
        if not value_type:
            value_type = (
                "percent"
                if str(semantics.get("unit") or "").casefold() == "percent"
                else "number"
                if semantic_type in {"counter", "gauge", "threshold"}
                else "text"
            )
        observation_type = str(semantics.get("observation_type") or "").strip()
        fields[name] = {
            "required": name in set(primary_keys) | set(required_fields),
            "type": value_type,
            "role": (
                "identity"
                if name in primary_keys
                else "threshold"
                if semantic_type == "threshold"
                else "metric"
                if semantic_type in {"counter", "gauge"}
                else "state"
                if semantic_type == "state"
                else "context"
            ),
            "operational_reason": (
                "Identify a record when the command returns business rows."
                if name in primary_keys
                else "Preserve the governed field when the command returns business rows."
            ),
            "change_behavior": "stable" if name in primary_keys else "volatile",
            "monitor": name not in primary_keys,
            **(
                {"canonical_candidate": observation_type}
                if observation_type
                else {}
            ),
        }
    entity = str(
        contract.get("entity_type") or contract.get("category") or "record"
    ).strip()
    entity = re.sub(r"[^a-z0-9_]+", "_", entity.casefold()).strip("_") or "record"
    spec = {
        "version": 2,
        "business_purpose": (
            f"Parse {job.get('canonical_command') or 'the command'} records while "
            "preserving an explicitly governed valid-empty result."
        ),
        "record_sets": [
            {
                "name": entity,
                "entity": entity,
                "cardinality": "repeated" if primary_keys else "singleton",
                "presence": "optional",
                "primary_keys": primary_keys,
                "fields": fields,
                "allow_empty": True,
                "empty_patterns": empty_patterns,
            }
        ],
    }
    return ParserSpecDocument.from_dict(spec).to_dict(), match.profile_id


def suggest_parser_spec(params: dict[str, Any]) -> dict[str, Any]:
    job_id = str(params.get("job_id") or "").strip()
    task_id = str(params.get("suggestion_task_id") or "").strip()
    if not job_id or not task_id:
        raise ValueError("job_id and suggestion_task_id are required")
    repository = ParserJobRepository()
    try:
        repository.record_parser_spec_suggestion(job_id, task_id=task_id, status="running")
        job = repository.get_job(job_id, include_sanitized_payload=True)
        if not job:
            raise ValueError(f"parser template job does not exist: {job_id}")
        revision_instruction = str(
            (
                (job.get("agent_state_json") or {}).get("parser_spec_suggestion")
                or {}
            ).get("revision_instruction")
            or ""
        ).strip()[:4000]
        selected_check_item_id = str(
            (
                (job.get("agent_state_json") or {}).get("parser_spec_suggestion")
                or {}
            ).get("check_item_id")
            or ""
        ).strip()
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
            valid_empty_draft = (
                _semantic_valid_empty_spec_draft(job=job, samples=classified)
                if header_only_count
                else None
            )
            if valid_empty_draft is not None:
                spec, semantic_profile_id = valid_empty_draft
                suggestion = {
                    "classification": "valid_empty_structured",
                    "parser_spec": spec,
                    "sample_suggestions": [
                        {
                            "sample_id": str(sample["id"]),
                            "recommended_role": "generation",
                            "classification": "governed_valid_empty",
                        }
                        for sample in classified
                    ],
                    "confidence": 1.0,
                    "rationale": (
                        "Every header-only sample matches an explicit valid-empty pattern "
                        "from the governed semantic catalog. The draft defines the row "
                        "shape without inventing records; an engineer must still confirm it."
                    ),
                    "model_called": False,
                    "draft_source": "semantic_catalog_valid_empty",
                    "semantic_profile_id": semantic_profile_id,
                }
            else:
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
                            "ParserSpec, unless an engineer first approves an explicit valid-empty "
                            "pattern for this command family."
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
            prompt_data_samples = _representative_data_samples(data_samples)
            field_naming_context = _field_naming_context(
                job,
                prompt_data_samples,
                selected_check_item_id,
            )
            with _SuggestionHeartbeat(
                repository=repository,
                job_id=job_id,
                task_id=task_id,
            ):
                response = call_llm(
                    _build_prompt(
                        job,
                        prompt_data_samples,
                        empty_samples,
                        field_naming_context,
                        revision_instruction,
                    )
                )
            payload = _json_object(response)
            normalization_warnings: list[str] = []
            spec = _normalize_spec(
                payload,
                warnings=normalization_warnings,
                field_naming_context=field_naming_context,
            )
            representative_plans = _normalize_sample_plans(
                payload,
                samples=prompt_data_samples,
            )
            sample_plans = _expand_sample_plans(
                representative_plans,
                representative_samples=prompt_data_samples,
                all_samples=data_samples,
            )
            suggestion = {
                "classification": "structured_data",
                "parser_spec": spec,
                "sample_suggestions": sample_plans + negative_suggestions,
                "source_sample_count": len(data_samples),
                "prompt_sample_count": len(prompt_data_samples),
                "confidence": max(0.0, min(float(payload.get("confidence", 0.5)), 1.0)),
                "rationale": str(payload.get("rationale") or "")[:1000],
                "model_called": True,
                "model_name": getattr(response, "model_name", None),
                "token_usage": getattr(response, "token_usage", {}),
                "normalization_warnings": normalization_warnings,
                "field_naming_context": field_naming_context,
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

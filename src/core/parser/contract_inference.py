"""Evidence-bound Parser Contract inference for TextFSM generation."""

from __future__ import annotations

import ast
import ipaddress
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any

from .evidence_utils import literal_occurs
from .policy import CategorySpec
from .sanitization import runtime_pattern_for_redacted_value
from .semantic_catalog import (
    SemanticCatalogMatch,
    field_aliases,
    resolve_semantic_contract,
)
from .semantic_catalog import (
    field_semantics as catalog_field_semantics,
)
from .structure_profiler import profile_cli_structure


@dataclass(frozen=True)
class ContractProposal:
    category: str
    entity_type: str
    primary_keys: tuple[str, ...]
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...] = ()
    validators: dict[str, dict[str, Any]] | None = None
    allow_empty: bool = False
    empty_patterns: tuple[str, ...] = ()
    ignore_fields: tuple[str, ...] = ()
    state_semantics: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    source: str = "semantic_catalog"
    confidence: float = 0.9

    def to_category_spec(self) -> CategorySpec:
        return CategorySpec(
            name=self.category,
            entity_type=self.entity_type,
            primary_keys=self.primary_keys,
            required_fields=self.required_fields,
            optional_fields=self.optional_fields,
            validators=self.validators or {},
            allow_empty=self.allow_empty,
            empty_patterns=self.empty_patterns,
            ignore_fields=self.ignore_fields,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state_semantics"] = self.state_semantics or infer_field_semantics(self)
        return data


def infer_field_semantics(proposal: ContractProposal) -> dict[str, Any]:
    """Resolve governed field semantics without vendor/model branches."""

    semantics: dict[str, Any] = {}
    key_fields = {field.casefold() for field in proposal.primary_keys}
    ignored = {field.casefold() for field in proposal.ignore_fields}
    for raw_field in (*proposal.required_fields, *proposal.optional_fields):
        field = str(raw_field)
        normalized = field.casefold()
        if normalized in key_fields:
            semantics[field] = {"semantic_type": "identity"}
        elif normalized in ignored:
            semantics[field] = {"semantic_type": "text", "ignore": True}
        else:
            semantics[field] = catalog_field_semantics(field) or {"semantic_type": "text"}
    return semantics


def _semantic_reference_fields(value: Any) -> set[str]:
    fields: set[str] = set()
    if isinstance(value, list):
        for item in value:
            fields.update(_semantic_reference_fields(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in {"field", "other_field"} and isinstance(item, str):
                field = item.strip()
                if field:
                    fields.add(field)
            else:
                fields.update(_semantic_reference_fields(item))
    return fields


def _govern_field_semantics(proposal: ContractProposal) -> ContractProposal:
    governed = infer_field_semantics(proposal)
    proposed = dict(proposal.state_semantics or {})
    for field, semantics in tuple(governed.items()):
        profile_override = proposal.source.startswith("semantic_catalog:")
        if isinstance(proposed.get(field), dict) and (
            profile_override or catalog_field_semantics(field) is None
        ):
            governed[field] = {**semantics, **dict(proposed[field])}

    declared = {*proposal.required_fields, *proposal.optional_fields}
    removed_applicability: dict[str, dict[str, Any]] = {}
    removed_units: dict[str, dict[str, Any]] = {}
    for field, metadata in tuple(governed.items()):
        if not isinstance(metadata, dict):
            continue
        governed[field] = dict(metadata)
        semantic_type = str(metadata.get("semantic_type") or "text").casefold()
        if metadata.get("unit") and semantic_type not in {"gauge", "counter"}:
            removed_units[field] = {
                "reason": "unit_requires_numeric_semantic_type",
                "semantic_type": semantic_type,
                "removed_unit": str(metadata.get("unit")),
            }
            governed[field].pop("unit", None)
        applicability = metadata.get("applicability")
        if isinstance(applicability, dict):
            referenced = _semantic_reference_fields(applicability)
            missing = sorted(referenced - declared)
            if missing:
                governed[field].pop("applicability", None)
                removed_applicability[field] = {
                    "reason": "undeclared_context_fields",
                    "missing_fields": missing,
                }

    evidence = json.loads(json.dumps(dict(proposal.evidence or {})))
    if removed_applicability or removed_units:
        governance = dict(evidence.get("semantic_reference_governance") or {})
        if removed_applicability:
            governance["removed_applicability"] = removed_applicability
        if removed_units:
            governance["removed_units"] = removed_units
        evidence["semantic_reference_governance"] = governance
    return replace(
        proposal,
        state_semantics=governed,
        evidence=evidence or None,
    )


def govern_contract_candidate(proposal: ContractProposal) -> ContractProposal:
    """Reapply current deterministic governance to a persisted Contract."""

    return _govern_field_semantics(proposal)


def _extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", str(text or ""))
    if not match:
        raise ValueError("contract inference did not return a JSON object")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("contract inference JSON must be an object")
    return value


_SAMPLE_BLOCK_RE = re.compile(
    r'<CLI_SAMPLE\s+id="(?P<sample_id>[^"]+)"[^>]*>\s*\n?'
    r"(?P<body>[\s\S]*?)\n?\s*</CLI_SAMPLE>"
)


def _normalize_binding_lines(
    proposal: ContractProposal,
    *,
    samples_text: str,
) -> ContractProposal:
    """Correct unambiguous LLM line-number drift from blank-line counting."""

    evidence = json.loads(json.dumps(dict(proposal.evidence or {})))
    field_bindings = evidence.get("field_bindings")
    if not isinstance(field_bindings, dict):
        return proposal
    samples = {
        match.group("sample_id"): match.group("body").splitlines()
        for match in _SAMPLE_BLOCK_RE.finditer(samples_text or "")
    }
    corrections: list[dict[str, Any]] = []
    for field_name, raw_bindings in field_bindings.items():
        bindings = raw_bindings if isinstance(raw_bindings, list) else [raw_bindings]
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            sample_id = str(binding.get("sample_id") or "").strip()
            lines = samples.get(sample_id)
            if not lines:
                continue
            try:
                requested = int(binding.get("line") or binding.get("line_number"))
            except (TypeError, ValueError):
                continue
            source_label = str(binding.get("source_label") or "").strip().casefold()
            source_value = str(binding.get("source_value") or "").strip().casefold()

            def present(line: str) -> bool:
                return bool(
                    (source_label and literal_occurs(line, source_label))
                    or (source_value and literal_occurs(line, source_value))
                )

            if 1 <= requested <= len(lines) and present(lines[requested - 1]):
                continue
            label_matches = [
                index
                for index, line in enumerate(lines, 1)
                if source_label and literal_occurs(line, source_label)
            ]
            value_matches = [
                index
                for index, line in enumerate(lines, 1)
                if source_value and literal_occurs(line, source_value)
            ]
            matches = label_matches or value_matches
            if not matches:
                continue
            if len(matches) == 1:
                resolved = matches[0]
                strategy = "unique_literal_match"
            else:
                ranked = sorted((abs(index - requested), index) for index in matches)
                if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
                    continue
                resolved = ranked[0][1]
                strategy = "nearest_literal_match"
            binding["line"] = resolved
            binding.pop("line_number", None)
            corrections.append(
                {
                    "field": str(field_name),
                    "sample_id": sample_id,
                    "from_line": requested,
                    "to_line": resolved,
                    "strategy": strategy,
                }
            )
    if corrections:
        evidence["line_normalization"] = {
            "strategy": "literal_line_reconciliation",
            "corrections": corrections,
        }
    return replace(proposal, evidence=evidence)


def _validator_accepts(value: str, rules: dict[str, Any]) -> bool:
    rule_type = str(rules.get("type") or "").strip().casefold()
    text = str(value or "").strip()
    if not text:
        return False
    if rule_type == "number":
        try:
            float(text)
        except ValueError:
            return False
    elif rule_type == "percent":
        try:
            number = float(text.rstrip("%"))
        except ValueError:
            return False
        return 0 <= number <= 100
    elif rule_type == "enum":
        allowed = {str(item).strip().casefold() for item in rules.get("values") or []}
        return not allowed or text.casefold() in allowed
    elif rule_type == "regex":
        pattern = str(rules.get("pattern") or "")
        return not pattern or re.fullmatch(pattern, text) is not None
    elif rule_type == "interface_name":
        return text.casefold() not in {
            "interface",
            "port",
            "name",
            "interface name",
            "port name",
        }
    return True


def _normalize_validator_from_values(
    rules: dict[str, Any],
    values: list[str],
) -> tuple[dict[str, Any], bool]:
    """Make weak-model validators cover every value in a proven table column."""

    normalized = dict(rules or {})
    invalid = [value for value in values if not _validator_accepts(value, normalized)]
    if not invalid:
        return normalized, False
    rule_type = str(normalized.get("type") or "").strip().casefold()
    if rule_type == "enum":
        observed = list(
            dict.fromkeys(
                [
                    *(str(item) for item in normalized.get("values") or []),
                    *values,
                ]
            )
        )
        if len(observed) <= 64:
            normalized["values"] = observed
        else:
            normalized = {"type": "text"}
        return normalized, True
    if rule_type == "regex":
        ip_or_missing = True
        has_ip = False
        for raw in values:
            text = raw.strip()
            if text.casefold() in {"unassigned", "--", "none", "n/a", "na"}:
                continue
            if "." not in text and ":" not in text:
                ip_or_missing = False
                break
            try:
                ipaddress.ip_interface(text)
            except ValueError:
                ip_or_missing = False
                break
            has_ip = True
        if ip_or_missing and has_ip:
            return {"type": "ip_or_unassigned"}, True
        return {"type": "text"}, True
    if rule_type == "ip" and all(
        value.strip().casefold() in {"unassigned", "--", "none", "n/a", "na"} for value in invalid
    ):
        return {"type": "ip_or_unassigned"}, True
    return normalized, False


def _header_field_spans(
    header: str,
    fields: tuple[str, ...],
) -> dict[str, tuple[int, int, str]]:
    """Map governed field aliases to fixed-width header spans."""

    matches: dict[str, tuple[int, int, str]] = {}
    for field in fields:
        exact_alias = str(field).replace("_", " ").strip()

        def find_aliases(aliases: set[str]) -> list[tuple[int, int, str]]:
            found: list[tuple[int, int, str]] = []
            for alias in aliases:
                words = re.findall(r"[A-Za-z0-9]+", alias)
                if not words:
                    continue
                pattern = (
                    r"(?<![A-Za-z0-9])"
                    + r"\s+".join(re.escape(word) for word in words)
                    + r"(?![A-Za-z0-9])"
                )
                for match in re.finditer(pattern, header, re.IGNORECASE):
                    found.append((match.start(), match.end(), match.group(0)))
            return found

        candidates = find_aliases({exact_alias})
        if not candidates:
            candidates = find_aliases({str(item).strip() for item in field_aliases(field)})
        if candidates:
            matches[field] = max(
                candidates,
                key=lambda item: (item[1] - item[0], -item[0]),
            )

    # One physical column cannot prove two synonymous Contract fields. Leave
    # ambiguous spans unresolved so the Agent requests a Contract revision.
    span_counts: dict[tuple[int, int], int] = {}
    for start, end, _label in matches.values():
        span_counts[(start, end)] = span_counts.get((start, end), 0) + 1
    return {
        field: value for field, value in matches.items() if span_counts[(value[0], value[1])] == 1
    }


def _normalize_table_column_bindings(
    proposal: ContractProposal,
    *,
    samples_text: str,
) -> ContractProposal:
    """Rebuild table evidence from header positions instead of model guesses."""

    samples = {
        match.group("sample_id"): match.group("body").splitlines()
        for match in _SAMPLE_BLOCK_RE.finditer(samples_text or "")
    }
    if not samples:
        return proposal
    declared = (*proposal.required_fields, *proposal.optional_fields)
    validators = dict(proposal.validators or {})
    evidence = json.loads(json.dumps(dict(proposal.evidence or {})))
    bindings = dict(evidence.get("field_bindings") or {})
    rebuilt: dict[str, list[dict[str, Any]]] = {field: [] for field in declared}
    region_coverage: dict[str, set[tuple[str, int]]] = {field: set() for field in declared}
    normalized_validator_fields: set[str] = set()
    all_regions: set[tuple[str, int]] = set()
    layouts: set[str] = set()

    for sample_id, lines in samples.items():
        plan = profile_cli_structure("\n".join(lines), sample_id=sample_id)
        layouts.add(plan.layout_kind)
        for region in plan.table_regions:
            region_key = (sample_id, region.header_line_number)
            all_regions.add(region_key)
            header = lines[region.header_line_number - 1]
            spans = _header_field_spans(header, declared)
            ordered_starts = sorted({start for start, _end, _label in spans.values()})
            for field, (start, _match_end, label) in spans.items():
                next_starts = [value for value in ordered_starts if value > start]
                end = next_starts[0] if next_starts else None
                column_values = [
                    lines[line_number - 1][start:end].strip()
                    for line_number in range(
                        region.first_row_line_number,
                        region.last_row_line_number + 1,
                    )
                    if lines[line_number - 1][start:end].strip()
                ]
                normalized_rules, changed = _normalize_validator_from_values(
                    dict(validators.get(field) or {}),
                    column_values,
                )
                if changed:
                    validators[field] = normalized_rules
                    normalized_validator_fields.add(field)
                selected: tuple[int, str] | None = None
                for line_number in range(
                    region.first_row_line_number,
                    region.last_row_line_number + 1,
                ):
                    line = lines[line_number - 1]
                    candidate = line[start:end].strip()
                    if _validator_accepts(candidate, dict(validators.get(field) or {})):
                        selected = (line_number, candidate)
                        break
                if selected is None:
                    continue
                line_number, source_value = selected
                rebuilt[field].append(
                    {
                        "sample_id": sample_id,
                        "line": line_number,
                        "header_line": region.header_line_number,
                        "source_label": label.strip(),
                        "source_value": source_value,
                        "capture_kind": "column",
                    }
                )
                region_coverage[field].add(region_key)

    replaced_fields = [field for field, values in rebuilt.items() if values]
    if not replaced_fields:
        return proposal
    for field in replaced_fields:
        bindings[field] = rebuilt[field]

    required = list(proposal.required_fields)
    optional = list(proposal.optional_fields)
    reclassified: list[str] = []
    if "multi_table" in layouts and all_regions:
        for field in tuple(required):
            if field in proposal.primary_keys:
                continue
            if region_coverage.get(field, set()) == all_regions:
                continue
            required.remove(field)
            if field not in optional:
                optional.append(field)
            reclassified.append(field)

    evidence["field_bindings"] = bindings
    evidence["table_binding_normalization"] = {
        "strategy": "fixed_width_header_alignment",
        "region_count": len(all_regions),
        "rebuilt_fields": replaced_fields,
        "reclassified_fields": reclassified,
        "normalized_validator_fields": sorted(normalized_validator_fields),
    }
    return replace(
        proposal,
        required_fields=tuple(required),
        optional_fields=tuple(optional),
        validators=validators,
        evidence=evidence,
    )


def _normalize_capture_requirements(
    proposal: ContractProposal,
    *,
    structure_profile: dict[str, Any] | None = None,
) -> ContractProposal:
    """Keep required fields only where every entity can expose them directly."""

    evidence = json.loads(json.dumps(dict(proposal.evidence or {})))
    bindings = dict(evidence.get("field_bindings") or {})
    required = list(proposal.required_fields)
    optional = list(proposal.optional_fields)
    primary = set(proposal.primary_keys)
    plans = list((structure_profile or {}).get("samples") or [])
    multi_entity_layouts = {
        str(plan.get("layout_kind") or "")
        for plan in plans
        if int(plan.get("estimated_entities") or 0) > 1
    }
    reclassified: list[str] = []
    for field_name in tuple(required):
        raw = bindings.get(field_name) or []
        items = raw if isinstance(raw, list) else [raw]
        kinds = {str(item.get("capture_kind") or "") for item in items if isinstance(item, dict)}
        table_column_proven = (
            bool(multi_entity_layouts)
            and all(
                layout in {"table", "multi_table", "sectioned_table"}
                for layout in multi_entity_layouts
            )
            and kinds == {"column"}
        )
        unsupported_requirement = kinds == {"derived"} or (
            bool(multi_entity_layouts) and not table_column_proven
        )
        if field_name in primary or not kinds or not unsupported_requirement:
            continue
        required.remove(field_name)
        if field_name not in optional:
            optional.append(field_name)
        reclassified.append(field_name)
    if not reclassified:
        return proposal
    evidence["capture_normalization"] = {
        "strategy": "required_fields_must_be_directly_available_per_entity",
        "reclassified_fields": reclassified,
    }
    return replace(
        proposal,
        required_fields=tuple(required),
        optional_fields=tuple(optional),
        evidence=evidence,
    )


def _normalize_redacted_value_validators(
    proposal: ContractProposal,
) -> ContractProposal:
    """Compile privacy placeholders into validators for real runtime values."""

    evidence = json.loads(json.dumps(dict(proposal.evidence or {})))
    bindings = dict(evidence.get("field_bindings") or {})
    validators = json.loads(json.dumps(dict(proposal.validators or {})))
    normalized: dict[str, dict[str, Any]] = {}
    missing_markers = {"none", "n/a", "na", "--", "unknown", "unavailable"}
    for field_name in (*proposal.required_fields, *proposal.optional_fields):
        raw = bindings.get(field_name) or []
        items = raw if isinstance(raw, list) else [raw]
        values = [
            str(item.get("source_value") or "").strip()
            for item in items
            if isinstance(item, dict) and str(item.get("source_value") or "").strip()
        ]
        runtime_patterns = list(
            dict.fromkeys(
                pattern
                for value in values
                if (pattern := runtime_pattern_for_redacted_value(value))
            )
        )
        if not runtime_patterns:
            continue
        marker_literals = sorted(
            {re.escape(value) for value in values if value.casefold() in missing_markers}
        )
        fragments = [*runtime_patterns, *marker_literals]
        validators[field_name] = {
            "type": "regex",
            "pattern": "^(?:" + "|".join(fragments) + ")$",
        }
        normalized[field_name] = {
            "placeholders": sorted(
                {value for value in values if runtime_pattern_for_redacted_value(value)}
            ),
            "runtime_pattern": validators[field_name]["pattern"],
        }
    if not normalized:
        return proposal
    evidence["redaction_validator_normalization"] = {
        "strategy": "privacy_placeholder_to_runtime_type",
        "fields": normalized,
    }
    return replace(proposal, validators=validators, evidence=evidence)


def _field_identifier(value: Any) -> str:
    identifier = re.sub(
        r"[^a-z0-9]+",
        "_",
        str(value or "").strip().casefold(),
    ).strip("_")
    if not identifier:
        raise ValueError(f"inferred field name is not an ASCII identifier: {value!r}")
    if identifier[0].isdigit():
        identifier = f"field_{identifier}"
    return identifier


def _normalize_contract_field_identifiers(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize every Contract reference to one stable TextFSM identifier."""

    payload = json.loads(json.dumps(dict(raw or {})))
    required_raw = [str(item) for item in payload.get("required_fields") or ()]
    optional_raw = [str(item) for item in payload.get("optional_fields") or ()]
    declared_raw = [*required_raw, *optional_raw]
    mapping = {field: _field_identifier(field) for field in declared_raw}
    reverse: dict[str, set[str]] = {}
    for original, normalized in mapping.items():
        reverse.setdefault(normalized, set()).add(original)
    collisions = {
        normalized: sorted(originals)
        for normalized, originals in reverse.items()
        if len(originals) > 1
    }
    if collisions:
        raise ValueError(
            "inferred contract fields collide after identifier normalization: "
            + json.dumps(collisions, ensure_ascii=True, sort_keys=True)
        )
    casefold_mapping = {key.casefold(): value for key, value in mapping.items()}

    def resolve(value: Any) -> str:
        text = str(value or "")
        return mapping.get(text) or casefold_mapping.get(text.casefold()) or _field_identifier(text)

    payload["required_fields"] = [resolve(item) for item in required_raw]
    payload["optional_fields"] = [resolve(item) for item in optional_raw]
    payload["primary_keys"] = [resolve(item) for item in payload.get("primary_keys") or ()]
    payload["ignore_fields"] = [resolve(item) for item in payload.get("ignore_fields") or ()]
    payload["validators"] = {
        resolve(field): rules for field, rules in dict(payload.get("validators") or {}).items()
    }

    def remap_references(value: Any) -> Any:
        if isinstance(value, list):
            return [remap_references(item) for item in value]
        if not isinstance(value, dict):
            return value
        output: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"field", "other_field"} and isinstance(item, str):
                output[key] = resolve(item)
            else:
                output[key] = remap_references(item)
        return output

    state_semantics = payload.get("state_semantics")
    if isinstance(state_semantics, dict):
        payload["state_semantics"] = {
            resolve(field): remap_references(metadata)
            for field, metadata in state_semantics.items()
        }
    evidence = payload.get("evidence")
    if isinstance(evidence, dict):
        field_bindings = evidence.get("field_bindings")
        if isinstance(field_bindings, dict):
            evidence["field_bindings"] = {
                resolve(field): bindings for field, bindings in field_bindings.items()
            }
        changed = {
            original: normalized
            for original, normalized in mapping.items()
            if original != normalized
        }
        if changed:
            evidence["field_name_normalization"] = {
                "strategy": "ascii_snake_case",
                "mapping": changed,
            }
        payload["evidence"] = remap_references(evidence)
    return payload


def _proposal_from_payload(
    raw: dict[str, Any],
    *,
    source: str,
) -> ContractProposal:
    raw = _normalize_contract_field_identifiers(raw)
    required = tuple(str(item) for item in raw.get("required_fields") or ())
    optional = tuple(str(item) for item in raw.get("optional_fields") or ())
    primary_keys = tuple(str(item) for item in raw.get("primary_keys") or ())
    if not required:
        raise ValueError("inferred contract has no required_fields")
    if len({*required, *optional}) != len(required) + len(optional):
        raise ValueError("inferred contract declares duplicate fields")
    declared_fields = {*required, *optional}
    if any(key not in declared_fields for key in primary_keys):
        raise ValueError("inferred primary key is not a declared field")
    validator_aliases = {
        "numeric": "number",
        "float": "number",
        "integer": "number",
        "percentage": "percent",
        "ip_address": "ip",
        "interface": "interface_name",
        "string": "text",
    }
    validators: dict[str, dict[str, Any]] = {}
    invalid_validators: list[str] = []
    for raw_field, raw_rules in dict(raw.get("validators") or {}).items():
        field = str(raw_field)
        if isinstance(raw_rules, dict):
            rules = dict(raw_rules)
            rule_type = str(rules.get("type") or "").strip().casefold()
            if rule_type:
                rules["type"] = validator_aliases.get(rule_type, rule_type)
            validators[field] = rules
        elif isinstance(raw_rules, str) and raw_rules.strip():
            text = raw_rules.strip()
            pattern_match = re.match(r"(?is)^pattern\s*:\s*(.+)$", text)
            enum_match = re.match(r"(?is)^enum\s*:\s*(.+)$", text)
            type_match = re.search(r"(?i)\btype\s*:\s*([a-z_]+)", text)
            range_match = re.search(
                r"(?i)\brange\s*:\s*\[\s*([-+]?\d+(?:\.\d+)?)\s*,\s*"
                r"([-+]?\d+(?:\.\d+)?)\s*\]",
                text,
            )
            if pattern_match:
                validators[field] = {
                    "type": "regex",
                    "pattern": pattern_match.group(1).strip(),
                }
            elif enum_match:
                raw_values = enum_match.group(1).strip()
                try:
                    parsed_values = ast.literal_eval(raw_values)
                except (SyntaxError, ValueError):
                    parsed_values = [item.strip() for item in raw_values.split("|")]
                if not isinstance(parsed_values, (list, tuple, set)):
                    parsed_values = [parsed_values]
                validators[field] = {
                    "type": "enum",
                    "values": [str(item) for item in parsed_values],
                }
            elif type_match:
                rule_type = type_match.group(1).casefold()
                validators[field] = {"type": validator_aliases.get(rule_type, rule_type)}
                if range_match:
                    validators[field]["minimum"] = float(range_match.group(1))
                    validators[field]["maximum"] = float(range_match.group(2))
            else:
                rule_type = text.casefold()
                validators[field] = {"type": validator_aliases.get(rule_type, rule_type)}
        elif isinstance(raw_rules, list) and raw_rules:
            validators[field] = {"type": "enum", "values": list(raw_rules)}
        else:
            invalid_validators.append(field)
    if invalid_validators:
        raise ValueError(
            "inferred validators must be objects: " + ", ".join(sorted(invalid_validators))
        )
    raw_semantics = raw.get("state_semantics") or {}
    normalized_semantics: dict[str, dict[str, Any]] = {}
    if isinstance(raw_semantics, dict):
        for raw_field, value in raw_semantics.items():
            if isinstance(value, dict):
                normalized_semantics[str(raw_field)] = dict(value)
            elif isinstance(value, str) and value.strip():
                normalized_semantics[str(raw_field)] = {"description": value.strip()}
    proposal = ContractProposal(
        category=str(raw.get("category") or "record"),
        entity_type=str(raw.get("entity_type") or raw.get("category") or "record"),
        primary_keys=primary_keys,
        required_fields=required,
        optional_fields=optional,
        validators=validators,
        allow_empty=bool(raw.get("allow_empty", False)),
        empty_patterns=tuple(str(item) for item in raw.get("empty_patterns") or ()),
        ignore_fields=tuple(str(item) for item in raw.get("ignore_fields") or ()),
        state_semantics=normalized_semantics or None,
        evidence=dict(raw.get("evidence") or {}) or None,
        source=source,
        confidence=float(raw.get("confidence") or 0.7),
    )
    return govern_contract_candidate(proposal)


def _proposal_from_catalog(
    match: SemanticCatalogMatch,
    *,
    llm_fallback_reason: str | None = None,
) -> ContractProposal:
    payload = dict(match.contract)
    evidence = match.evidence()
    if llm_fallback_reason:
        evidence["llm_fallback_reason"] = llm_fallback_reason
    payload["evidence"] = evidence
    payload["confidence"] = min(0.99, 0.75 + match.score / 10000)
    return _proposal_from_payload(
        payload,
        source=f"semantic_catalog:{match.profile_id}",
    )


def _catalog_compatibility_errors(
    proposal: ContractProposal,
    match: SemanticCatalogMatch,
) -> list[str]:
    expected = match.contract
    errors: list[str] = []
    if proposal.entity_type != str(expected.get("entity_type") or ""):
        errors.append(f"entity_type {proposal.entity_type!r} conflicts with semantic evidence")
    if proposal.primary_keys != tuple(str(item) for item in expected.get("primary_keys") or ()):
        errors.append("primary_keys conflict with the governed semantic identity")
    declared = {*proposal.required_fields, *proposal.optional_fields}
    anchors = set(match.semantic_anchor_fields)
    if anchors and not anchors.intersection(declared):
        errors.append("proposal omits every semantic anchor evidenced by the samples")
    if not proposal.evidence:
        errors.append("proposal has no line-level evidence")
    return errors


def infer_contract(
    *,
    command: str,
    samples_text: str,
    structure_profile: dict[str, Any] | None = None,
    llm_call: Callable[[str], str] | None = None,
    review_catalog: bool = False,
    use_manual_profile: bool = False,
) -> ContractProposal:
    """Infer one Contract from sample evidence.

    Automatic generation always uses the evidence-bound model path. Command-based
    catalog profiles remain available only through the explicit manual provider
    switch for compatibility and emergency fallback.
    """

    catalog_match = None
    if use_manual_profile or review_catalog:
        catalog_match = resolve_semantic_contract(
            command=command,
            samples_text=samples_text,
        )
    if catalog_match is not None and use_manual_profile:
        return _proposal_from_catalog(catalog_match)
    if llm_call is None:
        raise ValueError(
            "automatic contract inference requires an LLM callback; "
            "set use_manual_profile=True only for the manual fallback"
        )

    catalog_hint = (
        {
            "profile_id": catalog_match.profile_id,
            "contract": catalog_match.contract,
            "semantic_anchor_fields": list(catalog_match.semantic_anchor_fields),
            "catalog_version": catalog_match.catalog_version,
        }
        if catalog_match
        else None
    )
    prompt = f"""Infer a stable data contract for network CLI output.
Canonical command: {command}
The command is metadata, not a schema selector. Infer the schema from sample
evidence. Optional manual semantic hint:
{json.dumps(catalog_hint, ensure_ascii=True, sort_keys=True)}

Deterministic structure profile:
{json.dumps(structure_profile or {}, ensure_ascii=True, sort_keys=True)}

Sanitized samples:
{samples_text}

Return JSON only with category, entity_type, primary_keys, required_fields,
optional_fields, validators, allow_empty, empty_patterns, ignore_fields,
state_semantics, confidence, and evidence. Evidence must contain a
field_bindings object. Every declared field maps to one or more objects with
sample_id, line, source_label, source_value, and capture_kind. Use capture_kind
column, key_value, record_anchor, or derived. source_label and source_value must
be literal text copied from the referenced line, never role names such as
entity_header or module_data. Required fields and primary keys must be directly
capturable and cannot use capture_kind=derived. They must be evidenced in every
non-empty positive sample; optional fields need at least one real occurrence.
Field names must be lowercase ASCII snake_case identifiers without spaces.
For repeated or nested multi-entity blocks, make only the entity key required
unless line evidence proves a field occurs in every entity. A field that exists
only for some entity variants is optional even when it is operationally important.
Never cite a heading or value that is absent from the referenced line.
Validator values must be JSON objects such as {{"type":"number"}},
{{"type":"enum","values":["up","down"]}}, or
{{"type":"regex","pattern":"..."}}.
Every validator must accept every non-missing source_value cited for its field.
Do not bind two different scalar values for the same field to the same line.
Primary keys must identify records within one device and command snapshot.
Composite primary keys are conjunctive: every emitted record must contain every
key field. Never use different sibling record-anchor variants as separate key
fields. When one output mixes sibling entity variants, bind one neutral identity
field across every variant anchor (and an optional type field if evidenced), or
propose separate contracts for the variants.
Do not use metrics, status, timestamps, uptime, counters, or descriptions as keys.
Use an empty primary_keys list only when every sample proves a singleton entity.
"""
    try:
        proposal = _proposal_from_payload(
            _extract_json(llm_call(prompt)),
            source="llm_proposal",
        )
    except Exception as exc:
        if catalog_match is None:
            raise
        return _proposal_from_catalog(
            catalog_match,
            llm_fallback_reason=f"model_output_invalid:{type(exc).__name__}",
        )

    proposal = _normalize_capture_requirements(
        _normalize_binding_lines(
            _normalize_table_column_bindings(
                proposal,
                samples_text=samples_text,
            ),
            samples_text=samples_text,
        ),
        structure_profile=structure_profile,
    )
    proposal = _normalize_redacted_value_validators(proposal)
    if catalog_match:
        conflicts = _catalog_compatibility_errors(proposal, catalog_match)
        if conflicts:
            return _proposal_from_catalog(
                catalog_match,
                llm_fallback_reason="; ".join(conflicts),
            )
    return proposal


def revise_contract(
    *,
    previous: ContractProposal,
    command: str,
    samples_text: str,
    structure_profile: dict[str, Any],
    diagnostic: dict[str, Any],
    llm_call: Callable[[str], str],
) -> ContractProposal:
    """Revise Contract evidence without silently weakening requirements."""

    prompt = f"""Revise a network CLI parser contract using only supplied evidence.
Canonical command: {command}
Previous contract:
{json.dumps(previous.to_dict(), ensure_ascii=True, sort_keys=True)}
Structure profile:
{json.dumps(structure_profile, ensure_ascii=True, sort_keys=True)}
Validation diagnostic:
{json.dumps(diagnostic, ensure_ascii=True, sort_keys=True)}

Sanitized samples:
{samples_text}

Return JSON only with category, entity_type, primary_keys, required_fields,
optional_fields, validators, allow_empty, empty_patterns, ignore_fields,
state_semantics, confidence, and evidence. Evidence must include field_bindings
for every declared field. Each binding must reference a supplied sample_id and
line and include literal source_label, literal source_value, and capture_kind.
Field names must be lowercase ASCII snake_case identifiers without spaces.
Required fields and primary keys cannot be derived. Validator values must be JSON
objects and must accept all cited non-missing values. Do not bind two different
scalar values for one field to one line. Do not remove a required field or primary
key merely to make validation pass. Rename, add, remove, or reclassify a field only
when the new line evidence
proves the previous semantic mapping was wrong. Use no primary key only for a
proven singleton layout. For repeated or nested multi-entity blocks, non-key
fields are optional unless evidence proves they occur in every entity.
Composite primary keys are conjunctive: every record must contain every key.
Different sibling record-anchor variants cannot be separate key fields. Replace
that invalid union with one neutral identity field bound across every sibling
variant, or propose separate contracts when the variants cannot share a schema.
"""
    proposal = _proposal_from_payload(
        _extract_json(llm_call(prompt)),
        source="llm_revision",
    )
    proposal = _normalize_capture_requirements(
        _normalize_binding_lines(
            _normalize_table_column_bindings(
                proposal,
                samples_text=samples_text,
            ),
            samples_text=samples_text,
        ),
        structure_profile=structure_profile,
    )
    return _normalize_redacted_value_validators(proposal)

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
    canonical_field_for_label,
    canonical_schema_field,
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
    canonical_entity_type: str | None = None
    entity_identity_fields: tuple[str, ...] = ()
    entity_dimensions: dict[str, Any] | None = None
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
    corrected_primary_keys: dict[str, dict[str, Any]] = {}
    for field in proposal.primary_keys:
        previous = dict(governed.get(field) or {})
        if previous != {"semantic_type": "identity"}:
            corrected_primary_keys[field] = {
                "reason": "primary_key_requires_identity_semantics",
                "previous": previous,
            }
        governed[field] = {"semantic_type": "identity"}

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
    if corrected_primary_keys or removed_applicability or removed_units:
        governance = dict(evidence.get("semantic_reference_governance") or {})
        if corrected_primary_keys:
            governance["corrected_primary_key_semantics"] = corrected_primary_keys
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

    return _govern_field_semantics(
        _normalize_redacted_value_validators(
            _normalize_evidence_bound_validators(
                _normalize_semantic_field_aliases(proposal)
            )
        )
    )


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


def _normalize_capture_kind_aliases(proposal: ContractProposal) -> ContractProposal:
    """Map common model vocabulary onto the four governed Parser IR kinds."""

    evidence = json.loads(json.dumps(dict(proposal.evidence or {})))
    field_bindings = evidence.get("field_bindings")
    if not isinstance(field_bindings, dict):
        return proposal
    primary_keys = set(proposal.primary_keys)
    aliases = {
        "table": "column",
        "table_column": "column",
        "column_value": "column",
        "kv": "key_value",
        "key-value": "key_value",
        "label_value": "key_value",
        "anchor": "record_anchor",
        "entity_header": "record_anchor",
        "record_header": "record_anchor",
        "computed": "derived",
        "inferred": "derived",
        "normalized": "derived",
    }
    normalized: list[dict[str, str]] = []
    for field_name, raw in field_bindings.items():
        bindings = raw if isinstance(raw, list) else [raw]
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            original = str(binding.get("capture_kind") or "").strip().casefold()
            target = aliases.get(original)
            if original in {"literal", "direct", "value"}:
                target = "record_anchor" if field_name in primary_keys else "key_value"
            if not target or target == original:
                continue
            binding["capture_kind"] = target
            normalized.append(
                {"field": str(field_name), "from": original, "to": target}
            )
    if not normalized:
        return proposal
    evidence["capture_kind_governance"] = {
        "strategy": "bounded_parser_ir_alias_mapping",
        "normalizations": normalized,
    }
    return replace(proposal, evidence=evidence)


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

    def context_overlap(field_name: str, lines: list[str], line_number: int) -> int:
        field_tokens = set(re.findall(r"[a-z0-9]+", field_name.casefold()))
        lower_bound = max(0, line_number - 10)
        for index in range(line_number - 2, lower_bound - 1, -1):
            heading = lines[index].strip()
            if not heading.endswith(":"):
                continue
            heading_tokens = set(re.findall(r"[a-z0-9]+", heading.casefold()))
            return len(field_tokens.intersection(heading_tokens))
        return 0

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
            contextual = [
                (context_overlap(str(field_name), lines, index), index)
                for index in matches
            ]
            best_overlap = max((overlap for overlap, _index in contextual), default=0)
            if best_overlap > 0:
                matches = [
                    index for overlap, index in contextual if overlap == best_overlap
                ]
            if (
                1 <= requested <= len(lines)
                and present(lines[requested - 1])
                and requested in matches
            ):
                continue
            if len(matches) == 1:
                resolved = matches[0]
                strategy = (
                    "section_context_match"
                    if best_overlap > 0
                    else "unique_literal_match"
                )
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


def _prune_unverifiable_field_bindings(
    proposal: ContractProposal,
    *,
    samples_text: str,
) -> ContractProposal:
    """Remove optional schema claims that have no literal sample evidence.

    A weak model may cite the right record family but attach one sibling's label
    to another sibling's line. Line reconciliation can repair unambiguous drift;
    anything still absent after that pass is not evidence and must not reach the
    template compiler. Required fields remain declared so validation continues to
    block when pruning exposes a real identity or completeness gap.
    """

    evidence = json.loads(json.dumps(dict(proposal.evidence or {})))
    raw_bindings = evidence.get("field_bindings")
    if not isinstance(raw_bindings, dict):
        return proposal
    samples = {
        match.group("sample_id"): match.group("body").splitlines()
        for match in _SAMPLE_BLOCK_RE.finditer(samples_text or "")
    }
    if not samples:
        return proposal

    optional = list(proposal.optional_fields)
    required = set(proposal.required_fields)
    primary = set(proposal.primary_keys)
    validators = json.loads(json.dumps(dict(proposal.validators or {})))
    semantics = json.loads(json.dumps(dict(proposal.state_semantics or {})))
    removed_bindings: list[dict[str, Any]] = []
    removed_fields: list[str] = []

    for field_name in tuple((*proposal.required_fields, *proposal.optional_fields)):
        raw = raw_bindings.get(field_name)
        bindings = raw if isinstance(raw, list) else [raw]
        kept: list[dict[str, Any]] = []
        seen_bindings: set[tuple[str, int, str, str, str]] = set()
        for binding in bindings:
            if not isinstance(binding, dict):
                kept.append(binding)
                continue
            sample_id = str(binding.get("sample_id") or "").strip()
            lines = samples.get(sample_id)
            line_value = binding.get("line") or binding.get("line_number")
            try:
                line_number = int(line_value)
            except (TypeError, ValueError):
                kept.append(binding)
                continue
            if lines is None or line_number < 1 or line_number > len(lines):
                kept.append(binding)
                continue
            source_label = str(binding.get("source_label") or "").strip()
            source_value = str(binding.get("source_value") or "").strip()
            line = lines[line_number - 1]
            if (
                (source_label and literal_occurs(line, source_label))
                or (source_value and literal_occurs(line, source_value))
            ):
                signature = (
                    sample_id,
                    line_number,
                    str(binding.get("capture_kind") or "").strip().casefold(),
                    source_label.casefold(),
                    source_value.casefold(),
                )
                if signature in seen_bindings:
                    removed_bindings.append(
                        {
                            "field": field_name,
                            "sample_id": sample_id,
                            "line": line_number,
                            "reason": "duplicate_evidence_binding",
                        }
                    )
                    continue
                seen_bindings.add(signature)
                kept.append(binding)
                continue
            removed_bindings.append(
                {
                    "field": field_name,
                    "sample_id": sample_id,
                    "line": line_number,
                    "reason": "literal_absent_from_referenced_line",
                }
            )

        if kept:
            raw_bindings[field_name] = kept
            continue
        if field_name in required or field_name in primary:
            raw_bindings[field_name] = []
            continue
        raw_bindings.pop(field_name, None)
        if field_name in optional:
            optional.remove(field_name)
        validators.pop(field_name, None)
        semantics.pop(field_name, None)
        removed_fields.append(field_name)

    if not removed_bindings and not removed_fields:
        return proposal
    evidence["field_bindings"] = raw_bindings
    evidence["literal_evidence_governance"] = {
        "strategy": "discard_unverifiable_model_claims",
        "removed_bindings": removed_bindings,
        "removed_optional_fields": removed_fields,
    }
    return replace(
        proposal,
        optional_fields=tuple(optional),
        validators=validators,
        state_semantics=semantics or None,
        evidence=evidence,
    )


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


def _is_literal_only_regex(pattern: str) -> bool:
    """Identify exact-value regexes that encode one observed sample literal."""

    source = str(pattern or "").strip()
    if source.startswith(r"\A"):
        source = source[2:]
    elif source.startswith("^"):
        source = source[1:]
    if source.endswith(r"\Z"):
        source = source[:-2]
    elif source.endswith("$") and not source.endswith(r"\$"):
        source = source[:-1]
    if not source:
        return False
    index = 0
    while index < len(source):
        character = source[index]
        if character == "\\":
            index += 1
            if index >= len(source) or source[index].isalnum():
                return False
        elif character in ".+*?{}[]()|^$":
            return False
        index += 1
    return True


def _normalize_validator_from_values(
    rules: dict[str, Any],
    values: list[str],
) -> tuple[dict[str, Any], bool]:
    """Make weak-model validators cover every value in a proven table column."""

    normalized = dict(rules or {})
    rule_type = str(normalized.get("type") or "").strip().casefold()
    if rule_type == "regex" and _is_literal_only_regex(
        str(normalized.get("pattern") or "")
    ):
        return {"type": "text"}, True
    invalid = [value for value in values if not _validator_accepts(value, normalized)]
    if not invalid:
        return normalized, False
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


def _normalize_evidence_bound_validators(
    proposal: ContractProposal,
) -> ContractProposal:
    """Make lexical validators accept every directly evidenced sample value.

    Regex and enum validators are often useful model suggestions, but a validator
    that rejects its own cited literal is self-contradictory. The correction stays
    conservative: numeric and typed validators are never relaxed here, while an
    over-fitted lexical regex falls back to text and enums are extended only to a
    bounded observed domain.
    """

    evidence = json.loads(json.dumps(dict(proposal.evidence or {})))
    field_bindings = dict(evidence.get("field_bindings") or {})
    validators = json.loads(json.dumps(dict(proposal.validators or {})))
    normalized_fields: dict[str, dict[str, Any]] = {}
    missing_markers = {"none", "n/a", "na", "--", "unknown", "unavailable"}
    for field_name, rules in tuple(validators.items()):
        raw = field_bindings.get(field_name) or []
        bindings = raw if isinstance(raw, list) else [raw]
        values = list(
            dict.fromkeys(
                value
                for item in bindings
                if isinstance(item, dict)
                and str(item.get("capture_kind") or "").strip().casefold() != "derived"
                and (value := str(item.get("source_value") or "").strip())
                and value.casefold() not in missing_markers
                and runtime_pattern_for_redacted_value(value) is None
            )
        )
        if not values:
            continue
        normalized, changed = _normalize_validator_from_values(dict(rules or {}), values)
        if not changed:
            continue
        validators[field_name] = normalized
        normalized_fields[field_name] = {
            "reason": "validator_rejected_literal_evidence",
            "previous": dict(rules or {}),
            "normalized": normalized,
            "evidence_value_count": len(values),
        }
    if not normalized_fields:
        return proposal
    evidence["validator_evidence_governance"] = {
        "strategy": "bounded_literal_domain_reconciliation",
        "fields": normalized_fields,
    }
    return replace(proposal, validators=validators, evidence=evidence)


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
                    + r"[^A-Za-z0-9]+".join(re.escape(word) for word in words)
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


def _stable_table_column_starts(lines: list[str], region: Any) -> tuple[int, ...]:
    """Infer fixed-width boundaries from repeated row whitespace.

    A weak Contract may omit trailing columns, so declared field spans alone
    cannot safely determine where its final field ends. Repeated row starts are
    structural evidence and remain useful even when header labels contain
    punctuation, multiple words, or fields unknown to the semantic catalog.
    """

    counts: dict[int, int] = {}
    row_count = 0
    for line_number in range(
        region.first_row_line_number,
        region.last_row_line_number + 1,
    ):
        line = lines[line_number - 1]
        starts = {
            match.start("value")
            for match in re.finditer(r"(?:^|\s{2,})(?P<value>\S)", line)
        }
        if not starts:
            continue
        row_count += 1
        for start in starts:
            counts[start] = counts.get(start, 0) + 1
    minimum_support = max(1, (row_count + 1) // 2)
    return tuple(sorted(start for start, count in counts.items() if count >= minimum_support))


def _table_region_context_signature(lines: list[str], region: Any) -> tuple[str, str]:
    """Describe a table by its header and nearest enclosing section heading."""

    header = re.sub(
        r"\s+",
        " ",
        re.sub(r"[-+]?\d+(?:\.\d+)?", "<n>", lines[region.header_line_number - 1].casefold()),
    ).strip()
    context = ""
    lower_bound = max(0, region.header_line_number - 8)
    for index in range(region.header_line_number - 2, lower_bound - 1, -1):
        candidate = lines[index].strip()
        if candidate.endswith(":"):
            context = re.sub(
                r"\s+",
                " ",
                re.sub(r"[-+]?\d+(?:\.\d+)?", "<n>", candidate.casefold()),
            ).strip()
            break
    return context, header


def _table_context_role(context: str) -> str | None:
    """Classify table sections whose identical headers carry different semantics."""

    normalized = str(context or "").casefold()
    role_markers = (
        ("warning", ("warning", "warn")),
        ("alarm", ("alarm", "critical")),
        ("threshold", ("threshold", "limit")),
        ("current", ("current", "actual", "measured", "reading", "value")),
    )
    for role, markers in role_markers:
        if any(marker in normalized for marker in markers):
            return role
    return None


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
    plans = {
        sample_id: profile_cli_structure("\n".join(lines), sample_id=sample_id)
        for sample_id, lines in samples.items()
    }
    original_region_signatures: dict[str, set[tuple[str, str]]] = {
        field: set() for field in declared
    }
    for field, raw in bindings.items():
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            if not isinstance(item, dict):
                continue
            sample_id = str(item.get("sample_id") or "").strip()
            lines = samples.get(sample_id)
            plan = plans.get(sample_id)
            try:
                line_number = int(item.get("line") or item.get("line_number"))
            except (TypeError, ValueError):
                continue
            if not lines or plan is None:
                continue
            for region in plan.table_regions:
                if region.first_row_line_number <= line_number <= region.last_row_line_number:
                    original_region_signatures.setdefault(str(field), set()).add(
                        _table_region_context_signature(lines, region)
                    )
                    break

    for sample_id, lines in samples.items():
        plan = plans[sample_id]
        layouts.add(plan.layout_kind)
        for region in plan.table_regions:
            region_key = (sample_id, region.header_line_number)
            all_regions.add(region_key)
            header = lines[region.header_line_number - 1]
            spans = _header_field_spans(header, declared)
            ordered_starts = sorted(
                {
                    *(start for start, _end, _label in spans.values()),
                    *_stable_table_column_starts(lines, region),
                }
            )
            for field, (start, _match_end, label) in spans.items():
                allowed_signatures = original_region_signatures.get(field) or set()
                governed_roles = {
                    role
                    for context, _header in allowed_signatures
                    if (role := _table_context_role(context)) is not None
                }
                if (
                    governed_roles
                    and _table_region_context_signature(lines, region)
                    not in allowed_signatures
                ):
                    continue
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


def _source_field_identifier(label: str) -> str | None:
    """Derive a stable Parser-source identifier from literal CLI text."""

    tokens = re.findall(r"[A-Za-z0-9]+", str(label or "").casefold())
    if not tokens:
        return None
    candidate = "_".join(tokens)
    if candidate[0].isdigit():
        candidate = f"value_{candidate}"
    return candidate


def _normalize_semantic_field_aliases(
    proposal: ContractProposal,
    *,
    check_item: str | None = None,
    vendor: str | None = None,
    model: str | None = None,
    command: str | None = None,
) -> ContractProposal:
    """Keep Parser fields source-shaped and retain semantic matches as hints.

    Parser contracts describe only TextFSM record fields.  They must not rename
    source fields into CheckItem/Observation vocabulary: that A→B translation is
    an immutable CommandAdapter V2 projection.  Literal evidence can still fix a
    plainly wrong *source* identifier (for example ``status`` bound to an
    ``RX power (dBm)`` label), while semantic-catalog matches are exposed only as
    non-authoritative projection candidates for the later mapping workflow.
    """

    evidence = json.loads(json.dumps(dict(proposal.evidence or {})))
    previous_scope = dict(evidence.get("value_mapping_scope") or {})
    resolved_scope = {
        "check_item": check_item or previous_scope.get("check_item") or proposal.category,
        "vendor": vendor or previous_scope.get("vendor"),
        "model": model or previous_scope.get("model"),
        "command": command or previous_scope.get("command"),
    }
    if all(str(value or "").strip() for value in resolved_scope.values()):
        evidence["value_mapping_scope"] = resolved_scope
    field_bindings = dict(evidence.get("field_bindings") or {})
    declared = [*proposal.required_fields, *proposal.optional_fields]
    primary = set(proposal.primary_keys)
    source_renames: dict[str, str] = {}
    source_rename_sources: dict[str, str] = {}
    semantic_candidates: dict[str, str] = {}
    semantic_candidate_sources: dict[str, str] = {}
    for field in declared:
        # Primary keys are public entity identity.  Keep them stable unless a
        # human explicitly edits the Parser contract.
        if field in primary:
            continue
        raw = field_bindings.get(field) or []
        bindings = raw if isinstance(raw, list) else [raw]
        source_identifiers = {
            identifier
            for item in bindings
            if isinstance(item, dict)
            and str(item.get("capture_kind") or "") not in {"derived", "record_anchor"}
            and str(item.get("source_label") or "").strip()
            != str(item.get("source_value") or "").strip()
            and (
                identifier := _source_field_identifier(
                    str(item.get("source_label") or "")
                )
            )
        }
        source_identifier = (
            next(iter(source_identifiers))
            if len(source_identifiers) == 1
            else None
        )
        if source_identifier and source_identifier != field:
            source_semantics = catalog_field_semantics(field)
            label_semantics = catalog_field_semantics(source_identifier)
            if label_semantics != source_semantics:
                source_renames[field] = source_identifier
                source_rename_sources[field] = "literal_source_label"

    def resolve_source(field: str) -> str:
        return source_renames.get(str(field), str(field))

    resolved_declared = [resolve_source(field) for field in declared]
    resolved_declared_set = set(resolved_declared)
    for field in declared:
        if field in primary:
            continue
        source_field = resolve_source(field)
        raw = field_bindings.get(field) or []
        bindings = raw if isinstance(raw, list) else [raw]
        canonical_fields = {
            canonical
            for item in bindings
            if isinstance(item, dict)
            and str(item.get("capture_kind") or "")
            not in {"derived", "record_anchor"}
            and str(item.get("source_label") or "").strip()
            != str(item.get("source_value") or "").strip()
            and (
                canonical := canonical_field_for_label(
                    str(item.get("source_label") or "")
                )
            )
        }
        scoped_canonical = canonical_schema_field(source_field, **resolved_scope)
        canonical = (
            next(iter(canonical_fields))
            if len(canonical_fields) == 1
            else scoped_canonical or canonical_schema_field(source_field)
        )
        if canonical and canonical != source_field:
            semantic_candidates[source_field] = canonical
            semantic_candidate_sources[source_field] = (
                "literal_label"
                if canonical_fields
                else "scoped_value_mapping"
                if scoped_canonical
                else "qualified_field_name"
            )

    target_counts: dict[str, int] = {}
    for target in semantic_candidates.values():
        target_counts[target] = target_counts.get(target, 0) + 1
    candidate_collisions = {
        source: target
        for source, target in semantic_candidates.items()
        if target_counts[target] > 1
        or target in resolved_declared_set - {source}
    }
    projection_candidates = {
        source: target
        for source, target in semantic_candidates.items()
        if source not in candidate_collisions
    }
    if not source_renames and not projection_candidates and not candidate_collisions:
        return proposal

    def resolve(field: str) -> str:
        return resolve_source(field)

    def unique_fields(fields: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(resolve(field) for field in fields))

    def remap_references(value: Any) -> Any:
        if isinstance(value, list):
            return [remap_references(item) for item in value]
        if not isinstance(value, dict):
            return value
        return {
            key: (
                resolve(str(item))
                if key in {"field", "other_field", "field_name"}
                and isinstance(item, str)
                else remap_references(item)
            )
            for key, item in value.items()
        }

    required = unique_fields(proposal.required_fields)
    required_set = set(required)
    optional = tuple(
        field
        for field in unique_fields(proposal.optional_fields)
        if field not in required_set
    )
    validators = {
        resolve(str(field)): dict(rules or {})
        for field, rules in dict(proposal.validators or {}).items()
    }
    state_semantics = {
        resolve(str(field)): remap_references(dict(metadata or {}))
        for field, metadata in dict(proposal.state_semantics or {}).items()
    }
    evidence["field_bindings"] = {
        resolve(str(field)): bindings
        for field, bindings in field_bindings.items()
    }
    evidence = remap_references(evidence)
    evidence["semantic_field_normalization"] = {
        "strategy": "literal_source_label_identifier",
        "mapping": source_renames,
        "skipped_collisions": {},
        "resolution_sources": {
            source: source_rename_sources[source]
            for source in source_renames
        },
    }
    evidence["projection_field_candidates"] = {
        "strategy": "semantic_catalog_non_authoritative",
        "mapping": projection_candidates,
        "skipped_collisions": candidate_collisions,
        "resolution_sources": {
            source: semantic_candidate_sources[source]
            for source in (*projection_candidates, *candidate_collisions)
        },
    }
    return replace(
        proposal,
        primary_keys=unique_fields(proposal.primary_keys),
        required_fields=required,
        optional_fields=optional,
        validators=validators,
        ignore_fields=unique_fields(proposal.ignore_fields),
        state_semantics=state_semantics or None,
        evidence=evidence,
    )


def contract_has_singleton_scope(
    proposal: ContractProposal,
    *,
    structure_profile: dict[str, Any] | None,
    samples_text: str,
) -> bool:
    """Prove that required fields describe a snapshot-level singleton region.

    Some commands contain a stable device-level preamble followed by repeated
    component sections. A global structure label such as ``repeated_block`` must
    not force those preamble fields into a row-level entity contract. The proof
    is intentionally conservative: every required field must have direct evidence
    in every sample, before that sample's first repeated entity boundary.
    """

    if proposal.primary_keys or not proposal.required_fields:
        return False
    samples = {
        match.group("sample_id"): match.group("body").splitlines()
        for match in _SAMPLE_BLOCK_RE.finditer(samples_text or "")
    }
    if not samples:
        return False
    plans = list((structure_profile or {}).get("samples") or [])
    if not plans:
        return False

    plans_by_sample: dict[str, dict[str, Any]] = {}
    unresolved_plans: list[dict[str, Any]] = []
    for plan in plans:
        sample_ids = {
            str(item.get("sample_id") or "").strip()
            for item in [*(plan.get("evidence") or []), *(plan.get("table_regions") or [])]
            if isinstance(item, dict) and str(item.get("sample_id") or "").strip()
        }
        if sample_ids:
            for sample_id in sample_ids:
                plans_by_sample[sample_id] = plan
        else:
            unresolved_plans.append(plan)
    missing_sample_ids = [sample_id for sample_id in samples if sample_id not in plans_by_sample]
    if len(missing_sample_ids) == len(unresolved_plans):
        plans_by_sample.update(zip(missing_sample_ids, unresolved_plans, strict=True))
    if set(samples) - set(plans_by_sample):
        return False

    evidence = dict(proposal.evidence or {})
    field_bindings = dict(evidence.get("field_bindings") or {})
    for sample_id, lines in samples.items():
        plan = plans_by_sample[sample_id]
        estimated_entities = int(plan.get("estimated_entities") or 0)
        repeated_boundaries = [
            int(item.get("line_number") or 0)
            for item in plan.get("evidence") or []
            if isinstance(item, dict)
            and item.get("role") == "entity_header"
            and int(item.get("line_number") or 0) > 0
        ]
        repeated_boundaries.extend(
            int(item.get("first_row_line_number") or item.get("header_line_number") or 0)
            for item in plan.get("table_regions") or []
            if isinstance(item, dict)
            and int(item.get("first_row_line_number") or item.get("header_line_number") or 0) > 0
        )
        first_repeated_line = min(repeated_boundaries, default=0)
        if estimated_entities > 1 and not first_repeated_line:
            return False

        for field_name in proposal.required_fields:
            raw_bindings = field_bindings.get(field_name) or []
            bindings = raw_bindings if isinstance(raw_bindings, list) else [raw_bindings]
            sample_bindings = [
                item
                for item in bindings
                if isinstance(item, dict)
                and str(item.get("sample_id") or "").strip() == sample_id
            ]
            if not sample_bindings:
                return False
            for binding in sample_bindings:
                if str(binding.get("capture_kind") or "").strip() == "derived":
                    return False
                try:
                    line_number = int(binding.get("line") or binding.get("line_number"))
                except (TypeError, ValueError):
                    return False
                if line_number < 1 or line_number > len(lines):
                    return False
                if first_repeated_line and line_number >= first_repeated_line:
                    return False
    return True


def _normalize_capture_requirements(
    proposal: ContractProposal,
    *,
    structure_profile: dict[str, Any] | None = None,
    samples_text: str = "",
) -> ContractProposal:
    """Keep required fields only where every entity can expose them directly."""

    evidence = json.loads(json.dumps(dict(proposal.evidence or {})))
    bindings = dict(evidence.get("field_bindings") or {})
    required = list(proposal.required_fields)
    optional = list(proposal.optional_fields)
    primary = set(proposal.primary_keys)
    plans = list((structure_profile or {}).get("samples") or [])
    singleton_scope = contract_has_singleton_scope(
        proposal,
        structure_profile=structure_profile,
        samples_text=samples_text,
    )
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
            bool(multi_entity_layouts)
            and not table_column_proven
            and not singleton_scope
        )
        if field_name in primary or not kinds or not unsupported_requirement:
            continue
        required.remove(field_name)
        if field_name not in optional:
            optional.append(field_name)
        reclassified.append(field_name)
    if singleton_scope:
        evidence["entity_scope_governance"] = {
            "strategy": "required_bindings_before_repeated_entities",
            "scope": "singleton",
        }
    if not reclassified:
        return replace(proposal, evidence=evidence) if singleton_scope else proposal
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
    check_item: str | None = None,
    vendor: str | None = None,
    model: str | None = None,
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
        _prune_unverifiable_field_bindings(
            _normalize_semantic_field_aliases(
                _normalize_table_column_bindings(
                    _normalize_binding_lines(
                        _normalize_capture_kind_aliases(proposal),
                        samples_text=samples_text,
                    ),
                    samples_text=samples_text,
                ),
                check_item=check_item,
                vendor=vendor,
                model=model,
                command=command,
            ),
            samples_text=samples_text,
        ),
        structure_profile=structure_profile,
        samples_text=samples_text,
    )
    proposal = _normalize_redacted_value_validators(
        _normalize_evidence_bound_validators(proposal)
    )
    if catalog_match:
        conflicts = _catalog_compatibility_errors(proposal, catalog_match)
        if conflicts:
            return _proposal_from_catalog(
                catalog_match,
                llm_fallback_reason="; ".join(conflicts),
            )
    return govern_contract_candidate(proposal)


def revise_contract(
    *,
    previous: ContractProposal,
    command: str,
    samples_text: str,
    structure_profile: dict[str, Any],
    diagnostic: dict[str, Any],
    llm_call: Callable[[str], str],
    check_item: str | None = None,
    vendor: str | None = None,
    model: str | None = None,
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
        _prune_unverifiable_field_bindings(
            _normalize_semantic_field_aliases(
                _normalize_table_column_bindings(
                    _normalize_binding_lines(
                        _normalize_capture_kind_aliases(proposal),
                        samples_text=samples_text,
                    ),
                    samples_text=samples_text,
                ),
                check_item=check_item,
                vendor=vendor,
                model=model,
                command=command,
            ),
            samples_text=samples_text,
        ),
        structure_profile=structure_profile,
        samples_text=samples_text,
    )
    return govern_contract_candidate(
        _normalize_redacted_value_validators(
            _normalize_evidence_bound_validators(proposal)
        )
    )

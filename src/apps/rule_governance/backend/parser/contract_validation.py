"""Deterministic validation for parser ObservationContract proposals."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.apps.rule_governance.backend.parser.contract_inference import (
    ContractProposal,
    contract_has_singleton_scope,
    infer_contract,
    infer_field_semantics,
)
from src.apps.rule_governance.backend.parser.evidence_utils import literal_occurs
from src.apps.rule_governance.backend.parser.generation_diagnostics import ParserDiagnostic
from src.apps.rule_governance.backend.parser.key_contract import validate_entity_keys
from src.apps.rule_governance.backend.parser.sanitization import runtime_pattern_for_redacted_value
from src.apps.rule_governance.backend.parser.semantic_catalog import (
    canonical_field_for_label,
)
from src.apps.rule_governance.backend.parser.semantic_catalog import (
    field_semantics as catalog_field_semantics,
)
from src.apps.rule_governance.backend.parser.value_types import (
    is_unavailable_value,
    is_valid_ip_value,
)


@dataclass(frozen=True)
class ContractValidationInput:
    command: str
    records: list[dict[str, Any]]
    samples_text: str = ""
    proposal: ContractProposal | None = None


def _field_value(record: dict[str, Any], field_name: str) -> Any:
    if field_name in record:
        return record[field_name]
    lowered = {str(key).lower(): key for key in record}
    actual = lowered.get(field_name.lower())
    return record.get(actual) if actual is not None else None


def _has_field(record: dict[str, Any], field_name: str) -> bool:
    if field_name in record:
        return True
    lowered = {str(key).lower() for key in record}
    return field_name.lower() in lowered


def _is_missing(value: Any) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    if isinstance(value, (list, tuple)):
        return not value or all(_is_missing(item) for item in value)
    return False


def _check_required_fields(
    records: list[dict[str, Any]],
    required_fields: tuple[str, ...],
) -> tuple[int, list[str]]:
    """Validate the record schema, not per-entity business applicability.

    TextFSM emits a stable column for every declared Value, while a particular
    entity may legitimately carry an empty value (no IP address, no description,
    absent transceiver measurement). Primary-key validation and downstream
    Observation/Coverage policy own non-empty identity and business completeness.
    """

    if not required_fields:
        return 100, []
    if not records:
        return 0, list(required_fields)
    missing = [
        field
        for field in required_fields
        if any(not _has_field(record, field) for record in records)
    ]
    coverage = round(100 * (len(required_fields) - len(missing)) / len(required_fields))
    return int(coverage), missing


def _parse_percent(value: Any) -> float | None:
    try:
        return float(str(value).strip().rstrip("%"))
    except ValueError:
        return None


def _empty_output_matches(samples_text: str, empty_patterns: tuple[str, ...]) -> bool:
    if not empty_patterns:
        return True
    text = samples_text or ""
    return any(re.search(pattern, text) for pattern in empty_patterns if pattern)


def _validate_field_values(
    records: list[dict[str, Any]],
    validators: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for field_name, rules in validators.items():
        rule_type = rules.get("type")
        for index, record in enumerate(records):
            raw_value = _field_value(record, field_name)
            raw_values = list(raw_value) if isinstance(raw_value, (list, tuple)) else [raw_value]
            for value_index, raw in enumerate(raw_values):
                if _is_missing(raw):
                    continue
                if is_unavailable_value(raw, rules.get("unavailable_tokens")):
                    continue
                error_code: str | None = None
                if rule_type == "percent":
                    value = _parse_percent(raw)
                    if value is None or not 0 <= value <= 100:
                        error_code = "value_not_percent"
                elif rule_type == "enum":
                    allowed = {str(value).strip().lower() for value in rules.get("values") or []}
                    if allowed and str(raw).strip().lower() not in allowed:
                        error_code = "value_not_allowed"
                elif rule_type in {"ip", "ip_or_unassigned"}:
                    if not is_valid_ip_value(
                        raw,
                        allow_unassigned=rule_type == "ip_or_unassigned",
                    ):
                        error_code = "value_not_ip"
                elif rule_type == "interface_name":
                    text = str(raw).strip().lower()
                    if text in {
                        "interface",
                        "port",
                        "name",
                        "interface name",
                        "port name",
                    }:
                        error_code = "value_looks_like_header"
                elif rule_type == "number":
                    try:
                        value = float(str(raw).strip())
                    except ValueError:
                        error_code = "value_not_numeric"
                    else:
                        minimum = rules.get("minimum")
                        maximum = rules.get("maximum")
                        if minimum is not None and value < float(minimum):
                            error_code = "value_below_minimum"
                        elif maximum is not None and value > float(maximum):
                            error_code = "value_above_maximum"
                elif rule_type == "regex":
                    pattern = str(rules.get("pattern") or "")
                    if pattern and re.fullmatch(pattern, str(raw).strip()) is None:
                        error_code = "value_not_matching_pattern"
                if error_code:
                    error = {
                        "record_index": index,
                        "field": field_name,
                        "validator": rule_type,
                        "error": error_code,
                    }
                    if isinstance(raw_value, (list, tuple)):
                        error["value_index"] = value_index
                    errors.append(error)
    return errors


def infer_and_validate_contract(
    *,
    command: str,
    records: list[dict[str, Any]],
    samples_text: str = "",
    proposal: ContractProposal | None = None,
) -> dict[str, Any]:
    contract = proposal or infer_contract(
        command=command,
        samples_text=samples_text,
        use_manual_profile=True,
    )
    state_semantics = contract.state_semantics or infer_field_semantics(contract)
    required_coverage, missing_fields = _check_required_fields(
        records,
        contract.required_fields,
    )
    key_result = validate_entity_keys(records, contract.primary_keys)
    value_errors = _validate_field_values(records, contract.validators or {})
    valid_empty = (
        not records
        and contract.allow_empty
        and _empty_output_matches(samples_text, contract.empty_patterns)
    )
    success = (
        (bool(records) and required_coverage == 100 and key_result.valid) or valid_empty
    ) and not value_errors
    return {
        "success": success,
        "proposal": {**contract.to_dict(), "state_semantics": state_semantics},
        "validation": {
            "record_count": len(records),
            "required_field_coverage": required_coverage,
            "missing_fields": missing_fields,
            "primary_key_status": "valid" if key_result.valid else "invalid",
            "primary_key_fields": list(contract.primary_keys),
            "primary_key_count": len(key_result.keys),
            "duplicate_key_count": len(key_result.duplicate_keys),
            "missing_key_fields": list(key_result.missing_fields),
            "key_error_count": len(key_result.errors),
            "value_error_count": len(value_errors),
            "value_errors": value_errors[:20],
            "allow_empty": contract.allow_empty,
            "empty_accepted": valid_empty,
            "semantic_field_count": len(state_semantics),
        },
    }


def proposal_from_dict(payload: dict[str, Any]) -> ContractProposal:
    return ContractProposal(
        category=str(payload.get("category") or "record"),
        entity_type=str(payload.get("entity_type") or payload.get("category") or "record"),
        primary_keys=tuple(str(item) for item in payload.get("primary_keys") or ()),
        required_fields=tuple(str(item) for item in payload.get("required_fields") or ()),
        canonical_entity_type=(
            str(payload.get("canonical_entity_type")).strip()
            if payload.get("canonical_entity_type")
            else None
        ),
        entity_identity_fields=tuple(
            str(item) for item in payload.get("entity_identity_fields") or ()
        ),
        entity_dimensions=dict(payload.get("entity_dimensions") or {}),
        optional_fields=tuple(str(item) for item in payload.get("optional_fields") or ()),
        validators=dict(payload.get("validators") or {}),
        allow_empty=bool(payload.get("allow_empty", False)),
        empty_patterns=tuple(str(item) for item in payload.get("empty_patterns") or ()),
        ignore_fields=tuple(str(item) for item in payload.get("ignore_fields") or ()),
        state_semantics=dict(payload.get("state_semantics") or {}),
        evidence=dict(payload.get("evidence") or {}) or None,
        source=str(payload.get("source") or "provided"),
        confidence=float(payload.get("confidence") or 0.8),
    )


_SAMPLE_BLOCK_RE = re.compile(
    r'<CLI_SAMPLE\s+id="(?P<sample_id>[^"]+)"[^>]*>\s*\n?'
    r"(?P<body>[\s\S]*?)\n?\s*</CLI_SAMPLE>"
)


def _sample_line_map(samples_text: str) -> dict[str, list[str]]:
    return {
        match.group("sample_id"): match.group("body").splitlines()
        for match in _SAMPLE_BLOCK_RE.finditer(samples_text or "")
    }


def _line_numbers(value: Any) -> list[int]:
    values = value if isinstance(value, list) else [value]
    output: list[int] = []
    for item in values:
        try:
            output.append(int(item))
        except (TypeError, ValueError):
            continue
    return output


def _evidence_references(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return
        sample_id = str(value.get("sample_id") or "").strip()
        lines = _line_numbers(
            value.get("line") or value.get("line_number") or value.get("start_line")
        )
        if sample_id and lines:
            for line_number in lines:
                references.append(
                    {
                        "sample_id": sample_id,
                        "line_number": line_number,
                        "value": value.get("value") or value.get("key_text"),
                    }
                )
        for nested in value.values():
            walk(nested)

    walk(evidence)
    sample_ids = [str(item) for item in evidence.get("sample_ids") or []]
    line_numbers = evidence.get("line_numbers")
    if sample_ids and isinstance(line_numbers, dict):
        for sample_id in sample_ids:
            for values in line_numbers.values():
                for line_number in _line_numbers(values):
                    references.append(
                        {"sample_id": sample_id, "line_number": line_number, "value": None}
                    )
    return references


def _validate_line_evidence(
    evidence: dict[str, Any],
    *,
    samples_text: str,
) -> list[str]:
    samples = _sample_line_map(samples_text)
    references = _evidence_references(evidence)
    if not samples:
        return ["sanitized sample boundaries are unavailable for evidence validation"]
    if not references:
        return ["contract evidence has no sample_id and line-number references"]
    errors: list[str] = []
    for reference in references:
        sample_id = reference["sample_id"]
        line_number = reference["line_number"]
        lines = samples.get(sample_id)
        if lines is None:
            errors.append(f"evidence references unknown sample {sample_id}")
            continue
        if line_number < 1 or line_number > len(lines):
            errors.append(f"evidence line {line_number} is outside sample {sample_id}")
            continue
        expected_value = str(reference.get("value") or "").strip()
        if expected_value and not literal_occurs(lines[line_number - 1], expected_value):
            errors.append(
                f"evidence value {expected_value!r} is absent from {sample_id}:{line_number}"
            )
    return errors


_CAPTURE_KINDS = {"column", "key_value", "record_anchor", "derived"}
_MISSING_VALUE_MARKERS = {
    "n/a",
    "na",
    "--",
    "none",
    "null",
    "unknown",
    "unavailable",
    "not available",
}


def _field_bindings(evidence: dict[str, Any], field_name: str) -> list[dict[str, Any]]:
    raw = dict(evidence.get("field_bindings") or {}).get(field_name) or []
    values = raw if isinstance(raw, list) else [raw]
    return [dict(item) for item in values if isinstance(item, dict)]


def _composite_anchor_conflicts(
    proposal: ContractProposal,
    *,
    samples_text: str,
) -> list[str]:
    """Reject conjunctive keys built from mutually exclusive sibling blocks."""

    if len(proposal.primary_keys) < 2:
        return []
    samples = _sample_line_map(samples_text)
    evidence = dict(proposal.evidence or {})
    anchors_by_key: dict[str, dict[str, list[tuple[int, int]]]] = {}
    for field_name in proposal.primary_keys:
        per_sample: dict[str, list[tuple[int, int]]] = {}
        bindings = _field_bindings(evidence, field_name)
        if not bindings or any(
            str(binding.get("capture_kind") or "") != "record_anchor" for binding in bindings
        ):
            return []
        for binding in bindings:
            sample_id = str(binding.get("sample_id") or "").strip()
            lines = samples.get(sample_id)
            if lines is None:
                continue
            for line_number in _line_numbers(
                binding.get("line") or binding.get("line_number") or binding.get("start_line")
            ):
                if 1 <= line_number <= len(lines):
                    line = lines[line_number - 1]
                    indent = len(line) - len(line.lstrip())
                    per_sample.setdefault(sample_id, []).append((line_number, indent))
        anchors_by_key[field_name] = per_sample

    conflicts: list[str] = []
    sample_ids = set.intersection(*(set(anchors_by_key[key]) for key in proposal.primary_keys))
    for sample_id in sorted(sample_ids):
        locations = {key: anchors_by_key[key][sample_id] for key in proposal.primary_keys}
        shared_lines = set.intersection(
            *(
                {line_number for line_number, _indent in locations[key]}
                for key in proposal.primary_keys
            )
        )
        if shared_lines:
            continue
        common_indent_levels = set.intersection(
            *({indent for _line_number, indent in locations[key]} for key in proposal.primary_keys)
        )
        if not common_indent_levels:
            continue
        conflicts.append(
            "composite primary keys "
            + ", ".join(proposal.primary_keys)
            + " are bound to disjoint sibling record anchors in "
            + sample_id
            + "; every emitted record must contain every primary-key field. "
            "Use one neutral entity key across sibling variants, or model the "
            "variants as separate contracts."
        )
    return conflicts


def _profile_sample_ids(structure_profile: dict[str, Any]) -> set[str]:
    sample_ids: set[str] = set()
    for plan in structure_profile.get("samples") or []:
        for item in plan.get("evidence") or []:
            sample_id = str((item or {}).get("sample_id") or "").strip()
            if sample_id:
                sample_ids.add(sample_id)
        for item in plan.get("table_regions") or []:
            sample_id = str((item or {}).get("sample_id") or "").strip()
            if sample_id:
                sample_ids.add(sample_id)
    return sample_ids


def _evidence_entity_cardinality(
    proposal: ContractProposal,
) -> tuple[dict[str, int], dict[str, int]]:
    """Estimate entity count and key capacity from direct line evidence."""

    evidence = dict(proposal.evidence or {})
    counts_by_field: dict[str, dict[str, set[tuple[int, str]]]] = {}
    for field_name in proposal.required_fields:
        for binding in _field_bindings(evidence, field_name):
            if str(binding.get("capture_kind") or "").strip() == "derived":
                continue
            sample_id = str(binding.get("sample_id") or "").strip()
            try:
                line_number = int(binding.get("line") or binding.get("line_number"))
            except (TypeError, ValueError):
                continue
            value = str(binding.get("source_value") or "").strip()
            if sample_id and line_number > 0:
                counts_by_field.setdefault(field_name, {}).setdefault(sample_id, set()).add(
                    (line_number, value)
                )

    sample_ids = {
        sample_id
        for by_sample in counts_by_field.values()
        for sample_id in by_sample
    }
    entity_counts = {
        sample_id: max(
            (
                len(counts_by_field.get(field_name, {}).get(sample_id, set()))
                for field_name in proposal.required_fields
            ),
            default=0,
        )
        for sample_id in sample_ids
    }
    key_capacity: dict[str, int] = {}
    for sample_id in sample_ids:
        capacity = 1
        for key in proposal.primary_keys:
            values = {
                value
                for _line, value in counts_by_field.get(key, {}).get(sample_id, set())
                if value
            }
            capacity *= len(values)
        key_capacity[sample_id] = capacity if proposal.primary_keys else 0
    return entity_counts, key_capacity


def _same_governed_semantics(first: str, second: str) -> bool:
    left = catalog_field_semantics(first)
    right = catalog_field_semantics(second)
    return bool(left and right and left == right)


def _validate_field_bindings(
    proposal: ContractProposal,
    *,
    structure_profile: dict[str, Any],
    samples_text: str,
) -> list[str]:
    samples = _sample_line_map(samples_text)
    expected_sample_ids = _profile_sample_ids(structure_profile)
    evidence = dict(proposal.evidence or {})
    declared = (*proposal.required_fields, *proposal.optional_fields)
    errors: list[str] = []
    if not isinstance(evidence.get("field_bindings"), dict):
        return ["contract evidence has no field_bindings object"]

    for field_name in declared:
        bindings = _field_bindings(evidence, field_name)
        if not bindings:
            errors.append(f"field {field_name} has no evidence binding")
            continue
        covered_samples: set[str] = set()
        for binding in bindings:
            sample_id = str(binding.get("sample_id") or "").strip()
            line_numbers = _line_numbers(
                binding.get("line") or binding.get("line_number") or binding.get("start_line")
            )
            capture_kind = str(binding.get("capture_kind") or "").strip()
            source_label = str(binding.get("source_label") or "").strip()
            source_value = str(binding.get("source_value") or "").strip()
            if capture_kind not in _CAPTURE_KINDS:
                errors.append(f"field {field_name} has unsupported capture_kind {capture_kind!r}")
            if not sample_id or not line_numbers:
                errors.append(f"field {field_name} binding lacks sample_id or line")
                continue
            lines = samples.get(sample_id)
            if lines is None:
                errors.append(f"field {field_name} references unknown sample {sample_id}")
                continue
            covered_samples.add(sample_id)
            source_label_is_literal = False
            for line_number in line_numbers:
                if line_number < 1 or line_number > len(lines):
                    errors.append(
                        f"field {field_name} line {line_number} is outside sample {sample_id}"
                    )
                    continue
                line = lines[line_number - 1]
                if not source_label and not source_value:
                    errors.append(f"field {field_name} binding has no source_label or source_value")
                label_present = bool(source_label and literal_occurs(line, source_label))
                value_present = bool(source_value and literal_occurs(line, source_value))
                source_label_is_literal = source_label_is_literal or label_present
                if not label_present and not value_present:
                    errors.append(
                        f"field {field_name} evidence is absent from {sample_id}:{line_number}"
                    )
            if (
                field_name in {*proposal.required_fields, *proposal.primary_keys}
                and capture_kind == "derived"
            ):
                errors.append(f"required field {field_name} cannot use a derived binding")
            validator = dict((proposal.validators or {}).get(field_name) or {})
            if (
                validator
                and capture_kind != "derived"
                and source_value
                and source_value.casefold() not in _MISSING_VALUE_MARKERS
                and runtime_pattern_for_redacted_value(source_value) is None
            ):
                value_errors = _validate_field_values(
                    [{field_name: source_value}],
                    {field_name: validator},
                )
                if value_errors:
                    errors.append(
                        f"field {field_name} validator rejects evidenced value {source_value!r}"
                    )
            governed = (
                canonical_field_for_label(source_label)
                if source_label and source_label_is_literal
                else None
            )
            if (
                governed
                and governed != field_name
                and capture_kind != "record_anchor"
                and not _same_governed_semantics(governed, field_name)
            ):
                errors.append(
                    f"field {field_name} conflicts with governed label "
                    f"{source_label!r} ({governed})"
                )
        locations: dict[tuple[str, int], set[str]] = {}
        for binding in bindings:
            sample_id = str(binding.get("sample_id") or "").strip()
            source_value = str(binding.get("source_value") or "").strip()
            for line_number in _line_numbers(
                binding.get("line") or binding.get("line_number") or binding.get("start_line")
            ):
                if sample_id and source_value:
                    locations.setdefault((sample_id, line_number), set()).add(source_value)
        conflicts = [
            (sample_id, line_number, sorted(values))
            for (sample_id, line_number), values in locations.items()
            if len(values) > 1
        ]
        if conflicts:
            sample_id, line_number, values = conflicts[0]
            errors.append(
                f"field {field_name} has conflicting evidence values at "
                f"{sample_id}:{line_number}: {values}"
            )
        if field_name in {*proposal.required_fields, *proposal.primary_keys}:
            missing_samples = sorted(expected_sample_ids - covered_samples)
            if missing_samples:
                errors.append(
                    f"required field {field_name} lacks evidence in samples: "
                    + ", ".join(missing_samples)
                )
    return errors


def validate_contract_candidate(
    proposal: ContractProposal,
    *,
    structure_profile: dict[str, Any],
    samples_text: str = "",
) -> tuple[bool, list[ParserDiagnostic]]:
    """Validate entity identity and evidence before template generation starts."""
    diagnostics: list[ParserDiagnostic] = []
    declared = {*proposal.required_fields, *proposal.optional_fields}
    missing_keys = [key for key in proposal.primary_keys if key not in declared]
    if not proposal.required_fields:
        diagnostics.append(
            ParserDiagnostic(
                code="contract_evidence_conflict",
                message="contract has no required fields",
            )
        )
    if missing_keys:
        diagnostics.append(
            ParserDiagnostic(
                code="contract_evidence_conflict",
                message=f"primary keys are not declared fields: {', '.join(missing_keys)}",
                evidence={"missing_keys": missing_keys},
            )
        )
    plans = list(structure_profile.get("samples") or [])
    estimated = [
        int(item["estimated_entities"])
        for item in plans
        if item.get("estimated_entities") is not None
    ]
    singleton_scope = contract_has_singleton_scope(
        proposal,
        structure_profile=structure_profile,
        samples_text=samples_text,
    )
    evidence_entity_counts, evidence_key_capacity = _evidence_entity_cardinality(proposal)
    maximum_entity_count = max([*estimated, *evidence_entity_counts.values()], default=0)
    if maximum_entity_count > 1 and not proposal.primary_keys and not singleton_scope:
        diagnostics.append(
            ParserDiagnostic(
                code="contract_evidence_conflict",
                message="multi-entity samples require explicit primary keys",
                expected="one or more primary key fields",
                actual=[],
            )
        )
    insufficient_key_samples = {
        sample_id: {
            "evidenced_entities": entity_count,
            "key_capacity": evidence_key_capacity.get(sample_id, 0),
        }
        for sample_id, entity_count in evidence_entity_counts.items()
        if proposal.primary_keys
        and entity_count > evidence_key_capacity.get(sample_id, 0)
    }
    if insufficient_key_samples:
        diagnostics.append(
            ParserDiagnostic(
                code="contract_evidence_conflict",
                message=(
                    "primary keys do not distinguish all evidenced entities; "
                    "include the repeated entity identity in the key"
                ),
                expected={
                    "minimum_key_capacity": max(
                        item["evidenced_entities"]
                        for item in insufficient_key_samples.values()
                    )
                },
                actual={
                    "primary_keys": list(proposal.primary_keys),
                    "samples": insufficient_key_samples,
                },
            )
        )
    if structure_profile.get("dominant_layout") in {None, "empty"}:
        diagnostics.append(
            ParserDiagnostic(
                code="sample_insufficient",
                message="samples do not contain enough structure to infer a contract",
            )
        )
    if proposal.source.startswith("llm") and not proposal.evidence:
        diagnostics.append(
            ParserDiagnostic(
                code="contract_evidence_conflict",
                message="LLM contract proposal has no line-level evidence",
            )
        )
    elif proposal.source.startswith("llm"):
        evidence_errors = _validate_line_evidence(
            dict(proposal.evidence or {}),
            samples_text=samples_text,
        )
        evidence_errors.extend(
            _validate_field_bindings(
                proposal,
                structure_profile=structure_profile,
                samples_text=samples_text,
            )
        )
        evidence_errors.extend(
            _composite_anchor_conflicts(
                proposal,
                samples_text=samples_text,
            )
        )
        if evidence_errors:
            diagnostics.append(
                ParserDiagnostic(
                    code="contract_evidence_conflict",
                    message=evidence_errors[0],
                    evidence={"errors": evidence_errors[:10]},
                )
            )
    return not diagnostics, diagnostics

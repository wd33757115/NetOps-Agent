"""Deterministic validation for parser ObservationContract proposals."""

from __future__ import annotations

import ipaddress
from dataclasses import asdict, dataclass
from typing import Any

from src.core.parser.contract_inference import (
    ContractProposal,
    infer_contract,
    infer_field_semantics,
)
from src.core.parser.key_contract import validate_entity_keys


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


def _check_required_fields(
    records: list[dict[str, Any]],
    required_fields: tuple[str, ...],
) -> tuple[int, list[str]]:
    if not required_fields:
        return 100, []
    if not records:
        return 0, list(required_fields)
    missing = [
        field
        for field in required_fields
        if any(str(_field_value(record, field) or "").strip() == "" for record in records)
    ]
    coverage = round(100 * (len(required_fields) - len(missing)) / len(required_fields))
    return int(coverage), missing


def _parse_percent(value: Any) -> float | None:
    try:
        return float(str(value).strip().rstrip("%"))
    except ValueError:
        return None


def _validate_field_values(
    records: list[dict[str, Any]],
    validators: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for field_name, rules in validators.items():
        rule_type = rules.get("type")
        for index, record in enumerate(records):
            raw = _field_value(record, field_name)
            if raw in (None, ""):
                continue
            if rule_type == "percent":
                value = _parse_percent(raw)
                if value is None or not 0 <= value <= 100:
                    errors.append(
                        {
                            "record_index": index,
                            "field": field_name,
                            "validator": "percent",
                            "error": "value_not_percent",
                        }
                    )
            elif rule_type == "enum":
                allowed = {
                    str(value).strip().lower()
                    for value in rules.get("values") or []
                }
                if allowed and str(raw).strip().lower() not in allowed:
                    errors.append(
                        {
                            "record_index": index,
                            "field": field_name,
                            "validator": "enum",
                            "error": "value_not_allowed",
                        }
                    )
            elif rule_type in {"ip", "ip_or_unassigned"}:
                text = str(raw).strip()
                if rule_type == "ip_or_unassigned" and text.lower() in {
                    "unassigned",
                    "--",
                    "none",
                }:
                    continue
                try:
                    ipaddress.ip_interface(text)
                except ValueError:
                    errors.append(
                        {
                            "record_index": index,
                            "field": field_name,
                            "validator": rule_type,
                            "error": "value_not_ip",
                        }
                    )
            elif rule_type == "interface_name":
                text = str(raw).strip().lower()
                if text in {"interface", "port", "name", "interface name", "port name"}:
                    errors.append(
                        {
                            "record_index": index,
                            "field": field_name,
                            "validator": "interface_name",
                            "error": "value_looks_like_header",
                        }
                    )
    return errors


def infer_and_validate_contract(
    *,
    command: str,
    records: list[dict[str, Any]],
    samples_text: str = "",
    proposal: ContractProposal | None = None,
) -> dict[str, Any]:
    contract = proposal or infer_contract(command=command, samples_text=samples_text)
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
        and (
            not contract.empty_patterns
            or any(pattern for pattern in contract.empty_patterns)
        )
    )
    success = (
        (bool(records) or valid_empty)
        and required_coverage == 100
        and key_result.valid
        and not value_errors
    )
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
        optional_fields=tuple(str(item) for item in payload.get("optional_fields") or ()),
        validators=dict(payload.get("validators") or {}),
        allow_empty=bool(payload.get("allow_empty", False)),
        empty_patterns=tuple(str(item) for item in payload.get("empty_patterns") or ()),
        ignore_fields=tuple(str(item) for item in payload.get("ignore_fields") or ()),
        state_semantics=dict(payload.get("state_semantics") or {}),
        source=str(payload.get("source") or "provided"),
        confidence=float(payload.get("confidence") or 0.8),
    )


def proposal_to_public_dict(proposal: ContractProposal) -> dict[str, Any]:
    return asdict(proposal)

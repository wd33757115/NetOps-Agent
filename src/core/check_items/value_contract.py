"""CheckItem-owned Value names and exhaustive, opt-in change events."""

import re
from copy import deepcopy


def normalize_value_contract(contract):
    result = deepcopy(contract)
    if "change_events" not in result:
        return result  # Other active checks retain their explicitly configured assessment rules.
    if not isinstance(result["change_events"], bool):
        raise ValueError("change_events must be a boolean")
    result["value_contract"] = "same-name/v1"
    identities = set(result.get("identity_dimensions") or []) - {"device_id"}
    seen = set(identities)
    for row in result.get("observations", []):
        field = row.get("canonical_field", "")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", field) or field in seen:
            raise ValueError(f"invalid or duplicate standard Value: {field}")
        seen.add(field)
        row["event_on_change"] = result["change_events"]
        roles = set(row.get("assessment_roles") or []) - {"change"}
        if result["change_events"]:
            roles.add("change")
            event_type = str(row["observation_type"]) + ".changed"
            row["event_constraints"] = {
                "required_event_types": [event_type],
                "role_event_types": {"change": event_type},
                "required_event_classes": {event_type: "change"},
                "required_severities": {event_type: "info"},
                "required_recovery_pairs": [],
            }
        else:
            row.pop("event_constraints", None)
        row["assessment_roles"] = sorted(roles)
    return result


def same_name_fields(contract, source_fields):
    """No aliases or semantic conversion; missing optional fields remain absent."""
    sources = set(source_fields)
    identities = set(contract.get("identity_dimensions") or []) - {"device_id"}
    rows = contract.get("observations") or []
    allowed = identities | {row["canonical_field"] for row in rows}
    required = identities | {row["canonical_field"] for row in rows if row.get("required", True)}
    errors = []
    if sources - allowed:
        errors.append("TextFSM contains undeclared Values: " + ", ".join(sorted(sources - allowed)))
    if required - sources:
        errors.append("TextFSM missing required Values: " + ", ".join(sorted(required - sources)))
    if errors:
        raise ValueError("; ".join(errors))
    return {field: {"source": field} for field in sorted(sources)}


def validate_same_name_record_sets(contracts, record_sets):
    """Every target must fit one record set; shared commands may serve several targets."""
    allowed = set()
    required_sets = []
    for contract in contracts:
        identities = set(contract.get("identity_dimensions") or []) - {"device_id"}
        rows = contract.get("observations") or []
        allowed.update(identities | {row["canonical_field"] for row in rows})
        required_sets.append(
            identities | {row["canonical_field"] for row in rows if row.get("required", True)}
        )
    sources = [set(record_set["fields"]) for record_set in record_sets]
    undeclared = set().union(*sources) - allowed
    if undeclared:
        raise ValueError("TextFSM contains undeclared Values: " + ", ".join(sorted(undeclared)))
    for required in required_sets:
        if not any(required <= source for source in sources):
            raise ValueError(
                "TextFSM missing required Values in a single record set: "
                + ", ".join(sorted(required))
            )

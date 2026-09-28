"""CheckItem-owned Value names and exhaustive, opt-in change events."""

import re
from copy import deepcopy


def _without_change_events(constraints):
    """Remove only ordinary change intents, retaining health/recovery constraints."""
    result = deepcopy(constraints or {})
    removed = {key for key, value in result.get('required_event_classes', {}).items() if value == 'change'}
    change_type = result.get('role_event_types', {}).pop('change', None)
    if change_type:
        removed.add(change_type)
    for key in ('required_event_types',):
        if key in result:
            result[key] = [value for value in result[key] if value not in removed]
    for key in ('required_event_classes', 'required_severities', 'required_condition_kinds'):
        if key in result:
            result[key] = {name: value for name, value in result[key].items() if name not in removed}
    return {key: value for key, value in result.items() if value not in ({}, [])}


def normalize_value_contract(contract):
    result = deepcopy(contract)
    for row in result.get('observations', []):
        tracking = row.get('change_tracking', True)
        if not isinstance(tracking, bool):
            raise ValueError(f"{row.get('canonical_field')}: change_tracking must be boolean")
        if not tracking:
            row['event_on_change'] = False
            row['assessment_roles'] = [role for role in row.get('assessment_roles', []) if role != 'change'] or ['evidence']
            if 'event_constraints' in row:
                row['event_constraints'] = _without_change_events(row['event_constraints'])
                if not row['event_constraints']:
                    row.pop('event_constraints')
    if "change_events" not in result:
        if not any('semantic_definition' in row for row in result.get('observations', [])):
            return result  # Other active checks retain their explicitly configured assessment rules.
        result['change_events'] = False
    if not isinstance(result["change_events"], bool):
        raise ValueError("change_events must be a boolean")
    result["value_contract"] = "same-name/v1"
    identities = set(result.get("identity_dimensions") or []) - {"device_id"}
    seen = set(identities)
    for row in result.get("observations", []):
        field = row.get("canonical_field", "")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", field) or field in seen:
            raise ValueError(f"invalid or duplicate standard Value: {field}")
        seen.add(field)
        if 'semantic_definition' in row and not result['change_events'] and 'current_state' in row.get('assessment_roles', []) and 'change' not in row.get('assessment_roles', []):
            row['event_on_change'] = False
            continue
        local_change = 'semantic_definition' in row and 'change' in row.get('assessment_roles', [])
        track_change = (result["change_events"] or local_change) and row.get("change_tracking", True)
        row["event_on_change"] = track_change
        roles = set(row.get("assessment_roles") or []) - {"change"}
        if track_change:
            roles.discard('evidence')
            roles.add("change")
            event_type = str(row["observation_type"]) + ".changed"
            constraints = _without_change_events(row.get('event_constraints'))
            constraints['required_event_types'] = list(dict.fromkeys([*constraints.get('required_event_types', []), event_type]))
            for key, value in (('role_event_types', {'change': event_type}), ('required_event_classes', {event_type: 'change'}), ('required_severities', {event_type: 'info'})):
                constraints[key] = {**constraints.get(key, {}), **value}
            constraints.setdefault('required_recovery_pairs', [])
            row['event_constraints'] = constraints
        else:
            if 'event_constraints' in row:
                row['event_constraints'] = _without_change_events(row['event_constraints'])
                if not row['event_constraints']:
                    row.pop('event_constraints')
        row["assessment_roles"] = sorted(roles) or ['evidence']
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
    fields = {field: {"source": field} for field in sorted(sources)}
    for row in rows:
        field = row['canonical_field']
        dimensions = row.get('dimensions') or []
        if field not in fields or not dimensions:
            continue
        missing = set(dimensions) - sources
        if missing:
            raise ValueError('TextFSM missing dimension Values: ' + ', '.join(sorted(missing)))
        fields[field]['dimensions'] = {name: {'field': name} for name in dimensions}
    return fields


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

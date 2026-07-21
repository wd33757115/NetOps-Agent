"""Declarative applicability guards for structured observations."""

from __future__ import annotations

from copy import deepcopy
from itertools import product
from typing import Any

APPLICABILITY_CONTEXT_KEY = "_observation_context"
SUPPORTED_PREDICATE_OPERATORS = {
    "contains_any_ci",
    "empty",
    "eq",
    "equals_field",
    "in_ci",
    "lte",
    "not_empty",
    "not_in_ci",
}


def _text(value: Any) -> str:
    return str(value or "").strip().casefold()


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _leaf_matches(predicate: dict[str, Any], context: dict[str, Any]) -> bool:
    field = str(predicate.get("field") or "").strip()
    operator = str(predicate.get("op") or "").strip()
    if not field or operator not in SUPPORTED_PREDICATE_OPERATORS:
        return False
    present = field in context
    value = context.get(field)
    if operator == "empty":
        return present and value in (None, "")
    if operator == "not_empty":
        return present and value not in (None, "")
    if not present:
        return False
    if operator == "eq":
        expected = predicate.get("value")
        left_number = _number(value)
        right_number = _number(expected)
        if left_number is not None and right_number is not None:
            return abs(left_number - right_number) < 1e-9
        return _text(value) == _text(expected)
    if operator == "lte":
        actual = _number(value)
        expected = _number(predicate.get("value"))
        return actual is not None and expected is not None and actual <= expected
    if operator == "equals_field":
        other = str(predicate.get("other_field") or "").strip()
        if not other or other not in context:
            return False
        left_number = _number(value)
        right_number = _number(context.get(other))
        if left_number is not None and right_number is not None:
            return abs(left_number - right_number) < 1e-9
        return _text(value) == _text(context.get(other))
    values = {_text(item) for item in predicate.get("values") or []}
    if operator == "in_ci":
        return _text(value) in values
    if operator == "not_in_ci":
        return _text(value) not in values
    if operator == "contains_any_ci":
        actual = _text(value)
        return any(item and item in actual for item in values)
    return False


def predicate_matches(predicate: Any, context: dict[str, Any]) -> bool:
    """Evaluate a small, non-executable predicate tree against record context."""

    if not isinstance(predicate, dict):
        return False
    if isinstance(predicate.get("all"), list):
        values = predicate["all"]
        return bool(values) and all(predicate_matches(item, context) for item in values)
    if isinstance(predicate.get("any"), list):
        values = predicate["any"]
        return bool(values) and any(predicate_matches(item, context) for item in values)
    if isinstance(predicate.get("not"), dict):
        return not predicate_matches(predicate["not"], context)
    return _leaf_matches(predicate, context)


def applicability_context_fields(applicability: Any) -> tuple[str, ...]:
    fields: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return
        for key in ("field", "other_field"):
            field = str(value.get(key) or "").strip()
            if field:
                fields.add(field)
        for key in ("all", "any", "not", "when", "exclude_when"):
            walk(value.get(key))

    walk(applicability)
    return tuple(sorted(fields))


def validate_applicability(
    applicability: Any,
    *,
    declared_fields: set[str] | None = None,
) -> list[str]:
    if applicability in (None, {}):
        return []
    if not isinstance(applicability, dict):
        return ["applicability must be an object"]
    errors: list[str] = []
    allowed = {"exclude_when", "on_missing_context", "version"}
    unknown = sorted(set(applicability) - allowed)
    if unknown:
        errors.append("applicability contains unsupported keys: " + ", ".join(unknown))
    missing_mode = str(applicability.get("on_missing_context") or "reject")
    if missing_mode not in {"allow", "reject"}:
        errors.append("applicability.on_missing_context must be allow or reject")
    exclusions = applicability.get("exclude_when")
    if not isinstance(exclusions, list) or not exclusions:
        errors.append("applicability.exclude_when must be a non-empty list")
        return errors

    def validate_predicate(value: Any, path: str) -> None:
        if not isinstance(value, dict):
            errors.append(f"{path} must be an object")
            return
        combinators = [key for key in ("all", "any", "not") if key in value]
        if combinators:
            if len(combinators) != 1:
                errors.append(f"{path} must contain exactly one boolean combinator")
                return
            key = combinators[0]
            nested = value[key]
            if key in {"all", "any"}:
                if not isinstance(nested, list) or not nested:
                    errors.append(f"{path}.{key} must be a non-empty list")
                    return
                for index, item in enumerate(nested):
                    validate_predicate(item, f"{path}.{key}[{index}]")
            else:
                validate_predicate(nested, f"{path}.not")
            return
        field = str(value.get("field") or "").strip()
        operator = str(value.get("op") or "").strip()
        if not field:
            errors.append(f"{path}.field is required")
        elif declared_fields is not None and field not in declared_fields:
            errors.append(f"{path}.field references undeclared field: {field}")
        if operator not in SUPPORTED_PREDICATE_OPERATORS:
            errors.append(f"{path}.op is unsupported: {operator or '<empty>'}")
        if operator in {"in_ci", "not_in_ci", "contains_any_ci"} and not isinstance(
            value.get("values"), list
        ):
            errors.append(f"{path}.values must be a list")
        if operator in {"eq", "lte"} and "value" not in value:
            errors.append(f"{path}.value is required")
        if operator == "equals_field":
            other = str(value.get("other_field") or "").strip()
            if not other:
                errors.append(f"{path}.other_field is required")
            elif declared_fields is not None and other not in declared_fields:
                errors.append(f"{path}.other_field references undeclared field: {other}")

    for index, exclusion in enumerate(exclusions):
        if not isinstance(exclusion, dict):
            errors.append(f"applicability.exclude_when[{index}] must be an object")
            continue
        code = str(exclusion.get("code") or "").strip()
        if not code:
            errors.append(f"applicability.exclude_when[{index}].code is required")
        validate_predicate(
            exclusion.get("when"),
            f"applicability.exclude_when[{index}].when",
        )
    return list(dict.fromkeys(errors))


def applicability_decision(
    applicability: Any,
    *,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(applicability, dict) or not applicability:
        return {"applicable": True, "reason": "not_governed"}
    if context is None:
        allowed = str(applicability.get("on_missing_context") or "reject") == "allow"
        return {
            "applicable": allowed,
            "reason": "missing_context_allowed" if allowed else "missing_observation_context",
        }
    for exclusion in applicability.get("exclude_when") or []:
        if isinstance(exclusion, dict) and predicate_matches(exclusion.get("when"), context):
            return {
                "applicable": False,
                "reason": str(exclusion.get("code") or "measurement_not_applicable"),
            }
    return {"applicable": True, "reason": "applicable"}


def excluded_context_example(applicability: Any) -> dict[str, Any] | None:
    """Build one deterministic context that exercises the first exclusion."""

    if not isinstance(applicability, dict):
        return None
    exclusions = applicability.get("exclude_when") or []
    if not exclusions or not isinstance(exclusions[0], dict):
        return None

    def materialize(predicate: Any) -> dict[str, Any] | None:
        if not isinstance(predicate, dict):
            return None
        if isinstance(predicate.get("all"), list):
            result: dict[str, Any] = {}
            for item in predicate["all"]:
                values = materialize(item)
                if values is None:
                    return None
                result.update(values)
            return result
        if isinstance(predicate.get("any"), list) and predicate["any"]:
            return materialize(predicate["any"][0])
        if "not" in predicate:
            return None
        field = str(predicate.get("field") or "").strip()
        operator = str(predicate.get("op") or "").strip()
        if not field:
            return None
        if operator == "empty":
            return {field: ""}
        if operator == "not_empty":
            return {field: "value"}
        if operator in {"in_ci", "contains_any_ci"}:
            values = list(predicate.get("values") or [])
            return {field: values[0]} if values else None
        if operator == "not_in_ci":
            return {field: "__other__"}
        if operator in {"eq", "lte"}:
            return {field: predicate.get("value")}
        if operator == "equals_field":
            other = str(predicate.get("other_field") or "").strip()
            return {field: 0, other: 0} if other else None
        return None

    return materialize(exclusions[0].get("when"))


def applicable_context_example(
    applicability: Any,
    *,
    seed: dict[str, Any] | None = None,
    max_candidates: int = 16_384,
) -> dict[str, Any] | None:
    """Build a bounded deterministic context that does not match any exclusion.

    Candidate values come from the declared predicate DSL. This keeps synthetic
    validation independent of vendors, commands, and observation field names.
    Existing values, such as per-record threshold references, are preserved.
    """

    context = deepcopy(seed or {})
    if not isinstance(applicability, dict) or not applicability:
        return context

    fields = applicability_context_fields(applicability)
    if not fields:
        decision = applicability_decision(applicability, context=context)
        return context if decision["applicable"] else None

    candidates: dict[str, list[Any]] = {field: [] for field in fields}

    def add(field: str, value: Any) -> None:
        if field not in candidates:
            return
        if not any(existing == value for existing in candidates[field]):
            candidates[field].append(value)

    for field in fields:
        if field in context:
            add(field, context[field])
        for value in ("__applicable__", 1, 0, "", None):
            add(field, value)

    def collect(predicate: Any) -> None:
        if not isinstance(predicate, dict):
            return
        for key in ("all", "any"):
            if isinstance(predicate.get(key), list):
                for item in predicate[key]:
                    collect(item)
                return
        if isinstance(predicate.get("not"), dict):
            collect(predicate["not"])
            return
        field = str(predicate.get("field") or "").strip()
        operator = str(predicate.get("op") or "").strip()
        if not field:
            return
        if operator in {"in_ci", "not_in_ci", "contains_any_ci"}:
            for value in predicate.get("values") or []:
                add(field, value)
        elif operator in {"eq", "lte"}:
            expected = predicate.get("value")
            add(field, expected)
            numeric = _number(expected)
            if numeric is not None:
                add(field, numeric + 1)
                add(field, numeric - 1)
        elif operator == "equals_field":
            other = str(predicate.get("other_field") or "").strip()
            add(field, 0)
            add(field, 1)
            if other:
                add(other, 0)
                add(other, 1)

    for exclusion in applicability.get("exclude_when") or []:
        if isinstance(exclusion, dict):
            collect(exclusion.get("when"))

    attempts = 0
    pools = [candidates[field] for field in fields]
    for values in product(*pools):
        attempts += 1
        if attempts > max_candidates:
            break
        candidate = {**context, **dict(zip(fields, values, strict=True))}
        if applicability_decision(applicability, context=candidate)["applicable"]:
            return candidate
    return None


def context_from_change(change: dict[str, Any]) -> dict[str, Any] | None:
    dimensions = change.get("dimensions")
    sources = (
        dimensions.get(APPLICABILITY_CONTEXT_KEY) if isinstance(dimensions, dict) else None,
        change.get("record_context"),
        change.get("context"),
    )
    merged: dict[str, Any] = {}
    found = False
    for value in sources:
        if isinstance(value, dict):
            merged.update(value)
            found = True
    return merged if found else None


def condition_applicability_matches(condition: dict[str, Any], change: dict[str, Any]) -> bool:
    applicability = condition.get("applicability")
    if not applicability:
        return True
    return bool(
        applicability_decision(
            applicability,
            context=context_from_change(change),
        )["applicable"]
    )


def applicability_for_fields(
    contract: dict[str, Any], fields: set[str] | list[str] | tuple[str, ...]
) -> dict[str, Any] | None:
    semantics = dict(contract.get("state_semantics") or {})
    values = []
    for field in fields:
        metadata = semantics.get(str(field))
        if isinstance(metadata, dict) and isinstance(metadata.get("applicability"), dict):
            values.append(metadata["applicability"])
    if not values:
        return None
    first = values[0]
    if any(value != first for value in values[1:]):
        return None
    return deepcopy(first)


def attach_governed_applicability(
    condition: dict[str, Any], contract: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    output = dict(condition)
    fields = {str(item) for item in output.get("fields") or []}
    governed = applicability_for_fields(contract, fields)
    if governed is None:
        output.pop("applicability", None)
        return output, False
    changed = output.get("applicability") != governed
    output["applicability"] = governed
    return output, changed

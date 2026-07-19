# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Compile, parse, field, value, and multi-sample TextFSM validation."""

from __future__ import annotations

import io
import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any

from src.core.parser.evidence_utils import literal_occurs
from src.core.parser.generation_diagnostics import ParserDiagnostic
from src.core.parser.key_contract import validate_entity_keys
from src.core.parser.textfsm_dialect import lint_textfsm_template
from src.core.parser.textfsm_input import prepare_textfsm_input

try:
    import textfsm
except ImportError:  # pragma: no cover
    textfsm = None  # type: ignore[assignment]


@dataclass
class ValidationResult:
    compile_success: bool = False
    parse_success: bool = False
    record_count: int = 0
    field_coverage: int = 0
    validation_score: int = 0
    missing_fields: list[str] = field(default_factory=list)
    value_errors: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    empty_accepted: bool = False
    key_errors: list[str] = field(default_factory=list)
    completeness_errors: list[str] = field(default_factory=list)
    evidence_errors: list[str] = field(default_factory=list)
    diagnostics: list[ParserDiagnostic] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return (
            self.compile_success
            and self.parse_success
            and (self.record_count > 0 or self.empty_accepted)
            and not self.missing_fields
            and not self.value_errors
            and not self.key_errors
            and not self.completeness_errors
            and not self.evidence_errors
        )


def _parse_percent(value: Any) -> float | None:
    try:
        return float(str(value).strip().rstrip("%"))
    except ValueError:
        return None


def validate_field_values(
    records: list[dict[str, Any]],
    validators: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    for field_name, rules in validators.items():
        if not isinstance(rules, dict):
            errors.append(f"validator for {field_name} must be an object")
            continue
        rule_type = rules.get("type")
        for index, record in enumerate(records):
            if field_name not in record:
                continue
            raw_value = record[field_name]
            if raw_value is None or raw_value == "" or raw_value == []:
                continue
            values = raw_value if isinstance(raw_value, (list, tuple)) else [raw_value]
            for value_index, raw in enumerate(values):
                path = f"record[{index}].{field_name}"
                if isinstance(raw_value, (list, tuple)):
                    path += f"[{value_index}]"
                if rule_type == "percent":
                    value = _parse_percent(raw)
                    if value is None:
                        errors.append(f"{path}={raw!r} is not numeric")
                    elif not 0 <= value <= 100:
                        errors.append(f"{path}={raw!r} is outside 0-100")
                elif rule_type == "enum":
                    allowed = {
                        str(value).strip().lower()
                        for value in rules.get("values") or []
                    }
                    if allowed and str(raw).strip().lower() not in allowed:
                        errors.append(f"{path}={raw!r} is not in {sorted(allowed)}")
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
                        errors.append(f"{path}={raw!r} is not an IP address")
                elif rule_type == "number":
                    try:
                        value = float(str(raw).strip())
                    except ValueError:
                        errors.append(f"{path}={raw!r} is not numeric")
                        continue
                    minimum = rules.get("minimum")
                    maximum = rules.get("maximum")
                    if minimum is not None and value < float(minimum):
                        errors.append(f"{path}={raw!r} is below {minimum}")
                    if maximum is not None and value > float(maximum):
                        errors.append(f"{path}={raw!r} is above {maximum}")
                elif rule_type == "regex":
                    pattern = str(rules.get("pattern") or "")
                    if pattern and re.fullmatch(pattern, str(raw).strip()) is None:
                        errors.append(f"{path}={raw!r} does not match {pattern!r}")
                elif rule_type == "interface_name":
                    text = str(raw).strip().casefold()
                    if not text or text in {
                        "interface",
                        "port",
                        "interface name",
                        "port name",
                    }:
                        errors.append(f"{path}={raw!r} is not an entity name")
    return errors


def check_required_fields(
    records: list[dict[str, Any]],
    required_fields: list[str],
) -> tuple[int, list[str]]:
    if not required_fields:
        return 100, []
    if not records:
        return 0, list(required_fields)
    missing = [
        field
        for field in required_fields
        if any(
            record.get(field) is None
            or record.get(field) == ""
            or record.get(field) == []
            for record in records
        )
    ]
    coverage = round(100 * (len(required_fields) - len(missing)) / len(required_fields))
    return int(coverage), missing


def parse_with_template(
    template_text: str,
    cli_output: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    if textfsm is None:
        return [], ["textfsm is not installed"]
    try:
        fsm = textfsm.TextFSM(io.StringIO(template_text))
        rows = fsm.ParseText(prepare_textfsm_input(template_text, cli_output))
        return [dict(zip(fsm.header, row)) for row in rows], []
    except Exception as exc:
        return [], [f"parse failed: {exc}"]


def uncovered_structured_lines(
    template_text: str,
    cli_output: str,
    *,
    primary_keys: list[str] | tuple[str, ...],
    records: list[dict[str, Any]],
    captured_fields: set[str],
) -> list[dict[str, Any]]:
    """Find numeric entity rows that no field-capturing rule can parse."""

    value_patterns: dict[str, str] = {}
    declaration = re.compile(
        r"^Value(?:\s+[A-Za-z,]+)?\s+([A-Za-z_][A-Za-z0-9_]*)\s+\((.*)\)$"
    )
    for line in str(template_text or "").splitlines():
        match = declaration.match(line.strip())
        if match:
            value_patterns[match.group(1)] = match.group(2)

    capture_rules: list[tuple[re.Pattern[str], set[str]]] = []
    for line in str(template_text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("^"):
            continue
        pattern_text = stripped.split("->", 1)[0].rstrip().replace("$$", "$")
        fields = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", pattern_text))
        if not fields.intersection(captured_fields):
            continue
        for field_name in fields:
            field_pattern = value_patterns.get(field_name) or r"\S+"
            pattern_text = pattern_text.replace(
                f"${{{field_name}}}",
                f"(?:{field_pattern})",
            )
        try:
            capture_rules.append((re.compile(pattern_text), fields))
        except re.error:
            continue

    key_values = [
        tuple(str(record.get(key) or "").strip() for key in primary_keys)
        for record in records
    ]
    key_values = [key for key in key_values if key and all(key)]
    current_key: tuple[str, ...] | None = None
    uncovered: list[dict[str, Any]] = []
    numeric_token = re.compile(r"(?<![\w/])[-+]?\d+(?:\.\d+)?(?![\w/])")
    missing_token = re.compile(r"(?i)(?<!\w)(?:N/A|NA|--)(?!\w)")
    for line_number, line in enumerate(str(cli_output or "").splitlines(), 1):
        matching_key = next(
            (
                key
                for key in key_values
                if all(literal_occurs(line, component) for component in key)
            ),
            None,
        )
        if matching_key:
            current_key = matching_key
            continue
        if current_key is None:
            continue
        value_count = len(numeric_token.findall(line)) + len(missing_token.findall(line))
        if value_count < 2:
            continue
        if any(rule.match(line) for rule, _fields in capture_rules):
            continue
        uncovered.append(
            {
                "line_number": line_number,
                "entity_key": list(current_key),
                "line_shape": re.sub(r"[-+]?\d+(?:\.\d+)?", "<N>", line.strip())[:240],
            }
        )
    return uncovered


def compile_template_diagnostics(
    template_text: str,
) -> tuple[bool, list[str], list[ParserDiagnostic]]:
    if textfsm is None:
        diagnostic = ParserDiagnostic(
            code="dialect_version_mismatch",
            message="textfsm is not installed",
        )
        return False, [diagnostic.message], [diagnostic]
    violations = lint_textfsm_template(template_text)
    diagnostics = [
        ParserDiagnostic(
            code=item.code,
            message=item.message,
            line_number=item.line_number,
            evidence={"template_line": item.evidence} if item.evidence else {},
        )
        for item in violations
    ]
    errors = [item.message for item in diagnostics]
    return not diagnostics, errors, diagnostics


def compile_template(template_text: str) -> tuple[bool, list[str]]:
    success, errors, _diagnostics = compile_template_diagnostics(template_text)
    return success, errors


def _binding_items(
    field_bindings: dict[str, Any],
    field_name: str,
) -> list[dict[str, Any]]:
    raw = field_bindings.get(field_name) or []
    values = raw if isinstance(raw, list) else [raw]
    return [item for item in values if isinstance(item, dict)]


def _scalar_record_values(value: Any) -> list[str]:
    values = value if isinstance(value, (list, tuple)) else [value]
    return [str(item).strip() for item in values if str(item or "").strip()]


def validate_record_evidence_alignment(
    records: list[dict[str, Any]],
    cli_output: str,
    *,
    primary_keys: list[str] | tuple[str, ...],
    field_bindings: dict[str, Any] | None,
    sample_id: str | None,
    evidence_line_map: dict[int, int] | None = None,
) -> tuple[list[str], list[ParserDiagnostic]]:
    """Prove that evidenced fields belong to the entity that emitted them.

    Record counts and key sets cannot detect a TextFSM template that commits a
    block when the next key is seen and consequently shifts non-key fields by
    one entity. Contract bindings cite source lines, so each cited value must be
    present on the source line of the parsed record for the nearest preceding
    entity key. Values are compared by lexical occurrence rather than equality
    so this remains valid for redacted prompts and list-valued captures.
    """

    bindings = dict(field_bindings or {})
    keys = tuple(str(field) for field in primary_keys if str(field))
    if not records or not keys or not bindings or not sample_id:
        return [], []

    lines = str(cli_output or "").splitlines()
    anchored_records: list[tuple[int, int, dict[str, Any]]] = []
    cursor = 0
    for record_index, record in enumerate(records):
        components = [str(record.get(key) or "").strip() for key in keys]
        if not components or not all(components):
            continue
        line_index = next(
            (
                index
                for index in range(cursor, len(lines))
                if all(literal_occurs(lines[index], component) for component in components)
            ),
            None,
        )
        if line_index is None:
            continue
        anchored_records.append((line_index + 1, record_index, record))
        cursor = line_index + 1
    if not anchored_records:
        return [], []

    errors: list[str] = []
    diagnostics: list[ParserDiagnostic] = []
    seen: set[tuple[str, int, int]] = set()
    for field_name in bindings:
        for binding in _binding_items(bindings, field_name):
            if str(binding.get("sample_id") or "") != sample_id:
                continue
            if str(binding.get("capture_kind") or "") == "derived":
                continue
            try:
                prompt_line = int(binding.get("line") or binding.get("line_number"))
            except (TypeError, ValueError):
                continue
            if evidence_line_map is not None:
                runtime_line = evidence_line_map.get(prompt_line)
                if runtime_line is None:
                    continue
            else:
                runtime_line = prompt_line
            if runtime_line < 1 or runtime_line > len(lines):
                continue
            anchor = next(
                (
                    item
                    for item in reversed(anchored_records)
                    if item[0] <= runtime_line
                ),
                None,
            )
            if anchor is None:
                # Global Filldown metadata before the first entity has no
                # unambiguous per-record ownership and is validated elsewhere.
                continue
            _anchor_line, record_index, record = anchor
            signature = (field_name, runtime_line, record_index)
            if signature in seen:
                continue
            seen.add(signature)
            values = _scalar_record_values(record.get(field_name))
            value_origin_matches = any(
                literal_occurs(lines[runtime_line - 1], value) for value in values
            )
            if value_origin_matches:
                continue
            missing_value = not values
            code = (
                "missing_evidenced_field_instance"
                if missing_value
                else "record_evidence_mismatch"
            )
            message = (
                f"parsed field {field_name!r} is missing for its evidenced entity "
                f"line {runtime_line}"
                if missing_value
                else (
                    f"parsed field {field_name!r} is not sourced from its evidenced "
                    f"entity line {runtime_line}"
                )
            )
            errors.append(message)
            diagnostics.append(
                ParserDiagnostic(
                    code=code,
                    message=message,
                    line_number=runtime_line,
                    expected={
                        "field": field_name,
                        "source": "contract_evidence_line",
                    },
                    actual={
                        "field_present": bool(values),
                        "value_origin_matches_line": False,
                    },
                    evidence={
                        "field": field_name,
                        "entity_ordinal": record_index + 1,
                        "binding_kind": str(binding.get("capture_kind") or ""),
                        "repair_hint": (
                            "capture this evidenced field with its runtime validator"
                            if missing_value
                            else (
                                "commit the previous entity before capturing the next "
                                "key; use a non-capturing Continue.Record boundary and "
                                "a second rule to capture the key on the same line"
                            )
                        ),
                    },
                )
            )
    return errors, diagnostics


def validate_template(
    template_text: str,
    cli_output: str,
    required_fields: list[str],
    validators: dict[str, dict[str, Any]] | None = None,
    *,
    allow_empty: bool = False,
    empty_patterns: list[str] | None = None,
    primary_keys: list[str] | tuple[str, ...] | None = None,
    expected_min_records: int | None = None,
    expected_record_count: int | None = None,
    expected_primary_keys: set[tuple[str, ...]] | None = None,
    field_bindings: dict[str, Any] | None = None,
    sample_id: str | None = None,
    evidence_line_map: dict[int, int] | None = None,
) -> ValidationResult:
    result = ValidationResult()
    validators = validators or {}
    (
        result.compile_success,
        compile_errors,
        compile_diagnostics,
    ) = compile_template_diagnostics(template_text)
    result.errors.extend(compile_errors)
    result.diagnostics.extend(compile_diagnostics)
    if not result.compile_success:
        return result

    records, parse_errors = parse_with_template(template_text, cli_output)
    result.errors.extend(parse_errors)
    if parse_errors:
        result.diagnostics.append(
            ParserDiagnostic(
                code="template_syntax_error",
                message=parse_errors[0],
            )
        )
        result.validation_score = 25
        return result

    result.records = records
    result.record_count = len(records)
    result.parse_success = bool(records)
    if not records:
        if allow_empty and not expected_min_records and any(
            re.search(pattern, cli_output, re.IGNORECASE | re.MULTILINE)
            for pattern in (empty_patterns or [])
        ):
            result.parse_success = True
            result.empty_accepted = True
            result.field_coverage = 100
            result.validation_score = 100
            return result
        if allow_empty and expected_min_records:
            message = (
                "empty output cannot be accepted because the sample contains "
                f"at least {expected_min_records} entities"
            )
            result.errors.append(message)
            result.diagnostics.append(
                ParserDiagnostic(
                    code="missing_entities",
                    message=message,
                    expected=expected_min_records,
                    actual=0,
                )
            )
        zero_message = "parsed record count is 0"
        result.errors.append(zero_message)
        result.diagnostics.append(
            ParserDiagnostic(
                code="legal_empty_unproven" if allow_empty else "zero_records",
                message=zero_message,
                expected=expected_min_records or ">0",
                actual=0,
            )
        )
        result.validation_score = 25
        return result

    if expected_min_records and len(records) < expected_min_records:
        result.completeness_errors.append(
            "parsed record count "
            f"{len(records)} is below expected minimum {expected_min_records}"
        )

    if expected_record_count is not None and len(records) > expected_record_count:
        message = (
            f"parsed record count {len(records)} exceeds the detected entity count "
            f"{expected_record_count}; emit exactly one Record per entity boundary"
        )
        result.completeness_errors.append(message)
        result.errors.append(message)
        result.diagnostics.append(
            ParserDiagnostic(
                code="record_boundary_error",
                message=message,
                expected=expected_record_count,
                actual=len(records),
                evidence={"repair_hint": "move Record to the complete entity boundary"},
            )
        )
        result.errors.extend(result.completeness_errors)
        result.diagnostics.append(
            ParserDiagnostic(
                code="missing_entities",
                message=result.completeness_errors[-1],
                expected=expected_min_records,
                actual=len(records),
            )
        )

    result.field_coverage, result.missing_fields = check_required_fields(
        records, required_fields
    )
    result.value_errors = validate_field_values(records, validators)
    result.errors.extend(result.value_errors)
    if result.missing_fields:
        result.diagnostics.append(
            ParserDiagnostic(
                code="missing_required_fields",
                message=f"missing required fields: {', '.join(result.missing_fields)}",
                expected=list(required_fields),
                actual=[
                    field_name
                    for field_name in required_fields
                    if field_name not in result.missing_fields
                ],
                evidence={"parsed_records_preview": records[:3]},
            )
        )
    if result.value_errors:
        result.diagnostics.append(
            ParserDiagnostic(
                code="invalid_field_value",
                message=result.value_errors[0],
                evidence={"errors": result.value_errors[:10]},
            )
        )
    key_result = validate_entity_keys(records, primary_keys or ())
    result.key_errors = list(key_result.errors)
    result.errors.extend(result.key_errors)
    for error in result.key_errors:
        result.diagnostics.append(
            ParserDiagnostic(
                code=(
                    "duplicate_primary_key"
                    if "duplicate" in error.lower()
                    else "missing_primary_key"
                ),
                message=error,
            )
        )
    if expected_primary_keys and primary_keys:
        actual_keys = {
            tuple(str(record.get(field, "")).strip() for field in primary_keys)
            for record in records
        }
        missing_keys = sorted(expected_primary_keys - actual_keys)
        if missing_keys:
            preview = ", ".join("/".join(key) for key in missing_keys[:10])
            suffix = "" if len(missing_keys) <= 10 else f" (+{len(missing_keys) - 10} more)"
            message = f"missing expected entity keys: {preview}{suffix}"
            result.completeness_errors.append(message)
            result.errors.append(message)
            result.diagnostics.append(
                ParserDiagnostic(
                    code="missing_entities",
                    message=message,
                    expected=sorted(expected_primary_keys),
                    actual=sorted(actual_keys),
                    evidence={"missing_keys": missing_keys[:50]},
                )
            )
        unexpected_keys = sorted(actual_keys - expected_primary_keys)
        if unexpected_keys:
            preview = ", ".join("/".join(key) for key in unexpected_keys[:10])
            suffix = (
                ""
                if len(unexpected_keys) <= 10
                else f" (+{len(unexpected_keys) - 10} more)"
            )
            message = f"unexpected entity keys: {preview}{suffix}"
            result.completeness_errors.append(message)
            result.errors.append(message)
            result.diagnostics.append(
                ParserDiagnostic(
                    code="extra_entities",
                    message=message,
                    expected=sorted(expected_primary_keys),
                    actual=sorted(actual_keys),
                    evidence={"unexpected_keys": unexpected_keys[:50]},
                )
            )
    evidence_errors, evidence_diagnostics = validate_record_evidence_alignment(
        records,
        cli_output,
        primary_keys=primary_keys or (),
        field_bindings=field_bindings,
        sample_id=sample_id,
        evidence_line_map=evidence_line_map,
    )
    result.evidence_errors.extend(evidence_errors)
    result.errors.extend(evidence_errors)
    result.diagnostics.extend(evidence_diagnostics)
    score = 50 + round(25 * result.field_coverage / 100)
    contract_errors = (
        result.value_errors
        + result.key_errors
        + result.completeness_errors
        + result.evidence_errors
    )
    score += 25 if not contract_errors else max(0, 25 - len(contract_errors) * 5)
    result.validation_score = min(100, int(score))
    return result


def strip_llm_template(raw: str) -> str:
    text = str(raw or "").strip()
    fence = re.search(r"```(?:textfsm|text|)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    output: list[str] = []
    started = False
    for line in text.splitlines():
        if not started and (
            line.strip().startswith("Value ") or line.strip().startswith("Start")
        ):
            started = True
        if started:
            output.append(line)
    cleaned = "\n".join(output).strip() if output else text.strip()
    return f"{cleaned}\n"


def canonicalize_template_fields(
    template_text: str,
    contract_fields: list[str] | tuple[str, ...],
) -> str:
    """Normalize generated declarations without changing capture regexes.

    TextFSM's ``Required`` option silently drops incomplete records. The agent
    validator must see those records so it can report missing fields and entity
    loss deterministically, therefore generated templates delegate required-field
    enforcement to the platform gates. ``Filldown`` remains available.
    """

    canonical = {str(field).casefold(): str(field) for field in contract_fields}
    replacements: dict[str, str] = {}
    value_pattern = re.compile(
        r"^(?P<indent>\s*)Value(?P<options>\s+[A-Za-z,]+)?\s+"
        r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
        r"(?P<suffix>\s+\(.*)$"
    )
    lines: list[str] = []
    for line in str(template_text or "").splitlines():
        match = value_pattern.match(line)
        if match:
            declared = match.group("name")
            target = canonical.get(declared.casefold())
            if target and target != declared:
                replacements[declared] = target
            field_name = target or declared
            options = [
                option.strip()
                for option in str(match.group("options") or "").split(",")
                if option.strip() and option.strip().casefold() != "required"
            ]
            option_text = f" {','.join(options)}" if options else ""
            line = (
                f"{match.group('indent')}Value{option_text} {field_name}"
                f"{match.group('suffix')}"
            )
        lines.append(line)
    normalized = "\n".join(lines)
    for declared, target in replacements.items():
        normalized = re.sub(
            rf"\$\{{{re.escape(declared)}\}}",
            "${" + target + "}",
            normalized,
        )
    return f"{normalized.rstrip()}\n"

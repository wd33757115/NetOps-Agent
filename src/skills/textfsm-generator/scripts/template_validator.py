# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Compile, parse, field, value, and multi-sample TextFSM validation."""

from __future__ import annotations

import io
import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any

from src.core.parser.key_contract import validate_entity_keys

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
        rule_type = rules.get("type")
        for index, record in enumerate(records):
            if field_name not in record or record[field_name] in {None, ""}:
                continue
            raw = record[field_name]
            if rule_type == "percent":
                value = _parse_percent(raw)
                if value is None:
                    errors.append(f"record[{index}].{field_name}={raw!r} is not numeric")
                elif not 0 <= value <= 100:
                    errors.append(f"record[{index}].{field_name}={raw!r} is outside 0-100")
            elif rule_type == "enum":
                allowed = {str(value).strip().lower() for value in rules.get("values") or []}
                if allowed and str(raw).strip().lower() not in allowed:
                    errors.append(
                        f"record[{index}].{field_name}={raw!r} is not in {sorted(allowed)}"
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
                        f"record[{index}].{field_name}={raw!r} is not an IP address"
                    )
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
        if any(str(record.get(field, "")).strip() == "" for record in records)
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
        rows = fsm.ParseText(cli_output)
        return [dict(zip(fsm.header, row)) for row in rows], []
    except Exception as exc:
        return [], [f"parse failed: {exc}"]


def compile_template(template_text: str) -> tuple[bool, list[str]]:
    if textfsm is None:
        return False, ["textfsm is not installed"]
    try:
        textfsm.TextFSM(io.StringIO(template_text))
        return True, []
    except Exception as exc:
        return False, [f"compile failed: {exc}"]


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
) -> ValidationResult:
    result = ValidationResult()
    validators = validators or {}
    result.compile_success, compile_errors = compile_template(template_text)
    result.errors.extend(compile_errors)
    if not result.compile_success:
        return result

    records, parse_errors = parse_with_template(template_text, cli_output)
    result.errors.extend(parse_errors)
    if parse_errors:
        result.validation_score = 25
        return result

    result.records = records
    result.record_count = len(records)
    result.parse_success = bool(records)
    if not records:
        if allow_empty and any(
            re.search(pattern, cli_output, re.IGNORECASE | re.MULTILINE)
            for pattern in (empty_patterns or [])
        ):
            result.parse_success = True
            result.empty_accepted = True
            result.field_coverage = 100
            result.validation_score = 100
            return result
        result.errors.append("parsed record count is 0")
        result.validation_score = 25
        return result

    if expected_min_records and len(records) < expected_min_records:
        result.completeness_errors.append(
            "parsed record count "
            f"{len(records)} is below expected minimum {expected_min_records}"
        )
        result.errors.extend(result.completeness_errors)

    result.field_coverage, result.missing_fields = check_required_fields(
        records, required_fields
    )
    result.value_errors = validate_field_values(records, validators)
    result.errors.extend(result.value_errors)
    key_result = validate_entity_keys(records, primary_keys or ())
    result.key_errors = list(key_result.errors)
    result.errors.extend(result.key_errors)
    score = 50 + round(25 * result.field_coverage / 100)
    contract_errors = (
        result.value_errors + result.key_errors + result.completeness_errors
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
            # LLMs sometimes emit the invalid `-> State.Record` form for a
            # parent line. The parent should only enter the child state;
            # child rows are responsible for emitting records.
            line = re.sub(
                r"(\s+)->\s*([A-Za-z][A-Za-z0-9_]*)\.Record\s*$",
                r"\1-> \2",
                line,
            )
            output.append(line)
    cleaned = "\n".join(output).strip() if output else text.strip()
    if re.search(r"(?m)^Value\s+Filldown\s+", cleaned) and not re.search(
        r"(?m)^EOF\s*$", cleaned
    ):
        cleaned = f"{cleaned}\n\nEOF"
    return f"{cleaned}\n"

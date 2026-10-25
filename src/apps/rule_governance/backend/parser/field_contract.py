"""Immutable ParserSpec-to-TextFSM field-contract evidence."""

from __future__ import annotations

import io
from collections.abc import Mapping
from typing import Any

from src.apps.rule_governance.backend.parser.parser_bundle import parse_parser_bundle

try:
    import textfsm
except ImportError:  # pragma: no cover
    textfsm = None


def _field_names(payload: Mapping[str, Any]) -> set[str]:
    fields = {
        str(name)
        for name in dict(payload.get("fields") or {})
        if str(name).strip()
    }
    for key in ("primary_keys", "required_fields", "optional_fields"):
        fields.update(
            str(item)
            for item in payload.get(key) or []
            if str(item).strip()
        )
    return fields


def _raw_record_sets(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("record_sets")
    if isinstance(raw, Mapping):
        return [
            {"name": str(name), **dict(config)}
            for name, config in raw.items()
            if isinstance(config, Mapping)
        ]
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, Mapping)]
    return []


def parser_spec_field_sets(
    validation: Mapping[str, Any] | None,
    *,
    template_record_set_names: set[str] | None = None,
) -> dict[str, list[str]]:
    """Read the frozen ParserSpec field inventory using runtime record-set names."""

    payload = dict(validation or {})
    source = next(
        (
            dict(value)
            for value in (
                payload.get("source_parser_spec"),
                payload.get("parser_spec"),
            )
            if isinstance(value, Mapping)
        ),
        payload,
    )
    record_sets = _raw_record_sets(source)
    actual_names = set(template_record_set_names or set())
    if record_sets:
        if actual_names == {"default"} and len(record_sets) == 1:
            return {"default": sorted(_field_names(record_sets[0]))}
        return {
            str(item.get("name") or item.get("entity") or "").strip(): sorted(
                _field_names(item)
            )
            for item in record_sets
            if str(item.get("name") or item.get("entity") or "").strip()
        }
    fields = _field_names(source)
    return {"default": sorted(fields)} if fields else {}


def textfsm_field_sets(template_text: str | None) -> dict[str, list[str]]:
    """Compile TextFSM content and return its actual emitted field names."""

    if textfsm is None:
        raise RuntimeError("textfsm library is unavailable")
    content = str(template_text or "").strip()
    if not content:
        raise ValueError("Parser template content is unavailable")
    bundle = parse_parser_bundle(content)
    if bundle is not None:
        return {
            item.name: sorted(
                str(field)
                for field in textfsm.TextFSM(
                    io.StringIO(item.template_text)
                ).header
            )
            for item in bundle.record_sets
        }
    parser = textfsm.TextFSM(io.StringIO(content))
    return {"default": sorted(str(field) for field in parser.header)}


def build_parser_field_contract(
    *,
    validation: Mapping[str, Any] | None,
    template_text: str | None,
) -> dict[str, Any]:
    """Prove that ParserSpec and executable TextFSM expose the same fields."""

    try:
        template_sets = textfsm_field_sets(template_text)
    except Exception as exc:
        return {
            "schema_version": "parser-field-contract/v1",
            "status": "template_invalid",
            "aligned": False,
            "parser_spec_record_sets": {},
            "template_record_sets": {},
            "differences": [],
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
    spec_sets = parser_spec_field_sets(
        validation,
        template_record_set_names=set(template_sets),
    )
    if not spec_sets:
        return {
            "schema_version": "parser-field-contract/v1",
            "status": "parser_spec_missing",
            "aligned": False,
            "parser_spec_record_sets": {},
            "template_record_sets": template_sets,
            "differences": [],
            "errors": ["approved ParserSpec has no field inventory"],
        }

    differences: list[dict[str, Any]] = []
    for name in sorted(set(spec_sets) | set(template_sets)):
        spec_fields = set(spec_sets.get(name) or [])
        template_fields = set(template_sets.get(name) or [])
        missing_in_template = sorted(spec_fields - template_fields)
        undeclared_in_spec = sorted(template_fields - spec_fields)
        if (
            name not in spec_sets
            or name not in template_sets
            or missing_in_template
            or undeclared_in_spec
        ):
            differences.append(
                {
                    "record_set": name,
                    "parser_spec_present": name in spec_sets,
                    "template_present": name in template_sets,
                    "missing_in_template": missing_in_template,
                    "undeclared_in_parser_spec": undeclared_in_spec,
                }
            )
    return {
        "schema_version": "parser-field-contract/v1",
        "status": "aligned" if not differences else "schema_drift",
        "aligned": not differences,
        "parser_spec_record_sets": spec_sets,
        "template_record_sets": template_sets,
        "differences": differences,
        "errors": [],
    }


__all__ = [
    "build_parser_field_contract",
    "parser_spec_field_sets",
    "textfsm_field_sets",
]

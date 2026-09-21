"""Scoped TextFSM Value aliases shared by Parser generation and reporting."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.apps.rule_governance.paths import VALUE_MAPPING_CONFIG

BASE_SEMANTIC_CATALOG_PATH = (
    Path(__file__).with_name("assets") / "contract_semantics.v1.yaml"
)
DEFAULT_VALUE_MAPPING_CONFIG_PATH = VALUE_MAPPING_CONFIG
VALUE_MAPPING_CONFIG_ENV = "NETOPS_PARSER_VALUE_MAPPING_CONFIG"
VALUE_MAPPING_CONFIG_VERSION = 2

_VALUE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def value_mapping_config_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    configured = str(os.getenv(VALUE_MAPPING_CONFIG_ENV) or "").strip()
    return Path(configured) if configured else DEFAULT_VALUE_MAPPING_CONFIG_PATH


def _value_name(value: Any, *, label: str) -> str:
    name = str(value or "").strip()
    if not _VALUE_NAME_RE.fullmatch(name):
        raise ValueError(
            f"{label} must start with a letter and contain only letters, digits, or underscores"
        )
    return name.casefold()


def _string_list(value: Any, *, label: str) -> list[str]:
    if isinstance(value, str):
        source = [value]
    elif isinstance(value, (list, tuple)):
        source = list(value)
    else:
        raise ValueError(f"{label} must be an array of TextFSM Value names")
    output: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(source):
        normalized = _value_name(item, label=f"{label}[{index}]")
        if normalized not in seen:
            output.append(normalized)
            seen.add(normalized)
    return output


def _scope_text(value: Any, *, label: str) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise ValueError(f"{label} cannot be empty")
    return text


def _normalize_scoped_mappings(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("scoped_value_mappings must be an array")
    output: list[dict[str, Any]] = []
    rules: set[tuple[str, str, str, str, str]] = set()
    alias_owners: dict[tuple[str, str, str, str, str], str] = {}
    for index, raw_rule in enumerate(value):
        if not isinstance(raw_rule, Mapping):
            raise ValueError(f"scoped_value_mappings[{index}] must be an object")
        scope = {
            key: _scope_text(
                raw_rule.get(key),
                label=f"scoped_value_mappings[{index}].{key}",
            )
            for key in ("check_item", "vendor", "model", "command")
        }
        canonical = _value_name(
            raw_rule.get("canonical"),
            label=f"scoped_value_mappings[{index}].canonical",
        )
        aliases = [
            alias
            for alias in _string_list(
                raw_rule.get("aliases"),
                label=f"scoped_value_mappings[{index}].aliases",
            )
            if alias != canonical
        ]
        if not aliases:
            raise ValueError(f"scoped_value_mappings[{index}].aliases cannot be empty")
        normalized_scope = tuple(scope[key].casefold() for key in scope)
        rule_key = (*normalized_scope, canonical)
        if rule_key in rules:
            raise ValueError(
                f"scoped_value_mappings[{index}] duplicates an existing scope and canonical Value"
            )
        rules.add(rule_key)
        for alias in aliases:
            alias_key = (*normalized_scope, alias)
            owner = alias_owners.get(alias_key)
            if owner is not None and owner != canonical:
                raise ValueError(
                    f"TextFSM Value alias {alias!r} maps to both {owner!r} and "
                    f"{canonical!r} in the same scope"
                )
            alias_owners[alias_key] = canonical
        output.append(
            {**scope, "canonical": canonical, "aliases": aliases}
        )
    return output


def normalize_value_mapping_document(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("TextFSM Value mapping configuration must be an object")
    version = int(payload.get("version") or VALUE_MAPPING_CONFIG_VERSION)
    if version != VALUE_MAPPING_CONFIG_VERSION:
        raise ValueError(f"unsupported TextFSM Value mapping version: {version}")
    return {
        "version": VALUE_MAPPING_CONFIG_VERSION,
        "scoped_value_mappings": _normalize_scoped_mappings(
            payload.get("scoped_value_mappings")
        ),
    }


@lru_cache(maxsize=16)
def _load_value_mapping_document_cached(
    path_text: str,
    modified_at_ns: int,
) -> dict[str, Any]:
    del modified_at_ns
    target = Path(path_text)
    if not target.exists():
        return normalize_value_mapping_document({})
    try:
        payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid TextFSM Value mapping YAML: {target}") from exc
    return normalize_value_mapping_document(payload)


def load_value_mapping_document(
    path: str | Path | None = None,
) -> dict[str, Any]:
    target = value_mapping_config_path(path).resolve()
    modified_at_ns = target.stat().st_mtime_ns if target.exists() else -1
    return deepcopy(
        _load_value_mapping_document_cached(str(target), modified_at_ns)
    )


def save_value_mapping_document(
    payload: Any,
    path: str | Path | None = None,
) -> dict[str, Any]:
    normalized = normalize_value_mapping_document(payload)
    target = value_mapping_config_path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_text(
        yaml.safe_dump(
            normalized,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    temporary.replace(target)
    return normalized


def parser_value_mapping_view() -> dict[str, Any]:
    document = load_value_mapping_document()
    return {
        "version": VALUE_MAPPING_CONFIG_VERSION,
        "config_path": str(value_mapping_config_path().resolve()),
        "scoped_value_mappings": list(
            document.get("scoped_value_mappings") or []
        ),
    }


def resolve_scoped_schema_field(
    field_name: Any,
    *,
    check_item: str | None,
    vendor: str | None,
    model: str | None,
    command: str | None,
) -> str | None:
    target = str(field_name or "").strip().casefold()
    scope = {
        "check_item": " ".join(str(check_item or "").strip().casefold().split()),
        "vendor": " ".join(str(vendor or "").strip().casefold().split()),
        "model": " ".join(str(model or "").strip().casefold().split()),
        "command": " ".join(str(command or "").strip().casefold().split()),
    }
    if not target or not all(scope.values()):
        return None
    for rule in load_value_mapping_document().get("scoped_value_mappings") or []:
        if target not in set(rule.get("aliases") or []):
            continue
        if all(
            " ".join(str(rule.get(key) or "").strip().casefold().split()) == value
            for key, value in scope.items()
        ):
            return str(rule.get("canonical") or "").strip().casefold() or None
    return None

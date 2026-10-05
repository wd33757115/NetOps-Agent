"""System base check items: the ones topology discovery consumes (owner decision 2026-10-04).

``assets/system_check_items.yaml`` lists them compactly; this module expands them into
the catalog's own payloads, so the existing loader, validator and bootstrap handle them
like any check item:

- every Value is a same-name/v1 field with its semantics owned by the check item
  (``semantic_definition``, frozen into ``rule_semantics_snapshot`` under the
  ``topology-system`` catalog id, independent of the shared semantic catalog);
- identity Values become ``identity_dimensions``; all others are evidence observations
  without change events;
- one collection implementation per platform family, selected by the existing resolver
  (vendor and platform match; a model exception wins on specificity);
- each implementation names the shared-library template that parses its command.

System check items and their implementations cannot be deleted
(``ensure_deletable``), and a new version may only add optional Values
(``ensure_compatible``).
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

SPEC_PATH = Path(__file__).parent / "assets" / "system_check_items.yaml"
TEMPLATE_ROOT = Path(__file__).resolve().parents[5] / "src/skills/shared/textfsm-templates"
SEMANTIC_TYPE = {"text": "text"}  # every other value type is a state
BLANK = r"\A\s*\Z"  # an output with nothing but whitespace


class SystemCheckItemError(ValueError):
    """A change that would remove what topology discovery relies on."""


@lru_cache(maxsize=1)
def spec(path: str | Path = SPEC_PATH) -> dict[str, Any]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(payload.get("check_items"), dict) or not payload["check_items"]:
        raise ValueError("system check items require check_items")
    return payload


def _contract(item_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    from src.core.check_items.local_semantics import validate_local_definition

    keys = list(raw.get("keys") or [])
    values = dict(raw["values"])
    if not keys or set(keys) - set(values):
        raise ValueError(f"{item_id}: keys must be declared Values")
    rules, observations, definitions = {}, [], []
    for name, (value_type, required, description) in values.items():
        if name in keys:
            rules[name] = {
                "kind": "identifier",
                "value_type": value_type,
                "description": description,
            }
            continue
        observation_type = f"{item_id}.{name.lower()}"
        definition = validate_local_definition(
            {
                "description": description,
                "semantic_type": SEMANTIC_TYPE.get(value_type, "state"),
                "value_types": [value_type],
                "assessment_roles": ["evidence"],
            },
            observation_type,
        )
        definitions.append(definition)
        observations.append(
            {
                "canonical_field": name,
                "observation_type": observation_type,
                "semantic_type": definition["semantic_type"],
                "value_type": value_type,
                "description": description,
                "required": bool(required),
                "assessment_roles": ["evidence"],
                "event_on_change": False,
                "change_tracking": False,
                "semantic_definition": definition,
            }
        )
    digest = hashlib.sha256(
        json.dumps(definitions, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "entity_type": raw["entity_type"],
        "value_contract": "same-name/v1",
        "change_events": False,
        "identity_dimensions": keys,
        "identity_dimension_rules": rules,
        "observations": observations,
        "rule_semantics_snapshot": {
            "catalog_id": spec()["catalog"],
            "version": int(spec()["version"]),
            "digest": digest,
            "observations": definitions,
        },
    }


def check_item_payloads() -> list[dict[str, Any]]:
    return [
        {
            "id": item_id,
            "version": int(raw.get("version") or 1),
            "name": raw["name"],
            "category": "topology",
            "description": raw.get("description")
            or f"{raw['name']}（系统基础检查项，拓扑发现使用）",
            "result_mode": "structured",
            "assessment_mode": "evidence",
            "report_projection": "auto@1",
            "status": "active",
            "canonical_contract": _contract(item_id, raw),
        }
        for item_id, raw in spec()["check_items"].items()
    ]


def implementation_id(row: dict[str, Any]) -> str:
    """``<check item>.<platform family>``; a model exception adds its ``variant`` name."""
    variant = f".{row['variant']}" if row.get("variant") else ""
    return f"{row['check_item']}.{row['platform']}{variant}"


def implementation_payloads() -> list[dict[str, Any]]:
    platforms = spec()["platforms"]
    payloads = []
    for row in spec()["implementations"]:
        platform = platforms[row["platform"]]
        match = {"vendor": platform["vendor"], "platforms": list(platform["platforms"])}
        if row.get("model_patterns"):
            if not row.get("variant"):
                raise ValueError(f"{implementation_id(row)}: a model exception needs a variant")
            match["model_patterns"] = list(row["model_patterns"])
        payloads.append(
            {
                "id": implementation_id(row),
                "version": int(row.get("version") or 1),
                "name": f"{row['check_item']} {platform['family']}",
                "check_item_ids": [row["check_item"]],
                "match": match,
                "commands": [{"command": row["command"], "priority": 100}],
                "collector": "cli",
                "parser_requirement": "structured",
                "template_scope": "model" if row.get("model_patterns") else "family",
                "selection_priority": 100,
                "status": "active",
                **({"output_quality_policy": policy} if (policy := _policy(row)) else {}),
            }
        )
    return payloads


def _policy(row: dict[str, Any]) -> dict[str, Any]:
    """valid_empty, required (lines every data output has) and counts ({pattern, of})."""
    policy: dict[str, Any] = {}
    if row.get("valid_empty"):
        policy["valid_empty_patterns"] = [
            BLANK if pattern == "BLANK" else pattern for pattern in row["valid_empty"]
        ]
    if row.get("required"):
        policy["required_patterns"] = list(row["required"])
    if row.get("counts"):
        policy["count_patterns"] = [
            {"pattern": count["pattern"], "of": count["of"]} for count in row["counts"]
        ]
    for pattern in [
        *policy.get("valid_empty_patterns", ()),
        *policy.get("required_patterns", ()),
        *(count["pattern"] for count in policy.get("count_patterns", ())),
    ]:
        re.compile(pattern)
    for count in policy.get("count_patterns", ()):
        if count["of"] not in {"rows", "keys"} or re.compile(count["pattern"]).groups < 1:
            raise ValueError(f"{implementation_id(row)}: a count is a group of rows or keys")
    return {"profile_version": 1, **policy} if policy else {}


def templates() -> dict[str, dict[str, Any]]:
    """{implementation id: {command, scoped command, template path and pin, vendor, family}}."""
    platforms = spec()["platforms"]
    return {
        implementation_id(row): {
            "command": row["command"],
            "scoped_command": row.get("scoped_command"),
            "scope_header": row.get("scope_header"),
            "units": dict(row.get("units") or {}),
            "defaults": dict(row.get("defaults") or {}),
            "template": row["template"],
            "sha256": row["sha256"],
            "vendor": platforms[row["platform"]]["vendor"],
            "family": platforms[row["platform"]]["family"],
        }
        for row in spec()["implementations"]
    }


def template_text(path: str) -> str:
    target = (TEMPLATE_ROOT / path).resolve()
    if TEMPLATE_ROOT.resolve() not in target.parents:
        raise ValueError("system template path leaves the shared library")
    return target.read_text(encoding="utf-8")


def system_check_item_ids() -> frozenset[str]:
    return frozenset(spec()["check_items"])


def system_implementation_ids() -> frozenset[str]:
    return frozenset(implementation_id(row) for row in spec()["implementations"])


def ensure_deletable(*, check_item_id: str | None = None, implementation: str | None = None):
    if check_item_id and check_item_id in system_check_item_ids():
        raise SystemCheckItemError(f"{check_item_id} 是系统基础检查项（拓扑发现使用），不能删除")
    if implementation and implementation in system_implementation_ids():
        raise SystemCheckItemError(f"{implementation} 是系统基础检查项的默认采集实现，不能删除")


def ensure_compatible(check_item_id: str, contract: dict[str, Any]) -> None:
    """A new version of a system check item keeps every required Value and its name."""
    if check_item_id not in system_check_item_ids():
        return
    raw = spec()["check_items"][check_item_id]
    required = {name for name, (_, needed, _) in raw["values"].items() if needed}
    fields = set(contract.get("identity_dimensions") or ()) | {
        str(row.get("canonical_field")) for row in contract.get("observations") or ()
    }
    missing = sorted(required - fields)
    if missing:
        raise SystemCheckItemError(
            f"{check_item_id} 是系统基础检查项，不能删除或改名必填 Value：{', '.join(missing)}"
        )

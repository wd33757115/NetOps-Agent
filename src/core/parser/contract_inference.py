"""Infer parser data contracts during validation-mode template generation."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from .policy import CategorySpec


@dataclass(frozen=True)
class ContractProposal:
    category: str
    entity_type: str
    primary_keys: tuple[str, ...]
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...] = ()
    validators: dict[str, dict[str, Any]] | None = None
    allow_empty: bool = False
    empty_patterns: tuple[str, ...] = ()
    ignore_fields: tuple[str, ...] = ()
    source: str = "semantic_rules"
    confidence: float = 0.9

    def to_category_spec(self) -> CategorySpec:
        return CategorySpec(
            name=self.category,
            entity_type=self.entity_type,
            primary_keys=self.primary_keys,
            required_fields=self.required_fields,
            optional_fields=self.optional_fields,
            validators=self.validators or {},
            allow_empty=self.allow_empty,
            empty_patterns=self.empty_patterns,
            ignore_fields=self.ignore_fields,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SEMANTIC_RULES: tuple[tuple[re.Pattern[str], ContractProposal], ...] = (
    (
        re.compile(r"\bversion$"),
        ContractProposal(
            "device_version", "device", (), ("model", "os_version"),
            ("software_release", "uptime"), ignore_fields=("uptime",),
        ),
    ),
    (
        re.compile(r"\b(?:inventory|manuinfo)$"),
        ContractProposal(
            "inventory", "inventory_component", ("component", "serial"),
            ("component", "name"), ("pid", "serial", "vendor"),
        ),
    ),
    (
        re.compile(r"\b(?:cpu|processes cpu)$"),
        ContractProposal(
            "cpu", "cpu", ("slot",), ("slot", "cpu_5s", "cpu_1m", "cpu_5m"),
            validators={
                "cpu_5s": {"type": "percent"},
                "cpu_1m": {"type": "percent"},
                "cpu_5m": {"type": "percent"},
            },
        ),
    ),
    (
        re.compile(r"\b(?:memory|processes memory)$"),
        ContractProposal(
            "memory", "memory", ("slot",), ("slot", "total", "used", "free", "usage"),
            validators={"usage": {"type": "percent"}},
        ),
    ),
    (
        re.compile(r"\bsystem resources$"),
        ContractProposal(
            "system_resources", "system_resource", ("slot",),
            ("slot", "cpu_5s", "total", "used", "free"),
            ("cpu_1m", "cpu_5m", "usage"),
            validators={
                "cpu_5s": {"type": "percent"},
                "cpu_1m": {"type": "percent"},
                "cpu_5m": {"type": "percent"},
                "usage": {"type": "percent"},
            },
        ),
    ),
    (
        re.compile(r"\b(?:ip interface brief|interface brief)$"),
        ContractProposal(
            "interface_l3", "interface_l3", ("interface",),
            ("interface", "status", "protocol"), ("ip_address", "description"),
            validators={"ip_address": {"type": "ip_or_unassigned"}},
            allow_empty=True,
            empty_patterns=(r"(?is)Interface\s+IP Address.*Status\s*$",),
        ),
    ),
    (
        re.compile(r"\binterface status$"),
        ContractProposal(
            "interface_l2", "interface_l2", ("interface",),
            ("interface", "status"), ("vlan", "duplex", "speed", "description", "type"),
        ),
    ),
    (
        re.compile(r"\bfan$"),
        ContractProposal(
            "fan", "fan", ("slot", "fan"), ("slot", "fan", "state"),
            ("speed", "direction", "model"),
        ),
    ),
    (
        re.compile(r"\bpower$"),
        ContractProposal(
            "power", "power_supply", ("slot", "power_supply"),
            ("slot", "power_supply", "state"),
            ("model", "output_watts", "input_watts", "capacity_watts"),
        ),
    ),
    (
        re.compile(r"\b(?:environment|environment temperature|env all)$"),
        ContractProposal(
            "environment", "environment_sensor", ("component", "location"),
            ("component", "location", "state"), ("reading", "unit"),
        ),
    ),
    (
        re.compile(r"\bvrrp (?:statistics|brief)$"),
        ContractProposal(
            "vrrp", "vrrp", ("interface", "group"),
            ("interface", "group", "state"),
            ("virtual_ip", "master_ip", "priority"),
            allow_empty=True,
            empty_patterns=(r"(?i)(?:VRRP.*not configured|no vrrp)",),
        ),
    ),
    (
        re.compile(r"\b(?:irf|m-lag system|vpc)$"),
        ContractProposal(
            "redundancy", "redundancy_member", ("member",),
            ("member", "role", "state"), ("peer", "priority", "description"),
            allow_empty=True,
            empty_patterns=(r"(?i)not configured",),
        ),
    ),
)


def _extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise ValueError("contract inference did not return a JSON object")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("contract inference JSON must be an object")
    return value


def infer_contract(
    *,
    command: str,
    samples_text: str,
    llm_call: Callable[[str], str] | None = None,
) -> ContractProposal:
    normalized = " ".join(command.lower().split())
    for pattern, proposal in SEMANTIC_RULES:
        if pattern.search(normalized):
            return proposal
    if llm_call is None:
        raise ValueError(f"no deterministic contract rule for command: {command}")

    prompt = f"""Infer a stable data contract for network CLI output.
Canonical command: {command}
Samples:
{samples_text}

Return JSON only with:
category, entity_type, primary_keys, required_fields, optional_fields,
validators, allow_empty, empty_patterns, ignore_fields.
Primary keys must identify records within one device and command snapshot.
Do not use metrics, status, timestamps, uptime, counters, or descriptions as keys.
Use an empty primary_keys list only when every sample contains at most one entity.
"""
    raw = _extract_json(llm_call(prompt))
    required = tuple(str(item) for item in raw.get("required_fields") or ())
    primary_keys = tuple(str(item) for item in raw.get("primary_keys") or ())
    if not required:
        raise ValueError("inferred contract has no required_fields")
    declared_fields = {*required, *(raw.get("optional_fields") or [])}
    if any(key not in declared_fields for key in primary_keys):
        raise ValueError("inferred primary key is not a declared field")
    return ContractProposal(
        category=str(raw.get("category") or "record"),
        entity_type=str(raw.get("entity_type") or raw.get("category") or "record"),
        primary_keys=primary_keys,
        required_fields=required,
        optional_fields=tuple(str(item) for item in raw.get("optional_fields") or ()),
        validators=dict(raw.get("validators") or {}),
        allow_empty=bool(raw.get("allow_empty", False)),
        empty_patterns=tuple(str(item) for item in raw.get("empty_patterns") or ()),
        ignore_fields=tuple(str(item) for item in raw.get("ignore_fields") or ()),
        source="llm_proposal",
        confidence=0.7,
    )

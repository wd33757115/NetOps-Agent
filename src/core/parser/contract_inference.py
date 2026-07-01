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
    state_semantics: dict[str, Any] | None = None
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
        data = asdict(self)
        data["state_semantics"] = self.state_semantics or infer_field_semantics(self)
        return data


def infer_field_semantics(proposal: ContractProposal) -> dict[str, Any]:
    """Infer stable field-level semantics for draft ObservationContract metadata."""
    semantics: dict[str, Any] = {}
    key_fields = {field.lower() for field in proposal.primary_keys}
    ignore_fields = {field.lower() for field in proposal.ignore_fields}
    fields = [*proposal.required_fields, *proposal.optional_fields]
    for field in fields:
        name = str(field)
        lowered = name.lower()
        if lowered in key_fields:
            semantics[name] = {"semantic_type": "identity"}
        elif lowered in ignore_fields or lowered in {"uptime", "description"}:
            semantics[name] = {"semantic_type": "text", "ignore": True}
        elif lowered in {"status", "state", "protocol", "oper_status", "line_protocol"}:
            semantics[name] = {
                "semantic_type": "state",
                "value_mapping": {
                    "up": "up",
                    "connected": "up",
                    "established": "up",
                    "normal": "normal",
                    "ok": "normal",
                    "down": "down",
                    "notconnect": "down",
                    "notconnec": "down",
                    "idle": "down",
                    "failed": "failed",
                    "fault": "failed",
                },
            }
        elif lowered.startswith("cpu") or lowered in {
            "usage",
            "used_percent",
            "memory_usage",
            "mem_usage",
            "free_ratio",
        }:
            semantics[name] = {"semantic_type": "gauge", "unit": "percent"}
        elif lowered in {
            "total",
            "used",
            "free",
            "shared",
            "buffers",
            "cached",
            "reading",
            "temperature",
            "warning_threshold",
            "alarm_threshold",
            "lower_threshold",
            "minor_threshold",
            "shutdown_threshold",
            "speed",
            "priority",
        }:
            semantics[name] = {"semantic_type": "gauge"}
        elif lowered.endswith("count") or lowered.endswith("_total"):
            semantics[name] = {"semantic_type": "counter"}
        elif "ip" in lowered:
            semantics[name] = {"semantic_type": "text", "unit": "ip"}
        else:
            semantics[name] = {"semantic_type": "text"}
    return semantics


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
            "inventory", "inventory_component", ("component",),
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
            validators={
                "interface": {"type": "interface_name"},
                "ip_address": {"type": "ip_or_unassigned"},
            },
            allow_empty=True,
            empty_patterns=(r"(?is)Interface\s+IP Address.*Status\s*$",),
        ),
    ),
    (
        re.compile(r"\binterface status$"),
        ContractProposal(
            "interface_l2", "interface_l2", ("interface",),
            ("interface", "status"), ("vlan", "duplex", "speed", "description", "type"),
            validators={"interface": {"type": "interface_name"}},
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
            ("component", "location", "state"),
            ("reading", "unit", "warning_threshold", "minor_threshold"),
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
    if re.search(r"\b(?:environment|env) fan$", normalized):
        return ContractProposal(
            "fan",
            "fan",
            ("fan",),
            ("fan", "state"),
            ("slot", "speed", "direction", "model"),
        )
    if re.search(r"\b(?:environment|env) power$", normalized):
        return ContractProposal(
            "power",
            "power_supply",
            ("power_supply",),
            ("power_supply", "state"),
            ("slot", "model", "output_watts", "input_watts", "capacity_watts"),
        )
    if normalized == "display device manuinfo":
        return ContractProposal(
            "inventory",
            "inventory_component",
            ("component", "serial"),
            ("component", "name", "serial"),
            ("pid", "vendor", "mac_address", "manufacturing_date"),
        )
    if normalized == "display memory":
        return ContractProposal(
            "memory",
            "memory",
            ("slot",),
            ("slot", "total", "used", "free"),
            ("shared", "buffers", "cached", "free_ratio"),
            validators={"free_ratio": {"type": "percent"}},
        )
    if normalized == "display power":
        if re.search(r"(?mi)^\s*Slot\s+\S+\s*:", samples_text or ""):
            return ContractProposal(
                "power",
                "power_supply",
                ("slot", "power_supply"),
                ("slot", "power_supply", "state"),
                ("mode", "current", "voltage", "power", "fan_direction", "model"),
            )
        return ContractProposal(
            "power",
            "power_supply",
            ("power_supply",),
            ("power_supply", "state"),
            ("slot", "mode", "current", "voltage", "power", "fan_direction", "model"),
        )
    if normalized == "display environment":
        if re.search(r"(?mi)^\s*Slot\s+Subslot\s+Sensor\s+ID\s+Temperature", samples_text or ""):
            return ContractProposal(
                "environment",
                "environment_sensor",
                ("component", "subslot", "location", "sensor_id"),
                ("component", "subslot", "location", "sensor_id", "reading"),
                ("lower_threshold", "warning_threshold", "alarm_threshold", "state", "unit"),
            )
        return ContractProposal(
            "environment",
            "environment_sensor",
            ("component", "location"),
            ("component", "location", "reading"),
            ("lower_threshold", "warning_threshold", "alarm_threshold", "shutdown_threshold", "state", "unit"),
        )
    if normalized == "display irf":
        return ContractProposal(
            "redundancy",
            "redundancy_member",
            ("member",),
            ("member", "role"),
            ("state", "priority", "cpu_mac", "description"),
            allow_empty=True,
            empty_patterns=(r"(?i)(?:not configured|incomplete command|unrecognized command)",),
        )
    if normalized == "display m-lag system":
        return ContractProposal(
            "redundancy",
            "redundancy_member",
            ("member",),
            ("member", "state"),
            ("role", "peer", "priority", "description"),
            allow_empty=True,
            empty_patterns=(r"(?i)(?:m-lag is not configured|not configured)",),
        )
    if re.search(r"\bsystem resources$", normalized):
        return ContractProposal(
            "system_resources",
            "system_resource",
            (),
            ("cpu_idle", "total", "used", "free"),
            (
                "cpu_user",
                "cpu_kernel",
                "load_1m",
                "load_5m",
                "load_15m",
                "process_total",
                "process_running",
                "memory_status",
            ),
            validators={
                "cpu_user": {"type": "percent"},
                "cpu_kernel": {"type": "percent"},
                "cpu_idle": {"type": "percent"},
            },
        )
    if re.search(r"\bvpc$", normalized):
        return ContractProposal(
            "redundancy",
            "redundancy_member",
            ("member", "peer"),
            ("member", "peer", "state"),
            ("role", "consistency", "reason", "priority", "description"),
            allow_empty=True,
            empty_patterns=(r"(?i)(?:not configured|no vpc)",),
        )
    if re.search(r"\bfan$", normalized):
        if re.search(
            r"(?mi)^\s*Index\s+Status\s+Speedlevel\b", samples_text or ""
        ) and not re.search(r"(?mi)^\s*Slot\s+\S+\s*:", samples_text or ""):
            return ContractProposal(
                "fan",
                "fan",
                ("fan",),
                ("fan", "state"),
                ("speed", "slot", "direction", "model"),
            )
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

"""Versioned event rules for turning changes into operational events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
import uuid

from src.infrastructure.db.models import EventRuleRecord, EventRuleVersionRecord
from src.infrastructure.db.postgres import get_db_session

def norm(value: Any) -> str:
    return str(value or "").strip().lower()


def norm_field(value: Any) -> str:
    return norm(value).replace("-", "_")


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("%", "")
    try:
        return float(text)
    except ValueError:
        return None


def is_down(value: Any) -> bool:
    return norm(value) in {
        "down",
        "administratively down",
        "admin-down",
        "notconnec",
        "notconnect",
        "notconnected",
        "xcvrabsen",
        "sfpabsent",
        "inactive",
        "failed",
        "fault",
    }


def is_up(value: Any) -> bool:
    return norm(value) in {
        "up",
        "connected",
        "active",
        "established",
        "normal",
        "ok",
    }


def is_bgp_change(change: dict[str, Any]) -> bool:
    entity_type = norm(change.get("entity_type"))
    command = norm(change.get("command"))
    return "bgp" in entity_type or "bgp" in command


@dataclass(frozen=True)
class EventRule:
    rule_id: str
    version: int
    event_type: str
    severity: str
    predicate: Callable[[dict[str, Any]], bool]

    def match(self, change: dict[str, Any]) -> bool:
        return self.predicate(change)


def condition_matches(condition: dict[str, Any], change: dict[str, Any]) -> bool:
    kind = str(condition.get("kind") or "").strip()
    if kind == "threshold":
        fields = {norm_field(item) for item in condition.get("fields") or []}
        threshold = float(condition.get("gte"))
        old_value = to_float(change.get("old"))
        new_value = to_float(change.get("new"))
        return norm_field(change.get("field")) in fields and (
            new_value is not None
        ) and float(new_value or 0) >= threshold and (
            change.get("change_type") == "initial"
            or old_value is None
            or float(old_value) < threshold
        )
    if kind == "threshold_recovery":
        fields = {norm_field(item) for item in condition.get("fields") or []}
        threshold = float(condition.get("lt"))
        old_value = to_float(change.get("old"))
        new_value = to_float(change.get("new"))
        return (
            norm_field(change.get("field")) in fields
            and old_value is not None
            and new_value is not None
            and float(old_value) >= threshold
            and float(new_value) < threshold
        )
    if kind == "transition":
        fields = {norm_field(item) for item in condition.get("fields") or []}
        old_group = _state_group(change.get("old"))
        if change.get("change_type") == "initial":
            old_group = str(condition.get("from_group"))
        return (
            norm_field(change.get("field")) in fields
            and old_group == str(condition.get("from_group"))
            and _state_group(change.get("new")) == str(condition.get("to_group"))
        )
    if kind == "bgp_transition":
        fields = {norm_field(item) for item in condition.get("fields") or []}
        from_established = bool(condition.get("from_established"))
        to_established = bool(condition.get("to_established"))
        old_established = norm(change.get("old")) == "established"
        if change.get("change_type") == "initial":
            old_established = from_established
        return (
            is_bgp_change(change)
            and norm_field(change.get("field")) in fields
            and old_established is from_established
            and (norm(change.get("new")) == "established") is to_established
        )
    if kind == "raw_config_hash":
        return (
            change.get("entity_type") == "raw_command_output"
            and change.get("field") == "raw_text_hash"
            and any(
                marker in norm(change.get("command"))
                for marker in condition.get("command_markers") or []
            )
        )
    if kind == "entity_status_down":
        entity_types = {norm(item) for item in condition.get("entity_types") or []}
        fields = {norm_field(item) for item in condition.get("fields") or []}
        return (
            norm(change.get("entity_type")) in entity_types
            and norm_field(change.get("field")) in fields
            and is_down(change.get("new"))
        )
    return False


def _state_group(value: Any) -> str:
    if is_up(value):
        return "up"
    if is_down(value):
        return "down"
    return "other"


class EventRuleRepository:
    def list_versions(self) -> list[dict[str, Any]]:
        with get_db_session() as db:
            rows = (
                db.query(EventRuleRecord, EventRuleVersionRecord)
                .join(
                    EventRuleVersionRecord,
                    EventRuleVersionRecord.rule_id == EventRuleRecord.id,
                )
                .order_by(EventRuleRecord.name, EventRuleVersionRecord.version.desc())
                .all()
            )
            return [
                {
                    **rule.to_dict(),
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "version_id": version.id,
                    "version": version.version,
                    "event_type": version.event_type,
                    "severity": version.severity,
                    "condition": version.condition_json,
                    "version_status": version.status,
                    "activated_at": version.activated_at,
                    "deprecated_at": version.deprecated_at,
                }
                for rule, version in rows
            ]

    def list_active_rules(self) -> list[EventRule]:
        with get_db_session() as db:
            rows = (
                db.query(EventRuleRecord, EventRuleVersionRecord)
                .join(
                    EventRuleVersionRecord,
                    EventRuleVersionRecord.rule_id == EventRuleRecord.id,
                )
                .filter(EventRuleRecord.status == "active")
                .filter(EventRuleVersionRecord.status == "active")
                .order_by(EventRuleRecord.name)
                .all()
            )
            return [
                EventRule(
                    rule_id=rule.name,
                    version=int(version.version),
                    event_type=version.event_type,
                    severity=version.severity,
                    predicate=lambda change, condition=dict(version.condition_json or {}): condition_matches(
                        condition,
                        change,
                    ),
                )
                for rule, version in rows
            ]

    def ensure_default_rules(self) -> int:
        created = 0
        with get_db_session() as db:
            for rule in default_event_rules():
                row = (
                    db.query(EventRuleRecord)
                    .filter(EventRuleRecord.name == rule.rule_id)
                    .first()
                )
                if row is None:
                    row = EventRuleRecord(
                        id=str(uuid.uuid4()),
                        name=rule.rule_id,
                        description=f"Built-in rule for {rule.event_type}",
                        status="active",
                        current_version=rule.version,
                    )
                    db.add(row)
                    db.flush()
                    created += 1
                if (
                    db.query(EventRuleVersionRecord)
                    .filter(
                        EventRuleVersionRecord.rule_id == row.id,
                        EventRuleVersionRecord.version == rule.version,
                    )
                    .first()
                    is None
                ):
                    db.add(
                        EventRuleVersionRecord(
                            id=str(uuid.uuid4()),
                            rule_id=row.id,
                            version=rule.version,
                            event_type=rule.event_type,
                            severity=rule.severity,
                            condition_json=_default_condition(rule.rule_id),
                            status="active",
                        )
                    )
                    row.current_version = rule.version
                    created += 1
        return created

    def upsert_active_rule(
        self,
        *,
        name: str,
        event_type: str,
        severity: str,
        condition: dict[str, Any],
        description: str | None = None,
        created_by: str | None = None,
    ) -> str:
        with get_db_session() as db:
            rule = (
                db.query(EventRuleRecord)
                .filter(EventRuleRecord.name == name)
                .first()
            )
            if rule is None:
                rule = EventRuleRecord(
                    id=str(uuid.uuid4()),
                    name=name,
                    description=description,
                    status="active",
                    current_version=0,
                )
                db.add(rule)
                db.flush()
            rule.status = "active"
            rule.description = description or rule.description
            latest_version = (
                db.query(EventRuleVersionRecord)
                .filter(EventRuleVersionRecord.rule_id == rule.id)
                .order_by(EventRuleVersionRecord.version.desc())
                .first()
            )
            next_version = int(latest_version.version) + 1 if latest_version else 1
            db.query(EventRuleVersionRecord).filter(
                EventRuleVersionRecord.rule_id == rule.id,
                EventRuleVersionRecord.status == "active",
            ).update({"status": "deprecated"})
            version = EventRuleVersionRecord(
                id=str(uuid.uuid4()),
                rule_id=rule.id,
                version=next_version,
                event_type=event_type,
                severity=severity,
                condition_json=condition,
                status="active",
                created_by=created_by,
                activated_at=datetime.now(timezone.utc),
            )
            db.add(version)
            rule.current_version = next_version
            return version.id

    def create_rule_version(
        self,
        *,
        name: str,
        event_type: str,
        severity: str,
        condition: dict[str, Any],
        description: str | None = None,
        created_by: str | None = None,
        status: str = "draft",
    ) -> str:
        if status not in {"draft", "pending_approval"}:
            raise ValueError("event rule version status must be draft or pending_approval")
        with get_db_session() as db:
            rule = (
                db.query(EventRuleRecord)
                .filter(EventRuleRecord.name == name)
                .first()
            )
            if rule is None:
                rule = EventRuleRecord(
                    id=str(uuid.uuid4()),
                    name=name,
                    description=description,
                    status="draft",
                    current_version=0,
                )
                db.add(rule)
                db.flush()
            rule.description = description or rule.description
            latest_version = (
                db.query(EventRuleVersionRecord)
                .filter(EventRuleVersionRecord.rule_id == rule.id)
                .order_by(EventRuleVersionRecord.version.desc())
                .first()
            )
            next_version = int(latest_version.version) + 1 if latest_version else 1
            version = EventRuleVersionRecord(
                id=str(uuid.uuid4()),
                rule_id=rule.id,
                version=next_version,
                event_type=event_type,
                severity=severity,
                condition_json=condition,
                status=status,
                created_by=created_by,
            )
            db.add(version)
            return version.id

    def activate_rule_version(self, version_id: str) -> dict[str, Any]:
        with get_db_session() as db:
            version = (
                db.query(EventRuleVersionRecord)
                .filter(EventRuleVersionRecord.id == version_id)
                .first()
            )
            if version is None:
                raise ValueError("event rule version not found")
            rule = (
                db.query(EventRuleRecord)
                .filter(EventRuleRecord.id == version.rule_id)
                .first()
            )
            if rule is None:
                raise ValueError("event rule not found")
            now = datetime.now(timezone.utc)
            (
                db.query(EventRuleVersionRecord)
                .filter(EventRuleVersionRecord.rule_id == rule.id)
                .filter(EventRuleVersionRecord.status == "active")
                .filter(EventRuleVersionRecord.id != version.id)
                .update({"status": "deprecated", "deprecated_at": now})
            )
            version.status = "active"
            version.activated_at = now
            version.deprecated_at = None
            rule.status = "active"
            rule.current_version = int(version.version)
            db.flush()
            return {
                **rule.to_dict(),
                "rule_id": rule.id,
                "rule_name": rule.name,
                "version_id": version.id,
                "version": version.version,
                "event_type": version.event_type,
                "severity": version.severity,
                "condition": version.condition_json,
                "version_status": version.status,
                "activated_at": version.activated_at,
                "deprecated_at": version.deprecated_at,
            }

    def deprecate_rule_version(self, version_id: str) -> dict[str, Any]:
        with get_db_session() as db:
            version = (
                db.query(EventRuleVersionRecord)
                .filter(EventRuleVersionRecord.id == version_id)
                .first()
            )
            if version is None:
                raise ValueError("event rule version not found")
            rule = (
                db.query(EventRuleRecord)
                .filter(EventRuleRecord.id == version.rule_id)
                .first()
            )
            if rule is None:
                raise ValueError("event rule not found")
            version.status = "deprecated"
            version.deprecated_at = datetime.now(timezone.utc)
            if int(rule.current_version or 0) == int(version.version):
                active = (
                    db.query(EventRuleVersionRecord)
                    .filter(EventRuleVersionRecord.rule_id == rule.id)
                    .filter(EventRuleVersionRecord.status == "active")
                    .first()
                )
                rule.current_version = int(active.version) if active else 0
                rule.status = "active" if active else "deprecated"
            db.flush()
            return {
                **rule.to_dict(),
                "rule_id": rule.id,
                "rule_name": rule.name,
                "version_id": version.id,
                "version": version.version,
                "event_type": version.event_type,
                "severity": version.severity,
                "condition": version.condition_json,
                "version_status": version.status,
                "activated_at": version.activated_at,
                "deprecated_at": version.deprecated_at,
            }


def _field_in(change: dict[str, Any], names: set[str]) -> bool:
    return norm_field(change.get("field")) in names


def default_event_rules() -> list[EventRule]:
    return [
        EventRule(
            rule_id="cpu-high",
            version=1,
            event_type="CPUHigh",
            severity="warning",
            predicate=lambda c: (
                norm_field(c.get("field"))
                in {"cpu", "cpu_5s", "cpu_1m", "cpu_5m", "cpu_usage"}
                and (to_float(c.get("new")) is not None)
                and float(to_float(c.get("new")) or 0) >= 90
                and (
                    c.get("change_type") == "initial"
                    or to_float(c.get("old")) is None
                    or float(to_float(c.get("old")) or 0) < 90
                )
            ),
        ),
        EventRule(
            rule_id="cpu-recovered",
            version=1,
            event_type="CPURecovered",
            severity="info",
            predicate=lambda c: norm_field(c.get("field"))
            in {"cpu", "cpu_5s", "cpu_1m", "cpu_5m", "cpu_usage"}
            and (to_float(c.get("old")) is not None)
            and (to_float(c.get("new")) is not None)
            and float(to_float(c.get("old")) or 0) >= 90
            and float(to_float(c.get("new")) or 0) < 90,
        ),
        EventRule(
            rule_id="memory-high",
            version=1,
            event_type="MemoryHigh",
            severity="warning",
            predicate=lambda c: (
                norm_field(c.get("field"))
                in {
                    "memory",
                    "memory_usage",
                    "mem_usage",
                    "usage",
                    "used_percent",
                    "memory_used_percent",
                }
                and norm(c.get("entity_type")) in {"memory", "system_resources"}
                and (to_float(c.get("new")) is not None)
                and float(to_float(c.get("new")) or 0) >= 90
                and (
                    c.get("change_type") == "initial"
                    or to_float(c.get("old")) is None
                    or float(to_float(c.get("old")) or 0) < 90
                )
            ),
        ),
        EventRule(
            rule_id="memory-recovered",
            version=1,
            event_type="MemoryRecovered",
            severity="info",
            predicate=lambda c: norm_field(c.get("field"))
            in {
                "memory",
                "memory_usage",
                "mem_usage",
                "usage",
                "used_percent",
                "memory_used_percent",
            }
            and norm(c.get("entity_type")) in {"memory", "system_resources"}
            and (to_float(c.get("old")) is not None)
            and (to_float(c.get("new")) is not None)
            and float(to_float(c.get("old")) or 0) >= 90
            and float(to_float(c.get("new")) or 0) < 90,
        ),
        EventRule(
            rule_id="interface-down",
            version=1,
            event_type="InterfaceDown",
            severity="major",
            predicate=lambda c: _field_in(
                c,
                {
                    "status",
                    "protocol",
                    "physical",
                    "physical_status",
                    "oper_status",
                    "line_protocol",
                },
            )
            and (is_up(c.get("old")) or c.get("change_type") == "initial")
            and is_down(c.get("new")),
        ),
        EventRule(
            rule_id="interface-up",
            version=1,
            event_type="InterfaceUp",
            severity="info",
            predicate=lambda c: _field_in(
                c,
                {
                    "status",
                    "protocol",
                    "physical",
                    "physical_status",
                    "oper_status",
                    "line_protocol",
                },
            )
            and is_down(c.get("old"))
            and is_up(c.get("new")),
        ),
        EventRule(
            rule_id="bgp-neighbor-lost",
            version=1,
            event_type="BGPNeighborLost",
            severity="critical",
            predicate=lambda c: is_bgp_change(c)
            and norm_field(c.get("field"))
            in {"state", "bgp_state", "peer_state", "neighbor_state"}
            and (norm(c.get("old")) == "established" or c.get("change_type") == "initial")
            and norm(c.get("new")) != "established",
        ),
        EventRule(
            rule_id="bgp-neighbor-established",
            version=1,
            event_type="BGPNeighborEstablished",
            severity="info",
            predicate=lambda c: is_bgp_change(c)
            and norm_field(c.get("field"))
            in {"state", "bgp_state", "peer_state", "neighbor_state"}
            and norm(c.get("old")) != "established"
            and norm(c.get("new")) == "established",
        ),
        EventRule(
            rule_id="config-changed",
            version=1,
            event_type="ConfigChanged",
            severity="minor",
            predicate=lambda c: c.get("entity_type") == "raw_command_output"
            and c.get("field") == "raw_text_hash"
            and any(
                marker in norm(c.get("command"))
                for marker in (
                    "configuration",
                    "running-config",
                    "current-configuration",
                )
            ),
        ),
        EventRule(
            rule_id="fan-failed",
            version=1,
            event_type="FanFailed",
            severity="major",
            predicate=lambda c: norm(c.get("entity_type")) in {"fan", "environment_fan"}
            and _field_in(c, {"state", "status"})
            and is_down(c.get("new")),
        ),
        EventRule(
            rule_id="power-failed",
            version=1,
            event_type="PowerFailed",
            severity="major",
            predicate=lambda c: norm(c.get("entity_type"))
            in {"power", "power_supply", "environment_power"}
            and _field_in(c, {"state", "status"})
            and is_down(c.get("new")),
        ),
    ]


def match_event_rule(change: dict[str, Any]) -> EventRule | None:
    rules = load_event_rules()
    for rule in rules:
        if rule.match(change):
            return rule
    return None


def load_event_rules() -> list[EventRule]:
    try:
        rules = EventRuleRepository().list_active_rules()
        if not rules:
            return default_event_rules()
        existing = {rule.rule_id for rule in rules}
        missing_defaults = [
            rule for rule in default_event_rules() if rule.rule_id not in existing
        ]
        return rules + missing_defaults
    except Exception:
        return default_event_rules()


def _default_condition(rule_id: str) -> dict[str, Any]:
    if rule_id == "cpu-high":
        return {
            "kind": "threshold",
            "fields": ["cpu", "cpu_5s", "cpu_1m", "cpu_5m", "cpu_usage"],
            "gte": 90,
        }
    if rule_id == "memory-high":
        return {
            "kind": "threshold",
            "fields": [
                "memory",
                "memory_usage",
                "mem_usage",
                "usage",
                "used_percent",
                "memory_used_percent",
            ],
            "gte": 90,
        }
    if rule_id == "cpu-recovered":
        return {
            "kind": "threshold_recovery",
            "fields": ["cpu", "cpu_5s", "cpu_1m", "cpu_5m", "cpu_usage"],
            "lt": 90,
        }
    if rule_id == "memory-recovered":
        return {
            "kind": "threshold_recovery",
            "fields": [
                "memory",
                "memory_usage",
                "mem_usage",
                "usage",
                "used_percent",
                "memory_used_percent",
            ],
            "lt": 90,
        }
    if rule_id == "interface-down":
        return {
            "kind": "transition",
            "fields": [
                "status",
                "protocol",
                "physical",
                "physical_status",
                "oper_status",
                "line_protocol",
            ],
            "from_group": "up",
            "to_group": "down",
        }
    if rule_id == "interface-up":
        return {
            "kind": "transition",
            "fields": [
                "status",
                "protocol",
                "physical",
                "physical_status",
                "oper_status",
                "line_protocol",
            ],
            "from_group": "down",
            "to_group": "up",
        }
    if rule_id == "bgp-neighbor-lost":
        return {
            "kind": "bgp_transition",
            "fields": ["state", "bgp_state", "peer_state", "neighbor_state"],
            "from_established": True,
            "to_established": False,
        }
    if rule_id == "bgp-neighbor-established":
        return {
            "kind": "bgp_transition",
            "fields": ["state", "bgp_state", "peer_state", "neighbor_state"],
            "from_established": False,
            "to_established": True,
        }
    if rule_id == "config-changed":
        return {
            "kind": "raw_config_hash",
            "command_markers": [
                "configuration",
                "running-config",
                "current-configuration",
            ],
        }
    if rule_id == "fan-failed":
        return {
            "kind": "entity_status_down",
            "entity_types": ["fan", "environment_fan"],
            "fields": ["state", "status"],
        }
    if rule_id == "power-failed":
        return {
            "kind": "entity_status_down",
            "entity_types": ["power", "power_supply", "environment_power"],
            "fields": ["state", "status"],
        }
    return {"kind": "unknown"}

"""Versioned event rules for turning changes into operational events."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_

from src.core.parser.policy import load_yaml
from src.core.patrol.change_policy import dimension_selector_matches
from src.core.state.observation_applicability import (
    condition_applicability_matches,
    context_from_change,
)
from src.infrastructure.db.models import (
    AuditLogRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
)
from src.infrastructure.db.postgres import get_db_session

SHARED_RULE_PACK_ROOT = Path(__file__).resolve().parents[2] / "skills" / "shared" / "rule-packs"
BUILTIN_EVENT_RULE_PACK = SHARED_RULE_PACK_ROOT / "event_rules.builtin.yaml"


def norm(value: Any) -> str:
    return str(value or "").strip().lower()


def norm_value(value: Any) -> str:
    """Normalize a value for comparison with the values a rule lists.

    Unlike ``norm``, False and 0 are values here, not blanks: a boolean field
    whose abnormal value is False has to be able to match it.
    """

    return "" if value is None else str(value).strip().lower()


def norm_field(value: Any) -> str:
    return norm(value).replace("-", "_")


def event_rule_digest(
    *,
    name: str,
    event_type: str,
    severity: str,
    condition: dict[str, Any],
    governance: dict[str, Any] | None = None,
) -> str:
    payload = {
        "name": name,
        "event_type": event_type,
        "severity": severity,
        "condition": condition,
        "governance": governance or {},
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


EVALUATION_SCOPES = frozenset({"current_state", "change", "both"})


def govern_evaluation_scope(value: Any, *, event_class: str = "fault") -> str:
    """Return a supported evaluation scope with a safe legacy default."""

    scope = str(value or "").strip().casefold()
    if not scope:
        return "change" if str(event_class or "fault").casefold() == "recovery" else "both"
    if scope not in EVALUATION_SCOPES:
        raise ValueError(f"unsupported event rule evaluation_scope: {scope}")
    if str(event_class or "fault").casefold() == "recovery" and scope == "current_state":
        raise ValueError("recovery rules cannot use current_state-only evaluation")
    return scope


_KNOWN_EVENT_RULE_ROLES = {
    "threshold": "high",
    "threshold_field_gte": "high",
    "threshold_recovery": "high_recovery",
    "threshold_field_high_recovery": "high_recovery",
    "threshold_lte": "low",
    "threshold_field_lte": "low",
    "threshold_low_recovery": "low_recovery",
    "threshold_field_low_recovery": "low_recovery",
    "entity_status_down": "down",
    "raw_config_hash": "raw_config_hash",
}


def _logical_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")


def _logical_namespace(name: str, event_type: str) -> str:
    """Return the platform-owned namespace while preserving legacy compatibility."""

    candidate = str(name or "").strip()
    if "." in candidate:
        candidate = candidate.split(".", 1)[0]
    return _logical_token(candidate or event_type) or "unnamespaced"


def _logical_role(
    *,
    name: str,
    event_type: str,
    condition: dict[str, Any],
    event_class: str,
) -> str:
    kind = str(condition.get("kind") or "").strip().casefold()
    role = _KNOWN_EVENT_RULE_ROLES.get(kind)
    threshold_role = norm_field(condition.get("threshold_role"))
    # Dynamic-threshold rules carry their alert tier in ``threshold_role``.
    # The generated event name is presentation text and may only contain
    # "Critical", so it cannot reliably distinguish warning from alarm.
    if role in {"high", "low"} and threshold_role in {
        "high",
        "critical_high",
        "low",
        "critical_low",
    }:
        return threshold_role
    if kind == "transition":
        role = _logical_token(condition.get("to_group")) or "transition"
    elif kind == "bgp_transition":
        role = "up" if bool(condition.get("to_established")) else "down"

    marker = "_".join(_logical_token(value) for value in (name, event_type, event_class))
    if role == "low" and any(
        token in marker for token in ("critical-low", "loss-of-light", "signal-loss")
    ):
        return "critical_low"
    if role == "high" and "critical-high" in marker:
        return "critical_high"
    if role:
        return role
    return f"custom-{_logical_token(kind) or 'condition'}"


def _canonical_logical_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _canonical_logical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set)):
        normalized = [_canonical_logical_value(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(item, sort_keys=True, default=str),
        )
    if isinstance(value, str):
        return value.strip().casefold()
    return value


def event_rule_logical_key(
    *,
    name: str,
    event_type: str,
    condition: dict[str, Any],
    event_class: str = "fault",
) -> str:
    """Identify a semantic EventRule slot independently of LLM wording and thresholds.

    Known platform condition kinds intentionally exclude concrete threshold values and
    reference-field choices. Those are versioned policy decisions. Entity/field scope,
    applicability guards, namespace and trigger role remain part of the identity so two
    genuinely different rules cannot silently replace each other. Unknown condition
    kinds use their complete condition as a conservative fallback.
    """

    normalized_condition = dict(condition or {})
    role = _logical_role(
        name=name,
        event_type=event_type,
        condition=normalized_condition,
        event_class=event_class,
    )
    scope: dict[str, Any] = {
        "entity_types": sorted(
            {norm(item) for item in normalized_condition.get("entity_types") or [] if norm(item)}
        ),
        "fields": sorted(
            {
                norm_field(item)
                for item in normalized_condition.get("fields") or []
                if norm_field(item)
            }
        ),
        "applicability": _canonical_logical_value(normalized_condition.get("applicability") or {}),
        "dimensions": _canonical_logical_value(normalized_condition.get("dimensions") or {}),
    }
    if role == "raw_config_hash":
        scope["command_markers"] = sorted(
            {norm(item) for item in normalized_condition.get("command_markers") or [] if norm(item)}
        )
    if role.startswith("custom-"):
        scope["condition"] = _canonical_logical_value(normalized_condition)

    namespace = _logical_namespace(name, event_type)
    payload = {
        "namespace": namespace,
        "role": role,
        "event_class": norm(event_class) or "fault",
        "scope": scope,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return f"event-rule:v1:{namespace}:{role}:{digest}"


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("%", "")
    try:
        return float(text)
    except ValueError:
        return None


def resolve_threshold_reference(
    condition: dict[str, Any], context: dict[str, Any] | None
) -> tuple[str | None, float | None]:
    """Resolve the first available approved per-observation threshold field."""

    fields = [
        str(condition.get("reference_field") or ""),
        *(str(item) for item in condition.get("fallback_reference_fields") or []),
    ]
    values = dict(context or {})
    for field_name in fields:
        if not field_name:
            continue
        value = to_float(values.get(field_name))
        if value is not None:
            return field_name, value
    return None, None


def is_down(value: Any) -> bool:
    return norm(value) in {
        "down",
        "administratively down",
        "admin-down",
        "disabled",
        "disable",
        "shutdown",
        "err-disabled",
        "errdisabled",
        "notconnec",
        "notconnect",
        "notconnected",
        "xcvrabsen",
        "sfpabsent",
        "inactive",
        "failed",
        "fault",
        "absent",
    }


def is_suppressed_interface_state(value: Any) -> bool:
    return norm(value) in {
        "administratively down",
        "admin down",
        "admin-down",
        "disabled",
        "disable",
        "shutdown",
        "xcvrabsen",
        "xcvrabsent",
        "sfpabsent",
        "transceiver absent",
        "transceiver is not present",
        "notpresent",
        "not present",
    }


def is_up(value: Any) -> bool:
    return norm(value) in {
        "up",
        "connected",
        "active",
        "established",
        "normal",
        "ok",
        "present",
    }


def is_bgp_change(change: dict[str, Any]) -> bool:
    entity_type = norm(change.get("entity_type"))
    command = norm(change.get("command"))
    return "bgp" in entity_type or "bgp" in command


REQUIRED_DEVICE_EVENT_CORRELATION_KEYS = (
    "device_id",
    "entity_type",
    "entity_key",
)


def govern_event_correlation_keys(
    correlation_keys: list[str] | tuple[str, ...] | None,
) -> tuple[str, ...]:
    """Keep generated event identities isolated by device and entity."""

    proposed = [str(item).strip() for item in correlation_keys or () if str(item).strip()]
    governed: list[str] = []
    for key in (*REQUIRED_DEVICE_EVENT_CORRELATION_KEYS, *proposed):
        if key not in governed:
            governed.append(key)
    return tuple(governed)


@dataclass(frozen=True)
class EventRule:
    rule_id: str
    version: int
    event_type: str
    severity: str
    predicate: Callable[[dict[str, Any]], bool]
    condition: dict[str, Any] | None = None
    priority: int = 100
    exclusive: bool = False
    event_class: str = "fault"
    evaluation_scope: str = "both"
    recovery: dict[str, Any] | None = None
    correlation_keys: tuple[str, ...] = (
        "device_id",
        "entity_type",
        "entity_key",
        "field",
    )
    version_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evaluation_scope",
            govern_evaluation_scope(
                self.evaluation_scope,
                event_class=self.event_class,
            ),
        )
        object.__setattr__(
            self,
            "correlation_keys",
            govern_event_correlation_keys(self.correlation_keys),
        )

    def match(self, change: dict[str, Any]) -> bool:
        return self.predicate(change)


@dataclass(frozen=True)
class EventRuleDefinition:
    rule_id: str
    version: int
    event_type: str
    severity: str
    condition: dict[str, Any]
    description: str | None = None
    priority: int = 100
    exclusive: bool = False
    event_class: str = "fault"
    evaluation_scope: str = "both"
    recovery: dict[str, Any] | None = None
    correlation_keys: tuple[str, ...] = (
        "device_id",
        "entity_type",
        "entity_key",
        "field",
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evaluation_scope",
            govern_evaluation_scope(
                self.evaluation_scope,
                event_class=self.event_class,
            ),
        )
        object.__setattr__(
            self,
            "correlation_keys",
            govern_event_correlation_keys(self.correlation_keys),
        )

    def to_rule(self) -> EventRule:
        return EventRule(
            rule_id=self.rule_id,
            version=self.version,
            event_type=self.event_type,
            severity=self.severity,
            predicate=lambda change, condition=dict(self.condition): condition_matches(
                condition,
                change,
            ),
            condition=dict(self.condition),
            priority=self.priority,
            exclusive=self.exclusive,
            event_class=self.event_class,
            evaluation_scope=self.evaluation_scope,
            recovery=dict(self.recovery or {}),
            correlation_keys=self.correlation_keys,
        )


def _entity_type_allowed(condition: dict[str, Any], change: dict[str, Any]) -> bool:
    entity_types = {norm(item) for item in condition.get("entity_types") or []}
    return not entity_types or norm(change.get("entity_type")) in entity_types


def condition_matches(condition: dict[str, Any], change: dict[str, Any]) -> bool:
    if not condition_applicability_matches(condition, change):
        return False
    if not dimension_selector_matches(
        condition.get("dimensions"),
        change.get("dimensions") or change.get("dimensions_json"),
    ):
        return False
    kind = str(condition.get("kind") or "").strip()
    if kind == "threshold":
        if not _entity_type_allowed(condition, change):
            return False
        fields = {norm_field(item) for item in condition.get("fields") or []}
        threshold = to_float(condition.get("gte"))
        if threshold is None:
            return False
        old_value = to_float(change.get("old"))
        new_value = to_float(change.get("new"))
        return (
            norm_field(change.get("field")) in fields
            and (new_value is not None)
            and float(new_value or 0) >= threshold
            and (
                change.get("change_type") == "initial"
                or old_value is None
                or float(old_value) < threshold
            )
        )
    if kind == "threshold_recovery":
        if not _entity_type_allowed(condition, change):
            return False
        fields = {norm_field(item) for item in condition.get("fields") or []}
        threshold = to_float(
            condition.get("lte")
            if condition.get("lte") is not None
            else condition.get("lt")
        )
        if threshold is None:
            return False
        old_value = to_float(change.get("old"))
        new_value = to_float(change.get("new"))
        return (
            norm_field(change.get("field")) in fields
            and old_value is not None
            and new_value is not None
            and float(old_value) >= threshold
            and float(new_value) <= threshold
        )
    if kind == "threshold_lte":
        if not _entity_type_allowed(condition, change):
            return False
        fields = {norm_field(item) for item in condition.get("fields") or []}
        threshold = to_float(condition.get("lte"))
        if threshold is None:
            return False
        old_value = to_float(change.get("old"))
        new_value = to_float(change.get("new"))
        return (
            norm_field(change.get("field")) in fields
            and new_value is not None
            and float(new_value) <= threshold
            and (
                change.get("change_type") == "initial"
                or old_value is None
                or float(old_value) > threshold
            )
        )
    if kind == "threshold_low_recovery":
        if not _entity_type_allowed(condition, change):
            return False
        fields = {norm_field(item) for item in condition.get("fields") or []}
        low = to_float(condition.get("low"))
        if low is None:
            return False
        gt = to_float(condition.get("gt"))
        if gt is None:
            hysteresis = to_float(condition.get("hysteresis"))
            if hysteresis is None:
                return False
            gt = low + hysteresis
        old_value = to_float(change.get("old"))
        new_value = to_float(change.get("new"))
        return (
            norm_field(change.get("field")) in fields
            and old_value is not None
            and new_value is not None
            and float(old_value) <= low
            and float(new_value) >= gt
        )
    if kind in {
        "threshold_field_gte",
        "threshold_field_high_recovery",
        "threshold_field_lte",
        "threshold_field_low_recovery",
    }:
        if not _entity_type_allowed(condition, change):
            return False
        fields = {norm_field(item) for item in condition.get("fields") or []}
        if norm_field(change.get("field")) not in fields:
            return False
        context = context_from_change(change)
        _reference_field, reference = resolve_threshold_reference(condition, context)
        old_value = to_float(change.get("old"))
        new_value = to_float(change.get("new"))
        if reference is None or new_value is None:
            return False
        if kind == "threshold_field_gte":
            return float(new_value) >= reference and (
                change.get("change_type") == "initial"
                or old_value is None
                or float(old_value) < reference
            )
        if kind == "threshold_field_lte":
            return float(new_value) <= reference and (
                change.get("change_type") == "initial"
                or old_value is None
                or float(old_value) > reference
            )
        hysteresis = to_float(condition.get("hysteresis"))
        if old_value is None or hysteresis is None:
            return False
        if kind == "threshold_field_high_recovery":
            return float(old_value) >= reference and float(new_value) <= (reference - hysteresis)
        return float(old_value) <= reference and float(new_value) >= (reference + hysteresis)
    if kind == "transition":
        fields = {norm_field(item) for item in condition.get("fields") or []}
        entity_types = {norm(item) for item in condition.get("entity_types") or []}
        if entity_types and norm(change.get("entity_type")) not in entity_types:
            return False
        from_values = {norm_value(item) for item in condition.get("from_values") or []}
        to_values = {norm_value(item) for item in condition.get("to_values") or []}
        new_group = _state_group(change.get("new"))
        new_matches = (
            norm_value(change.get("new")) in to_values
            if to_values
            else new_group == str(condition.get("to_group"))
        )
        if change.get("change_type") == "initial":
            if is_suppressed_interface_state(change.get("new")):
                return False
            return bool(condition.get("allow_initial_target")) and (
                norm_field(change.get("field")) in fields
                and new_matches
            )
        old_group = _state_group(change.get("old"))
        old_matches = (
            norm_value(change.get("old")) in from_values
            if from_values
            else old_group == str(condition.get("from_group"))
        )
        return (
            norm_field(change.get("field")) in fields
            and old_matches
            and new_matches
        )
    if kind == "bgp_transition":
        fields = {norm_field(item) for item in condition.get("fields") or []}
        from_established = bool(condition.get("from_established"))
        to_established = bool(condition.get("to_established"))
        if change.get("change_type") == "initial":
            return (
                bool(condition.get("allow_initial_target"))
                and is_bgp_change(change)
                and norm_field(change.get("field")) in fields
                and (norm(change.get("new")) == "established") is to_established
            )
        old_established = norm(change.get("old")) == "established"
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
    if kind == "field_change":
        if not _entity_type_allowed(condition, change):
            return False
        fields = {norm_field(item) for item in condition.get("fields") or []}
        if norm_field(change.get("field")) not in fields:
            return False
        if change.get("change_type") == "initial":
            return bool(condition.get("allow_initial"))
        return change.get("old") != change.get("new")
    if kind == "entity_status_down":
        entity_types = {norm(item) for item in condition.get("entity_types") or []}
        fields = {norm_field(item) for item in condition.get("fields") or []}
        explicit_values = {norm_value(item) for item in condition.get("values") or []}
        return (
            norm(change.get("entity_type")) in entity_types
            and norm_field(change.get("field")) in fields
            and (
                norm_value(change.get("new")) in explicit_values
                if explicit_values
                else is_down(change.get("new"))
            )
        )
    return False


def current_state_condition_matches(
    condition: dict[str, Any],
    observation: dict[str, Any],
    *,
    explicit_current_scope: bool = False,
) -> bool:
    """Evaluate a fault condition against a current Observation, not a fake Change."""

    if not condition_applicability_matches(condition, observation):
        return False
    if not dimension_selector_matches(
        condition.get("dimensions"),
        observation.get("dimensions") or observation.get("dimensions_json"),
    ):
        return False
    if not _entity_type_allowed(condition, observation):
        return False

    kind = str(condition.get("kind") or "").strip()
    field = norm_field(observation.get("field") or observation.get("field_name"))
    fields = {norm_field(item) for item in condition.get("fields") or []}
    if fields and field not in fields:
        return False
    value = observation.get("value")
    if "value" not in observation:
        value = observation.get("value_json")

    if kind == "numeric_health_result":
        return condition.get("unhealthy") is True
    if kind == "threshold":
        threshold = to_float(condition.get("gte"))
        current = to_float(value)
        return threshold is not None and current is not None and current >= threshold
    if kind == "threshold_lte":
        threshold = to_float(condition.get("lte"))
        current = to_float(value)
        return threshold is not None and current is not None and current <= threshold
    if kind in {"threshold_field_gte", "threshold_field_lte"}:
        context = context_from_change(observation)
        _reference_field, reference = resolve_threshold_reference(condition, context)
        current = to_float(value)
        if reference is None or current is None:
            return False
        return current >= reference if kind == "threshold_field_gte" else current <= reference
    if kind == "linear_power_loss_ratio":
        loss_ratio = to_float(condition.get("loss_ratio"))
        maximum = to_float(condition.get("gt"))
        return (
            loss_ratio is not None
            and maximum is not None
            and loss_ratio > maximum
        )
    if kind == "transition":
        if is_suppressed_interface_state(value):
            return False
        allow_current = explicit_current_scope or bool(condition.get("allow_initial_target"))
        to_values = {norm(item) for item in condition.get("to_values") or []}
        target_matches = (
            norm(value) in to_values
            if to_values
            else _state_group(value) == str(condition.get("to_group") or "")
        )
        return allow_current and target_matches
    if kind == "bgp_transition":
        allow_current = explicit_current_scope or bool(condition.get("allow_initial_target"))
        return (
            allow_current
            and is_bgp_change(observation)
            and (norm(value) == "established") is bool(condition.get("to_established"))
        )
    if kind == "entity_status_down":
        explicit_values = {norm_value(item) for item in condition.get("values") or []}
        return norm_value(value) in explicit_values if explicit_values else is_down(value)
    return False


def _effective_condition(rule_name: str, condition: dict[str, Any]) -> dict[str, Any]:
    """Fill new built-in rule guard fields for older active DB rule versions."""
    current = dict(condition or {})
    defaults = _default_condition(rule_name)
    if defaults.get("kind") == current.get("kind"):
        for key, value in defaults.items():
            current.setdefault(key, value)
    return current


def _state_group(value: Any) -> str:
    if is_up(value):
        return "up"
    if is_down(value):
        return "down"
    return "other"


class EventRuleRepository:
    def list_versions(
        self,
        *,
        status: str | None = None,
        event_type: str | None = None,
        limit: int | None = 500,
    ) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = db.query(EventRuleRecord, EventRuleVersionRecord).join(
                EventRuleVersionRecord,
                EventRuleVersionRecord.rule_id == EventRuleRecord.id,
            )
            if status:
                query = query.filter(EventRuleVersionRecord.status == status)
            if event_type:
                query = query.filter(EventRuleVersionRecord.event_type == event_type)
            query = query.order_by(
                EventRuleVersionRecord.priority.desc(),
                EventRuleRecord.name,
                EventRuleVersionRecord.version.desc(),
            )
            if limit is not None:
                query = query.limit(max(1, int(limit)))
            rows = query.all()
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
                    "source_job_id": version.source_job_id,
                    "check_item_version_id": version.check_item_version_id,
                    "validation_report": version.validation_report_json,
                    "digest": version.digest,
                    "priority": version.priority,
                    "exclusive": version.exclusive,
                    "event_class": version.event_class,
                    "evaluation_scope": govern_evaluation_scope(
                        version.evaluation_scope,
                        event_class=version.event_class,
                    ),
                    "recovery": version.recovery_json,
                    "correlation_keys": version.correlation_key_json,
                    "created_by": version.created_by,
                    "created_at": version.created_at,
                    "activated_at": version.activated_at,
                    "deprecated_at": version.deprecated_at,
                }
                for rule, version in rows
            ]

    def list_versions_page(
        self,
        *,
        status: str | None = None,
        event_type: str | None = None,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        page_size = max(1, min(int(limit), 200))
        page_offset = max(0, int(offset))
        with get_db_session() as db:
            base_query = db.query(EventRuleRecord, EventRuleVersionRecord).join(
                EventRuleVersionRecord,
                EventRuleVersionRecord.rule_id == EventRuleRecord.id,
            )
            if event_type:
                base_query = base_query.filter(EventRuleVersionRecord.event_type == event_type)
            pattern = f"%{str(search).strip()}%" if str(search or "").strip() else None
            if pattern:
                base_query = base_query.filter(
                    or_(
                        EventRuleRecord.id.ilike(pattern),
                        EventRuleRecord.name.ilike(pattern),
                        EventRuleVersionRecord.id.ilike(pattern),
                        EventRuleVersionRecord.event_type.ilike(pattern),
                        EventRuleVersionRecord.severity.ilike(pattern),
                        EventRuleVersionRecord.created_by.ilike(pattern),
                    )
                )
            status_counts = {
                str(value): int(count)
                for value, count in (
                    base_query.with_entities(
                        EventRuleVersionRecord.status,
                        func.count(EventRuleVersionRecord.id),
                    )
                    .group_by(EventRuleVersionRecord.status)
                    .all()
                )
            }
            query = base_query
            if status:
                query = query.filter(EventRuleVersionRecord.status == status)
            total = query.order_by(None).count()
            rows = (
                query.order_by(
                    EventRuleVersionRecord.priority.desc(),
                    EventRuleRecord.name,
                    EventRuleVersionRecord.version.desc(),
                    EventRuleVersionRecord.id.desc(),
                )
                .offset(page_offset)
                .limit(page_size)
                .all()
            )
            return {
                "items": [self._to_version_payload(rule, version) for rule, version in rows],
                "total": total,
                "limit": page_size,
                "offset": page_offset,
                "status_counts": status_counts,
            }

    @staticmethod
    def _to_version_payload(
        rule: EventRuleRecord,
        version: EventRuleVersionRecord,
    ) -> dict[str, Any]:
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
            "source_job_id": version.source_job_id,
            "check_item_version_id": version.check_item_version_id,
            "validation_report": version.validation_report_json,
            "digest": version.digest,
            "priority": version.priority,
            "exclusive": version.exclusive,
            "event_class": version.event_class,
            "evaluation_scope": govern_evaluation_scope(
                version.evaluation_scope,
                event_class=version.event_class,
            ),
            "recovery": version.recovery_json,
            "correlation_keys": version.correlation_key_json,
            "created_by": version.created_by,
            "created_at": version.created_at,
            "activated_at": version.activated_at,
            "deprecated_at": version.deprecated_at,
        }

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
                .order_by(
                    EventRuleVersionRecord.priority.desc(),
                    EventRuleRecord.name,
                )
                .all()
            )
            return [
                EventRule(
                    rule_id=rule.name,
                    version=int(version.version),
                    event_type=version.event_type,
                    severity=version.severity,
                    predicate=partial(
                        condition_matches,
                        _effective_condition(
                            str(rule.name),
                            dict(version.condition_json or {}),
                        ),
                    ),
                    condition=_effective_condition(
                        str(rule.name),
                        dict(version.condition_json or {}),
                    ),
                    priority=int(version.priority or 100),
                    exclusive=bool(version.exclusive),
                    event_class=str(version.event_class or "fault"),
                    evaluation_scope=govern_evaluation_scope(
                        version.evaluation_scope,
                        event_class=version.event_class,
                    ),
                    recovery=dict(version.recovery_json or {}),
                    correlation_keys=tuple(
                        version.correlation_key_json
                        or ("device_id", "entity_type", "entity_key", "field")
                    ),
                    version_id=str(version.id),
                )
                for rule, version in rows
            ]

    def snapshot_active_rules(self) -> list[dict[str, Any]]:
        rows = self.list_versions(status="active", limit=None)
        if not rows:
            # An intentionally empty catalog must stay empty after disabling or
            # deleting the last rule; built-ins are only for an uninitialized DB.
            with get_db_session() as db:
                if db.query(EventRuleVersionRecord.id).first() is not None:
                    return []
            from src.common.config import get_settings

            if get_settings().EVENT_RULE_ALLOW_BUILTIN_FALLBACK:
                rows = [
                    {
                        "rule_id": definition.rule_id,
                        "rule_name": definition.rule_id,
                        "version": definition.version,
                        "event_type": definition.event_type,
                        "severity": definition.severity,
                        "condition": dict(definition.condition),
                        "priority": definition.priority,
                        "exclusive": definition.exclusive,
                        "event_class": definition.event_class,
                        "evaluation_scope": definition.evaluation_scope,
                        "recovery": dict(definition.recovery or {}),
                        "correlation_keys": list(definition.correlation_keys),
                        "source": "builtin_fallback",
                    }
                    for definition in default_event_rule_definitions()
                ]
        return [
            {
                **row,
                "effective_condition": _effective_condition(
                    str(row.get("rule_name") or row.get("rule_id") or ""),
                    dict(row.get("condition") or {}),
                ),
            }
            for row in rows
        ]

    def ensure_default_rules(
        self,
        *,
        actor: str | None = None,
        reason: str | None = None,
    ) -> int:
        created = 0
        with get_db_session() as db:
            for definition in default_event_rule_definitions():
                row = (
                    db.query(EventRuleRecord)
                    .filter(EventRuleRecord.name == definition.rule_id)
                    .first()
                )
                if row is None:
                    row = EventRuleRecord(
                        id=str(uuid.uuid4()),
                        name=definition.rule_id,
                        description=definition.description
                        or f"Built-in rule for {definition.event_type}",
                        status="active",
                        current_version=definition.version,
                    )
                    db.add(row)
                    db.flush()
                    created += 1
                if (
                    db.query(EventRuleVersionRecord)
                    .filter(
                        EventRuleVersionRecord.rule_id == row.id,
                        EventRuleVersionRecord.version == definition.version,
                    )
                    .first()
                    is None
                ):
                    db.add(
                        EventRuleVersionRecord(
                            id=str(uuid.uuid4()),
                            rule_id=row.id,
                            version=definition.version,
                            event_type=definition.event_type,
                            severity=definition.severity,
                            condition_json=definition.condition,
                            priority=definition.priority,
                            exclusive=definition.exclusive,
                            event_class=definition.event_class,
                            evaluation_scope=definition.evaluation_scope,
                            recovery_json=definition.recovery,
                            correlation_key_json=list(definition.correlation_keys),
                            status="active",
                            digest=event_rule_digest(
                                name=definition.rule_id,
                                event_type=definition.event_type,
                                severity=definition.severity,
                                condition=definition.condition,
                                governance={
                                    "priority": definition.priority,
                                    "exclusive": definition.exclusive,
                                    "event_class": definition.event_class,
                                    "evaluation_scope": definition.evaluation_scope,
                                    "recovery": definition.recovery,
                                    "correlation_keys": definition.correlation_keys,
                                },
                            ),
                        )
                    )
                    row.current_version = definition.version
                    created += 1
            if actor and reason:
                db.add(
                    AuditLogRecord(
                        id=str(uuid.uuid4()),
                        user_id=actor,
                        username=actor,
                        action="event_rule_builtin_bootstrap",
                        resource_type="event_rule_pack",
                        resource_id="builtin",
                        detail={
                            "reason": reason,
                            "created_count": created,
                            "emergency_override": True,
                        },
                        status="success",
                        created_at=datetime.now(timezone.utc),
                    )
                )
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
        priority: int = 100,
        exclusive: bool = False,
        event_class: str = "fault",
        evaluation_scope: str | None = None,
        recovery: dict[str, Any] | None = None,
        correlation_keys: list[str] | tuple[str, ...] | None = None,
    ) -> str:
        governed_scope = govern_evaluation_scope(
            evaluation_scope,
            event_class=event_class,
        )
        with get_db_session() as db:
            rule = db.query(EventRuleRecord).filter(EventRuleRecord.name == name).first()
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
                priority=priority,
                exclusive=exclusive,
                event_class=event_class,
                evaluation_scope=governed_scope,
                recovery_json=recovery,
                correlation_key_json=list(govern_event_correlation_keys(correlation_keys)),
                status="active",
                created_by=created_by,
                activated_at=datetime.now(timezone.utc),
                digest=event_rule_digest(
                    name=name,
                    event_type=event_type,
                    severity=severity,
                    condition=condition,
                    governance={
                        "priority": priority,
                        "exclusive": exclusive,
                        "event_class": event_class,
                        "evaluation_scope": governed_scope,
                        "recovery": recovery,
                        "correlation_keys": govern_event_correlation_keys(correlation_keys),
                    },
                ),
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
        source_job_id: str | None = None,
        check_item_version_id: str | None = None,
        validation_report: dict[str, Any] | None = None,
        digest: str | None = None,
        status: str = "draft",
        priority: int = 100,
        exclusive: bool = False,
        event_class: str = "fault",
        evaluation_scope: str | None = None,
        recovery: dict[str, Any] | None = None,
        correlation_keys: list[str] | tuple[str, ...] | None = None,
    ) -> str:
        if status not in {"draft", "pending_approval"}:
            raise ValueError("event rule version status must be draft or pending_approval")
        governed_scope = govern_evaluation_scope(
            evaluation_scope,
            event_class=event_class,
        )
        with get_db_session() as db:
            rule = db.query(EventRuleRecord).filter(EventRuleRecord.name == name).first()
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
                priority=priority,
                exclusive=exclusive,
                event_class=event_class,
                evaluation_scope=governed_scope,
                recovery_json=recovery,
                correlation_key_json=list(govern_event_correlation_keys(correlation_keys)),
                status=status,
                created_by=created_by,
                source_job_id=source_job_id,
                check_item_version_id=check_item_version_id,
                validation_report_json=validation_report,
                digest=digest
                or event_rule_digest(
                    name=name,
                    event_type=event_type,
                    severity=severity,
                    condition=condition,
                    governance={
                        "priority": priority,
                        "exclusive": exclusive,
                        "event_class": event_class,
                        "evaluation_scope": governed_scope,
                        "recovery": recovery,
                        "correlation_keys": govern_event_correlation_keys(correlation_keys),
                    },
                ),
            )
            db.add(version)
            return version.id

    def activate_rule_version(self, version_id: str) -> dict[str, Any]:
        with get_db_session() as db:
            version = db.get(EventRuleVersionRecord, version_id)
            if version is None:
                raise ValueError("event rule version not found")
            if version.status == "active":
                return {
                    "success": True,
                    "version_id": version.id,
                    "version_status": version.status,
                    "idempotent": True,
                }
        raise ValueError(
            "direct event rule activation is disabled; approve the associated "
            "RuleGenerationJob or use the audited force-activate endpoint"
        )

    def force_activate_rule_version(
        self,
        version_id: str,
        *,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        if not str(actor or "").strip() or not str(reason or "").strip():
            raise ValueError("force activation requires actor and reason")
        with get_db_session() as db:
            version = (
                db.query(EventRuleVersionRecord)
                .filter(EventRuleVersionRecord.id == version_id)
                .first()
            )
            if version is None:
                raise ValueError("event rule version not found")
            if version.status == "deleted":
                raise ValueError("事件规则已删除，不能变更状态")
            rule = db.query(EventRuleRecord).filter(EventRuleRecord.id == version.rule_id).first()
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
            db.add(
                AuditLogRecord(
                    id=str(uuid.uuid4()),
                    user_id=actor,
                    username=actor,
                    action="event_rule_force_activate",
                    resource_type="event_rule_version",
                    resource_id=version.id,
                    detail={
                        "reason": reason,
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "emergency_override": True,
                    },
                    status="success",
                    created_at=now,
                )
            )
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
                "priority": version.priority,
                "exclusive": version.exclusive,
                "event_class": version.event_class,
                "evaluation_scope": govern_evaluation_scope(
                    version.evaluation_scope,
                    event_class=version.event_class,
                ),
                "recovery": version.recovery_json,
                "correlation_keys": version.correlation_key_json,
                "version_status": version.status,
                "activated_at": version.activated_at,
                "deprecated_at": version.deprecated_at,
            }

    def deprecate_rule_version(
        self,
        version_id: str,
        *,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        if not str(actor or "").strip() or not str(reason or "").strip():
            raise ValueError("event rule deprecation requires actor and reason")
        with get_db_session() as db:
            version = (
                db.query(EventRuleVersionRecord)
                .filter(EventRuleVersionRecord.id == version_id)
                .first()
            )
            if version is None:
                raise ValueError("event rule version not found")
            if version.status == "deleted":
                raise ValueError("事件规则已删除，不能变更状态")
            rule = db.query(EventRuleRecord).filter(EventRuleRecord.id == version.rule_id).first()
            if rule is None:
                raise ValueError("event rule not found")
            now = datetime.now(timezone.utc)
            version.status = "deprecated"
            version.deprecated_at = now
            if int(rule.current_version or 0) == int(version.version):
                active = (
                    db.query(EventRuleVersionRecord)
                    .filter(EventRuleVersionRecord.rule_id == rule.id)
                    .filter(EventRuleVersionRecord.status == "active")
                    .first()
                )
                rule.current_version = int(active.version) if active else 0
                rule.status = "active" if active else "deprecated"
            db.add(
                AuditLogRecord(
                    id=str(uuid.uuid4()),
                    user_id=actor,
                    username=actor,
                    action="event_rule_deprecate",
                    resource_type="event_rule_version",
                    resource_id=version.id,
                    detail={
                        "reason": reason,
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                    },
                    status="success",
                    created_at=now,
                )
            )
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


def load_event_rule_pack(path: str | Path | None = None) -> list[EventRuleDefinition]:
    payload = load_yaml(path or BUILTIN_EVENT_RULE_PACK)
    rules = payload.get("rules") or []
    if not isinstance(rules, list):
        raise ValueError("event rule pack rules must be a list")
    definitions: list[EventRuleDefinition] = []
    for item in rules:
        if not isinstance(item, dict):
            raise ValueError("event rule pack item must be an object")
        condition = item.get("condition") or {}
        if not isinstance(condition, dict):
            raise ValueError(f"event rule condition must be an object: {item.get('name')}")
        definitions.append(
            EventRuleDefinition(
                rule_id=str(item["name"]),
                version=int(item.get("version") or 1),
                event_type=str(item["event_type"]),
                severity=str(item["severity"]),
                condition=dict(condition),
                description=item.get("description"),
                priority=int(item.get("priority") or 100),
                exclusive=bool(item.get("exclusive", False)),
                event_class=str(item.get("event_class") or "fault"),
                evaluation_scope=govern_evaluation_scope(
                    item.get("evaluation_scope"),
                    event_class=str(item.get("event_class") or "fault"),
                ),
                recovery=dict(item.get("recovery") or {}),
                correlation_keys=tuple(
                    item.get("correlation_key")
                    or item.get("correlation_keys")
                    or ("device_id", "entity_type", "entity_key", "field")
                ),
            )
        )
    return definitions


def default_event_rule_definitions() -> list[EventRuleDefinition]:
    return load_event_rule_pack(BUILTIN_EVENT_RULE_PACK)


def default_event_rules() -> list[EventRule]:
    return [definition.to_rule() for definition in default_event_rule_definitions()]


def match_event_rules(
    change: dict[str, Any],
    *,
    rules: list[EventRule] | None = None,
    preordered: bool = False,
) -> list[EventRule]:
    matched: list[EventRule] = []
    effective_rules = rules if rules is not None else load_event_rules()
    if rules is not None and not preordered:
        effective_rules = sorted(effective_rules, key=lambda rule: (-rule.priority, rule.rule_id))
    for rule in effective_rules:
        if rule.match(change):
            matched.append(rule)
            if rule.exclusive:
                break
    return matched


def event_rule_definitions_from_snapshot(
    rows: list[dict[str, Any]],
) -> list[EventRuleDefinition]:
    return [
        EventRuleDefinition(
            rule_id=str(row.get("rule_name") or row.get("rule_id") or ""),
            version=int(row.get("version") or 1),
            event_type=str(row.get("event_type") or ""),
            severity=str(row.get("severity") or "warning"),
            condition=dict(row.get("effective_condition") or row.get("condition") or {}),
            priority=int(row.get("priority") or 100),
            exclusive=bool(row.get("exclusive")),
            event_class=str(row.get("event_class") or "fault"),
            evaluation_scope=govern_evaluation_scope(
                row.get("evaluation_scope"),
                event_class=str(row.get("event_class") or "fault"),
            ),
            recovery=dict(row.get("recovery") or {}),
            correlation_keys=tuple(
                row.get("correlation_keys") or ("device_id", "entity_type", "entity_key", "field")
            ),
        )
        for row in rows
    ]


def event_rules_from_snapshot(rows: list[dict[str, Any]]) -> list[EventRule]:
    rules = [
        definition.to_rule()
        for definition in event_rule_definitions_from_snapshot(rows)
    ]
    return sorted(rules, key=lambda rule: (-rule.priority, rule.rule_id))


def load_event_rules(*, allow_builtin_fallback: bool | None = None) -> list[EventRule]:
    if allow_builtin_fallback is None:
        from src.common.config import get_settings

        allow_builtin_fallback = bool(get_settings().EVENT_RULE_ALLOW_BUILTIN_FALLBACK)
    try:
        rules = EventRuleRepository().list_active_rules()
        if rules:
            return sorted(rules, key=lambda rule: (-rule.priority, rule.rule_id))
        fallback = default_event_rules() if allow_builtin_fallback else []
        return sorted(fallback, key=lambda rule: (-rule.priority, rule.rule_id))
    except Exception as exc:
        if allow_builtin_fallback:
            return sorted(default_event_rules(), key=lambda rule: (-rule.priority, rule.rule_id))
        raise RuntimeError("active event rules are unavailable") from exc


def _default_condition(rule_id: str) -> dict[str, Any]:
    for definition in default_event_rule_definitions():
        if definition.rule_id == rule_id:
            return dict(definition.condition)
    return {"kind": "unknown"}

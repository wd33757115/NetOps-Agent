"""Build immutable LLM inputs from structured patrol snapshots only."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, or_

from src.apps.rule_governance.backend.check_items.runtime_bundle_assembly import (
    derive_asset_requirement_profile,
)
from src.apps.rule_governance.backend.rule_control.planning import profile_fields
from src.apps.rule_governance.backend.rule_control.source_scope import (
    require_current_rule_generation_context,
    require_immutable_rule_asset_source_datasets,
    rule_generation_context_digest,
)
from src.core.check_items.canonical import rule_semantics_catalog
from src.core.check_items.command_adapter import is_direct_projection_adapter
from src.infrastructure.db.models import (
    CheckItemVersionRecord,
    CollectionImplementationVersionRecord,
    CommandSnapshot,
    ParserTemplate,
    RuleAssetCommandUnitCheckItemRecord,
    RuleAssetCommandUnitRecord,
    RuleGenerationContextRecord,
    RuleGenerationJobRecord,
    SnapshotParseResult,
)
from src.infrastructure.db.postgres import get_db_session


def assess_context_quality(
    records: list[dict[str, Any]],
    field_name: str | None,
    *,
    min_records: int | None = None,
    min_field_fill_rate: float | None = None,
) -> dict[str, Any]:
    """Gate auto-built contexts: enough samples and a usable target field.

    Too few records (or a mostly-empty target field) produce unreliable Policy
    thresholds, so callers should keep missing_context and require a manually
    attached dataset instead of silently freezing a weak context.
    """
    from src.common.config import get_settings

    settings = get_settings()
    required_records = max(
        1,
        int(
            min_records
            if min_records is not None
            else settings.RULE_CONTEXT_MIN_RECORDS
        ),
    )
    required_fill = float(
        min_field_fill_rate
        if min_field_fill_rate is not None
        else settings.RULE_CONTEXT_MIN_FIELD_FILL_RATE
    )
    required_fill = min(1.0, max(0.0, required_fill))
    record_count = len(records or [])
    field = str(field_name or "").strip()
    non_empty = 0
    if field and record_count:
        for record in records:
            value = record.get(field) if isinstance(record, dict) else None
            if value not in (None, "", [], {}):
                non_empty += 1
    fill_rate = (non_empty / record_count) if record_count else 0.0
    reasons: list[str] = []
    if record_count < required_records:
        reasons.append(
            f"insufficient_records: {record_count} < {required_records}"
        )
    if field and fill_rate < required_fill:
        reasons.append(
            f"low_field_fill_rate: {field}={fill_rate:.2f} < {required_fill:.2f}"
        )
    if not field:
        reasons.append("missing_target_field")
    return {
        "passed": not reasons,
        "record_count": record_count,
        "min_records": required_records,
        "field_name": field or None,
        "field_fill_rate": round(fill_rate, 4),
        "min_field_fill_rate": required_fill,
        "reasons": reasons,
    }


SENSITIVE_KEYS = {
    "auth",
    "authentication",
    "community",
    "credential",
    "password",
    "private_key",
    "raw",
    "raw_cli",
    "raw_output",
    "raw_text",
    "secret",
    "snmp_community",
    "token",
}
IDENTITY_KEYS = {
    "device",
    "device_id",
    "device_name",
    "host",
    "hostname",
    "ip",
    "ip_address",
    "management_ip",
    "neighbor",
    "neighbor_name",
    "peer",
    "peer_name",
    "peer_ip",
    "remote_device",
    "remote_name",
    "remote_ip",
}
IP_IDENTITY_KEYS = {"ip", "ip_address", "management_ip", "peer_ip", "remote_ip"}
FREE_TEXT_IDENTITY_KEYS = {"desc", "description"}
IPV4_PATTERN = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
SECRET_ASSIGNMENT_PATTERN = re.compile(r"(?i)\b(password|secret|token|community)\s*[:=]\s*[^\s,;]+")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stable_alias(kind: str, value: Any) -> str:
    digest = hashlib.sha256(f"offline-rule-context|{kind}|{value}".encode()).hexdigest()
    return f"{kind}-{digest[:12]}"


def _sanitize_string(value: str, *, removed: dict[str, int], pseudonymized: dict[str, int]) -> str:
    candidate = value.strip()
    try:
        parsed = ipaddress.ip_interface(candidate)
    except ValueError:
        pass
    else:
        pseudonymized["ip_literal"] = pseudonymized.get("ip_literal", 0) + 1
        return _pseudonymize_ip(parsed, had_prefix="/" in candidate)

    def replace_secret(match: re.Match[str]) -> str:
        removed["embedded_secret"] = removed.get("embedded_secret", 0) + 1
        return f"{match.group(1)}=[REDACTED]"

    def replace_ipv4(match: re.Match[str]) -> str:
        candidate = match.group(0)
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            return candidate
        pseudonymized["embedded_ip"] = pseudonymized.get("embedded_ip", 0) + 1
        return _stable_alias("ip", candidate)

    redacted = SECRET_ASSIGNMENT_PATTERN.sub(replace_secret, value)
    return IPV4_PATTERN.sub(replace_ipv4, redacted)


def _pseudonymize_ip(
    value: ipaddress.IPv4Interface | ipaddress.IPv6Interface,
    *,
    had_prefix: bool,
) -> str:
    digest = int(hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:8], 16)
    if value.version == 4:
        address = ipaddress.ip_address(f"198.51.100.{1 + digest % 254}")
    else:
        address = ipaddress.ip_address(f"2001:db8::{1 + digest % 65534:x}")
    return f"{address}/{value.network.prefixlen}" if had_prefix else str(address)


def _sanitize(value: Any, *, removed: dict[str, int], pseudonymized: dict[str, int]) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for raw_key, nested in value.items():
            key = str(raw_key)
            lowered = key.lower()
            if lowered in SENSITIVE_KEYS or any(
                token in lowered
                for token in ("password", "secret", "credential", "token", "community")
            ):
                removed[lowered] = removed.get(lowered, 0) + 1
                continue
            if lowered in IP_IDENTITY_KEYS and nested not in (None, ""):
                is_collection = isinstance(nested, (list, tuple))
                sanitized_values: list[Any] = []
                for item in list(nested) if is_collection else [nested]:
                    text = str(item).strip()
                    if text.casefold() in {"--", "none", "unassigned", "unknown"}:
                        sanitized_values.append(text)
                        continue
                    try:
                        parsed_ip = ipaddress.ip_interface(text)
                    except ValueError:
                        sanitized_values.append(_stable_alias("identity", item))
                    else:
                        sanitized_values.append(
                            _pseudonymize_ip(parsed_ip, had_prefix="/" in text)
                        )
                    pseudonymized[lowered] = pseudonymized.get(lowered, 0) + 1
                output[key] = sanitized_values if is_collection else sanitized_values[0]
                continue
            if lowered in IDENTITY_KEYS and nested not in (None, ""):
                pseudonymized[lowered] = pseudonymized.get(lowered, 0) + 1
                output[key] = _stable_alias("identity", nested)
                continue
            if lowered in FREE_TEXT_IDENTITY_KEYS and nested not in (None, ""):
                pseudonymized[lowered] = pseudonymized.get(lowered, 0) + 1
                output[key] = _stable_alias("text", nested)
                continue
            output[key] = _sanitize(nested, removed=removed, pseudonymized=pseudonymized)
        return output
    if isinstance(value, list):
        return [_sanitize(item, removed=removed, pseudonymized=pseudonymized) for item in value]
    if isinstance(value, str):
        return _sanitize_string(value, removed=removed, pseudonymized=pseudonymized)
    return value


def _numeric_scalars(value: Any) -> list[float]:
    """Flatten parser scalar/list measurements into finite numeric evidence."""

    if isinstance(value, list):
        return [number for item in value for number in _numeric_scalars(item)]
    if isinstance(value, bool) or value in (None, ""):
        return []
    try:
        number = float(value)
    except (TypeError, ValueError):
        return []
    return [number] if math.isfinite(number) else []


def sanitize_structured_records(
    records: list[dict[str, Any]],
    *,
    approved_fields: set[str] | None = None,
) -> dict[str, Any]:
    """Pure sanitizer shared by database contexts and local validation tools."""

    removed: dict[str, int] = {}
    pseudonymized: dict[str, int] = {}
    dropped_unapproved: dict[str, int] = {}
    sanitized: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"records[{index}] must be an object")
        selected: dict[str, Any] = {}
        for raw_key, value in record.items():
            key = str(raw_key)
            if approved_fields is not None and key not in approved_fields:
                dropped_unapproved[key] = dropped_unapproved.get(key, 0) + 1
                continue
            selected[key] = value
        sanitized.append(_sanitize(selected, removed=removed, pseudonymized=pseudonymized))
    return {
        "records": sanitized,
        "privacy": {
            "success": True,
            "raw_cli_included": False,
            "sampled_record_count": len(sanitized),
            "approved_field_whitelist": sorted(approved_fields or set()),
            "dropped_unapproved_fields": dropped_unapproved,
            "removed_fields": removed,
            "pseudonymized_fields": pseudonymized,
        },
    }


def _approved_field_whitelist(
    template: ParserTemplate,
    *,
    require_approval: bool = True,
) -> set[str]:
    validation = dict(template.validation_json or {})
    if require_approval and validation.get("contract_approval_status") != "approved":
        raise ValueError("parser template field contract is not approved")
    fields = {
        str(field).strip()
        for key in ("primary_keys", "required_fields", "optional_fields")
        for field in (validation.get(key) or [])
        if str(field).strip()
    }
    for record_set in validation.get("record_sets") or []:
        if not isinstance(record_set, dict):
            continue
        fields.update(
            str(field).strip()
            for key in ("primary_keys", "required_fields", "optional_fields")
            for field in record_set.get(key) or []
            if str(field).strip()
        )
    if not fields:
        raise ValueError("parser template has no approved field whitelist")
    return fields


def _round_robin_snapshot_records(
    candidates: list[tuple[str, str, dict[str, Any]]],
    *,
    max_records: int,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    # A parser bundle can emit several named record sets from one snapshot. A
    # large table (for example, one row per interface) must not exhaust the
    # context budget before singleton measurements or threshold sets are seen.
    # Keep the snapshot boundary for replay evidence, but sample independently
    # within every named record set.
    grouped: dict[
        tuple[str, str, str],
        deque[tuple[int, dict[str, Any]]],
    ] = {}
    for index, (dataset_id, group_id, record) in enumerate(candidates):
        record_set = str(record.get("__record_set__") or "__default__")
        grouped.setdefault((dataset_id, group_id, record_set), deque()).append(
            (index, record)
        )
    records: list[dict[str, Any]] = []
    selected_groups: dict[str, list[dict[str, Any]]] = {}
    group_order = sorted(grouped)

    # Large CLI tables are usually ordered by entity identity. Plain head
    # sampling can therefore hide rare operational states near the tail. Build
    # a bounded vocabulary only for low-cardinality fields; identifiers and
    # free text naturally exceed the cap and do not consume this budget.
    value_sets: dict[str, set[tuple[str, str]]] = {}
    record_tokens: dict[int, set[tuple[str, str, str]]] = {}
    for index, (_dataset_id, _group_id, record) in enumerate(candidates):
        tokens: set[tuple[str, str, str]] = set()
        for raw_field, raw_value in record.items():
            field = str(raw_field)
            if field == "__record_set__":
                continue
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            for value in values:
                if value in (None, "") or isinstance(value, (dict, list)):
                    continue
                if isinstance(value, str):
                    token = ("string", value.strip().casefold())
                else:
                    token = (type(value).__name__, str(value))
                value_sets.setdefault(field, set()).add(token)
                tokens.add((field, *token))
        record_tokens[index] = tokens
    diversity_fields = {
        field
        for field, values in value_sets.items()
        if 1 < len(values) <= 20
    }
    eligible_tokens = {
        (field, *token)
        for field in diversity_fields
        for token in value_sets[field]
    }
    selected_indices: set[int] = set()

    def select(index: int, record: dict[str, Any], group_id: str) -> None:
        selected_indices.add(index)
        records.append(record)
        selected_groups.setdefault(group_id, []).append(record)

    # Preserve every immutable snapshot / named record-set boundary first.
    for group_key in group_order:
        if len(records) >= max_records:
            break
        bucket = grouped[group_key]
        if bucket:
            index, record = bucket.popleft()
            select(index, record, group_key[1])

    # Then greedily cover rare values that head sampling would otherwise miss.
    covered_tokens = {
        token
        for index in selected_indices
        for token in record_tokens.get(index, set())
        if token in eligible_tokens
    }
    uncovered_tokens = eligible_tokens - covered_tokens
    while uncovered_tokens and len(records) < max_records:
        best_index: int | None = None
        best_group_id = ""
        best_record: dict[str, Any] | None = None
        best_score: tuple[int, int, str] | None = None
        for index, (_dataset_id, group_id, record) in enumerate(candidates):
            if index in selected_indices:
                continue
            gain = len(record_tokens.get(index, set()) & uncovered_tokens)
            if gain <= 0:
                continue
            score = (gain, -len(selected_groups.get(group_id, [])), group_id)
            if best_score is None or score > best_score:
                best_index = index
                best_group_id = group_id
                best_record = record
                best_score = score
        if best_index is None or best_record is None:
            break
        select(best_index, best_record, best_group_id)
        uncovered_tokens -= record_tokens.get(best_index, set())

    # Fill the remaining budget in stable round-robin order.
    while len(records) < max_records and any(grouped.values()):
        for group_key in group_order:
            bucket = grouped[group_key]
            while bucket and bucket[0][0] in selected_indices:
                bucket.popleft()
            if bucket and len(records) < max_records:
                index, record = bucket.popleft()
                select(index, record, group_key[1])
    return records, selected_groups


def _structured_records(structured: Any) -> list[dict[str, Any]]:
    if isinstance(structured, dict):
        raw_sets = structured.get("record_sets")
        if isinstance(raw_sets, dict):
            return [
                {"__record_set__": str(name), **dict(record)}
                for name, records in raw_sets.items()
                for record in (records if isinstance(records, list) else [records])
                if isinstance(record, dict)
            ]
        return [structured]
    if isinstance(structured, list):
        return [dict(item) for item in structured if isinstance(item, dict)]
    return []


def _check_item_generation_governance(
    db: Any,
    *,
    template: ParserTemplate,
    offline_job_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve approved check-item targets from materialized command-unit links."""

    unit_query = db.query(RuleAssetCommandUnitRecord).filter(
        func.lower(RuleAssetCommandUnitRecord.vendor)
        == str(template.vendor or "").strip().casefold(),
        func.lower(RuleAssetCommandUnitRecord.canonical_command)
        == str(template.canonical_command or "").strip().casefold(),
    )
    if offline_job_ids:
        unit_query = unit_query.filter(
            RuleAssetCommandUnitRecord.offline_job_id.in_(offline_job_ids)
        )
    units = unit_query.order_by(
        RuleAssetCommandUnitRecord.updated_at.desc(),
        RuleAssetCommandUnitRecord.id.desc(),
    ).all()
    exact = [
        item
        for item in units
        if str(item.parser_template_id or "") == str(template.id)
    ]
    selected_units = exact or units
    unit_ids = [str(item.id) for item in selected_units]
    if not unit_ids:
        return {
            "check_item_targets": [],
            "observation_targets": [],
            "strict_observation_targets": False,
            "canonical_identity_constraint": {},
            "target_resolution": "unclassified",
        }
    rows = (
        db.query(
            RuleAssetCommandUnitCheckItemRecord,
            CheckItemVersionRecord,
            CollectionImplementationVersionRecord,
        )
        .join(
            CheckItemVersionRecord,
            CheckItemVersionRecord.id
            == RuleAssetCommandUnitCheckItemRecord.check_item_version_id,
        )
        .outerjoin(
            CollectionImplementationVersionRecord,
            CollectionImplementationVersionRecord.id
            == RuleAssetCommandUnitCheckItemRecord.implementation_version_id,
        )
        .filter(
            RuleAssetCommandUnitCheckItemRecord.command_unit_id.in_(unit_ids),
            CheckItemVersionRecord.status == "active",
        )
        .order_by(
            RuleAssetCommandUnitCheckItemRecord.check_item_id,
            RuleAssetCommandUnitCheckItemRecord.implementation_version_id,
        )
        .all()
    )
    targets: list[dict[str, Any]] = []
    seen_links: set[tuple[str, str]] = set()
    for link, version, implementation_version in rows:
        key = (
            str(link.check_item_version_id),
            str(link.implementation_version_id),
        )
        if key in seen_links:
            continue
        seen_links.add(key)
        canonical_contract = dict(version.canonical_contract_json or {})
        canonical_catalog = rule_semantics_catalog(canonical_contract)
        asset_requirement_profile = derive_asset_requirement_profile(
            result_mode=str(version.result_mode),
            assessment_mode=str(version.assessment_mode),
            canonical_rows=[
                dict(item)
                for item in canonical_contract.get("observations") or []
                if isinstance(item, dict)
            ],
            canonical_catalog=canonical_catalog,
            direct_projection=bool(
                implementation_version is not None
                and is_direct_projection_adapter(
                    dict(implementation_version.adapter_spec_json or {})
                )
            ),
        )
        targets.append(
            {
                "check_item_id": str(link.check_item_id),
                "check_item_version_id": str(link.check_item_version_id),
                "implementation_id": str(link.implementation_id),
                "implementation_version_id": str(
                    link.implementation_version_id
                ),
                "result_mode": str(version.result_mode),
                "assessment_mode": str(version.assessment_mode),
                "asset_requirement_profile": asset_requirement_profile,
                "canonical_entity_type": str(
                    canonical_contract.get("entity_type") or ""
                ),
                "identity_dimensions": [
                    str(item)
                    for item in canonical_contract.get(
                        "identity_dimensions"
                    )
                    or []
                ],
                "identity_dimension_rules": dict(
                    canonical_contract.get("identity_dimension_rules") or {}
                ),
                "observations": [
                    {
                        **dict(item),
                        "check_item_id": str(link.check_item_id),
                        "canonical_entity_type": str(
                            canonical_contract.get("entity_type") or ""
                        ),
                        "identity_dimensions": [
                            str(dimension)
                            for dimension in canonical_contract.get(
                                "identity_dimensions"
                            )
                            or []
                        ],
                        "identity_dimension_rules": dict(
                            canonical_contract.get(
                                "identity_dimension_rules"
                            )
                            or {}
                        ),
                        **(
                            {
                                "canonical_description": definition.description,
                                "allowed_values": list(definition.allowed_values),
                                "value_types": list(definition.value_types),
                                "assessment_roles": list(
                                    definition.assessment_roles
                                ),
                            }
                            if (
                                definition := canonical_catalog.get(
                                    str(item.get("observation_type") or "")
                                )
                            )
                            is not None
                            else {}
                        ),
                    }
                    for item in canonical_contract.get("observations") or []
                    if isinstance(item, dict)
                ],
            }
        )
    observation_targets: list[dict[str, Any]] = []
    seen_observations: set[str] = set()
    for target in targets:
        for observation in target["observations"]:
            observation_type = str(
                observation.get("observation_type") or ""
            ).strip()
            if not observation_type or observation_type in seen_observations:
                continue
            seen_observations.add(observation_type)
            observation_targets.append(observation)
    identity_constraints = {
        json.dumps(
            {
                "canonical_entity_type": target["canonical_entity_type"],
                "identity_dimensions": target["identity_dimensions"],
                "identity_dimension_rules": target[
                    "identity_dimension_rules"
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        for target in targets
        if target["canonical_entity_type"]
    }
    canonical_identity_constraint = (
        json.loads(next(iter(identity_constraints)))
        if len(identity_constraints) == 1
        else {}
    )
    canonical_identity_constraints = [
        json.loads(item) for item in sorted(identity_constraints)
    ]
    required_asset_types = {
        str(asset_type)
        for target in targets
        for asset_type in (
            target.get("asset_requirement_profile", {}).get(
                "required_asset_types"
            )
            or []
        )
    }
    requirement_profiles = [
        dict(
            target.get("asset_requirement_profile", {}).get(
                "requirements"
            )
            or {}
        )
        for target in targets
    ]
    current_state_observation_types = sorted(
        {
            str(observation_type)
            for profile in requirement_profiles
            for observation_type in (
                profile.get("current_state_rule", {}).get(
                    "required_observation_types"
                )
                or []
            )
            if str(observation_type).strip()
        }
    )
    change_policy_observation_types = sorted(
        {
            str(observation_type)
            for profile in requirement_profiles
            for observation_type in (
                profile.get("change_policy", {}).get(
                    "required_observation_types"
                )
                or []
            )
            if str(observation_type).strip()
        }
    )
    required_event_types_by_observation: dict[str, set[str]] = {}
    for profile in requirement_profiles:
        for observation_type, event_types in dict(
            profile.get("event_rule", {}).get(
                "required_event_types_by_observation"
            )
            or {}
        ).items():
            required_event_types_by_observation.setdefault(
                str(observation_type), set()
            ).update(
                str(event_type)
                for event_type in event_types or []
                if str(event_type).strip()
            )
    requires_current_state_rule = "current_state_rule" in required_asset_types
    requires_event_rule = "event_rule" in required_asset_types
    rule_asset_requirements = {
        "schema_version": "rule-asset-requirements/v1",
        "required_asset_types": sorted(required_asset_types),
        "requires_observation_contract": (
            "observation_contract" in required_asset_types
        ),
        "requires_change_policy": (
            "change_policy" in required_asset_types
        ),
        "requires_current_state_rule": requires_current_state_rule,
        "requires_event_rule": requires_event_rule,
        # CurrentStateRule reuses immutable EventRuleVersion storage but remains
        # a separate logical role and assembly gate.
        "requires_event_rule_assets": (
            requires_current_state_rule or requires_event_rule
        ),
        "requires_historical_replay": requires_event_rule,
        "current_state_observation_types": (
            current_state_observation_types
        ),
        "change_policy_observation_types": (
            change_policy_observation_types
        ),
        "required_event_types_by_observation": {
            observation_type: sorted(event_types)
            for observation_type, event_types in sorted(
                required_event_types_by_observation.items()
            )
        },
        "check_item_target_count": len(targets),
    }
    return {
        "check_item_targets": targets,
        "observation_targets": observation_targets,
        "strict_observation_targets": bool(observation_targets),
        "canonical_identity_constraint": canonical_identity_constraint,
        "canonical_identity_constraints": canonical_identity_constraints,
        "rule_asset_requirements": rule_asset_requirements,
        "target_resolution": (
            "resolved"
            if targets and len(identity_constraints) <= 1
            else "resolved_multi_entity"
            if targets
            else "unclassified"
        ),
    }


class DatasetRuleGenerationContextService:
    def prepare(
        self,
        *,
        job_id: str,
        dataset_ids: list[str],
        created_by: str | None,
        max_records: int | None = 120,
        field_name: str | None = None,
        observation_type: str | None = None,
    ) -> dict[str, Any]:
        dataset_ids = sorted({str(item).strip() for item in dataset_ids if str(item).strip()})
        if not dataset_ids:
            raise ValueError("select at least one offline patrol dataset")
        max_records = max(1, min(int(max_records or 120), 500))
        with get_db_session() as db:
            job = db.get(RuleGenerationJobRecord, job_id)
            if not job:
                raise KeyError("rule generation job not found")
            payload = dict(job.payload_json or {})
            defaults = dict(payload.get("workflow_context_defaults") or {})
            template_id = str(payload.get("template_id") or "").strip()
            if not template_id:
                source_ref = str(
                    payload.get("source_ref") or job.source_ref or ""
                ).strip()
                prefix = "parser_template:"
                if source_ref.startswith(prefix):
                    template_id = source_ref[len(prefix) :].strip()
            if not template_id:
                raise ValueError("rule generation job is not linked to a parser template")
            template = db.get(ParserTemplate, template_id)
            if not template or template.status != "active":
                raise ValueError("rule generation job parser template is not active")
            allowed_fields = _approved_field_whitelist(template)
            validation = dict(template.validation_json or {})
            selected_field = str(field_name or "").strip()
            selected_observation = str(observation_type or "").strip()
            source_scope = require_immutable_rule_asset_source_datasets(
                db,
                dataset_ids,
            )
            period_scope = dict(source_scope["period_scope"])
            offline_job_ids = list(source_scope["job_ids"])
            # Catalog activation can race a user's explicit context rebuild.
            # Refresh the materialized command/check-item links before freezing
            # their immutable version IDs into the new generation context.
            from src.apps.rule_governance.backend.rule_control.command_asset_units import (
                RuleAssetCommandUnitService,
            )

            projection_service = RuleAssetCommandUnitService()
            for offline_job_id in offline_job_ids:
                projection_service.ensure_projection_current(offline_job_id)
            snapshot_scope = or_(
                *[
                    and_(
                        CommandSnapshot.dataset_id == dataset_id,
                        CommandSnapshot.run_id == run_id,
                    )
                    for dataset_id, run_id in sorted(period_scope.items())
                ]
            )
            scope_type = "offline_period_authoritative_runs"

            snapshots = (
                db.query(CommandSnapshot)
                .filter(
                    snapshot_scope,
                    CommandSnapshot.canonical_command == template.canonical_command,
                )
                .order_by(
                    CommandSnapshot.dataset_id,
                    CommandSnapshot.observed_at,
                    CommandSnapshot.id,
                )
                .all()
            )
            snapshot_ids = [str(item.id) for item in snapshots]
            reparses = (
                db.query(SnapshotParseResult)
                .filter(
                    SnapshotParseResult.snapshot_id.in_(snapshot_ids),
                    SnapshotParseResult.template_id == template_id,
                    SnapshotParseResult.parse_status == "success",
                    SnapshotParseResult.structured_json.is_not(None),
                )
                .order_by(
                    SnapshotParseResult.snapshot_id,
                    SnapshotParseResult.parsed_at.desc(),
                    SnapshotParseResult.id.desc(),
                )
                .all()
                if snapshot_ids
                else []
            )
            effective_reparse: dict[str, SnapshotParseResult] = {}
            for reparse in reparses:
                effective_reparse.setdefault(str(reparse.snapshot_id), reparse)
            rows: list[tuple[str, str, str, datetime, Any]] = []
            source_counts = {"capture": 0, "reparse": 0}
            seen_source_snapshots: set[tuple[str, str, str, str, str]] = set()
            for snapshot in snapshots:
                reparse = effective_reparse.get(str(snapshot.id))
                if reparse is not None:
                    structured = reparse.structured_json
                    source = "reparse"
                elif (
                    snapshot.parser_template_id == template_id
                    and snapshot.parse_status == "parsed"
                    and snapshot.structured_json is not None
                ):
                    structured = snapshot.structured_json
                    source = "capture"
                else:
                    continue
                if not isinstance(structured, dict) and not (
                    isinstance(structured, list)
                    and any(isinstance(item, dict) for item in structured)
                ):
                    continue
                source_snapshot_key = (
                    str(snapshot.dataset_id),
                    str(snapshot.run_id),
                    str(snapshot.device_id),
                    snapshot.observed_at.isoformat(),
                    json.dumps(
                        structured,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ),
                )
                if source_snapshot_key in seen_source_snapshots:
                    continue
                seen_source_snapshots.add(source_snapshot_key)
                source_counts[source] += 1
                rows.append(
                    (
                        str(snapshot.dataset_id),
                        str(snapshot.run_id),
                        str(snapshot.device_id),
                        snapshot.observed_at,
                        structured,
                    )
                )
            generation_governance = _check_item_generation_governance(
                db,
                template=template,
                offline_job_ids=offline_job_ids,
            )
        if not rows:
            raise ValueError("selected datasets contain no parsed records for this template")

        if period_scope:
            represented_datasets = {str(row[0]) for row in rows}
            missing_datasets = sorted(set(dataset_ids) - represented_datasets)
            if missing_datasets:
                raise ValueError(
                    "authoritative offline patrol runs contain no parsed records for this "
                    "template: " + ", ".join(missing_datasets)
                )

        removed: dict[str, int] = {}
        pseudonymized: dict[str, int] = {}
        dropped_unapproved: dict[str, int] = {}
        candidates: list[tuple[str, str, dict[str, Any]]] = []
        replay_run_ids = (
            sorted(set(period_scope.values()))
            if period_scope
            else sorted(
                {
                    str(run_id).strip()
                    for _dataset_id, run_id, _device_id, _observed_at, _structured in rows
                    if str(run_id or "").strip()
                }
            )
        )
        if not replay_run_ids:
            raise ValueError("selected datasets contain no replayable patrol run ids")
        for dataset_id, run_id, device_id, observed_at, structured in rows:
            group_id = _stable_alias(
                "snapshot",
                f"{dataset_id}|{run_id}|{device_id}|{observed_at}",
            )
            records = _structured_records(structured)
            for record in records:
                if isinstance(record, dict):
                    whitelisted = {}
                    for key, value in record.items():
                        field = str(key)
                        if field == "__record_set__":
                            whitelisted[field] = value
                            continue
                        if field not in allowed_fields:
                            dropped_unapproved[field] = dropped_unapproved.get(field, 0) + 1
                            continue
                        whitelisted[field] = value
                    candidates.append(
                        (
                            str(dataset_id),
                            group_id,
                            _sanitize(
                                whitelisted,
                                removed=removed,
                                pseudonymized=pseudonymized,
                            ),
                        )
                    )

        # Round-robin immutable snapshots, not only datasets. Otherwise the first
        # large device capture in each period can crowd every peer device out.
        records, selected_groups = _round_robin_snapshot_records(
            candidates,
            max_records=max_records,
        )

        if not selected_field:
            selected_field = self._first_comparable_field(
                records,
                semantics=dict(validation.get("state_semantics") or {}),
            )
        if not selected_field:
            selected_field = str(defaults.get("field_name") or "").strip()
        if not selected_field:
            selected_field = self._first_scalar_field(records)
        if selected_field and selected_field not in allowed_fields:
            raise ValueError("rule target field is not in the approved field whitelist")
        if not selected_field:
            raise ValueError("unable to infer a rule target field from structured records")
        if (
            not selected_observation
            and selected_field == str(defaults.get("field_name") or "").strip()
        ):
            selected_observation = str(defaults.get("observation_type") or "").strip()
        historical_values = [
            number for record in records for number in _numeric_scalars(record.get(selected_field))
        ]
        full_field_profiles = profile_fields(
            [record for _dataset_id, _group_id, record in candidates],
            field_inventory=sorted(allowed_fields),
        )
        context = {
            "records": records,
            "record_groups": [
                {"group_id": group_id, "records": group_records}
                for group_id, group_records in sorted(selected_groups.items())
            ],
            # The model sees a bounded sample, while deterministic validation
            # receives statistics from the complete approved structured source.
            "field_profiles": full_field_profiles,
            "field_name": selected_field,
            "observation_type": selected_observation or None,
            "replay_run_ids": replay_run_ids,
            **generation_governance,
        }
        if historical_values:
            context["historical_values"] = historical_values
        privacy = {
            "success": True,
            "raw_cli_included": False,
            "scope_type": scope_type,
            "dataset_run_scopes": [
                {"dataset_id": dataset_id, "run_id": run_id}
                for dataset_id, run_id in (
                    sorted(period_scope.items())
                    if period_scope
                    else sorted(
                        {(str(dataset_id), str(run_id)) for dataset_id, run_id, *_rest in rows}
                    )
                )
            ],
            "source_projection": [
                "dataset_id",
                "run_id",
                "device_id",
                "observed_at",
                "structured_json",
            ],
            "source_snapshot_count": len(rows),
            "capture_snapshot_count": source_counts["capture"],
            "reparse_snapshot_count": source_counts["reparse"],
            "source_resolution": "latest_successful_reparse_then_capture",
            "candidate_record_count": len(candidates),
            "sampled_record_count": len(records),
            "profiled_record_count": len(candidates),
            "sampling_strategy": "snapshot_round_robin_with_value_coverage_v2",
            "approved_field_whitelist": sorted(allowed_fields),
            "dropped_unapproved_fields": dropped_unapproved,
            "removed_fields": removed,
            "pseudonymized_fields": pseudonymized,
            "source_scope_digest": source_scope["scope_digest"],
            "source_scope_manifest": source_scope["manifest"],
        }
        digest = rule_generation_context_digest(
            job_id=job_id,
            dataset_ids=dataset_ids,
            context=context,
            privacy=privacy,
        )
        with get_db_session() as db:
            current_scope = require_immutable_rule_asset_source_datasets(
                db,
                dataset_ids,
            )
            if current_scope["scope_digest"] != source_scope["scope_digest"]:
                raise ValueError(
                    "offline source evidence changed while the generation context "
                    "was being prepared"
                )
            existing = (
                db.query(RuleGenerationContextRecord)
                .filter(RuleGenerationContextRecord.context_digest == digest)
                .first()
            )
            if existing:
                row = existing
            else:
                row = RuleGenerationContextRecord(
                    id=f"rule-context-{uuid.uuid4().hex[:20]}",
                    job_id=job_id,
                    offline_job_id=offline_job_ids[0] if len(offline_job_ids) == 1 else None,
                    dataset_ids_json=dataset_ids,
                    observation_type=selected_observation or None,
                    field_name=selected_field,
                    context_json=context,
                    context_digest=digest,
                    privacy_report_json=privacy,
                    created_by=created_by,
                    created_at=_now(),
                )
                db.add(row)
                db.flush()
            require_current_rule_generation_context(
                db,
                row,
                expected_context_digest=digest,
                current_source_scope=current_scope,
            )
            return {
                "success": True,
                "context_id": row.id,
                "context_digest": row.context_digest,
                "dataset_ids": list(row.dataset_ids_json or []),
                "context": dict(row.context_json or {}),
                "privacy": dict(row.privacy_report_json or {}),
            }

    def load(self, *, context_id: str, job_id: str) -> dict[str, Any]:
        with get_db_session() as db:
            row = db.get(RuleGenerationContextRecord, context_id)
            if not row or row.job_id != job_id:
                raise KeyError("rule generation context not found")
            integrity = require_current_rule_generation_context(
                db,
                row,
                expected_context_digest=str(row.context_digest or ""),
            )
            return {
                "context_id": row.id,
                "context_digest": row.context_digest,
                "dataset_ids": list(row.dataset_ids_json or []),
                "context": dict(row.context_json or {}),
                "privacy": dict(row.privacy_report_json or {}),
                "source_integrity": integrity,
            }

    @staticmethod
    def _first_scalar_field(records: list[dict[str, Any]]) -> str:
        excluded = {
            "__record_set__",
            "interface",
            "name",
            "id",
            "index",
            "description",
        }
        counts: dict[str, int] = {}
        for record in records:
            for key, value in record.items():
                if key.lower() in excluded or isinstance(value, (dict, list)) or value is None:
                    continue
                counts[key] = counts.get(key, 0) + 1
        return max(counts, key=counts.get) if counts else ""

    @staticmethod
    def _first_comparable_field(
        records: list[dict[str, Any]],
        *,
        semantics: dict[str, Any],
    ) -> str:
        comparable = {
            "boolean",
            "counter",
            "enum",
            "gauge",
            "percentage",
            "raw_hash",
            "state",
        }
        non_targets = {"dimension", "identity", "metadata", "reference", "threshold"}
        populated = {
            str(field)
            for record in records
            if isinstance(record, dict)
            for field, value in record.items()
            if value not in (None, "", [], {})
        }
        for field, raw_metadata in semantics.items():
            if not isinstance(raw_metadata, dict) or str(field) not in populated:
                continue
            metadata = dict(raw_metadata)
            semantic_type = str(metadata.get("semantic_type") or "").casefold()
            comparison_role = str(metadata.get("comparison_role") or "").casefold()
            if (
                semantic_type in comparable
                and comparison_role not in non_targets
                and metadata.get("ignore") is not True
                and metadata.get("monitor") is not False
            ):
                return str(field)
        return ""

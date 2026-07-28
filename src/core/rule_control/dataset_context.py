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

from src.core.rule_control.planning import profile_fields
from src.core.rule_control.source_scope import require_rule_asset_source_job
from src.infrastructure.db.models import (
    CheckItemVersionRecord,
    CommandSnapshot,
    OfflinePatrolPeriodRecord,
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
    required_records = max(1, int(min_records if min_records is not None else settings.RULE_CONTEXT_MIN_RECORDS))
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
                text = str(nested).strip()
                if text.casefold() in {"--", "none", "unassigned", "unknown"}:
                    output[key] = text
                    continue
                try:
                    parsed_ip = ipaddress.ip_interface(text)
                except ValueError:
                    pseudonymized[lowered] = pseudonymized.get(lowered, 0) + 1
                    output[key] = _stable_alias("identity", nested)
                else:
                    pseudonymized[lowered] = pseudonymized.get(lowered, 0) + 1
                    output[key] = _pseudonymize_ip(
                        parsed_ip,
                        had_prefix="/" in text,
                    )
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


def _approved_field_whitelist(template: ParserTemplate, *, require_approval: bool = True) -> set[str]:
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


def _field_whitelist_relaxed(template: ParserTemplate) -> set[str]:
    try:
        return _approved_field_whitelist(template, require_approval=True)
    except ValueError:
        return _approved_field_whitelist(template, require_approval=False)


def _round_robin_snapshot_records(
    candidates: list[tuple[str, str, dict[str, Any]]],
    *,
    max_records: int,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    grouped: dict[tuple[str, str], deque[dict[str, Any]]] = {}
    for dataset_id, group_id, record in candidates:
        grouped.setdefault((dataset_id, group_id), deque()).append(record)
    records: list[dict[str, Any]] = []
    selected_groups: dict[str, list[dict[str, Any]]] = {}
    group_order = sorted(grouped)
    while len(records) < max_records and any(grouped.values()):
        for group_key in group_order:
            bucket = grouped[group_key]
            if bucket and len(records) < max_records:
                record = bucket.popleft()
                records.append(record)
                selected_groups.setdefault(group_key[1], []).append(record)
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
        )
        .join(
            CheckItemVersionRecord,
            CheckItemVersionRecord.id
            == RuleAssetCommandUnitCheckItemRecord.check_item_version_id,
        )
        .filter(
            RuleAssetCommandUnitCheckItemRecord.command_unit_id.in_(unit_ids)
        )
        .order_by(
            RuleAssetCommandUnitCheckItemRecord.check_item_id,
            RuleAssetCommandUnitCheckItemRecord.implementation_version_id,
        )
        .all()
    )
    targets: list[dict[str, Any]] = []
    seen_links: set[tuple[str, str]] = set()
    for link, version in rows:
        key = (
            str(link.check_item_version_id),
            str(link.implementation_version_id),
        )
        if key in seen_links:
            continue
        seen_links.add(key)
        canonical_contract = dict(version.canonical_contract_json or {})
        targets.append(
            {
                "check_item_id": str(link.check_item_id),
                "check_item_version_id": str(link.check_item_version_id),
                "implementation_id": str(link.implementation_id),
                "implementation_version_id": str(
                    link.implementation_version_id
                ),
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
                    dict(item)
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
    return {
        "check_item_targets": targets,
        "observation_targets": observation_targets,
        "strict_observation_targets": bool(observation_targets),
        "canonical_identity_constraint": canonical_identity_constraint,
        "target_resolution": (
            "resolved"
            if targets and len(identity_constraints) <= 1
            else "multi_entity_partition_required"
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
        max_records: int = 120,
        field_name: str | None = None,
        observation_type: str | None = None,
    ) -> dict[str, Any]:
        dataset_ids = sorted({str(item).strip() for item in dataset_ids if str(item).strip()})
        if not dataset_ids:
            raise ValueError("select at least one offline patrol dataset")
        max_records = max(1, min(int(max_records), 500))
        with get_db_session() as db:
            job = db.get(RuleGenerationJobRecord, job_id)
            if not job:
                raise KeyError("rule generation job not found")
            payload = dict(job.payload_json or {})
            defaults = dict(payload.get("workflow_context_defaults") or {})
            template_id = str(payload.get("template_id") or "").strip()
            if not template_id:
                raise ValueError("rule generation job is not linked to a parser template")
            template = db.get(ParserTemplate, template_id)
            if not template or template.status != "active":
                raise ValueError("rule generation job parser template is not active")
            allowed_fields = _approved_field_whitelist(template)
            validation = dict(template.validation_json or {})
            selected_field = str(field_name or "").strip()
            selected_observation = str(observation_type or "").strip()
            period_rows = (
                db.query(
                    OfflinePatrolPeriodRecord.dataset_id,
                    OfflinePatrolPeriodRecord.patrol_run_id,
                    OfflinePatrolPeriodRecord.job_id,
                )
                .filter(OfflinePatrolPeriodRecord.dataset_id.in_(dataset_ids))
                .all()
            )
            if not period_rows:
                raise ValueError(
                    "rule generation datasets must come from a Parser control asset-source job"
                )
            source_job_ids = sorted({str(item[2]) for item in period_rows})
            for source_job_id in source_job_ids:
                require_rule_asset_source_job(db, source_job_id)
            period_scope = {
                str(dataset_id): str(run_id or "").strip()
                for dataset_id, run_id, _job_id in period_rows
            }
            missing_periods = sorted(set(dataset_ids) - set(period_scope))
            if missing_periods:
                raise ValueError(
                    "offline dataset selection mixes governed and ungoverned periods: "
                    + ", ".join(missing_periods)
                )
            missing_runs = sorted(
                dataset_id for dataset_id, run_id in period_scope.items() if not run_id
            )
            if missing_runs:
                raise ValueError(
                    "offline patrol periods have no authoritative patrol run: "
                    + ", ".join(missing_runs)
                )
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
            offline_job_ids = sorted({str(job_id) for _dataset_id, _run_id, job_id in period_rows})
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
        context = {
            "records": records,
            "record_groups": [
                {"group_id": group_id, "records": group_records}
                for group_id, group_records in sorted(selected_groups.items())
            ],
            "field_profiles": profile_fields(
                records,
                field_inventory=sorted(allowed_fields),
            ),
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
            "approved_field_whitelist": sorted(allowed_fields),
            "dropped_unapproved_fields": dropped_unapproved,
            "removed_fields": removed,
            "pseudonymized_fields": pseudonymized,
        }
        digest_payload = {
            "job_id": job_id,
            "dataset_ids": dataset_ids,
            "context": context,
            "privacy": privacy,
        }
        digest = hashlib.sha256(
            json.dumps(
                digest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        with get_db_session() as db:
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
            return {
                "success": True,
                "context_id": row.id,
                "context_digest": row.context_digest,
                "dataset_ids": list(row.dataset_ids_json or []),
                "context": dict(row.context_json or {}),
                "privacy": dict(row.privacy_report_json or {}),
            }

    def prepare_from_parser_template(
        self,
        *,
        job_id: str,
        created_by: str | None = None,
        max_records: int = 120,
    ) -> dict[str, Any]:
        """Build immutable context from structured snapshots for an activated template."""
        max_records = max(1, min(int(max_records), 500))
        with get_db_session() as db:
            job = db.get(RuleGenerationJobRecord, job_id)
            if not job:
                raise KeyError("rule generation job not found")
            payload = dict(job.payload_json or {})
            defaults = dict(payload.get("workflow_context_defaults") or {})
            template_id = str(payload.get("template_id") or "").strip()
            if not template_id:
                raise ValueError("rule generation job is not linked to a parser template")
            template = db.get(ParserTemplate, template_id)
            if not template or template.status != "active":
                raise ValueError("rule generation job parser template is not active")
            allowed_fields = _field_whitelist_relaxed(template)
            validation = dict(template.validation_json or {})
            generation_governance = _check_item_generation_governance(
                db,
                template=template,
            )
            vendor = str(template.vendor or "").strip()
            command = str(template.canonical_command or "").strip()
            if not vendor or not command:
                raise ValueError("parser template missing vendor or canonical_command")
            rows = (
                db.query(CommandSnapshot)
                .filter(
                    func.lower(CommandSnapshot.vendor) == vendor.lower(),
                    func.lower(CommandSnapshot.canonical_command) == command.lower(),
                    CommandSnapshot.parse_status.in_(("parsed", "success")),
                    CommandSnapshot.structured_json.isnot(None),
                )
                .order_by(CommandSnapshot.observed_at.desc())
                .limit(max(50, max_records * 3))
                .all()
            )
            if not rows:
                # Prefer SnapshotParseResult join path when snapshots lack structured_json
                from src.infrastructure.db.models import SnapshotParseResult

                parse_rows = (
                    db.query(CommandSnapshot, SnapshotParseResult)
                    .join(
                        SnapshotParseResult,
                        SnapshotParseResult.snapshot_id == CommandSnapshot.id,
                    )
                    .filter(
                        func.lower(CommandSnapshot.vendor) == vendor.lower(),
                        func.lower(CommandSnapshot.canonical_command) == command.lower(),
                        SnapshotParseResult.structured_json.isnot(None),
                    )
                    .order_by(CommandSnapshot.observed_at.desc())
                    .limit(max(50, max_records * 3))
                    .all()
                )
                rows = []
                for snapshot, parse in parse_rows:
                    snapshot.structured_json = parse.structured_json
                    rows.append(snapshot)
            if not rows:
                raise ValueError(
                    "no structured snapshots available for parser template context"
                )

            removed: dict[str, int] = {}
            pseudonymized: dict[str, int] = {}
            dropped_unapproved: dict[str, int] = {}
            candidates: list[tuple[str, str, dict[str, Any]]] = []
            replay_run_ids: list[str] = []
            dataset_id = f"parser_template:{template_id}"
            for snapshot in rows:
                run_id = str(snapshot.run_id or "").strip()
                if run_id and run_id not in replay_run_ids:
                    replay_run_ids.append(run_id)
                group_id = _stable_alias(
                    "snapshot",
                    f"{dataset_id}|{snapshot.run_id}|{snapshot.device_id}|{snapshot.observed_at}",
                )
                structured = snapshot.structured_json
                records = _structured_records(structured)
                for record in records:
                    if not isinstance(record, dict):
                        continue
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
                            dataset_id,
                            group_id,
                            _sanitize(
                                whitelisted,
                                removed=removed,
                                pseudonymized=pseudonymized,
                            ),
                        )
                    )
            records, selected_groups = _round_robin_snapshot_records(
                candidates,
                max_records=max_records,
            )
            if not records:
                raise ValueError("sanitized records are empty for parser template context")
            selected_field = self._first_comparable_field(
                records,
                semantics=dict(validation.get("state_semantics") or {}),
            ) or str(defaults.get("field_name") or "").strip() or self._first_scalar_field(records)
            if not selected_field:
                raise ValueError("unable to infer a rule target field from structured records")
            selected_observation = str(defaults.get("observation_type") or "").strip() or None
            historical_values = [
                number
                for record in records
                for number in _numeric_scalars(record.get(selected_field))
            ]
            context = {
                "records": records,
                "record_groups": [
                    {"group_id": group_id, "records": group_records}
                    for group_id, group_records in sorted(selected_groups.items())
                ],
                "field_profiles": profile_fields(
                    records,
                    field_inventory=sorted(allowed_fields),
                ),
                "field_name": selected_field,
                "observation_type": selected_observation,
                "replay_run_ids": replay_run_ids,
                **generation_governance,
            }
            if historical_values:
                context["historical_values"] = historical_values
            privacy = {
                "success": True,
                "raw_cli_included": False,
                "scope_type": "parser_template_snapshots",
                "template_id": template_id,
                "source_snapshot_count": len(rows),
                "sampled_record_count": len(records),
                "approved_field_whitelist": sorted(allowed_fields),
                "dropped_unapproved_fields": dropped_unapproved,
                "removed_fields": removed,
                "pseudonymized_fields": pseudonymized,
            }
            quality = assess_context_quality(records, selected_field)
            privacy["quality"] = quality
            digest_payload = {
                "job_id": job_id,
                "dataset_ids": [dataset_id],
                "context": context,
                "privacy": privacy,
            }
            digest = hashlib.sha256(
                json.dumps(
                    digest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
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
                    offline_job_id=None,
                    dataset_ids_json=[dataset_id],
                    observation_type=selected_observation,
                    field_name=selected_field,
                    context_json=context,
                    context_digest=digest,
                    privacy_report_json=privacy,
                    created_by=created_by or "parser-template-activation",
                    created_at=_now(),
                )
                db.add(row)
                db.flush()
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
            privacy = dict(row.privacy_report_json or {})
            scope_type = privacy.get("scope_type")
            if scope_type == "parser_template_snapshots":
                return {
                    "context_id": row.id,
                    "context_digest": row.context_digest,
                    "dataset_ids": list(row.dataset_ids_json or []),
                    "context": dict(row.context_json or {}),
                    "privacy": privacy,
                }
            if not row.offline_job_id or scope_type != (
                "offline_period_authoritative_runs"
            ):
                raise ValueError("rule generation context is not backed by an offline patrol job")
            require_rule_asset_source_job(db, str(row.offline_job_id))
            return {
                "context_id": row.id,
                "context_digest": row.context_digest,
                "dataset_ids": list(row.dataset_ids_json or []),
                "context": dict(row.context_json or {}),
                "privacy": privacy,
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

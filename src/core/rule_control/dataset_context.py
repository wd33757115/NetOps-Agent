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

from sqlalchemy import and_, or_

from src.core.rule_control.planning import profile_fields
from src.infrastructure.db.models import (
    CommandSnapshot,
    OfflinePatrolPeriodRecord,
    ParserTemplate,
    RuleGenerationContextRecord,
    RuleGenerationJobRecord,
)
from src.infrastructure.db.postgres import get_db_session

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


def _approved_field_whitelist(template: ParserTemplate) -> set[str]:
    validation = dict(template.validation_json or {})
    if validation.get("contract_approval_status") != "approved":
        raise ValueError("parser template field contract is not approved")
    fields = {
        str(field).strip()
        for key in ("primary_keys", "required_fields", "optional_fields")
        for field in (validation.get(key) or [])
        if str(field).strip()
    }
    if not fields:
        raise ValueError("parser template has no approved field whitelist")
    return fields


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
            period_scope = {
                str(dataset_id): str(run_id or "").strip()
                for dataset_id, run_id, _job_id in period_rows
            }
            if period_rows:
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
            else:
                snapshot_scope = CommandSnapshot.dataset_id.in_(dataset_ids)
                scope_type = "dataset_snapshot_runs"

            rows = (
                db.query(
                    CommandSnapshot.dataset_id,
                    CommandSnapshot.run_id,
                    CommandSnapshot.device_id,
                    CommandSnapshot.observed_at,
                    CommandSnapshot.structured_json,
                )
                .filter(
                    snapshot_scope,
                    CommandSnapshot.parser_template_id == template_id,
                    CommandSnapshot.parse_status == "parsed",
                    CommandSnapshot.structured_json.is_not(None),
                )
                .order_by(
                    CommandSnapshot.dataset_id,
                    CommandSnapshot.observed_at,
                    CommandSnapshot.id,
                )
                .all()
            )
            offline_job_ids = sorted(
                {str(job_id) for _dataset_id, _run_id, job_id in period_rows}
            )
        if not rows:
            raise ValueError("selected datasets contain no parsed records for this template")

        if period_scope:
            represented_datasets = {str(row[0]) for row in rows}
            missing_datasets = sorted(set(dataset_ids) - represented_datasets)
            if missing_datasets:
                raise ValueError(
                    "authoritative offline patrol runs contain no parsed records for this "
                    "template: "
                    + ", ".join(missing_datasets)
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
            records = structured if isinstance(structured, list) else [structured]
            for record in records:
                if isinstance(record, dict):
                    whitelisted = {}
                    for key, value in record.items():
                        field = str(key)
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
        if not selected_observation and selected_field == str(
            defaults.get("field_name") or ""
        ).strip():
            selected_observation = str(defaults.get("observation_type") or "").strip()
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
            "observation_type": selected_observation or None,
            "replay_run_ids": replay_run_ids,
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
                        {
                            (str(dataset_id), str(run_id))
                            for dataset_id, run_id, *_rest in rows
                        }
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

    def load(self, *, context_id: str, job_id: str) -> dict[str, Any]:
        with get_db_session() as db:
            row = db.get(RuleGenerationContextRecord, context_id)
            if not row or row.job_id != job_id:
                raise KeyError("rule generation context not found")
            return {
                "context_id": row.id,
                "context_digest": row.context_digest,
                "dataset_ids": list(row.dataset_ids_json or []),
                "context": dict(row.context_json or {}),
                "privacy": dict(row.privacy_report_json or {}),
            }

    @staticmethod
    def _first_scalar_field(records: list[dict[str, Any]]) -> str:
        excluded = {"interface", "name", "id", "index", "description"}
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

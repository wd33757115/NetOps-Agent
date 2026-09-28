"""Durable offline patrol import and historical comparison orchestration."""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import logging
import re
import shutil
import tempfile
import uuid
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import defer, load_only

from src.core.check_items.command_adapter import is_direct_projection_adapter
from src.core.check_items.numeric_health import policy_rule
from src.core.check_items.report_contract import OFFLINE_HISTORICAL
from src.core.check_items.runtime_bundle_assembly import RuntimeBundleAssemblyPlanner
from src.core.patrol.change_event_repository import ChangeEventRepository
from src.core.patrol.event_rules import EventRuleRepository
from src.core.patrol.leadership_report import (
    build_leadership_report,
    render_leadership_docx,
)
from src.core.patrol.offline_job_lock import idle_job
from src.core.patrol.raw_importer import import_raw_path, preview_raw_path
from src.core.patrol.reporting import render_patrol_batch_xlsx
from src.core.patrol.repository import PatrolRepository
from src.core.state.observation_applicability import APPLICABILITY_CONTEXT_KEY
from src.core.workflows.patrol_batch_service import (
    domain_checks,
    evaluate_current_state,
    quality_from_run_data,
    report_domain,
)
from src.infrastructure.db.models import (
    ChangePolicyVersionRecord,
    CheckItemImplementationLinkRecord,
    CollectionImplementationParserBindingRecord,
    CollectionImplementationVersionRecord,
    EventRuleVersionRecord,
    FileMetadata,
    NetworkDevice,
    OfflinePatrolDeviceBindingRecord,
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    OfflinePatrolSourceFileRecord,
    ParserTemplate,
    PatrolAnalysisAttemptRecord,
    StateObservationRecord,
    WorkflowRun,
    WorkflowStepRecord,
)
from src.infrastructure.db.postgres import get_db_session
from src.infrastructure.storage.minio_client import get_minio_storage
from src.storage.object_resolver import resolve_object_key

log = logging.getLogger(__name__)

JOB_PURPOSES = {"offline_patrol", "rule_asset_source"}
JOB_TYPES = {"parser_validation", "single_patrol", "history_compare"}
JOB_ACTIVE_STATUSES = {"previewing", "queued", "running"}
JOB_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
INGESTION_BLOCKER_CODES = {
    "empty_unverified",
    "file_invalid",
    "identity_unknown",
    "no_common_device",
}
PATROL_RUNTIME_BLOCKER_CODES = INGESTION_BLOCKER_CODES | {
    "ambiguous_device_binding",
    "no_common_ready_parser",
    "parser_template_inactive",
    "template_missing",
    "parse_failed",
    "observation_contract_missing",
    "change_policy_missing",
    "event_rule_missing",
    "command_adapter_projection_missing",
    "runtime_bundle_missing",
    "runtime_bundle_not_ready",
}
HISTORICAL_DEGRADATION_WARNING_CODES = {
    "parser_template_inactive",
    "template_missing",
    "parse_failed",
    "observation_contract_missing",
    "change_policy_missing",
    "event_rule_missing",
    "command_adapter_projection_missing",
    "runtime_bundle_missing",
    "runtime_bundle_not_ready",
}
PERIOD_ROLES = {
    "parser_validation": ("sample",),
    "single_patrol": ("current",),
    "history_compare": ("baseline", "current"),
}
ALLOWED_SOURCE_SUFFIXES = {".txt", ".log", ".zip"}
MAX_ARCHIVE_FILES = 2000
MAX_ARCHIVE_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 500 * 1024 * 1024
PREVIEW_STATUS_KEYS = (
    "ready",
    "template_missing",
    "parse_failed",
    "ignored",
    "raw_compare",
    "identity_unknown",
    "empty_unverified",
    "command_error",
    "truncated",
    "paged",
)
PREVIEW_TOTAL_KEYS = (
    "file_count",
    "device_count",
    "command_count",
    *PREVIEW_STATUS_KEYS,
    "duplicate_snapshots",
    "new_snapshots",
)
RESULT_EXPORT_FIELDS = {
    "changes": (
        "change_id",
        "run_id",
        "device_id",
        "command",
        "entity_type",
        "entity_key",
        "field",
        "observation_type",
        "old",
        "new",
        "change_type",
        "event_status",
        "decision_reason",
        "observed_at",
        "detected_at",
    ),
    "events": (
        "occurrence_id",
        "event_id",
        "run_id",
        "source_change_id",
        "occurrence_kind",
        "event_type",
        "severity",
        "check_item_id",
        "device_id",
        "entity_type",
        "entity_key",
        "status_after_occurrence",
        "observed_at",
    ),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _preview_device_totals(previews: dict[str, Any]) -> tuple[int, int]:
    """Return physical device coverage and device-period observations."""

    identities: set[str] = set()
    fallback_count = 0
    device_period_count = 0
    for preview in previews.values():
        device_period_count += int(preview.get("device_count") or 0)
        files = list(preview.get("files") or [])
        period_identities: set[str] = set()
        for item in files:
            identity = next(
                (
                    str(item.get(key) or "").strip().casefold()
                    for key in (
                        "inventory_device_id",
                        "management_ip",
                        "device_id",
                        "device_name",
                    )
                    if str(item.get(key) or "").strip()
                ),
                "",
            )
            if identity:
                period_identities.add(identity)
        identities.update(period_identities)
        fallback_count += max(0, int(preview.get("device_count") or 0) - len(period_identities))
    return len(identities) + fallback_count, device_period_count


def _normalized_preview(preview: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(preview or {})
    periods = dict(result.get("periods") or {})
    totals = dict(result.get("totals") or {})
    if periods:
        unique_device_count, device_period_count = _preview_device_totals(periods)
        totals["device_count"] = unique_device_count
        totals["device_period_count"] = device_period_count
    result["totals"] = totals
    return result


def _execution_counts(status: str, target_count: int) -> dict[str, int | float]:
    """Each target belongs to exactly one execution state, never failed AND skipped."""
    completed = target_count if status == "completed" else 0
    launched = status in JOB_ACTIVE_STATUSES | JOB_TERMINAL_STATUSES
    return {
        "target_count": target_count,
        "launched_count": target_count if launched else 0,
        "completed_count": completed,
        "failed_count": target_count if status == "failed" else 0,
        "cancelled_count": target_count if status == "cancelled" else 0,
        "active_count": target_count if status in JOB_ACTIVE_STATUSES else 0,
        "skipped_count": 0 if launched else target_count,
        "preflight_failed_count": 0,
        "execution_coverage_rate": completed / target_count if target_count else 0.0,
    }


def _recount_period_preview(preview: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(preview)
    counts = {key: 0 for key in PREVIEW_STATUS_KEYS}
    for file_item in result.get("files") or []:
        file_counts: dict[str, int] = {}
        for command in file_item.get("commands") or []:
            status = str(command.get("status") or "")
            if status in counts:
                counts[status] += 1
            if status:
                file_counts[status] = file_counts.get(status, 0) + 1
        file_item["statuses"] = file_counts
    result.update(counts)
    return result


def _aggregate_preview_totals(previews: dict[str, Any]) -> dict[str, int]:
    totals = {
        key: sum(int(preview.get(key) or 0) for preview in previews.values())
        for key in PREVIEW_TOTAL_KEYS
    }
    unique_device_count, device_period_count = _preview_device_totals(previews)
    totals["device_count"] = unique_device_count
    totals["device_period_count"] = device_period_count
    return totals


def _apply_output_dispositions(
    previews: dict[str, Any],
    dispositions: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply immutable, job-local empty-output decisions to a preview copy."""

    decisions_by_id = {
        str(item.get("review_item_id") or ""): dict(item)
        for item in dispositions
        if isinstance(item, dict)
        and item.get("review_item_id")
        and item.get("decision") == "ignore_valid_empty"
    }
    result = copy.deepcopy(previews)
    review_items: list[dict[str, Any]] = []
    current_ids: set[str] = set()
    for role, preview in result.items():
        for file_item in preview.get("files") or []:
            relative_path = str(
                file_item.get("relative_path")
                or Path(str(file_item.get("file_path") or "capture.txt")).name
            )
            for command in file_item.get("commands") or []:
                original_status = str(
                    command.get("original_status") or command.get("status") or ""
                )
                review_item_id = str(command.get("review_item_id") or "")
                if original_status != "empty_unverified" or not review_item_id:
                    continue
                current_ids.add(review_item_id)
                item = {
                    "review_item_id": review_item_id,
                    "period_role": role,
                    "source_relative_path": relative_path,
                    "device_id": file_item.get("device_id"),
                    "device_name": file_item.get("device_name"),
                    "management_ip": file_item.get("management_ip"),
                    "canonical_command": command.get("command"),
                    "start_line": command.get("start_line"),
                    "end_line": command.get("end_line"),
                    "raw_text_hash": command.get("raw_text_hash"),
                    "raw_text_length": int(command.get("raw_text_length") or 0),
                    "status": "empty_unverified",
                }
                command["original_status"] = "empty_unverified"
                disposition = decisions_by_id.get(review_item_id)
                if disposition:
                    command["status"] = "ignored"
                    command["output_disposition"] = disposition
                else:
                    command["status"] = "empty_unverified"
                    command.pop("output_disposition", None)
                    review_items.append(item)
        result[role] = _recount_period_preview(preview)
    active_dispositions = []
    for raw in dispositions:
        item = dict(raw)
        item["applies_to_current_preview"] = (
            str(item.get("review_item_id") or "") in current_ids
        )
        active_dispositions.append(item)
    review_items.sort(
        key=lambda item: (
            str(item.get("period_role") or ""),
            str(item.get("device_name") or ""),
            str(item.get("canonical_command") or ""),
            str(item.get("source_relative_path") or ""),
        )
    )
    return result, review_items, active_dispositions


def _rank_inventory_matches(
    inventory: list[NetworkDevice],
    *,
    device_name: str,
    management_ip: str | None,
) -> list[tuple[NetworkDevice, float, str]]:
    normalized_name = str(device_name or "").strip().casefold()
    normalized_ip = str(management_ip or "").strip()
    matches: list[tuple[NetworkDevice, float, str]] = []
    for device in inventory:
        same_name = (
            bool(normalized_name)
            and str(device.device_name or "").strip().casefold() == normalized_name
        )
        same_ip = bool(
            normalized_ip
            and str(device.management_ip or "").strip() == normalized_ip
        )
        if same_name and same_ip:
            matches.append((device, 1.0, "name_and_ip"))
        elif same_ip:
            matches.append((device, 0.95, "management_ip"))
        elif same_name:
            matches.append((device, 0.85, "device_name"))
    matches.sort(key=lambda item: (-item[1], str(item[0].id)))
    return matches


def _inventory_match_index(inventory: list[NetworkDevice]) -> dict[tuple[str, str], list[NetworkDevice]]:
    index: dict[tuple[str, str], list[NetworkDevice]] = {}
    for device in inventory:
        name = str(device.device_name or "").strip().casefold()
        ip = str(device.management_ip or "").strip()
        if name:
            index.setdefault(("name", name), []).append(device)
        if ip:
            index.setdefault(("ip", ip), []).append(device)
    return index


def _indexed_inventory_candidates(index, *, device_name, management_ip):
    matches = [
        *index.get(("name", str(device_name or "").strip().casefold()), []),
        *index.get(("ip", str(management_ip or "").strip()), []),
    ]
    return list({str(device.id): device for device in matches}.values())


def _serialize_device_binding(
    binding: OfflinePatrolDeviceBindingRecord,
    *,
    inventory: list[NetworkDevice],
) -> dict[str, Any]:
    result = _json_safe(binding.to_dict())
    result["match_candidates"] = [
        {
            "inventory_device_id": str(device.id),
            "device_name": str(device.device_name or ""),
            "management_ip": str(device.management_ip or "") or None,
            "vendor": str(device.vendor or "") or None,
            "model": str(device.model or "") or None,
            "confidence": confidence,
            "match_reason": reason,
        }
        for device, confidence, reason in _rank_inventory_matches(
            inventory,
            device_name=binding.device_name,
            management_ip=binding.management_ip,
        )
    ]
    return result


def _serialize_offline_change(
    item: dict[str, Any],
    *,
    binding_names: dict[str, str],
) -> dict[str, Any]:
    """Preserve the full comparison evidence needed by offline reports."""

    device_id = str(item.get("device_id") or "")
    return {
        "id": item.get("change_id"),
        "run_id": item.get("run_id"),
        "device_id": item.get("device_id"),
        "device_name": binding_names.get(device_id, device_id),
        "command": item.get("command"),
        "entity_type": item.get("entity_type"),
        "entity_key": item.get("entity_key"),
        "dimensions": dict(item.get("dimensions") or {}),
        "field": item.get("field"),
        "observation_type": item.get("observation_type"),
        "old_value": item.get("old"),
        "new_value": item.get("new"),
        "change_type": item.get("change_type"),
        "event_status": item.get("event_status") or "unknown",
        "no_event_reason": item.get("decision_reason") or "unknown",
        "comparison_type": item.get("comparison_type"),
        "previous_snapshot_id": item.get("previous_snapshot_id"),
        "current_snapshot_id": item.get("current_snapshot_id"),
        "previous_observation_id": item.get("previous_observation_id"),
        "current_observation_id": item.get("current_observation_id"),
        "change_policy_id": item.get("change_policy_id"),
        "change_policy_version_id": item.get("change_policy_version_id"),
        "rule_bundle_digest": item.get("rule_bundle_digest"),
        "policy_snapshot": dict(item.get("policy_snapshot") or {}),
        "detected_at": item.get("detected_at"),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: str, *, fallback: str) -> Path:
    normalized = str(value or fallback).replace("\\", "/").strip("/")
    pure = PurePosixPath(normalized)
    if not normalized or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"unsafe offline patrol relative path: {value}")
    if pure.parts and ":" in pure.parts[0]:
        raise ValueError(f"unsafe offline patrol relative path: {value}")
    return Path(*pure.parts)


def _offline_job_display_name(name: str | None, job_type: str) -> str:
    """Keep the stored audit value intact while hiding obviously corrupted labels."""

    value = str(name or "").strip()
    replacement_count = value.count("?") + value.count("\ufffd")
    corrupted = not value or "\ufffd" in value or (
        replacement_count >= 3 and replacement_count / max(len(value), 1) >= 0.2
    )
    if not corrupted:
        return value
    if job_type == "history_compare":
        quarters = list(dict.fromkeys(item.upper() for item in re.findall(r"Q[1-4]", value, re.I)))
        if len(quarters) >= 2:
            return f"{quarters[0]} → {quarters[1]} 规则回放样本"
        return "双周期规则回放样本"
    return "规则资产样本导入"


def _purpose_aware_display_name(row: OfflinePatrolJobRecord) -> str:
    if row.purpose == "rule_asset_source":
        return _offline_job_display_name(row.name, row.job_type)
    value = str(row.name or "").strip()
    replacement_count = value.count("?") + value.count("\ufffd")
    corrupted = not value or "\ufffd" in value or (
        replacement_count >= 3 and replacement_count / max(len(value), 1) >= 0.2
    )
    if not corrupted:
        return value
    if row.job_type == "history_compare":
        quarters = list(
            dict.fromkeys(item.upper() for item in re.findall(r"Q[1-4]", value, re.I))
        )
        if len(quarters) >= 2:
            return f"{quarters[0]} -> {quarters[1]} 离线巡检对比"
        return "双周期离线巡检对比"
    return "单周期离线巡检"


class OfflinePatrolJobService:
    def create_job(
        self,
        *,
        owner_id: str,
        idempotency_key: str,
        job_type: str,
        name: str,
        periods: list[dict[str, Any]],
        folder_id: str | None,
        purpose: str = "offline_patrol",
        report_intent: str = "historical",
        vendor: str | None = None,
        model: str | None = None,
        publish_events: bool = False,
    ) -> dict[str, Any]:
        purpose = str(purpose or "").strip()
        if purpose not in JOB_PURPOSES:
            raise ValueError(f"unsupported offline job purpose: {purpose}")
        report_intent = str(report_intent or "historical").strip()
        if report_intent not in {"historical", "current_attestation"}:
            raise ValueError(
                f"unsupported offline report intent: {report_intent}"
            )
        if purpose == "rule_asset_source" and report_intent != "historical":
            raise ValueError(
                "rule asset source jobs cannot request current authority"
            )
        job_type = str(job_type or "").strip()
        if job_type not in JOB_TYPES:
            raise ValueError(f"unsupported offline patrol job type: {job_type}")
        allowed_types = (
            {"parser_validation", "history_compare"}
            if purpose == "rule_asset_source"
            else {"single_patrol", "history_compare"}
        )
        if job_type not in allowed_types:
            raise ValueError(f"{job_type} is not valid for {purpose}")
        expected_roles = PERIOD_ROLES[job_type]
        by_role = {str(item.get("role") or ""): item for item in periods}
        if set(by_role) != set(expected_roles):
            raise ValueError(f"{job_type} requires periods: {', '.join(expected_roles)}")
        parsed_periods = {
            role: _as_datetime(by_role[role]["observed_at"]) for role in expected_roles
        }
        if (
            job_type == "history_compare"
            and parsed_periods["baseline"] >= parsed_periods["current"]
        ):
            raise ValueError("baseline observed_at must be earlier than current observed_at")

        stable_key = hashlib.sha256(f"{owner_id}|{idempotency_key}".encode()).hexdigest()
        with get_db_session() as db:
            existing = (
                db.query(OfflinePatrolJobRecord)
                .filter(OfflinePatrolJobRecord.idempotency_key == stable_key)
                .first()
            )
            if existing:
                if (
                    str(existing.report_intent or "historical")
                    != report_intent
                ):
                    raise ValueError(
                        "idempotency key already belongs to a different "
                        "offline report intent"
                    )
                job_id = existing.id
            else:
                job_id = f"offline-{uuid.uuid4().hex[:20]}"
                now = _now()
                db.add(
                    OfflinePatrolJobRecord(
                        id=job_id,
                        idempotency_key=stable_key,
                        purpose=purpose,
                        job_type=job_type,
                        report_intent=report_intent,
                        name=str(name or "规则资产样本任务").strip()[:256],
                        owner_id=owner_id,
                        status="draft",
                        vendor=vendor,
                        model=model,
                        publish_events=bool(publish_events),
                        folder_id=folder_id,
                        progress_json={"phase": "draft", "percent": 0},
                        created_at=now,
                        updated_at=now,
                    )
                )
                for role in expected_roles:
                    item = by_role[role]
                    db.add(
                        OfflinePatrolPeriodRecord(
                            id=f"period-{uuid.uuid4().hex[:20]}",
                            job_id=job_id,
                            role=role,
                            label=str(item.get("label") or role)[:128],
                            observed_at=parsed_periods[role],
                            dataset_id=f"{job_id}-{role}",
                            status="pending",
                            created_at=now,
                            updated_at=now,
                        )
                    )
        return self.get_job(job_id, owner_id=owner_id)

    def list_jobs(
        self,
        *,
        owner_id: str,
        is_admin: bool = False,
        page: int = 1,
        page_size: int = 25,
        search: str | None = None,
        status: str | None = None,
        job_type: str | None = None,
        purpose: str | None = None,
        archived: bool = False,
    ) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))
        with get_db_session() as db:
            query = db.query(OfflinePatrolJobRecord).options(
                defer(OfflinePatrolJobRecord.result_json),
                defer(OfflinePatrolJobRecord.artifacts_json),
            )
            if not is_admin:
                query = query.filter(OfflinePatrolJobRecord.owner_id == owner_id)
            if purpose:
                query = query.filter(OfflinePatrolJobRecord.purpose == purpose)
            query = query.filter(OfflinePatrolJobRecord.archived.is_(bool(archived)))
            if status:
                query = query.filter(OfflinePatrolJobRecord.status == status)
            if job_type:
                query = query.filter(OfflinePatrolJobRecord.job_type == job_type)
            if search:
                pattern = f"%{search.strip()}%"
                query = query.filter(
                    or_(
                        OfflinePatrolJobRecord.id.ilike(pattern),
                        OfflinePatrolJobRecord.name.ilike(pattern),
                    )
                )
            total = query.count()
            rows = (
                query.order_by(
                    OfflinePatrolJobRecord.created_at.desc(),
                    OfflinePatrolJobRecord.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            # Completed legacy results are reconciled when their detail is opened.
            # A list only needs active status; don't lazy-load two large JSON
            # columns for every historical row (N+1 queries on each poll).
            self._sync_workflow_rows(db, rows, include_completed=False)
            items = [self._serialize_job_summary(row) for row in rows]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def check_access(
        self, job_id: str, *, owner_id: str | None = None,
        is_admin: bool = False, purpose: str | None = None,
    ) -> None:
        """Authorize by identity only, without loading task evidence or results."""
        with get_db_session() as db:
            row = db.query(OfflinePatrolJobRecord).options(load_only(
                OfflinePatrolJobRecord.id, OfflinePatrolJobRecord.owner_id,
                OfflinePatrolJobRecord.purpose,
            )).filter(OfflinePatrolJobRecord.id == job_id).one_or_none()
            self._assert_access(row, owner_id=owner_id, is_admin=is_admin)
            if purpose and row.purpose != purpose:
                raise KeyError("offline job not found in this workspace")

    def get_job(
        self,
        job_id: str,
        *,
        owner_id: str | None = None,
        is_admin: bool = False,
        purpose: str | None = None,
    ) -> dict[str, Any]:
        with get_db_session() as db:
            row = db.get(OfflinePatrolJobRecord, job_id)
            self._assert_access(row, owner_id=owner_id, is_admin=is_admin)
            if purpose and row.purpose != purpose:
                raise KeyError("offline job not found in this workspace")
            self._sync_workflow_rows(db, [row])
            periods = (
                db.query(OfflinePatrolPeriodRecord)
                .filter(OfflinePatrolPeriodRecord.job_id == job_id)
                .order_by(OfflinePatrolPeriodRecord.observed_at)
                .all()
            )
            sources = (
                db.query(OfflinePatrolSourceFileRecord)
                .filter(OfflinePatrolSourceFileRecord.job_id == job_id)
                .order_by(
                    OfflinePatrolSourceFileRecord.period_role,
                    OfflinePatrolSourceFileRecord.relative_path,
                )
                .all()
            )
            bindings = (
                db.query(OfflinePatrolDeviceBindingRecord)
                .filter(OfflinePatrolDeviceBindingRecord.job_id == job_id)
                .order_by(
                    OfflinePatrolDeviceBindingRecord.period_role,
                    OfflinePatrolDeviceBindingRecord.device_name,
                )
                .all()
            )
            inventory = (
                db.query(NetworkDevice).options(load_only(
                    NetworkDevice.id, NetworkDevice.device_name, NetworkDevice.management_ip,
                    NetworkDevice.vendor, NetworkDevice.model,
                ))
                .filter(NetworkDevice.status == "active")
                .order_by(NetworkDevice.device_name, NetworkDevice.management_ip)
                .all()
            )
            inventory_index = _inventory_match_index(inventory)
            result = self._serialize_job_summary(row)
            normalized_preview = _normalized_preview(row.preview_json)
            result.update(
                periods=[_json_safe(item.to_dict()) for item in periods],
                source_files=[_json_safe(item.to_dict()) for item in sources],
                device_bindings=[
                    _serialize_device_binding(item, inventory=_indexed_inventory_candidates(
                        inventory_index, device_name=item.device_name, management_ip=item.management_ip,
                    ))
                    for item in bindings
                ],
                preview=_json_safe(normalized_preview),
                output_review_items=_json_safe(
                    normalized_preview.get("output_review_items") or []
                ),
                output_dispositions=_json_safe(
                    normalized_preview.get("output_dispositions") or []
                ),
                result=_json_safe(row.result_json or {}),
                artifacts=_json_safe(row.artifacts_json or {}),
                blockers=_json_safe(row.blockers_json or []),
                error_message=row.error_message,
            )
            return result

    @idle_job
    def attach_source(
        self,
        *,
        job_id: str,
        period_role: str,
        file_id: str,
        relative_path: str,
        sha256: str | None,
        size_bytes: int,
    ) -> dict[str, Any]:
        safe_relative = _safe_relative_path(relative_path, fallback=file_id).as_posix()
        with get_db_session() as db:
            job = db.get(OfflinePatrolJobRecord, job_id)
            if not job:
                raise KeyError("offline patrol job not found")
            if job.status in JOB_ACTIVE_STATUSES:
                raise ValueError("cannot change sources while the job is active")
            period = (
                db.query(OfflinePatrolPeriodRecord)
                .filter(
                    OfflinePatrolPeriodRecord.job_id == job_id,
                    OfflinePatrolPeriodRecord.role == period_role,
                )
                .first()
            )
            if not period:
                raise ValueError(f"period role does not belong to job: {period_role}")
            file_row = db.get(FileMetadata, file_id)
            if not file_row or file_row.is_deleted or file_row.status != "active":
                raise ValueError("attached storage file is not active")
            suffix = Path(safe_relative).suffix.lower()
            if suffix not in ALLOWED_SOURCE_SUFFIXES:
                raise ValueError("offline patrol sources must be .txt, .log, or .zip")
            existing = (
                db.query(OfflinePatrolSourceFileRecord)
                .filter(
                    OfflinePatrolSourceFileRecord.job_id == job_id,
                    OfflinePatrolSourceFileRecord.period_role == period_role,
                    OfflinePatrolSourceFileRecord.file_id == file_id,
                )
                .first()
            )
            if existing:
                source = existing
                source.relative_path = safe_relative
                source.sha256 = sha256 or source.sha256
                source.size_bytes = int(size_bytes or file_row.size_bytes or 0)
                source.status = "attached"
                source.error_message = None
                source.updated_at = _now()
            else:
                source = OfflinePatrolSourceFileRecord(
                    id=f"source-{uuid.uuid4().hex[:20]}",
                    job_id=job_id,
                    period_role=period_role,
                    file_id=file_id,
                    relative_path=safe_relative,
                    sha256=sha256,
                    size_bytes=int(size_bytes or file_row.size_bytes or 0),
                    status="attached",
                    created_at=_now(),
                    updated_at=_now(),
                )
                db.add(source)
            job.status = "uploading"
            job.preview_json = None
            job.blockers_json = None
            job.progress_json = {"phase": "uploading", "percent": 0}
            job.updated_at = _now()
            db.flush()
            return _json_safe(source.to_dict())

    @idle_job
    def remove_source(self, *, job_id: str, source_id: str) -> None:
        with get_db_session() as db:
            job = db.get(OfflinePatrolJobRecord, job_id)
            if not job:
                raise KeyError("offline patrol job not found")
            if job.status in JOB_ACTIVE_STATUSES:
                raise ValueError("cannot change sources while the job is active")
            source = db.get(OfflinePatrolSourceFileRecord, source_id)
            if not source or source.job_id != job_id:
                raise KeyError("offline patrol source not found")
            db.delete(source)
            job.status = "uploading"
            job.preview_json = None
            job.blockers_json = None
            job.updated_at = _now()

    @idle_job
    def mark_preview_queued(self, job_id: str, celery_task_id: str) -> None:
        with get_db_session() as db:
            job = (
                db.query(OfflinePatrolJobRecord)
                .filter(OfflinePatrolJobRecord.id == job_id)
                .with_for_update()
                .one_or_none()
            )
            if not job:
                raise KeyError("offline patrol job not found")
            if job.status in JOB_ACTIVE_STATUSES:
                if job.status == "previewing" and job.celery_task_id == celery_task_id:
                    return
                raise ValueError("offline patrol job is already active")
            job.status = "previewing"
            job.celery_task_id = celery_task_id
            job.error_message = None
            job.progress_json = {"phase": "previewing", "percent": 1}
            job.updated_at = _now()

    def mark_preview_dispatch_failed(self, job_id: str, celery_task_id: str, error: str) -> None:
        with get_db_session() as db:
            job = db.get(OfflinePatrolJobRecord, job_id)
            if job and job.status == "previewing" and job.celery_task_id == celery_task_id:
                job.status = "failed"
                job.error_message = error[:2000]
                job.progress_json = {"phase": "preview_dispatch_failed", "percent": 100}
                job.updated_at = _now()

    def run_preview(self, job_id: str, *, rule_source_task_id: str | None = None, patrol_task_id: str | None = None) -> dict[str, Any]:
        try:
            with get_db_session() as db:
                job = db.get(OfflinePatrolJobRecord, job_id)
                if not job:
                    raise KeyError("offline patrol job not found")
                if patrol_task_id and (job.purpose != "offline_patrol" or job.status != "previewing"
                                       or job.celery_task_id != patrol_task_id):
                    return {"success": False, "ignored": True}
                if job.purpose == "rule_asset_source":
                    if (not rule_source_task_id or job.status != "previewing"
                            or job.celery_task_id != rule_source_task_id):
                        return {"success": False, "ignored": True}
                    from src.apps.rule_governance.backend.access import authorize
                    authorize(job.owner_id)
                if job.purpose == "offline_patrol":
                    from src.apps.inspection.backend.access import authorize
                    authorize(job.owner_id)
                periods = (
                    db.query(OfflinePatrolPeriodRecord)
                    .filter(OfflinePatrolPeriodRecord.job_id == job_id)
                    .order_by(OfflinePatrolPeriodRecord.observed_at)
                    .all()
                )
                vendor, model, job_type, purpose = (
                    job.vendor,
                    job.model,
                    job.job_type,
                    job.purpose,
                )
                existing_output_dispositions = list(
                    (job.preview_json or {}).get("output_dispositions") or []
                )
                period_specs = [(item.role, item.dataset_id) for item in periods]
                if not period_specs:
                    raise ValueError("offline patrol job has no periods")
                source_count = (
                    db.query(OfflinePatrolSourceFileRecord)
                    .filter(OfflinePatrolSourceFileRecord.job_id == job_id)
                    .count()
                )
                if source_count == 0:
                    raise ValueError("upload at least one offline patrol source")

            previews: dict[str, Any] = {}
            for index, (role, dataset_id) in enumerate(period_specs, start=1):
                self._update_progress(
                    job_id,
                    phase=f"preview:{role}",
                    percent=max(2, int((index - 1) / len(period_specs) * 85)),
                )
                with self.materialize_period(job_id, role) as root:
                    previews[role] = preview_raw_path(
                        file_path=root,
                        mode="parser_validation"
                        if purpose == "rule_asset_source" and job_type == "parser_validation"
                        else "production",
                        vendor=vendor,
                        model=model,
                        metadata={
                            "dataset_id": dataset_id,
                            "offline_job_id": job_id,
                            "period_role": role,
                        },
                    )
                self._upsert_device_bindings(
                    job_id,
                    role,
                    previews[role],
                    match_inventory=purpose == "offline_patrol",
                )

            previews, output_review_items, output_dispositions = (
                _apply_output_dispositions(
                    previews,
                    existing_output_dispositions,
                )
            )
            quality_issues = self._build_quality_blockers(job_id, previews)
            blockers, warnings = self._partition_quality_issues(
                quality_issues,
                purpose=purpose,
                job_type=job_type,
            )
            status = "ready" if not blockers else "blocked"
            totals = _aggregate_preview_totals(previews)
            result = {
                "periods": previews,
                "totals": totals,
                "blockers": blockers,
                "warnings": warnings,
                "quality_issues": quality_issues,
                "output_review_items": output_review_items,
                "output_dispositions": output_dispositions,
            }
            with get_db_session() as db:
                job = db.query(OfflinePatrolJobRecord).filter_by(id=job_id).with_for_update().one()
                if patrol_task_id and (job.status != "previewing" or job.celery_task_id != patrol_task_id):
                    return {"success": False, "ignored": True}
                job.status = status
                job.preview_json = result
                job.blockers_json = quality_issues
                job.progress_json = {"phase": status, "percent": 100}
                job.error_message = None
                job.updated_at = _now()
                for period in (
                    db.query(OfflinePatrolPeriodRecord)
                    .filter(OfflinePatrolPeriodRecord.job_id == job_id)
                    .all()
                ):
                    period.status = "previewed"
                    period.result_json = previews.get(period.role)
                    period.updated_at = _now()
            return {"success": True, "job_id": job_id, "status": status, **result}
        except Exception as exc:
            with get_db_session() as db:
                job = db.query(OfflinePatrolJobRecord).filter_by(id=job_id).with_for_update().one_or_none()
                if job and (not patrol_task_id or (job.status == "previewing" and job.celery_task_id == patrol_task_id)):
                    job.status = "failed"
                    job.error_message = f"{type(exc).__name__}: {exc}"
                    job.progress_json = {"phase": "preview_failed", "percent": 100}
                    job.updated_at = _now()
            raise

    @contextmanager
    def materialize_period(self, job_id: str, period_role: str) -> Iterator[Path]:
        with get_db_session() as db:
            sources = (
                db.query(OfflinePatrolSourceFileRecord, FileMetadata)
                .join(FileMetadata, FileMetadata.id == OfflinePatrolSourceFileRecord.file_id)
                .filter(
                    OfflinePatrolSourceFileRecord.job_id == job_id,
                    OfflinePatrolSourceFileRecord.period_role == period_role,
                    FileMetadata.status == "active",
                    FileMetadata.is_deleted.is_(False),
                )
                .order_by(OfflinePatrolSourceFileRecord.relative_path)
                .all()
            )
            detached = [
                {
                    "source_id": source.id,
                    "relative_path": source.relative_path,
                    "expected_sha256": source.sha256,
                    "object_key": resolve_object_key(db, file_row, repair=True),
                    "file_name": file_row.name,
                }
                for source, file_row in sources
            ]
        if not detached:
            raise ValueError(f"period {period_role} has no attached source files")

        storage = get_minio_storage()
        with tempfile.TemporaryDirectory(prefix=f"netops-{job_id}-{period_role}-") as temp:
            root = Path(temp) / "captures"
            downloads = Path(temp) / "downloads"
            root.mkdir(parents=True)
            downloads.mkdir(parents=True)
            expanded_count = 0
            expanded_bytes = 0
            for index, item in enumerate(detached, start=1):
                download_path = downloads / f"{index:04d}-{Path(item['file_name']).name}"
                if not storage.download_to_file(item["object_key"], download_path):
                    raise RuntimeError(f"unable to download attached file: {item['relative_path']}")
                digest = _sha256_file(download_path)
                if item["expected_sha256"] and digest != item["expected_sha256"]:
                    raise ValueError(f"source hash mismatch: {item['relative_path']}")
                self._mark_source_materialized(
                    item["source_id"], digest, download_path.stat().st_size
                )
                suffix = Path(item["relative_path"]).suffix.lower()
                if suffix == ".zip":
                    count, size = self._extract_zip(download_path, root)
                    expanded_count += count
                    expanded_bytes += size
                else:
                    target = root / _safe_relative_path(
                        item["relative_path"], fallback=item["file_name"]
                    )
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(download_path, target)
                    expanded_count += 1
                    expanded_bytes += target.stat().st_size
                if (
                    expanded_count > MAX_ARCHIVE_FILES
                    or expanded_bytes > MAX_ARCHIVE_EXPANDED_BYTES
                ):
                    raise ValueError("offline patrol source exceeds expanded file limits")
            if not any(path.suffix.lower() in {".txt", ".log"} for path in root.rglob("*")):
                raise ValueError(f"period {period_role} contains no txt/log captures")
            yield root

    def import_period(self, job_id: str, period_role: str, *, rule_source_execution_key: str | None = None, patrol_execution_key: str | None = None, patrol_task_id: str | None = None) -> dict[str, Any]:
        with get_db_session() as db:
            job = db.query(OfflinePatrolJobRecord).filter_by(id=job_id).with_for_update().one_or_none()
            if not job:
                raise KeyError("offline patrol job not found")
            period = (
                db.query(OfflinePatrolPeriodRecord)
                .filter(
                    OfflinePatrolPeriodRecord.job_id == job_id,
                    OfflinePatrolPeriodRecord.role == period_role,
                )
                .first()
            )
            if not period:
                raise KeyError("offline patrol period not found")
            if job.purpose == "rule_asset_source":
                if (not rule_source_execution_key or job.status != "running"
                        or job.execution_idempotency_key != rule_source_execution_key):
                    raise ValueError("规则样本导入请通过规则治理应用提交")
                from src.apps.rule_governance.backend.access import authorize
                authorize(job.owner_id)
            if job.purpose == "offline_patrol":
                if (not patrol_execution_key or not patrol_task_id or job.status != "running"
                        or job.workflow_run_id is not None
                        or job.execution_idempotency_key != patrol_execution_key
                        or job.celery_task_id != patrol_task_id):
                    raise ValueError("离线巡检导入请通过巡检固定任务提交")
            run_id = period.patrol_run_id or f"{job_id}-{period_role}-attempt-{job.attempt_number}"
            # Rule-asset sources retain Parser gaps as governed generation
            # evidence and never publish them into the production event stream.
            mode = "parser_validation" if job.purpose == "rule_asset_source" else "production"
            vendor, model, purpose = job.vendor, job.model, job.purpose
            observed_at = period.observed_at.isoformat()
            dataset_id = period.dataset_id
            bindings = (
                db.query(OfflinePatrolDeviceBindingRecord)
                .filter(
                    OfflinePatrolDeviceBindingRecord.job_id == job_id,
                    OfflinePatrolDeviceBindingRecord.period_role == period_role,
                    OfflinePatrolDeviceBindingRecord.match_status == "confirmed",
                    OfflinePatrolDeviceBindingRecord.inventory_device_id.is_not(None),
                )
                .all()
            )
            inventory_bindings = {
                item.external_device_id: item.inventory_device_id for item in bindings
            }
            output_dispositions = [
                dict(item)
                for item in (job.preview_json or {}).get("output_dispositions") or []
                if isinstance(item, dict)
                and item.get("decision") == "ignore_valid_empty"
                and item.get("applies_to_current_preview", True)
            ]
            period.patrol_run_id = run_id
            period.status = "running"
            period.updated_at = _now()
            job.status = "running"
            job.progress_json = {"phase": f"import:{period_role}", "percent": 10}
            job.updated_at = _now()

        with self.materialize_period(job_id, period_role) as root:
            result = import_raw_path(
                file_path=root,
                mode=mode,
                run_id=run_id,
                vendor=vendor,
                model=model,
                observed_at=observed_at,
                inventory_bindings=inventory_bindings,
                metadata={
                    "dataset_id": dataset_id,
                    "dataset_name": f"{job_id} {period_role}",
                    "source": "offline_import",
                    "offline_job_id": job_id,
                    "period_role": period_role,
                    "output_dispositions": output_dispositions,
                    "run_purpose": (
                        "parser_validation"
                        if mode == "parser_validation"
                        else "offline_patrol"
                        if purpose == "offline_patrol"
                        else "rule_asset_replay"
                    ),
                },
            )
        with get_db_session() as db:
            if purpose == "offline_patrol":
                job = db.query(OfflinePatrolJobRecord).filter_by(id=job_id).with_for_update().one()
                if (job.status != "running" or job.execution_idempotency_key != patrol_execution_key
                        or job.celery_task_id != patrol_task_id):
                    raise ValueError("离线巡检已取消或执行凭据已失效")
            period = (
                db.query(OfflinePatrolPeriodRecord)
                .filter(
                    OfflinePatrolPeriodRecord.job_id == job_id,
                    OfflinePatrolPeriodRecord.role == period_role,
                )
                .first()
            )
            period.status = "completed" if result.get("success") else "failed"
            period.result_json = result
            period.updated_at = _now()
        if result.get("success") and purpose == "rule_asset_source":
            from src.core.parser.repository import ParserJobRepository

            result["parser_sample_reconciliation"] = (
                ParserJobRepository().retire_non_authoritative_samples(
                    dataset_id=str(dataset_id),
                    authoritative_run_id=str(run_id),
                    actor="offline-rule-asset-import",
                    reason=(
                        "offline source attempt was superseded by the current "
                        "authoritative PatrolRun"
                    ),
                )
            )
        return result

    def execution_quality_gate(self, job_id: str) -> dict[str, Any]:
        detail = self.get_job(job_id)
        if not detail.get("source_files"):
            return {
                "success": False,
                "job_id": job_id,
                "blockers": [{"code": "no_sources", "message": "未上传规则资产样本文件"}],
            }
        if not detail.get("preview"):
            return {
                "success": False,
                "job_id": job_id,
                "blockers": [{"code": "preview_required", "message": "执行前必须完成预览"}],
            }
        period_previews = dict((detail.get("preview") or {}).get("periods") or {})
        quality_issues = self._build_quality_blockers(job_id, period_previews)
        blockers, warnings = self._partition_quality_issues(
            quality_issues,
            purpose=str(detail.get("purpose") or "offline_patrol"),
            job_type=str(detail.get("job_type") or ""),
        )
        with get_db_session() as db:
            job = db.get(OfflinePatrolJobRecord, job_id)
            if job:
                job.blockers_json = quality_issues
                job.updated_at = _now()
        return {
            "success": not blockers,
            "job_id": job_id,
            "blockers": blockers,
            "warnings": warnings,
            "quality_issues": quality_issues,
        }

    def start_execution(
        self,
        *,
        job_id: str,
        owner_id: str,
        execution_idempotency_key: str,
        is_admin: bool = False,
    ) -> dict[str, Any]:
        # A repeat HTTP request can observe its existing execution even while a
        # worker owns the job lock. Only creation of a new attempt needs it.
        with get_db_session() as db:
            job = db.get(OfflinePatrolJobRecord, job_id)
            if not job:
                raise KeyError("offline patrol job not found")
            if job.owner_id != owner_id and not is_admin:
                raise PermissionError("offline patrol job belongs to another operator")
            if job.execution_idempotency_key == execution_idempotency_key and job.status in (
                JOB_ACTIVE_STATUSES | {"completed"}
            ):
                return {"success": True, "job_id": job_id, "workflow_run_id": job.workflow_run_id,
                        "task_id": job.celery_task_id, "idempotent": True}
        return self._start_execution(
            job_id=job_id, owner_id=owner_id,
            execution_idempotency_key=execution_idempotency_key, is_admin=is_admin,
        )

    @idle_job
    def _start_execution(
        self,
        *,
        job_id: str,
        owner_id: str,
        execution_idempotency_key: str,
        is_admin: bool = False,
    ) -> dict[str, Any]:
        with get_db_session() as db:
            job = db.get(OfflinePatrolJobRecord, job_id)
            if not job:
                raise KeyError("offline patrol job not found")
            if job.owner_id != owner_id and not is_admin:
                raise PermissionError("offline patrol job belongs to another operator")

        gate = self.execution_quality_gate(job_id)
        if not gate["success"]:
            raise ValueError(
                "offline patrol quality gate blocked execution: "
                + "; ".join(
                    str(item.get("message") or item.get("code")) for item in gate["blockers"]
                )
            )
        with get_db_session() as db:
            job = (
                db.query(OfflinePatrolJobRecord)
                .filter(OfflinePatrolJobRecord.id == job_id)
                .with_for_update()
                .one_or_none()
            )
            if not job:
                raise KeyError("offline patrol job not found")
            if job.owner_id != owner_id and not is_admin:
                raise PermissionError("offline patrol job belongs to another operator")
            allowed_types = (
                {"parser_validation", "history_compare"}
                if job.purpose == "rule_asset_source"
                else {"single_patrol", "history_compare"}
            )
            if job.job_type not in allowed_types:
                raise ValueError(f"{job.job_type} is not valid for {job.purpose}")
            if job.purpose == "rule_asset_source" and job.publish_events:
                raise ValueError("rule asset source jobs cannot publish production events")
            if job.execution_idempotency_key == execution_idempotency_key and job.status in (
                JOB_ACTIVE_STATUSES | {"completed"}
            ):
                return {
                    "success": True,
                    "job_id": job_id,
                    "workflow_run_id": job.workflow_run_id,
                    "task_id": job.celery_task_id,
                    "idempotent": True,
                }
            if job.status in JOB_ACTIVE_STATUSES:
                raise ValueError("offline patrol job is already active")
            job.attempt_number = int(job.attempt_number or 0) + 1
            job.execution_idempotency_key = execution_idempotency_key
            periods = (
                db.query(OfflinePatrolPeriodRecord)
                .filter(OfflinePatrolPeriodRecord.job_id == job_id)
                .all()
            )
            by_role = {item.role: item for item in periods}
            for item in periods:
                item.patrol_run_id = f"{job_id}-{item.role}-attempt-{job.attempt_number}"
                item.status = "pending"
                item.result_json = None
                item.updated_at = _now()
            job.status = "queued"
            job.error_message = None
            job.result_json = None
            job.artifacts_json = None
            job.completed_at = None
            job.workflow_run_id = None
            job.celery_task_id = str(uuid.uuid4())
            task_id, purpose = job.celery_task_id, job.purpose
            job.progress_json = {"phase": "queued", "percent": 0}
            job.updated_at = _now()
            required_roles = (
                {"sample"} if job.job_type == "parser_validation"
                else {"current"} if job.job_type == "single_patrol"
                else {"baseline", "current"}
            )
            if set(by_role) != required_roles:
                raise ValueError("离线任务周期与任务类型不一致")

        if purpose == "rule_asset_source":
            from src.apps.rule_governance.backend.source_tasks import dispatch
            dispatched = dispatch(job_id, execution_idempotency_key, task_id)
            execution = "rule_source_import"
        else:
            from src.apps.inspection.backend.offline_tasks import dispatch
            dispatched = dispatch(job_id, execution_idempotency_key, task_id)
            execution = "offline_patrol"
        return {
            "success": True,
            "job_id": job_id,
            "task_id": task_id,
            "workflow_run_id": None,
            "idempotent": False,
            "execution": execution,
            "dispatch_status": "queued" if dispatched else "pending",
        }

    @idle_job
    def confirm_bindings(
        self,
        *,
        job_id: str,
        actor: str,
        bindings: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        with get_db_session() as db:
            job = db.get(OfflinePatrolJobRecord, job_id)
            if not job:
                raise KeyError("offline patrol job not found")
            if job.status in JOB_ACTIVE_STATUSES:
                raise ValueError(
                    "device bindings cannot be changed while the offline job is active"
                )
            for payload in bindings:
                row = db.get(OfflinePatrolDeviceBindingRecord, str(payload.get("binding_id") or ""))
                if not row or row.job_id != job_id:
                    raise KeyError("offline patrol device binding not found")
                inventory_id = payload.get("inventory_device_id")
                if inventory_id and not db.get(NetworkDevice, str(inventory_id)):
                    raise ValueError(f"inventory device not found: {inventory_id}")
                row.inventory_device_id = str(inventory_id) if inventory_id else None
                row.match_status = "confirmed" if inventory_id else "external"
                row.confirmed_by = actor
                row.confirmed_at = _now()
                row.updated_at = _now()
                result.append(_json_safe(row.to_dict()))

        with get_db_session() as db:
            job = db.get(OfflinePatrolJobRecord, job_id)
            preview = dict(job.preview_json or {}) if job else {}
            purpose = str(job.purpose or "offline_patrol") if job else "offline_patrol"
            job_type = str(job.job_type or "") if job else ""
        period_previews = dict(preview.get("periods") or {})
        if period_previews:
            quality_issues = self._build_quality_blockers(job_id, period_previews)
            blockers, warnings = self._partition_quality_issues(
                quality_issues,
                purpose=purpose,
                job_type=job_type,
            )
            next_status = "ready" if not blockers else "blocked"
            preview.update(
                blockers=blockers,
                warnings=warnings,
                quality_issues=quality_issues,
            )
            with get_db_session() as db:
                job = db.get(OfflinePatrolJobRecord, job_id)
                if job:
                    job.status = next_status
                    job.preview_json = preview
                    job.blockers_json = quality_issues
                    job.progress_json = {"phase": next_status, "percent": 100}
                    job.updated_at = _now()
        return result

    @idle_job
    def confirm_output_dispositions(
        self,
        *,
        job_id: str,
        actor: str,
        review_item_ids: list[str],
        reason: str,
    ) -> dict[str, Any]:
        selected_ids = {
            str(item).strip() for item in review_item_ids if str(item).strip()
        }
        normalized_reason = str(reason or "").strip()
        if not selected_ids:
            raise ValueError("select at least one empty output to confirm")
        with get_db_session() as db:
            job = db.get(OfflinePatrolJobRecord, job_id)
            if not job:
                raise KeyError("offline patrol job not found")
            if job.status in JOB_ACTIVE_STATUSES:
                raise ValueError(
                    "output dispositions cannot be changed while the offline job is active"
                )
            preview = copy.deepcopy(job.preview_json or {})
            purpose = str(job.purpose or "offline_patrol")
            job_type = str(job.job_type or "")
            period_previews = dict(preview.get("periods") or {})
            if not period_previews:
                raise ValueError("run preview before confirming empty outputs")
            pending_by_id = {
                str(item.get("review_item_id") or ""): dict(item)
                for item in preview.get("output_review_items") or []
                if isinstance(item, dict) and item.get("review_item_id")
            }
            existing = [
                dict(item)
                for item in preview.get("output_dispositions") or []
                if isinstance(item, dict) and item.get("review_item_id")
            ]
            existing_by_id = {
                str(item.get("review_item_id") or ""): item for item in existing
            }
            unknown_ids = selected_ids - set(pending_by_id) - set(existing_by_id)
            if unknown_ids:
                raise ValueError(
                    "empty-output evidence changed; run preview again before confirming"
                )

            decided_at = _now().isoformat()
            for review_item_id in sorted(selected_ids):
                if review_item_id in existing_by_id:
                    continue
                evidence = pending_by_id[review_item_id]
                decision = {
                    **evidence,
                    "decision": "ignore_valid_empty",
                    "reason": normalized_reason,
                    "decided_by": actor,
                    "decided_at": decided_at,
                }
                existing.append(decision)
                existing_by_id[review_item_id] = decision

        period_previews, review_items, dispositions = _apply_output_dispositions(
            period_previews,
            existing,
        )
        quality_issues = self._build_quality_blockers(job_id, period_previews)
        blockers, warnings = self._partition_quality_issues(
            quality_issues,
            purpose=purpose,
            job_type=job_type,
        )
        next_status = "ready" if not blockers else "blocked"
        preview.update(
            periods=period_previews,
            totals=_aggregate_preview_totals(period_previews),
            blockers=blockers,
            warnings=warnings,
            quality_issues=quality_issues,
            output_review_items=review_items,
            output_dispositions=dispositions,
        )
        with get_db_session() as db:
            job = db.get(OfflinePatrolJobRecord, job_id)
            if not job:
                raise KeyError("offline patrol job not found")
            job.status = next_status
            job.preview_json = preview
            job.blockers_json = quality_issues
            job.progress_json = {"phase": next_status, "percent": 100}
            job.error_message = None
            job.updated_at = _now()
            for period in (
                db.query(OfflinePatrolPeriodRecord)
                .filter(OfflinePatrolPeriodRecord.job_id == job_id)
                .all()
            ):
                period.result_json = period_previews.get(period.role)
                period.updated_at = _now()
        return {
            "success": True,
            "job_id": job_id,
            "status": next_status,
            "confirmed_count": len(selected_ids),
            "output_review_items": review_items,
            "output_dispositions": dispositions,
        }

    @idle_job
    def archive(self, job_id: str, *, archived: bool) -> None:
        with get_db_session() as db:
            job = db.get(OfflinePatrolJobRecord, job_id)
            if not job:
                raise KeyError("offline patrol job not found")
            if job.status in JOB_ACTIVE_STATUSES:
                raise ValueError("active offline patrol jobs cannot be archived")
            job.archived = archived
            job.archived_at = _now() if archived else None
            job.updated_at = _now()

    def cancel(self, job_id: str) -> str | None:
        with get_db_session() as db:
            job = db.query(OfflinePatrolJobRecord).filter_by(id=job_id).with_for_update().one_or_none()
            if not job:
                raise KeyError("offline patrol job not found")
            task_id = job.celery_task_id
            if job.status not in JOB_ACTIVE_STATUSES:
                return task_id
            job.status = "cancelled"
            job.completed_at = _now()
            job.progress_json = {"phase": "cancelled", "percent": 100}
            job.updated_at = _now()
            if job.workflow_run_id:
                workflow = db.get(WorkflowRun, job.workflow_run_id)
                if workflow and workflow.status not in JOB_TERMINAL_STATUSES:
                    workflow.status = "cancelled"
                    workflow.completed_at = _now()
                    workflow.updated_at = _now()
        return task_id

    @idle_job
    def retry(self, job_id: str) -> None:
        with get_db_session() as db:
            job = db.get(OfflinePatrolJobRecord, job_id)
            if not job:
                raise KeyError("offline patrol job not found")
            if job.status in JOB_ACTIVE_STATUSES:
                raise ValueError("active offline patrol jobs cannot be retried")
            source_count = (
                db.query(OfflinePatrolSourceFileRecord)
                .filter(OfflinePatrolSourceFileRecord.job_id == job_id)
                .count()
            )
            if job.preview_json:
                blockers, _warnings = self._partition_quality_issues(
                    list(job.blockers_json or []),
                    purpose=job.purpose,
                    job_type=job.job_type,
                )
                job.status = "ready" if not blockers else "blocked"
                progress_percent = 100
            elif source_count:
                job.status = "uploading"
                job.blockers_json = []
                progress_percent = 0
            else:
                job.status = "draft"
                job.blockers_json = []
                progress_percent = 0
            job.workflow_run_id = None
            job.celery_task_id = None
            job.execution_idempotency_key = None
            job.error_message = None
            job.completed_at = None
            job.progress_json = {"phase": job.status, "percent": progress_percent}
            job.updated_at = _now()

    def build_original_bundle(self, job_id: str) -> tuple[str, bytes]:
        detail = self.get_job(job_id)
        storage = get_minio_storage()
        buffer = io.BytesIO()
        with get_db_session() as db, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for source in detail["source_files"]:
                file_row = db.get(FileMetadata, source["file_id"])
                if not file_row:
                    continue
                data = storage.download_file(resolve_object_key(db, file_row, repair=True))
                if data is not None:
                    archive.writestr(f"{source['period_role']}/{source['relative_path']}", data)
            device_lines = ["period\tdevice_name\tmanagement_ip\tinventory_device_id\tstatus"]
            failed_lines = ["period\tdevice_name\tmanagement_ip\treason"]
            for item in detail["device_bindings"]:
                device_lines.append(
                    "\t".join(
                        str(item.get(key) or "")
                        for key in (
                            "period_role",
                            "device_name",
                            "management_ip",
                            "inventory_device_id",
                            "match_status",
                        )
                    )
                )
                if item.get("match_status") == "ambiguous":
                    failed_lines.append(
                        "\t".join(
                            str(item.get(key) or "")
                            for key in (
                                "period_role",
                                "device_name",
                                "management_ip",
                                "match_reason",
                            )
                        )
                    )
            archive.writestr("devices.txt", "\n".join(device_lines) + "\n")
            archive.writestr("failed-devices.txt", "\n".join(failed_lines) + "\n")
        return f"{job_id}-raw-output.zip", buffer.getvalue()

    def build_result_export(self, job_id: str, kind: str) -> tuple[str, str, bytes]:
        detail = self.get_job(job_id)
        periods = {item["role"]: item for item in detail["periods"]}
        current = periods.get("current") or periods.get("sample")
        run_id = str((current or {}).get("patrol_run_id") or "")
        if not run_id:
            raise ValueError("offline patrol job has not produced a patrol run")
        if kind == "excel":
            if detail.get("purpose") == "offline_patrol":
                from src.core.patrol.detail_data import load_detail_data, load_detail_source
                from src.core.patrol.detail_workbook import render_detail_workbook

                source = load_detail_source(job_id, owner_id=detail["owner_id"])
                return (
                    "patrol-details.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    render_detail_workbook({}, source, load_detail_data(source)),
                )
            summary = self._build_offline_leadership_summary(detail, run_id)
            return (
                f"{job_id}-patrol-report.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                render_patrol_batch_xlsx(job_id, summary),
            )
        if kind == "report":
            if detail.get("purpose") == "offline_patrol":
                from src.core.patrol.report_service import export_default

                return (
                    f"{job_id}-patrol-report.docx",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    export_default(job_id),
                )
            report = self.build_leadership_report_data(
                job_id,
                detail=detail,
                run_id=run_id,
                include_raw_evidence=True,
            )
            return (
                f"{job_id}-patrol-report.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                render_leadership_docx(report),
            )
        rows = (
            ChangeEventRepository().list_changes(run_id=run_id)
            if kind == "changes"
            else ChangeEventRepository().list_run_event_facts(run_id=run_id)
        )
        output = io.StringIO(newline="")
        fieldnames = list(rows[0]) if rows else list(RESULT_EXPORT_FIELDS[kind])
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        if rows:
            for row in rows:
                writer.writerow(
                    {
                        key: json.dumps(value, ensure_ascii=False)
                        if isinstance(value, (dict, list))
                        else value
                        for key, value in row.items()
                    }
                )
        return (
            f"{job_id}-{kind}.csv",
            "text/csv; charset=utf-8",
            output.getvalue().encode("utf-8-sig"),
        )

    def build_leadership_report_data(
        self,
        job_id: str,
        *,
        detail: dict[str, Any] | None = None,
        run_id: str | None = None,
        analysis_attempt_id: str | None = None,
        include_raw_evidence: bool = False,
        include_source: bool = False,
    ) -> dict[str, Any]:
        """Return the same compact fact model used by the downloadable DOCX report."""

        detail = detail or self.get_job(job_id)
        if not run_id:
            periods = {item["role"]: item for item in detail.get("periods") or []}
            current = periods.get("current")
            run_id = str((current or {}).get("patrol_run_id") or "")
        if not run_id:
            raise ValueError("offline patrol job has not produced a current patrol run")
        summary = self._build_offline_leadership_summary(
            detail,
            run_id,
            analysis_attempt_id=analysis_attempt_id,
            include_raw_evidence=include_raw_evidence,
        )
        predecessor_id = (
            self._find_predecessor_history_job_id(job_id)
            if detail.get("job_type") == "history_compare"
            else None
        )
        if predecessor_id:
            predecessor_detail = self.get_job(predecessor_id)
            predecessor_periods = {
                item["role"]: item for item in predecessor_detail.get("periods") or []
            }
            predecessor_run_id = str(
                (predecessor_periods.get("current") or {}).get("patrol_run_id") or ""
            )
            if predecessor_run_id:
                predecessor_summary = self._build_offline_leadership_summary(
                    predecessor_detail,
                    predecessor_run_id,
                )
                predecessor_report = build_leadership_report(
                    predecessor_id,
                    predecessor_summary,
                    issuer="网络运维中心",
                    recipient="项目建设单位",
                )
                summary["previous_period"] = {
                    "job_id": predecessor_id,
                    "continuity_evidence": "sha256_exact_match",
                    "periods": predecessor_summary.get("periods") or [],
                    "change_count": predecessor_report["metrics"]["change_count"],
                    "event_count": predecessor_report["metrics"]["event_count"],
                    "event_summary": predecessor_report["event_summary"],
                    "metrics": predecessor_report["metrics"],
                    "events": predecessor_summary.get("events") or [],
                    "risks": predecessor_report["risks"],
                    "correlated_evidence": predecessor_report["correlated_evidence"],
                    "analysis": predecessor_report["evidence"]["analysis"],
                }
        report = build_leadership_report(
            job_id,
            summary,
            issuer="网络运维中心",
            recipient="项目建设单位",
        )
        if include_source:
            report["_source"] = summary
        return report

    @staticmethod
    def _snapshot_comparison_evidence(snapshot: Any | None) -> dict[str, Any] | None:
        if snapshot is None:
            return None

        def value(name: str) -> Any:
            if isinstance(snapshot, dict):
                return snapshot.get(name)
            return getattr(snapshot, name, None)

        observed_at = value("observed_at")
        return {
            "snapshot_id": value("snapshot_id") or value("id"),
            "run_id": value("run_id"),
            "command": value("command_canonical") or value("command"),
            "observed_at": (
                observed_at.isoformat()
                if hasattr(observed_at, "isoformat")
                else observed_at
            ),
            "parse_status": value("parse_status"),
            "raw_text": value("raw_text"),
            "structured_data": _json_safe(value("structured_json")),
        }

    @classmethod
    def _comparison_evidence_for_change(
        cls,
        change: dict[str, Any],
        *,
        repository: PatrolRepository | None = None,
        snapshot_cache: dict[str, Any | None] | None = None,
    ) -> dict[str, Any]:
        repository = repository or PatrolRepository()
        snapshot_cache = snapshot_cache if snapshot_cache is not None else {}

        def load(snapshot_id: Any) -> dict[str, Any] | None:
            identifier = str(snapshot_id or "")
            if not identifier:
                return None
            if identifier not in snapshot_cache:
                snapshot_cache[identifier] = repository.get_snapshot(identifier)
            return cls._snapshot_comparison_evidence(snapshot_cache[identifier])

        dimensions = dict(change.get("dimensions") or {})
        unstructured_diff = dict(dimensions.get("unstructured_diff") or {})
        baseline = load(change.get("previous_snapshot_id"))
        current = load(change.get("current_snapshot_id"))
        parser_failure = bool(
            unstructured_diff.get("parser_failure_text_comparison")
            or (baseline or {}).get("parse_status")
            in {"template_missing", "parse_failed"}
            or (current or {}).get("parse_status")
            in {"template_missing", "parse_failed"}
        )
        is_unstructured = bool(unstructured_diff) or (
            str(change.get("field") or "") == "raw_text_hash"
            or str(change.get("observation_type") or "") == "raw.command.hash"
        )
        return {
            "change_id": change.get("id") or change.get("change_id"),
            "device_id": change.get("device_id"),
            "command": change.get("command"),
            "comparison_kind": (
                "unstructured_text" if is_unstructured else "structured_event"
            ),
            "parser_failure_text_comparison": parser_failure,
            "baseline": baseline,
            "current": current,
        }

    def get_comparison_evidence(
        self,
        job_id: str,
        change_id: str,
    ) -> dict[str, Any]:
        detail = self.get_job(job_id)
        periods = {item["role"]: item for item in detail.get("periods") or []}
        current = periods.get("current") or periods.get("sample")
        run_id = str((current or {}).get("patrol_run_id") or "")
        if not run_id:
            raise ValueError("offline patrol job has not produced a patrol run")
        for change in ChangeEventRepository().list_changes(run_id=run_id):
            candidate_id = str(change.get("change_id") or change.get("id") or "")
            if candidate_id == change_id:
                return self._comparison_evidence_for_change(change)
        raise KeyError(f"change not found: {change_id}")

    @staticmethod
    def _find_predecessor_history_job_id(job_id: str) -> str | None:
        """Find an adjacent comparison by exact source-file content identity."""

        with get_db_session() as db:
            current_job = db.get(OfflinePatrolJobRecord, job_id)
            if current_job is None:
                return None
            baseline_rows = (
                db.query(OfflinePatrolSourceFileRecord)
                .filter(
                    OfflinePatrolSourceFileRecord.job_id == job_id,
                    OfflinePatrolSourceFileRecord.period_role == "baseline",
                    OfflinePatrolSourceFileRecord.sha256.is_not(None),
                )
                .all()
            )
            baseline_signature = {
                (str(row.sha256), int(row.size_bytes or 0)) for row in baseline_rows
            }
            if not baseline_signature:
                return None
            candidate_rows = (
                db.query(OfflinePatrolSourceFileRecord)
                .join(
                    OfflinePatrolJobRecord,
                    OfflinePatrolJobRecord.id == OfflinePatrolSourceFileRecord.job_id,
                )
                .filter(
                    OfflinePatrolSourceFileRecord.job_id != job_id,
                    OfflinePatrolSourceFileRecord.period_role == "current",
                    OfflinePatrolSourceFileRecord.sha256.in_(
                        sorted({item[0] for item in baseline_signature})
                    ),
                    OfflinePatrolJobRecord.job_type == "history_compare",
                    OfflinePatrolJobRecord.purpose == current_job.purpose,
                    OfflinePatrolJobRecord.status == "completed",
                )
                .all()
            )
            candidate_ids = sorted({str(row.job_id) for row in candidate_rows})
            candidates: list[tuple[Any, Any, str]] = []
            for candidate_id in candidate_ids:
                all_sources = (
                    db.query(OfflinePatrolSourceFileRecord)
                    .filter(
                        OfflinePatrolSourceFileRecord.job_id == candidate_id,
                        OfflinePatrolSourceFileRecord.period_role == "current",
                        OfflinePatrolSourceFileRecord.sha256.is_not(None),
                    )
                    .all()
                )
                signature = {
                    (str(row.sha256), int(row.size_bytes or 0)) for row in all_sources
                }
                if signature != baseline_signature:
                    continue
                period = (
                    db.query(OfflinePatrolPeriodRecord)
                    .filter(
                        OfflinePatrolPeriodRecord.job_id == candidate_id,
                        OfflinePatrolPeriodRecord.role == "current",
                        OfflinePatrolPeriodRecord.patrol_run_id.is_not(None),
                    )
                    .one_or_none()
                )
                candidate = db.get(OfflinePatrolJobRecord, candidate_id)
                if period is not None and candidate is not None:
                    candidates.append(
                        (
                            period.observed_at,
                            candidate.completed_at or candidate.updated_at,
                            candidate_id,
                        )
                    )
            if not candidates:
                return None
            return max(candidates)[2]

    def _build_offline_leadership_summary(
        self,
        detail: dict[str, Any],
        run_id: str,
        *,
        analysis_attempt_id: str | None = None,
        include_raw_evidence: bool = False,
    ) -> dict[str, Any]:
        patrol_repository = PatrolRepository()
        run = patrol_repository.get_run(run_id) or {}
        collection_rows = patrol_repository.list_collection_results(run_id)
        command_details = [
            {
                "device_id": str(item.get("device_id") or ""),
                "command": item.get("canonical_command") or item.get("command") or "",
                "execution_status": item.get("execution_status") or "unknown",
                "parse_status": item.get("parse_status") or "unknown",
                "output_status": item.get("output_status") or "not_classified",
                "fact_status": item.get("fact_status") or "unknown",
                "failure_code": item.get("failure_code") or "",
            }
            for item in collection_rows
        ]
        with get_db_session() as db:
            observation_rows = (
                db.query(StateObservationRecord)
                .filter(StateObservationRecord.run_id == run_id)
                .order_by(StateObservationRecord.observed_at)
                .yield_per(500)
            )
            attempt_query = db.query(PatrolAnalysisAttemptRecord).filter(
                PatrolAnalysisAttemptRecord.run_id == run_id,
                PatrolAnalysisAttemptRecord.stage == "event_building",
                PatrolAnalysisAttemptRecord.status == "completed",
            )
            if analysis_attempt_id:
                attempt_query = attempt_query.filter(
                    PatrolAnalysisAttemptRecord.id == analysis_attempt_id
                )
            attempt = (
                attempt_query
                .filter(
                    PatrolAnalysisAttemptRecord.completed_at.is_not(None),
                )
                .order_by(PatrolAnalysisAttemptRecord.completed_at.desc())
                .first()
            )
            if analysis_attempt_id and attempt is None:
                raise ValueError(
                    "selected analysis attempt is not a completed event-building "
                    "attempt for this run"
                )
            observation_facts = []
            for row in observation_rows:
                metadata = dict(row.metadata_json or {})
                observation_facts.append(
                    {
                        "id": row.id,
                        "run_id": row.run_id,
                        "snapshot_id": row.snapshot_id,
                        "evidence_id": row.evidence_id,
                        "device_id": row.device_id,
                        "vendor": row.vendor,
                        "model": row.model,
                        "command": row.canonical_command,
                        "entity_type": row.entity_type,
                        "entity_key": row.entity_key,
                        "field": row.field_name,
                        "observation_type": row.observation_type,
                        "semantic_type": row.semantic_type,
                        "value": row.value_json,
                        "unit": row.unit,
                        "observed_at": row.observed_at.isoformat() if row.observed_at else "",
                        "source_type": row.source_type,
                        "schema_id": row.schema_id,
                        "parser_template_id": row.parser_template_id,
                        "parser_version": row.parser_version,
                        "contract_version": row.contract_version,
                        "observation_contract_id": metadata.get("observation_contract_id"),
                        "observation_contract_digest": metadata.get("observation_contract_digest"),
                        "context": metadata.get(APPLICABILITY_CONTEXT_KEY),
                        "dimensions": metadata.get("dimensions"),
                        "metadata": metadata,
                    }
                )
            frozen_attempt = (
                {
                    "id": str(attempt.id),
                    "scope": dict(attempt.scope_json or {}),
                    "rule_bundle": dict(attempt.rule_bundle_json or {}),
                    "rule_bundle_digest": attempt.rule_bundle_digest,
                    "completed_at": (
                        attempt.completed_at.isoformat() if attempt.completed_at else None
                    ),
                }
                if attempt is not None
                else None
            )
        binding_names: dict[str, str] = {}
        current_role = (
            "current"
            if detail.get("job_type") in {"history_compare", "single_patrol"}
            else "sample"
        )
        current_bindings = [
            item
            for item in detail.get("device_bindings") or []
            if item.get("period_role") == current_role
        ]
        for item in current_bindings:
            device_id = str(
                item.get("inventory_device_id")
                or item.get("device_name")
                or item.get("management_ip")
                or ""
            )
            if device_id:
                binding_names[device_id] = str(item.get("device_name") or device_id)
        observations = [
            {
                **row,
                "device_name": binding_names.get(
                    str(row.get("device_id") or ""),
                    str(row.get("device_id") or ""),
                ),
            }
            for row in observation_facts
        ]
        # Only the baseline explicitly selected for this job may supply health evidence.
        from src.core.check_items.current_state import _attach_baselines

        baseline_run_ids = {
            str(period["patrol_run_id"])
            for period in detail.get("periods") or []
            if period.get("role") == "baseline" and period.get("patrol_run_id")
        }
        baseline_facts = []
        if len(baseline_run_ids) == 1:
            with get_db_session() as db:
                for row in (
                    db.query(StateObservationRecord)
                    .filter(StateObservationRecord.run_id == next(iter(baseline_run_ids)))
                    .yield_per(500)
                ):
                    metadata = dict(row.metadata_json or {})
                    baseline_facts.append(
                        {
                            "id": row.id,
                            "snapshot_id": row.snapshot_id,
                            "device_id": row.device_id,
                            "entity_type": row.entity_type,
                            "entity_key": row.entity_key,
                            "field": row.field_name,
                            "value": row.value_json,
                            "unit": row.unit,
                            "observed_at": row.observed_at.isoformat() if row.observed_at else "",
                            "dimensions": metadata.get("dimensions") or {},
                            "context": metadata.get(APPLICABILITY_CONTEXT_KEY) or {},
                        }
                    )
        observations = _attach_baselines(observations, baseline_facts)
        if frozen_attempt is not None:
            bundle = frozen_attempt["rule_bundle"]
            rules = list(bundle.get("event_rules") or [])
            rule_source = "frozen_analysis_attempt"
            rule_digest = frozen_attempt["rule_bundle_digest"]
            rule_error = None
        elif (
            detail.get("purpose") == "offline_patrol" or detail.get("job_type") == "history_compare"
        ):
            try:
                rules = EventRuleRepository().snapshot_active_rules()
                rule_source = "active_runtime"
                rule_digest = None
                rule_error = None if rules else "没有活动 EventRule"
            except Exception as exc:
                rules = []
                rule_source = "unavailable"
                rule_digest = None
                rule_error = f"活动 EventRule 加载失败：{exc}"
        else:
            rules = []
            rule_source = "not_applicable"
            rule_digest = None
            rule_error = "Parser 验证任务不执行网络健康规则判定"
        current_assessment = (
            evaluate_current_state(
                observations,
                rules,
                rule_source=rule_source,
                rule_bundle_digest=rule_digest,
            )
            if rules or (rule_source != "not_applicable" and any(map(policy_rule, observations)))
            else {
                "available": False,
                "rule_source": rule_source,
                "rule_bundle_digest": rule_digest,
                "active_fault_rule_count": 0,
                "latest_observation_count": len(observations),
                "evaluated_observation_count": 0,
                "threshold_unknown_observation_count": 0,
                "partial_threshold_observation_count": 0,
                "finding_count": 0,
                "severity_counts": {},
                "by_domain": {},
                "findings": [],
                "error": rule_error,
            }
        )
        attempt_scope = dict((frozen_attempt or {}).get("scope") or {})
        selected_revision = (
            str(attempt_scope.get("analysis_revision") or "original")
            if frozen_attempt is not None
            else None
        )
        scoped_change_ids = (
            [str(item) for item in attempt_scope.get("change_ids") or [] if str(item)]
            if frozen_attempt is not None and "change_ids" in attempt_scope
            else None
        )
        changes_raw = ChangeEventRepository().list_changes(
            run_id=run_id,
            analysis_revision=selected_revision,
            change_ids=scoped_change_ids,
        )
        event_repository = ChangeEventRepository()
        authoritative_change_ids = [
            str(item.get("change_id")) for item in changes_raw if item.get("change_id")
        ]
        events_raw = event_repository.list_run_event_facts(
            run_id=run_id,
            source_change_ids=authoritative_change_ids,
        )
        event_lifecycle = event_repository.summarize_run_events(
            run_id=run_id,
            source_change_ids=authoritative_change_ids,
        )
        changes = [
            _serialize_offline_change(item, binding_names=binding_names)
            for item in changes_raw
        ]
        if include_raw_evidence:
            evidence_repository = PatrolRepository()
            snapshot_cache: dict[str, Any | None] = {}
            for change in changes:
                change["comparison_evidence"] = self._comparison_evidence_for_change(
                    change,
                    repository=evidence_repository,
                    snapshot_cache=snapshot_cache,
                )
        changes_by_id = {str(item.get("id")): item for item in changes if item.get("id")}
        observations_by_id = {str(item.get("id")): item for item in observations if item.get("id")}
        rules_by_name: dict[str, dict[str, Any]] = {}
        for rule in rules:
            for key in (
                rule.get("rule_name"),
                rule.get("name"),
                rule.get("rule_id"),
            ):
                if key:
                    rules_by_name[str(key)] = rule
        events = []
        for item in events_raw:
            payload = dict(item.get("payload") or {})
            rule_payload = dict(payload.get("rule") or {})
            change_payload = dict(payload.get("change") or {})
            source_change_id = str(
                item.get("source_change_id") or change_payload.get("change_id") or ""
            )
            change = changes_by_id.get(source_change_id) or change_payload
            rule_name = str(rule_payload.get("rule_id") or "")
            frozen_rule = rules_by_name.get(rule_name) or {}
            current_observation_id = str(change.get("current_observation_id") or "")
            current_observation = observations_by_id.get(current_observation_id) or {}
            effective_condition = dict(
                frozen_rule.get("effective_condition")
                or frozen_rule.get("condition")
                or rule_payload.get("condition")
                or {}
            )
            reference_field = str(effective_condition.get("reference_field") or "")
            reference_value = dict(current_observation.get("context") or {}).get(
                reference_field
            )
            evidence_chain = {
                "analysis_attempt_id": (frozen_attempt or {}).get("id"),
                "analysis_revision": selected_revision,
                "workflow_run_id": detail.get("workflow_run_id"),
                "patrol_run_id": run_id,
                "previous_snapshot_id": change.get("previous_snapshot_id"),
                "current_snapshot_id": change.get("current_snapshot_id"),
                "raw_evidence_id": current_observation.get("evidence_id"),
                "previous_observation_id": change.get("previous_observation_id"),
                "current_observation_id": current_observation_id or None,
                "parser_template_id": current_observation.get("parser_template_id"),
                "parser_version": current_observation.get("parser_version"),
                "observation_contract_id": current_observation.get("observation_contract_id"),
                "observation_contract_version": current_observation.get("contract_version"),
                "observation_contract_digest": current_observation.get(
                    "observation_contract_digest"
                ),
                "change_id": source_change_id or None,
                "change_policy_id": change.get("change_policy_id"),
                "change_policy_version_id": change.get("change_policy_version_id"),
                "event_rule_id": frozen_rule.get("rule_id") or rule_name,
                "event_rule_name": frozen_rule.get("rule_name") or rule_name,
                "event_rule_version_id": frozen_rule.get("version_id"),
                "event_rule_version": frozen_rule.get("version") or rule_payload.get("version"),
                "rule_bundle_digest": change.get("rule_bundle_digest") or rule_digest,
                "event_id": item.get("event_id"),
                "event_occurrence_id": item.get("occurrence_id"),
            }
            events.append(
                {
                    "id": item.get("event_id"),
                    "occurrence_id": item.get("occurrence_id"),
                    "run_id": item.get("run_id"),
                    "source_change_id": source_change_id or None,
                    "device_id": item.get("device_id"),
                    "device_name": binding_names.get(
                        str(item.get("device_id") or ""), str(item.get("device_id") or "")
                    ),
                    "event_type": item.get("event_type"),
                    "severity": item.get("severity"),
                    "event_class": payload.get("event_class")
                    or rule_payload.get("event_class")
                    or "fault",
                    "status": item.get("status"),
                    "status_after_occurrence": item.get("status_after_occurrence"),
                    "occurrence_kind": item.get("occurrence_kind"),
                    "entity_type": item.get("entity_type"),
                    "entity_key": item.get("entity_key"),
                    "field": change.get("field"),
                    "previous_value": change.get("old_value", change.get("old")),
                    "current_value": change.get("new_value", change.get("new")),
                    "unit": change.get("unit") or current_observation.get("unit"),
                    "rule_id": frozen_rule.get("rule_id") or rule_name,
                    "rule_name": frozen_rule.get("rule_name") or rule_name,
                    "rule_version_id": frozen_rule.get("version_id"),
                    "rule_version": frozen_rule.get("version") or rule_payload.get("version"),
                    "condition": effective_condition,
                    "reference_field": reference_field or None,
                    "reference_value": reference_value,
                    "domain": report_domain(item.get("entity_type"), item.get("event_type")),
                    "occurred_at": item.get("occurred_at"),
                    "baseline_evidence": {
                        "source": "structured_observation",
                        "snapshot_id": change.get("previous_snapshot_id"),
                        "observation_id": change.get("previous_observation_id"),
                        "value": change.get("old_value", change.get("old")),
                    },
                    "current_evidence": {
                        "source": "structured_observation",
                        "snapshot_id": change.get("current_snapshot_id"),
                        "observation_id": change.get("current_observation_id"),
                        "observed_at": current_observation.get("observed_at"),
                        "value": change.get("new_value", change.get("new")),
                    },
                    "evidence_chain": evidence_chain,
                }
            )
        device_ids = sorted(
            {
                str(item.get("device_id") or "")
                for item in [*observations, *command_details, *changes, *events]
                if item.get("device_id")
            }
        )
        run_rows = []
        for device_id in device_ids:
            device_observations = [
                item for item in observations if str(item.get("device_id") or "") == device_id
            ]
            device_commands = [
                item for item in command_details if str(item.get("device_id") or "") == device_id
            ]
            device_changes = [
                item for item in changes if str(item.get("device_id") or "") == device_id
            ]
            device_events = [
                item for item in events if str(item.get("device_id") or "") == device_id
            ]
            if rules or (
                rule_source != "not_applicable" and any(map(policy_rule, device_observations))
            ):
                device_assessment = evaluate_current_state(
                    device_observations,
                    rules,
                    rule_source=rule_source,
                    rule_bundle_digest=rule_digest,
                )
            else:
                device_assessment = {
                    "available": False,
                    "threshold_unknown_observation_count": 0,
                    "partial_threshold_observation_count": 0,
                    "by_domain": {},
                    "findings": [],
                    "error": rule_error,
                }
            device_findings = list(device_assessment["findings"])
            run_rows.append(
                {
                    "run_id": run_id,
                    "device_id": device_id,
                    "device_name": binding_names.get(device_id, device_id),
                    "status": (
                        "completed" if detail.get("status") == "completed" else detail.get("status")
                    ),
                    "snapshot_run_id": run_id,
                    "quality": quality_from_run_data(device_commands, device_observations),
                    "command_details": device_commands,
                    "observations": device_observations,
                    "changes": device_changes,
                    "events": device_events,
                    "change_count": len(device_changes),
                    "event_count": len(device_events),
                    "current_findings": device_findings,
                    "domain_checks": domain_checks(
                        observations=device_observations,
                        command_details=device_commands,
                        changes=device_changes,
                        events=device_events,
                        current_findings=device_findings,
                        rule_coverage=device_assessment,
                    ),
                }
            )
        quality = quality_from_run_data(command_details, observations)
        template_missing_commands = sorted(
            {
                str(item.get("command") or "")
                for item in command_details
                if item.get("parse_status") == "template_missing" and item.get("command")
            }
        )
        parse_failed_commands = sorted(
            {
                str(item.get("command") or "")
                for item in command_details
                if item.get("parse_status") == "parse_failed" and item.get("command")
            }
        )
        quality.update(
            snapshot_count=int(
                (detail.get("result") or {}).get("snapshot_count") or len(collection_rows)
            ),
            duplicate_snapshot_count=int(
                (detail.get("result") or {}).get("duplicate_snapshot_count") or 0
            ),
            template_missing_count=int(
                (quality.get("parse_status_counts") or {}).get("template_missing") or 0
            ),
            parse_failed_count=int(
                (quality.get("parse_status_counts") or {}).get("parse_failed") or 0
            ),
            template_missing_commands=template_missing_commands,
            parse_failed_commands=parse_failed_commands,
        )
        target_count = len(current_bindings) or len(device_ids)
        job_name = str(detail.get("name") or "").strip()
        if not job_name or "?" in job_name or "\ufffd" in job_name:
            period_dates = {
                str(item.get("role") or ""): str(item.get("observed_at") or "")[:10]
                for item in detail.get("periods") or []
            }
            if detail.get("job_type") == "history_compare":
                job_name = (
                    "双周期规则回放（"
                    f"{period_dates.get('baseline') or '-'} 至 "
                    f"{period_dates.get('current') or '-'}）"
                )
            else:
                job_name = f"规则资产样本解析验证（{period_dates.get('sample') or '-'}）"
        purpose = str(detail.get("purpose") or "offline_patrol")
        report_purpose = (
            "parser_validation"
            if purpose == "rule_asset_source" and detail.get("job_type") == "parser_validation"
            else "rule_asset_replay"
            if purpose == "rule_asset_source"
            else OFFLINE_HISTORICAL
        )
        api_prefix = (
            "/api/v1/parser/asset-source-jobs"
            if purpose == "rule_asset_source"
            else "/api/v1/patrol/offline-jobs"
        )
        return {
            "report_purpose": report_purpose,
            "periods": [
                {
                    "role": item.get("role"),
                    "label": item.get("label"),
                    "observed_at": item.get("observed_at"),
                    "patrol_run_id": item.get("patrol_run_id"),
                }
                for item in detail.get("periods") or []
            ],
            "run_count": len(device_ids) or (1 if run else 0),
            "device_count": len(device_ids),
            "status_counts": {str(detail.get("status") or "unknown"): len(device_ids) or 1},
            "scope": {
                "manifest_available": True,
                "target_type": "offline_import",
                "target_label": job_name,
                "selector": {"job_id": detail.get("id"), "period_role": current_role},
                **_execution_counts(str(detail.get("status") or "unknown"), target_count),
            },
            "quality": quality,
            "change_count": len(changes),
            "change_policy_missing_count": int((((detail.get('result') or {}).get('steps') or {}).get('change_detection') or {}).get('change_policy_missing_count') or 0),
            "event_count": len(events),
            "workflow_run_id": detail.get("workflow_run_id"),
            "analysis": {
                "attempt_id": (frozen_attempt or {}).get("id"),
                "revision": selected_revision,
                "rule_bundle_digest": (frozen_attempt or {}).get("rule_bundle_digest"),
                "source_change_count": len(authoritative_change_ids),
                "completed_at": (frozen_attempt or {}).get("completed_at"),
            },
            "event_lifecycle": event_lifecycle,
            "changes": changes,
            "events": events,
            "observations": observations,
            "current_findings": current_assessment["findings"],
            "current_finding_count": current_assessment["finding_count"],
            "current_state_rule_coverage": {
                **current_assessment,
                "sources": {rule_source: 1},
                "bundle_digests": [rule_digest] if rule_digest else [],
                "errors": [rule_error] if rule_error else [],
            },
            "runs": run_rows,
            "deliverables": {
                "raw_cli_url": f"{api_prefix}/{detail.get('id')}/artifacts/raw",
                "events_url": f"{api_prefix}/{detail.get('id')}/artifacts/events",
                "technical_report_url": (
                    ((detail.get("artifacts") or {}).get("report") or {}).get("download_url")
                ),
            },
        }

    @staticmethod
    def _assert_access(
        row: OfflinePatrolJobRecord | None, *, owner_id: str | None, is_admin: bool
    ) -> None:
        if row is None:
            raise KeyError("offline patrol job not found")
        if owner_id is not None and not is_admin and row.owner_id != owner_id:
            raise PermissionError("offline patrol job belongs to another operator")

    @staticmethod
    def _serialize_job_summary(row: OfflinePatrolJobRecord) -> dict[str, Any]:
        issues = list(row.blockers_json or [])
        preview = _normalized_preview(row.preview_json)
        blockers, warnings = OfflinePatrolJobService._partition_quality_issues(
            issues,
            purpose=str(row.purpose or "offline_patrol"),
            job_type=str(row.job_type or ""),
        )
        return _json_safe(
            {
                "id": row.id,
                "purpose": row.purpose,
                "job_type": row.job_type,
                "report_intent": str(row.report_intent or "historical"),
                "name": row.name,
                "display_name": _purpose_aware_display_name(row),
                "owner_id": row.owner_id,
                "status": row.status,
                "vendor": row.vendor,
                "model": row.model,
                "publish_events": row.publish_events,
                "folder_id": row.folder_id,
                "workflow_run_id": row.workflow_run_id,
                "celery_task_id": row.celery_task_id,
                "attempt_number": row.attempt_number,
                "progress": row.progress_json or {},
                "preview_totals": preview.get("totals", {}),
                "blocker_count": len(blockers),
                "warning_count": len(warnings),
                "archived": row.archived,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "completed_at": row.completed_at,
            }
        )

    def _sync_workflow_rows(self, db, rows: list[OfflinePatrolJobRecord], *, include_completed=True) -> None:
        ids = [
            row.workflow_run_id
            for row in rows
            if row.workflow_run_id
            and (
                row.status in JOB_ACTIVE_STATUSES
                or (
                    include_completed and row.status == "completed"
                    and (
                        not row.artifacts_json
                        or (
                            row.job_type == "parser_validation"
                            and "snapshot_count" not in (row.result_json or {})
                        )
                        or (
                            row.job_type == "history_compare"
                            and "observation_count" not in (row.result_json or {})
                        )
                    )
                )
            )
        ]
        if not ids:
            return
        workflows = {
            item.id: item for item in db.query(WorkflowRun).filter(WorkflowRun.id.in_(ids)).all()
        }
        artifacts_by_run: dict[str, dict[str, Any]] = {}
        for step in (
            db.query(WorkflowStepRecord)
            .filter(WorkflowStepRecord.run_id.in_(ids))
            .order_by(WorkflowStepRecord.run_id, WorkflowStepRecord.step_index)
            .all()
        ):
            if step.output_artifacts:
                artifacts_by_run.setdefault(step.run_id, {}).update(step.output_artifacts)
        for row in rows:
            workflow = workflows.get(row.workflow_run_id)
            if not workflow:
                continue
            if workflow.status in JOB_TERMINAL_STATUSES:
                row.status = workflow.status
                row.error_message = workflow.error_message
                row.completed_at = workflow.completed_at or _now()
                row.progress_json = {"phase": workflow.status, "percent": 100}
                row.updated_at = _now()
                if artifacts := artifacts_by_run.get(workflow.id):
                    row.artifacts_json = _json_safe(artifacts)
                if workflow.status == "completed":
                    period = (
                        db.query(OfflinePatrolPeriodRecord)
                        .filter(
                            OfflinePatrolPeriodRecord.job_id == row.id,
                            OfflinePatrolPeriodRecord.role.in_(["current", "sample"]),
                        )
                        .order_by(OfflinePatrolPeriodRecord.observed_at.desc())
                        .first()
                    )
                    if period and period.patrol_run_id:
                        repository = ChangeEventRepository()
                        if row.job_type == "parser_validation":
                            period_result = dict(period.result_json or {})
                            row.result_json = {
                                "run_id": period.patrol_run_id,
                                "snapshot_count": len(period_result.get("snapshot_ids") or []),
                                "new_snapshot_count": int(
                                    period_result.get("new_snapshot_count") or 0
                                ),
                                "duplicate_snapshot_count": int(
                                    period_result.get("duplicate_snapshot_count") or 0
                                ),
                            }

                        else:
                            row.result_json = {
                                "run_id": period.patrol_run_id,
                                "observation_count": db.query(StateObservationRecord)
                                .filter(StateObservationRecord.run_id == period.patrol_run_id)
                                .count(),
                                "change_count": len(
                                    repository.list_changes(run_id=period.patrol_run_id)
                                ),
                                "event_count": len(
                                    repository.list_run_event_facts(run_id=period.patrol_run_id)
                                ),
                            }

    def sync_workflow_terminal_state(self, workflow_run_id: str) -> int:
        """Project a terminal Workflow into its owning offline patrol job immediately."""
        with get_db_session() as db:
            rows = (
                db.query(OfflinePatrolJobRecord)
                .filter(OfflinePatrolJobRecord.workflow_run_id == workflow_run_id)
                .all()
            )
            self._sync_workflow_rows(db, rows)
            terminal_job_ids = [
                str(row.id) for row in rows if row.status in JOB_TERMINAL_STATUSES
            ]
            terminal_count = len(terminal_job_ids)

        if terminal_job_ids:
            from src.core.rule_control.command_asset_units import RuleAssetCommandUnitService

            projector = RuleAssetCommandUnitService()
            for job_id in terminal_job_ids:
                try:
                    projector.reconcile(job_id)
                except Exception:
                    log.exception(
                        "offline command asset projection failed",
                        extra={"offline_job_id": job_id, "workflow_run_id": workflow_run_id},
                    )
        return terminal_count

    def _update_progress(self, job_id: str, *, phase: str, percent: int) -> None:
        with get_db_session() as db:
            job = db.query(OfflinePatrolJobRecord).filter_by(id=job_id).with_for_update().one_or_none()
            if job and job.status not in JOB_TERMINAL_STATUSES:
                job.progress_json = {"phase": phase, "percent": max(0, min(percent, 100))}
                job.updated_at = _now()

    def _mark_source_materialized(self, source_id: str, digest: str, size: int) -> None:
        with get_db_session() as db:
            source = db.get(OfflinePatrolSourceFileRecord, source_id)
            if source:
                source.sha256 = digest
                source.size_bytes = size
                source.status = "verified"
                source.error_message = None
                source.updated_at = _now()

    @staticmethod
    def _extract_zip(path: Path, target_root: Path) -> tuple[int, int]:
        count = 0
        total = 0
        with zipfile.ZipFile(path) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if len(members) > MAX_ARCHIVE_FILES:
                raise ValueError("zip contains too many files")
            for member in members:
                if member.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                    raise ValueError(f"zip member is too large: {member.filename}")
                relative = _safe_relative_path(member.filename, fallback="capture.log")
                if relative.suffix.lower() not in {".txt", ".log"}:
                    continue
                total += int(member.file_size)
                if total > MAX_ARCHIVE_EXPANDED_BYTES:
                    raise ValueError("zip expanded size exceeds limit")
                destination = target_root / relative
                resolved_root = target_root.resolve()
                resolved_destination = destination.resolve()
                if resolved_root not in resolved_destination.parents:
                    raise ValueError(f"zip path escapes target directory: {member.filename}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, destination.open("wb") as output:
                    while chunk := source.read(1024 * 1024):
                        output.write(chunk)
                count += 1
        return count, total

    def _upsert_device_bindings(
        self,
        job_id: str,
        role: str,
        preview: dict[str, Any],
        *,
        match_inventory: bool = True,
    ) -> None:
        devices: dict[str, dict[str, Any]] = {}
        for item in preview.get("files") or []:
            external_id = str(item.get("device_id") or "").strip()
            if not external_id:
                continue
            devices[external_id] = {
                "device_name": str(item.get("device_name") or external_id),
                "management_ip": item.get("management_ip"),
            }
        with get_db_session() as db:
            inventory = db.query(NetworkDevice).filter(NetworkDevice.status == "active").all()
            for external_id, identity in devices.items():
                ip = str(identity.get("management_ip") or "").strip()
                matches = (
                    _rank_inventory_matches(
                        inventory,
                        device_name=identity["device_name"],
                        management_ip=ip,
                    )
                    if match_inventory
                    else []
                )
                existing = (
                    db.query(OfflinePatrolDeviceBindingRecord)
                    .filter(
                        OfflinePatrolDeviceBindingRecord.job_id == job_id,
                        OfflinePatrolDeviceBindingRecord.period_role == role,
                        OfflinePatrolDeviceBindingRecord.external_device_id == external_id,
                    )
                    .first()
                )
                if existing and existing.match_status in {"confirmed", "external"}:
                    continue
                if not match_inventory:
                    confidence, reason = 1.0, "rule_asset_sample_identity"
                    status, inventory_id = "external", None
                elif len(matches) == 1:
                    candidate, confidence, reason = matches[0]
                    status, inventory_id = "suggested", candidate.id
                elif matches:
                    confidence, reason = matches[0][1], "multiple_inventory_candidates"
                    status, inventory_id = "ambiguous", None
                else:
                    confidence, reason = 0.0, "no_inventory_match"
                    status, inventory_id = "unmatched", None
                row = existing or OfflinePatrolDeviceBindingRecord(
                    id=f"binding-{uuid.uuid4().hex[:20]}",
                    job_id=job_id,
                    period_role=role,
                    external_device_id=external_id,
                    created_at=_now(),
                )
                row.device_name = identity["device_name"]
                row.management_ip = ip or None
                row.inventory_device_id = inventory_id
                row.match_status = status
                row.confidence = confidence
                row.match_reason = reason
                row.updated_at = _now()
                db.add(row)

    def _build_quality_blockers(
        self, job_id: str, previews: dict[str, Any]
    ) -> list[dict[str, Any]]:
        blockers: list[dict[str, Any]] = []
        for role, preview in previews.items():
            for key, code, label in (
                ("identity_unknown", "identity_unknown", "设备身份无法识别"),
                (
                    "empty_unverified",
                    "empty_unverified",
                    "命令输出为空，需人工确认是否可忽略",
                ),
                ("template_missing", "template_missing", "结构化命令缺少 Parser 模板"),
                ("parse_failed", "parse_failed", "Parser 解析失败"),
            ):
                count = int(preview.get(key) or 0)
                if count:
                    blockers.append(
                        {
                            "code": code,
                            "period_role": role,
                            "count": count,
                            "message": f"{label}：{count} 条",
                        }
                    )
        if set(previews) == {"baseline", "current"}:
            device_sets = [
                {
                    str(item.get("device_id"))
                    for item in previews[role].get("files") or []
                    if item.get("device_id")
                }
                for role in ("baseline", "current")
            ]
            comparable_command_sets = [
                {
                    (
                        str(file_item.get("device_id") or ""),
                        str(command.get("command") or ""),
                    )
                    for file_item in previews[role].get("files") or []
                    for command in file_item.get("commands") or []
                    if command.get("status") in {"ready", "raw_compare"}
                    and file_item.get("device_id")
                    and command.get("command")
                }
                for role in ("baseline", "current")
            ]
            if not comparable_command_sets[0].intersection(comparable_command_sets[1]):
                blockers.append(
                    {
                        "code": "no_common_ready_parser",
                        "message": (
                            "baseline and current have no common command eligible for "
                            "structured or raw comparison"
                        ),
                    }
                )
            if not device_sets[0].intersection(device_sets[1]):
                blockers.append(
                    {"code": "no_common_device", "message": "baseline 与 current 没有共同设备"}
                )
        with get_db_session() as db:
            ambiguous = (
                db.query(OfflinePatrolDeviceBindingRecord)
                .filter(
                    OfflinePatrolDeviceBindingRecord.job_id == job_id,
                    OfflinePatrolDeviceBindingRecord.match_status == "ambiguous",
                )
                .count()
            )
        if ambiguous:
            blockers.append(
                {
                    "code": "ambiguous_device_binding",
                    "count": ambiguous,
                    "message": f"{ambiguous} 台设备存在多个资产匹配候选",
                }
            )
        template_ids = {
            str(command.get("template_id"))
            for preview in previews.values()
            for file_item in preview.get("files") or []
            for command in file_item.get("commands") or []
            if command.get("status") == "ready" and command.get("template_id")
        }
        if template_ids:
            with get_db_session() as db:
                active_template_ids = {
                    str(item[0])
                    for item in db.query(ParserTemplate.id)
                    .filter(
                        ParserTemplate.id.in_(template_ids),
                        ParserTemplate.status == "active",
                    )
                    .all()
                }
                for template_id in sorted(template_ids - active_template_ids):
                    blockers.append(
                        {
                            "code": "parser_template_inactive",
                            "template_id": template_id,
                            "message": f"Parser template {template_id} is no longer active",
                        }
                    )
                # A CommandAdapter V2 owns the Parser A -> CheckItem B mapping.
                # It must therefore be checked through the same CheckItem Runtime
                # Bundle planner used by collection, rather than through the
                # retired Parser -> ObservationContract quality gate.
                parser_bindings = (
                    db.query(
                        CollectionImplementationParserBindingRecord,
                        CollectionImplementationVersionRecord,
                    )
                    .join(
                        CollectionImplementationVersionRecord,
                        CollectionImplementationVersionRecord.id
                        == CollectionImplementationParserBindingRecord.implementation_version_id,
                    )
                    .filter(
                        CollectionImplementationParserBindingRecord.parser_template_id.in_(
                            active_template_ids
                        ),
                        CollectionImplementationParserBindingRecord.status == "active",
                        CollectionImplementationVersionRecord.status == "active",
                    )
                    .all()
                    if active_template_ids
                    else []
                )
                direct_bindings = [
                    (binding, implementation)
                    for binding, implementation in parser_bindings
                    if is_direct_projection_adapter(
                        dict(implementation.adapter_spec_json or {})
                    )
                ]
                direct_template_ids = {
                    str(binding.parser_template_id)
                    for binding, _implementation in direct_bindings
                    if binding.parser_template_id
                }
                links_by_implementation: dict[str, list[str]] = {}
                if direct_bindings:
                    implementation_ids = {
                        str(implementation.id)
                        for _binding, implementation in direct_bindings
                    }
                    for link in (
                        db.query(CheckItemImplementationLinkRecord)
                        .filter(
                            CheckItemImplementationLinkRecord.implementation_version_id.in_(
                                implementation_ids
                            )
                        )
                        .all()
                    ):
                        links_by_implementation.setdefault(
                            str(link.implementation_version_id),
                            [],
                        ).append(str(link.check_item_version_id))
                planner = RuntimeBundleAssemblyPlanner(db)
                checked_routes: set[tuple[str, str, str]] = set()
                for binding, implementation in direct_bindings:
                    template_id = str(binding.parser_template_id)
                    implementation_id = str(implementation.id)
                    check_item_versions = links_by_implementation.get(
                        implementation_id, []
                    )
                    if not check_item_versions:
                        blockers.append(
                            {
                                "code": "command_adapter_projection_missing",
                                "template_id": template_id,
                                "implementation_version_id": implementation_id,
                                "message": (
                                    "CommandAdapter Projection 未关联任何 CheckItem 版本。"
                                ),
                            }
                        )
                        continue
                    for check_item_version_id in check_item_versions:
                        route_key = (
                            template_id,
                            implementation_id,
                            check_item_version_id,
                        )
                        if route_key in checked_routes:
                            continue
                        checked_routes.add(route_key)
                        plan = planner.build(
                            check_item_version_id=check_item_version_id,
                            implementation_version_id=implementation_id,
                        )
                        route_blockers = [
                            dict(item)
                            for item in plan.get("blockers") or []
                            if isinstance(item, dict)
                        ]
                        if route_blockers:
                            blockers.append(
                                {
                                    "code": "runtime_bundle_not_ready",
                                    "template_id": template_id,
                                    "implementation_version_id": implementation_id,
                                    "check_item_version_id": check_item_version_id,
                                    "message": (
                                        "CommandAdapter Projection 对应的 CheckItem "
                                        "运行包尚未就绪。"
                                    ),
                                    "runtime_bundle_blockers": route_blockers,
                                }
                            )
                        elif not plan.get("active_bundle"):
                            blockers.append(
                                {
                                    "code": "runtime_bundle_missing",
                                    "template_id": template_id,
                                    "implementation_version_id": implementation_id,
                                    "check_item_version_id": check_item_version_id,
                                    "message": (
                                        "CommandAdapter Projection 已验证，但尚未激活 "
                                        "CheckItem Runtime Bundle。"
                                    ),
                                }
                            )

                for template_id in sorted(active_template_ids - direct_template_ids):
                    blockers.append({
                        "code": "command_adapter_v2_required",
                        "template_id": template_id,
                        "message": "请关联检查项并发布 V2 命令适配器。",
                    })
        return blockers

    @staticmethod
    def _partition_quality_issues(
        issues: list[dict[str, Any]],
        *,
        purpose: str = "rule_asset_source",
        job_type: str = "",
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        blockers: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        for raw in issues:
            item = dict(raw)
            blocker_codes = (
                PATROL_RUNTIME_BLOCKER_CODES
                if purpose == "offline_patrol"
                else INGESTION_BLOCKER_CODES
            )
            code = str(item.get("code") or "")
            is_historical_degradation = (
                purpose == "offline_patrol"
                and job_type == "history_compare"
                and code in HISTORICAL_DEGRADATION_WARNING_CODES
            )
            if code in blocker_codes and not is_historical_degradation:
                item["severity"] = "blocking"
                blockers.append(item)
            else:
                item["severity"] = "warning"
                warnings.append(item)
        return blockers, warnings

    @staticmethod
    def _rule_mentions_target(
        rule: EventRuleVersionRecord, field_name: str, observation_type: str
    ) -> bool:
        payload = json.dumps(
            {
                "condition": rule.condition_json or {},
                "recovery": rule.recovery_json or {},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return field_name in payload or observation_type in payload

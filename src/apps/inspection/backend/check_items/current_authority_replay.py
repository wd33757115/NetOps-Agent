"""Replay attested offline-current evidence through the production runtime."""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.check_items.source_qualification import (
    source_qualification_is_valid,
)
from src.core.collection import execution_plan_from_metadata
from src.core.parser.policy import DEFAULT_COMMAND_ALIASES, ParserPolicy
from src.core.patrol.repository import PatrolRunRepositoryBackend
from src.core.patrol.runner import (
    build_frozen_check_item_parser_bundle,
    load_frozen_check_item_runtime_manifest,
    persist_patrol_snapshots,
)
from src.infrastructure.db.models import (
    CommandSnapshot,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    PatrolCurrentAuthorityRequestRecord,
    PatrolSourceQualificationRecord,
)
from src.infrastructure.db.postgres import get_db_session

logger = logging.getLogger(__name__)


class AttestedOfflineReplayError(RuntimeError):
    """Stable fail-closed error raised before production facts are derived."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)


def _canonical(value: Any) -> str:
    basic = ParserPolicy._basic_normalize(str(value or ""))
    return ParserPolicy._basic_normalize(DEFAULT_COMMAND_ALIASES.get(basic, basic))


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _row_value(row: Any, field: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(field)
    return getattr(row, field)


def _freeze_snapshot_row(row: CommandSnapshot) -> dict[str, Any]:
    """Detach the evidence values from the SQLAlchemy session boundary."""

    return {
        "id": str(row.id),
        "command": str(row.command or ""),
        "canonical_command": str(row.canonical_command or ""),
        "observed_at": _utc(row.observed_at),
        "raw_text": str(row.raw_text or ""),
        "raw_text_hash": str(row.raw_text_hash or ""),
        "source_file_hash": str(row.source_file_hash or ""),
        "inventory_device_id": str(row.inventory_device_id or ""),
    }


class AttestedOfflineCurrentReplayService:
    """Create a new production PatrolRun from frozen, attested raw evidence."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = (get_db_session),
    ) -> None:
        self._session_scope = session_scope

    def execute(
        self,
        *,
        execution_plan: Mapping[str, Any],
        execution_plan_id: str,
        workflow_run_id: str | None = None,
        snapshot_run_id: str | None = None,
        owner_token: str | None = None,
    ) -> dict[str, Any]:
        plan = execution_plan_from_metadata(dict(execution_plan))
        selector = dict(plan.selector or {})
        request_id = str(selector.get("authority_request_id") or "")
        batch_id = str(selector.get("patrol_batch_id") or "")
        if not request_id or not batch_id:
            raise AttestedOfflineReplayError(
                "authority_context_missing",
                "execution plan has no frozen current-authority identity",
            )
        context = self._load_context(
            request_id=request_id,
            batch_id=batch_id,
            device_id=str(plan.device_id),
            execution_plan_id=str(execution_plan_id),
        )
        try:
            runtime_manifest = load_frozen_check_item_runtime_manifest(plan)
        except Exception as exc:
            raise AttestedOfflineReplayError(
                "runtime_manifest_invalid",
                "frozen production runtime manifest is missing or invalid",
            ) from exc
        if str(runtime_manifest.get("patrol_batch_id") or "") != batch_id or str(
            runtime_manifest.get("digest") or ""
        ) != str(plan.runtime_manifest_digest or ""):
            raise AttestedOfflineReplayError(
                "runtime_manifest_mismatch",
                "execution plan does not match the frozen production manifest",
            )
        parser_bundle = build_frozen_check_item_parser_bundle(
            plan,
            frozen_device=context["frozen_device"],
        )
        selected, command_results, accounting = self._select_evidence(
            plan=plan,
            frozen_manifest=context["snapshot_manifest"],
            snapshot_rows=context["snapshot_rows"],
        )
        if not snapshot_run_id and not workflow_run_id:
            raise AttestedOfflineReplayError("execution_identity_missing", "缺少执行身份")
        run_id = snapshot_run_id or "patrol-attested-current-" + str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"netops/attested-current-run/{workflow_run_id}",
            )
        )
        owner_token = (
            owner_token
            or hashlib.sha256(
                f"{request_id}|{workflow_run_id}|{plan.device_id}".encode()
            ).hexdigest()
        )
        dataset_id = f"attested-current-{request_id}"
        PatrolRunRepositoryBackend().ensure_dataset(
            dataset_id=dataset_id,
            mode="production",
            source="attested_offline_current",
            name=f"Attested offline current {request_id}",
            metadata={
                "authority_request_id": request_id,
                "patrol_batch_id": batch_id,
                "source_offline_job_id": context["offline_job_id"],
                "source_qualification_id": context["qualification_id"],
                "source_qualification_digest": context["qualification_digest"],
            },
        )
        observed_values = [_utc(_row_value(item["row"], "observed_at")) for item in selected]
        started_at = (
            min(observed_values).isoformat() if observed_values else (context["observed_at_min"])
        )
        detail = {
            "inventory_device_id": str(plan.device_id),
            "device_name": context["frozen_device"].get("device_name"),
            "ip": context["frozen_device"].get("management_ip"),
            "vendor": context["frozen_device"].get("vendor"),
            "model": context["frozen_device"].get("model"),
            "success": bool(selected),
            "collection_status": (
                "complete"
                if all(
                    str(item.get("status"))
                    in {
                        "complete",
                        "fallback_not_needed",
                        "ignored_legacy_fallback",
                    }
                    for item in command_results
                )
                else "partial"
                if selected
                else "failed"
            ),
            "command_collection_results": command_results,
        }
        detail["collector_complete"] = detail["collection_status"] == "complete"
        with tempfile.TemporaryDirectory(
            prefix=f"netops-attested-current-{plan.device_id}-"
        ) as temp:
            if selected:
                output = Path(temp) / "attested-current-output.txt"
                output.write_text(
                    self._command_report(selected),
                    encoding="utf-8",
                )
                detail["output_file"] = str(output)
            persistence = persist_patrol_snapshots(
                details=[detail],
                snapshot_run_id=run_id,
                mode="production",
                execution_plan_id=str(execution_plan_id),
                patrol_batch_id=batch_id,
                run_purpose="scheduled_patrol",
                source="attested-offline-current",
                dataset_id=dataset_id,
                source_metadata={
                    "authority_request_id": request_id,
                    "source_qualification_id": context["qualification_id"],
                    "source_qualification_digest": context["qualification_digest"],
                    "source_offline_job_id": context["offline_job_id"],
                    "source_snapshot_ids": accounting["selected_snapshot_ids"],
                    "source_snapshot_manifest_digest": context["snapshot_manifest_digest"],
                    "evidence_accounting": accounting,
                    "runtime_asset_manifest_digest": runtime_manifest["digest"],
                    "runtime_asset_manifest": runtime_manifest,
                },
                started_at=started_at,
                create_run=True,
                finalize_run=True,
                run_owner_token=owner_token,
                parser_runtime_bundle=parser_bundle.to_dict(),
            )
        return {
            "success": True,
            "message": "经证明的离线当前证据已进入独立生产运行",
            "snapshot_run_id": run_id,
            "run_id": run_id,
            "patrol_batch_id": batch_id,
            "device_id": str(plan.device_id),
            "authority_request_id": request_id,
            "source_qualification_id": context["qualification_id"],
            "source_qualification_digest": context["qualification_digest"],
            "dataset_id": dataset_id,
            "runtime_asset_manifest": runtime_manifest,
            "runtime_asset_manifest_digest": runtime_manifest["digest"],
            "parser_runtime_bundle_digest": parser_bundle.digest,
            "source_accounting": accounting,
            **persistence,
        }

    def _load_context(
        self,
        *,
        request_id: str,
        batch_id: str,
        device_id: str,
        execution_plan_id: str,
    ) -> dict[str, Any]:
        with self._session_scope() as db:
            request = db.get(PatrolCurrentAuthorityRequestRecord, request_id)
            batch = db.get(PatrolBatchRecord, batch_id)
            if (
                request is None
                or batch is None
                or str(request.candidate_batch_id or "") != batch_id
                or str(batch.source_authority_request_id or "") != request_id
            ):
                raise AttestedOfflineReplayError(
                    "authority_context_missing",
                    "current-authority request or candidate batch is missing",
                )
            qualification = db.get(
                PatrolSourceQualificationRecord,
                request.approved_qualification_id,
            )
            if (
                str(request.status) not in {"dispatching", "completed"}
                or qualification is None
                or str(qualification.status) != "approved"
                or str(batch.source_qualification_digest or "") != str(qualification.digest)
            ):
                raise AttestedOfflineReplayError(
                    "qualification_not_active",
                    "source qualification is missing, revoked or not executable",
                )
            request_payload = dict(request.request_json or {})
            calculated_request_digest = hashlib.sha256(
                json.dumps(
                    request_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
            if calculated_request_digest != str(request.request_digest):
                raise AttestedOfflineReplayError(
                    "authority_request_digest_mismatch",
                    "frozen current-authority request content changed after attestation",
                )
            if not source_qualification_is_valid(
                qualification.qualification_json,
                expected_patrol_batch_id=batch_id,
                expected_scope_contract_digest=str(request.scope_contract_digest),
                expected_intended_target_matrix_digest=str(request.intended_target_matrix_digest),
            ):
                raise AttestedOfflineReplayError(
                    "qualification_digest_mismatch",
                    "source qualification content no longer verifies",
                )
            target = (
                db.query(PatrolBatchDeviceRecord)
                .filter(
                    PatrolBatchDeviceRecord.patrol_batch_id == batch_id,
                    PatrolBatchDeviceRecord.device_id == device_id,
                )
                .one_or_none()
            )
            if target is None or str(target.execution_plan_id or "") != execution_plan_id:
                raise AttestedOfflineReplayError(
                    "execution_plan_mismatch",
                    "batch target does not reference this execution plan",
                )
            payload = request_payload
            frozen_manifest = [
                dict(item)
                for item in payload.get("snapshot_manifest") or []
                if str(item.get("inventory_device_id") or "") == device_id
            ]
            ids = [str(item["snapshot_id"]) for item in frozen_manifest]
            rows = (
                db.query(CommandSnapshot).filter(CommandSnapshot.id.in_(ids)).all() if ids else []
            )
            if len(rows) != len(set(ids)):
                raise AttestedOfflineReplayError(
                    "source_snapshot_missing",
                    "one or more frozen source snapshots are missing",
                )
            row_by_id = {str(row.id): row for row in rows}
            for item in frozen_manifest:
                row = row_by_id[str(item["snapshot_id"])]
                calculated_raw_hash = hashlib.sha256(
                    str(row.raw_text or "").encode("utf-8")
                ).hexdigest()
                if (
                    str(row.raw_text_hash) != str(item["raw_text_hash"])
                    or calculated_raw_hash != str(item["raw_text_hash"])
                    or str(row.source_file_hash or "") != str(item.get("source_file_hash") or "")
                    or _canonical(row.canonical_command)
                    != _canonical(item.get("canonical_command"))
                    or str(row.inventory_device_id or "") != device_id
                    or _iso(row.observed_at) != str(item.get("observed_at") or "")
                ):
                    raise AttestedOfflineReplayError(
                        "source_snapshot_digest_mismatch",
                        "frozen source snapshot identity changed after attestation",
                    )
            frozen_rows_by_id = {
                snapshot_id: _freeze_snapshot_row(row) for snapshot_id, row in row_by_id.items()
            }
            inventory = dict(payload.get("inventory_snapshot") or {})
            inventory_row = next(
                (
                    dict(item)
                    for item in inventory.get("devices") or []
                    if str(item.get("device_id") or "") == device_id
                ),
                None,
            )
            binding = next(
                (
                    dict(item)
                    for item in payload.get("identity_bindings") or []
                    if str(item.get("inventory_device_id") or "") == device_id
                ),
                None,
            )
            if inventory_row is None or binding is None:
                raise AttestedOfflineReplayError(
                    "frozen_device_missing",
                    "frozen inventory or identity binding is missing",
                )
            scope_device_row = next(
                (
                    dict(item)
                    for item in payload.get("scope_device_facts") or []
                    if str(item.get("device_id") or "") == device_id
                ),
                {},
            )
            frozen_base = scope_device_row or inventory_row
            attributes = dict(frozen_base.get("attributes") or {})
            if "capabilities" not in attributes:
                attributes["capabilities"] = list(frozen_base.get("capabilities") or [])
            frozen_device = {
                **inventory_row,
                **frozen_base,
                "id": device_id,
                "device_id": device_id,
                "device_name": str(binding.get("device_name") or device_id),
                "management_ip": str(binding.get("management_ip") or ""),
                "attributes": attributes,
            }
            period = dict(payload.get("current_period") or {})
            return {
                "offline_job_id": str(request.offline_job_id),
                "qualification_id": str(qualification.id),
                "qualification_digest": str(qualification.digest),
                "snapshot_manifest": frozen_manifest,
                "snapshot_manifest_digest": hashlib.sha256(
                    json.dumps(
                        frozen_manifest,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
                "snapshot_rows": frozen_rows_by_id,
                "frozen_device": frozen_device,
                "observed_at_min": str(period.get("observed_at_min") or period.get("observed_at")),
            }

    @staticmethod
    def _select_evidence(
        *,
        plan: Any,
        frozen_manifest: list[dict[str, Any]],
        snapshot_rows: Mapping[str, Any],
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, Any],
    ]:
        available: dict[str, list[dict[str, Any]]] = {}
        for item in frozen_manifest:
            available.setdefault(_canonical(item.get("canonical_command")), []).append(item)
        for values in available.values():
            values.sort(
                key=lambda item: (
                    str(item.get("observed_at") or ""),
                    str(item.get("snapshot_id") or ""),
                ),
                reverse=True,
            )
        actions = list(plan.commands)
        binding_actions: dict[str, list[tuple[int, Any]]] = {}
        for ordinal, action in enumerate(actions):
            for binding in action.check_item_bindings:
                version_id = str(binding.get("check_item_version_id") or "")
                if version_id:
                    binding_actions.setdefault(version_id, []).append((ordinal, action))
        selected_action_by_binding: dict[str, int] = {}
        for version_id, candidates in binding_actions.items():
            candidates.sort(
                key=lambda item: (
                    int(item[1].fallback_rank),
                    item[0],
                )
            )
            selected_action_by_binding[version_id] = candidates[0][0]
            for _ordinal, ignored in candidates[1:]:
                logger.warning(
                    "legacy fallback evidence ignored during offline replay",
                    extra={
                        "event": "offline_replay_legacy_fallback_ignored",
                        "check_item_version_id": version_id,
                        "command": ignored.command,
                    },
                )

        selected: list[dict[str, Any]] = []
        command_results: list[dict[str, Any]] = []
        selected_snapshot_ids: list[str] = []
        accounted_snapshot_ids: set[str] = set()
        for ordinal, action in enumerate(actions):
            command = _canonical(action.command)
            selected_bindings = [
                dict(binding)
                for binding in action.check_item_bindings
                if selected_action_by_binding.get(str(binding.get("check_item_version_id") or ""))
                == ordinal
            ]
            all_binding_ids = {
                str(binding.get("check_item_version_id") or "")
                for binding in action.check_item_bindings
                if binding.get("check_item_version_id")
            }
            ignored_legacy_fallback = bool(all_binding_ids) and all(
                selected_action_by_binding.get(version_id) != ordinal
                for version_id in all_binding_ids
            )
            source = available.get(command, [None])[0] if available.get(command) else None
            if selected_bindings and source is not None:
                snapshot_id = str(source["snapshot_id"])
                row = snapshot_rows[snapshot_id]
                selected.append(
                    {
                        "action": action,
                        "manifest": source,
                        "row": row,
                    }
                )
                selected_snapshot_ids.append(snapshot_id)
                accounted_snapshot_ids.add(snapshot_id)
                status = "complete"
                failure_code = None
                observed_at = str(source["observed_at"])
            elif ignored_legacy_fallback:
                status = "ignored_legacy_fallback"
                failure_code = "fallback_disabled"
                observed_at = None
            else:
                status = "source_output_missing"
                failure_code = "source_output_missing"
                observed_at = None
            command_results.append(
                {
                    "command": action.command,
                    "status": status,
                    "failure_code": failure_code,
                    "observed_at": observed_at,
                    "execution_item_id": str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            "netops/attested-current-execution-item/"
                            f"{plan.device_id}/{action.collection_item_id}",
                        )
                    ),
                    "collection_item_id": action.collection_item_id,
                    "command_ordinal": ordinal,
                    "check_item_bindings": [dict(item) for item in action.check_item_bindings],
                    "selected_for_bindings": selected_bindings,
                }
            )
        all_snapshot_ids = {str(item["snapshot_id"]) for item in frozen_manifest}
        accounting = {
            "schema_version": "attested-offline-replay-accounting/v1",
            "frozen_snapshot_count": len(all_snapshot_ids),
            "selected_snapshot_count": len(selected_snapshot_ids),
            "selected_snapshot_ids": selected_snapshot_ids,
            "unselected_snapshot_ids": sorted(all_snapshot_ids - accounted_snapshot_ids),
            "planned_action_count": len(actions),
            "completed_action_count": sum(item["status"] == "complete" for item in command_results),
            "ignored_legacy_fallback_count": sum(
                item["status"] == "ignored_legacy_fallback" for item in command_results
            ),
            "missing_action_count": sum(
                item["status"] == "source_output_missing" for item in command_results
            ),
        }
        return selected, command_results, accounting

    @staticmethod
    def _command_report(selected: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for item in selected:
            row = item["row"]
            parts.extend(
                [
                    f"Command: {_row_value(row, 'command')}",
                    "Output:",
                    str(_row_value(row, "raw_text") or ""),
                    "-" * 50,
                ]
            )
        return "\n".join(parts).rstrip() + "\n"


__all__ = [
    "AttestedOfflineCurrentReplayService",
    "AttestedOfflineReplayError",
]

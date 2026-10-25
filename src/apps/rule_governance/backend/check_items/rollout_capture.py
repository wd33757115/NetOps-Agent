"""Live raw-evidence capture adapter for trusted rollout stage execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any, Protocol

from src.apps.rule_governance.backend.parser.policy import ParserPolicy
from src.core.check_items.report_contract import SHADOW_VALIDATION
from src.core.collection import CollectionPolicyResolver
from src.infrastructure.db.models import (
    CommandSnapshot,
    NetworkDevice,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    PatrolRun,
)
from src.infrastructure.db.postgres import get_db_session

if TYPE_CHECKING:
    from src.core.patrol.handlers import StartPatrolHandler


class RolloutCaptureError(RuntimeError):
    """Raised when live capture cannot provide complete immutable evidence."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class RolloutCaptureAdapter(Protocol):
    def start(
        self,
        *,
        execution_id: str,
        selection_manifest: Mapping[str, Any],
    ) -> dict[str, Any]: ...

    def poll(
        self,
        *,
        capture_batch_id: str,
        selection_manifest: Mapping[str, Any],
    ) -> dict[str, Any]: ...


def _find_nested_values(value: Any, key: str) -> list[Any]:
    values: list[Any] = []
    if isinstance(value, Mapping):
        for item_key, item_value in value.items():
            if item_key == key and item_value not in (None, ""):
                values.append(item_value)
            values.extend(_find_nested_values(item_value, key))
    elif isinstance(value, list):
        for item in value:
            values.extend(_find_nested_values(item, key))
    return values


def _device_payload(row: NetworkDevice) -> dict[str, Any]:
    return {
        "device_id": str(row.id),
        "id": str(row.id),
        "device_name": str(row.device_name),
        "ip": str(row.management_ip),
        "management_ip": str(row.management_ip),
        "username": row.username,
        "credential_ref": row.credential_ref,
        "vendor": row.vendor,
        "model": row.model,
        "family": row.family,
        "platform": row.platform,
        "device_type": row.device_type,
        "os_version": row.os_version,
        "site": row.site,
        "role": row.role,
        "status": row.status,
        "command_file": row.command_profile,
        "attributes": dict(row.attributes_json or {}),
    }


class ProductionPatrolCaptureAdapter:
    """Launch fixed inventory capture tasks and collect raw evidence only."""

    workflow_name = "rollout-live-capture"

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = (
            get_db_session
        ),
        *,
        start_handler_factory: Callable[[str], StartPatrolHandler] | None = None,
        policy_resolver_factory: Callable[
            [],
            CollectionPolicyResolver,
        ] | None = None,
    ) -> None:
        self._session_scope = session_scope
        self._start_handler_factory = (
            start_handler_factory or self._default_start_handler
        )
        self._policy_resolver_factory = (
            policy_resolver_factory or CollectionPolicyResolver
        )

    @staticmethod
    def _default_start_handler(batch_id: str) -> StartPatrolHandler:
        from src.core.patrol.handlers import StartPatrolHandler

        return StartPatrolHandler(batch_id_factory=lambda: batch_id)

    def start(
        self,
        *,
        execution_id: str,
        selection_manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        from src.core.patrol.handlers import StartPatrolCommand

        capture_batch_id = f"rollout-capture-{execution_id}"
        with self._session_scope() as db:
            existing = db.get(PatrolBatchRecord, capture_batch_id)
            if existing is not None:
                return {
                    "status": "capturing",
                    "capture_batch_id": capture_batch_id,
                    "idempotent_replay": True,
                }
            targets = [
                dict(item)
                for item in selection_manifest.get("targets") or []
            ]
            device_ids = sorted(
                {str(item.get("device_id") or "") for item in targets}
            )
            if not device_ids or "" in device_ids:
                raise RolloutCaptureError(
                    "capture_target_invalid",
                    "live capture requires frozen inventory device ids",
                )
            devices = (
                db.query(NetworkDevice)
                .filter(NetworkDevice.id.in_(device_ids))
                .all()
            )
            devices_by_id = {str(item.id): item for item in devices}
            if set(devices_by_id) != set(device_ids):
                raise RolloutCaptureError(
                    "capture_inventory_missing",
                    "one or more live-capture devices are missing",
                )
            inactive = sorted(
                device_id
                for device_id, row in devices_by_id.items()
                if str(row.status) != "active"
            )
            if inactive:
                raise RolloutCaptureError(
                    "capture_inventory_inactive",
                    "live-capture devices are not active: "
                    + ", ".join(inactive),
                )
            detached = {
                device_id: _device_payload(row)
                for device_id, row in devices_by_id.items()
            }

        commands_by_device: dict[str, list[str]] = {}
        for target in targets:
            device_id = str(target["device_id"])
            values = commands_by_device.setdefault(device_id, [])
            seen = {
                ParserPolicy._basic_normalize(item) for item in values
            }
            for command in target.get("commands") or []:
                command_text = str(command or "").strip()
                normalized = ParserPolicy._basic_normalize(command_text)
                if normalized and normalized not in seen:
                    values.append(command_text)
                    seen.add(normalized)
        if any(not values for values in commands_by_device.values()):
            raise RolloutCaptureError(
                "capture_commands_missing",
                "every live-capture target requires frozen commands",
            )

        rows: list[dict[str, Any]] = []
        for device_id in device_ids:
            device = detached[device_id]
            base_plan = self._policy_resolver_factory().build_plan(
                device=device,
                action="patrol",
                mode="parser_validation",
            )
            plan = base_plan.to_metadata()
            plan.update(
                {
                    "device_id": device_id,
                    "action": "patrol",
                    "mode": "parser_validation",
                    "profile_name": "rollout-live-capture",
                    "profile_version_id": None,
                    "policy_source": "rollout_frozen_target",
                    "binding_id": None,
                    "selector": {
                        "rollout_stage_execution_id": execution_id,
                    },
                    "check_item_shadow_plan": None,
                    "check_item_ids": [],
                    "runtime_manifest_id": None,
                    "runtime_manifest_digest": None,
                    "collection_items": [
                        {
                            "command": command,
                            "action": "patrol",
                            "collection_item_id": None,
                            "fallback_rank": 0,
                            "check_item_bindings": [],
                        }
                        for command in commands_by_device[device_id]
                    ],
                }
            )
            plan["rule_governance_actor_id"] = selection_manifest.get("execution_user_id")
            plan["rule_governance_stage_id"] = execution_id
            rows.append(
                {
                    "device_id": device_id,
                    "device_name": device["device_name"],
                    "ip": device["management_ip"],
                    "plan": plan,
                    "commands": list(commands_by_device[device_id]),
                    "command_count": len(commands_by_device[device_id]),
                }
            )
        result = self._start_handler_factory(capture_batch_id).handle(
            StartPatrolCommand(
                rows=rows,
                target_count=len(rows),
                skipped_errors=[],
                launch_mode="direct",
                workflow_name=self.workflow_name,
                mode="parser_validation",
                save_baseline=False,
                ticket_id=f"ROLLOUT-{execution_id}",
                target_snapshot={
                    "target_type": "rollout_stage",
                    "target_label": f"Rollout stage {execution_id}",
                    "selector": {
                        "rollout_stage_execution_id": execution_id,
                        "target_manifest_digest": str(
                            selection_manifest.get("selection_basis_digest")
                            or ""
                        ),
                    },
                },
                actor="system:rollout-capture",
                report_purpose=SHADOW_VALIDATION,
            )
        )
        return {
            "status": "capturing",
            "capture_batch_id": str(result["patrol_batch_id"]),
            "dispatch_status": result.get("dispatch_status"),
            "queued_count": int(result.get("queued_count") or 0),
            "idempotent_replay": False,
        }

    def poll(
        self,
        *,
        capture_batch_id: str,
        selection_manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._session_scope() as db:
            batch = db.get(PatrolBatchRecord, capture_batch_id)
            if batch is None:
                raise RolloutCaptureError(
                    "capture_batch_missing",
                    f"live-capture batch not found: {capture_batch_id}",
                )
            target_rows = (
                db.query(PatrolBatchDeviceRecord)
                .filter(
                    PatrolBatchDeviceRecord.patrol_batch_id
                    == capture_batch_id
                )
                .all()
            )
            if not target_rows:
                return {
                    "status": "capturing",
                    "capture_batch_id": capture_batch_id,
                    "reason": "capture targets are not dispatched yet",
                }
            failed_targets = [
                item
                for item in target_rows
                if str(item.selection_status) == "skipped"
                or str(item.dispatch_status) == "failed"
            ]
            if failed_targets:
                detail = "; ".join(
                    f"{item.device_id}:{item.failure_code or item.error_message}"
                    for item in failed_targets
                )
                return {
                    "status": "failed",
                    "failure_code": "capture_target_failed",
                    "error": detail,
                    "capture_batch_id": capture_batch_id,
                }
            logical_ids = ["patrol-" + str(item.celery_task_id) for item in target_rows if item.celery_task_id]
            if len(logical_ids) != len(target_rows):
                return {"status": "capturing", "capture_batch_id": capture_batch_id,
                        "reason": "capture tasks are still being dispatched"}
            runs = db.query(PatrolRun).filter(PatrolRun.logical_run_id.in_(logical_ids)).all()
            latest = {}
            for run in runs:
                previous = latest.get(run.logical_run_id)
                if previous is None or run.attempt_number > previous.attempt_number:
                    latest[run.logical_run_id] = run
            if len(latest) != len(logical_ids):
                return {"status": "capturing", "capture_batch_id": capture_batch_id}
            if any(run.status in {"failed", "cancelled"} for run in latest.values()):
                return {"status": "failed", "failure_code": "capture_task_failed", "capture_batch_id": capture_batch_id}
            if any(run.status != "completed" for run in latest.values()):
                return {"status": "capturing", "capture_batch_id": capture_batch_id}
            snapshot_run_ids = sorted(str(run.id) for run in latest.values())
            snapshots = (
                db.query(CommandSnapshot)
                .filter(CommandSnapshot.run_id.in_(snapshot_run_ids))
                .order_by(
                    CommandSnapshot.observed_at,
                    CommandSnapshot.created_at,
                    CommandSnapshot.id,
                )
                .all()
            )
            target_to_run = {str(item.device_id): str(latest["patrol-" + str(item.celery_task_id)].id)
                             for item in target_rows}
            snapshot_payloads = [
                {
                    "id": str(item.id),
                    "run_id": str(item.run_id),
                    "device_id": str(
                        item.inventory_device_id or item.device_id
                    ),
                    "canonical_command": ParserPolicy._basic_normalize(
                        str(item.canonical_command)
                    ),
                    "raw_text_hash": str(item.raw_text_hash),
                    "observed_at": item.observed_at,
                    "created_at": item.created_at,
                }
                for item in snapshots
            ]

        bindings: dict[str, dict[str, Any]] = {}
        blockers: list[str] = []
        for target in selection_manifest.get("targets") or []:
            target = dict(target)
            device_id = str(target["device_id"])
            run_id = target_to_run.get(device_id)
            by_command = {
                str(item["canonical_command"]): item
                for item in snapshot_payloads
                if str(item["device_id"]) == device_id
                and str(item["run_id"]) == str(run_id)
            }
            raw_commands_by_track = dict(
                target.get("commands_by_track") or {}
            )
            if not raw_commands_by_track:
                legacy_commands = list(target.get("commands") or [])
                raw_commands_by_track = {
                    "baseline": legacy_commands,
                    "candidate": legacy_commands,
                }
            expected_by_track = {
                track: [
                    ParserPolicy._basic_normalize(str(item))
                    for item in raw_commands_by_track.get(track) or []
                ]
                for track in ("baseline", "candidate")
            }
            expected_commands = list(
                dict.fromkeys(
                    [
                        *expected_by_track["baseline"],
                        *expected_by_track["candidate"],
                    ]
                )
            )
            missing = [
                command
                for command in expected_commands
                if command not in by_command
            ]
            if missing:
                blockers.append(
                    f"{device_id}/{target['target_key']} missing commands: "
                    + ", ".join(missing)
                )
                continue
            chosen = [by_command[item] for item in expected_commands]
            snapshot_ids_by_track = {
                track: [
                    str(by_command[command]["id"])
                    for command in commands
                ]
                for track, commands in expected_by_track.items()
            }
            periods_by_track = {
                track: (
                    [
                        {
                            "period_key": str(run_id),
                            "observed_at": max(
                                str(by_command[command]["observed_at"])
                                for command in commands
                            ),
                            "snapshot_ids": [
                                str(by_command[command]["id"])
                                for command in commands
                            ],
                        }
                    ]
                    if commands
                    else []
                )
                for track, commands in expected_by_track.items()
            }
            bindings[str(target["target_key"])] = {
                "snapshot_ids": [str(item["id"]) for item in chosen],
                "periods": [
                    {
                        "period_key": str(run_id),
                        "observed_at": max(
                            str(item["observed_at"]) for item in chosen
                        ),
                        "snapshot_ids": [
                            str(item["id"]) for item in chosen
                        ],
                    }
                ],
                "snapshot_ids_by_track": snapshot_ids_by_track,
                "periods_by_track": periods_by_track,
            }
        if blockers:
            return {
                "status": "failed",
                "failure_code": "capture_evidence_incomplete",
                "error": "; ".join(blockers),
                "capture_batch_id": capture_batch_id,
            }
        bound_ids = sorted(
            {
                snapshot_id
                for value in bindings.values()
                for snapshot_id in value["snapshot_ids"]
            }
        )
        return {
            "status": "completed",
            "capture_batch_id": capture_batch_id,
            "snapshot_bindings": bindings,
            "snapshot_ids": bound_ids,
            "snapshot_run_ids": snapshot_run_ids,
            "workflow_run_ids": [], "execution_run_ids": snapshot_run_ids,
            "target_count": len(bindings),
            "snapshot_count": len(bound_ids),
        }


__all__ = [
    "ProductionPatrolCaptureAdapter",
    "RolloutCaptureAdapter",
    "RolloutCaptureError",
]

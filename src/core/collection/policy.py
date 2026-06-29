"""Version-ready collection policy primitives."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from src.infrastructure.db.models import (
    CollectionExecutionPlanRecord,
    CommandProfileRecord,
    CommandProfileVersionRecord,
)
from src.infrastructure.db.models import CollectionProfileBindingRecord
from src.infrastructure.db.postgres import get_db_session


@dataclass(frozen=True)
class CollectionItem:
    command: str
    action: str
    collection_item_id: str | None = None


@dataclass(frozen=True)
class ExecutionPlan:
    device_id: str
    action: str
    mode: str
    profile_name: str
    profile_version_id: str | None
    commands: tuple[CollectionItem, ...]
    credential_ref: str | None = None
    collector_options: dict[str, Any] | None = None
    policy_source: str = "default"
    binding_id: str | None = None
    selector: dict[str, Any] | None = None

    def command_texts(self) -> list[str]:
        return [item.command for item in self.commands]

    def to_metadata(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "action": self.action,
            "mode": self.mode,
            "profile_name": self.profile_name,
            "profile_version_id": self.profile_version_id,
            "credential_ref": self.credential_ref,
            "collector_options": dict(self.collector_options or {}),
            "policy_source": self.policy_source,
            "binding_id": self.binding_id,
            "selector": self.selector,
            "collection_items": [
                {
                    "command": item.command,
                    "action": item.action,
                    "collection_item_id": item.collection_item_id,
                }
                for item in self.commands
            ],
        }


def execution_plan_from_metadata(data: dict[str, Any]) -> ExecutionPlan:
    """Build a validated execution plan from API/workflow metadata."""
    if not isinstance(data, dict):
        raise ValueError("execution_plan must be an object")
    device_id = str(data.get("device_id") or "").strip()
    action = str(data.get("action") or "").strip()
    mode = str(data.get("mode") or "production").strip()
    if not device_id:
        raise ValueError("execution_plan.device_id is required")
    if action not in {"patrol", "backup"}:
        raise ValueError("execution_plan.action must be patrol or backup")
    items = data.get("collection_items") or data.get("commands") or []
    if not isinstance(items, list) or not items:
        raise ValueError("execution_plan.collection_items is required")
    collection_items: list[CollectionItem] = []
    for item in items:
        if isinstance(item, str):
            command = item.strip()
            item_action = action
            collection_item_id = None
        elif isinstance(item, dict):
            command = str(item.get("command") or "").strip()
            item_action = str(item.get("action") or action).strip()
            collection_item_id = (
                str(item.get("collection_item_id"))
                if item.get("collection_item_id")
                else None
            )
        else:
            raise ValueError("execution_plan.collection_items must contain strings or objects")
        if not command:
            raise ValueError("execution_plan command cannot be empty")
        if item_action != action:
            raise ValueError("execution_plan item action must match plan action")
        collection_items.append(
            CollectionItem(
                command=command,
                action=item_action,
                collection_item_id=collection_item_id,
            )
        )
    return ExecutionPlan(
        device_id=device_id,
        action=action,
        mode=mode,
        profile_name=str(data.get("profile_name") or "explicit-plan"),
        profile_version_id=(
            str(data.get("profile_version_id"))
            if data.get("profile_version_id")
            else None
        ),
        credential_ref=(
            str(data.get("credential_ref")).strip()
            if data.get("credential_ref")
            else None
        ),
        collector_options=dict(data.get("collector_options") or {}),
        commands=tuple(collection_items),
        policy_source=str(data.get("policy_source") or "explicit"),
        binding_id=str(data.get("binding_id")) if data.get("binding_id") else None,
        selector=dict(data.get("selector") or {}) if data.get("selector") else None,
    )


def _stable_plan_sha256(plan_metadata: dict[str, Any]) -> str:
    payload = json.dumps(plan_metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ExecutionPlanRepository:
    """Persist immutable execution-plan snapshots for launch audit and replay."""

    def create_plan(
        self,
        plan: ExecutionPlan | dict[str, Any],
        *,
        status: str = "planned",
        launch_source: str | None = None,
        patrol_batch_id: str | None = None,
    ) -> dict[str, Any]:
        metadata = plan.to_metadata() if isinstance(plan, ExecutionPlan) else dict(plan)
        validated = execution_plan_from_metadata(metadata)
        normalized = validated.to_metadata()
        now = datetime.now(timezone.utc)
        row = CollectionExecutionPlanRecord(
            id=str(uuid.uuid4()),
            device_id=validated.device_id,
            action=validated.action,
            mode=validated.mode,
            profile_name=validated.profile_name,
            profile_version_id=validated.profile_version_id,
            binding_id=validated.binding_id,
            policy_source=validated.policy_source,
            credential_ref=validated.credential_ref,
            collector_options_json=dict(validated.collector_options or {}),
            selector_json=dict(validated.selector or {}),
            plan_json=normalized,
            command_count=len(validated.commands),
            plan_sha256=_stable_plan_sha256(normalized),
            status=status,
            launch_source=launch_source,
            patrol_batch_id=patrol_batch_id,
            created_at=now,
            updated_at=now,
        )
        with get_db_session() as db:
            db.add(row)
            db.flush()
            return self._row_to_dict(row)

    def mark_launched(
        self,
        plan_id: str,
        *,
        launch_source: str,
        patrol_batch_id: str | None = None,
        workflow_run_id: str | None = None,
        celery_task_id: str | None = None,
    ) -> dict[str, Any]:
        with get_db_session() as db:
            row = db.get(CollectionExecutionPlanRecord, plan_id)
            if row is None:
                raise ValueError(f"execution plan not found: {plan_id}")
            now = datetime.now(timezone.utc)
            row.status = "launched"
            row.launch_source = launch_source
            row.patrol_batch_id = patrol_batch_id or row.patrol_batch_id
            row.workflow_run_id = workflow_run_id
            row.celery_task_id = celery_task_id
            row.launched_at = now
            row.updated_at = now
            db.flush()
            return self._row_to_dict(row)

    def mark_failed(
        self,
        plan_id: str,
        *,
        failure_code: str,
        error: str,
    ) -> dict[str, Any]:
        with get_db_session() as db:
            row = db.get(CollectionExecutionPlanRecord, plan_id)
            if row is None:
                raise ValueError(f"execution plan not found: {plan_id}")
            row.status = "launch_failed"
            row.failure_code = failure_code
            row.error = error
            row.updated_at = datetime.now(timezone.utc)
            db.flush()
            return self._row_to_dict(row)

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        with get_db_session() as db:
            row = db.get(CollectionExecutionPlanRecord, plan_id)
            return self._row_to_dict(row) if row is not None else None

    @staticmethod
    def _row_to_dict(row: CollectionExecutionPlanRecord) -> dict[str, Any]:
        data = row.to_dict()
        data["execution_plan_id"] = row.id
        return data


class CollectionPolicyResolver:
    """Build collector execution plans from inventory metadata.

    This is intentionally deterministic for now. The public interface is the
    part that matters: device-patrol receives an ExecutionPlan instead of owning
    command-selection policy.
    """

    def build_plan(self, *, device: dict[str, Any], action: str, mode: str) -> ExecutionPlan:
        attributes = dict(device.get("attributes") or {})
        configured = attributes.get(f"{action}_commands") if action != "patrol" else None
        profile_name = str(device.get("command_file") or f"default-{action}")
        credential_ref = str(device.get("credential_ref") or "").strip() or None
        collector_options = self._collector_options(attributes, action)
        active_profile = None
        policy_source = "default"
        binding_id = None
        selector = None
        profile_version_id = attributes.get(f"{action}_profile_version_id")
        if isinstance(configured, list) and configured:
            commands = [str(item) for item in configured]
            policy_source = "device_attributes"
        else:
            if device.get("command_file"):
                active_profile = self._load_active_profile(profile_name, action)
                policy_source = "device_profile" if active_profile else "default"
            if not active_profile:
                active_profile = self._load_bound_profile(device, action)
                if active_profile:
                    profile_name = str(active_profile["profile_name"])
                    policy_source = "profile_binding"
                    binding_id = str(active_profile["binding_id"])
                    selector = dict(active_profile["selector"])
            profile_version_id = profile_version_id or (
                active_profile.get("profile_version_id") if active_profile else None
            )
            commands = list(active_profile["commands"]) if active_profile else []
        return ExecutionPlan(
            device_id=str(device.get("device_id") or ""),
            action=action,
            mode=mode,
            profile_name=profile_name,
            profile_version_id=str(profile_version_id) if profile_version_id else None,
            credential_ref=credential_ref,
            collector_options=collector_options,
            commands=tuple(
                CollectionItem(command=command, action=action)
                for command in commands
            ),
            policy_source=policy_source,
            binding_id=binding_id,
            selector=selector,
        )

    @staticmethod
    def _collector_options(attributes: dict[str, Any], action: str) -> dict[str, Any]:
        options: dict[str, Any] = {}
        shared = attributes.get("collector_options")
        if isinstance(shared, dict):
            options.update(shared)
        action_specific = attributes.get(f"{action}_collector_options")
        if isinstance(action_specific, dict):
            options.update(action_specific)
        return options

    def _load_active_profile(
        self,
        profile_name: str,
        action: str,
    ) -> dict[str, Any] | None:
        try:
            with get_db_session() as db:
                row = (
                    db.query(CommandProfileRecord, CommandProfileVersionRecord)
                    .join(
                        CommandProfileVersionRecord,
                        CommandProfileVersionRecord.profile_id
                        == CommandProfileRecord.id,
                    )
                    .filter(CommandProfileRecord.name == profile_name)
                    .filter(CommandProfileRecord.status == "active")
                    .filter(CommandProfileVersionRecord.status == "active")
                    .first()
                )
                if not row:
                    return None
                _profile, version = row
                items = [
                    item
                    for item in version.items_json or []
                    if str(item.get("action") or action) == action
                ]
                commands = [str(item["command"]) for item in items if item.get("command")]
                if not commands:
                    return None
                return {
                    "profile_name": _profile.name,
                    "profile_version_id": version.id,
                    "commands": commands,
                }
        except Exception:
            return None

    def _load_bound_profile(
        self,
        device: dict[str, Any],
        action: str,
    ) -> dict[str, Any] | None:
        try:
            with get_db_session() as db:
                rows = (
                    db.query(
                        CollectionProfileBindingRecord,
                        CommandProfileRecord,
                        CommandProfileVersionRecord,
                    )
                    .join(
                        CommandProfileVersionRecord,
                        CommandProfileVersionRecord.id
                        == CollectionProfileBindingRecord.profile_version_id,
                    )
                    .join(
                        CommandProfileRecord,
                        CommandProfileRecord.id
                        == CommandProfileVersionRecord.profile_id,
                    )
                    .filter(CollectionProfileBindingRecord.status == "active")
                    .filter(CommandProfileRecord.status == "active")
                    .filter(CommandProfileVersionRecord.status == "active")
                    .order_by(
                        CollectionProfileBindingRecord.priority.asc(),
                        CollectionProfileBindingRecord.created_at.asc(),
                    )
                    .all()
                )
                for binding, profile, version in rows:
                    selector = dict(binding.selector_json or {})
                    if not self._selector_matches(device, selector):
                        continue
                    items = [
                        item
                        for item in version.items_json or []
                        if str(item.get("action") or action) == action
                    ]
                    commands = [
                        str(item["command"]) for item in items if item.get("command")
                    ]
                    if not commands:
                        continue
                    return {
                        "binding_id": binding.id,
                        "selector": selector,
                        "profile_name": profile.name,
                        "profile_version_id": version.id,
                        "commands": commands,
                    }
        except Exception:
            return None
        return None

    @staticmethod
    def _selector_matches(device: dict[str, Any], selector: dict[str, Any]) -> bool:
        if not selector:
            return True
        for key, expected in selector.items():
            if key == "action":
                continue
            if expected is None:
                continue
            actual = device.get(key)
            if actual is None and key in {"group", "groups"}:
                actual = device.get("groups")
            if isinstance(expected, list):
                expected_values = {str(item).lower() for item in expected}
                if isinstance(actual, list):
                    actual_values = {str(item).lower() for item in actual}
                    if not actual_values.intersection(expected_values):
                        return False
                elif str(actual or "").lower() not in expected_values:
                    return False
            elif isinstance(actual, list):
                if str(expected).lower() not in {str(item).lower() for item in actual}:
                    return False
            elif str(actual or "").lower() != str(expected).lower():
                return False
        return True

class CommandProfileRepository:
    def get_profile_version(self, profile_version_id: str) -> dict[str, Any] | None:
        with get_db_session() as db:
            row = (
                db.query(CommandProfileRecord, CommandProfileVersionRecord)
                .join(
                    CommandProfileVersionRecord,
                    CommandProfileVersionRecord.profile_id == CommandProfileRecord.id,
                )
                .filter(CommandProfileVersionRecord.id == profile_version_id)
                .first()
            )
            if not row:
                return None
            profile, version = row
            return {
                **profile.to_dict(),
                "profile_id": profile.id,
                "profile_name": profile.name,
                "profile_version_id": version.id,
                "version": version.version,
                "version_status": version.status,
                "items": version.items_json or [],
                "coverage": version.coverage_json,
            }

    def list_profiles(self) -> list[dict[str, Any]]:
        with get_db_session() as db:
            rows = (
                db.query(CommandProfileRecord, CommandProfileVersionRecord)
                .outerjoin(
                    CommandProfileVersionRecord,
                    CommandProfileVersionRecord.profile_id
                    == CommandProfileRecord.id,
                )
                .order_by(CommandProfileRecord.name, CommandProfileVersionRecord.version.desc())
                .all()
            )
            return [
                {
                    **profile.to_dict(),
                    "profile_id": profile.id,
                    "profile_name": profile.name,
                    "profile_version_id": version.id if version else None,
                    "version": version.version if version else None,
                    "version_status": version.status if version else None,
                    "items": version.items_json if version else [],
                    "coverage": version.coverage_json if version else None,
                }
                for profile, version in rows
            ]

    def list_bindings(self) -> list[dict[str, Any]]:
        with get_db_session() as db:
            rows = (
                db.query(
                    CollectionProfileBindingRecord,
                    CommandProfileRecord,
                    CommandProfileVersionRecord,
                )
                .join(
                    CommandProfileVersionRecord,
                    CommandProfileVersionRecord.id
                    == CollectionProfileBindingRecord.profile_version_id,
                )
                .join(
                    CommandProfileRecord,
                    CommandProfileRecord.id == CommandProfileVersionRecord.profile_id,
                )
                .order_by(
                    CollectionProfileBindingRecord.status,
                    CollectionProfileBindingRecord.priority.asc(),
                    CommandProfileRecord.name,
                )
                .all()
            )
            return [
                {
                    **binding.to_dict(),
                    "binding_id": binding.id,
                    "profile_id": profile.id,
                    "profile_name": profile.name,
                    "profile_version": version.version,
                    "version_status": version.status,
                }
                for binding, profile, version in rows
            ]

    def upsert_binding(
        self,
        *,
        profile_version_id: str,
        selector: dict[str, Any],
        priority: int = 100,
        status: str = "active",
    ) -> str:
        selector = dict(selector or {})
        with get_db_session() as db:
            version = db.get(CommandProfileVersionRecord, profile_version_id)
            if version is None:
                raise ValueError(f"command profile version not found: {profile_version_id}")
            existing = (
                db.query(CollectionProfileBindingRecord)
                .filter(
                    CollectionProfileBindingRecord.profile_version_id
                    == profile_version_id,
                    CollectionProfileBindingRecord.selector_json == selector,
                )
                .first()
            )
            if existing is None:
                existing = CollectionProfileBindingRecord(
                    id=str(uuid.uuid4()),
                    profile_version_id=profile_version_id,
                    selector_json=selector,
                )
                db.add(existing)
            existing.priority = priority
            existing.status = status
            return existing.id

    def set_binding_status(self, binding_id: str, status: str) -> dict[str, Any]:
        if status not in {"active", "disabled"}:
            raise ValueError("binding status must be active or disabled")
        with get_db_session() as db:
            row = db.get(CollectionProfileBindingRecord, binding_id)
            if row is None:
                raise ValueError(f"command profile binding not found: {binding_id}")
            row.status = status
            row.updated_at = datetime.now(timezone.utc)
            return row.to_dict()

    def create_profile_version(
        self,
        *,
        name: str,
        action: str,
        commands: list[str],
        created_by: str | None = None,
        status: str = "draft",
    ) -> str:
        if status not in {"draft", "active"}:
            raise ValueError("profile version status must be draft or active")
        with get_db_session() as db:
            profile = (
                db.query(CommandProfileRecord)
                .filter(CommandProfileRecord.name == name)
                .first()
            )
            if profile is None:
                profile = CommandProfileRecord(
                    id=str(uuid.uuid4()),
                    name=name,
                    status="draft",
                    current_version=0,
                )
                db.add(profile)
                db.flush()
            latest_version = (
                db.query(CommandProfileVersionRecord)
                .filter(CommandProfileVersionRecord.profile_id == profile.id)
                .order_by(CommandProfileVersionRecord.version.desc())
                .first()
            )
            next_version = int(latest_version.version) + 1 if latest_version else 1
            version = CommandProfileVersionRecord(
                id=str(uuid.uuid4()),
                profile_id=profile.id,
                version=next_version,
                status=status,
                items_json=[
                    {"action": action, "command": command}
                    for command in commands
                ],
                created_by=created_by,
            )
            if status == "active":
                now = datetime.now(timezone.utc)
                db.query(CommandProfileVersionRecord).filter(
                    CommandProfileVersionRecord.profile_id == profile.id,
                    CommandProfileVersionRecord.status == "active",
                ).update({"status": "deprecated", "deprecated_at": now})
                version.activated_at = now
                profile.status = "active"
                profile.current_version = next_version
            db.add(version)
            return version.id

    def activate_profile_version(self, profile_version_id: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with get_db_session() as db:
            version = db.get(CommandProfileVersionRecord, profile_version_id)
            if version is None:
                raise ValueError(f"command profile version not found: {profile_version_id}")
            profile = db.get(CommandProfileRecord, version.profile_id)
            if profile is None:
                raise ValueError(f"command profile not found: {version.profile_id}")
            db.query(CommandProfileVersionRecord).filter(
                CommandProfileVersionRecord.profile_id == profile.id,
                CommandProfileVersionRecord.status == "active",
                CommandProfileVersionRecord.id != profile_version_id,
            ).update({"status": "deprecated", "deprecated_at": now})
            version.status = "active"
            version.activated_at = version.activated_at or now
            version.deprecated_at = None
            profile.status = "active"
            profile.current_version = version.version
            profile.updated_at = now
            return {
                **profile.to_dict(),
                "profile_id": profile.id,
                "profile_name": profile.name,
                "profile_version_id": version.id,
                "version": version.version,
                "version_status": version.status,
                "items": version.items_json or [],
                "coverage": version.coverage_json,
            }

    def validate_profile_activation(self, profile_version_id: str) -> None:
        with get_db_session() as db:
            version = db.get(CommandProfileVersionRecord, profile_version_id)
            if version is None:
                raise ValueError(f"command profile version not found: {profile_version_id}")
            coverage = dict(version.coverage_json or {})
            if not coverage:
                raise ValueError("command profile impact evidence is required before activation")

    def delete_profile_version(self, profile_version_id: str) -> dict[str, Any]:
        with get_db_session() as db:
            version = db.get(CommandProfileVersionRecord, profile_version_id)
            if version is None:
                raise ValueError(f"command profile version not found: {profile_version_id}")
            if version.status == "active":
                raise ValueError("active command profile version cannot be deleted; deprecate it first")
            profile = db.get(CommandProfileRecord, version.profile_id)
            profile_id = version.profile_id
            deleted_version = version.to_dict()
            db.delete(version)
            db.flush()
            remaining_count = (
                db.query(CommandProfileVersionRecord)
                .filter(CommandProfileVersionRecord.profile_id == profile_id)
                .count()
            )
            profile_deleted = False
            if remaining_count == 0 and profile is not None:
                db.delete(profile)
                profile_deleted = True
            elif profile is not None:
                profile.updated_at = datetime.now(timezone.utc)
            return {
                "success": True,
                "profile_id": profile_id,
                "profile_version_id": profile_version_id,
                "deleted_version": deleted_version,
                "profile_deleted": profile_deleted,
            }

    def record_profile_coverage(
        self,
        profile_version_id: str,
        coverage: dict[str, Any],
    ) -> dict[str, Any]:
        with get_db_session() as db:
            version = db.get(CommandProfileVersionRecord, profile_version_id)
            if version is None:
                raise ValueError(f"command profile version not found: {profile_version_id}")
            version.coverage_json = {
                **dict(coverage or {}),
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
            profile = db.get(CommandProfileRecord, version.profile_id)
            return {
                **(profile.to_dict() if profile else {}),
                "profile_id": profile.id if profile else version.profile_id,
                "profile_name": profile.name if profile else None,
                "profile_version_id": version.id,
                "version": version.version,
                "version_status": version.status,
                "items": version.items_json or [],
                "coverage": version.coverage_json,
            }

    def deprecate_profile_version(self, profile_version_id: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with get_db_session() as db:
            version = db.get(CommandProfileVersionRecord, profile_version_id)
            if version is None:
                raise ValueError(f"command profile version not found: {profile_version_id}")
            profile = db.get(CommandProfileRecord, version.profile_id)
            version.status = "deprecated"
            version.deprecated_at = now
            if profile is not None:
                active = (
                    db.query(CommandProfileVersionRecord)
                    .filter(
                        CommandProfileVersionRecord.profile_id == profile.id,
                        CommandProfileVersionRecord.status == "active",
                        CommandProfileVersionRecord.id != profile_version_id,
                    )
                    .order_by(CommandProfileVersionRecord.version.desc())
                    .first()
                )
                if active:
                    profile.status = "active"
                    profile.current_version = int(active.version)
                    profile.updated_at = now
                else:
                    profile.status = "deprecated"
                    profile.current_version = 0
                    profile.updated_at = now
            return version.to_dict()

    def rollback_profile_version(self, profile_version_id: str) -> dict[str, Any]:
        return self.activate_profile_version(profile_version_id)

    def upsert_active_profile(
        self,
        *,
        name: str,
        action: str,
        commands: list[str],
        created_by: str | None = None,
    ) -> str:
        version_id = self.create_profile_version(
            name=name,
            action=action,
            commands=commands,
            created_by=created_by,
            status="draft",
        )
        self.activate_profile_version(version_id)
        return version_id

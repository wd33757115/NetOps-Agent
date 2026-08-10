"""Version-safe check-item selection for patrol profiles."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

from src.core.check_items.presentation import check_item_display_name
from src.infrastructure.db.models import (
    CheckItemRecord,
    CheckItemVersionRecord,
    CommandProfileVersionRecord,
    PatrolProfileCheckItemRecord,
)
from src.infrastructure.db.postgres import get_db_session


class PatrolProfileCheckItemRepository:
    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
    ) -> None:
        self._session_scope = session_scope

    @staticmethod
    def _binding_id(profile_version_id: str, check_item_version_id: str) -> str:
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"netops/profile-check-item/{profile_version_id}/{check_item_version_id}",
            )
        )

    def set_draft_selection(
        self,
        profile_version_id: str,
        check_item_ids: Iterable[str],
        *,
        actor: str,
    ) -> list[dict[str, Any]]:
        selected = tuple(sorted(set(str(item) for item in check_item_ids if str(item))))
        if not selected:
            raise ValueError("patrol profile requires at least one check item")
        with self._session_scope() as db:
            profile = db.get(CommandProfileVersionRecord, profile_version_id)
            if profile is None:
                raise ValueError("command profile version not found")
            if str(profile.status) != "draft":
                raise ValueError("active or deprecated profile versions are immutable")
            versions = (
                db.query(CheckItemVersionRecord)
                .filter(
                    CheckItemVersionRecord.check_item_id.in_(selected),
                    CheckItemVersionRecord.status == "active",
                )
                .all()
            )
            by_id = {str(item.check_item_id): item for item in versions}
            missing = sorted(set(selected) - set(by_id))
            if missing:
                raise ValueError(
                    "active check items not found: " + ", ".join(missing)
                )
            db.query(PatrolProfileCheckItemRecord).filter(
                PatrolProfileCheckItemRecord.profile_version_id == profile_version_id
            ).delete(synchronize_session=False)
            now = datetime.now(timezone.utc)
            rows = [
                PatrolProfileCheckItemRecord(
                    id=self._binding_id(profile_version_id, by_id[item].id),
                    profile_version_id=profile_version_id,
                    check_item_version_id=by_id[item].id,
                    priority=100,
                    required=True,
                    status="active",
                    created_by=actor,
                    created_at=now,
                )
                for item in selected
            ]
            db.add_all(rows)
            db.flush()
            return [item.to_dict() for item in rows]

    def list_selection(
        self,
        profile_version_id: str,
        *,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        return self.list_selections(
            [profile_version_id],
            active_only=active_only,
        ).get(profile_version_id, [])

    def list_selections(
        self,
        profile_version_ids: Iterable[str],
        *,
        active_only: bool = True,
    ) -> dict[str, list[dict[str, Any]]]:
        version_ids = tuple(
            sorted(
                {
                    str(item).strip()
                    for item in profile_version_ids
                    if str(item).strip()
                }
            )
        )
        if not version_ids:
            return {}
        with self._session_scope() as db:
            query = (
                db.query(
                    PatrolProfileCheckItemRecord,
                    CheckItemVersionRecord,
                    CheckItemRecord,
                )
                .join(
                    CheckItemVersionRecord,
                    CheckItemVersionRecord.id
                    == PatrolProfileCheckItemRecord.check_item_version_id,
                )
                .join(
                    CheckItemRecord,
                    CheckItemRecord.id
                    == CheckItemVersionRecord.check_item_id,
                )
                .filter(
                    PatrolProfileCheckItemRecord.profile_version_id.in_(
                        version_ids
                    )
                )
            )
            if active_only:
                query = query.filter(
                    PatrolProfileCheckItemRecord.status == "active",
                    CheckItemVersionRecord.status == "active",
                )
            rows = query.order_by(
                PatrolProfileCheckItemRecord.profile_version_id,
                PatrolProfileCheckItemRecord.priority.desc(),
                CheckItemVersionRecord.check_item_id,
            ).all()
            grouped = {version_id: [] for version_id in version_ids}
            for binding, version, check_item in rows:
                grouped[str(binding.profile_version_id)].append(
                    {
                        **binding.to_dict(),
                        "check_item_id": version.check_item_id,
                        "check_item_name": check_item_display_name(
                            check_item.id,
                            check_item.name,
                        ),
                        "check_item_category": check_item.category,
                        "check_item_version": version.version,
                        "result_mode": version.result_mode,
                        "assessment_mode": version.assessment_mode,
                        "report_projection": version.report_projection,
                    }
                )
            return grouped

    def active_check_item_ids(self, profile_version_id: str) -> tuple[str, ...]:
        return tuple(
            str(item["check_item_id"])
            for item in self.list_selection(profile_version_id)
        )

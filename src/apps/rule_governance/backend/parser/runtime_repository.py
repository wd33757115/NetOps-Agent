"""Read-only Parser asset repository used by runtime resolution."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy import func

from src.infrastructure.db.models import (
    ParserCommandAlias,
    ParserDeviceProfile,
    ParserIdentitySignature,
    ParserTemplate,
)
from src.infrastructure.db.postgres import get_db_session


class ParserRuntimeRepository:
    """Keep runtime parsing independent from jobs, approvals and outbox writes."""

    def __init__(
        self,
        backend: Any | None = None,
        *,
        session_scope: Callable[[], AbstractContextManager[Any]] = (
            get_db_session
        ),
    ) -> None:
        self._backend = backend
        self._session_scope = session_scope

    def list_command_aliases(self, *, status: str = "confirmed") -> list[dict[str, Any]]:
        if self._backend is not None:
            return self._backend.list_command_aliases(status=status)
        with self._session_scope() as db:
            return [
                row.to_dict()
                for row in db.query(ParserCommandAlias)
                .filter(ParserCommandAlias.status == status)
                .order_by(ParserCommandAlias.alias)
                .all()
            ]

    def resolve_active(
        self,
        *,
        vendor: str,
        model: str,
        family: str | None,
        command: str,
    ) -> ParserTemplate | None:
        if self._backend is not None:
            return self._backend.resolve_active(
                vendor=vendor,
                model=model,
                family=family,
                command=command,
            )
        profiles = [model] + ([family] if family and family != model else [])
        with self._session_scope() as db:
            rows = (
                db.query(ParserTemplate)
                .filter(
                    ParserTemplate.vendor == vendor,
                    ParserTemplate.profile.in_(profiles),
                    ParserTemplate.canonical_command == command,
                    ParserTemplate.status == "active",
                )
                .all()
            )
            rows.sort(key=lambda row: profiles.index(row.profile))
            if not rows:
                return None
            db.expunge(rows[0])
            return rows[0]

    def get_template(self, template_id: str) -> ParserTemplate | None:
        if self._backend is not None:
            return self._backend.get_template(template_id)
        with self._session_scope() as db:
            row = db.get(ParserTemplate, template_id)
            if row is not None:
                db.expunge(row)
            return row

    def find_device_profile(self, vendor: str, model: str) -> dict[str, Any] | None:
        if self._backend is not None:
            return self._backend.find_device_profile(vendor, model)
        with self._session_scope() as db:
            row = (
                db.query(ParserDeviceProfile)
                .filter(
                    func.lower(ParserDeviceProfile.vendor) == vendor.lower(),
                    func.lower(ParserDeviceProfile.model) == model.lower(),
                    ParserDeviceProfile.status == "confirmed",
                )
                .first()
            )
            return row.to_dict() if row else None

    def list_identity_signatures(
        self,
        *,
        status: str = "confirmed",
    ) -> list[dict[str, Any]]:
        if self._backend is not None:
            return self._backend.list_identity_signatures(status=status)
        with self._session_scope() as db:
            return [
                row.to_dict()
                for row in db.query(ParserIdentitySignature)
                .filter(ParserIdentitySignature.status == status)
                .order_by(ParserIdentitySignature.vendor, ParserIdentitySignature.model)
                .all()
            ]

    def has_trusted_family(self, *, vendor: str, family: str) -> bool:
        if self._backend is not None:
            return self._backend.has_trusted_family(vendor=vendor, family=family)
        with self._session_scope() as db:
            return (
                db.query(ParserTemplate)
                .filter(
                    ParserTemplate.vendor == vendor,
                    ParserTemplate.family == family,
                    ParserTemplate.status == "active",
                )
                .first()
                is not None
            )

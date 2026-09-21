"""Governed adapters for immutable pre-RuntimeBundle rollout baselines."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any, Protocol

from src.apps.rule_governance.backend.check_items.runtime_bundle import (
    CheckItemRuntimeBundleService,
)
from src.infrastructure.db.models import (
    AuditLogRecord,
    RolloutLegacyRouteRecord,
)
from src.infrastructure.db.postgres import get_db_session

RUNTIME_BUNDLE_COMPAT_ADAPTER = "runtime_bundle_compat"
RUNTIME_BUNDLE_COMPAT_VERSION = "v1"


class LegacyRouteError(ValueError):
    """Raised when an immutable legacy route cannot be trusted."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class LegacyRouteAdapter(Protocol):
    """Translate one frozen legacy route into an isolated projection route."""

    kind: str
    version: str

    def projection_route(
        self,
        descriptor: Mapping[str, Any],
    ) -> dict[str, Any]: ...


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RuntimeBundleCompatibilityAdapter:
    """Use a frozen active RuntimeBundle as the executable legacy boundary."""

    kind = RUNTIME_BUNDLE_COMPAT_ADAPTER
    version = RUNTIME_BUNDLE_COMPAT_VERSION

    def projection_route(
        self,
        descriptor: Mapping[str, Any],
    ) -> dict[str, Any]:
        config = dict(descriptor.get("config_json") or {})
        version_id = str(config.get("runtime_bundle_version_id") or "").strip()
        bundle_digest = str(config.get("runtime_bundle_digest") or "").strip()
        if not version_id or not bundle_digest:
            raise LegacyRouteError(
                "legacy_route_config_invalid",
                "legacy RuntimeBundle adapter requires a version id and digest",
            )
        return {
            "mode": "runtime_bundle",
            "runtime_bundle_version_id": version_id,
            "expected_runtime_bundle_digest": bundle_digest,
        }


class LegacyRouteAdapterRegistry:
    """Explicit adapter registry; arbitrary import paths are never executed."""

    def __init__(
        self,
        adapters: tuple[LegacyRouteAdapter, ...] | None = None,
    ) -> None:
        configured = adapters or (RuntimeBundleCompatibilityAdapter(),)
        self._adapters = {
            (str(item.kind), str(item.version)): item
            for item in configured
        }

    def resolve(
        self,
        descriptor: Mapping[str, Any],
    ) -> LegacyRouteAdapter:
        key = (
            str(descriptor.get("adapter_kind") or ""),
            str(descriptor.get("adapter_version") or ""),
        )
        adapter = self._adapters.get(key)
        if adapter is None:
            raise LegacyRouteError(
                "legacy_adapter_unavailable",
                f"legacy rollout adapter is not installed: {key[0]}:{key[1]}",
            )
        return adapter


class RolloutLegacyRouteService:
    """Create and resolve immutable, scope-bound legacy baseline routes."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = (
            get_db_session
        ),
        *,
        adapter_registry: LegacyRouteAdapterRegistry | None = None,
    ) -> None:
        self._session_scope = session_scope
        self._adapters = adapter_registry or LegacyRouteAdapterRegistry()

    def create_runtime_bundle_route(
        self,
        *,
        route_name: str,
        runtime_bundle_version_id: str,
        actor: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        route_name = str(route_name or "").strip()
        actor = str(actor or "").strip()
        idempotency_key = str(idempotency_key or "").strip()
        if not route_name or not actor or not idempotency_key:
            raise LegacyRouteError(
                "legacy_route_input_invalid",
                "route_name, actor and idempotency_key are required",
            )
        bundle = CheckItemRuntimeBundleService(
            self._session_scope
        ).get_version(str(runtime_bundle_version_id or "").strip())
        if bundle is None:
            raise LegacyRouteError(
                "runtime_bundle_missing",
                "legacy route RuntimeBundle does not exist",
            )
        if str(bundle.get("status") or "") != "active":
            raise LegacyRouteError(
                "legacy_baseline_not_active",
                "legacy route RuntimeBundle must be active",
            )
        config = {
            "runtime_bundle_version_id": str(bundle["version_id"]),
            "runtime_bundle_digest": str(bundle["digest"]),
        }
        payload = {
            "schema_version": "rollout-legacy-route/v1",
            "route_name": route_name,
            "check_item_version_id": str(bundle["check_item_version_id"]),
            "implementation_version_id": str(
                bundle["implementation_version_id"]
            ),
            "adapter_kind": RUNTIME_BUNDLE_COMPAT_ADAPTER,
            "adapter_version": RUNTIME_BUNDLE_COMPAT_VERSION,
            "config": config,
        }
        digest = _digest(payload)
        route_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"netops/rollout-legacy-route/{digest}",
            )
        )
        now = _utcnow()
        with self._session_scope() as db:
            replay = (
                db.query(RolloutLegacyRouteRecord)
                .filter(
                    RolloutLegacyRouteRecord.idempotency_key
                    == idempotency_key
                )
                .one_or_none()
            )
            if replay is not None:
                if str(replay.digest) != digest:
                    raise LegacyRouteError(
                        "idempotency_conflict",
                        "idempotency key was already used for another route",
                    )
                return replay.to_dict()
            existing = db.get(RolloutLegacyRouteRecord, route_id)
            if existing is not None:
                return existing.to_dict()
            row = RolloutLegacyRouteRecord(
                id=route_id,
                route_name=route_name,
                check_item_version_id=payload["check_item_version_id"],
                implementation_version_id=payload[
                    "implementation_version_id"
                ],
                adapter_kind=RUNTIME_BUNDLE_COMPAT_ADAPTER,
                adapter_version=RUNTIME_BUNDLE_COMPAT_VERSION,
                config_json=config,
                digest=digest,
                status="active",
                idempotency_key=idempotency_key,
                created_by=actor,
                created_at=now,
            )
            db.add(row)
            db.add(
                AuditLogRecord(
                    id=str(uuid.uuid4()),
                    idempotency_key=(
                        f"rollout-legacy-route:create:{idempotency_key}"
                    ),
                    user_id=actor,
                    username=actor,
                    action="rollout_legacy_route_created",
                    resource_type="rollout_legacy_route",
                    resource_id=route_id,
                    detail={
                        "route_digest": digest,
                        "adapter_kind": RUNTIME_BUNDLE_COMPAT_ADAPTER,
                        "adapter_version": RUNTIME_BUNDLE_COMPAT_VERSION,
                    },
                    status="success",
                    created_at=now,
                )
            )
            db.flush()
            return row.to_dict()

    def resolve(
        self,
        route_id: str,
        digest: str,
        *,
        require_active: bool = True,
    ) -> dict[str, Any]:
        with self._session_scope() as db:
            row = db.get(RolloutLegacyRouteRecord, str(route_id or "").strip())
            if row is None:
                raise LegacyRouteError(
                    "legacy_route_missing",
                    f"legacy rollout route not found: {route_id}",
                )
            descriptor = row.to_dict()
        if str(descriptor["digest"]) != str(digest or "").strip():
            raise LegacyRouteError(
                "legacy_route_digest_mismatch",
                "legacy rollout route digest does not match the frozen route",
            )
        if require_active and descriptor["status"] != "active":
            raise LegacyRouteError(
                "legacy_route_inactive",
                "legacy rollout route is no longer active",
            )
        adapter = self._adapters.resolve(descriptor)
        projection_route = adapter.projection_route(descriptor)
        bundle = CheckItemRuntimeBundleService(
            self._session_scope
        ).get_version(
            str(projection_route["runtime_bundle_version_id"])
        )
        if bundle is None:
            raise LegacyRouteError(
                "runtime_bundle_missing",
                "legacy route RuntimeBundle disappeared",
            )
        if str(bundle.get("status") or "") != "active":
            raise LegacyRouteError(
                "legacy_baseline_not_active",
                "legacy route RuntimeBundle is no longer active",
            )
        if str(bundle.get("digest") or "") != str(
            projection_route["expected_runtime_bundle_digest"]
        ):
            raise LegacyRouteError(
                "legacy_route_dependency_drift",
                "legacy route RuntimeBundle digest drifted",
            )
        return {
            **descriptor,
            "projection_route": {
                "mode": "runtime_bundle",
                "runtime_bundle_version_id": str(bundle["version_id"]),
                "implementation_version_id": str(
                    bundle["implementation_version_id"]
                ),
            },
        }

    def get(self, route_id: str) -> dict[str, Any] | None:
        with self._session_scope() as db:
            row = db.get(RolloutLegacyRouteRecord, route_id)
            return row.to_dict() if row is not None else None

    def list(
        self,
        *,
        page: int = 1,
        page_size: int = 25,
        status: str | None = None,
    ) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))
        with self._session_scope() as db:
            query = db.query(RolloutLegacyRouteRecord)
            if status:
                query = query.filter(
                    RolloutLegacyRouteRecord.status == str(status)
                )
            total = query.count()
            rows = (
                query.order_by(
                    RolloutLegacyRouteRecord.created_at.desc(),
                    RolloutLegacyRouteRecord.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return {
                "items": [item.to_dict() for item in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
                "page_count": (total + page_size - 1) // page_size,
            }

    def deprecate(
        self,
        route_id: str,
        *,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        actor = str(actor or "").strip()
        reason = str(reason or "").strip()
        if not actor or not reason:
            raise LegacyRouteError(
                "legacy_route_input_invalid",
                "actor and deprecation reason are required",
            )
        now = _utcnow()
        with self._session_scope() as db:
            row = db.get(RolloutLegacyRouteRecord, route_id)
            if row is None:
                raise LegacyRouteError(
                    "legacy_route_missing",
                    f"legacy rollout route not found: {route_id}",
                )
            if row.status == "deprecated":
                return row.to_dict()
            row.status = "deprecated"
            row.deprecated_by = actor
            row.deprecated_at = now
            row.deprecation_reason = reason
            db.flush()
            return row.to_dict()


__all__ = [
    "LegacyRouteAdapter",
    "LegacyRouteAdapterRegistry",
    "LegacyRouteError",
    "RolloutLegacyRouteService",
    "RuntimeBundleCompatibilityAdapter",
]

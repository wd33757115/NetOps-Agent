"""Versioned entity-identity normalization used by cross-period comparison."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import yaml

from src.infrastructure.db.models import (
    EntityIdentityPolicyRecord,
    EntityIdentityPolicyVersionRecord,
)
from src.infrastructure.db.postgres import get_db_session

DEFAULT_IDENTITY_POLICY_PATH = (
    Path(__file__).parent / "assets" / "entity_identity.v1.yaml"
)
_INTERFACE_RE = re.compile(r"^([A-Za-z][A-Za-z-]*)(.*)$")


@dataclass(frozen=True, slots=True)
class EntityIdentityPolicy:
    policy_id: str
    version: int
    status: str
    digest: str
    entity_policies: tuple[dict[str, Any], ...]
    interface_prefixes: dict[str, str]


@dataclass(frozen=True, slots=True)
class EntityIdentityDecision:
    entity_type: str
    values: dict[str, Any]
    entity_key: str
    policy_id: str
    policy_version: int
    policy_digest: str
    matched_policy_index: int | None


@lru_cache(maxsize=4)
def load_entity_identity_policy(
    path: str | Path = DEFAULT_IDENTITY_POLICY_PATH,
) -> EntityIdentityPolicy:
    source = Path(path)
    content = source.read_bytes()
    payload = yaml.safe_load(content.decode("utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("entity identity policy must be an object")
    policy_id = str(payload.get("policy_id") or "").strip()
    version = int(payload.get("version") or 0)
    status = str(payload.get("status") or "").strip()
    rows = payload.get("entity_policies")
    if (
        not policy_id
        or version < 1
        or status not in {"draft", "active", "deprecated"}
        or not isinstance(rows, list)
    ):
        raise ValueError("entity identity policy metadata is invalid")
    policies: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"entity_policies[{index}] must be an object")
        patterns = [str(item) for item in raw.get("entity_type_patterns") or ()]
        if not patterns:
            raise ValueError(f"entity_policies[{index}] requires entity_type_patterns")
        for pattern in patterns:
            re.compile(pattern)
        normalizers = {
            str(field): tuple(str(item) for item in steps or ())
            for field, steps in dict(raw.get("field_normalizers") or {}).items()
        }
        unknown_steps = {
            step
            for steps in normalizers.values()
            for step in steps
            if step
            not in {
                "trim",
                "compact_whitespace",
                "casefold",
                "numeric_text",
                "interface_name",
            }
        }
        if unknown_steps:
            raise ValueError(
                f"entity_policies[{index}] uses unknown normalizers: "
                + ", ".join(sorted(unknown_steps))
            )
        policies.append(
            {
                "entity_type_patterns": tuple(patterns),
                "identity_fields": tuple(
                    str(item) for item in raw.get("identity_fields") or ()
                ),
                "field_normalizers": normalizers,
            }
        )
    return EntityIdentityPolicy(
        policy_id=policy_id,
        version=version,
        status=status,
        digest=hashlib.sha256(content).hexdigest(),
        entity_policies=tuple(policies),
        interface_prefixes={
            str(key).casefold(): str(value)
            for key, value in dict(payload.get("interface_prefixes") or {}).items()
        },
    )


def _normalize_interface(value: str, prefixes: dict[str, str]) -> str:
    compact = re.sub(r"\s+", "", value)
    match = _INTERFACE_RE.match(compact)
    if not match:
        return compact
    prefix, suffix = match.groups()
    return f"{prefixes.get(prefix.casefold(), prefix)}{suffix}"


def _apply_steps(
    value: Any,
    steps: tuple[str, ...],
    *,
    policy: EntityIdentityPolicy,
) -> Any:
    if value is None:
        return None
    result: Any = value
    for step in steps:
        text = str(result)
        if step == "trim":
            result = text.strip()
        elif step == "compact_whitespace":
            result = re.sub(r"\s+", "", text)
        elif step == "casefold":
            result = text.casefold()
        elif step == "numeric_text":
            stripped = text.strip()
            result = str(int(stripped)) if re.fullmatch(r"[+-]?\d+", stripped) else stripped
        elif step == "interface_name":
            result = _normalize_interface(text.strip(), policy.interface_prefixes)
    return result


def normalize_entity_identity(
    *,
    entity_type: str,
    identity_values: dict[str, Any],
    primary_keys: list[str] | tuple[str, ...],
    policy: EntityIdentityPolicy | None = None,
) -> EntityIdentityDecision:
    policy = policy or load_entity_identity_policy()
    matched_index: int | None = None
    normalizers: dict[str, tuple[str, ...]] = {}
    for index, item in enumerate(policy.entity_policies):
        if any(
            re.search(pattern, str(entity_type or ""), flags=re.IGNORECASE)
            for pattern in item["entity_type_patterns"]
        ):
            matched_index = index
            normalizers = dict(item["field_normalizers"])
            break
    normalized = {
        str(field): _apply_steps(
            identity_values.get(field),
            normalizers.get(str(field), ("trim",)),
            policy=policy,
        )
        for field in primary_keys
    }
    entity_key = json.dumps(
        [normalized[str(field)] for field in primary_keys],
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return EntityIdentityDecision(
        entity_type=str(entity_type),
        values=normalized,
        entity_key=entity_key,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        policy_digest=policy.digest,
        matched_policy_index=matched_index,
    )


class EntityIdentityPolicyRepository:
    """Persist the packaged policy as an immutable, auditable active version."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
    ) -> None:
        self._session_scope = session_scope

    @staticmethod
    def version_id(policy: EntityIdentityPolicy) -> str:
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"netops/entity-identity/{policy.policy_id}/v{policy.version}",
            )
        )

    @staticmethod
    def _payload(policy: EntityIdentityPolicy) -> dict[str, Any]:
        return {
            "policy_id": policy.policy_id,
            "version": policy.version,
            "entity_policies": [
                {
                    "entity_type_patterns": list(item["entity_type_patterns"]),
                    "identity_fields": list(item["identity_fields"]),
                    "field_normalizers": {
                        field: list(steps)
                        for field, steps in item["field_normalizers"].items()
                    },
                }
                for item in policy.entity_policies
            ],
            "interface_prefixes": dict(policy.interface_prefixes),
        }

    def bootstrap(
        self,
        policy: EntityIdentityPolicy | None = None,
        *,
        actor: str = "identity-policy-bootstrap",
    ) -> dict[str, Any]:
        policy = policy or load_entity_identity_policy()
        version_id = self.version_id(policy)
        now = datetime.now(timezone.utc)
        with self._session_scope() as db:
            root = db.get(EntityIdentityPolicyRecord, policy.policy_id)
            if root is None:
                root = EntityIdentityPolicyRecord(
                    id=policy.policy_id,
                    name=policy.policy_id,
                    status=policy.status,
                    current_version=policy.version if policy.status == "active" else 0,
                    created_at=now,
                    updated_at=now,
                )
                db.add(root)
            version = db.get(EntityIdentityPolicyVersionRecord, version_id)
            if version is not None and str(version.digest) != policy.digest:
                raise ValueError("entity identity policy version was changed in place")
            if version is None:
                if policy.status == "active":
                    existing = (
                        db.query(EntityIdentityPolicyVersionRecord)
                        .filter(
                            EntityIdentityPolicyVersionRecord.policy_id == policy.policy_id,
                            EntityIdentityPolicyVersionRecord.status == "active",
                        )
                        .first()
                    )
                    if existing is not None:
                        raise ValueError(
                            "entity identity policy already has another active version"
                        )
                version = EntityIdentityPolicyVersionRecord(
                    id=version_id,
                    policy_id=policy.policy_id,
                    version=policy.version,
                    policy_json=self._payload(policy),
                    digest=policy.digest,
                    status=policy.status,
                    created_by=actor,
                    created_at=now,
                    activated_at=now if policy.status == "active" else None,
                )
                db.add(version)
            root.status = policy.status
            root.current_version = policy.version if policy.status == "active" else 0
            root.updated_at = now
            db.flush()
            return version.to_dict()

    def get_active_version(self, policy_id: str) -> dict[str, Any] | None:
        with self._session_scope() as db:
            row = (
                db.query(EntityIdentityPolicyVersionRecord)
                .filter(
                    EntityIdentityPolicyVersionRecord.policy_id == policy_id,
                    EntityIdentityPolicyVersionRecord.status == "active",
                )
                .first()
            )
            return row.to_dict() if row is not None else None

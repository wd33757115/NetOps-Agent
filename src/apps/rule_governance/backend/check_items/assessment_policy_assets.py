"""Governed assessment-policy profiles, immutable versions and scope bindings."""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_

from src.core.check_items.assessment_policy import (
    assessment_policy_source_digest,
    build_target_assessment_policy_snapshot,
    canonical_digest,
    resolve_assessment_basis,
)
from src.infrastructure.db.models import (
    AssessmentPolicyBindingRecord,
    AssessmentPolicyDecisionRecord,
    AssessmentPolicyProfileRecord,
    AssessmentPolicyVersionRecord,
    CheckItemVersionRecord,
    NetworkDevice,
)
from src.infrastructure.db.postgres import get_db_session

POLICY_ROLES = frozenset(
    {
        "high",
        "critical_high",
        "low",
        "critical_low",
        "expected_state",
        "applicability",
    }
)
BINDING_TYPES = frozenset(
    {"inventory_profile", "scope_override", "catalog_default"}
)


class AssessmentPolicyConflictError(ValueError):
    """The requested lifecycle transition conflicts with frozen policy state."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | str | None) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("policy validity timestamp is invalid") from exc
    return (
        parsed.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None
        else parsed.astimezone(timezone.utc)
    )


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )
    return normalized.isoformat()


def _strings(values: Iterable[Any] | None) -> list[str]:
    return sorted(
        {
            str(value).strip()
            for value in values or ()
            if value is not None and str(value).strip()
        }
    )


def _normalized_unit(value: Any) -> str:
    normalized = "".join(str(value or "").strip().lower().split())
    return {
        "%": "percent",
        "pct": "percent",
        "percentage": "percent",
    }.get(normalized, normalized)


def _selector_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _strings([value])
    return _strings(value)


def _require_text(value: Any, *, label: str, minimum: int = 1) -> str:
    normalized = str(value or "").strip()
    if len(normalized) < minimum:
        raise ValueError(f"{label} must contain at least {minimum} characters")
    return normalized


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def _normalize_policy(policy_role: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    policy = dict(raw or {})
    if policy_role == "applicability":
        applicability = dict(policy.get("applicability") or policy)
        mode = str(applicability.get("mode") or "capability").strip().lower()
        reason_code = str(applicability.get("reason_code") or "").strip().lower()
        empty_classification = str(
            applicability.get("empty_result_classification") or "invalid"
        ).strip().lower()
        if empty_classification not in {
            "invalid",
            "expected_empty",
            "not_applicable",
        }:
            raise ValueError(
                "applicability empty_result_classification must be invalid, "
                "expected_empty or not_applicable"
            )
        legal_empty_patterns = _strings(
            applicability.get("legal_empty_patterns") or []
        )
        if len(legal_empty_patterns) > 32:
            raise ValueError(
                "applicability policy supports at most 32 legal-empty patterns"
            )
        for pattern in legal_empty_patterns:
            if len(pattern) > 512:
                raise ValueError(
                    "applicability legal-empty pattern must not exceed 512 characters"
                )
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(
                    f"applicability legal-empty pattern is invalid: {exc}"
                ) from exc
        if (
            empty_classification in {"expected_empty", "not_applicable"}
            and not legal_empty_patterns
        ):
            raise ValueError(
                "post-collection legal-empty classification requires at least one "
                "approved classifier pattern"
            )
        if mode not in {"capability", "explicit"}:
            raise ValueError(
                "applicability policy mode must be capability or explicit"
            )
        if reason_code and not re.fullmatch(r"[a-z][a-z0-9_]{2,127}", reason_code):
            raise ValueError(
                "applicability reason_code must use lowercase snake_case"
            )
        if mode == "explicit":
            decision = str(applicability.get("decision") or "").strip().lower()
            if decision not in {"applicable", "not_applicable"}:
                raise ValueError(
                    "explicit applicability policy requires applicable or "
                    "not_applicable decision"
                )
            normalized_applicability = {
                "mode": "explicit",
                "decision": decision,
                "reason_code": reason_code
                or (
                    "policy_explicit_not_applicable"
                    if decision == "not_applicable"
                    else "policy_explicit_applicable"
                ),
                "empty_result_classification": empty_classification,
            }
            if legal_empty_patterns:
                normalized_applicability["legal_empty_patterns"] = (
                    legal_empty_patterns
                )
            return {
                "schema_version": "assessment-policy/v1",
                "value_kind": "applicability",
                "applicability": normalized_applicability,
            }

        required_capabilities = _strings(
            applicability.get("required_capabilities")
            or applicability.get("capabilities")
            or []
        )
        if not required_capabilities:
            raise ValueError(
                "capability applicability policy requires at least one capability"
            )
        normalized_applicability = {
            "mode": "capability",
            "required_capabilities": required_capabilities,
            "when_present": "applicable",
            "when_absent": "not_applicable",
            "when_unknown": "unable_to_determine",
            "reason_code": reason_code or "capability_explicitly_absent",
            "empty_result_classification": empty_classification,
        }
        if legal_empty_patterns:
            normalized_applicability["legal_empty_patterns"] = legal_empty_patterns
        return {
            "schema_version": "assessment-policy/v1",
            "value_kind": "applicability",
            "applicability": normalized_applicability,
        }
    if policy_role == "expected_state":
        expected_states = _strings(policy.get("expected_states") or [])
        if not expected_states:
            raise ValueError("expected-state policy requires at least one expected state")
        return {
            "schema_version": "assessment-policy/v1",
            "value_kind": "expected_states",
            "expected_states": expected_states,
        }

    threshold = dict(policy.get("threshold") or policy)
    value = _number(threshold.get("value"))
    unit = _require_text(threshold.get("unit"), label="threshold unit")
    expected_operator = "gte" if policy_role in {"high", "critical_high"} else "lte"
    operator = str(threshold.get("operator") or expected_operator).strip().lower()
    if value is None:
        raise ValueError("threshold policy requires a numeric value")
    if operator != expected_operator:
        raise ValueError(
            f"{policy_role} threshold must use the {expected_operator} operator"
        )
    hysteresis = _number(threshold.get("hysteresis"))
    if hysteresis is not None and hysteresis < 0:
        raise ValueError("threshold hysteresis must be non-negative")
    window = str(threshold.get("window") or "").strip() or None
    return {
        "schema_version": "assessment-policy/v1",
        "value_kind": "threshold",
        "threshold": {
            "value": value,
            "unit": unit,
            "operator": operator,
            "hysteresis": hysteresis,
            "window": window,
        },
    }


def _normalize_scope(binding_type: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "device_ids",
        "sites",
        "roles",
        "entity_types",
        "entity_keys",
        "dimensions",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError("unsupported assessment-policy scope keys: " + ", ".join(unknown))
    scope = {
        key: _strings(raw.get(key) or [])
        for key in ("device_ids", "sites", "roles", "entity_types", "entity_keys")
        if _strings(raw.get(key) or [])
    }
    dimensions: dict[str, list[str]] = {}
    for key, values in dict(raw.get("dimensions") or {}).items():
        normalized = _strings(values if isinstance(values, list) else [values])
        if normalized:
            dimensions[str(key)] = normalized
    if dimensions:
        scope["dimensions"] = dict(sorted(dimensions.items()))
    if binding_type == "inventory_profile" and not scope.get("device_ids"):
        raise ValueError("inventory-profile policy binding requires device_ids")
    if binding_type == "scope_override" and not any(
        scope.get(key) for key in ("device_ids", "sites", "roles")
    ):
        raise ValueError("scope policy binding requires device_ids, sites or roles")
    if binding_type == "catalog_default" and any(
        scope.get(key)
        for key in ("device_ids", "sites", "roles", "entity_keys", "dimensions")
    ):
        raise ValueError("catalog-default policy cannot target mutable inventory scope")
    return scope


def _effective_window(
    version: AssessmentPolicyVersionRecord,
    binding: AssessmentPolicyBindingRecord,
) -> tuple[datetime | None, datetime | None]:
    starts = [value for value in (version.valid_from, binding.valid_from) if value]
    ends = [value for value in (version.valid_until, binding.valid_until) if value]
    return (max(starts) if starts else None, min(ends) if ends else None)


class AssessmentPolicyAssetService:
    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
    ) -> None:
        self._session_scope = session_scope

    def _session(self, session: Any | None):
        return nullcontext(session) if session is not None else self._session_scope()

    def create_version(
        self,
        *,
        check_item_id: str,
        check_item_version_id: str,
        observation_type: str,
        policy_role: str,
        name: str,
        policy: Mapping[str, Any],
        evidence_refs: Iterable[str],
        provenance: Mapping[str, Any] | None,
        created_by: str,
        idempotency_key: str,
        description: str | None = None,
        profile_id: str | None = None,
        valid_from: datetime | str | None = None,
        valid_until: datetime | str | None = None,
    ) -> dict[str, Any]:
        item_id = _require_text(check_item_id, label="check item")
        item_version_id = _require_text(
            check_item_version_id, label="check-item version"
        )
        observation = _require_text(observation_type, label="observation type")
        role = _require_text(policy_role, label="policy role").lower()
        if role not in POLICY_ROLES:
            raise ValueError("unsupported assessment policy role")
        display_name = _require_text(name, label="policy name")
        actor = _require_text(created_by, label="policy creator")
        idempotency = _require_text(
            idempotency_key, label="idempotency key", minimum=8
        )
        normalized_policy = _normalize_policy(role, policy)
        refs = _strings(evidence_refs)
        if not refs:
            raise ValueError("assessment policy requires at least one evidence reference")
        provenance_payload = dict(provenance or {})
        start = _as_utc(valid_from)
        end = _as_utc(valid_until)
        if start is not None and end is not None and start >= end:
            raise ValueError("policy valid_from must be earlier than valid_until")
        request = {
            "profile_id": str(profile_id or ""),
            "check_item_id": item_id,
            "check_item_version_id": item_version_id,
            "observation_type": observation,
            "policy_role": role,
            "name": display_name,
            "description": str(description or "").strip() or None,
            "policy": normalized_policy,
            "evidence_refs": refs,
            "provenance": provenance_payload,
            "valid_from": _iso(start),
            "valid_until": _iso(end),
            "created_by": actor,
        }
        request_digest = canonical_digest(request)
        now = _utcnow()
        with self._session(None) as db:
            existing = (
                db.query(AssessmentPolicyVersionRecord)
                .filter(AssessmentPolicyVersionRecord.idempotency_key == idempotency)
                .with_for_update()
                .first()
            )
            if existing is not None:
                if str(existing.request_digest) != request_digest:
                    raise AssessmentPolicyConflictError(
                        "idempotency key was used for a different policy version request"
                    )
                return self._serialize_version(db, existing)

            check_version = db.get(CheckItemVersionRecord, item_version_id)
            if check_version is None or str(check_version.check_item_id) != item_id:
                raise ValueError("check-item version does not belong to the requested check item")
            if str(check_version.status) != "active":
                raise AssessmentPolicyConflictError(
                    "assessment policy must target an active check-item version"
                )
            observation_types = {
                str(item.get("observation_type") or "")
                for item in dict(check_version.canonical_contract_json or {}).get(
                    "observations", []
                )
                if isinstance(item, Mapping)
            }
            if role == "applicability" and observation != "*":
                raise ValueError(
                    "applicability policy must use the check-item observation scope '*'"
                )
            if (
                role != "applicability"
                and observation_types
                and observation not in observation_types
            ):
                raise ValueError("observation type is not declared by the check-item contract")

            profile = None
            if profile_id:
                profile = (
                    db.query(AssessmentPolicyProfileRecord)
                    .filter(AssessmentPolicyProfileRecord.id == str(profile_id))
                    .with_for_update()
                    .first()
                )
                if profile is None:
                    raise ValueError("assessment policy profile not found")
                if (
                    str(profile.check_item_id) != item_id
                    or str(profile.observation_type) != observation
                    or str(profile.policy_role) != role
                ):
                    raise AssessmentPolicyConflictError(
                        "policy revision identity does not match the existing profile"
                    )
            else:
                profile = (
                    db.query(AssessmentPolicyProfileRecord)
                    .filter(
                        AssessmentPolicyProfileRecord.check_item_id == item_id,
                        AssessmentPolicyProfileRecord.observation_type == observation,
                        AssessmentPolicyProfileRecord.policy_role == role,
                        AssessmentPolicyProfileRecord.name == display_name,
                    )
                    .with_for_update()
                    .first()
                )
                if profile is None:
                    profile = AssessmentPolicyProfileRecord(
                        id=str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"netops/assessment-policy-profile/{idempotency}",
                            )
                        ),
                        check_item_id=item_id,
                        observation_type=observation,
                        policy_role=role,
                        name=display_name,
                        description=str(description or "").strip() or None,
                        status="draft",
                        current_version=0,
                        created_by=actor,
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(profile)
                    db.flush()

            version_number = int(profile.current_version or 0) + 1
            version_payload = {
                "schema_version": "assessment-policy-version/v1",
                "profile_id": str(profile.id),
                "version": version_number,
                "check_item_version_id": item_version_id,
                "observation_type": observation,
                "policy_role": role,
                "policy": normalized_policy,
                "evidence_refs": refs,
                "provenance": provenance_payload,
                "valid_from": _iso(start),
                "valid_until": _iso(end),
            }
            row = AssessmentPolicyVersionRecord(
                id=str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"netops/assessment-policy-version/{idempotency}",
                    )
                ),
                profile_id=profile.id,
                check_item_version_id=item_version_id,
                version=version_number,
                policy_json=normalized_policy,
                evidence_refs_json=refs,
                provenance_json=provenance_payload,
                valid_from=start,
                valid_until=end,
                digest=canonical_digest(version_payload),
                status="draft",
                idempotency_key=idempotency,
                request_digest=request_digest,
                created_by=actor,
                created_at=now,
                updated_at=now,
            )
            profile.current_version = version_number
            profile.updated_at = now
            db.add(row)
            db.flush()
            return self._serialize_version(db, row)

    def submit(
        self,
        version_id: str,
        *,
        actor: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._transition(
            version_id,
            stage="submission",
            decision="submitted",
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
        )

    def semantic_review(
        self,
        version_id: str,
        *,
        decision: str,
        actor: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._transition(
            version_id,
            stage="semantic_review",
            decision=decision,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
        )

    def publication_review(
        self,
        version_id: str,
        *,
        decision: str,
        actor: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._transition(
            version_id,
            stage="publication",
            decision=decision,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
        )

    def revoke(
        self,
        version_id: str,
        *,
        actor: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._transition(
            version_id,
            stage="revocation",
            decision="revoked",
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
        )

    def _transition(
        self,
        version_id: str,
        *,
        stage: str,
        decision: str,
        actor: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_actor = _require_text(actor, label="policy actor")
        rationale = _require_text(reason, label="policy decision reason", minimum=3)
        idempotency = _require_text(
            idempotency_key, label="idempotency key", minimum=8
        )
        normalized_decision = str(decision).strip().lower()
        if stage in {"semantic_review", "publication"} and normalized_decision not in {
            "approved",
            "rejected",
        }:
            raise ValueError("policy review decision must be approved or rejected")
        now = _utcnow()
        with self._session(None) as db:
            row = (
                db.query(AssessmentPolicyVersionRecord)
                .filter(AssessmentPolicyVersionRecord.id == str(version_id))
                .with_for_update()
                .first()
            )
            if row is None:
                raise ValueError("assessment policy version not found")
            # Serialize decisions for the same immutable version before reading
            # the idempotency ledger. A concurrent retry must observe the first
            # committed decision instead of failing on the already-advanced state.
            existing = (
                db.query(AssessmentPolicyDecisionRecord)
                .filter(AssessmentPolicyDecisionRecord.idempotency_key == idempotency)
                .with_for_update()
                .first()
            )
            request = {
                "policy_version_id": str(row.id),
                "stage": stage,
                "decision": normalized_decision,
                "actor": normalized_actor,
                "reason": rationale,
                "policy_digest": str(row.digest),
            }
            request_digest = canonical_digest(request)
            if existing is not None:
                if str(existing.request_digest) != request_digest:
                    raise AssessmentPolicyConflictError(
                        "idempotency key was used for a different policy decision"
                    )
                return self._serialize_version(db, row)

            expected_status = {
                "submission": "draft",
                "semantic_review": "pending_review",
                "publication": "pending_approval",
                "revocation": "approved",
            }[stage]
            if str(row.status) != expected_status:
                raise AssessmentPolicyConflictError(
                    f"policy version is not ready for {stage}"
                )
            decision_row = AssessmentPolicyDecisionRecord(
                id=str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"netops/assessment-policy-decision/{idempotency}",
                    )
                ),
                policy_version_id=row.id,
                stage=stage,
                decision=normalized_decision,
                actor=normalized_actor,
                reason=rationale,
                policy_digest=row.digest,
                idempotency_key=idempotency,
                request_digest=request_digest,
                created_at=now,
            )
            db.add(decision_row)
            if stage == "submission":
                row.status = "pending_review"
                row.submitted_at = now
            elif stage == "semantic_review":
                row.status = (
                    "pending_approval" if normalized_decision == "approved" else "rejected"
                )
                row.semantic_reviewed_by = normalized_actor
                row.semantic_review_reason = rationale
                row.semantic_reviewed_at = now
            elif stage == "publication":
                row.status = "approved" if normalized_decision == "approved" else "rejected"
                if normalized_decision == "approved":
                    row.approved_by = normalized_actor
                    row.approval_reason = rationale
                    row.approved_at = now
                    profile = db.get(AssessmentPolicyProfileRecord, row.profile_id)
                    if profile is not None:
                        profile.status = "active"
                        profile.updated_at = now
            else:
                row.status = "revoked"
                row.revoked_by = normalized_actor
                row.revoke_reason = rationale
                row.revoked_at = now
                for binding in (
                    db.query(AssessmentPolicyBindingRecord)
                    .filter(
                        AssessmentPolicyBindingRecord.policy_version_id == row.id,
                        AssessmentPolicyBindingRecord.status == "active",
                    )
                    .with_for_update()
                    .all()
                ):
                    binding.status = "revoked"
                    binding.ended_by = normalized_actor
                    binding.end_reason = rationale
                    binding.ended_at = now
                    binding.updated_at = now
            row.updated_at = now
            db.flush()
            return self._serialize_version(db, row)

    def bind(
        self,
        version_id: str,
        *,
        binding_type: str,
        scope: Mapping[str, Any],
        actor: str,
        reason: str,
        idempotency_key: str,
        valid_from: datetime | str | None = None,
        valid_until: datetime | str | None = None,
        replaces_binding_id: str | None = None,
    ) -> dict[str, Any]:
        kind = str(binding_type).strip().lower()
        if kind not in BINDING_TYPES:
            raise ValueError("unsupported assessment policy binding type")
        normalized_scope = _normalize_scope(kind, scope)
        normalized_actor = _require_text(actor, label="policy publisher")
        rationale = _require_text(reason, label="policy binding reason", minimum=3)
        idempotency = _require_text(
            idempotency_key, label="idempotency key", minimum=8
        )
        start = _as_utc(valid_from)
        end = _as_utc(valid_until)
        if start is not None and end is not None and start >= end:
            raise ValueError("binding valid_from must be earlier than valid_until")
        scope_digest = canonical_digest(normalized_scope)
        request = {
            "policy_version_id": str(version_id),
            "binding_type": kind,
            "scope": normalized_scope,
            "valid_from": _iso(start),
            "valid_until": _iso(end),
            "actor": normalized_actor,
            "reason": rationale,
            "replaces_binding_id": str(replaces_binding_id or ""),
        }
        request_digest = canonical_digest(request)
        now = _utcnow()
        with self._session(None) as db:
            existing = (
                db.query(AssessmentPolicyBindingRecord)
                .filter(AssessmentPolicyBindingRecord.idempotency_key == idempotency)
                .with_for_update()
                .first()
            )
            if existing is not None:
                if str(existing.request_digest) != request_digest:
                    raise AssessmentPolicyConflictError(
                        "idempotency key was used for a different policy binding request"
                    )
                return self._serialize_binding(existing)
            version = (
                db.query(AssessmentPolicyVersionRecord)
                .filter(AssessmentPolicyVersionRecord.id == str(version_id))
                .with_for_update()
                .first()
            )
            if version is None:
                raise ValueError("assessment policy version not found")
            if str(version.status) != "approved":
                raise AssessmentPolicyConflictError(
                    "only an approved assessment policy version can be bound"
                )
            profile = db.get(AssessmentPolicyProfileRecord, version.profile_id)
            if profile is None:
                raise ValueError("assessment policy profile not found")
            if kind == "inventory_profile":
                device_ids = set(normalized_scope.get("device_ids") or [])
                existing_device_ids = {
                    str(item[0])
                    for item in db.query(NetworkDevice.id)
                    .filter(NetworkDevice.id.in_(device_ids))
                    .all()
                }
                missing = sorted(device_ids - existing_device_ids)
                if missing:
                    raise ValueError(
                        "inventory policy scope contains unknown device ids: "
                        + ", ".join(missing)
                    )
            active_exact = (
                db.query(AssessmentPolicyBindingRecord)
                .filter(
                    AssessmentPolicyBindingRecord.check_item_id == profile.check_item_id,
                    AssessmentPolicyBindingRecord.observation_type
                    == profile.observation_type,
                    AssessmentPolicyBindingRecord.policy_role == profile.policy_role,
                    AssessmentPolicyBindingRecord.binding_type == kind,
                    AssessmentPolicyBindingRecord.scope_digest == scope_digest,
                    AssessmentPolicyBindingRecord.status == "active",
                )
                .with_for_update()
                .first()
            )
            if active_exact is not None:
                if not replaces_binding_id or str(active_exact.id) != str(
                    replaces_binding_id
                ):
                    raise AssessmentPolicyConflictError(
                        "an active assessment policy already owns this exact scope"
                    )
                active_exact.status = "ended"
                active_exact.ended_by = normalized_actor
                active_exact.end_reason = f"replaced by policy version {version.id}"
                active_exact.ended_at = now
                active_exact.updated_at = now
            binding_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"netops/assessment-policy-binding/{idempotency}",
                )
            )
            binding_payload = {
                "schema_version": "assessment-policy-binding/v1",
                "binding_id": binding_id,
                "profile_id": str(profile.id),
                "policy_version_id": str(version.id),
                "policy_digest": str(version.digest),
                "binding_type": kind,
                "scope": normalized_scope,
                "valid_from": _iso(start),
                "valid_until": _iso(end),
            }
            row = AssessmentPolicyBindingRecord(
                id=binding_id,
                profile_id=profile.id,
                policy_version_id=version.id,
                check_item_id=profile.check_item_id,
                observation_type=profile.observation_type,
                policy_role=profile.policy_role,
                binding_type=kind,
                scope_json=normalized_scope,
                scope_digest=scope_digest,
                binding_digest=canonical_digest(binding_payload),
                valid_from=start,
                valid_until=end,
                status="active",
                idempotency_key=idempotency,
                request_digest=request_digest,
                created_by=normalized_actor,
                reason=rationale,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.flush()
            return self._serialize_binding(row)

    def end_binding(
        self,
        binding_id: str,
        *,
        actor: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_actor = _require_text(actor, label="policy publisher")
        rationale = _require_text(reason, label="policy binding end reason", minimum=3)
        idempotency = _require_text(
            idempotency_key, label="idempotency key", minimum=8
        )
        request_digest = canonical_digest(
            {
                "binding_id": str(binding_id),
                "actor": normalized_actor,
                "reason": rationale,
            }
        )
        now = _utcnow()
        with self._session(None) as db:
            replay = (
                db.query(AssessmentPolicyBindingRecord)
                .filter(
                    AssessmentPolicyBindingRecord.end_idempotency_key == idempotency
                )
                .with_for_update()
                .first()
            )
            if replay is not None:
                if str(replay.end_request_digest) != request_digest:
                    raise AssessmentPolicyConflictError(
                        "idempotency key was used for a different binding end request"
                    )
                return self._serialize_binding(replay)
            row = (
                db.query(AssessmentPolicyBindingRecord)
                .filter(AssessmentPolicyBindingRecord.id == str(binding_id))
                .with_for_update()
                .first()
            )
            if row is None:
                raise ValueError("assessment policy binding not found")
            if row.status == "ended":
                raise AssessmentPolicyConflictError(
                    "assessment policy binding was already ended by another request"
                )
            if row.status != "active":
                raise AssessmentPolicyConflictError(
                    "only an active assessment policy binding can be ended"
                )
            row.status = "ended"
            row.ended_by = normalized_actor
            row.end_reason = rationale
            row.ended_at = now
            row.end_idempotency_key = idempotency
            row.end_request_digest = request_digest
            row.updated_at = now
            db.flush()
            return self._serialize_binding(row)

    def list_profiles(
        self,
        *,
        page: int = 1,
        page_size: int = 25,
        search: str | None = None,
        check_item_id: str | None = None,
        observation_type: str | None = None,
        policy_role: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        current_page = max(1, int(page))
        size = max(1, min(int(page_size), 100))
        with self._session(None) as db:
            query = db.query(AssessmentPolicyProfileRecord).outerjoin(
                AssessmentPolicyVersionRecord,
                (
                    AssessmentPolicyVersionRecord.profile_id
                    == AssessmentPolicyProfileRecord.id
                )
                & (
                    AssessmentPolicyVersionRecord.version
                    == AssessmentPolicyProfileRecord.current_version
                ),
            )
            if search:
                pattern = f"%{str(search).strip()}%"
                query = query.filter(
                    or_(
                        AssessmentPolicyProfileRecord.name.ilike(pattern),
                        AssessmentPolicyProfileRecord.check_item_id.ilike(pattern),
                        AssessmentPolicyProfileRecord.observation_type.ilike(pattern),
                    )
                )
            if check_item_id:
                query = query.filter(
                    AssessmentPolicyProfileRecord.check_item_id == check_item_id
                )
            if observation_type:
                query = query.filter(
                    AssessmentPolicyProfileRecord.observation_type == observation_type
                )
            if policy_role:
                query = query.filter(
                    AssessmentPolicyProfileRecord.policy_role == policy_role
                )
            if status:
                active_binding_exists = (
                    db.query(AssessmentPolicyBindingRecord.id)
                    .filter(
                        AssessmentPolicyBindingRecord.profile_id
                        == AssessmentPolicyProfileRecord.id,
                        AssessmentPolicyBindingRecord.status == "active",
                    )
                    .exists()
                )
                if status == "bound_active":
                    query = query.filter(active_binding_exists)
                elif status == "approved_unbound":
                    query = query.filter(
                        AssessmentPolicyVersionRecord.status == "approved",
                        ~active_binding_exists,
                    )
                elif status in {"profile_active", "profile_deprecated"}:
                    query = query.filter(
                        AssessmentPolicyProfileRecord.status
                        == status.removeprefix("profile_")
                    )
                else:
                    query = query.filter(
                        AssessmentPolicyVersionRecord.status == status
                    )
            total = int(query.count())
            profiles = (
                query.order_by(
                    AssessmentPolicyProfileRecord.updated_at.desc(),
                    AssessmentPolicyProfileRecord.id.desc(),
                )
                .offset((current_page - 1) * size)
                .limit(size)
                .all()
            )
            profile_ids = [str(row.id) for row in profiles]
            versions = (
                db.query(AssessmentPolicyVersionRecord)
                .join(
                    AssessmentPolicyProfileRecord,
                    AssessmentPolicyProfileRecord.id
                    == AssessmentPolicyVersionRecord.profile_id,
                )
                .filter(
                    AssessmentPolicyVersionRecord.profile_id.in_(profile_ids),
                    AssessmentPolicyVersionRecord.version
                    == AssessmentPolicyProfileRecord.current_version,
                )
                .all()
                if profile_ids
                else []
            )
            latest = {str(version.profile_id): version for version in versions}
            active_bindings = defaultdict(int)
            if profile_ids:
                for profile_ref, count in (
                    db.query(
                        AssessmentPolicyBindingRecord.profile_id,
                        func.count(AssessmentPolicyBindingRecord.id),
                    )
                    .filter(
                        AssessmentPolicyBindingRecord.profile_id.in_(profile_ids),
                        AssessmentPolicyBindingRecord.status == "active",
                    )
                    .group_by(AssessmentPolicyBindingRecord.profile_id)
                    .all()
                ):
                    active_bindings[str(profile_ref)] = int(count or 0)
            return {
                "items": [
                    self._serialize_profile_summary(
                        db,
                        row,
                        latest.get(str(row.id)),
                        active_bindings[str(row.id)],
                    )
                    for row in profiles
                ],
                "page": current_page,
                "page_size": size,
                "total": total,
                "page_count": (total + size - 1) // size,
            }

    def list_profile_versions(
        self,
        profile_id: str,
        *,
        page: int = 1,
        page_size: int = 25,
        status: str | None = None,
    ) -> dict[str, Any]:
        current_page = max(1, int(page))
        size = max(1, min(int(page_size), 100))
        with self._session(None) as db:
            profile = db.get(AssessmentPolicyProfileRecord, str(profile_id))
            if profile is None:
                raise ValueError("assessment policy profile not found")
            query = db.query(AssessmentPolicyVersionRecord).filter(
                AssessmentPolicyVersionRecord.profile_id == str(profile_id)
            )
            if status:
                query = query.filter(AssessmentPolicyVersionRecord.status == status)
            total = int(query.count())
            versions = (
                query.order_by(
                    AssessmentPolicyVersionRecord.version.desc(),
                    AssessmentPolicyVersionRecord.id.desc(),
                )
                .offset((current_page - 1) * size)
                .limit(size)
                .all()
            )
            return {
                "profile": profile.to_dict(),
                "items": [
                    self._serialize_version(db, row, compact=True)
                    for row in versions
                ],
                "page": current_page,
                "page_size": size,
                "total": total,
                "page_count": (total + size - 1) // size,
            }

    def get_version(self, version_id: str) -> dict[str, Any] | None:
        with self._session(None) as db:
            row = db.get(AssessmentPolicyVersionRecord, str(version_id))
            return self._serialize_version(db, row) if row is not None else None

    def list_rendered_sources(
        self,
        *,
        check_item_ids: Iterable[str],
        session: Any | None = None,
    ) -> dict[str, tuple[dict[str, Any], ...]]:
        ids = sorted({str(item).strip() for item in check_item_ids if str(item).strip()})
        if not ids:
            return {}
        with self._session(session) as db:
            rows = (
                db.query(
                    AssessmentPolicyBindingRecord,
                    AssessmentPolicyVersionRecord,
                    AssessmentPolicyProfileRecord,
                )
                .join(
                    AssessmentPolicyVersionRecord,
                    AssessmentPolicyVersionRecord.id
                    == AssessmentPolicyBindingRecord.policy_version_id,
                )
                .join(
                    AssessmentPolicyProfileRecord,
                    AssessmentPolicyProfileRecord.id
                    == AssessmentPolicyBindingRecord.profile_id,
                )
                .filter(
                    AssessmentPolicyBindingRecord.check_item_id.in_(ids),
                    AssessmentPolicyBindingRecord.status == "active",
                )
                .order_by(
                    AssessmentPolicyBindingRecord.check_item_id,
                    AssessmentPolicyBindingRecord.binding_type,
                    AssessmentPolicyBindingRecord.id,
                )
                .all()
            )
            version_ids = {str(version.id) for _, version, _ in rows}
            approval_by_version = {
                str(decision.policy_version_id): decision
                for decision in (
                    db.query(AssessmentPolicyDecisionRecord)
                    .filter(
                        AssessmentPolicyDecisionRecord.policy_version_id.in_(version_ids),
                        AssessmentPolicyDecisionRecord.stage == "publication",
                        AssessmentPolicyDecisionRecord.decision == "approved",
                    )
                    .all()
                    if version_ids
                    else []
                )
            }
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for binding, version, profile in rows:
                approval = approval_by_version.get(str(version.id))
                grouped[str(binding.check_item_id)].append(
                    self._render_source(
                        binding=binding,
                        version=version,
                        profile=profile,
                        approval=approval,
                    )
                )
            return {
                key: tuple(
                    sorted(
                        values,
                        key=lambda item: (
                            str(item.get("source_kind") or ""),
                            str(item.get("observation_type") or ""),
                            str(item.get("policy_id") or ""),
                            str(item.get("version") or ""),
                        ),
                    )
                )
                for key, values in grouped.items()
            }

    def preview_snapshot(
        self,
        *,
        check_item_version_id: str,
        device: Mapping[str, Any],
        policy_effective_at: datetime | str | None = None,
        include_legacy: bool = False,
    ) -> dict[str, Any]:
        with self._session(None) as db:
            check_version = db.get(CheckItemVersionRecord, str(check_item_version_id))
            if check_version is None:
                raise ValueError("check-item version not found")
            sources = self.list_rendered_sources(
                check_item_ids=[str(check_version.check_item_id)],
                session=db,
            ).get(str(check_version.check_item_id), ())
            target = dict(device)
            device_id = str(target.get("device_id") or target.get("id") or "").strip()
            inventory_device = db.get(NetworkDevice, device_id) if device_id else None
            if inventory_device is not None:
                inventory_payload = inventory_device.to_dict()
                target.setdefault("device_id", str(inventory_device.id))
                for key in (
                    "device_name",
                    "management_ip",
                    "vendor",
                    "model",
                    "family",
                    "platform",
                    "device_type",
                    "os_version",
                    "site",
                    "role",
                ):
                    if target.get(key) in (None, ""):
                        target[key] = inventory_payload.get(key)
                target_attributes = dict(inventory_device.attributes_json or {})
                target_attributes.update(
                    dict(target.get("attributes") or target.get("attributes_json") or {})
                )
                target["attributes"] = target_attributes
                target.pop("attributes_json", None)
            if not include_legacy:
                attributes = dict(
                    target.get("attributes") or target.get("attributes_json") or {}
                )
                attributes.pop("assessment_policy_bindings", None)
                attributes.pop("assessment_policies", None)
                target["attributes"] = attributes
                target.pop("attributes_json", None)
            return build_target_assessment_policy_snapshot(
                check_item_id=str(check_version.check_item_id),
                check_item_version_id=str(check_version.id),
                canonical_contract=dict(check_version.canonical_contract_json or {}),
                device=target,
                governed_sources=sources,
                applicability_requirement=dict(
                    check_version.applicability_json or {}
                ),
                policy_effective_at=policy_effective_at or _utcnow(),
                allow_legacy_sources=bool(include_legacy),
                allow_legacy_rule_fallback=False,
            )

    def preview_resolution(
        self,
        *,
        check_item_version_id: str,
        device: Mapping[str, Any],
        observation: Mapping[str, Any] | None = None,
        rule: Mapping[str, Any] | None = None,
        policy_effective_at: datetime | str | None = None,
        include_legacy: bool = False,
        runtime_bundle_version_id: str = "assessment-policy-preview",
        runtime_bundle_digest: str = "0" * 64,
    ) -> dict[str, Any]:
        snapshot = self.preview_snapshot(
            check_item_version_id=check_item_version_id,
            device=device,
            policy_effective_at=policy_effective_at,
            include_legacy=include_legacy,
        )
        if observation is None and rule is None:
            return {"snapshot": snapshot, "resolution": None}
        if observation is None or rule is None:
            raise ValueError(
                "assessment policy resolution preview requires both observation and rule"
            )
        resolved = resolve_assessment_basis(
            rule=dict(rule),
            observation=dict(observation),
            policy_snapshot=snapshot,
            runtime_bundle_version_id=str(runtime_bundle_version_id),
            runtime_bundle_digest=str(runtime_bundle_digest),
        )
        return {
            "snapshot": snapshot,
            "resolution": {
                "status": resolved.status,
                "blocker_code": resolved.blocker_code,
                "effective_condition": resolved.effective_condition,
                "should_evaluate": resolved.should_evaluate,
                "assessment_basis": resolved.basis,
            },
        }

    def preview_target_impact(
        self,
        version_id: str,
        *,
        binding_type: str,
        proposed_scope: Mapping[str, Any] | None = None,
        target_selector: Mapping[str, Any] | None = None,
        policy_effective_at: datetime | str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Project one immutable candidate onto Inventory without creating a binding."""

        binding_kind = _require_text(
            binding_type,
            label="assessment policy binding type",
        )
        if binding_kind not in BINDING_TYPES:
            raise ValueError("unsupported assessment policy binding type")
        scope = dict(proposed_scope or {})
        selector = dict(target_selector or {})
        unsupported_selector_keys = sorted(
            set(selector)
            - {
                "device_ids",
                "sites",
                "roles",
                "vendors",
                "models",
                "families",
                "status",
                "search",
            }
        )
        if unsupported_selector_keys:
            raise ValueError(
                "unsupported target selector keys: "
                + ", ".join(unsupported_selector_keys)
            )
        current_page = max(1, int(page))
        size = max(1, min(int(page_size), 100))
        effective_at = _as_utc(policy_effective_at) or _utcnow()

        with self._session(None) as db:
            version = db.get(AssessmentPolicyVersionRecord, str(version_id))
            if version is None:
                raise ValueError("assessment policy version not found")
            profile = db.get(AssessmentPolicyProfileRecord, str(version.profile_id))
            check_version = db.get(
                CheckItemVersionRecord,
                str(version.check_item_version_id),
            )
            if profile is None or check_version is None:
                raise ValueError("assessment policy dependencies are incomplete")
            version_ref = str(version.id)
            version_digest = str(version.digest)
            check_item_ref = str(profile.check_item_id)
            check_item_version_ref = str(check_version.id)
            observation_ref = str(profile.observation_type)
            policy_role_ref = str(profile.policy_role)

            query = db.query(NetworkDevice)
            statuses = _selector_values(selector.get("status"))
            if statuses:
                query = query.filter(NetworkDevice.status.in_(statuses))
            else:
                query = query.filter(NetworkDevice.status == "active")
            for key, column in (
                ("device_ids", NetworkDevice.id),
                ("sites", NetworkDevice.site),
                ("roles", NetworkDevice.role),
                ("vendors", NetworkDevice.vendor),
                ("models", NetworkDevice.model),
                ("families", NetworkDevice.family),
            ):
                values = _selector_values(selector.get(key))
                if values:
                    query = query.filter(column.in_(values))
            search = str(selector.get("search") or "").strip()
            if search:
                pattern = f"%{search}%"
                query = query.filter(
                    or_(
                        NetworkDevice.id.ilike(pattern),
                        NetworkDevice.device_name.ilike(pattern),
                        NetworkDevice.management_ip.ilike(pattern),
                        NetworkDevice.vendor.ilike(pattern),
                        NetworkDevice.model.ilike(pattern),
                    )
                )
            devices = query.order_by(
                NetworkDevice.device_name,
                NetworkDevice.management_ip,
                NetworkDevice.id,
            ).all()
            if len(devices) > 20_000:
                raise ValueError(
                    "assessment policy impact preview exceeds 20000 targets; "
                    "narrow the target selector"
                )

            active_sources = list(
                self.list_rendered_sources(
                    check_item_ids=[str(profile.check_item_id)],
                    session=db,
                ).get(str(profile.check_item_id), ())
            )
            active_sources = [
                source
                for source in active_sources
                if str(source.get("policy_id") or "") != str(profile.id)
            ]
            candidate = self._render_preview_source(
                profile=profile,
                version=version,
                binding_type=binding_kind,
                scope=scope,
                effective_at=effective_at,
            )
            rows: list[dict[str, Any]] = []
            for device_row in devices:
                device = device_row.to_dict()
                device["device_id"] = str(device_row.id)
                device["attributes"] = dict(device_row.attributes_json or {})
                device.pop("attributes_json", None)
                snapshot = build_target_assessment_policy_snapshot(
                    check_item_id=str(profile.check_item_id),
                    check_item_version_id=str(check_version.id),
                    canonical_contract=dict(
                        check_version.canonical_contract_json or {}
                    ),
                    device=device,
                    governed_sources=[*active_sources, candidate],
                    applicability_requirement=dict(
                        check_version.applicability_json or {}
                    ),
                    policy_effective_at=effective_at,
                    allow_legacy_sources=False,
                    allow_legacy_rule_fallback=False,
                )
                rows.append(
                    self._target_impact_row(
                        profile=profile,
                        version=version,
                        device=device,
                        snapshot=snapshot,
                        scope=scope,
                    )
                )

        counts: dict[str, int] = defaultdict(int)
        for row in rows:
            counts[str(row["impact_status"])] += 1
        start = (current_page - 1) * size
        payload = {
            "schema_version": "assessment-policy-impact-preview/v1",
            "policy_version_id": version_ref,
            "policy_version_digest": version_digest,
            "check_item_id": check_item_ref,
            "check_item_version_id": check_item_version_ref,
            "observation_type": observation_ref,
            "policy_role": policy_role_ref,
            "binding_type": binding_kind,
            "proposed_scope": scope,
            "target_selector": selector,
            "policy_effective_at": _iso(effective_at),
            "summary": {
                "target_count": len(rows),
                "counts": dict(sorted(counts.items())),
                "blocking_count": sum(
                    count
                    for status, count in counts.items()
                    if status
                    in {
                        "conflict",
                        "expired",
                        "invalid",
                        "unit_incompatible",
                        "unknown",
                    }
                ),
            },
            "items": rows[start : start + size],
            "page": current_page,
            "page_size": size,
            "total": len(rows),
            "page_count": (len(rows) + size - 1) // size,
        }
        payload["digest"] = canonical_digest(payload)
        return payload

    @staticmethod
    def _render_preview_source(
        *,
        profile: AssessmentPolicyProfileRecord,
        version: AssessmentPolicyVersionRecord,
        binding_type: str,
        scope: Mapping[str, Any],
        effective_at: datetime,
    ) -> dict[str, Any]:
        policy = dict(version.policy_json or {})
        binding_digest = canonical_digest(
            {
                "preview_only": True,
                "policy_version_id": str(version.id),
                "binding_type": binding_type,
                "scope": dict(scope),
                "policy_effective_at": _iso(effective_at),
            }
        )
        source = {
            "source_kind": binding_type,
            "policy_id": str(profile.id),
            "version": str(version.version),
            "policy_version_id": str(version.id),
            "policy_version_digest": str(version.digest),
            "binding_id": f"preview:{binding_digest[:24]}",
            "binding_digest": binding_digest,
            "approval_digest": f"preview:{version.digest}",
            "check_item_id": str(profile.check_item_id),
            "observation_type": str(profile.observation_type),
            "policy_role": str(profile.policy_role),
            "scope": dict(scope),
            "status": "approved",
            "approved_by": "assessment-policy-impact-preview",
            "approved_at": _iso(effective_at),
            "valid_from": _iso(version.valid_from),
            "valid_until": _iso(version.valid_until),
            "evidence_refs": _strings(
                [
                    *(version.evidence_refs_json or []),
                    f"AssessmentPolicyVersion:{version.id}",
                    "assessment-policy-impact-preview",
                ]
            ),
            "preview_only": True,
        }
        if str(profile.policy_role) == "applicability":
            source["applicability"] = dict(policy.get("applicability") or {})
        elif str(profile.policy_role) == "expected_state":
            source["expected_states"] = list(policy.get("expected_states") or [])
        else:
            source["thresholds"] = {
                str(profile.policy_role): dict(policy.get("threshold") or {})
            }
        source["digest"] = assessment_policy_source_digest(source)
        return source

    @staticmethod
    def _target_impact_row(
        *,
        profile: AssessmentPolicyProfileRecord,
        version: AssessmentPolicyVersionRecord,
        device: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        scope: Mapping[str, Any],
    ) -> dict[str, Any]:
        candidate_id = str(version.id)
        invalid = next(
            (
                dict(item)
                for item in snapshot.get("invalid_sources") or []
                if str(item.get("policy_version_id") or "") == candidate_id
            ),
            None,
        )
        candidate_present = any(
            str(item.get("policy_version_id") or "") == candidate_id
            for item in snapshot.get("sources") or []
        )
        reason_codes: list[str] = []
        selected_policy: dict[str, Any] | None = None
        if invalid is not None:
            reason = str(invalid.get("reason_code") or "policy_source_invalid")
            status = "expired" if reason == "policy_expired" else "invalid"
            reason_codes = [reason]
        elif not candidate_present:
            status = "no_match"
            reason_codes = ["candidate_scope_not_matched"]
        elif str(profile.policy_role) == "applicability":
            decision = dict(snapshot.get("applicability_decision") or {})
            status = str(decision.get("status") or "unknown")
            reason_codes = _strings(decision.get("reason_codes"))
            selected_policy = dict(decision.get("selected_policy") or {}) or None
        else:
            entity_scoped = any(
                scope.get(key)
                for key in ("entity_types", "entity_keys", "dimensions")
            )
            policy = dict(version.policy_json or {})
            threshold = dict(policy.get("threshold") or {})
            expected_unit = _normalized_unit(
                dict(
                    dict(snapshot.get("contract") or {})
                    .get("thresholds", {})
                    .get(str(profile.observation_type), {})
                ).get("unit")
            )
            candidate_unit = _normalized_unit(threshold.get("unit"))
            if expected_unit and candidate_unit and expected_unit != candidate_unit:
                status = "unit_incompatible"
                reason_codes = ["assessment_policy_unit_mismatch"]
            elif entity_scoped:
                status = "entity_replay_required"
                reason_codes = ["entity_scope_requires_structured_replay"]
            else:
                expected_states = _strings(policy.get("expected_states"))
                role = str(profile.policy_role)
                resolution = resolve_assessment_basis(
                    rule={
                        "rule_id": "assessment-policy-impact-preview",
                        "version_id": candidate_id,
                        "digest": str(version.digest),
                        "condition": (
                            {"kind": "state"}
                            if role == "expected_state"
                            else {
                                "kind": (
                                    "threshold"
                                    if role in {"high", "critical_high"}
                                    else "threshold_lte"
                                ),
                                "threshold_role": role,
                            }
                        ),
                    },
                    observation={
                        "id": f"impact-preview:{device.get('device_id')}",
                        "device_id": device.get("device_id"),
                        "entity_type": "device",
                        "entity_key": device.get("device_id"),
                        "observation_type": str(profile.observation_type),
                        "value": (
                            expected_states[0]
                            if expected_states
                            else threshold.get("value", 0)
                        ),
                        "unit": threshold.get("unit"),
                    },
                    policy_snapshot=snapshot,
                    runtime_bundle_version_id="assessment-policy-impact-preview",
                    runtime_bundle_digest="0" * 64,
                )
                status = str(resolution.status)
                reason_codes = _strings([resolution.blocker_code])
                if resolution.blocker_code == "assessment_policy_unit_mismatch":
                    status = "unit_incompatible"
                elif resolution.status == "conflict":
                    status = "conflict"
                selected_policy = dict(
                    resolution.basis.get("threshold_binding")
                    or resolution.basis.get("expected_state_binding")
                    or {}
                ) or None
        return {
            "device_id": str(device.get("device_id") or ""),
            "device_name": str(device.get("device_name") or ""),
            "management_ip": str(device.get("management_ip") or ""),
            "vendor": str(device.get("vendor") or ""),
            "model": str(device.get("model") or ""),
            "site": str(device.get("site") or ""),
            "role": str(device.get("role") or ""),
            "impact_status": status,
            "reason_codes": reason_codes,
            "candidate_matched": candidate_present,
            "selected_policy": selected_policy,
            "snapshot_digest": str(snapshot.get("digest") or ""),
        }

    def _serialize_profile_summary(
        self,
        db: Any,
        row: AssessmentPolicyProfileRecord,
        latest: AssessmentPolicyVersionRecord | None,
        active_binding_count: int,
    ) -> dict[str, Any]:
        latest_payload = (
            self._serialize_version(db, latest, compact=True)
            if latest is not None
            else None
        )
        latest_status = str(latest.status) if latest is not None else "empty"
        governance_status = (
            latest_status
            if latest_status != "approved"
            else "bound_active"
            if active_binding_count > 0
            else "approved_unbound"
        )
        return {
            **row.to_dict(),
            "latest_version": latest_payload,
            "latest_version_status": latest_status,
            "governance_status": governance_status,
            "production_status": (
                "bound_active" if active_binding_count > 0 else "not_bound"
            ),
            "active_binding_count": active_binding_count,
        }

    @staticmethod
    def _render_source(
        *,
        binding: AssessmentPolicyBindingRecord,
        version: AssessmentPolicyVersionRecord,
        profile: AssessmentPolicyProfileRecord,
        approval: AssessmentPolicyDecisionRecord | None,
    ) -> dict[str, Any]:
        start, end = _effective_window(version, binding)
        policy = dict(version.policy_json or {})
        version_payload = {
            "schema_version": "assessment-policy-version/v1",
            "profile_id": str(profile.id),
            "version": int(version.version),
            "check_item_version_id": str(version.check_item_version_id),
            "observation_type": str(profile.observation_type),
            "policy_role": str(profile.policy_role),
            "policy": policy,
            "evidence_refs": _strings(version.evidence_refs_json or []),
            "provenance": dict(version.provenance_json or {}),
            "valid_from": _iso(version.valid_from),
            "valid_until": _iso(version.valid_until),
        }
        integrity_errors: list[str] = []
        if canonical_digest(version_payload) != str(version.digest):
            integrity_errors.append("policy_version_digest_mismatch")
        if (
            str(binding.profile_id) != str(profile.id)
            or str(binding.policy_version_id) != str(version.id)
            or str(binding.check_item_id) != str(profile.check_item_id)
            or str(binding.observation_type) != str(profile.observation_type)
            or str(binding.policy_role) != str(profile.policy_role)
        ):
            integrity_errors.append("policy_binding_identity_mismatch")
        scope = dict(binding.scope_json or {})
        if canonical_digest(scope) != str(binding.scope_digest):
            integrity_errors.append("policy_binding_scope_digest_mismatch")
        binding_payload = {
            "schema_version": "assessment-policy-binding/v1",
            "binding_id": str(binding.id),
            "profile_id": str(profile.id),
            "policy_version_id": str(version.id),
            "policy_digest": str(version.digest),
            "binding_type": str(binding.binding_type),
            "scope": scope,
            "valid_from": _iso(binding.valid_from),
            "valid_until": _iso(binding.valid_until),
        }
        if canonical_digest(binding_payload) != str(binding.binding_digest):
            integrity_errors.append("policy_binding_digest_mismatch")
        if approval is None:
            integrity_errors.append("policy_publication_approval_missing")
        else:
            approval_request = {
                "policy_version_id": str(version.id),
                "stage": str(approval.stage),
                "decision": str(approval.decision),
                "actor": str(approval.actor),
                "reason": str(approval.reason),
                "policy_digest": str(approval.policy_digest),
            }
            if canonical_digest(approval_request) != str(approval.request_digest):
                integrity_errors.append("policy_approval_digest_mismatch")
            if (
                str(approval.stage) != "publication"
                or str(approval.decision) != "approved"
                or str(approval.policy_digest) != str(version.digest)
                or str(approval.actor) != str(version.approved_by)
                or str(approval.reason) != str(version.approval_reason)
            ):
                integrity_errors.append("policy_approval_identity_mismatch")
        source = {
            "source_kind": str(binding.binding_type),
            "policy_id": str(profile.id),
            "version": str(version.version),
            "policy_version_id": str(version.id),
            "policy_version_digest": str(version.digest),
            "binding_id": str(binding.id),
            "binding_digest": str(binding.binding_digest),
            "approval_digest": (
                str(approval.request_digest) if approval is not None else None
            ),
            "check_item_id": str(profile.check_item_id),
            "observation_type": str(profile.observation_type),
            "policy_role": str(profile.policy_role),
            "scope": scope,
            "status": str(version.status),
            "approved_by": version.approved_by,
            "approved_at": _iso(version.approved_at),
            "valid_from": _iso(start),
            "valid_until": _iso(end),
            "evidence_refs": _strings(
                [
                    *(version.evidence_refs_json or []),
                    f"AssessmentPolicyVersion:{version.id}",
                    f"AssessmentPolicyBinding:{binding.id}",
                    *(
                        [f"AssessmentPolicyDecision:{approval.id}"]
                        if approval is not None
                        else []
                    ),
                ]
            ),
        }
        if integrity_errors:
            source["integrity_errors"] = sorted(set(integrity_errors))
        if str(profile.policy_role) == "applicability":
            source["applicability"] = dict(policy.get("applicability") or {})
        elif str(profile.policy_role) == "expected_state":
            source["expected_states"] = list(policy.get("expected_states") or [])
        else:
            source["thresholds"] = {
                str(profile.policy_role): dict(policy.get("threshold") or {})
            }
        source["digest"] = assessment_policy_source_digest(source)
        return source

    @staticmethod
    def _serialize_binding(row: AssessmentPolicyBindingRecord) -> dict[str, Any]:
        return {
            **row.to_dict(),
            "valid_from": _iso(row.valid_from),
            "valid_until": _iso(row.valid_until),
            "ended_at": _iso(row.ended_at),
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }

    def _serialize_version(
        self,
        db: Any,
        row: AssessmentPolicyVersionRecord,
        *,
        compact: bool = False,
    ) -> dict[str, Any]:
        profile = db.get(AssessmentPolicyProfileRecord, row.profile_id)
        payload = {
            **row.to_dict(),
            "valid_from": _iso(row.valid_from),
            "valid_until": _iso(row.valid_until),
            "submitted_at": _iso(row.submitted_at),
            "semantic_reviewed_at": _iso(row.semantic_reviewed_at),
            "approved_at": _iso(row.approved_at),
            "revoked_at": _iso(row.revoked_at),
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
            "profile": profile.to_dict() if profile is not None else None,
        }
        if compact:
            for key in ("policy_json", "evidence_refs_json", "provenance_json"):
                payload.pop(key, None)
            return payload
        decisions = (
            db.query(AssessmentPolicyDecisionRecord)
            .filter(AssessmentPolicyDecisionRecord.policy_version_id == row.id)
            .order_by(AssessmentPolicyDecisionRecord.created_at, AssessmentPolicyDecisionRecord.id)
            .all()
        )
        bindings = (
            db.query(AssessmentPolicyBindingRecord)
            .filter(AssessmentPolicyBindingRecord.policy_version_id == row.id)
            .order_by(AssessmentPolicyBindingRecord.created_at, AssessmentPolicyBindingRecord.id)
            .all()
        )
        payload["decisions"] = [
            {**item.to_dict(), "created_at": _iso(item.created_at)} for item in decisions
        ]
        payload["bindings"] = [self._serialize_binding(item) for item in bindings]
        payload["capabilities"] = {
            "can_submit": row.status == "draft",
            "can_semantic_review": row.status == "pending_review",
            "can_publication_review": row.status == "pending_approval",
            "can_bind": row.status == "approved",
            "can_revoke": row.status == "approved",
        }
        return payload


__all__ = [
    "AssessmentPolicyAssetService",
    "AssessmentPolicyConflictError",
    "BINDING_TYPES",
    "POLICY_ROLES",
]

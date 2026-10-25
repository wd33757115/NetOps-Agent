"""Deterministic leadership issue projection over immutable risk episodes."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

_SEVERITY_RANK = {
    "unknown": -1,
    "info": 0,
    "warning": 1,
    "minor": 2,
    "major": 3,
    "critical": 4,
}
_ACTIVE_RISK_STATUSES = {"open", "acknowledged", "recovering"}


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def risk_issue_id(risk: Mapping[str, Any]) -> str:
    episode_id = str(risk.get("episode_id") or "").strip()
    risk_case_id = str(risk.get("id") or "").strip()
    identity = episode_id or f"legacy-case:{risk_case_id}"
    return f"risk-issue:v1:{_digest(identity)}"


def _association_error(
    association: Mapping[str, Any],
    reason_code: str,
) -> dict[str, Any]:
    return {
        "association_id": str(association.get("id") or ""),
        "reason_code": reason_code,
        "primary_risk_case_id": str(association.get("primary_risk_case_id") or ""),
        "related_risk_case_id": str(association.get("related_risk_case_id") or ""),
    }


def _entity_identity(risk: Mapping[str, Any]) -> str:
    return _canonical(
        {
            "device_id": str(risk.get("device_id") or ""),
            "entity_type": str(risk.get("entity_type") or ""),
            "entity_key": str(risk.get("entity_key") or ""),
            "dimensions": dict(
                risk.get("correlation_dimensions_json") or risk.get("dimensions") or {}
            ),
        }
    )


def _member_summary(risk: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "risk_case_id": str(risk.get("id") or ""),
        "risk_episode_id": str(risk.get("episode_id") or ""),
        "claim_id": risk.get("claim_id"),
        "check_item_id": str(risk.get("check_item_id") or ""),
        "device_id": str(risk.get("device_id") or ""),
        "device_name": risk.get("device_name"),
        "entity_type": str(risk.get("entity_type") or ""),
        "entity_key": str(risk.get("entity_key") or ""),
        "risk_type": str(risk.get("risk_type") or ""),
        "severity": str(risk.get("severity") or "unknown"),
        "status": str(risk.get("status") or ""),
        "source_event_ids": sorted(
            {str(item) for item in risk.get("source_event_ids_json") or [] if str(item)}
        ),
    }


def build_risk_issue_projection(
    *,
    risks: Iterable[Mapping[str, Any]],
    associations: Iterable[Mapping[str, Any]],
    event_occurrences: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Group only exact, approved episode links; preserve all technical facts."""

    risk_rows = [dict(item) for item in risks if str(item.get("id") or "")]
    risk_by_id = {str(item["id"]): item for item in risk_rows}
    association_rows = [dict(item) for item in associations]
    ignored: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    for association in association_rows:
        if str(association.get("status") or "") != "approved":
            continue
        primary_id = str(association.get("primary_risk_case_id") or "")
        related_id = str(association.get("related_risk_case_id") or "")
        primary = risk_by_id.get(primary_id)
        related = risk_by_id.get(related_id)
        if primary is None or related is None:
            ignored.append(_association_error(association, "risk_not_in_projection"))
            continue
        if primary_id == related_id:
            ignored.append(_association_error(association, "same_risk_case"))
            continue
        if not association.get("reviewed_by") or not association.get("reviewed_at"):
            ignored.append(_association_error(association, "approval_evidence_missing"))
            continue
        primary_episode = str(primary.get("episode_id") or "")
        related_episode = str(related.get("episode_id") or "")
        if not primary_episode or not related_episode:
            ignored.append(_association_error(association, "episode_identity_missing"))
            continue
        if primary_episode != str(
            association.get("primary_risk_episode_id") or ""
        ) or related_episode != str(association.get("related_risk_episode_id") or ""):
            ignored.append(_association_error(association, "episode_stale"))
            continue
        if (
            str(primary.get("status") or "") not in _ACTIVE_RISK_STATUSES
            or str(related.get("status") or "") not in _ACTIVE_RISK_STATUSES
        ):
            ignored.append(_association_error(association, "risk_not_active"))
            continue
        if str(association.get("source_type") or "manual") == "approved_policy":
            policy = dict(association.get("policy_ref_json") or {})
            required = {
                "policy_id",
                "policy_version",
                "policy_digest",
                "approval_digest",
            }
            if not required.issubset({key for key, value in policy.items() if value}):
                ignored.append(_association_error(association, "policy_approval_missing"))
                continue
        candidates.append(association)

    child_counts = Counter(str(item.get("related_risk_episode_id") or "") for item in candidates)
    parent_episodes = {str(item.get("primary_risk_episode_id") or "") for item in candidates}
    child_episodes = {str(item.get("related_risk_episode_id") or "") for item in candidates}
    accepted: list[dict[str, Any]] = []
    for association in candidates:
        child_episode = str(association.get("related_risk_episode_id") or "")
        parent_episode = str(association.get("primary_risk_episode_id") or "")
        if child_counts[child_episode] > 1:
            ignored.append(_association_error(association, "ambiguous_parent"))
        elif parent_episode in child_episodes or child_episode in parent_episodes:
            ignored.append(_association_error(association, "non_star_topology"))
        else:
            accepted.append(association)

    children_by_primary: dict[str, list[dict[str, Any]]] = {}
    child_ids: set[str] = set()
    for association in accepted:
        primary_id = str(association.get("primary_risk_case_id") or "")
        related_id = str(association.get("related_risk_case_id") or "")
        children_by_primary.setdefault(primary_id, []).append(association)
        child_ids.add(related_id)

    event_rows = [dict(item) for item in event_occurrences]
    issues: list[dict[str, Any]] = []
    for primary in risk_rows:
        primary_id = str(primary["id"])
        if primary_id in child_ids:
            continue
        links = sorted(
            children_by_primary.get(primary_id, []),
            key=lambda item: str(item.get("related_risk_case_id") or ""),
        )
        members = [primary] + [risk_by_id[str(item.get("related_risk_case_id"))] for item in links]
        member_event_ids = {
            str(event_id)
            for member in members
            for event_id in member.get("source_event_ids_json") or []
            if str(event_id)
        }
        linked_event_occurrences = [
            item
            for item in event_rows
            if str(item.get("aggregate_event_id") or item.get("event_id") or "") in member_event_ids
        ]
        severities = [str(item.get("severity") or "unknown").lower() for item in members]
        severity = max(
            severities,
            key=lambda item: _SEVERITY_RANK.get(item, -1),
        )
        devices = sorted(
            {str(item.get("device_id") or "") for item in members if item.get("device_id")}
        )
        entities = sorted({_entity_identity(item) for item in members})
        check_items = sorted(
            {str(item.get("check_item_id") or "") for item in members if item.get("check_item_id")}
        )
        issue_id = risk_issue_id(primary)
        issues.append(
            {
                "issue_id": issue_id,
                "claim_id": primary.get("claim_id"),
                "primary_risk_case_id": primary_id,
                "primary_risk_episode_id": str(primary.get("episode_id") or ""),
                "risk_type": str(primary.get("risk_type") or ""),
                "severity": severity,
                "status": str(primary.get("status") or ""),
                "correlation_applied": bool(links),
                "association_ids": [str(item.get("id") or "") for item in links],
                "relation_types": sorted({str(item.get("relation_type") or "") for item in links}),
                "risk_case_count": len(members),
                "technical_risk_count": len(members),
                "associated_symptom_count": len(links),
                "symptom_count": len(members),
                "affected_device_count": len(devices),
                "affected_entity_count": len(entities),
                "event_occurrence_count": len(linked_event_occurrences),
                "device_ids": devices,
                "check_item_ids": check_items,
                "members": [_member_summary(item) for item in members],
                "association_evidence_refs": sorted(
                    {
                        str(ref)
                        for link in links
                        for ref in link.get("evidence_refs_json") or []
                        if str(ref)
                    }
                ),
            }
        )

    issues.sort(
        key=lambda item: (
            -_SEVERITY_RANK.get(str(item.get("severity") or "unknown"), -1),
            str(item.get("issue_id") or ""),
        )
    )
    all_devices = {str(item.get("device_id") or "") for item in risk_rows if item.get("device_id")}
    all_entities = {_entity_identity(item) for item in risk_rows}
    return {
        "schema_version": "risk-issue-projection/v1",
        "problem_count": len(issues),
        "risk_episode_count": len(risk_rows),
        "technical_risk_count": len(risk_rows),
        "associated_symptom_count": len(accepted),
        "symptom_count": len(risk_rows),
        "affected_device_count": len(all_devices),
        "affected_entity_count": len(all_entities),
        "event_occurrence_count": len(event_rows),
        "approved_association_count": len(accepted),
        "pending_review_count": sum(
            str(item.get("status") or "") == "pending_review" for item in association_rows
        ),
        "ignored_association_count": len(ignored),
        "issues": issues,
        "applied_association_ids": sorted(str(item.get("id") or "") for item in accepted),
        "ignored_associations": sorted(
            ignored,
            key=lambda item: (
                str(item.get("association_id") or ""),
                str(item.get("reason_code") or ""),
            ),
        ),
    }


__all__ = ["build_risk_issue_projection", "risk_issue_id"]

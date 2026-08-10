"""Immutable patrol scope contracts and per-target scope decisions."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


SCOPE_INCLUDED = "included"
SCOPE_APPROVED_EXCLUSION = "approved_exclusion"
SCOPE_NOT_APPLICABLE = "not_applicable"
SCOPE_UNRESOLVED = "unresolved"

_VALID_DECISIONS = {
    SCOPE_INCLUDED,
    SCOPE_APPROVED_EXCLUSION,
    SCOPE_NOT_APPLICABLE,
    SCOPE_UNRESOLVED,
}


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _device_id(device: Mapping[str, Any]) -> str:
    return str(device.get("device_id") or device.get("id") or "").strip()


def _device_role(device: Mapping[str, Any]) -> str:
    attributes = device.get("attributes")
    attrs = dict(attributes) if isinstance(attributes, Mapping) else {}
    return str(
        device.get("device_role")
        or device.get("role")
        or attrs.get("device_role")
        or attrs.get("role")
        or ""
    ).strip().casefold()


def _pair_key(device_id: str, check_item_id: str) -> str:
    return f"{device_id}\u001f{check_item_id}"


def _normalized_pair_overrides(
    values: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    return {
        str(key): dict(value)
        for key, value in (values or {}).items()
        if str(key).strip() and isinstance(value, Mapping)
    }


@dataclass(frozen=True, slots=True)
class ScopeResolution:
    contract: dict[str, Any]
    contract_digest: str
    intended_target_matrix: dict[str, Any]
    intended_target_pairs: tuple[dict[str, Any], ...]
    intended_target_matrix_digest: str
    decisions: tuple[dict[str, Any], ...]
    scope_decision_digest: str
    selected_target_pairs: tuple[dict[str, Any], ...]
    scope_reconciled: bool
    claim_boundary: str
    unmatched_device_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ScopeResolution":
        contract = dict(value.get("scope_contract") or {})
        matrix = dict(value.get("intended_target_matrix") or {})
        decisions = tuple(
            dict(item) for item in value.get("scope_decisions") or []
        )
        unmatched = tuple(
            sorted(
                str(item)
                for item in value.get("unmatched_device_ids") or []
                if str(item)
            )
        )
        contract_digest = str(value.get("scope_contract_digest") or "")
        matrix_digest = str(
            value.get("intended_target_matrix_digest") or ""
        )
        decision_digest = str(value.get("scope_decision_digest") or "")
        if not contract or _digest(contract) != contract_digest:
            raise ValueError("scope contract digest mismatch")
        if not matrix or _digest(matrix) != matrix_digest:
            raise ValueError("intended target matrix digest mismatch")
        expected_decision_digest = _digest(
            {
                "schema_version": "scope-decisions/v1",
                "scope_contract_digest": contract_digest,
                "intended_target_matrix_digest": matrix_digest,
                "decisions": list(decisions),
                "unmatched_device_ids": list(unmatched),
            }
        )
        if expected_decision_digest != decision_digest:
            raise ValueError("scope decision digest mismatch")
        return cls(
            contract=contract,
            contract_digest=contract_digest,
            intended_target_matrix=matrix,
            intended_target_pairs=tuple(
                dict(item)
                for item in value.get("intended_target_pairs") or []
            ),
            intended_target_matrix_digest=matrix_digest,
            decisions=decisions,
            scope_decision_digest=decision_digest,
            selected_target_pairs=tuple(
                dict(item)
                for item in value.get("selected_target_pairs") or []
            ),
            scope_reconciled=bool(value.get("scope_reconciled")),
            claim_boundary=str(
                value.get("claim_boundary") or "selected_scope_only"
            ),
            unmatched_device_ids=unmatched,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_contract": dict(self.contract),
            "scope_contract_version_id": self.contract[
                "scope_contract_version_id"
            ],
            "scope_contract_digest": self.contract_digest,
            "intended_target_matrix": dict(
                self.intended_target_matrix
            ),
            "intended_target_pairs": [
                dict(item) for item in self.intended_target_pairs
            ],
            "intended_target_matrix_digest": (
                self.intended_target_matrix_digest
            ),
            "scope_decisions": [dict(item) for item in self.decisions],
            "scope_decision_digest": self.scope_decision_digest,
            "selected_target_pairs": [
                dict(item) for item in self.selected_target_pairs
            ],
            "scope_reconciled": self.scope_reconciled,
            "claim_boundary": self.claim_boundary,
            "unmatched_device_ids": list(self.unmatched_device_ids),
        }


def build_ad_hoc_scope_contract(
    *,
    selected_check_item_ids_by_device: Mapping[str, Sequence[str]],
    report_purpose: str | None = None,
) -> dict[str, Any]:
    """Freeze legacy/profile selections without granting whole-network claims."""

    selected = {
        str(device_id): sorted(
            {
                str(item).strip()
                for item in check_item_ids
                if str(item).strip()
            }
        )
        for device_id, check_item_ids in selected_check_item_ids_by_device.items()
        if str(device_id).strip()
    }
    selected_digest = _digest(selected)
    return {
        "schema_version": "patrol-scope-contract/v1",
        "scope_contract_id": (
            f"ad-hoc-selected-scope:{selected_digest[:24]}"
        ),
        "version": 1,
        "report_purpose": str(report_purpose or "").strip() or None,
        "inventory_selector": {},
        "device_role_rules": [],
        "explicit_required_by_device": selected,
        "allowed_exclusion_codes": [],
        "claim_boundary": "selected_scope_only",
        "origin": "legacy_profile_selection",
    }


def resolve_patrol_scope(
    *,
    contract: Mapping[str, Any] | None,
    devices: Sequence[Mapping[str, Any]],
    selected_check_item_ids_by_device: Mapping[str, Sequence[str]],
    check_item_version_ids: Mapping[str, str],
    inventory_snapshot_digest: str | None = None,
    exclusions: Mapping[str, Mapping[str, Any]] | None = None,
    not_applicable: Mapping[str, Mapping[str, Any]] | None = None,
) -> ScopeResolution:
    """Resolve intended scope independently from the executable plan."""

    selected = {
        str(device_id): tuple(
            sorted(
                {
                    str(item).strip()
                    for item in values
                    if str(item).strip()
                }
            )
        )
        for device_id, values in selected_check_item_ids_by_device.items()
        if str(device_id).strip()
    }
    raw_contract = dict(contract or {})
    if not raw_contract:
        raw_contract = build_ad_hoc_scope_contract(
            selected_check_item_ids_by_device=selected,
        )
    normalized_contract = {
        "schema_version": str(
            raw_contract.get("schema_version")
            or "patrol-scope-contract/v1"
        ),
        "scope_contract_id": str(
            raw_contract.get("scope_contract_id") or ""
        ).strip(),
        "scope_contract_version_id": (
            str(raw_contract.get("scope_contract_version_id") or "").strip()
            or None
        ),
        "version": int(raw_contract.get("version") or 1),
        "report_purpose": (
            str(raw_contract.get("report_purpose") or "").strip() or None
        ),
        "inventory_selector": dict(
            raw_contract.get("inventory_selector") or {}
        ),
        "device_role_rules": [
            dict(item)
            for item in raw_contract.get("device_role_rules") or []
            if isinstance(item, Mapping)
        ],
        "explicit_required_by_device": {
            str(device_id): sorted(
                {
                    str(item).strip()
                    for item in values or []
                    if str(item).strip()
                }
            )
            for device_id, values in dict(
                raw_contract.get("explicit_required_by_device") or {}
            ).items()
        },
        "allowed_exclusion_codes": sorted(
            {
                str(item).strip()
                for item in raw_contract.get("allowed_exclusion_codes") or []
                if str(item).strip()
            }
        ),
        "claim_boundary": str(
            raw_contract.get("claim_boundary") or "selected_scope_only"
        ).strip(),
        "origin": str(raw_contract.get("origin") or "explicit").strip(),
    }
    if not normalized_contract["scope_contract_id"]:
        raise ValueError("scope_contract_id is required")
    if not normalized_contract["scope_contract_version_id"]:
        identity_payload = {
            **normalized_contract,
            "scope_contract_version_id": None,
        }
        normalized_contract["scope_contract_version_id"] = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "netops/patrol-scope-contract-version/"
                f"{_digest(identity_payload)}",
            )
        )
    contract_digest = _digest(normalized_contract)
    exclusion_by_pair = _normalized_pair_overrides(exclusions)
    not_applicable_by_pair = _normalized_pair_overrides(not_applicable)
    allowed_exclusion_codes = set(
        normalized_contract["allowed_exclusion_codes"]
    )

    intended: list[dict[str, Any]] = []
    selected_pairs: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    unmatched_devices: list[str] = []

    for device in sorted(devices, key=_device_id):
        device_id = _device_id(device)
        if not device_id:
            raise ValueError("every scope device requires device_id")
        role = _device_role(device)
        required = set(
            normalized_contract["explicit_required_by_device"].get(
                device_id,
                [],
            )
        )
        matched_rule = False
        for rule in normalized_contract["device_role_rules"]:
            roles = {
                str(item).strip().casefold()
                for item in rule.get("roles") or []
                if str(item).strip()
            }
            if roles and role not in roles:
                continue
            matched_rule = True
            required.update(
                str(item).strip()
                for item in rule.get("required_check_items") or []
                if str(item).strip()
            )
        if (
            normalized_contract["device_role_rules"]
            and not matched_rule
            and not required
        ):
            unmatched_devices.append(device_id)

        selected_for_device = set(selected.get(device_id, ()))
        all_known_ids = required | selected_for_device
        unknown = sorted(all_known_ids - set(check_item_version_ids))
        if unknown:
            raise ValueError(
                "scope references unknown active check items: "
                + ", ".join(unknown)
            )

        for check_item_id in sorted(required):
            intended.append(
                {
                    "device_id": device_id,
                    "check_item_id": check_item_id,
                    "check_item_version_id": check_item_version_ids[
                        check_item_id
                    ],
                    "requirement": "required",
                    "device_role": role or None,
                }
            )

        for check_item_id in sorted(selected_for_device):
            pair = {
                "device_id": device_id,
                "check_item_id": check_item_id,
                "check_item_version_id": check_item_version_ids[
                    check_item_id
                ],
            }
            selected_pairs.append(pair)

        for check_item_id in sorted(required | selected_for_device):
            key = _pair_key(device_id, check_item_id)
            decision = SCOPE_INCLUDED
            reason_codes: list[str] = []
            evidence: dict[str, Any] = {}
            if key in not_applicable_by_pair:
                decision = SCOPE_NOT_APPLICABLE
                evidence = not_applicable_by_pair[key]
                reason_codes = [
                    str(item)
                    for item in evidence.get("reason_codes") or []
                    if str(item)
                ] or ["approved_applicability_rule"]
            elif key in exclusion_by_pair:
                evidence = exclusion_by_pair[key]
                reason_code = str(evidence.get("reason_code") or "").strip()
                approved_by = str(evidence.get("approved_by") or "").strip()
                if (
                    reason_code in allowed_exclusion_codes
                    and approved_by
                ):
                    decision = SCOPE_APPROVED_EXCLUSION
                    reason_codes = [reason_code]
                else:
                    decision = SCOPE_UNRESOLVED
                    reason_codes = ["scope_exclusion_not_approved"]
            elif check_item_id not in selected_for_device:
                decision = SCOPE_UNRESOLVED
                reason_codes = ["required_check_item_not_selected"]
            elif check_item_id not in required:
                reason_codes = ["additional_selected_check_item"]
            else:
                reason_codes = ["required_check_item_selected"]
            if decision not in _VALID_DECISIONS:
                raise ValueError(f"invalid scope decision: {decision}")
            decision_payload = {
                "device_id": device_id,
                "check_item_id": check_item_id,
                "check_item_version_id": check_item_version_ids[
                    check_item_id
                ],
                "decision": decision,
                "requirement": (
                    "required" if check_item_id in required else "additional"
                ),
                "reason_codes": sorted(set(reason_codes)),
                "evidence": evidence,
            }
            decisions.append(
                {
                    **decision_payload,
                    "digest": _digest(decision_payload),
                }
            )

    intended.sort(
        key=lambda item: (
            item["device_id"],
            item["check_item_version_id"],
        )
    )
    selected_pairs.sort(
        key=lambda item: (
            item["device_id"],
            item["check_item_version_id"],
        )
    )
    decisions.sort(
        key=lambda item: (
            item["device_id"],
            item["check_item_version_id"],
        )
    )
    intended_target_matrix = {
        "schema_version": "intended-target-matrix/v1",
        "scope_contract_digest": contract_digest,
        "inventory_snapshot_digest": str(
            inventory_snapshot_digest or ""
        ),
        "target_device_ids": sorted(
            _device_id(device) for device in devices if _device_id(device)
        ),
        "pairs": intended,
    }
    intended_digest = _digest(intended_target_matrix)
    decision_digest = _digest(
        {
            "schema_version": "scope-decisions/v1",
            "scope_contract_digest": contract_digest,
            "intended_target_matrix_digest": intended_digest,
            "decisions": decisions,
            "unmatched_device_ids": sorted(unmatched_devices),
        }
    )
    scope_reconciled = bool(
        intended
        and not unmatched_devices
        and not any(
            item["decision"] == SCOPE_UNRESOLVED
            for item in decisions
        )
    )
    return ScopeResolution(
        contract=normalized_contract,
        contract_digest=contract_digest,
        intended_target_matrix=intended_target_matrix,
        intended_target_pairs=tuple(intended),
        intended_target_matrix_digest=intended_digest,
        decisions=tuple(decisions),
        scope_decision_digest=decision_digest,
        selected_target_pairs=tuple(selected_pairs),
        scope_reconciled=scope_reconciled,
        claim_boundary=normalized_contract["claim_boundary"],
        unmatched_device_ids=tuple(sorted(unmatched_devices)),
    )


__all__ = [
    "SCOPE_APPROVED_EXCLUSION",
    "SCOPE_INCLUDED",
    "SCOPE_NOT_APPLICABLE",
    "SCOPE_UNRESOLVED",
    "ScopeResolution",
    "build_ad_hoc_scope_contract",
    "resolve_patrol_scope",
]

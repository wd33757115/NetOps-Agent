"""Deterministic, explainable collection implementation resolution."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from src.core.check_items.catalog import (
    CheckItemCatalog,
    CollectionImplementation,
    primary_collection_commands,
)
from src.core.parser.policy import DEFAULT_COMMAND_ALIASES, ParserPolicy


@dataclass(frozen=True)
class ImplementationDecision:
    check_item_id: str
    check_item_version_id: str
    status: str
    implementation_id: str | None
    implementation_version_id: str | None
    match_score: int
    reason_codes: tuple[str, ...]
    candidate_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_item_id": self.check_item_id,
            "check_item_version_id": self.check_item_version_id,
            "status": self.status,
            "implementation_id": self.implementation_id,
            "implementation_version_id": self.implementation_version_id,
            "match_score": self.match_score,
            "reason_codes": list(self.reason_codes),
            "candidate_ids": list(self.candidate_ids),
        }


@dataclass(frozen=True)
class ShadowCheckItemPlan:
    device_id: str
    decisions: tuple[ImplementationDecision, ...]
    collection_actions: tuple[dict[str, Any], ...]
    digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "mode": "shadow",
            "device_id": self.device_id,
            "decisions": [decision.to_dict() for decision in self.decisions],
            "collection_actions": [dict(action) for action in self.collection_actions],
            "digest": self.digest,
        }


def _normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def _canonical_command(value: Any) -> str:
    basic = ParserPolicy._basic_normalize(str(value or ""))
    return ParserPolicy._basic_normalize(DEFAULT_COMMAND_ALIASES.get(basic, basic))


def _as_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        return tuple(_normalized(item) for item in value if _normalized(item))
    normalized = _normalized(value)
    return (normalized,) if normalized else ()


def _device_value(device: dict[str, Any], key: str) -> Any:
    if key in device:
        return device.get(key)
    attributes = device.get("attributes") or device.get("attributes_json") or {}
    return attributes.get(key) if isinstance(attributes, dict) else None


class CheckItemImplementationResolver:
    """Resolve check items without guessing across missing or ambiguous identity."""

    def __init__(self, catalog: CheckItemCatalog) -> None:
        self.catalog = catalog

    def resolve(
        self,
        *,
        device: dict[str, Any],
        check_item_id: str,
    ) -> ImplementationDecision:
        check_item = self.catalog.check_item(check_item_id)
        if check_item is None or check_item.status != "active":
            return ImplementationDecision(
                check_item_id=check_item_id,
                check_item_version_id="",
                status="unsupported",
                implementation_id=None,
                implementation_version_id=None,
                match_score=0,
                reason_codes=("check_item_inactive_or_unknown",),
                candidate_ids=(),
            )
        candidates: list[tuple[CollectionImplementation, int]] = []
        rejection_codes: set[str] = set()
        for implementation in self.catalog.implementations:
            if (
                implementation.status != "active"
                or check_item_id not in implementation.check_item_ids
            ):
                continue
            matched, score, reasons = self._match(device, implementation.match)
            if matched:
                candidates.append((implementation, score))
            else:
                rejection_codes.update(reasons)
        if not candidates:
            return ImplementationDecision(
                check_item_id=check_item.id,
                check_item_version_id=check_item.version_id,
                status="unsupported",
                implementation_id=None,
                implementation_version_id=None,
                match_score=0,
                reason_codes=tuple(sorted(rejection_codes or {"no_compatible_implementation"})),
                candidate_ids=(),
            )
        candidates.sort(
            key=lambda item: (
                item[0].selection_priority,
                item[1],
                item[0].id,
            ),
            reverse=True,
        )
        best, best_score = candidates[0]
        ties = [
            item
            for item in candidates
            if item[0].selection_priority == best.selection_priority
            and item[1] == best_score
        ]
        if len(ties) > 1:
            return ImplementationDecision(
                check_item_id=check_item.id,
                check_item_version_id=check_item.version_id,
                status="ambiguous",
                implementation_id=None,
                implementation_version_id=None,
                match_score=best_score,
                reason_codes=("multiple_equally_specific_implementations",),
                candidate_ids=tuple(sorted(item[0].id for item in ties)),
            )
        return ImplementationDecision(
            check_item_id=check_item.id,
            check_item_version_id=check_item.version_id,
            status="resolved",
            implementation_id=best.id,
            implementation_version_id=best.version_id,
            match_score=best_score,
            reason_codes=("compatible_implementation_resolved",),
            candidate_ids=tuple(item[0].id for item in candidates),
        )

    def build_shadow_plan(
        self,
        *,
        device: dict[str, Any],
        check_item_ids: list[str] | tuple[str, ...] | None = None,
    ) -> ShadowCheckItemPlan:
        selected_ids = list(check_item_ids or [item.id for item in self.catalog.check_items])
        decisions = tuple(
            self.resolve(device=device, check_item_id=check_item_id)
            for check_item_id in selected_ids
        )
        implementation_by_version = {
            item.version_id: item for item in self.catalog.implementations
        }
        actions_by_command: dict[str, dict[str, Any]] = {}
        for decision in decisions:
            if decision.status != "resolved" or not decision.implementation_version_id:
                continue
            implementation = implementation_by_version[decision.implementation_version_id]
            ordered_commands = primary_collection_commands(
                implementation.commands,
                implementation_id=implementation.id,
                version_id=implementation.version_id,
            )
            for fallback_rank, command in enumerate(ordered_commands):
                key = " ".join(command.command.lower().split())
                action = actions_by_command.setdefault(
                    key,
                    {
                        "command": command.command,
                        "collector": implementation.collector,
                        "priority": command.priority,
                        "fallback_rank": fallback_rank,
                        "check_item_ids": [],
                        "implementation_version_ids": [],
                    },
                )
                action["priority"] = max(int(action["priority"]), command.priority)
                action["fallback_rank"] = min(
                    int(action["fallback_rank"]),
                    fallback_rank,
                )
                if decision.check_item_id not in action["check_item_ids"]:
                    action["check_item_ids"].append(decision.check_item_id)
                if implementation.version_id not in action["implementation_version_ids"]:
                    action["implementation_version_ids"].append(implementation.version_id)
        actions = tuple(
            {
                **action,
                "check_item_ids": sorted(action["check_item_ids"]),
                "implementation_version_ids": sorted(
                    action["implementation_version_ids"]
                ),
            }
            for _key, action in sorted(
                actions_by_command.items(),
                key=lambda item: (
                    int(item[1]["fallback_rank"]),
                    -int(item[1]["priority"]),
                    item[0],
                ),
            )
        )
        digest_payload = {
            "version": 1,
            "mode": "shadow",
            "device_id": str(device.get("device_id") or device.get("id") or ""),
            "decisions": [decision.to_dict() for decision in decisions],
            "collection_actions": list(actions),
        }
        digest = hashlib.sha256(
            json.dumps(
                digest_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return ShadowCheckItemPlan(
            device_id=digest_payload["device_id"],
            decisions=decisions,
            collection_actions=actions,
            digest=digest,
        )

    def project_legacy_commands(
        self,
        *,
        device: dict[str, Any],
        commands: list[str] | tuple[str, ...],
    ) -> dict[str, Any]:
        """Project an unchanged command plan onto check items for shadow comparison."""

        legacy_by_canonical: dict[str, str] = {}
        for command in commands:
            canonical = _canonical_command(command)
            if canonical:
                legacy_by_canonical.setdefault(canonical, str(command).strip())
        implementation_by_version = {
            item.version_id: item for item in self.catalog.implementations
        }
        decisions: list[dict[str, Any]] = []
        actions_by_command: dict[str, dict[str, Any]] = {}
        for check_item in self.catalog.check_items:
            decision = self.resolve(device=device, check_item_id=check_item.id)
            projection_status = decision.status
            selected_command: str | None = None
            if decision.status == "resolved" and decision.implementation_version_id:
                implementation = implementation_by_version[
                    decision.implementation_version_id
                ]
                matching_commands = sorted(
                    (
                        command
                        for command in implementation.commands
                        if _canonical_command(command.command) in legacy_by_canonical
                    ),
                    key=lambda item: (-item.priority, item.command.lower()),
                )
                if matching_commands:
                    selected = matching_commands[0]
                    canonical = _canonical_command(selected.command)
                    selected_command = legacy_by_canonical[canonical]
                    projection_status = "mapped"
                    action = actions_by_command.setdefault(
                        canonical,
                        {
                            "command": selected_command,
                            "canonical_command": canonical,
                            "check_item_ids": [],
                            "implementation_version_ids": [],
                        },
                    )
                    if check_item.id not in action["check_item_ids"]:
                        action["check_item_ids"].append(check_item.id)
                    if implementation.version_id not in action[
                        "implementation_version_ids"
                    ]:
                        action["implementation_version_ids"].append(
                            implementation.version_id
                        )
                else:
                    projection_status = "not_in_legacy_profile"
            decisions.append(
                {
                    **decision.to_dict(),
                    "projection_status": projection_status,
                    "selected_legacy_command": selected_command,
                }
            )
        mapped_commands = set(actions_by_command)
        actions = [
            {
                **action,
                "check_item_ids": sorted(action["check_item_ids"]),
                "implementation_version_ids": sorted(
                    action["implementation_version_ids"]
                ),
            }
            for _canonical, action in sorted(actions_by_command.items())
        ]
        decision_summary: dict[str, int] = {}
        for decision in decisions:
            status = str(decision["projection_status"])
            decision_summary[status] = decision_summary.get(status, 0) + 1
        unmapped_commands = [
            legacy_by_canonical[key]
            for key in sorted(set(legacy_by_canonical) - mapped_commands)
        ]
        payload = {
            "version": 1,
            "mode": "legacy_command_projection",
            "status": "ready",
            "catalog_version": self.catalog.catalog_version,
            "device_id": str(device.get("device_id") or device.get("id") or ""),
            "legacy_commands": [
                legacy_by_canonical[key] for key in sorted(legacy_by_canonical)
            ],
            "decisions": decisions,
            "collection_actions": actions,
            "mapped_check_item_ids": sorted(
                {
                    check_item_id
                    for action in actions
                    for check_item_id in action["check_item_ids"]
                }
            ),
            "unmapped_commands": unmapped_commands,
            "metrics": {
                "legacy_command_count": len(legacy_by_canonical),
                "mapped_command_count": len(mapped_commands),
                "unmapped_command_count": len(unmapped_commands),
                "mapped_check_item_count": len(
                    {
                        check_item_id
                        for action in actions
                        for check_item_id in action["check_item_ids"]
                    }
                ),
                "command_mapping_rate": (
                    round(len(mapped_commands) / len(legacy_by_canonical), 6)
                    if legacy_by_canonical
                    else 1.0
                ),
            },
            "decision_summary": dict(sorted(decision_summary.items())),
        }
        payload["digest"] = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return payload

    @staticmethod
    def _match(
        device: dict[str, Any],
        selector: dict[str, Any],
    ) -> tuple[bool, int, tuple[str, ...]]:
        score = 0
        reasons: list[str] = []

        def exact(
            selector_key: str,
            device_key: str,
            *,
            weight: int,
        ) -> bool:
            nonlocal score
            expected = _as_values(selector.get(selector_key))
            if not expected:
                return True
            actual = _normalized(_device_value(device, device_key))
            if not actual:
                reasons.append(f"identity_missing:{device_key}")
                return False
            if actual not in expected:
                reasons.append(f"identity_mismatch:{device_key}")
                return False
            score += weight
            return True

        if not exact("vendor", "vendor", weight=20):
            return False, 0, tuple(reasons)
        if not exact("vendors", "vendor", weight=20):
            return False, 0, tuple(reasons)
        if not exact("families", "family", weight=30):
            return False, 0, tuple(reasons)
        if not exact("platforms", "platform", weight=25):
            return False, 0, tuple(reasons)

        for selector_key, device_key, weight in (
            ("model_patterns", "model", 40),
            ("os_version_patterns", "os_version", 10),
        ):
            patterns = tuple(str(item) for item in selector.get(selector_key) or ())
            if not patterns:
                continue
            actual = str(_device_value(device, device_key) or "").strip()
            if not actual:
                reasons.append(f"identity_missing:{device_key}")
                return False, 0, tuple(reasons)
            if not any(re.search(pattern, actual, flags=re.IGNORECASE) for pattern in patterns):
                reasons.append(f"identity_mismatch:{device_key}")
                return False, 0, tuple(reasons)
            score += weight

        required_capabilities = set(_as_values(selector.get("capabilities")))
        if required_capabilities:
            actual_capabilities = set(_as_values(_device_value(device, "capabilities")))
            if not required_capabilities.issubset(actual_capabilities):
                reasons.append("capability_missing")
                return False, 0, tuple(reasons)
            score += 15
        return True, score, ("selector_matched",)

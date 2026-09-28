"""Immutable Parser and rule-asset selection for one patrol run."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any



def _digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _manifest_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": int(manifest.get("version") or 1),
        "parser_bundle": dict(manifest.get("parser_bundle") or {}),
        "rule_assets": dict(manifest.get("rule_assets") or {}),
        "readiness": dict(manifest.get("readiness") or {}),
    }


def _check_item_manifest_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in manifest.items()
        if key not in {"digest", "manifest_id", "manifest_kind"}
    }




def validate_patrol_runtime_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(manifest or {})
    supplied = str(normalized.get("digest") or "")
    actual = _digest(
        _check_item_manifest_payload(normalized)
        if normalized.get("manifest_kind") == "check_item_runtime"
        else _manifest_payload(normalized)
    )
    if not supplied or supplied != actual:
        raise ValueError("patrol runtime asset manifest digest mismatch")
    return normalized


def resolve_patrol_runtime_rule_assets(
    manifest: dict[str, Any],
    *,
    bundle_service: Any | None = None,
) -> dict[str, Any]:
    """Resolve rules from the exact asset versions frozen for this patrol."""

    normalized = validate_patrol_runtime_manifest(manifest)
    if normalized.get("manifest_kind") != "check_item_runtime":
        return dict(normalized.get("rule_assets") or {})

    if bundle_service is None:
        from src.apps.rule_governance.runtime import CheckItemRuntimeReader

        bundle_service = CheckItemRuntimeReader()

    policies: dict[str, dict[str, Any]] = {}
    event_rules: dict[str, dict[str, Any]] = {}
    policies_by_check_item: dict[str, dict[str, dict[str, Any]]] = {}
    event_rules_by_check_item: dict[str, dict[str, dict[str, Any]]] = {}
    policies_by_target: dict[
        str, dict[str, dict[str, dict[str, Any]]]
    ] = {}
    event_rules_by_target: dict[
        str, dict[str, dict[str, dict[str, Any]]]
    ] = {}
    loaded_by_version: dict[str, dict[str, Any]] = {}
    refs = list(normalized.get("runtime_bundle_refs") or [])
    for ref in refs:
        if not isinstance(ref, dict):
            raise ValueError("check-item runtime manifest contains an invalid bundle ref")
        version_id = str(ref.get("bundle_version_id") or "")
        expected_digest = str(ref.get("digest") or "")
        if not version_id or not expected_digest:
            raise ValueError("check-item runtime manifest bundle identity is incomplete")
        loaded = loaded_by_version.get(version_id)
        if loaded is None:
            loaded = bundle_service.get_rule_assets(version_id)
            if loaded is None:
                raise ValueError(f"frozen RuntimeBundle version not found: {version_id}")
            loaded_by_version[version_id] = loaded
        bundle = dict(loaded.get("bundle") or {})
        expected_identity = {
            "version_id": version_id,
            "digest": expected_digest,
            "bundle_id": str(ref.get("bundle_id") or ""),
            "check_item_version_id": str(ref.get("check_item_version_id") or ""),
            "implementation_version_id": str(
                ref.get("implementation_version_id") or ""
            ),
            "entity_identity_policy_version_id": str(
                ref.get("entity_identity_policy_version_id") or ""
            ),
        }
        for key, expected in expected_identity.items():
            if expected and str(bundle.get(key) or "") != expected:
                raise ValueError(
                    f"frozen RuntimeBundle {key} mismatch: {version_id}"
                )
        check_item_version_id = str(ref.get("check_item_version_id") or "")
        device_id = str(ref.get("device_id") or "")
        if not check_item_version_id:
            raise ValueError(
                "check-item runtime manifest bundle ref has no check item version"
            )
        if not device_id:
            raise ValueError(
                "check-item runtime manifest bundle ref has no device scope"
            )
        scoped_policies = policies_by_check_item.setdefault(
            check_item_version_id,
            {},
        )
        scoped_event_rules = event_rules_by_check_item.setdefault(
            check_item_version_id,
            {},
        )
        target_policies = policies_by_target.setdefault(
            device_id,
            {},
        ).setdefault(check_item_version_id, {})
        target_event_rules = event_rules_by_target.setdefault(
            device_id,
            {},
        ).setdefault(check_item_version_id, {})
        for row in loaded.get("change_policies") or []:
            item = dict(row)
            asset_id = str(item.get("version_id") or "")
            if not asset_id:
                raise ValueError("frozen ChangePolicy has no version identity")
            if asset_id in policies and policies[asset_id] != item:
                raise ValueError(f"conflicting frozen ChangePolicy: {asset_id}")
            policies[asset_id] = item
            if asset_id in scoped_policies and scoped_policies[asset_id] != item:
                raise ValueError(
                    "conflicting frozen ChangePolicy in check-item scope: "
                    f"{check_item_version_id}/{asset_id}"
                )
            scoped_policies[asset_id] = item
            target_policies[asset_id] = item
        for row in loaded.get("event_rules") or []:
            item = dict(row)
            asset_id = str(item.get("version_id") or "")
            if not asset_id:
                raise ValueError("frozen EventRule has no version identity")
            if asset_id in event_rules and event_rules[asset_id] != item:
                raise ValueError(f"conflicting frozen EventRule: {asset_id}")
            event_rules[asset_id] = item
            if asset_id in scoped_event_rules and scoped_event_rules[asset_id] != item:
                raise ValueError(
                    "conflicting frozen EventRule in check-item scope: "
                    f"{check_item_version_id}/{asset_id}"
                )
            scoped_event_rules[asset_id] = item
            target_event_rules[asset_id] = item

    return {
        "change_policies": {
            key: policies[key] for key in sorted(policies)
        },
        "event_rules": [event_rules[key] for key in sorted(event_rules)],
        "change_policies_by_check_item_version_id": {
            check_item_version_id: [
                rows[asset_id] for asset_id in sorted(rows)
            ]
            for check_item_version_id, rows in sorted(
                policies_by_check_item.items()
            )
        },
        "event_rules_by_check_item_version_id": {
            check_item_version_id: [
                rows[asset_id] for asset_id in sorted(rows)
            ]
            for check_item_version_id, rows in sorted(
                event_rules_by_check_item.items()
            )
        },
        "change_policies_by_device_and_check_item_version_id": {
            device_id: {
                check_item_version_id: [
                    rows[asset_id] for asset_id in sorted(rows)
                ]
                for check_item_version_id, rows in sorted(
                    check_items.items()
                )
            }
            for device_id, check_items in sorted(
                policies_by_target.items()
            )
        },
        "event_rules_by_device_and_check_item_version_id": {
            device_id: {
                check_item_version_id: [
                    rows[asset_id] for asset_id in sorted(rows)
                ]
                for check_item_version_id, rows in sorted(
                    check_items.items()
                )
            }
            for device_id, check_items in sorted(
                event_rules_by_target.items()
            )
        },
    }


def comparison_scopes_for_device(
    manifest: dict[str, Any],
    *,
    device_id: str,
) -> list[dict[str, Any]]:
    """Resolve signed, target-level comparison scopes for one device run."""

    normalized = validate_patrol_runtime_manifest(manifest)
    if normalized.get("manifest_kind") != "check_item_runtime":
        return []
    normalized_device_id = str(device_id or "").strip()
    if not normalized_device_id:
        raise ValueError("device_id is required for check-item comparison scopes")

    contexts = {
        str(item.get("id") or ""): dict(item)
        for item in normalized.get("comparison_contexts") or []
        if isinstance(item, dict) and item.get("id")
    }
    actions = {
        str(item.get("id") or ""): dict(item)
        for item in normalized.get("collection_actions") or []
        if isinstance(item, dict)
        and item.get("id")
        and str(item.get("device_id") or "") == normalized_device_id
    }
    scopes: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for decision in normalized.get("decisions") or []:
        if (
            not isinstance(decision, dict)
            or str(decision.get("device_id") or "") != normalized_device_id
        ):
            continue
        check_item_version_id = str(
            decision.get("check_item_version_id") or ""
        )
        execution_id = str(decision.get("execution_id") or "")
        context_id = str(decision.get("comparison_context_id") or "")
        context_digest = str(decision.get("comparison_context_digest") or "")
        if not check_item_version_id or not execution_id or not context_id:
            raise ValueError(
                "check-item runtime decision has incomplete comparison identity"
            )
        scope_key = (check_item_version_id, execution_id)
        if scope_key in seen:
            raise ValueError(
                "check-item runtime manifest contains a duplicate comparison scope"
            )
        seen.add(scope_key)
        context = contexts.get(context_id)
        if context is None or str(context.get("digest") or "") != context_digest:
            raise ValueError(
                "check-item runtime comparison context identity mismatch"
            )
        if context.get("device_id") and str(context["device_id"]) != normalized_device_id:
            raise ValueError("comparison context device scope mismatch")
        if (
            context.get("check_item_version_id")
            and str(context["check_item_version_id"]) != check_item_version_id
        ):
            raise ValueError("comparison context check-item scope mismatch")

        action_ids = sorted(
            {
                str(item)
                for item in decision.get("collection_action_ids") or []
                if str(item)
            }
        )
        for action_id in action_ids:
            action = actions.get(action_id)
            if action is None:
                raise ValueError(
                    "comparison scope references a missing collection action"
                )
            bound_versions = {
                str(binding.get("check_item_version_id") or "")
                for binding in action.get("bindings") or []
                if isinstance(binding, dict)
            }
            if check_item_version_id not in bound_versions:
                raise ValueError(
                    "comparison scope references an action outside its check item"
                )
        status = str(context.get("comparison_status") or "baseline_missing")
        baseline_snapshot_ids = sorted(
            {
                str(item)
                for item in context.get("baseline_snapshot_ids") or []
                if str(item)
            }
        )
        baseline_run_id = str(context.get("baseline_run_id") or "") or None
        if status == "comparable" and (
            not baseline_run_id or not baseline_snapshot_ids
        ):
            raise ValueError(
                "comparable context has no frozen baseline run or snapshots"
            )
        scopes.append(
            {
                "device_id": normalized_device_id,
                "check_item_id": str(decision.get("check_item_id") or "") or None,
                "check_item_version_id": check_item_version_id,
                "check_execution_id": execution_id,
                "comparison_context_id": context_id,
                "comparison_context_digest": context_digest,
                "comparison_status": status,
                **({'change_tracking': dict(decision['change_tracking'])} if 'change_tracking' in decision else {}),
                "selection_reason": str(
                    context.get("selection_reason") or status
                ),
                "baseline_run_id": baseline_run_id,
                "baseline_snapshot_ids": baseline_snapshot_ids,
                "collection_action_ids": action_ids,
                "runtime_bundle_version_id": str(
                    decision.get("runtime_bundle_version_id") or ""
                )
                or None,
            }
        )
    return sorted(
        scopes,
        key=lambda item: (
            str(item["check_item_version_id"]),
            str(item["check_execution_id"]),
        ),
    )


def first_observation_collection_action_ids(
    manifest: dict[str, Any],
    *,
    device_id: str,
) -> set[str]:
    """Return actions whose every bound check item has no historical baseline."""

    normalized = validate_patrol_runtime_manifest(manifest)
    if normalized.get("manifest_kind") != "check_item_runtime":
        return set()
    normalized_device_id = str(device_id or "").strip()
    if not normalized_device_id:
        return set()
    contexts = {
        str(item.get("id") or ""): dict(item)
        for item in normalized.get("comparison_contexts") or []
        if isinstance(item, dict) and item.get("id")
    }
    first_versions: set[str] = set()
    non_first_versions: set[str] = set()
    for decision in normalized.get("decisions") or []:
        if (
            not isinstance(decision, dict)
            or str(decision.get("device_id") or "") != normalized_device_id
        ):
            continue
        check_version_id = str(
            decision.get("check_item_version_id") or ""
        )
        context = contexts.get(
            str(decision.get("comparison_context_id") or "")
        )
        if not check_version_id or context is None:
            continue
        is_first = (
            str(context.get("mode") or "") == "no_baseline"
            and str(context.get("comparison_status") or "")
            == "first_observation"
        )
        (first_versions if is_first else non_first_versions).add(
            check_version_id
        )

    action_ids: set[str] = set()
    for action in normalized.get("collection_actions") or []:
        if (
            not isinstance(action, dict)
            or str(action.get("device_id") or "") != normalized_device_id
        ):
            continue
        bound_versions = {
            str(binding.get("check_item_version_id") or "")
            for binding in action.get("bindings") or []
            if isinstance(binding, dict)
            and binding.get("check_item_version_id")
        }
        if (
            bound_versions
            and bound_versions.issubset(first_versions)
            and not bound_versions.intersection(non_first_versions)
            and action.get("id")
        ):
            action_ids.add(str(action["id"]))
    return action_ids

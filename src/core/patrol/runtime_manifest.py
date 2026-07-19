"""Immutable Parser and rule-asset selection for one patrol run."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from src.core.rule_control import RuntimeRuleAssetResolver


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


def build_patrol_runtime_manifest(
    parser_bundle: Any | None,
    *,
    resolver: RuntimeRuleAssetResolver | None = None,
) -> dict[str, Any]:
    parser_data = parser_bundle.to_dict() if parser_bundle is not None else {}
    template_refs = {
        str(command): dict(value)
        for command, value in dict(parser_data.get("templates") or {}).items()
        if value
    }
    schemas = [("cli", str(value["template_id"])) for value in template_refs.values()]
    rule_bundle = (resolver or RuntimeRuleAssetResolver()).load_bundle(
        observation_schemas=schemas,
        include_event_rules=True,
    )
    rule_data = rule_bundle.to_dict()
    missing_parser_templates = sorted(
        str(command)
        for command, value in dict(parser_data.get("templates") or {}).items()
        if not value
    )
    readiness = {
        "collection": {
            "ready": True,
            "parser_bundle_resolved": bool(parser_data),
            "missing_parser_templates": missing_parser_templates,
        },
        "normalization": {
            "ready": rule_bundle.collection_ready,
            "missing_observation_contracts": list(
                rule_bundle.missing_observation_contracts
            ),
        },
        "change_detection": {
            "ready": rule_bundle.analysis_ready,
            "missing_change_policies": list(rule_bundle.missing_change_policies),
        },
        "event_evaluation": {
            "ready": rule_bundle.event_evaluation_ready,
            "active_event_rule_count": len(rule_bundle.event_rules),
        },
    }
    manifest = {
        "version": 1,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "parser_bundle": parser_data,
        "rule_assets": rule_data,
        "readiness": readiness,
    }
    manifest["digest"] = _digest(_manifest_payload(manifest))
    return manifest


def validate_patrol_runtime_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(manifest or {})
    supplied = str(normalized.get("digest") or "")
    actual = _digest(_manifest_payload(normalized))
    if not supplied or supplied != actual:
        raise ValueError("patrol runtime asset manifest digest mismatch")
    return normalized

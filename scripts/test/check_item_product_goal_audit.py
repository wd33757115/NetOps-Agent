"""Export a conservative, machine-readable product-goal completion audit."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.check_items.product_goal_audit import (  # noqa: E402
    evaluate_product_goal_snapshot,
)
from src.infrastructure.db.postgres import engine  # noqa: E402


def _contains_production_claim(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("production_network_claim") is True:
            return True
        return any(_contains_production_claim(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_production_claim(item) for item in value)
    return False


def _is_production_candidate_device(row: dict[str, Any]) -> bool:
    if not row.get("credential_ref"):
        return False
    try:
        address = ipaddress.ip_address(str(row.get("management_ip") or ""))
    except ValueError:
        return False
    return not (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address in ipaddress.ip_network("192.0.2.0/24")
        or address in ipaddress.ip_network("198.51.100.0/24")
        or address in ipaddress.ip_network("203.0.113.0/24")
    )


def _status_counts(connection, table_name: str) -> dict[str, int]:
    allowed_tables = {
        "netops_check_items",
        "netops_collection_implementations",
        "parser_templates",
        "netops_observation_contracts",
        "netops_change_policy_versions",
        "netops_event_rule_versions",
        "netops_assessment_policy_versions",
        "netops_text_comparison_profile_versions",
        "netops_check_item_runtime_bundle_versions",
    }
    if table_name not in allowed_tables:
        raise ValueError(f"unsupported audit table: {table_name}")
    rows = connection.execute(
        text(
            f"SELECT status, count(*) AS count FROM {table_name} "  # noqa: S608
            "GROUP BY status ORDER BY status"
        )
    )
    return {str(row.status): int(row.count) for row in rows}


def collect_snapshot() -> dict[str, Any]:
    with engine.connect() as connection:
        active_bundle_items = connection.execute(
            text(
                "SELECT DISTINCT b.check_item_id "
                "FROM netops_check_item_runtime_bundle_versions v "
                "JOIN netops_check_item_runtime_bundles b ON b.id = v.bundle_id "
                "WHERE v.status = 'active' AND b.status = 'active'"
            )
        ).scalars().all()
        devices = [
            dict(row._mapping)
            for row in connection.execute(
                text(
                    "SELECT id, management_ip, vendor, credential_ref "
                    "FROM netops_devices WHERE status = 'active'"
                )
            )
        ]
        production_candidates = [
            row for row in devices if _is_production_candidate_device(row)
        ]
        manifests = [
            dict(row._mapping)
            for row in connection.execute(
                text(
                    "SELECT id, patrol_batch_id, source_facts_json "
                    "FROM netops_report_manifests "
                    "WHERE report_purpose = 'production_current' AND status = 'final'"
                )
            )
        ]
        production_claim_manifest_ids = {
            str(row["id"])
            for row in manifests
            if _contains_production_claim(row.get("source_facts_json"))
        }
        accepted_review_count = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM netops_report_content_reviews r "
                    "JOIN netops_report_manifests m ON m.id = r.report_manifest_id "
                    "WHERE r.status = 'accepted' "
                    "AND m.report_purpose = 'production_current'"
                )
            ).scalar_one()
        )
        production_release_rows = [
            dict(row._mapping)
            for row in connection.execute(
                text(
                    "SELECT rel.report_manifest_id, rel.patrol_batch_id "
                    "FROM netops_report_releases rel "
                    "JOIN netops_report_manifests m ON m.id = rel.report_manifest_id "
                    "LEFT JOIN netops_report_content_reviews r "
                    "ON r.id = rel.content_review_id "
                    "WHERE m.report_purpose = 'production_current' "
                    "AND rel.status = 'released' AND r.status = 'accepted'"
                )
            )
        ]
        legacy_call_count = int(
            connection.execute(
                text("SELECT count(*) FROM netops_legacy_path_observations")
            ).scalar_one()
        )
        asset_inventory = {
            "check_items": _status_counts(connection, "netops_check_items"),
            "collection_implementations": _status_counts(
                connection,
                "netops_collection_implementations",
            ),
            "parser_templates": _status_counts(connection, "parser_templates"),
            "observation_contracts": _status_counts(
                connection,
                "netops_observation_contracts",
            ),
            "change_policy_versions": _status_counts(
                connection,
                "netops_change_policy_versions",
            ),
            "event_rule_versions": _status_counts(
                connection,
                "netops_event_rule_versions",
            ),
            "assessment_policy_versions": _status_counts(
                connection,
                "netops_assessment_policy_versions",
            ),
            "text_comparison_profile_versions": _status_counts(
                connection,
                "netops_text_comparison_profile_versions",
            ),
            "runtime_bundle_versions": _status_counts(
                connection,
                "netops_check_item_runtime_bundle_versions",
            ),
        }

    deprecation_path = ROOT / "docs" / "implementation" / "deprecations.yaml"
    deprecation_payload = yaml.safe_load(deprecation_path.read_text(encoding="utf-8")) or {}
    candidates = list(deprecation_payload.get("candidates") or [])
    exit_ready = [
        item
        for item in candidates
        if str(item.get("status") or "") in {"ready_to_remove", "removed"}
    ]
    accepted_cycles = {
        str(row["patrol_batch_id"])
        for row in production_release_rows
        if str(row["report_manifest_id"]) in production_claim_manifest_ids
    }
    return {
        "active_runtime_bundle_check_items": sorted(
            str(item) for item in active_bundle_items
        ),
        "active_inventory_device_count": len(devices),
        "production_candidate_device_count": len(production_candidates),
        "production_candidate_vendor_count": len(
            {
                str(row.get("vendor") or "").strip().lower()
                for row in production_candidates
                if str(row.get("vendor") or "").strip()
            }
        ),
        "production_claim_manifest_count": len(production_claim_manifest_ids),
        "accepted_production_cycle_count": len(accepted_cycles),
        "accepted_content_review_count": accepted_review_count,
        "production_release_count": len(production_release_rows),
        "deprecation_candidate_count": len(candidates),
        "deprecation_exit_ready_count": len(exit_ready),
        "legacy_call_count": legacy_call_count,
        "asset_inventory": asset_inventory,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--external-evidence",
        type=Path,
        help="Immutable evidence manifest for human/customer-owned gates.",
    )
    parser.add_argument(
        "--require-achieved",
        action="store_true",
        help="Return non-zero unless every product-goal gate is proven passed.",
    )
    args = parser.parse_args()
    snapshot = collect_snapshot()
    external_evidence: dict[str, Any] = {}
    external_evidence_sha256 = None
    if args.external_evidence:
        evidence_bytes = args.external_evidence.read_bytes()
        external_evidence_sha256 = hashlib.sha256(evidence_bytes).hexdigest()
        payload = json.loads(evidence_bytes.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("external evidence manifest must be a JSON object")
        external_evidence = dict(payload.get("gates") or {})
    result = {
        **evaluate_product_goal_snapshot(
            snapshot,
            external_evidence=external_evidence,
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot": snapshot,
        "external_evidence_manifest": {
            "path": str(args.external_evidence) if args.external_evidence else None,
            "sha256": external_evidence_sha256,
        },
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 1 if args.require_achieved and not result["product_goal_achieved"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

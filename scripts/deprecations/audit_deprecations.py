#!/usr/bin/env python3
"""Validate deprecation ownership and evidence before code removal."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "docs" / "implementation" / "deprecations.yaml"
STATUSES = {
    "identified",
    "write_disabled",
    "read_only",
    "observing",
    "ready_to_remove",
    "removed",
}
REQUIRED_GATES = {
    "static_references_cleared",
    "runtime_traffic_zero",
    "database_writes_zero",
    "task_registry_cleared",
    "replacement_coverage_complete",
    "tests_and_migrations_passed",
    "rollback_rehearsed",
}


def _date(value: Any, *, label: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD") from exc


def audit(path: Path, *, today: date) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("unsupported deprecation registry schema")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("deprecation registry requires candidates")

    errors: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    status_counts = {status: 0 for status in STATUSES}
    for index, raw in enumerate(candidates, start=1):
        if not isinstance(raw, dict):
            errors.append(f"candidates[{index}] must be an object")
            continue
        candidate_id = str(raw.get("id") or "").strip()
        if not candidate_id:
            errors.append(f"candidates[{index}].id is required")
            continue
        if candidate_id in seen:
            errors.append(f"duplicate deprecation id: {candidate_id}")
        seen.add(candidate_id)
        status = str(raw.get("status") or "").strip()
        if status not in STATUSES:
            errors.append(
                f"{candidate_id}: invalid status {status or '<empty>'}"
            )
            continue
        status_counts[status] += 1
        for field in ("owner", "replacement", "rollback"):
            if not str(raw.get(field) or "").strip():
                errors.append(f"{candidate_id}: {field} is required")
        try:
            deprecated_at = _date(
                raw.get("deprecated_at"),
                label=f"{candidate_id}.deprecated_at",
            )
            remove_after = _date(
                raw.get("remove_after"),
                label=f"{candidate_id}.remove_after",
            )
            if remove_after <= deprecated_at:
                errors.append(
                    f"{candidate_id}: remove_after must be after deprecated_at"
                )
            if (
                remove_after < today
                and status not in {"ready_to_remove", "removed"}
            ):
                warnings.append(
                    f"{candidate_id}: remove_after has passed but removal "
                    f"gates are not complete"
                )
        except ValueError as exc:
            errors.append(str(exc))

        gates = raw.get("gates")
        if not isinstance(gates, dict):
            errors.append(f"{candidate_id}: gates must be an object")
            continue
        missing_gates = sorted(REQUIRED_GATES - set(gates))
        if missing_gates:
            errors.append(
                f"{candidate_id}: missing gates "
                + ", ".join(missing_gates)
            )
        unknown_gates = sorted(set(gates) - REQUIRED_GATES)
        if unknown_gates:
            errors.append(
                f"{candidate_id}: unknown gates "
                + ", ".join(unknown_gates)
            )
        completed = {
            key for key, value in gates.items() if value is True
        }
        if status in {"ready_to_remove", "removed"}:
            incomplete = sorted(REQUIRED_GATES - completed)
            if incomplete:
                errors.append(
                    f"{candidate_id}: {status} requires completed gates "
                    + ", ".join(incomplete)
                )
            evidence = raw.get("evidence")
            if not isinstance(evidence, dict) or not evidence:
                errors.append(
                    f"{candidate_id}: {status} requires immutable evidence"
                )
        if status == "removed" and not raw.get("removed_at"):
            errors.append(f"{candidate_id}: removed_at is required")

    return {
        "success": not errors,
        "candidate_count": len(candidates),
        "status_counts": status_counts,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=date.today(),
    )
    args = parser.parse_args()
    try:
        result = audit(args.registry.resolve(), today=args.today)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"deprecation audit configuration error: {exc}", file=sys.stderr)
        return 2
    for warning in result["warnings"]:
        print(f"[WARN] {warning}")
    for error in result["errors"]:
        print(f"[ERROR] {error}")
    statuses = " ".join(
        f"{key}={value}"
        for key, value in sorted(result["status_counts"].items())
        if value
    )
    print(
        f"Deprecation audit: candidates={result['candidate_count']} "
        f"{statuses}"
    )
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

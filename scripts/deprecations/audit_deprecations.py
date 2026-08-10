#!/usr/bin/env python3
"""Validate deprecation ownership and evidence before code removal."""

from __future__ import annotations

import argparse
import re
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
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _date(value: Any, *, label: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD") from exc


def _timestamp(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must use ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed


def _artifact_errors(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{label} must be an object"]
    errors: list[str] = []
    uri = str(value.get("artifact_uri") or "").strip()
    digest = str(value.get("sha256") or "").strip().lower()
    if not uri or "://" not in uri:
        errors.append(f"{label}.artifact_uri must be an immutable URI")
    if not SHA256_PATTERN.fullmatch(digest):
        errors.append(f"{label}.sha256 must be a lowercase SHA-256 digest")
    return errors


def _removal_evidence_errors(
    candidate_id: str,
    candidate: dict[str, Any],
) -> list[str]:
    evidence = candidate.get("evidence")
    if not isinstance(evidence, dict):
        return [f"{candidate_id}: ready/removed state requires evidence object"]

    errors: list[str] = []
    if int(evidence.get("schema_version") or 0) != 1:
        errors.append(f"{candidate_id}: evidence.schema_version must be 1")
    try:
        _timestamp(
            evidence.get("generated_at"),
            label=f"{candidate_id}.evidence.generated_at",
        )
    except ValueError as exc:
        errors.append(str(exc))

    gate_evidence = evidence.get("gate_evidence")
    if not isinstance(gate_evidence, dict):
        errors.append(f"{candidate_id}: evidence.gate_evidence must be an object")
    else:
        missing = sorted(REQUIRED_GATES - set(gate_evidence))
        if missing:
            errors.append(
                f"{candidate_id}: evidence missing gate artifacts " + ", ".join(missing)
            )
        for gate in sorted(REQUIRED_GATES & set(gate_evidence)):
            errors.extend(
                _artifact_errors(
                    gate_evidence[gate],
                    label=f"{candidate_id}.evidence.gate_evidence.{gate}",
                )
            )

    observation = evidence.get("runtime_observation")
    if not isinstance(observation, dict):
        errors.append(f"{candidate_id}: evidence.runtime_observation must be an object")
        return errors
    metric = str(observation.get("metric") or "").strip()
    expected_metric = str(candidate.get("telemetry_metric") or "").strip()
    if metric != expected_metric:
        errors.append(
            f"{candidate_id}: runtime observation metric must be "
            f"{expected_metric or '<configured>'}"
        )
    windows = observation.get("windows")
    if not isinstance(windows, list) or len(windows) < 2:
        errors.append(
            f"{candidate_id}: runtime observation requires at least two release-cycle windows"
        )
        return errors

    parsed_windows: list[tuple[datetime, datetime, str]] = []
    release_cycles: set[str] = set()
    for index, window in enumerate(windows, start=1):
        label = f"{candidate_id}.evidence.runtime_observation.windows[{index}]"
        if not isinstance(window, dict):
            errors.append(f"{label} must be an object")
            continue
        release_cycle = str(window.get("release_cycle") or "").strip()
        if not release_cycle:
            errors.append(f"{label}.release_cycle is required")
        release_cycles.add(release_cycle)
        try:
            started_at = _timestamp(window.get("started_at"), label=f"{label}.started_at")
            ended_at = _timestamp(window.get("ended_at"), label=f"{label}.ended_at")
            if ended_at <= started_at:
                errors.append(f"{label}.ended_at must be after started_at")
            parsed_windows.append((started_at, ended_at, release_cycle))
        except ValueError as exc:
            errors.append(str(exc))
        if window.get("call_count") != 0:
            errors.append(f"{label}.call_count must be 0")
        errors.extend(_artifact_errors(window, label=label))

    if "" in release_cycles:
        release_cycles.discard("")
    if len(release_cycles) < 2:
        errors.append(
            f"{candidate_id}: runtime observation must span two distinct release cycles"
        )
    ordered = sorted(parsed_windows, key=lambda item: item[0])
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] < previous[1]:
            errors.append(
                f"{candidate_id}: runtime observation windows must not overlap"
            )
            break
    return errors


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
        if not str(raw.get("telemetry_metric") or "").strip():
            errors.append(f"{candidate_id}: telemetry_metric is required")
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
            else:
                errors.extend(_removal_evidence_errors(candidate_id, raw))
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

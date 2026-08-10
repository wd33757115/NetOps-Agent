#!/usr/bin/env python3
"""Export immutable zero-traffic evidence from the legacy observation ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from scripts.deprecations.audit_deprecations import DEFAULT_REGISTRY, _timestamp
from src.observability.deprecations import LegacyPathObservationRepository


def _window(value: str) -> dict[str, Any]:
    try:
        release_cycle, interval = value.split("=", 1)
        started_raw, ended_raw = interval.split("/", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "window must use RELEASE=START/END"
        ) from exc
    try:
        started_at = _timestamp(started_raw, label="window.started_at")
        ended_at = _timestamp(ended_raw, label="window.ended_at")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if ended_at <= started_at:
        raise argparse.ArgumentTypeError("window end must be after start")
    return {
        "release_cycle": release_cycle.strip(),
        "started_at": started_at,
        "ended_at": ended_at,
    }


def build_report(
    *,
    candidate: dict[str, Any],
    windows: list[dict[str, Any]],
    repository: LegacyPathObservationRepository,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    candidate_id = str(candidate.get("id") or "").strip()
    if not candidate_id:
        raise ValueError("candidate id is required")
    release_cycles = {str(item.get("release_cycle") or "").strip() for item in windows}
    if len(windows) < 2 or len(release_cycles - {""}) < 2:
        raise ValueError("at least two distinct release-cycle windows are required")
    ordered = sorted(windows, key=lambda item: item["started_at"])
    for previous, current in zip(ordered, ordered[1:]):
        if current["started_at"] < previous["ended_at"]:
            raise ValueError("release-cycle windows must not overlap")

    results = []
    for item in ordered:
        started_at = item["started_at"]
        ended_at = item["ended_at"]
        results.append(
            {
                "release_cycle": str(item["release_cycle"]),
                "started_at": started_at.isoformat(),
                "ended_at": ended_at.isoformat(),
                "call_count": repository.count_window(
                    candidate_id=candidate_id,
                    started_at=started_at,
                    ended_at=ended_at,
                ),
            }
        )
    return {
        "schema_version": 1,
        "candidate_id": candidate_id,
        "metric": str(candidate.get("telemetry_metric") or ""),
        "generated_at": (generated_at or datetime.now(timezone.utc)).isoformat(),
        "windows": results,
        "runtime_traffic_zero": all(item["call_count"] == 0 for item in results),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--window", action="append", type=_window, required=True)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    registry = yaml.safe_load(args.registry.read_text(encoding="utf-8")) or {}
    candidate = next(
        (
            item
            for item in registry.get("candidates") or []
            if str(item.get("id") or "") == args.candidate
        ),
        None,
    )
    if candidate is None:
        parser.error(f"unknown deprecation candidate: {args.candidate}")
    try:
        report = build_report(
            candidate=candidate,
            windows=args.window,
            repository=LegacyPathObservationRepository(),
        )
    except ValueError as exc:
        parser.error(str(exc))
    content = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ).encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "sha256": digest,
                "runtime_traffic_zero": report["runtime_traffic_zero"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["runtime_traffic_zero"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

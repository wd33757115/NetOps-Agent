#!/usr/bin/env python3
"""Estimate patrol capacity baselines for concurrency / queue sizing."""

from __future__ import annotations

import argparse
import math


def estimate(
    *,
    devices: int,
    avg_commands: int = 5,
    avg_seconds_per_command: float = 3.0,
    target_window_minutes: int = 60,
    worker_count: int = 4,
) -> dict:
    device_seconds = devices * avg_commands * avg_seconds_per_command
    window_seconds = max(60, target_window_minutes * 60)
    required_concurrency = math.ceil(device_seconds / window_seconds)
    per_worker = math.ceil(required_concurrency / max(1, worker_count))
    return {
        "devices": devices,
        "device_seconds": device_seconds,
        "target_window_seconds": window_seconds,
        "recommended_patrol_max_concurrency": required_concurrency,
        "recommended_per_worker_concurrency": per_worker,
        "worker_count": worker_count,
        "notes": [
            "Assumes SSH serial commands per device and no nested virt overhead.",
            "Increase wait/lease seconds if device RTT > avg_seconds_per_command.",
            "Keep PATROL_CONCURRENCY_FAIL_CLOSED=true in production.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devices", type=int, default=0)
    parser.add_argument("--avg-commands", type=int, default=5)
    parser.add_argument("--avg-seconds-per-command", type=float, default=3.0)
    parser.add_argument("--window-minutes", type=int, default=60)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="Print 1000/2000 device baseline recommendations",
    )
    args = parser.parse_args()
    if args.baseline or not args.devices:
        for devices in (1000, 2000):
            print(
                estimate(
                    devices=devices,
                    avg_commands=args.avg_commands,
                    avg_seconds_per_command=args.avg_seconds_per_command,
                    target_window_minutes=args.window_minutes,
                    worker_count=args.workers,
                )
            )
        return 0
    result = estimate(
        devices=args.devices,
        avg_commands=args.avg_commands,
        avg_seconds_per_command=args.avg_seconds_per_command,
        target_window_minutes=args.window_minutes,
        worker_count=args.workers,
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Production impact scoring for parser work items and coverage KPIs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func

from src.infrastructure.db.models import (
    CommandSnapshot,
    NetworkDevice,
    ParserTemplate,
    ParserTemplateJob,
)
from src.infrastructure.db.postgres import get_db_session


def production_impact_for_pairs(
    pairs: set[tuple[str, str]],
    *,
    lookback_days: int = 30,
) -> dict[tuple[str, str], int]:
    """Score (vendor, command) pairs by device bindings × recent template_missing."""
    if not pairs:
        return {}
    since = datetime.now(timezone.utc) - timedelta(days=max(1, lookback_days))
    with get_db_session() as db:
        scores: dict[tuple[str, str], int] = {}
        for vendor, command in pairs:
            missing = (
                db.query(func.count(CommandSnapshot.id))
                .filter(
                    func.lower(CommandSnapshot.vendor) == vendor,
                    func.lower(CommandSnapshot.canonical_command) == command,
                    CommandSnapshot.parse_status == "template_missing",
                    CommandSnapshot.observed_at >= since,
                )
                .scalar()
            )
            devices = (
                db.query(func.count(func.distinct(NetworkDevice.id)))
                .filter(func.lower(NetworkDevice.vendor) == vendor)
                .scalar()
            )
            scores[(vendor, command)] = max(0, int(devices or 0)) * max(1, int(missing or 0))
    return scores


def production_impact_for_jobs(
    jobs: list[ParserTemplateJob],
    *,
    lookback_days: int = 30,
) -> dict[str, int]:
    """Score open parser jobs by device bindings × recent template_missing."""
    if not jobs:
        return {}
    keys = {
        (
            str(job.vendor or "").strip().lower(),
            str(job.canonical_command or "").strip().lower(),
        )
        for job in jobs
    }
    pair_scores = production_impact_for_pairs(keys, lookback_days=lookback_days)
    scores: dict[str, int] = {}
    for job in jobs:
        key = (
            str(job.vendor or "").strip().lower(),
            str(job.canonical_command or "").strip().lower(),
        )
        scores[str(job.id)] = pair_scores.get(key, 0)
    return scores


def effective_plan_coverage_summary() -> dict[str, Any]:
    """Ready rate for active production templates covering inventory vendors."""
    with get_db_session() as db:
        vendors = {
            str(row[0]).strip().lower()
            for row in db.query(NetworkDevice.vendor)
            .filter(NetworkDevice.vendor.isnot(None))
            .distinct()
            .all()
            if str(row[0] or "").strip()
        }
        templates = (
            db.query(ParserTemplate)
            .filter(ParserTemplate.status == "active")
            .all()
        )
        ready_commands = {
            (
                str(item.vendor or "").strip().lower(),
                str(item.canonical_command or "").strip().lower(),
            )
            for item in templates
            if str(item.vendor or "").strip() and str(item.canonical_command or "").strip()
        }
        # Approximate "effective plan commands" as distinct snapshot commands
        # seen for inventory vendors in the last 30 days.
        since = datetime.now(timezone.utc) - timedelta(days=30)
        plan_commands = (
            db.query(
                CommandSnapshot.vendor,
                CommandSnapshot.canonical_command,
            )
            .filter(
                CommandSnapshot.observed_at >= since,
                CommandSnapshot.vendor.isnot(None),
            )
            .distinct()
            .all()
        )
        eligible = []
        ready = []
        for vendor, command in plan_commands:
            vendor_key = str(vendor or "").strip().lower()
            command_key = str(command or "").strip().lower()
            if not vendor_key or not command_key:
                continue
            if vendors and vendor_key not in vendors:
                continue
            eligible.append((vendor_key, command_key))
            if (vendor_key, command_key) in ready_commands:
                ready.append((vendor_key, command_key))
        eligible_count = len(set(eligible))
        ready_count = len(set(ready))
        return {
            "by_effective_plan": True,
            "eligible_command_count": eligible_count,
            "ready_command_count": ready_count,
            "ready_rate": (ready_count / eligible_count) if eligible_count else 1.0,
            "inventory_vendor_count": len(vendors),
            "active_template_count": len(templates),
        }

# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Discover template candidates from PostgreSQL parser-validation snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config_loader import CommandMapping

from src.core.parser.policy import ParserPolicy
from src.core.parser.repository import ParserMetadataRepository
from src.core.patrol.repository import PatrolRepository


@dataclass(frozen=True)
class TemplateCandidate:
    vendor: str
    model: str
    command: str
    sample_output: str
    source_table: str
    device_id: str


def discover_missing_template_candidates(
    patrol_db: Path | None,
    devices_db: Path | None,
    command_mappings: list[CommandMapping],
    *,
    vendor_filter: str | None = None,
    model_filter: str | None = None,
    command_filter: str | None = None,
) -> list[TemplateCandidate]:
    """Return one PostgreSQL validation sample for each unresolved parser profile."""
    del patrol_db, devices_db, command_mappings

    parser_repository = ParserMetadataRepository()
    policy = ParserPolicy(repository=parser_repository)
    snapshots = PatrolRepository().list_dataset_snapshots(mode="parser_validation")
    seen: set[tuple[str, str, str]] = set()
    candidates: list[TemplateCandidate] = []

    for snapshot in snapshots:
        if snapshot.parse_status not in {"template_missing", "parse_failed"}:
            continue
        vendor = str(snapshot.vendor or "").strip()
        model = str(snapshot.model or "").strip()
        if not vendor or not model:
            continue
        profile = parser_repository.find_device_profile(vendor, model)
        decision = policy.decide(
            vendor=vendor,
            model=model,
            family=profile.get("family") if profile else None,
            command=snapshot.command_canonical,
        )
        if not decision.enabled:
            continue
        if vendor_filter and vendor.lower() != vendor_filter.lower():
            continue
        if model_filter and model.lower() != model_filter.lower():
            continue
        if (
            command_filter
            and snapshot.command_canonical.strip().lower()
            != command_filter.strip().lower()
        ):
            continue

        key = (vendor.lower(), model.lower(), snapshot.command_canonical)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            TemplateCandidate(
                vendor=vendor,
                model=model,
                command=snapshot.command_canonical,
                sample_output=snapshot.raw_text,
                source_table="netops_command_snapshots",
                device_id=snapshot.device_id,
            )
        )

    return candidates

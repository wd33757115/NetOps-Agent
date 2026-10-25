"""Bootstrap and inspect parser discovery assets stored in PostgreSQL."""

from __future__ import annotations

import re
from typing import Any

from .repository import ParserMetadataRepository, ParserTemplateRepository

BUILTIN_ALIASES = {
    "display cpu-us": "display cpu",
    "display cpu-usage": "display cpu",
    "display ip int brief": "display ip interface brief",
    "display vrrp statis": "display vrrp statistics",
    "show env power": "show environment power",
    "show env fan": "show environment fan",
    "show env temp": "show environment temperature",
    "show env temperature": "show environment temperature",
    "show int status": "show interface status",
    "show ip int brief": "show ip interface brief",
    "show proc cpu": "show processes cpu",
    "show proc memory": "show processes memory",
}


def bootstrap_discovery_assets() -> dict[str, Any]:
    metadata_repository = ParserMetadataRepository()
    template_repository = ParserTemplateRepository()
    for alias, canonical in BUILTIN_ALIASES.items():
        metadata_repository.upsert_command_alias(
            alias=alias,
            canonical_command=canonical,
            confidence=1.0,
            evidence={"source": "builtin_abbreviation_migration"},
        )

    profiles = 0
    signatures = 0
    for template in template_repository.list_templates(status="active"):
        if not template.get("model"):
            continue
        metadata_repository.upsert_device_profile(
            vendor=template["vendor"],
            model=template["model"],
            family=template.get("family"),
            confidence=1.0,
            evidence={
                "source": "active_template_migration",
                "template_id": template["id"],
            },
        )
        profiles += 1
        if template.get("category") == "device_version":
            metadata_repository.register_identity_signature(
                vendor=template["vendor"],
                model=template["model"],
                family=template.get("family"),
                evidence_command=template["canonical_command"],
                pattern=rf"(?i)\b{re.escape(template['model'])}\b",
                confidence=1.0,
                source="active_template_migration",
            )
            signatures += 1
    return {
        "aliases": len(BUILTIN_ALIASES),
        "profiles": profiles,
        "identity_signatures": signatures,
    }

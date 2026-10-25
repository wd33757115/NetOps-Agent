# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Load TextFSM generator configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.apps.rule_governance.backend.parser.policy import SHARED_PARSER_CONFIG_ROOT, ParserPolicy, load_yaml

DEFAULT_MAPPING = SHARED_PARSER_CONFIG_ROOT / "command_policy.yaml"


@dataclass(frozen=True)
class CommandMapping:
    vendor: str
    model: str
    command: str
    category: str
    family: str | None = None


def load_command_mapping(path: Path | None = None) -> list[CommandMapping]:
    raw = load_yaml(path or DEFAULT_MAPPING)
    mappings: list[CommandMapping] = []
    for device_key, commands in raw.items():
        if not isinstance(commands, dict):
            continue
        parts = str(device_key).strip().split(None, 1)
        if len(parts) != 2:
            continue
        vendor, model = parts
        for command, meta in commands.items():
            if not isinstance(meta, dict):
                continue
            category = str(meta.get("category") or "").strip()
            if category:
                mappings.append(
                    CommandMapping(
                        vendor=vendor,
                        model=model,
                        command=normalize_command(str(command)),
                        category=category,
                        family=str(meta.get("family") or "").strip() or None,
                    )
                )
    if mappings:
        return mappings
    from src.apps.rule_governance.backend.parser.repository import ParserTemplateRepository

    seen: set[tuple[str, str, str]] = set()
    for template in ParserTemplateRepository().list_templates(status="active"):
        key = (
            str(template["vendor"]),
            str(template["profile"]),
            str(template["canonical_command"]),
        )
        if key in seen:
            continue
        seen.add(key)
        mappings.append(
            CommandMapping(
                vendor=key[0],
                model=key[1],
                command=key[2],
                category=str(template.get("category") or "record"),
                family=template.get("family"),
            )
        )
    return mappings


def normalize_command(command: str, aliases: dict[str, str] | None = None) -> str:
    policy = ParserPolicy()
    normalized = policy.normalize_command(command)
    return (aliases or {}).get(normalized, normalized)


def list_device_profiles(mappings: list[CommandMapping]) -> list[str]:
    return sorted({f"{item.vendor} {item.model}" for item in mappings})

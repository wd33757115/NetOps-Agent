"""Shared command normalization and structured parsing policy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SHARED_PARSER_CONFIG_ROOT = (
    Path(__file__).resolve().parents[2] / "skills" / "shared" / "parser-config"
)

DEFAULT_COMMAND_ALIASES = {
    "display cpu-us": "display cpu",
    "display cpu-usage": "display cpu",
    "display ip int brief": "display ip interface brief",
    "display ip interface": "display ip interface brief",
    "show env power": "show environment power",
    "show env fan": "show environment fan",
    "show env temperature": "show environment temperature",
    "show int status": "show interface status",
    "show ip int brief": "show ip interface brief",
    "show proc cpu": "show processes cpu",
    "show proc memory": "show processes memory",
}


@dataclass(frozen=True)
class CategorySpec:
    name: str
    entity_type: str
    primary_keys: tuple[str, ...]
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...]
    validators: dict[str, dict[str, Any]]
    allow_empty: bool = False
    empty_patterns: tuple[str, ...] = ()
    ignore_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyDecision:
    enabled: bool
    canonical_command: str
    handling: str
    category: str | None = None
    family: str | None = None
    spec: CategorySpec | None = None


def load_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise ValueError(f"configuration root must be an object: {path}")
    return value


class ParserPolicy:
    def __init__(
        self,
        *,
        policy_path: str | Path | None = None,
        repository=None,
    ):
        self.policy = load_yaml(
            policy_path or SHARED_PARSER_CONFIG_ROOT / "command_policy.yaml"
        )
        if repository is None:
            from .repository import ParserRepository

            repository = ParserRepository()
        self.repository = repository
        self.aliases = {
            self._basic_normalize(alias): self._basic_normalize(canonical)
            for alias, canonical in DEFAULT_COMMAND_ALIASES.items()
        }
        self.aliases.update({
            self._basic_normalize(item["alias"]): self._basic_normalize(
                item["canonical_command"]
            )
            for item in repository.list_command_aliases()
        })

    @staticmethod
    def _basic_normalize(command: str) -> str:
        normalized = " ".join(str(command or "").strip().lower().split())
        tokens = normalized.split()
        expansions = {
            "dis": "display",
            "int": "interface",
            "proc": "processes",
            "env": "environment",
            "mem": "memory",
            "ver": "version",
            "statis": "statistics",
        }
        return " ".join(expansions.get(token, token) for token in tokens)

    def normalize_command(self, command: str) -> str:
        normalized = self._basic_normalize(command)
        return self.aliases.get(normalized, normalized)

    def decide(
        self,
        *,
        vendor: str | None,
        model: str | None,
        family: str | None,
        command: str,
    ) -> PolicyDecision:
        canonical = self.normalize_command(command)
        commands = self.policy.get("commands") or self.policy.get("_global") or {}
        global_metadata = commands.get(canonical)
        if isinstance(global_metadata, dict):
            return PolicyDecision(
                enabled=bool(global_metadata.get("enabled", True)),
                canonical_command=canonical,
                handling=str(
                    global_metadata.get("handling")
                    or (
                        "structured"
                        if global_metadata.get("enabled", True)
                        else "raw_only"
                    )
                ),
            )
        return PolicyDecision(
            enabled=False,
            canonical_command=canonical,
            handling="raw_only",
        )

    def is_enabled(
        self,
        *,
        vendor: str | None,
        model: str | None,
        family: str | None = None,
        command: str,
    ) -> bool:
        return self.decide(
            vendor=vendor, model=model, family=family, command=command
        ).enabled

"""Shared command normalization and structured parsing policy."""

from __future__ import annotations

import re
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
    "show int transceiver detail": "show interface transceiver detail",
    "show interfaces transceiver detail": "show interface transceiver detail",
    "show ip int brief": "show ip interface brief",
    "show proc cpu": "show processes cpu",
    "show proc memory": "show processes memory",
    "display transceiver diagnosis": "display transceiver diagnosis interface",
    "display transceiver diagnosis int": "display transceiver diagnosis interface",
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
    capability: str | None = None
    capability_status: str = "unknown"
    template_scope: str = "model"
    template_profile: str | None = None
    empty_semantics: dict[str, Any] | None = None


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
        self.policy = load_yaml(policy_path or SHARED_PARSER_CONFIG_ROOT / "command_policy.yaml")
        if repository is None:
            from .repository import ParserMetadataRepository

            repository = ParserMetadataRepository()
        self.repository = repository
        self.aliases = {
            self._basic_normalize(alias): self._basic_normalize(canonical)
            for alias, canonical in DEFAULT_COMMAND_ALIASES.items()
        }
        self.aliases.update(
            {
                self._basic_normalize(item["alias"]): self._basic_normalize(
                    item["canonical_command"]
                )
                for item in repository.list_command_aliases()
            }
        )

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
            enabled = bool(global_metadata.get("enabled", True))
            capability = str(global_metadata.get("capability") or "").strip() or None
            capability_metadata = dict(
                (self.policy.get("capabilities") or {}).get(capability) or {}
            )
            template_scope = str(
                global_metadata.get("template_scope")
                or capability_metadata.get("template_scope")
                or "model"
            )
            if template_scope not in {"model", "family"}:
                raise ValueError(f"unsupported template_scope for {canonical}: {template_scope}")
            support_selectors = global_metadata.get("supported_on") or ()
            capability_status = self._capability_status(
                vendor=vendor,
                model=model,
                family=family,
                selectors=support_selectors,
            )
            empty_semantics = {
                **dict(capability_metadata.get("empty_semantics") or {}),
                **dict(global_metadata.get("empty_semantics") or {}),
            }
            return PolicyDecision(
                enabled=enabled,
                canonical_command=canonical,
                handling=str(
                    global_metadata.get("handling") or ("structured" if enabled else "raw_only")
                ),
                capability=capability,
                capability_status=capability_status,
                template_scope=template_scope,
                template_profile=(
                    family
                    if template_scope == "family"
                    and family
                    and family.casefold() not in {"auto-discovered", "unknown", "generic"}
                    else model
                ),
                empty_semantics=empty_semantics,
            )
        return PolicyDecision(
            enabled=False,
            canonical_command=canonical,
            handling="raw_only",
        )

    @staticmethod
    def _capability_status(
        *,
        vendor: str | None,
        model: str | None,
        family: str | None,
        selectors: Any,
    ) -> str:
        if not selectors:
            return "unknown"
        for selector in selectors:
            if not isinstance(selector, dict):
                continue
            expected_vendor = str(selector.get("vendor") or "").casefold()
            families = {str(item).casefold() for item in selector.get("families") or ()}
            models = {str(item).casefold() for item in selector.get("models") or ()}
            model_patterns = [str(item) for item in selector.get("model_patterns") or ()]
            if expected_vendor and expected_vendor != str(vendor or "").casefold():
                continue
            if families and str(family or "").casefold() not in families:
                continue
            if models and str(model or "").casefold() not in models:
                continue
            if model_patterns and not any(
                re.search(pattern, str(model or ""), re.IGNORECASE) for pattern in model_patterns
            ):
                continue
            return "supported"
        return "unknown"

    def is_enabled(
        self,
        *,
        vendor: str | None,
        model: str | None,
        family: str | None = None,
        command: str,
    ) -> bool:
        return self.decide(vendor=vendor, model=model, family=family, command=command).enabled

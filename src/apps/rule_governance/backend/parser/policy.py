"""Shared command normalization and structured parsing policy."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.apps.rule_governance.paths import RESOURCES

SHARED_PARSER_CONFIG_ROOT = RESOURCES / "parser-config"

DEFAULT_COMMAND_ALIASES = {
    "display cpu-us": "display cpu",
    "display cpu-usage": "display cpu",
    "display ip int brief": "display ip interface brief",
    "display ip interface": "display ip interface brief",
    "show env power": "show environment power",
    "show env fan": "show environment fan",
    "show env temperature": "show environment temperature",
    "show environment all": "show env all",
    "show int status": "show interface status",
    "show int transceiver detail": "show interface transceiver detail",
    "show interfaces transceiver detail": "show interface transceiver detail",
    "show ip int brief": "show ip interface brief",
    "show proc cpu": "show processes cpu",
    "show proc memory": "show processes memory",
    "display transceiver diagnosis": "display transceiver diagnosis interface",
    "display transceiver diagnosis int": "display transceiver diagnosis interface",
}

RULE_ASSET_MODES = frozenset(
    {"full_chain", "observation_only", "change_only", "raw_hash", "ignored"}
)


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
    presence: str = "required"
    # Validation-only defaults for optional primary-key fields.  The Parser
    # keeps the original source value in structured_data for Adapter Projection.
    primary_key_defaults: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyDecision:
    enabled: bool
    canonical_command: str
    handling: str
    asset_mode: str = "ignored"
    category: str | None = None
    family: str | None = None
    spec: CategorySpec | None = None
    capability: str | None = None
    capability_status: str = "unknown"
    template_scope: str = "model"
    template_profile: str | None = None
    empty_semantics: dict[str, Any] | None = None
    comparison_profile: str | None = None
    comparison_profile_version: int | None = None


def approved_template_may_serve(policy: Any, decision: PolicyDecision, command: str) -> bool:
    """Whether an approved template gets to parse this command's output.

    Commands the policy enables are parsed. A disabled command kept only as raw
    text, or one the policy says nothing about, is parsed once a template is
    approved for it. A command the policy explicitly sends to text comparison
    (a log window, a hash diff) never is, whatever templates exist.
    """

    return (
        decision.enabled
        or decision.handling == "raw_only"
        or (
            decision.handling == "raw_hash_diff"
            and isinstance(policy, ParserPolicy)
            and not policy.has_explicit_policy(command)
        )
    )


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
            comparison_profile, comparison_profile_version = self._comparison_profile(
                global_metadata.get("comparison_profile")
            )
            handling = str(
                global_metadata.get("handling") or ("structured" if enabled else "raw_only")
            )
            return PolicyDecision(
                enabled=enabled,
                canonical_command=canonical,
                handling=handling,
                asset_mode=self._asset_mode(handling, global_metadata.get("asset_mode")),
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
                comparison_profile=comparison_profile,
                comparison_profile_version=comparison_profile_version,
            )
        raw_rule = self._matching_unstructured_rule(canonical)
        if raw_rule is not None:
            comparison_profile, comparison_profile_version = self._comparison_profile(
                raw_rule.get("profile")
            )
            handling = str(raw_rule.get("handling") or "raw_hash_diff")
            return PolicyDecision(
                enabled=False,
                canonical_command=canonical,
                handling=handling,
                asset_mode=self._asset_mode(handling, raw_rule.get("asset_mode")),
                comparison_profile=comparison_profile,
                comparison_profile_version=comparison_profile_version,
            )
        return PolicyDecision(
            enabled=False,
            canonical_command=canonical,
            # A command without a structural policy is still useful patrol
            # evidence. Keep it out of Parser/contract generation, but retain
            # it for baseline-to-current text comparison in the report.
            handling="raw_hash_diff",
            asset_mode=self._asset_mode("raw_hash_diff"),
        )

    def has_explicit_policy(self, command: str) -> bool:
        canonical = self.normalize_command(command)
        commands = self.policy.get("commands") or self.policy.get("_global") or {}
        return canonical in commands or self._matching_unstructured_rule(canonical) is not None

    @staticmethod
    def _asset_mode(handling: str, configured: Any = None) -> str:
        defaults = {
            "structured": "full_chain",
            "raw_hash_diff": "raw_hash",
            "raw_only": "ignored",
            "not_applicable": "ignored",
        }
        mode = str(configured or defaults.get(handling, "ignored")).strip()
        if mode not in RULE_ASSET_MODES:
            raise ValueError(f"unsupported rule asset mode: {mode}")
        if mode in {"full_chain", "observation_only", "change_only"} and handling != "structured":
            raise ValueError(f"rule asset mode {mode} requires structured handling")
        if mode == "raw_hash" and handling != "raw_hash_diff":
            raise ValueError("rule asset mode raw_hash requires raw_hash_diff handling")
        if handling in {"raw_only", "not_applicable"} and mode != "ignored":
            raise ValueError(f"handling {handling} only supports ignored asset mode")
        return mode

    def _comparison_profile(self, value: Any) -> tuple[str | None, int | None]:
        name = str(value or "").strip()
        if not name:
            return None, None
        profiles = dict(
            (self.policy.get("unstructured_comparison") or {}).get("profiles") or {}
        )
        profile = profiles.get(name)
        if not isinstance(profile, dict):
            raise ValueError(f"unstructured comparison profile not found: {name}")
        return name, int(profile.get("version") or 1)

    def _matching_unstructured_rule(self, canonical: str) -> dict[str, Any] | None:
        rules = (self.policy.get("unstructured_comparison") or {}).get("rules") or []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            pattern = str(rule.get("command_pattern") or "").strip()
            if pattern and re.search(pattern, canonical, re.IGNORECASE):
                return dict(rule)
        return None

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

"""Versioned, vendor-neutral semantic hints for Parser Contract generation."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.apps.rule_governance.backend.parser.value_mapping_config import (
    BASE_SEMANTIC_CATALOG_PATH,
    resolve_scoped_schema_field,
)

CATALOG_PATH = BASE_SEMANTIC_CATALOG_PATH


@dataclass(frozen=True)
class SemanticCatalogMatch:
    profile_id: str
    contract: dict[str, Any]
    semantic_anchor_fields: tuple[str, ...]
    score: int
    catalog_version: str
    catalog_hash: str
    matched_command_pattern: str
    matched_sample_patterns: tuple[str, ...]

    def evidence(self) -> dict[str, Any]:
        return {
            "catalog_profile": self.profile_id,
            "catalog_version": self.catalog_version,
            "catalog_hash": self.catalog_hash,
            "matched_command_pattern": self.matched_command_pattern,
            "matched_sample_patterns": list(self.matched_sample_patterns),
        }


@lru_cache(maxsize=4)
def load_semantic_catalog(path: str | Path = CATALOG_PATH) -> dict[str, Any]:
    target = Path(path)
    content = target.read_bytes()
    payload = yaml.safe_load(content.decode("utf-8")) or {}
    if payload.get("catalog_version") != "parser-semantic-catalog-v1":
        raise ValueError(f"unsupported parser semantic catalog: {target}")
    if not isinstance(payload.get("contract_profiles"), list):
        raise ValueError("parser semantic catalog has no contract_profiles list")
    effective_content = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload["catalog_hash"] = hashlib.sha256(effective_content).hexdigest()
    return payload


def _matching_patterns(patterns: list[str], text: str) -> tuple[str, ...]:
    return tuple(pattern for pattern in patterns if re.search(pattern, text))


def resolve_semantic_contract(
    *,
    command: str,
    samples_text: str,
    catalog_path: str | Path = CATALOG_PATH,
) -> SemanticCatalogMatch | None:
    """Resolve the strongest semantic profile without using vendor or model."""

    catalog = load_semantic_catalog(catalog_path)
    normalized_command = " ".join(str(command or "").strip().casefold().split())
    samples = str(samples_text or "")
    candidates: list[SemanticCatalogMatch] = []
    for raw_profile in catalog["contract_profiles"]:
        profile = dict(raw_profile or {})
        match = dict(profile.get("match") or {})
        command_patterns = [str(item) for item in match.get("command_any") or []]
        command_hits = _matching_patterns(command_patterns, normalized_command)
        if command_patterns and not command_hits:
            continue

        sample_all = [str(item) for item in match.get("sample_all") or []]
        sample_any = [str(item) for item in match.get("sample_any") or []]
        sample_none = [str(item) for item in match.get("sample_none") or []]
        all_hits = _matching_patterns(sample_all, samples)
        any_hits = _matching_patterns(sample_any, samples)
        none_hits = _matching_patterns(sample_none, samples)
        if len(all_hits) != len(sample_all):
            continue
        if sample_any and not any_hits:
            continue
        if none_hits:
            continue

        priority = int(profile.get("priority") or 0)
        score = priority + 10 * len(command_hits) + 4 * len(all_hits) + len(any_hits)
        contract = deepcopy(dict(profile.get("contract") or {}))
        if not contract.get("required_fields"):
            raise ValueError(f"semantic catalog profile {profile.get('id')} has no required_fields")
        candidates.append(
            SemanticCatalogMatch(
                profile_id=str(profile.get("id") or "unknown"),
                contract=contract,
                semantic_anchor_fields=tuple(
                    str(item) for item in profile.get("semantic_anchor_fields") or ()
                ),
                score=score,
                catalog_version=str(catalog["catalog_version"]),
                catalog_hash=str(catalog["catalog_hash"]),
                matched_command_pattern=command_hits[0] if command_hits else "",
                matched_sample_patterns=tuple([*all_hits, *any_hits]),
            )
        )
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.score, item.profile_id))


def field_semantics(
    field_name: str,
    *,
    catalog_path: str | Path = CATALOG_PATH,
) -> dict[str, Any] | None:
    catalog = load_semantic_catalog(catalog_path)
    field_catalog = dict(catalog.get("field_semantics") or {})
    normalized = str(field_name or "").strip().casefold()
    exact = dict(field_catalog.get("exact") or {})
    if normalized in exact:
        return deepcopy(dict(exact[normalized] or {}))
    for item in field_catalog.get("patterns") or []:
        pattern = str((item or {}).get("pattern") or "")
        match = re.search(pattern, normalized)
        if not match:
            continue
        value = json.loads(json.dumps(dict((item or {}).get("semantics") or {})))
        for key, raw in tuple(value.items()):
            if isinstance(raw, str):
                value[key] = raw.format(*match.groups(), **match.groupdict())
        return value
    canonical = _canonical_field_for_name(normalized, catalog=catalog)
    if canonical and canonical != normalized:
        inherited = field_semantics(canonical, catalog_path=catalog_path)
        if inherited:
            inherited = deepcopy(inherited)
            # Composite field names may safely inherit value semantics, but a
            # canonical applicability predicate references canonical sibling
            # fields that need not exist in the generated Contract. Keeping it
            # would silently reject otherwise valid observations downstream.
            inherited.pop("applicability", None)
            if re.search(r"(?:^|_)(?:alarm|warning|threshold|high|low)(?:_|$)", normalized):
                inherited.pop("observation_type", None)
            return inherited
    return None


def rebound_field_applicability(
    field_name: str,
    *,
    field_inventory: list[str] | tuple[str, ...] | set[str],
    catalog_path: str | Path = CATALOG_PATH,
) -> dict[str, Any] | None:
    """Bind canonical applicability predicates to an approved Parser schema.

    Generated schemas often qualify canonical names (for example, a lane-level
    measurement may add a ``channel_`` prefix). Applicability remains portable
    only when every field reference in an exclusion branch resolves uniquely in
    the current schema. Unresolved branches are dropped, which can only reduce
    suppression and therefore fails toward observation rather than silence.
    """

    catalog = load_semantic_catalog(catalog_path)
    normalized = str(field_name or "").strip().casefold()
    canonical = _canonical_field_for_name(normalized, catalog=catalog) or normalized
    exact = dict((catalog.get("field_semantics") or {}).get("exact") or {})
    source = dict(exact.get(canonical) or {})
    return rebind_applicability(
        source.get("applicability"),
        field_inventory=field_inventory,
        catalog_path=catalog_path,
    )


def rebind_applicability(
    applicability: Any,
    *,
    field_inventory: list[str] | tuple[str, ...] | set[str],
    catalog_path: str | Path = CATALOG_PATH,
) -> dict[str, Any] | None:
    """Compile applicability references against one approved Parser schema.

    Each exclusion is independent. A branch that cannot bind all referenced
    fields is removed instead of leaving an invalid or silently rejecting guard.
    """

    if not isinstance(applicability, dict):
        return None
    catalog = load_semantic_catalog(catalog_path)
    inventory = [str(item) for item in field_inventory if str(item).strip()]

    def resolve(reference: str) -> str | None:
        target = str(reference or "").strip().casefold()
        matches = [
            item
            for item in inventory
            if item.casefold() == target
            or _canonical_field_for_name(item, catalog=catalog) == target
        ]
        return matches[0] if len(matches) == 1 else None

    def bind_predicate(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        output = deepcopy(value)
        for key in ("all", "any"):
            if key not in output:
                continue
            nested = [bind_predicate(item) for item in output.get(key) or []]
            if not nested or any(item is None for item in nested):
                return None
            output[key] = nested
            return output
        if "not" in output:
            nested = bind_predicate(output.get("not"))
            if nested is None:
                return None
            output["not"] = nested
            return output
        for key in ("field", "other_field"):
            if key not in output:
                continue
            resolved = resolve(str(output.get(key) or ""))
            if resolved is None:
                return None
            output[key] = resolved
        return output

    exclusions = []
    for raw in applicability.get("exclude_when") or []:
        if not isinstance(raw, dict):
            continue
        predicate = bind_predicate(raw.get("when"))
        if predicate is None:
            continue
        exclusions.append({**deepcopy(raw), "when": predicate})
    if not exclusions:
        return None
    return {
        **deepcopy(applicability),
        "exclude_when": exclusions,
    }


def _normalize_field_label(value: str) -> str:
    text = re.sub(r"\([^)]*\)", "", str(value or "").casefold())
    return re.sub(r"[^a-z0-9]+", "", text)


def _canonical_field_for_name(
    field_name: str,
    *,
    catalog: dict[str, Any],
) -> str | None:
    raw_tokens = [
        token
        for token in re.split(r"[^a-z0-9]+", str(field_name or "").casefold())
        if token
    ]
    unit_tokens = {
        "amp",
        "amps",
        "byte",
        "bytes",
        "celsius",
        "dbm",
        "degree",
        "degrees",
        "fahrenheit",
        "gbps",
        "ma",
        "mbps",
        "milliamp",
        "milliamps",
        "percent",
        "percentage",
        "v",
        "volt",
        "volts",
    }
    qualifier_tokens = {
        "id",
        "identifier",
        "label",
        "name",
        "state",
        "status",
        "value",
    }
    semantic_tokens = list(raw_tokens)
    while len(semantic_tokens) > 1 and semantic_tokens[-1] in {
        *unit_tokens,
        *qualifier_tokens,
    }:
        semantic_tokens.pop()
    targets = {
        _normalize_field_label(field_name),
        "".join(semantic_tokens),
    }
    candidates: list[tuple[int, str]] = []
    for canonical, values in dict(catalog.get("field_aliases") or {}).items():
        for raw in (canonical, *(values or [])):
            alias = _normalize_field_label(str(raw))
            if len(alias) >= 3 and any(target.endswith(alias) for target in targets):
                candidates.append((len(alias), str(canonical)))
    if not candidates:
        return None
    width = max(length for length, _canonical in candidates)
    winners = {canonical for length, canonical in candidates if length == width}
    return next(iter(winners)) if len(winners) == 1 else None


def field_aliases(
    field_name: str,
    *,
    catalog_path: str | Path = CATALOG_PATH,
) -> tuple[str, ...]:
    catalog = load_semantic_catalog(catalog_path)
    aliases = dict(catalog.get("field_aliases") or {})
    normalized = str(field_name or "").strip().casefold()
    canonical = normalized if normalized in aliases else _canonical_field_for_name(
        normalized,
        catalog=catalog,
    )
    return tuple(str(item) for item in aliases.get(canonical) or ())


def canonical_field_for_label(
    label: str,
    *,
    catalog_path: str | Path = CATALOG_PATH,
) -> str | None:
    """Resolve an exact governed label alias without command or vendor logic."""

    catalog = load_semantic_catalog(catalog_path)
    aliases = dict(catalog.get("field_aliases") or {})

    target = _normalize_field_label(label)
    if not target:
        return None
    matches = {
        str(field)
        for field, values in aliases.items()
        if target
        in {
            _normalize_field_label(str(field)),
            *(_normalize_field_label(str(value)) for value in values or []),
        }
    }
    return next(iter(matches)) if len(matches) == 1 else None


def canonical_field_for_name(
    field_name: str,
    *,
    catalog_path: str | Path = CATALOG_PATH,
) -> str | None:
    """Resolve a governed field from a qualified machine identifier."""

    catalog = load_semantic_catalog(catalog_path)
    return _canonical_field_for_name(field_name, catalog=catalog)


def canonical_schema_field(
    field_name: str,
    *,
    check_item: str | None = None,
    vendor: str | None = None,
    model: str | None = None,
    command: str | None = None,
    catalog_path: str | Path = CATALOG_PATH,
) -> str | None:
    """Resolve only an explicitly governed machine-field alias.

    This stricter resolver is used when renaming a generated schema. The broad
    suffix resolver remains appropriate for inheriting semantics, but is too
    permissive for changing public field names such as ``serial_number``.
    """

    if Path(catalog_path).resolve() == CATALOG_PATH.resolve():
        scoped = resolve_scoped_schema_field(
            field_name,
            check_item=check_item,
            vendor=vendor,
            model=model,
            command=command,
        )
        if scoped:
            return scoped
    catalog = load_semantic_catalog(catalog_path)
    target = str(field_name or "").strip().casefold()
    aliases_by_field = dict(catalog.get("schema_field_aliases") or {})
    if target in aliases_by_field:
        return target
    return None


def schema_field_aliases(
    field_name: str,
    *,
    catalog_path: str | Path = CATALOG_PATH,
) -> tuple[str, ...]:
    """Return exact machine-field aliases for one governed Parser field.

    ``field_aliases`` includes human-readable CLI labels and is useful for
    interpreting headers.  This narrower list is safe to expose as suggested
    TextFSM ``Value`` identifiers because every alias is explicitly governed.
    """

    catalog = load_semantic_catalog(catalog_path)
    aliases = dict(catalog.get("schema_field_aliases") or {})
    normalized = str(field_name or "").strip().casefold()
    return tuple(str(item) for item in aliases.get(normalized) or ())

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

CATALOG_PATH = Path(__file__).with_name("assets") / "contract_semantics.v1.yaml"


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
    payload["catalog_hash"] = hashlib.sha256(content).hexdigest()
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

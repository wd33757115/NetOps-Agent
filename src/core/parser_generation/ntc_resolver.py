"""Resolve immutable ntc_templates candidates with objective execution evidence."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from importlib.metadata import metadata, version
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml
from textfsm import clitable

from src.core.parser.semantic_catalog import (
    field_aliases as governed_field_aliases,
    resolve_semantic_contract,
    schema_field_aliases,
)

from .samples import ParserSample
from .service import TemplateCandidate
from .spec import ParserSpec, ParserSpecDocument
from .validation import TextFSMValidationBackend, execute_template_candidate

DEFAULT_PLATFORM_MAP = Path(__file__).parent / "assets" / "ntc_platforms.v1.yaml"
NTC_COMPAT_TRANSFORMATION_VERSION = "ntc-platform-compat/v2"
NTC_CONTRACT_TRANSFORMATION_VERSION = "ntc-platform-compat/v4"
NTC_SCHEMA_PROJECTION_TRANSFORMATION_VERSION = "ntc-platform-compat/v5"
_NTC_CATCH_ALL_ERROR_RE = re.compile(
    r"^(?P<indent>\s*)(?P<pattern>\^(?:\.|\.\*|\.\+)(?:\$\$)?)"
    r"\s*->\s*Error(?:\s+.*)?$"
)
_TEXTFSM_VALUE_RE = re.compile(
    r"^(?P<prefix>\s*Value\s+)"
    r"(?:(?P<options>(?:Filldown|Fillup|Key|List|Required)"
    r"(?:\s*,\s*(?:Filldown|Fillup|Key|List|Required))*)\s+)?"
    r"(?P<field>[A-Za-z_]\w*)"
    r"(?P<suffix>\s+\(.*)$"
)
_TEXTFSM_FIELD_REFERENCE_RE = re.compile(r"\$\{(?P<field>[A-Za-z_]\w*)\}")
_TEXTFSM_RECORD_ACTION_RE = re.compile(
    r"(?P<body>.*?)\s*->\s*(?:(?P<line_op>Next|Continue)\.)?Record"
    r"(?:\s+(?P<state>[A-Za-z_]\w*))?\s*$"
)
_FIELD_LABEL_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_COMMAND_TOKEN_RE = re.compile(r"[a-z0-9]+")
_FIELD_ALIAS_QUALIFIERS = {
    "addr",
    "address",
    "descr",
    "description",
    "id",
    "identifier",
    "name",
    "num",
    "number",
    "state",
    "status",
    "type",
    "value",
}


@dataclass(frozen=True)
class NtcPlatformDecision:
    status: str
    platforms: tuple[str, ...]
    mapping_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "platforms": list(self.platforms),
            "mapping_ids": list(self.mapping_ids),
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class NtcResolution:
    status: str
    platform: str | None
    command: str
    template_name: str | None
    candidate: TemplateCandidate | None
    execution_evidence: dict[str, Any]
    error: str | None = None

    def to_dict(self, *, include_template: bool = False) -> dict[str, Any]:
        result = {
            "status": self.status,
            "platform": self.platform,
            "command": self.command,
            "template_name": self.template_name,
            "provider": self.candidate.provider if self.candidate else None,
            "template_sha256": self.candidate.sha256 if self.candidate else None,
            "provenance": dict(self.candidate.provenance or {}) if self.candidate else {},
            "execution_evidence": dict(self.execution_evidence),
            "error": self.error,
        }
        if include_template and self.candidate:
            result["template_text"] = self.candidate.template_text
        return result


def _normalized(value: Any) -> str:
    return str(value or "").strip().casefold()


def _device_value(device: dict[str, Any], key: str) -> Any:
    if key in device:
        return device.get(key)
    attributes = device.get("attributes") or device.get("attributes_json") or {}
    return attributes.get(key) if isinstance(attributes, dict) else None


def _stable_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _field_profiles(report: Any, *, limit: int = 8) -> dict[str, dict[str, Any]]:
    """Summarize sanitized parsed values for ParserSpec semantic guidance."""

    values_by_field: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    shape_counts: dict[str, dict[str, int]] = {}
    shape_examples: dict[str, dict[str, str]] = {}

    def value_shape(value: str) -> str:
        if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value) is not None:
            return "number"
        if re.fullmatch(r"[-+]?\d+(?:\.\d+)?%", value) is not None:
            return "percent"
        return "text"

    for sample in report.samples:
        for record in sample.records:
            for field, raw_value in record.items():
                items = raw_value if isinstance(raw_value, (list, tuple)) else (raw_value,)
                for item in items:
                    value = str(item or "").strip()
                    if not value:
                        continue
                    field_name = str(field)
                    counts[field_name] = counts.get(field_name, 0) + 1
                    shape = value_shape(value)
                    field_shape_counts = shape_counts.setdefault(field_name, {})
                    field_shape_counts[shape] = field_shape_counts.get(shape, 0) + 1
                    shape_examples.setdefault(field_name, {}).setdefault(
                        shape,
                        value[:160],
                    )
                    examples = values_by_field.setdefault(field_name, [])
                    if value not in examples and len(examples) < limit:
                        examples.append(value[:160])
    profiles: dict[str, dict[str, Any]] = {}
    for field, observed_examples in sorted(values_by_field.items()):
        field_shape_counts = shape_counts.get(field, {})
        total_count = counts.get(field, 0)
        all_numbers = bool(total_count) and field_shape_counts.get("number", 0) == total_count
        all_percent = bool(total_count) and field_shape_counts.get("percent", 0) == total_count

        # Keep the first observed value, then surface every additional shape
        # before the remaining examples. This makes late sentinels such as an
        # unavailable VLAN represented by "-" visible to ParserSpec review.
        examples: list[str] = []
        if observed_examples:
            examples.append(observed_examples[0])
        for representative in shape_examples.get(field, {}).values():
            if representative not in examples:
                examples.append(representative)
        for value in observed_examples:
            if value not in examples:
                examples.append(value)
            if len(examples) >= limit:
                break
        profiles[field] = {
            "examples": examples[:limit],
            "observed_non_empty_count": total_count,
            "value_shape": (
                "number" if all_numbers else "percent" if all_percent else "text"
            ),
            "value_shape_counts": dict(sorted(field_shape_counts.items())),
        }
    return profiles


def _field_label_token(value: Any) -> str:
    return _FIELD_LABEL_TOKEN_RE.sub("", str(value or "").strip().casefold())


def _parser_spec_record_sets(
    spec: ParserSpec | ParserSpecDocument | None,
) -> tuple[ParserSpec, ...]:
    if spec is None:
        return ()
    if isinstance(spec, ParserSpecDocument):
        return spec.record_sets
    return (spec,)


def _unambiguous_aliases(
    candidates: dict[str, set[str]],
) -> dict[str, str]:
    return {
        token: next(iter(targets))
        for token, targets in candidates.items()
        if token and len(targets) == 1
    }


def _semantic_profile_field_aliases(
    *,
    command: str,
    samples: tuple[ParserSample, ...],
) -> tuple[dict[str, str], str | None]:
    """Build command-scoped aliases from the vendor-neutral semantic catalog."""

    match = resolve_semantic_contract(
        command=command,
        samples_text="\n\n".join(sample.validation_text for sample in samples),
    )
    if match is None:
        return {}, None
    contract = dict(match.contract or {})
    fields = tuple(
        dict.fromkeys(
            str(item).strip()
            for item in (
                *(contract.get("required_fields") or ()),
                *(contract.get("optional_fields") or ()),
            )
            if str(item).strip()
        )
    )
    candidates: dict[str, set[str]] = {}
    for field in fields:
        for label in (
            field,
            *governed_field_aliases(field),
            *schema_field_aliases(field),
        ):
            token = _field_label_token(label)
            if token:
                candidates.setdefault(token, set()).add(field)
    return _unambiguous_aliases(candidates), match.profile_id


def _parser_spec_field_aliases(
    spec: ParserSpec | ParserSpecDocument | None,
    *,
    semantic_aliases: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build unambiguous aliases from reviewed and governed field semantics.

    NTC uses source-oriented names such as ``DESCR`` and ``SN``. ParserSpec
    labels remain the strongest evidence, while the command-scoped semantic
    catalog keeps equivalent fields stable across vendors and models. Any
    ambiguous alias is left untouched rather than guessed.
    """

    record_sets = _parser_spec_record_sets(spec)
    governed = dict(semantic_aliases or {})
    candidates: dict[str, set[str]] = {}
    for record_set in record_sets:
        for field in record_set.fields:
            for label in (field.name, *field.source_labels):
                token = _field_label_token(label)
                if token:
                    candidates.setdefault(token, set()).add(field.name)
    aliases = _unambiguous_aliases(candidates)
    for token, target in governed.items():
        if token in aliases:
            continue
        explicitly_claimed = any(
            token.startswith(alias_token)
            and token[len(alias_token) :] in _FIELD_ALIAS_QUALIFIERS
            for alias_token in aliases
        )
        if not explicitly_claimed:
            aliases[token] = target
    return aliases


def _command_tokens(value: str) -> tuple[str, ...]:
    return tuple(_COMMAND_TOKEN_RE.findall(str(value or "").casefold()))


def _token_compatible(left: str, right: str) -> bool:
    return left == right or (
        min(len(left), len(right)) >= 3
        and (left.startswith(right) or right.startswith(left))
    )


def _template_command_compatible(
    *,
    template_name: str,
    platform: str,
    command: str,
) -> bool:
    """Reject prefix-only NTC matches that point at another command."""

    stem = Path(str(template_name)).stem.casefold()
    prefix = f"{str(platform).strip().casefold()}_"
    if stem.startswith(prefix):
        stem = stem[len(prefix) :]
    template_tokens = tuple(
        token
        for token in _command_tokens(stem)
        if token not in {"show", "display"}
    )
    requested_tokens = tuple(
        token
        for token in _command_tokens(command)
        if token not in {"show", "display"}
    )
    return bool(template_tokens) and all(
        any(
            _token_compatible(template_token, requested)
            for requested in requested_tokens
        )
        for template_token in template_tokens
    )


def _resolve_field_alias(
    source_name: str,
    aliases: dict[str, str],
) -> str | None:
    source_token = _field_label_token(source_name)
    exact = aliases.get(source_token)
    if exact is not None:
        return exact
    candidates: set[str] = set()
    for alias_token, target in aliases.items():
        remainder = ""
        if source_token.startswith(alias_token):
            remainder = source_token[len(alias_token) :]
        if remainder in _FIELD_ALIAS_QUALIFIERS:
            candidates.add(target)
    return next(iter(candidates)) if len(candidates) == 1 else None


def _adapt_ntc_template(
    template_text: str,
    *,
    field_aliases: dict[str, str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Apply the narrow, versioned compatibility transforms for trusted NTC.

    The generated-template dialect intentionally rejects Error. NTC commonly
    uses a final catch-all Error rule to detect unknown lines, while this
    platform records drift through output qualification, coverage and replay.
    Preserve every original rule and only turn the trusted catch-all into an
    ignore rule.

    NTC also conventionally exposes uppercase field identifiers while platform
    ParserSpec fields use lowercase snake case. Normalize identifier casing only;
    do not rename field semantics. This keeps the copied candidate executable by
    the production ParserRuntime while the untouched NTC source remains frozen in
    provenance. All other unsupported constructs remain validation errors.
    """

    trailing_newline = template_text.endswith(("\n", "\r"))
    lines = template_text.splitlines()
    aliases = dict(field_aliases or {})
    desired_mapping: dict[str, str] = {}
    for line in lines:
        match = _TEXTFSM_VALUE_RE.fullmatch(line)
        if match is None:
            continue
        source_name = match.group("field")
        desired_mapping[source_name] = (
            _resolve_field_alias(source_name, aliases)
            or source_name.casefold()
        )

    sources_by_target: dict[str, list[str]] = {}
    for source_name, target in desired_mapping.items():
        sources_by_target.setdefault(target, []).append(source_name)
    field_mapping: dict[str, str] = {}
    collision_fallbacks: dict[str, str] = {}
    for target, source_names in sources_by_target.items():
        if len(source_names) == 1:
            field_mapping[source_names[0]] = target
            continue
        exact = [name for name in source_names if name.casefold() == target]
        preferred = exact[0] if len(exact) == 1 else None
        for source_name in source_names:
            resolved = target if source_name == preferred else source_name.casefold()
            field_mapping[source_name] = resolved
            if resolved != target:
                collision_fallbacks[source_name] = resolved

    output: list[str] = []
    transformations: list[dict[str, Any]] = []
    normalized_fields = {
        source: target
        for source, target in field_mapping.items()
        if source != target and target == source.casefold()
    }
    contract_aligned_fields = {
        source: target
        for source, target in field_mapping.items()
        if target != source.casefold()
    }
    if normalized_fields:
        transformations.append(
            {
                "kind": "field_name_case_normalization",
                "mapping": dict(sorted(normalized_fields.items())),
            }
        )
    if contract_aligned_fields:
        transformations.append(
            {
                "kind": "field_name_contract_alignment",
                "mapping": dict(sorted(contract_aligned_fields.items())),
            }
        )
    if collision_fallbacks:
        transformations.append(
            {
                "kind": "field_name_collision_fallback",
                "mapping": dict(sorted(collision_fallbacks.items())),
            }
        )
    for line_number, line in enumerate(lines, start=1):
        match = _NTC_CATCH_ALL_ERROR_RE.fullmatch(line)
        if match is not None:
            derived = f"{match.group('indent')}{match.group('pattern')}"
            output.append(derived)
            transformations.append(
                {
                    "kind": "catch_all_error_to_ignore",
                    "line_number": line_number,
                    "source": line.strip(),
                    "derived": derived.strip(),
                }
            )
            continue
        value_match = _TEXTFSM_VALUE_RE.fullmatch(line)
        if value_match is not None:
            source_name = value_match.group("field")
            options = value_match.group("options")
            output.append(
                f"{value_match.group('prefix')}"
                f"{f'{options} ' if options else ''}"
                f"{field_mapping.get(source_name, source_name)}"
                f"{value_match.group('suffix')}"
            )
            continue
        output.append(
            _TEXTFSM_FIELD_REFERENCE_RE.sub(
                lambda reference: (
                    "${"
                    + field_mapping.get(
                        reference.group("field"),
                        reference.group("field"),
                    )
                    + "}"
                ),
                line,
            )
        )
    transformed = "\n".join(output)
    if trailing_newline:
        transformed += "\n"
    return transformed, transformations


def _project_ntc_template_to_fields(
    template_text: str,
    *,
    allowed_fields: frozenset[str],
) -> tuple[str, dict[str, Any] | None]:
    """Remove unrelated NTC output columns without changing source matching.

    Some NTC assets intentionally parse several sections of a broad command in
    one template. A narrower platform command can reuse that asset, but its
    ParserSpec should not inherit columns that are never populated. Excluded
    Value references become non-capturing regexes, and excluded-only Record
    rules become ignore rules. The projected template is still executed against
    every frozen sample before it can become a candidate.
    """

    trailing_newline = template_text.endswith(("\n", "\r"))
    lines = template_text.splitlines()
    patterns: dict[str, str] = {}
    for line in lines:
        match = _TEXTFSM_VALUE_RE.fullmatch(line)
        if match is None:
            continue
        suffix = str(match.group("suffix") or "").strip()
        if not (suffix.startswith("(") and suffix.endswith(")")):
            raise ValueError(
                f"cannot project NTC Value with unsupported pattern: {line.strip()}"
            )
        patterns[match.group("field")] = f"(?:{suffix[1:-1]})"

    removed_fields = frozenset(patterns) - allowed_fields
    if not removed_fields:
        return template_text, None

    output: list[str] = []
    for line in lines:
        value_match = _TEXTFSM_VALUE_RE.fullmatch(line)
        if (
            value_match is not None
            and value_match.group("field") in removed_fields
        ):
            continue

        references = tuple(
            match.group("field")
            for match in _TEXTFSM_FIELD_REFERENCE_RE.finditer(line)
        )
        excluded_references = frozenset(references) & removed_fields
        projected = _TEXTFSM_FIELD_REFERENCE_RE.sub(
            lambda match: (
                patterns[match.group("field")]
                if match.group("field") in removed_fields
                else match.group(0)
            ),
            line,
        )
        if excluded_references and not (frozenset(references) - removed_fields):
            action = _TEXTFSM_RECORD_ACTION_RE.fullmatch(projected)
            if action is not None:
                body = str(action.group("body") or "").rstrip()
                state = str(action.group("state") or "").strip()
                line_op = str(action.group("line_op") or "").strip()
                if state:
                    projected = f"{body} -> {state}"
                elif line_op == "Continue":
                    projected = f"{body} -> Continue"
                else:
                    projected = body
        output.append(projected)

    transformed = "\n".join(output)
    if trailing_newline:
        transformed += "\n"
    return transformed, {
        "kind": "field_schema_projection",
        "kept_fields": sorted(frozenset(patterns) & allowed_fields),
        "removed_fields": sorted(removed_fields),
    }


class NtcPlatformMapper:
    """Map inventory identity to NTC platforms without guessing missing families."""

    def __init__(self, path: str | Path = DEFAULT_PLATFORM_MAP) -> None:
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        mappings = payload.get("mappings")
        if not isinstance(mappings, list):
            raise ValueError("NTC platform map requires a mappings list")
        self.version = int(payload.get("version") or 1)
        self.mappings = tuple(dict(item) for item in mappings)

    def resolve(self, device: dict[str, Any]) -> NtcPlatformDecision:
        vendor = _normalized(_device_value(device, "vendor"))
        family = _normalized(_device_value(device, "family"))
        model = _normalized(_device_value(device, "model"))
        platform = _normalized(_device_value(device, "platform"))
        device_type = _normalized(_device_value(device, "device_type"))
        if not vendor:
            return NtcPlatformDecision(
                status="unresolved",
                platforms=(),
                mapping_ids=(),
                reason_codes=("identity_missing:vendor",),
            )
        matches: list[tuple[int, str, tuple[str, ...]]] = []
        family_required_but_missing = False
        for row in self.mappings:
            selector = dict(row.get("match") or {})
            vendors = {_normalized(item) for item in selector.get("vendors") or []}
            if vendors and vendor not in vendors:
                continue
            families = {_normalized(item) for item in selector.get("families") or []}
            if families:
                if family:
                    family_patterns = [
                        str(item).strip()
                        for item in selector.get("family_patterns") or []
                        if str(item).strip()
                    ]
                    family_matches = family in families or any(
                        re.search(pattern, family, flags=re.IGNORECASE)
                        for pattern in family_patterns
                    )
                    if not family_matches:
                        continue
                else:
                    model_patterns = [
                        str(item).strip()
                        for item in selector.get("model_patterns") or []
                        if str(item).strip()
                    ]
                    model_matches = bool(model) and any(
                        re.search(pattern, model, flags=re.IGNORECASE)
                        for pattern in model_patterns
                    )
                    if not model_matches:
                        family_required_but_missing = True
                        continue
            platforms = {_normalized(item) for item in selector.get("platforms") or []}
            if platforms and platform not in platforms:
                continue
            device_types = {
                _normalized(item) for item in selector.get("device_types") or []
            }
            if device_types and device_type not in device_types:
                continue
            ntc_platforms = tuple(
                str(item).strip()
                for item in row.get("platforms") or []
                if str(item).strip()
            )
            if ntc_platforms:
                matches.append(
                    (
                        int(row.get("priority") or 0),
                        str(row.get("id") or ""),
                        ntc_platforms,
                    )
                )
        if not matches:
            reasons = (
                ("identity_missing:family",)
                if family_required_but_missing
                else ("no_ntc_platform_mapping",)
            )
            return NtcPlatformDecision("unresolved", (), (), reasons)
        best_priority = max(item[0] for item in matches)
        winners = [item for item in matches if item[0] == best_priority]
        platform_values = tuple(
            sorted({platform_name for _, _, values in winners for platform_name in values})
        )
        return NtcPlatformDecision(
            status="resolved" if len(platform_values) == 1 else "ambiguous",
            platforms=platform_values,
            mapping_ids=tuple(sorted(item[1] for item in winners)),
            reason_codes=(
                ("platform_mapping_resolved",)
                if len(platform_values) == 1
                else ("multiple_ntc_platforms_require_execution",)
            ),
        )


class NtcTemplateResolver:
    """Resolve and objectively execute local NTC templates.

    A zero-record result remains objective evidence. Semantic acceptance belongs
    to the LLM or engineer review stages.
    """

    def __init__(
        self,
        *,
        templates_dir: str | Path | None = None,
        platform_mapper: NtcPlatformMapper | None = None,
    ) -> None:
        package_templates = files("ntc_templates").joinpath("templates")
        self.templates_dir = Path(templates_dir or str(package_templates))
        self.platform_mapper = platform_mapper or NtcPlatformMapper()
        self.package_version = version("ntc_templates")
        package_metadata = metadata("ntc_templates")
        self.license = str(package_metadata.get("License") or "unknown")
        self._index = clitable.CliTable("index", str(self.templates_dir))
        self._backend = TextFSMValidationBackend()

    def resolve(
        self,
        *,
        device: dict[str, Any],
        command: str,
        samples: tuple[ParserSample, ...],
        spec: ParserSpec | ParserSpecDocument | None = None,
    ) -> tuple[NtcPlatformDecision, tuple[NtcResolution, ...]]:
        platform_decision = self.platform_mapper.resolve(device)
        if not platform_decision.platforms:
            return platform_decision, (
                NtcResolution(
                    status="not_found",
                    platform=None,
                    command=command,
                    template_name=None,
                    candidate=None,
                    execution_evidence={},
                    error="; ".join(platform_decision.reason_codes),
                ),
            )
        semantic_aliases, semantic_profile_id = _semantic_profile_field_aliases(
            command=command,
            samples=samples,
        )
        field_aliases = _parser_spec_field_aliases(
            spec,
            semantic_aliases=semantic_aliases,
        )
        allowed_fields = (
            frozenset(field.name for field in spec.fields)
            if isinstance(spec, ParserSpec)
            else None
        )
        resolutions = tuple(
            self._resolve_platform(
                platform=platform,
                command=command,
                samples=samples,
                mapping=platform_decision,
                field_aliases=field_aliases,
                semantic_profile_id=semantic_profile_id,
                allowed_fields=allowed_fields,
            )
            for platform in platform_decision.platforms
        )
        return platform_decision, resolutions

    def _resolve_platform(
        self,
        *,
        platform: str,
        command: str,
        samples: tuple[ParserSample, ...],
        mapping: NtcPlatformDecision,
        field_aliases: dict[str, str],
        semantic_profile_id: str | None,
        allowed_fields: frozenset[str] | None,
    ) -> NtcResolution:
        row_index = self._index.index.GetRowMatch(
            {"Command": str(command), "Platform": platform}
        )
        if not row_index:
            return NtcResolution(
                status="not_found",
                platform=platform,
                command=command,
                template_name=None,
                candidate=None,
                execution_evidence={},
                error="NTC index has no matching template",
            )
        template_name = str(self._index.index.index[row_index]["Template"])
        if ":" in template_name:
            return NtcResolution(
                status="execution_error",
                platform=platform,
                command=command,
                template_name=template_name,
                candidate=None,
                execution_evidence={},
                error="composite NTC templates are not importable as one immutable Parser",
            )
        command_pattern = self._index.index.compiled[row_index]["Command"]
        if not (
            command_pattern.fullmatch(str(command))
            or _template_command_compatible(
                template_name=template_name,
                platform=platform,
                command=command,
            )
        ):
            return NtcResolution(
                status="not_found",
                platform=platform,
                command=command,
                template_name=template_name,
                candidate=None,
                execution_evidence={},
                error="NTC index matched only a prefix belonging to another command",
            )
        source = self.templates_dir / template_name
        try:
            source_template_text = source.read_text(encoding="utf-8")
        except OSError as exc:
            return NtcResolution(
                status="execution_error",
                platform=platform,
                command=command,
                template_name=template_name,
                candidate=None,
                execution_evidence={},
                error=f"unable to read NTC template: {exc}",
            )
        try:
            template_text, transformations = _adapt_ntc_template(
                source_template_text,
                field_aliases=field_aliases,
            )
            if allowed_fields is not None:
                template_text, projection = _project_ntc_template_to_fields(
                    template_text,
                    allowed_fields=allowed_fields,
                )
                if projection is not None:
                    transformations.append(projection)
        except ValueError as exc:
            return NtcResolution(
                status="execution_error",
                platform=platform,
                command=command,
                template_name=template_name,
                candidate=None,
                execution_evidence={},
                error=str(exc),
            )
        provenance = {
            "provider": "ntc_templates",
            "package_version": self.package_version,
            "source_template": template_name,
            "source_template_text": source_template_text,
            "source_content_sha256": hashlib.sha256(
                source_template_text.encode("utf-8")
            ).hexdigest(),
            "content_sha256": hashlib.sha256(
                template_text.encode("utf-8")
            ).hexdigest(),
            "platform": platform,
            "command": command,
            "license": self.license,
            "platform_mapping_version": self.platform_mapper.version,
            "platform_mapping_ids": list(mapping.mapping_ids),
            "semantic_profile_id": semantic_profile_id,
            "transformation_version": (
                (
                    NTC_SCHEMA_PROJECTION_TRANSFORMATION_VERSION
                    if any(
                        item.get("kind") == "field_schema_projection"
                        for item in transformations
                    )
                    else NTC_CONTRACT_TRANSFORMATION_VERSION
                    if any(
                        item.get("kind") == "field_name_contract_alignment"
                        for item in transformations
                    )
                    else NTC_COMPAT_TRANSFORMATION_VERSION
                )
                if transformations
                else None
            ),
            "transformations": transformations,
        }
        candidate = TemplateCandidate(
            template_text=template_text,
            provider="ntc_templates",
            protocol_version="ntc-objective-execution-v1",
            provenance=provenance,
        )
        report = execute_template_candidate(
            candidate.template_text,
            samples,
            self._backend,
        )
        evidence = {
            "template_sha256": candidate.sha256,
            "compile_status": (
                "compiled" if report.hard_gates.get("compiled") else "failed"
            ),
            "runtime_error": (
                None if report.passed else "; ".join(report.diagnostics[:10])
            ),
            "samples": [
                {
                    "sample_id": sample.sample_id,
                    "record_count": sample.record_count,
                    "field_names": sorted(
                        {
                            str(field)
                            for record in sample.records
                            for field in record
                        }
                    ),
                    "record_shape_sha256": _stable_digest(
                        [
                            {
                                str(field): bool(str(value or "").strip())
                                for field, value in sorted(record.items())
                            }
                            for record in sample.records
                        ]
                    ),
                    "records_sha256": _stable_digest(
                        [dict(record) for record in sample.records]
                    ),
                    "errors": list(sample.errors),
                }
                for sample in report.samples
            ],
            "field_profiles": _field_profiles(report),
            "engine_version": "textfsm",
        }
        evidence["digest"] = _stable_digest(evidence)
        return NtcResolution(
            status="resolved" if report.passed else "execution_error",
            platform=platform,
            command=command,
            template_name=template_name,
            candidate=candidate,
            execution_evidence=evidence,
            error=None if report.passed else evidence["runtime_error"],
        )

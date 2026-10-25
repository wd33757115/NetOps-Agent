"""Policy-driven TextFSM runtime."""

from __future__ import annotations

import hashlib
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from .assets import TemplateAssetStore
from .identity import DeviceIdentity
from .key_contract import validate_entity_keys
from .output_classifier import OutputClassifier
from .parser_bundle import parse_parser_bundle
from .policy import CategorySpec, ParserPolicy, approved_template_may_serve
from .record_shape import RecordShapeError, expand_list_primary_records
from .textfsm_input import prepare_textfsm_input
from .value_types import is_unavailable_value, is_valid_ip_value

try:
    import textfsm
except ImportError:  # pragma: no cover
    textfsm = None


@dataclass(frozen=True)
class ParserRuntimeResult:
    policy_status: str
    identity_status: str
    parser_status: str
    canonical_command: str
    handling: str
    template_id: str | None = None
    template_version: str | None = None
    structured_data: Any = None
    failure_code: str | None = None
    identity_evidence: dict[str, Any] | None = None
    category: str | None = None
    entity_type: str | None = None
    primary_keys: tuple[str, ...] = ()
    output_status: str = "data"
    capability: str | None = None
    capability_status: str = "unknown"
    template_profile: str | None = None
    comparison_profile: str | None = None
    comparison_profile_version: int | None = None


def _field_value(record: Mapping[str, Any], field_name: str) -> Any:
    """Read a Parser field with the key-contract's case-insensitive behavior."""

    if field_name in record:
        return record[field_name]
    expected = field_name.casefold()
    for key, value in record.items():
        if str(key).casefold() == expected:
            return value
    return None


def _record_set_fields(
    payload: Mapping[str, Any], *, record_set_name: str | None
) -> Mapping[str, Any]:
    """Find fields for one record set in either ParserSpec persistence shape."""

    record_sets = payload.get("record_sets")
    candidates: list[Mapping[str, Any]] = []
    if isinstance(record_sets, list):
        candidates = [item for item in record_sets if isinstance(item, Mapping)]
    elif isinstance(record_sets, Mapping):
        candidates = [
            {"name": name, **dict(item)}
            for name, item in record_sets.items()
            if isinstance(item, Mapping)
        ]
    if candidates:
        if record_set_name:
            expected = record_set_name.casefold()
            for item in candidates:
                if str(item.get("name") or "").casefold() == expected:
                    fields = item.get("fields")
                    return fields if isinstance(fields, Mapping) else {}
        if len(candidates) == 1:
            fields = candidates[0].get("fields")
            return fields if isinstance(fields, Mapping) else {}
        return {}
    fields = payload.get("fields")
    return fields if isinstance(fields, Mapping) else {}


def _primary_key_defaults_from_validation(
    validation: Mapping[str, Any],
    *,
    primary_keys: tuple[str, ...],
    record_set_name: str | None = None,
) -> dict[str, str]:
    """Read validation-only key defaults from immutable ParserSpec evidence.

    Older candidates only carry this information under ``parser_spec`` while
    newer persisted contracts also expose ``primary_key_defaults`` directly.
    Supporting both lets an already-created candidate be replayed correctly
    without rewriting its immutable evidence.
    """

    if not primary_keys:
        return {}
    payloads: list[Mapping[str, Any]] = [validation]
    for name in ("parser_spec", "source_parser_spec"):
        payload = validation.get(name)
        if isinstance(payload, Mapping):
            payloads.append(payload)
    defaults: dict[str, str] = {}
    for field_name in primary_keys:
        expected = field_name.casefold()
        for payload in payloads:
            explicit = payload.get("primary_key_defaults")
            value = None
            if isinstance(explicit, Mapping):
                for key, candidate in explicit.items():
                    if str(key).casefold() == expected:
                        value = candidate
                        break
            if value is None:
                fields = _record_set_fields(
                    payload,
                    record_set_name=record_set_name,
                )
                for key, definition in fields.items():
                    if str(key).casefold() == expected and isinstance(definition, Mapping):
                        value = definition.get("primary_key_default")
                        break
            normalized = str(value).strip() if value is not None else ""
            if normalized:
                defaults[field_name] = normalized
                break
    return defaults


def _records_for_key_validation(
    records: list[dict[str, Any]],
    spec: CategorySpec,
) -> list[dict[str, Any]]:
    """Return validation copies with declared sparse-key defaults applied.

    These copies exist only for entity-key validation.  The original parser
    records remain untouched so the Adapter Projection is still the sole owner
    of canonical normalization (for example ``""`` -> ``"aggregate"``).
    """

    if not spec.primary_key_defaults:
        return records
    normalized_records: list[dict[str, Any]] = []
    for record in records:
        normalized = record
        for field_name, default in spec.primary_key_defaults.items():
            value = _field_value(record, field_name)
            if value is None or (isinstance(value, str) and not value.strip()):
                if normalized is record:
                    normalized = dict(record)
                normalized[field_name] = default
        normalized_records.append(normalized)
    return normalized_records


class ParserRuntime:
    def __init__(
        self,
        *,
        policy=None,
        registry=None,
        asset_store=None,
        output_classifier=None,
        runtime_bundle=None,
    ):
        if registry is None:
            from .runtime_repository import ParserRuntimeRepository

            registry = ParserRuntimeRepository()
        self.registry = registry
        policy_repository = registry if hasattr(registry, "list_command_aliases") else None
        self.policy = policy or ParserPolicy(repository=policy_repository)
        self.asset_store = asset_store or TemplateAssetStore()
        self.output_classifier = output_classifier or OutputClassifier()
        self.runtime_bundle = runtime_bundle

    def _resolve_template(self, *, identity: DeviceIdentity, canonical_command: str):
        if self.runtime_bundle is not None:
            return self.runtime_bundle.resolve(
                canonical_command=canonical_command,
                registry=self.registry,
            )
        return self.registry.resolve_active(
            vendor=identity.vendor or "",
            model=identity.model or "",
            family=identity.family,
            command=canonical_command,
        )

    def _effective_decision(
        self,
        *,
        identity: DeviceIdentity,
        command: str,
    ):
        """Promote catalog-managed commands when an approved Parser exists.

        Explicit raw-hash policies remain authoritative. Unconfigured commands
        can use approved assets instead of the default text-only fallback.
        """

        decision = self.policy.decide(
            vendor=identity.vendor,
            model=identity.model,
            family=identity.family,
            command=command,
        )
        template = None
        if (
            not decision.enabled
            and approved_template_may_serve(self.policy, decision, command)
            and identity.status == "known"
        ):
            template = self._resolve_template(
                identity=identity,
                canonical_command=decision.canonical_command,
            )
            if template is not None:
                category = str(getattr(template, "category", None) or "parser_asset")
                decision = replace(
                    decision,
                    enabled=True,
                    handling="structured",
                    asset_mode="full_chain",
                    category=decision.category or category,
                    capability=decision.capability or category,
                    capability_status="supported",
                    template_profile=(
                        decision.template_profile
                        or str(getattr(template, "profile", None) or "")
                        or None
                    ),
                )
        return decision, template

    def check_coverage(
        self,
        *,
        identity: DeviceIdentity,
        command: str,
    ) -> dict[str, Any]:
        """Check production readiness without parsing device output."""
        decision, resolved_template = self._effective_decision(
            identity=identity,
            command=command,
        )
        result = {
            "canonical_command": decision.canonical_command,
            "policy_status": (
                "eligible"
                if decision.enabled
                else ("raw" if decision.handling == "raw_hash_diff" else "ignored")
            ),
            "identity_status": identity.status,
            "coverage_status": "ignored",
            "template_id": None,
            "template_version": None,
            "failure_code": None,
            "comparison_profile": decision.comparison_profile,
            "comparison_profile_version": decision.comparison_profile_version,
        }
        if not decision.enabled:
            if decision.handling == "raw_hash_diff":
                return {
                    **result,
                    "coverage_status": "ready",
                    "failure_code": None,
                }
            return result
        if identity.status != "known":
            return {
                **result,
                "coverage_status": "identity_unknown",
                "failure_code": "identity_unknown",
            }
        template = resolved_template or self._resolve_template(
            identity=identity,
            canonical_command=decision.canonical_command,
        )
        if template is None:
            return {
                **result,
                "coverage_status": "template_missing",
                "failure_code": "template_missing",
            }
        if not self.asset_store.verify(
            template.asset_path,
            template.sha256,
            getattr(template, "template_text", None),
        ):
            return {
                **result,
                "coverage_status": "asset_invalid",
                "template_id": template.id,
                "template_version": template.version,
                "failure_code": "asset_hash_mismatch",
            }
        return {
            **result,
            "coverage_status": "ready",
            "template_id": template.id,
            "template_version": template.version,
        }

    def classify_output(
        self,
        *,
        identity: DeviceIdentity,
        command: str,
        raw_text: str,
    ) -> dict[str, Any]:
        """Classify raw command evidence without resolving or running a Parser."""
        decision, _resolved_template = self._effective_decision(
            identity=identity,
            command=command,
        )
        output_quality_profile = (
            self.runtime_bundle.output_quality_profile(
                decision.canonical_command
            )
            if self.runtime_bundle is not None
            and hasattr(self.runtime_bundle, "output_quality_profile")
            else None
        )
        classification = self.output_classifier.classify(
            raw_text,
            decision=decision,
            profile=output_quality_profile,
        )
        return {
            "canonical_command": decision.canonical_command,
            "policy_status": (
                "eligible"
                if decision.enabled
                else (
                    "raw"
                    if decision.handling == "raw_hash_diff"
                    else "ignored"
                )
            ),
            "identity_status": identity.status,
            "handling": decision.handling,
            "output_status": classification.status,
            "output_reason": classification.reason,
            "capability": decision.capability,
            "capability_status": decision.capability_status,
            "template_profile": decision.template_profile,
            "comparison_profile": decision.comparison_profile,
            "comparison_profile_version": (
                decision.comparison_profile_version
            ),
        }

    def parse(
        self,
        *,
        identity: DeviceIdentity,
        command: str,
        raw_text: str,
    ) -> ParserRuntimeResult:
        decision, resolved_template = self._effective_decision(
            identity=identity,
            command=command,
        )
        evidence = {
            "confidence": identity.confidence,
            "source": identity.source,
            "command": identity.evidence_command,
            "text": identity.evidence_text,
        }
        if not decision.enabled:
            if decision.handling == "raw_hash_diff":
                return ParserRuntimeResult(
                    policy_status="raw",
                    identity_status=identity.status,
                    parser_status="not_required",
                    canonical_command=decision.canonical_command,
                    handling=decision.handling,
                    # This is evidence metadata rather than a Parser record.
                    # Keeping the digest in the result lets offline callers
                    # preview the deterministic text-diff input without
                    # exposing raw CLI; persistence still treats
                    # ``CommandSnapshot.raw_text_hash`` as authoritative.
                    structured_data=[
                        {
                            "raw_text_hash": hashlib.sha256(
                                raw_text.encode("utf-8")
                            ).hexdigest()
                        }
                    ],
                    identity_evidence=evidence,
                    output_status="data",
                    capability=decision.capability,
                    capability_status=decision.capability_status,
                    template_profile=decision.template_profile,
                    comparison_profile=decision.comparison_profile,
                    comparison_profile_version=decision.comparison_profile_version,
                )
            return ParserRuntimeResult(
                policy_status="ignored",
                identity_status=identity.status,
                parser_status="unavailable",
                canonical_command=decision.canonical_command,
                handling=decision.handling,
                identity_evidence=evidence,
                output_status="data",
            )
        output_quality_profile = (
            self.runtime_bundle.output_quality_profile(
                decision.canonical_command
            )
            if self.runtime_bundle is not None
            and hasattr(self.runtime_bundle, "output_quality_profile")
            else None
        )
        classification = self.output_classifier.classify(
            raw_text,
            decision=decision,
            profile=output_quality_profile,
        )
        common = {
            "capability": decision.capability,
            "capability_status": decision.capability_status,
            "template_profile": decision.template_profile,
            "output_status": classification.status,
        }
        if classification.status == "valid_empty":
            return ParserRuntimeResult(
                policy_status="eligible",
                identity_status=identity.status,
                parser_status="empty",
                canonical_command=decision.canonical_command,
                handling=decision.handling,
                structured_data=[],
                identity_evidence=evidence,
                category=decision.capability,
                **common,
            )
        if classification.status in {
            "empty_unverified",
            "unsupported_command",
            "command_error",
            "truncated",
            "paged",
        }:
            return ParserRuntimeResult(
                policy_status="eligible",
                identity_status=identity.status,
                parser_status="unavailable",
                canonical_command=decision.canonical_command,
                handling=decision.handling,
                failure_code=classification.status,
                identity_evidence={**evidence, "output_reason": classification.reason},
                category=decision.capability,
                **common,
            )
        if identity.status != "known":
            return ParserRuntimeResult(
                policy_status="eligible",
                identity_status=identity.status,
                parser_status="unavailable",
                canonical_command=decision.canonical_command,
                handling=decision.handling,
                failure_code="identity_unknown",
                identity_evidence=evidence,
                category=decision.category,
                **common,
            )
        template = resolved_template or self._resolve_template(
            identity=identity,
            canonical_command=decision.canonical_command,
        )
        if template is None:
            return ParserRuntimeResult(
                policy_status="eligible",
                identity_status="known",
                parser_status="unavailable",
                canonical_command=decision.canonical_command,
                handling=decision.handling,
                failure_code="template_missing",
                identity_evidence=evidence,
                category=decision.category,
                **common,
            )
        if not self.asset_store.verify(
            template.asset_path,
            template.sha256,
            getattr(template, "template_text", None),
        ):
            return self._failed(decision, identity, template, "asset_hash_mismatch", evidence)
        if textfsm is None:
            return self._failed(decision, identity, template, "textfsm_unavailable", evidence)
        spec = self._template_spec(template)
        try:
            content = self.asset_store.read(
                template.asset_path,
                template.sha256,
                getattr(template, "template_text", None),
            )
            bundle = parse_parser_bundle(content)
            if bundle is not None:
                return self._parse_bundle(
                    bundle=bundle,
                    raw_text=raw_text,
                    decision=decision,
                    identity=identity,
                    template=template,
                    evidence=evidence,
                    common=common,
                )
            parser = textfsm.TextFSM(io.StringIO(content))
            values = parser.ParseText(prepare_textfsm_input(content, raw_text or ""))
            records = [dict(zip(parser.header, row, strict=False)) for row in values]
        except Exception:
            return self._failed(decision, identity, template, "template_parse_error", evidence)
        if not records:
            legal_empty = bool(
                spec
                and spec.allow_empty
                and any(
                    re.search(pattern, raw_text or "", re.IGNORECASE | re.DOTALL)
                    for pattern in spec.empty_patterns
                )
            )
            if legal_empty:
                return ParserRuntimeResult(
                    policy_status="eligible",
                    identity_status="known",
                    parser_status="empty",
                    canonical_command=decision.canonical_command,
                    handling=decision.handling,
                    template_id=template.id,
                    template_version=template.version,
                    structured_data=[],
                    identity_evidence=evidence,
                    category=spec.name,
                    entity_type=spec.entity_type if spec else None,
                    primary_keys=spec.primary_keys if spec else (),
                    output_status="valid_empty",
                    capability=decision.capability,
                    capability_status=decision.capability_status,
                    template_profile=decision.template_profile,
                )
            return self._failed(decision, identity, template, "unexpected_empty", evidence)
        required = set(spec.required_fields if spec else ())
        if required and any(
            not required.issubset(record)
            for record in records
        ):
            return self._failed(decision, identity, template, "required_fields_missing", evidence)
        if spec:
            key_result = validate_entity_keys(
                _records_for_key_validation(records, spec),
                spec.primary_keys,
            )
            if not key_result.valid:
                failure_code = (
                    "primary_key_duplicate" if key_result.duplicate_keys else "primary_key_invalid"
                )
                return self._failed(decision, identity, template, failure_code, evidence)
        if spec:
            for record in records:
                for field, validator in spec.validators.items():
                    value = record.get(field)
                    if _field_validation_failed(value, validator):
                        return self._failed(
                            decision, identity, template, "field_validation_failed", evidence
                        )
        return ParserRuntimeResult(
            policy_status="eligible",
            identity_status="known",
            parser_status="success",
            canonical_command=decision.canonical_command,
            handling=decision.handling,
            template_id=template.id,
            template_version=template.version,
            structured_data=records,
            identity_evidence=evidence,
            category=spec.name,
            entity_type=spec.entity_type if spec else None,
            primary_keys=spec.primary_keys if spec else (),
            output_status="data",
            capability=decision.capability,
            capability_status=decision.capability_status,
            template_profile=decision.template_profile,
        )

    def _parse_bundle(
        self,
        *,
        bundle,
        raw_text: str,
        decision,
        identity,
        template,
        evidence: dict[str, Any],
        common: dict[str, Any],
    ) -> ParserRuntimeResult:
        validation = dict(getattr(template, "validation_json", None) or {})
        raw_specs = [
            dict(item)
            for item in validation.get("record_sets") or ()
            if isinstance(item, dict)
        ]
        specs: dict[str, CategorySpec] = {}
        for item in raw_specs:
            record_set_name = str(item.get("name") or "").strip()
            if not record_set_name:
                continue
            primary_keys = tuple(item.get("primary_keys") or ())
            specs[record_set_name] = self._category_spec_from_validation(
                {
                    **item,
                    "primary_key_defaults": {
                        **_primary_key_defaults_from_validation(
                            validation,
                            record_set_name=record_set_name,
                            primary_keys=primary_keys,
                        ),
                        **_primary_key_defaults_from_validation(
                            item,
                            primary_keys=primary_keys,
                        ),
                    },
                }
            )
        bundle_names = {item.name for item in bundle.record_sets}
        if specs and set(specs) != bundle_names:
            return self._failed(
                decision,
                identity,
                template,
                "record_set_contract_mismatch",
                evidence,
            )
        output: dict[str, list[dict[str, Any]]] = {}
        for record_set in bundle.record_sets:
            parser = textfsm.TextFSM(io.StringIO(record_set.template_text))
            rows = parser.ParseText(
                prepare_textfsm_input(record_set.template_text, raw_text or "")
            )
            records = [
                dict(zip(parser.header, row, strict=False))
                for row in rows
            ]
            spec = specs.get(record_set.name)
            if spec:
                try:
                    records = expand_list_primary_records(records, spec.primary_keys)
                except RecordShapeError:
                    return self._failed(
                        decision,
                        identity,
                        template,
                        f"record_shape_invalid:{record_set.name}",
                        evidence,
                    )
            failure = self._validate_records(records, spec, raw_text=raw_text)
            if failure:
                return self._failed(
                    decision,
                    identity,
                    template,
                    f"{failure}:{record_set.name}",
                    evidence,
                )
            output[record_set.name] = records
        if not any(output.values()):
            legal_empty = any(
                spec
                and spec.allow_empty
                and any(
                    re.search(pattern, raw_text or "", re.IGNORECASE | re.DOTALL)
                    for pattern in spec.empty_patterns
                )
                for spec in specs.values()
            )
            if legal_empty:
                return ParserRuntimeResult(
                    policy_status="eligible",
                    identity_status="known",
                    parser_status="empty",
                    canonical_command=decision.canonical_command,
                    handling=decision.handling,
                    template_id=template.id,
                    template_version=template.version,
                    structured_data={
                        "format": "netops.structured_record_sets/v1",
                        "record_sets": output,
                    },
                    identity_evidence=evidence,
                    category="multi_record",
                    output_status="valid_empty",
                    capability=decision.capability,
                    capability_status=decision.capability_status,
                    template_profile=decision.template_profile,
                )
            return self._failed(
                decision,
                identity,
                template,
                "unexpected_empty",
                evidence,
            )
        return ParserRuntimeResult(
            policy_status="eligible",
            identity_status="known",
            parser_status="success",
            canonical_command=decision.canonical_command,
            handling=decision.handling,
            template_id=template.id,
            template_version=template.version,
            structured_data={
                "format": "netops.structured_record_sets/v1",
                "record_sets": output,
            },
            identity_evidence=evidence,
            category="multi_record",
            entity_type=None,
            primary_keys=(),
            output_status="data",
            capability=decision.capability,
            capability_status=decision.capability_status,
            template_profile=decision.template_profile,
        )

    @staticmethod
    def _category_spec_from_validation(validation: dict[str, Any]) -> CategorySpec:
        category = str(
            validation.get("category")
            or validation.get("entity_type")
            or validation.get("entity")
            or "record"
        )
        return CategorySpec(
            name=category,
            entity_type=str(
                validation.get("entity_type")
                or validation.get("entity")
                or category
            ),
            primary_keys=tuple(validation.get("primary_keys") or ()),
            required_fields=tuple(validation.get("required_fields") or ()),
            optional_fields=tuple(validation.get("optional_fields") or ()),
            validators=dict(validation.get("validators") or {}),
            allow_empty=bool(validation.get("allow_empty", False)),
            empty_patterns=tuple(validation.get("empty_patterns") or ()),
            ignore_fields=tuple(validation.get("ignore_fields") or ()),
            presence=str(validation.get("presence") or "required").strip().casefold(),
            primary_key_defaults=_primary_key_defaults_from_validation(
                validation,
                primary_keys=tuple(validation.get("primary_keys") or ()),
            ),
        )

    @staticmethod
    def _validate_records(
        records: list[dict[str, Any]],
        spec: CategorySpec | None,
        *,
        raw_text: str,
    ) -> str | None:
        if spec is None:
            return None
        if not records:
            if spec.presence == "optional":
                return None
            legal_empty = spec.allow_empty and any(
                re.search(pattern, raw_text or "", re.IGNORECASE | re.DOTALL)
                for pattern in spec.empty_patterns
            )
            return None if legal_empty else "unexpected_empty"
        required = set(spec.required_fields)
        if required and any(
            not required.issubset(record)
            or any(record.get(field) in (None, "") for field in required)
            for record in records
        ):
            return "required_fields_missing"
        if spec.primary_keys:
            key_result = validate_entity_keys(
                _records_for_key_validation(records, spec),
                spec.primary_keys,
            )
            if not key_result.valid:
                return (
                    "primary_key_duplicate"
                    if key_result.duplicate_keys
                    else "primary_key_invalid"
                )
        if any(
            _field_validation_failed(record.get(field), validator)
            for record in records
            for field, validator in spec.validators.items()
        ):
            return "field_validation_failed"
        return None

    @staticmethod
    def _template_spec(template) -> CategorySpec:
        validation = dict(getattr(template, "validation_json", None) or {})
        if not validation.get("category"):
            validation["category"] = getattr(template, "category", None) or "record"
        return ParserRuntime._category_spec_from_validation(validation)

    @classmethod
    def _failed(cls, decision, identity, template, code, evidence):
        spec = cls._template_spec(template)
        return ParserRuntimeResult(
            policy_status="eligible",
            identity_status=identity.status,
            parser_status="failed",
            canonical_command=decision.canonical_command,
            handling=decision.handling,
            template_id=template.id,
            template_version=template.version,
            failure_code=code,
            identity_evidence=evidence,
            category=spec.name,
            entity_type=spec.entity_type,
            primary_keys=spec.primary_keys,
            output_status="data",
            capability=decision.capability,
            capability_status=decision.capability_status,
            template_profile=decision.template_profile,
        )


def _looks_like_interface_header(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {
        "interface",
        "port",
        "name",
        "interface name",
        "port name",
    }


def _field_validation_failed(value: Any, validator: dict[str, Any]) -> bool:
    # TextFSM ``Value List`` fields preserve repeated captures as a list while
    # ParserSpec validators describe the scalar item type. Validate every
    # captured item instead of stringifying the whole list (which turns valid
    # IP/MAC/number lists into false ``field_validation_failed`` results).
    if isinstance(value, (list, tuple, set)):
        return any(_field_validation_failed(item, validator) for item in value)
    if value in (None, ""):
        return False
    if is_unavailable_value(value, validator.get("unavailable_tokens")):
        return False
    rule_type = str(validator.get("type") or "").strip().casefold()
    text = str(value).strip()
    if rule_type in {"number", "percent"}:
        try:
            numeric = float(text.rstrip("%") if rule_type == "percent" else text)
        except ValueError:
            return True
        if rule_type == "percent" and not 0 <= numeric <= 100:
            return True
        minimum = validator.get("minimum")
        maximum = validator.get("maximum")
        return bool(
            (minimum is not None and numeric < float(minimum))
            or (maximum is not None and numeric > float(maximum))
        )
    if rule_type == "enum":
        allowed = {str(item).strip().casefold() for item in validator.get("values") or ()}
        return bool(allowed and text.casefold() not in allowed)
    if rule_type in {"ip", "ip_or_unassigned"}:
        return not is_valid_ip_value(
            value,
            allow_unassigned=rule_type == "ip_or_unassigned",
        )
    if rule_type == "interface_name":
        return _looks_like_interface_header(value)
    if rule_type == "regex":
        pattern = str(validator.get("pattern") or "")
        return bool(pattern and re.fullmatch(pattern, text) is None)
    return False

"""Policy-driven TextFSM runtime."""

from __future__ import annotations

import hashlib
import io
import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .assets import TemplateAssetStore
from .identity import DeviceIdentity
from .key_contract import validate_entity_keys
from .output_classifier import OutputClassifier
from .policy import CategorySpec, ParserPolicy
from .textfsm_input import prepare_textfsm_input

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
    structured_data: list[dict[str, Any]] | None = None
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

    def check_coverage(
        self,
        *,
        identity: DeviceIdentity,
        command: str,
    ) -> dict[str, Any]:
        """Check production readiness without parsing device output."""
        decision = self.policy.decide(
            vendor=identity.vendor,
            model=identity.model,
            family=identity.family,
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
        template = self._resolve_template(
            identity=identity,
            canonical_command=decision.canonical_command,
        )
        if template is None:
            return {
                **result,
                "coverage_status": "template_missing",
                "failure_code": "template_missing",
            }
        if not self.asset_store.verify(template.asset_path, template.sha256):
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

    def parse(
        self,
        *,
        identity: DeviceIdentity,
        command: str,
        raw_text: str,
    ) -> ParserRuntimeResult:
        decision = self.policy.decide(
            vendor=identity.vendor,
            model=identity.model,
            family=identity.family,
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
                    structured_data=[
                        {
                            "raw_text_hash": hashlib.sha256(
                                str(raw_text or "").encode("utf-8")
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
        classification = self.output_classifier.classify(raw_text, decision=decision)
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
        template = self._resolve_template(
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
        if not self.asset_store.verify(template.asset_path, template.sha256):
            return self._failed(decision, identity, template, "asset_hash_mismatch", evidence)
        if textfsm is None:
            return self._failed(decision, identity, template, "textfsm_unavailable", evidence)
        spec = self._template_spec(template)
        try:
            content = Path(template.asset_path).read_text(encoding="utf-8")
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
            or any(record.get(field) in (None, "") for field in required)
            for record in records
        ):
            return self._failed(decision, identity, template, "required_fields_missing", evidence)
        if spec:
            key_result = validate_entity_keys(records, spec.primary_keys)
            if not key_result.valid:
                failure_code = (
                    "primary_key_duplicate" if key_result.duplicate_keys else "primary_key_invalid"
                )
                return self._failed(decision, identity, template, failure_code, evidence)
        if spec:
            for record in records:
                for field, validator in spec.validators.items():
                    value = record.get(field)
                    if value in (None, ""):
                        continue
                    if validator.get("type") == "percent":
                        try:
                            numeric = float(str(value).rstrip("%"))
                        except ValueError:
                            return self._failed(
                                decision, identity, template, "field_validation_failed", evidence
                            )
                        if numeric < 0 or numeric > 100:
                            return self._failed(
                                decision, identity, template, "field_validation_failed", evidence
                            )
                    if validator.get("type") in {"ip", "ip_or_unassigned"}:
                        text = str(value).strip()
                        if validator.get("type") == "ip_or_unassigned" and text.lower() in {
                            "unassigned",
                            "--",
                            "none",
                        }:
                            continue
                        try:
                            ipaddress.ip_interface(text)
                        except ValueError:
                            return self._failed(
                                decision,
                                identity,
                                template,
                                "field_validation_failed",
                                evidence,
                            )
                    if validator.get("type") == "interface_name":
                        if _looks_like_interface_header(value):
                            return self._failed(
                                decision,
                                identity,
                                template,
                                "field_validation_failed",
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

    @staticmethod
    def _template_spec(template) -> CategorySpec:
        validation = dict(getattr(template, "validation_json", None) or {})
        category = str(
            validation.get("category") or getattr(template, "category", None) or "record"
        )
        return CategorySpec(
            name=category,
            entity_type=str(validation.get("entity_type") or category),
            primary_keys=tuple(validation.get("primary_keys") or ()),
            required_fields=tuple(validation.get("required_fields") or ()),
            optional_fields=tuple(validation.get("optional_fields") or ()),
            validators=dict(validation.get("validators") or {}),
            allow_empty=bool(validation.get("allow_empty", False)),
            empty_patterns=tuple(validation.get("empty_patterns") or ()),
            ignore_fields=tuple(validation.get("ignore_fields") or ()),
        )

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

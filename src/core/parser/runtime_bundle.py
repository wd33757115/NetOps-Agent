"""Immutable Parser asset selection for one patrol run."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .identity import DeviceIdentity


@dataclass(frozen=True, slots=True)
class ParserTemplateRef:
    template_id: str
    version: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "template_id": self.template_id,
            "version": self.version,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class ParserRuntimeBundle:
    """Pinned Parser resolution for one device and command set."""

    vendor: str
    model: str
    family: str | None
    templates: dict[str, ParserTemplateRef | None]
    output_quality_profiles: dict[str, dict[str, Any]]
    digest: str

    @classmethod
    def build(
        cls,
        *,
        identity: DeviceIdentity,
        commands: list[str],
        runtime: Any | None = None,
    ) -> ParserRuntimeBundle:
        from .runtime import ParserRuntime

        runtime = runtime or ParserRuntime()
        templates: dict[str, ParserTemplateRef | None] = {}
        for command in commands:
            decision, resolved_template = runtime._effective_decision(
                identity=identity,
                command=command,
            )
            canonical = decision.canonical_command
            if not decision.enabled or identity.status != "known":
                templates.setdefault(canonical, None)
                continue
            template = resolved_template or runtime.registry.resolve_active(
                vendor=identity.vendor or "",
                model=identity.model or "",
                family=identity.family,
                command=canonical,
            )
            templates[canonical] = (
                ParserTemplateRef(
                    template_id=str(template.id),
                    version=str(template.version),
                    sha256=str(template.sha256),
                )
                if template is not None
                else None
            )
        payload = cls._payload(
            vendor=identity.vendor or "",
            model=identity.model or "",
            family=identity.family,
            templates=templates,
            output_quality_profiles={},
        )
        return cls(
            vendor=identity.vendor or "",
            model=identity.model or "",
            family=identity.family,
            templates=templates,
            output_quality_profiles={},
            digest=cls._digest(payload),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ParserRuntimeBundle:
        templates = {
            str(command): (
                ParserTemplateRef(
                    template_id=str(value["template_id"]),
                    version=str(value["version"]),
                    sha256=str(value["sha256"]),
                )
                if value
                else None
            )
            for command, value in dict(data.get("templates") or {}).items()
        }
        output_quality_profiles = {
            str(command): dict(value)
            for command, value in dict(
                data.get("output_quality_profiles") or {}
            ).items()
            if isinstance(value, dict)
        }
        payload = cls._payload(
            vendor=str(data.get("vendor") or ""),
            model=str(data.get("model") or ""),
            family=str(data["family"]) if data.get("family") else None,
            templates=templates,
            output_quality_profiles=output_quality_profiles,
        )
        digest = cls._digest(payload)
        supplied_digest = str(data.get("digest") or "")
        if supplied_digest and supplied_digest != digest:
            raise ValueError("Parser runtime bundle digest mismatch")
        return cls(
            vendor=payload["vendor"],
            model=payload["model"],
            family=payload["family"],
            templates=templates,
            output_quality_profiles=output_quality_profiles,
            digest=digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._payload(
                vendor=self.vendor,
                model=self.model,
                family=self.family,
                templates=self.templates,
                output_quality_profiles=self.output_quality_profiles,
            ),
            "digest": self.digest,
        }

    def resolve(self, *, canonical_command: str, registry: Any) -> Any | None:
        ref = self.templates.get(canonical_command)
        if ref is None:
            return None
        template = registry.get_template(ref.template_id)
        if template is None:
            raise RuntimeError(f"Pinned Parser template not found: {ref.template_id}")
        if str(template.version) != ref.version or str(template.sha256) != ref.sha256:
            raise RuntimeError(f"Pinned Parser template changed: {ref.template_id}")
        return template

    def output_quality_profile(
        self,
        canonical_command: str,
    ) -> dict[str, Any] | None:
        profile = self.output_quality_profiles.get(canonical_command)
        return dict(profile) if profile is not None else None

    @staticmethod
    def _payload(
        *,
        vendor: str,
        model: str,
        family: str | None,
        templates: dict[str, ParserTemplateRef | None],
        output_quality_profiles: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        payload = {
            "vendor": vendor,
            "model": model,
            "family": family,
            "templates": {
                command: ref.to_dict() if ref else None
                for command, ref in sorted(templates.items())
            },
        }
        if output_quality_profiles:
            payload["output_quality_profiles"] = {
                command: dict(profile)
                for command, profile in sorted(
                    output_quality_profiles.items()
                )
            }
        return payload

    @staticmethod
    def _digest(payload: dict[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

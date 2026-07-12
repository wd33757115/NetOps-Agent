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
            decision = runtime.policy.decide(
                vendor=identity.vendor,
                model=identity.model,
                family=identity.family,
                command=command,
            )
            canonical = decision.canonical_command
            if not decision.enabled or identity.status != "known":
                templates.setdefault(canonical, None)
                continue
            template = runtime.registry.resolve_active(
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
        )
        return cls(
            vendor=identity.vendor or "",
            model=identity.model or "",
            family=identity.family,
            templates=templates,
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
        payload = cls._payload(
            vendor=str(data.get("vendor") or ""),
            model=str(data.get("model") or ""),
            family=str(data["family"]) if data.get("family") else None,
            templates=templates,
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
            digest=digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._payload(
                vendor=self.vendor,
                model=self.model,
                family=self.family,
                templates=self.templates,
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

    @staticmethod
    def _payload(
        *,
        vendor: str,
        model: str,
        family: str | None,
        templates: dict[str, ParserTemplateRef | None],
    ) -> dict[str, Any]:
        return {
            "vendor": vendor,
            "model": model,
            "family": family,
            "templates": {
                command: ref.to_dict() if ref else None
                for command, ref in sorted(templates.items())
            },
        }

    @staticmethod
    def _digest(payload: dict[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

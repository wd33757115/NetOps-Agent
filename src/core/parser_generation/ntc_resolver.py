"""Resolve immutable ntc_templates candidates with objective execution evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.metadata import metadata, version
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml
from textfsm import clitable

from .samples import ParserSample
from .service import TemplateCandidate
from .validation import TextFSMValidationBackend, execute_template_candidate

DEFAULT_PLATFORM_MAP = Path(__file__).parent / "assets" / "ntc_platforms.v1.yaml"


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
                if not family:
                    family_required_but_missing = True
                    continue
                if family not in families:
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
        resolutions = tuple(
            self._resolve_platform(
                platform=platform,
                command=command,
                samples=samples,
                mapping=platform_decision,
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
        source = self.templates_dir / template_name
        try:
            template_text = source.read_text(encoding="utf-8")
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
        provenance = {
            "provider": "ntc_templates",
            "package_version": self.package_version,
            "source_template": template_name,
            "content_sha256": hashlib.sha256(
                template_text.encode("utf-8")
            ).hexdigest(),
            "platform": platform,
            "command": command,
            "license": self.license,
            "platform_mapping_version": self.platform_mapper.version,
            "platform_mapping_ids": list(mapping.mapping_ids),
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
                    "errors": list(sample.errors),
                }
                for sample in report.samples
            ],
            "engine_version": "textfsm",
        }
        evidence["digest"] = hashlib.sha256(
            json.dumps(
                evidence,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return NtcResolution(
            status="resolved" if report.passed else "execution_error",
            platform=platform,
            command=command,
            template_name=template_name,
            candidate=candidate,
            execution_evidence=evidence,
            error=None if report.passed else evidence["runtime_error"],
        )

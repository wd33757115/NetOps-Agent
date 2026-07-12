"""Approved-asset and generic-fingerprint device identity resolution."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .policy import ParserPolicy


@dataclass(frozen=True)
class DeviceIdentity:
    vendor: str | None
    model: str | None
    family: str | None
    confidence: float
    evidence_command: str | None = None
    evidence_text: str | None = None
    source: str = "unresolved"

    @property
    def detected(self) -> bool:
        return bool(self.vendor and self.model)

    @property
    def status(self) -> str:
        if not self.detected:
            return "unknown"
        return "known" if self.confidence >= 0.8 else "low_confidence"


VENDOR_FINGERPRINTS = (
    ("H3C", re.compile(r"(?i)\b(?:H3C|Comware)\b")),
    ("Cisco", re.compile(r"(?i)\b(?:Cisco IOS|NX-OS|Nexus Operating System)\b")),
    ("Huawei", re.compile(r"(?i)\b(?:Huawei|VRP \(R\) software)\b")),
)

GENERIC_MODEL_PATTERNS = (
    re.compile(r"(?im)^H3C\s+(?P<model>[A-Z0-9][A-Z0-9-]+)\s+uptime\b"),
    re.compile(r"(?im)^\s*cisco\s+Nexus9000\s+(?P<model>C[0-9A-Z-]+)\s+Chassis\b"),
    re.compile(r"(?im)^\s*cisco\s+(?P<model>(?:WS-C|ISR)[0-9A-Z/-]+)\s+\("),
    re.compile(r"(?im)^DEVICE_NAME\s*:\s*(?P<model>[A-Z0-9][A-Z0-9/-]+)\s*$"),
    re.compile(r"(?im)^PID:\s*(?P<model>[A-Z0-9][A-Z0-9/-]+)"),
    re.compile(r"(?im)^\s*(?:Model|Model number)\s*[:#]\s*(?P<model>[A-Z0-9][A-Z0-9/-]+)"),
)


class DeviceIdentityResolver:
    def __init__(self, *, repository=None, policy=None):
        if repository is None:
            from .runtime_repository import ParserRuntimeRepository

            repository = ParserRuntimeRepository()
        self.repository = repository
        self.policy = policy or ParserPolicy(repository=repository)

    @staticmethod
    def _normalize_model(model: str) -> str:
        value = model.strip().strip(",")
        if re.fullmatch(r"C(?:9364C|93180YC-FX)", value, re.IGNORECASE):
            value = f"N9K-{value.upper()}"
        return value.upper()

    def family_for(self, vendor: str, model: str) -> str | None:
        profile = self.repository.find_device_profile(vendor, model)
        if profile:
            return profile.get("family")
        upper = model.upper()
        if vendor.lower() == "h3c" and re.match(r"^(S|MSR)", upper):
            return "Comware7"
        if vendor.lower() == "cisco":
            if upper.startswith("N9K-"):
                return "NXOS9K"
            if upper.startswith("WS-C2960"):
                return "IOS-C2960"
            if upper.startswith("ISR"):
                return "IOS-XE-ISR"
        return None

    @staticmethod
    def _vendor_from_text(text: str) -> str | None:
        for vendor, pattern in VENDOR_FINGERPRINTS:
            if pattern.search(text):
                return vendor
        return None

    def resolve(
        self,
        *,
        blocks: Iterable[Any],
        path: str | Path | None = None,
        vendor: str | None = None,
        model: str | None = None,
    ) -> DeviceIdentity:
        if vendor and model:
            normalized = self._normalize_model(model)
            return DeviceIdentity(
                vendor=vendor.strip(),
                model=normalized,
                family=self.family_for(vendor.strip(), normalized),
                confidence=1.0,
                evidence_text="explicit parameters",
                source="explicit",
            )

        normalized_blocks = [
            (self.policy.normalize_command(block.command_canonical), block.raw_output)
            for block in blocks
        ]
        for signature in self.repository.list_identity_signatures():
            for command, output in normalized_blocks:
                if command != signature["evidence_command"]:
                    continue
                match = re.search(signature["pattern"], output, re.IGNORECASE | re.MULTILINE)
                if match:
                    return DeviceIdentity(
                        vendor=signature["vendor"],
                        model=signature["model"],
                        family=signature.get("family"),
                        confidence=float(signature.get("confidence") or 1.0),
                        evidence_command=command,
                        evidence_text=match.group(0).strip()[:500],
                        source="approved_signature",
                    )

        evidence_commands = {
            "display version",
            "show version",
            "display device manuinfo",
            "show inventory",
        }
        for command, output in normalized_blocks:
            if command not in evidence_commands:
                continue
            detected_vendor = vendor or self._vendor_from_text(output)
            for pattern in GENERIC_MODEL_PATTERNS:
                match = pattern.search(output)
                if not match or not detected_vendor:
                    continue
                normalized = self._normalize_model(match.group("model"))
                return DeviceIdentity(
                    vendor=detected_vendor,
                    model=normalized,
                    family=self.family_for(detected_vendor, normalized),
                    confidence=0.99,
                    evidence_command=command,
                    evidence_text=match.group(0).strip()[:500],
                    source="generic_fingerprint",
                )

        return DeviceIdentity(
            vendor=vendor,
            model=self._normalize_model(model) if model else None,
            family=None,
            confidence=0.0,
            evidence_text=Path(path).name if path else None,
        )

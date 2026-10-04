"""Fuse device version evidence with weak hints, without letting hints select a driver."""

import hashlib
import re
from dataclasses import asdict, dataclass

from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.detection import DiscoveryPlan

from . import families
from .normalize import parser_text

PROBES = ("display version", "show version")
REJECTED = re.compile(
    r"(?im)(unrecognized command|unknown command|invalid (?:command|input)|"
    r"incomplete command|wrong parameter|not supported|permission denied)"
)


@dataclass(frozen=True)
class Probe:
    command: str
    output: str
    complete: bool = True


@dataclass(frozen=True)
class Identity:
    vendor: str | None = None
    os_family: str | None = None
    profile_id: str | None = None
    command_style: str = "unknown"
    confidence: str = "low"
    status: str = "waiting_for_input"
    evidence: tuple = ()
    warnings: tuple = ()


def identify(probes=(), *, lldp_description="", ssh_banner="", prompt="", paging_style=""):
    probes = tuple(probes)
    evidence, own, hints, styles, warnings = [], {}, set(), set(), []
    incomplete = False
    signals = [
        ("lldp", lldp_description, None),
        ("ssh_banner", ssh_banner, None),
        ("prompt", prompt, None),
        ("paging", paging_style, None),
    ]
    for probe in probes:
        if probe.command not in PROBES:
            raise ValueError("unsupported identification command")
        signals.append(("version", probe.output, probe))
    for source, raw, probe in signals:
        if not raw:
            if probe and not probe.complete:
                incomplete = True
            continue
        if not isinstance(raw, str) or len(raw.encode()) > 256 * 1024:
            raise ValueError("invalid or oversized identification signal")
        normalized = parser_text(raw)
        matches = {
            row["id"]: row
            for row, pattern in families.signatures()
            if pattern.search(normalized.text)
        }
        rejected = bool(probe and REJECTED.search(normalized.text))
        usable = not probe or (probe.complete and not normalized.had_paging and not rejected)
        evidence.append(
            {
                "source": source,
                "command": probe.command if probe else None,
                "sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "excerpt": normalized.text[:256],
                "matches": sorted(matches),
                "usable": usable,
            }
        )
        if source == "version":
            if not probe.complete or normalized.had_paging:
                incomplete = True
            if usable:
                own.update(matches)
                if re.search(
                    r"(?i)\b(?:software\s+)?version\s*[:,]?\s*[vV]?[0-9]+\.", normalized.text
                ):
                    styles.add(probe.command.split()[0])
        elif source in {"lldp", "ssh_banner"}:
            hints.update(matches)
    if incomplete:
        return Identity(evidence=tuple(evidence), warnings=("incomplete_probe",))
    # An output may also carry another family's signature (IOS-XE prints an IOS line).
    for row in list(own.values()):
        for other in row.get("supersedes", []):
            own.pop(other, None)
    if len(own) > 1:
        return Identity(
            status="ambiguous",
            evidence=tuple(evidence),
            warnings=("conflicting_version_signatures",),
        )
    if own:
        family, row = next(iter(own.items()))
        if hints - {family, *row.get("supersedes", [])}:
            warnings.append("upstream_hint_disagrees")
        own_text = "\n".join(probe.output for probe in probes)
        vendor = row["vendor"]
        if row.get("vendor_pattern"):
            match = re.search(row["vendor_pattern"], own_text, re.I | re.M)
            vendor = match.group(1).upper() if match else None
        profile = row["profile_id"]
        if row.get("profile_requires") and not re.search(
            row["profile_requires"], own_text, re.I | re.M
        ):
            profile = None  # Family recognition does not prove major-version compatibility.
        return Identity(
            vendor,
            family,
            profile,
            row["command_style"],
            "high",
            "identified",
            tuple(evidence),
            tuple(warnings),
        )
    if len(styles) == 1:
        return Identity(
            command_style=next(iter(styles)),
            confidence="medium",
            status="generic",
            evidence=tuple(evidence),
            warnings=("no_matching_platform_signature",),
        )
    return Identity(evidence=tuple(evidence), warnings=("insufficient_device_evidence",))


class TopologyDiscoveryPlan(DiscoveryPlan):
    """Compatibility adapter: only existing LLDP profiles execute before P2-2/P2-8."""

    def __init__(self, profile_id="auto", **hints):
        self.identity = Identity()
        self.hints = hints
        self.probes = {}
        super().__init__(profile_id, identifier=self._identify_output)
        if self.source == "manual":
            self.identity = Identity(status="manual", warnings=("platform_not_auto_verified",))

    def _identify_output(self, output, command_id):
        command = {"detect-display": "display version", "detect-show": "show version"}[command_id]
        self.probes[command_id] = Probe(command, output)
        self.identity = identify(self.probes.values(), **self.hints)
        if self.identity.status == "ambiguous":
            raise DeviceAccessError("platform_ambiguous", "平台识别证据冲突，请人工确认")
        return families.discovery_profile(self.identity.os_family)

    def metadata(self):
        return {**super().metadata(), "identity": asdict(self.identity)}

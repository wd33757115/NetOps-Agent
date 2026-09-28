"""Fuse device version evidence with weak hints, without letting hints select a driver."""

import hashlib
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.detection import DiscoveryPlan

from .normalize import parser_text

PROBES = ("display version", "show version")
REJECTED = re.compile(
    r"(?im)(unrecognized command|unknown command|invalid (?:command|input)|"
    r"incomplete command|wrong parameter|not supported|permission denied)"
)


@lru_cache(maxsize=1)
def signatures():
    data = yaml.safe_load(Path(__file__).with_name("signatures.yaml").read_text(encoding="utf-8"))
    if data["schema_version"] != 1:
        raise ValueError("unknown signature schema")
    rows, seen = [], set()
    for row in data["families"]:
        if row["id"] in seen or row["command_style"] not in {"display", "show", "unknown"}:
            raise ValueError("invalid platform signature")
        seen.add(row["id"])
        rows.append((row, re.compile(row["pattern"], re.I | re.M | re.ASCII)))
    return tuple(rows)


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
            row["id"]: row for row, pattern in signatures() if pattern.search(normalized.text)
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
    # IOS-XE output commonly also contains an IOS Software line.
    if "iosxe" in own:
        own.pop("ios", None)
    if len(own) > 1:
        return Identity(
            status="ambiguous",
            evidence=tuple(evidence),
            warnings=("conflicting_version_signatures",),
        )
    if own:
        family, row = next(iter(own.items()))
        if hints - {family, "ios" if family == "iosxe" else family}:
            warnings.append("upstream_hint_disagrees")
        vendor = row["vendor"]
        if family == "comware":
            own_text = "\n".join(probe.output for probe in probes)
            match = re.search(r"(?im)^\s*(H3C|HPE|HP)\s+Comware", own_text)
            vendor = match.group(1).upper() if match else None
        profile = row["profile_id"]
        if family == "comware" and not re.search(
            r"(?im)^\s*(?:(?:H3C|HPE|HP)\s+)?Comware[^\n]*\bversion\s+7\.", own_text
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
        return {"comware": "comware-lldp-v1", "nxos": "nxos-lldp-v1"}.get(self.identity.os_family)

    def metadata(self):
        return {**super().metadata(), "identity": asdict(self.identity)}

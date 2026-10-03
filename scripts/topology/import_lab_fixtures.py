"""Copy an approved lab batch only after secret scanning; never print secret terms."""

import argparse
import getpass
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.scan_sensitive_device_data import scan_project_files  # noqa: E402
from scripts.topology.collect_lab_samples import CISCO_COMMANDS, COMMANDS, clean  # noqa: E402


@dataclass(frozen=True)
class Lab:
    batch: str
    devices: dict  # host -> fixture folder
    capabilities: dict  # host -> capabilities copied (others in the batch are left out)
    destination: Path
    unsupported: frozenset  # capabilities whose output is an unsupported-command message


BATCH = "20260928-163159"
DEVICES = {"10.10.1.1": "vsr-1", "10.10.1.2": "vsr-2"}
CAPABILITIES = frozenset(
    {
        "version",
        "inventory",
        "lldp_local",
        "lldp_verbose",
        "lldp_list",
        "interfaces",
        "ip_interfaces",
        "arp",
        "mac",
        "routes",
        "vlan",
        "lag",
        "irf",
        "vrfs",
    }
)
DESTINATION = ROOT / "tests/fixtures/topology/h3c/vsr1000-7.1.064"
VSR = Lab(
    BATCH,
    DEVICES,
    dict.fromkeys(DEVICES, CAPABILITIES),
    DESTINATION,
    frozenset({"inventory", "irf"}),
)

# Campus + WAN lab (scripts/topology/campus_wan): what the SIMAP builder and the
# planned VRRP/LAG facts read. Device configuration output is never copied.
CAMPUS_HOSTS = {
    f"10.10.1.{11 + i}": name
    for i, name in enumerate(("br1", "br2", "core1", "core2", "dist1", "dist2", "acc1", "acc2"))
}
COMWARE_FACTS = frozenset(
    {
        "version",
        "lldp_local",
        "lldp_verbose",
        "interfaces",
        "ip_interfaces",
        "arp",
        "mac",
        "routes",
        "lag",
        "vrrp",
    }
)
IOS_FACTS = frozenset(
    {
        "version",
        "lldp_verbose",
        "interfaces_description",
        "ip_interface",
        "arp",
        "mac",
        "routes",
        "vrrp",
    }
)
SWITCH_FACTS = IOS_FACTS | {"lag", "stp", "vlan", "trunk"}
CAMPUS = Lab(
    "20261003-010038",
    CAMPUS_HOSTS,
    {
        host: COMWARE_FACTS
        if name.startswith("br")
        else SWITCH_FACTS
        if name.startswith(("dist", "acc"))
        else IOS_FACTS
        for host, name in CAMPUS_HOSTS.items()
    },
    ROOT / "tests/fixtures/topology/campus-wan",
    frozenset(),
)
CAMPUS_VLAN = Lab(
    "20261003-014006",
    CAMPUS_HOSTS,
    {
        host: frozenset({"interfaces", "vlan", "trunk"})
        if name.startswith("br")
        else frozenset({"switchport", "vlan", "trunk"})
        for host, name in CAMPUS_HOSTS.items()
    },
    ROOT / "tests/fixtures/topology/campus-wan/vlan-trunk",
    frozenset(),
)
CAMPUS_OSPF = Lab(
    "20261003-015834",
    CAMPUS_HOSTS,
    dict.fromkeys(CAMPUS_HOSTS, frozenset({"ospf_interfaces", "ospf_process"})),
    ROOT / "tests/fixtures/topology/campus-wan/ospf",
    frozenset(),
)
# Protocol neighbours from the same capture as CAMPUS (OSPF neighbours, BGP peers).
CAMPUS_PROTOCOLS = Lab(
    "20261003-010038",
    CAMPUS_HOSTS,
    dict.fromkeys(CAMPUS_HOSTS, frozenset({"ospf", "bgp"})),
    ROOT / "tests/fixtures/topology/campus-wan/protocols",
    frozenset(),
)
# Per-VRF route tables ("show ip route vrf MGMT") from the IOS routers and distribution.
# ACC1/ACC2 were refused by the sampler (changed SSH host keys) and contribute nothing.
CAMPUS_VRF_ROUTES = Lab(
    "20261003-202324",
    {host: name for host, name in CAMPUS_HOSTS.items() if not name.startswith("br")},
    {
        host: frozenset({"routes_mgmt"}) if name.startswith(("core", "dist")) else frozenset()
        for host, name in CAMPUS_HOSTS.items()
        if not name.startswith("br")
    },
    ROOT / "tests/fixtures/topology/campus-wan/vrf-routes",
    frozenset(),
)
LABS = {
    "vsr1000": VSR,
    "campus-vrf-routes": CAMPUS_VRF_ROUTES,
    "campus-wan": CAMPUS,
    "campus-vlan": CAMPUS_VLAN,
    "campus-ospf": CAMPUS_OSPF,
    "campus-protocols": CAMPUS_PROTOCOLS,
}
SENSITIVE = re.compile(
    rb"(?im)^\s*(?:password\s+|secret\s+|community\s+|"
    rb"snmp-agent\s+community\b|-----BEGIN .*PRIVATE KEY-----)"
)


def command_of(capability, command):
    """The collector's own command for this capability, Comware or IOS."""
    return command in {dict(COMMANDS).get(capability), dict(CISCO_COMMANDS).get(capability)}


def prepare(source, secrets, lab=VSR):
    """Validate everything in memory before creating any destination files."""
    if not secrets or any(not secret for secret in secrets):
        raise ValueError("secret scan requires nonempty terms")
    scan = scan_project_files(project_root=source, terms=secrets)
    # Existing scanner skips NUL-containing files. Direct byte checks below cover them.
    if scan["match_count"] or scan["skipped_error_count"]:
        raise ValueError("sensitive data scan failed")
    source_meta = json.loads((source / "meta.json").read_text(encoding="utf-8"))
    if {item["host"] for item in source_meta} != set(lab.devices) or len(source_meta) != len(
        lab.devices
    ):
        raise ValueError("unexpected lab devices")
    files, observations = {}, []
    for device in source_meta:
        name, host = lab.devices[device["host"]], device["host"]
        wanted, seen = lab.capabilities[host], set()
        for entry in device["commands"]:
            capability = entry["id"]
            if capability in {"paging", "width"}:
                continue
            if lab is VSR and capability not in wanted:
                raise ValueError("unexpected or repeated capability")
            if capability not in wanted:
                continue
            if capability in seen:
                raise ValueError("unexpected or repeated capability")
            if not command_of(capability, entry["command"]):
                raise ValueError("command differs from approved lab collection")
            seen.add(capability)
            rejected = entry["status"] == "rejected_by_device"
            record = {
                "device": name,
                "host": host,
                "capability": capability,
                "command": entry["command"],
                "observed_at": entry["finished_at"],
                "transport_status": entry["status"],
                "expected_status": "unsupported"
                if capability in lab.unsupported or rejected
                else "complete",
                "files": {},
            }
            pair = {}
            for suffix in ("txt", "raw.txt"):
                path = source / host / f"{capability}.{suffix}"
                raw = path.read_bytes()
                if (
                    len(raw) > 1024 * 1024
                    or SENSITIVE.search(raw)
                    or any(secret.encode().lower() in raw.lower() for secret in secrets)
                ):
                    raise ValueError("sensitive or oversized fixture rejected")
                relative = f"{name}/{capability}.{suffix}"
                files[relative] = raw
                pair[suffix] = raw
                record["files"][suffix] = {
                    "path": relative,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw),
                }
            if clean(pair["raw.txt"].decode("utf-8"), entry["command"]).encode() != pair["txt"]:
                raise ValueError("raw and cleaned evidence disagree")
            observations.append(record)
        if seen != wanted:
            raise ValueError("incomplete capability set")
    manifest = {
        "schema_version": 1,
        "batch": lab.batch,
        "source": "GNS3 authorized lab capture",
        "raw_format": "UTF-8 text saved by collector before CLI cleanup; original line endings",
        "limitations": ["Not an arbitrary-encoding wire capture", "No initial login banner saved"],
        "secret_scan": {
            "literal_matches": 0,
            "sensitive_config_matches": 0,
            "scanner": "scripts/scan_sensitive_device_data.py + direct byte check",
        },
        "observations": observations,
    }
    files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab", choices=sorted(LABS), default="vsr1000")
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    lab = LABS[args.lab]
    source = args.source or ROOT / ".runtime/lab-samples" / lab.batch
    if source.name != lab.batch:
        raise ValueError("only the approved source batch may be imported")
    secret = os.getenv("NETOPS_LAB_PASSWORD") or getpass.getpass("Lab password for scan (hidden): ")
    files = prepare(source, [secret], lab)
    # Refuse a changed import, preserving already reviewed fixtures.
    for relative, content in files.items():
        path = lab.destination / relative
        if path.exists() and path.read_bytes() != content:
            raise ValueError("existing fixture differs; review the source batch")
    for relative, content in files.items():
        path = lab.destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    print(
        json.dumps(
            {
                "lab": args.lab,
                "files": len(files),
                "devices": len(lab.devices),
                "observations": len(files) // 2,
                "sensitive_matches": 0,
            }
        )
    )


if __name__ == "__main__":
    main()

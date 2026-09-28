"""Copy the approved lab batch only after secret scanning; never print secret terms."""

import argparse
import getpass
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.scan_sensitive_device_data import scan_project_files  # noqa: E402
from scripts.topology.collect_lab_samples import COMMANDS, clean  # noqa: E402

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
SENSITIVE = re.compile(
    rb"(?im)^\s*(?:password\s+|secret\s+|community\s+|"
    rb"snmp-agent\s+community\b|-----BEGIN .*PRIVATE KEY-----)"
)


def prepare(source, secrets):
    """Validate everything in memory before creating any destination files."""
    if not secrets or any(not secret for secret in secrets):
        raise ValueError("secret scan requires nonempty terms")
    scan = scan_project_files(project_root=source, terms=secrets)
    # Existing scanner skips NUL-containing files. Direct byte checks below cover them.
    if scan["match_count"] or scan["skipped_error_count"]:
        raise ValueError("sensitive data scan failed")
    source_meta = json.loads((source / "meta.json").read_text(encoding="utf-8"))
    if {item["host"] for item in source_meta} != set(DEVICES) or len(source_meta) != 2:
        raise ValueError("unexpected lab devices")
    files, observations = {}, []
    for device in source_meta:
        name, host = DEVICES[device["host"]], device["host"]
        seen = set()
        for entry in device["commands"]:
            capability = entry["id"]
            if capability == "paging":
                continue
            if capability not in CAPABILITIES or capability in seen:
                raise ValueError("unexpected or repeated capability")
            if entry["command"] != dict(COMMANDS)[capability]:
                raise ValueError("command differs from approved lab collection")
            seen.add(capability)
            record = {
                "device": name,
                "host": host,
                "capability": capability,
                "command": entry["command"],
                "observed_at": entry["finished_at"],
                "transport_status": entry["status"],
                "expected_status": "unsupported"
                if capability in {"inventory", "irf"}
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
        if seen != CAPABILITIES:
            raise ValueError("incomplete capability set")
    manifest = {
        "schema_version": 1,
        "batch": BATCH,
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
    parser.add_argument("--source", type=Path, default=ROOT / ".runtime/lab-samples" / BATCH)
    args = parser.parse_args()
    if args.source.name != BATCH:
        raise ValueError("only the approved source batch may be imported")
    secret = os.getenv("NETOPS_LAB_PASSWORD") or getpass.getpass("Lab password for scan (hidden): ")
    files = prepare(args.source, [secret])
    # Refuse a changed import, preserving already reviewed fixtures.
    for relative, content in files.items():
        path = DESTINATION / relative
        if path.exists() and path.read_bytes() != content:
            raise ValueError("existing fixture differs; review the source batch")
    for relative, content in files.items():
        path = DESTINATION / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    print(
        json.dumps(
            {
                "files": len(files),
                "devices": 2,
                "capabilities_per_device": 14,
                "sensitive_matches": 0,
            }
        )
    )


if __name__ == "__main__":
    main()

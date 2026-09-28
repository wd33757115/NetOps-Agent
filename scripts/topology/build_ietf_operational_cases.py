"""Build native operational mount and partial-observation validation fixtures.

Synthetic state timestamps are test values, never inferred from VSR captures.
The original VSR interface instances are copied byte-for-byte and remain partial.
"""

from __future__ import annotations

import argparse
import copy
import json

from build_ietf_model_package import ROOT, digest
from build_ietf_mount_cases import PACKAGE, extension_xml
from build_ietf_mount_cases import build as build_config

OUTPUT = ROOT / "models/network-twin/mount/operational"


def build() -> dict[str, bytes]:
    config = build_config()
    parent = json.loads(config["parent-library.json"])
    extension = json.loads(config["extension.json"])
    data = json.loads(config["valid.json"])
    namespaces = {
        entry["name"]: entry["namespace"]
        for lib in (
            parent,
            extension["ietf-network-instance:network-instances"]["network-instance"][0]["vrf-root"],
        )
        for entry in lib["ietf-yang-library:modules-state"]["module"]
    }
    selected = "/if:interfaces/if:interface[ni:bind-ni-name = current()/../ni:name]"
    extension["ietf-yang-schema-mount:schema-mounts"]["mount-point"][0]["shared-schema"][
        "parent-reference"
    ] += [selected + "/if:oper-status", selected + "/if:statistics"]
    data.update(copy.deepcopy(parent))
    for interface in data["ietf-interfaces:interfaces"]["interface"]:
        interface.update(
            {
                "oper-status": "up",
                "statistics": {"discontinuity-time": "2026-01-01T00:00:00Z", "in-octets": "42"},
            }
        )
    for ni, library_ni, port in zip(
        data["ietf-network-instance:network-instances"]["network-instance"],
        extension["ietf-network-instance:network-instances"]["network-instance"],
        ("eth0", "eth1"),
        strict=True,
    ):
        ni["vrf-root"].update(copy.deepcopy(library_ni["vrf-root"]))
        ni["vrf-root"]["ietf-routing:routing"]["control-plane-protocols"]["control-plane-protocol"][
            0
        ]["type"] = "static"
        ni["vrf-root"]["ietf-routing:routing"]["ribs"] = {
            "rib": [
                {
                    "name": "ipv4",
                    "address-family": "ietf-ipv4-unicast-routing:ipv4-unicast",
                    "routes": {
                        "route": [
                            {
                                "ietf-ipv4-unicast-routing:destination-prefix": "192.0.2.0/24",
                                "source-protocol": "static",
                                "route-preference": 1,
                                "next-hop": {"outgoing-interface": port},
                            }
                        ]
                    },
                }
            ],
        }
    output = {
        "parent-library.json": config["parent-library.json"],
        "extension.xml": extension_xml(extension, namespaces).encode(),
    }
    documents = {"complete.json": data}
    specs = []

    def case(name, file, mode, valid, diagnostic=None):
        specs.append(
            {
                "name": name,
                "data": file,
                "extension": "extension.xml",
                "type": mode,
                "expected_valid": valid,
                "diagnostic": diagnostic,
            }
        )

    case("complete-mounted-strict", "complete.json", "strict", True)
    case("complete-mounted-operational", "complete.json", "data", True)
    case("state-not-configuration", "complete.json", "config", False, "oper-status")
    partial = copy.deepcopy(data)
    partial["ietf-interfaces:interfaces"]["interface"][0].pop("statistics")
    documents["partial.json"] = partial
    case("missing-statistics-strict", "partial.json", "strict", False, "discontinuity-time")
    case(
        "missing-statistics-operational-warning",
        "partial.json",
        "data",
        True,
        'Mandatory node "discontinuity-time"',
    )
    case("missing-statistics-parse-only", "partial.json", "get", True)
    cross = copy.deepcopy(data)
    cross["ietf-network-instance:network-instances"]["network-instance"][0]["vrf-root"][
        "ietf-routing:routing"
    ]["ribs"]["rib"][0]["routes"]["route"][0]["next-hop"]["outgoing-interface"] = "eth1"
    documents["cross-vrf.json"] = cross
    case("state-cross-vrf-strict", "cross-vrf.json", "strict", False, "outgoing-interface")
    case(
        "state-cross-vrf-operational-rejected",
        "cross-vrf.json",
        "data",
        False,
        "outgoing-interface",
    )
    # Parse-only deliberately omits leafref instance resolution: this must never
    # be reported as complete semantic validation or accepted as a resolved route.
    case("state-cross-vrf-parse-only-unresolved", "cross-vrf.json", "get", True)

    sources = {}
    for name in ("vsr-1", "vsr-2"):
        file = f"{name}-interfaces.partial.json"
        source = ROOT / "models/network-twin/instances" / file
        output[file] = source.read_bytes()
        sources[source.relative_to(ROOT).as_posix()] = digest(output[file])
        case(f"{name}-strict-incomplete", file, "strict", False, "discontinuity-time")
        case(f"{name}-partial-observation", file, "get", True)
    observation = json.loads(output["vsr-1-interfaces.partial.json"])
    mutations = [
        ("invalid-status", "oper-status", "UNKNOWN", "oper-status"),
        ("custom-field", "project-confidence", 0.9, "project-confidence"),
        ("invalid-identity", "type", "iana-if-type:not-defined", "not-defined"),
        ("invalid-time", "statistics", {"discontinuity-time": "not-a-date"}, "discontinuity-time"),
        ("invalid-counter", "statistics", {"in-octets": "-1"}, "in-octets"),
        (
            "invalid-ip",
            "ietf-ip:ipv4",
            {"address": [{"ip": "999.1.1.1", "prefix-length": 24}]},
            "ip",
        ),
        (
            "invalid-prefix",
            "ietf-ip:ipv4",
            {"address": [{"ip": "10.10.1.1", "prefix-length": 33}]},
            "prefix-length",
        ),
    ]
    for name, field, value, diagnostic in mutations:
        tree = copy.deepcopy(observation)
        tree["ietf-interfaces:interfaces"]["interface"][0][field] = value
        file = name + ".json"
        documents[file] = tree
        case(name + "-parse-rejected", file, "get", False, diagnostic)
    tree = copy.deepcopy(observation)
    interface = tree["ietf-interfaces:interfaces"]["interface"][0]
    interface["discontinuity-time"] = "2026-01-01T00:00:00Z"
    documents["wrong-hierarchy.json"] = tree
    case("wrong-hierarchy-rejected", "wrong-hierarchy.json", "get", False, "discontinuity-time")
    for name, content in documents.items():
        output[name] = (json.dumps(content, indent=2) + "\n").encode()
    manifest = {
        "scope": "Offline native operational mount tests and partial VSR observations; no live I/O",
        "model_package_sha256": digest((PACKAGE / "manifest.json").read_bytes()),
        "observation_sources": sources,
        "synthetic_timestamp": "2026-01-01T00:00:00Z (test fixture only; not a device fact)",
        "parse_only_is_full_validation": False,
        "files": {name: digest(raw) for name, raw in sorted(output.items())},
        "cases": specs,
    }
    output["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = build()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if args.check and {p.name for p in OUTPUT.iterdir() if p.is_file()} != set(files):
        raise ValueError("Generated operational file set differs")
    for name, raw in files.items():
        if args.check:
            if (OUTPUT / name).read_bytes() != raw:
                raise ValueError(f"Generated operational case differs: {name}")
        else:
            (OUTPUT / name).write_bytes(raw)
    print(json.dumps({"files": len(files), "checked": args.check}))

"""Build libyang cases for the VSR ARP/VLAN/LLDP partial observations.

The VSR l2 instances are copied byte-for-byte. Yangson stops at the first
missing mandatory node, so libyang reports the complete missing set (strict),
resolves references while tolerating RFC 8342 section 5.3 gaps (data), and
checks types/hierarchy without references (get). Mutations are negative tests.
"""

from __future__ import annotations

import argparse
import copy
import json

from build_ietf_model_package import ROOT, digest
from build_ietf_mount_cases import PACKAGE, library

OUTPUT = ROOT / "models/network-twin/l2-cases"
INSTANCES = ROOT / "models/network-twin/instances"


def build() -> dict[str, bytes]:
    modules = {p.name: p.read_bytes() for p in (PACKAGE / "modules").glob("*.yang")}
    manifest = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
    if {name: digest(data) for name, data in modules.items()} != manifest["modules"]:
        raise ValueError("Model package bytes differ from the compiled manifest")
    roots = [
        "ietf-yang-library",
        "ietf-datastores",
        "iana-if-type",
        "ietf-interfaces",
        "ietf-ip",
        # LLDP management-address subtype uses ietf-routing identities; libyang only
        # resolves identity values from implemented modules.
        "ietf-routing",
        "ieee802-dot1q-bridge",
        "ieee802-dot1ab-lldp",
    ]
    output = {
        "parent-library.json": (json.dumps(library(modules, roots, "l2"), indent=2) + "\n").encode()
    }
    specs = []

    def case(name, file, mode, valid, diagnostic=None):
        specs.append(
            {
                "name": name,
                "data": file,
                "type": mode,
                "expected_valid": valid,
                "diagnostic": diagnostic,
            }
        )

    sources = {}
    for device in ("vsr-1", "vsr-2"):
        file = f"{device}-l2.partial.json"
        output[file] = (INSTANCES / file).read_bytes()
        sources[(INSTANCES / file).relative_to(ROOT).as_posix()] = digest(output[file])
        case(f"{device}-strict-missing-counters", file, "strict", False, "discontinuity-time")
        case(
            f"{device}-strict-missing-bridge-address",
            file,
            "strict",
            False,
            'Mandatory node "address"',
        )
        # Operational mode resolves LLDP port / VLAN member interface references and
        # reports the RFC 8342 section 5.3 mandatory gaps as warnings only.
        case(f"{device}-operational-references", file, "data", True, 'Mandatory node "address"')
        case(f"{device}-partial-observation", file, "get", True)

    observation = json.loads(output["vsr-1-l2.partial.json"])
    documents = {}

    def mutate(name, mode, diagnostic, change):
        tree = copy.deepcopy(observation)
        change(tree)
        documents[name + ".json"] = tree
        case(name + "-rejected", name + ".json", mode, False, diagnostic)

    interfaces = lambda t: t["ietf-interfaces:interfaces"]["interface"]  # noqa: E731
    vlan = lambda t: t["ieee802-dot1q-bridge:bridges"]["bridge"][0]["component"][0][  # noqa: E731
        "bridge-vlan"
    ]["vlan"][1]
    mutate(
        "lldp-port-unknown-interface",
        "data",
        "GE9/0",
        lambda t: t["ieee802-dot1ab-lldp:lldp"]["port"][0].update(name="GE9/0"),
    )
    mutate(
        "vlan-member-unknown-interface",
        "data",
        "GE9/0",
        lambda t: vlan(t).update({"egress-ports": ["GE9/0"]}),
    )
    mutate(
        "pvid-out-of-range",
        "get",
        "pvid",
        lambda t: interfaces(t)[2]["ieee802-dot1q-bridge:bridge-port"].update(pvid=4095),
    )
    # RFC 8342 section 5.3 lets operational data violate "when"; strict must reject it.
    mutate(
        "bridge-port-on-vlan-interface",
        "strict",
        "ianaif:ethernetCsmacd",
        lambda t: interfaces(t)[3].update(
            {
                "ieee802-dot1q-bridge:bridge-port": interfaces(t)[2][
                    "ieee802-dot1q-bridge:bridge-port"
                ]
            }
        ),
    )
    mutate(
        "neighbor-invalid-mac",
        "get",
        "link-layer-address",
        lambda t: interfaces(t)[0]["ietf-ip:ipv4"]["neighbor"][0].update(
            {"link-layer-address": "0c-d2-5b-c5-00-00"}
        ),
    )
    mutate(
        "lldp-dest-mac-dotted",
        "get",
        "dest-mac-address",
        lambda t: t["ieee802-dot1ab-lldp:lldp"]["port"][0].update(
            {"dest-mac-address": "0180-c200-000e"}
        ),
    )
    mutate(
        "lldp-management-address-dotted",
        "get",
        '"10.10.1.2" does not match',
        lambda t: t["ieee802-dot1ab-lldp:lldp"]["port"][0]["remote-systems-data"][0][
            "management-address"
        ][0].update(address="10.10.1.2"),
    )
    mutate(
        "custom-field",
        "get",
        "project-confidence",
        lambda t: interfaces(t)[0].update({"project-confidence": 0.9}),
    )
    for name, content in documents.items():
        output[name] = (json.dumps(content, indent=2) + "\n").encode()
    manifest = {
        "scope": "Offline VSR ARP/VLAN/LLDP partial observations; no live I/O",
        "model_package_sha256": digest((PACKAGE / "manifest.json").read_bytes()),
        "observation_sources": sources,
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
        raise ValueError("Generated l2 case file set differs")
    for name, raw in files.items():
        if args.check:
            if (OUTPUT / name).read_bytes() != raw:
                raise ValueError(f"Generated l2 case differs: {name}")
        else:
            (OUTPUT / name).write_bytes(raw)
    print(json.dumps({"files": len(files), "checked": args.check}))

"""Map one frozen lab capture to native YANG examples, not a production parser.

No device access, application imports, schema additions, or inferred defaults.
The report is provenance for these files, never part of a YANG instance.
"""

from __future__ import annotations

import argparse
import json
import re

from audit_ietf_models import ROOT, digest

FIXTURES = ROOT / "tests/fixtures/topology/h3c/vsr1000-7.1.064"
OUTPUT = ROOT / "models/network-twin/instances"


def render() -> dict[str, bytes]:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    if manifest["batch"] != "20260928-163159":
        raise ValueError("Only the reviewed capture batch is supported")
    observations = {}
    for obs in manifest["observations"]:
        for spec in obs["files"].values():
            data = (FIXTURES / spec["path"]).read_bytes()
            if digest(data) != spec["sha256"] or len(data) != spec["bytes"]:
                raise ValueError(f"Capture changed: {spec['path']}")
        observations[obs["device"], obs["capability"]] = obs

    def source(device, capability):
        obs = observations[device, capability]
        return (FIXTURES / obs["files"]["txt"]["path"]).read_text(encoding="utf-8")

    outputs = {}
    entries = []

    def save(filename, data, profile, sources, failure=None):
        raw = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()
        outputs[filename] = raw
        entries.append(
            {
                "file": filename,
                "sha256": digest(raw),
                "profile": profile,
                "expected_valid": failure is None,
                "expected_diagnostic": failure,
                "sources": sources,
            }
        )

    nodes = []
    elements = []
    chassis = {}
    omissions = []
    counts = {}
    for device in ("vsr-1", "vsr-2"):
        local = source(device, "lldp_local")
        chassis[device] = re.search(r"Chassis ID\s*:\s*(\S+)", local)[1]
        name = re.search(r"System name\s*:\s*(\S+)", local)[1]
        version = source(device, "version").splitlines()[0]
        if not version.startswith("H3C Comware Software, Version "):
            raise ValueError("Unreviewed software identification")
        elements.append(
            {
                "ne-id": f"urn:example:vsr-lab:{device}",
                "ne-type": "ietf-network-inventory-sw-ext:ne-virtual",
                "name": name,
                "mfg-name": "H3C",
                "product-name": "VSR1000",
                "software-rev": [{"name": "Comware", "revision": version.split("Version ", 1)[1]}],
            }
        )
        nodes.append(
            {
                "node-id": f"urn:example:vsr-lab:{device}",
                "ietf-l2-topology:l2-node-attributes": {"name": name},
                "ietf-network-topology:termination-point": [
                    {
                        "tp-id": f"urn:example:vsr-lab:ge{port}-0",
                        "ietf-l2-topology:l2-termination-point-attributes": {
                            "interface-name": f"GigabitEthernet{port}/0"
                        },
                    }
                    for port in (1, 2)
                ],
            }
        )
        interfaces = []
        for line in source(device, "ip_interfaces").splitlines():
            fields = line.split()
            if not fields or not re.fullmatch(r"GE\d+/\d+|Loop\d+|Vlan\d+", fields[0]):
                continue
            name, physical, _protocol, address, vpn, _description = fields
            if vpn != "--" or physical not in {"up", "down"}:
                raise ValueError("Unreviewed interface state or NI mapping")
            iftype = (
                "ethernetCsmacd"
                if name.startswith("GE")
                else ("softwareLoopback" if name.startswith("Loop") else "l3ipvlan")
            )
            interface = {"name": name, "type": f"iana-if-type:{iftype}", "oper-status": physical}
            if address != "--":
                ip, prefix = address.split("/")
                interface["ietf-ip:ipv4"] = {"address": [{"ip": ip, "prefix-length": int(prefix)}]}
            interfaces.append(interface)
        if len(interfaces) != 6:
            raise ValueError("IP-interface capture changed")
        save(
            f"{device}-interfaces.partial.json",
            {"ietf-interfaces:interfaces": {"interface": interfaces}},
            "device",
            [f"{device}/ip_interfaces.txt"],
            "missing-data: expected 'statistics'",
        )
        routes = []
        for line in source(device, "routes").splitlines():
            fields = line.split()
            if not fields or not re.fullmatch(r"[\d.]+/\d+", fields[0]):
                continue
            prefix, protocol, preference, cost, next_hop, interface = fields
            if protocol not in {"Direct", "O_INTRA"}:
                raise ValueError("Unreviewed route protocol")
            routes.append(
                {
                    "ietf-ipv4-unicast-routing:destination-prefix": prefix,
                    "source-protocol": "ietf-routing:direct"
                    if protocol == "Direct"
                    else "ietf-ospf:ospf",
                    "route-preference": int(preference),
                    "next-hop": {"ietf-ipv4-unicast-routing:next-hop-address": next_hop},
                }
            )
            omissions.append(
                {"device": device, "prefix": prefix, "cost": int(cost), "interface": interface}
            )
        expected_count = 18 if device == "vsr-1" else 19
        if len(routes) != expected_count:
            raise ValueError("Route count differs from reviewed experiment")
        save(
            f"{device}-routes.json",
            {
                "ietf-routing:routing": {
                    "ribs": {
                        "rib": [
                            {
                                "name": "captured-public-ipv4",
                                "address-family": "ietf-ipv4-unicast-routing:ipv4-unicast",
                                "routes": {"route": routes},
                            }
                        ]
                    }
                }
            },
            "device",
            [f"{device}/routes.txt"],
        )
        counts[device] = {"routes": len(routes), "partial_interfaces": len(interfaces)}

    links = []
    for device, peer in (("vsr-1", "vsr-2"), ("vsr-2", "vsr-1")):
        rows = [
            line.split()
            for line in source(device, "lldp_list").splitlines()
            if line.startswith("GE")
        ]
        if len(rows) != 2:
            raise ValueError("Unexpected LLDP adjacency count")
        for port, row in zip((1, 2), rows, strict=True):
            if row != [f"GE{port}/0", chassis[peer], f"GigabitEthernet{port}/0", "H3C"]:
                raise ValueError("LLDP identity or interface differs from reviewed sample")
            links.append(
                {
                    "link-id": f"urn:example:vsr-lab:lldp:{device}-to-{peer}:ge{port}-0",
                    "source": {
                        "source-node": f"urn:example:vsr-lab:{device}",
                        "source-tp": f"urn:example:vsr-lab:ge{port}-0",
                    },
                    "destination": {
                        "dest-node": f"urn:example:vsr-lab:{peer}",
                        "dest-tp": f"urn:example:vsr-lab:ge{port}-0",
                    },
                }
            )
    save(
        "topology-observed.json",
        {
            "ietf-network:networks": {
                "network": [
                    {
                        "network-id": "urn:example:vsr-lab:lldp-observed",
                        "network-types": {"ietf-l2-topology:l2-topology": {}},
                        "ietf-simap-topology:network-category": (
                            "ietf-simap-topology:network-category-live"
                        ),
                        "ietf-simap-topology:description": (
                            "Captured LLDP adjacency, not physical cabling; "
                            "shared media may be present."
                        ),
                        "node": nodes,
                        "ietf-network-topology:link": links,
                    }
                ]
            }
        },
        "topology",
        [f"{d}/{c}.txt" for d in chassis for c in ("lldp_local", "lldp_list")],
    )
    save(
        "inventory.json",
        {
            "ietf-network-inventory:network-inventory": {
                "network-elements": {"network-element": elements}
            }
        },
        "topology",
        [f"{d}/{c}.txt" for d in chassis for c in ("version", "lldp_local")],
    )
    report = {
        "scope": "Offline fixture projection, not a device datastore or a complete twin",
        "capture_batch": manifest["batch"],
        "capture_manifest_sha256": digest((FIXTURES / "manifest.json").read_bytes()),
        "entries": entries,
        "counts": counts,
        "unmapped_route_fields": omissions,
        "gaps": [
            "Interface statistics/discontinuity-time is absent; "
            "partial instances intentionally fail full validation.",
            "Six IP-interface rows per device only; "
            "bridge and special internal interfaces are not represented.",
            "RIB key is an example projection label, not a device-reported native YANG RIB name.",
            "Route outgoing-interface is omitted in standalone route subsets; "
            "no closed forwarding-path model.",
            "OSPF version is not inferred from O_INTRA; "
            "area-route type and metric remain in source evidence.",
            "No receive/blackhole next-hop semantics inferred "
            "from InLoop0, NULL0 or address alone.",
            "No default route synthesized; 0.0.0.0/32 is preserved unchanged.",
            "No default VRF synthesized; captured device has zero configured VPN instances.",
            "Virtual NE type comes from the documented GNS3 VSR lab deployment, not LLDP alone.",
            "No virtual node-to-inventory mapping claimed by reusing identical example keys.",
            "LLDP serial number is retained in capture; no invented physical component for the VM.",
            "Hub, unmanaged switches, ARP/MAC/VLAN, IPv6, counters "
            "and physical wiring remain outside these subsets.",
        ],
    }
    outputs["manifest.json"] = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Check tracked examples without rewriting"
    )
    args = parser.parse_args()
    files = render()
    if args.check:
        if {p.name for p in OUTPUT.glob("*.json")} != set(files):
            raise ValueError("Tracked instance file set differs from expected output")
        for name, data in files.items():
            if (OUTPUT / name).read_bytes() != data:
                raise ValueError(f"Tracked example differs: {name}")
    else:
        OUTPUT.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (OUTPUT / name).write_bytes(data)
    print(json.dumps({"files": len(files), "checked": args.check}))


if __name__ == "__main__":
    main()

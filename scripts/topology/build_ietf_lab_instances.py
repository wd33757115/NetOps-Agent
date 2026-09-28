"""Map one frozen lab capture to native YANG examples, not a production parser.

No device access, application imports, schema additions, or inferred defaults.
The report is provenance for these files, never part of a YANG instance.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys

from audit_ietf_models import ROOT, digest

FIXTURES = ROOT / "tests/fixtures/topology/h3c/vsr1000-7.1.064"
OUTPUT = ROOT / "models/network-twin/instances"


# IEEE 802.1AB-2016 Table 7-1 group address of the nearest-bridge agent.
NEAREST_BRIDGE = "01-80-C2-00-00-0E"
# IEEE 802.1AB capability names as printed by Comware, mapped to the native bits.
CAPABILITIES = {
    "Bridge": "bridge",
    "Router": "router",
    "Customer Bridge": "cvlan-component",
    "Service Bridge": "svlan-component",
}


BRIDGE = "captured-bridge"
COMPONENT = "captured-c-vlan-component"


def ieee_mac(dotted: str) -> str:
    """Convert Comware xxxx-xxxx-xxxx to the canonical IEEE hyphen form."""
    digits = dotted.replace("-", "")
    if not re.fullmatch(r"[0-9a-f]{12}", digits):
        raise ValueError(f"Unreviewed MAC format: {dotted}")
    return "-".join(digits[i : i + 2] for i in range(0, 12, 2)).upper()


def layer2(device, source, ip_interfaces):
    """Map ARP, access-port VLAN and LLDP local data; keep unmappable rows as evidence."""
    unmapped = []
    by_name = {i["name"]: i for i in ip_interfaces}
    selected = {}

    def interface(name):
        if name not in selected:
            base = by_name[name]
            selected[name] = {k: base[k] for k in ("name", "type", "oper-status")}
            if "ietf-ip:ipv4" in base:
                selected[name]["ietf-ip:ipv4"] = json.loads(json.dumps(base["ietf-ip:ipv4"]))
        return selected[name]

    subnets = {
        name: ipaddress.ip_interface(f"{a['ip']}/{a['prefix-length']}").network
        for name, i in by_name.items()
        for a in i.get("ietf-ip:ipv4", {}).get("address", [])
    }
    for line in source(device, "arp").splitlines():
        fields = line.split()
        if not fields or not re.fullmatch(r"[\d.]+", fields[0]):
            continue
        ip, mac, vlan, port, aging, kind = fields
        if kind != "D":
            raise ValueError("Unreviewed ARP entry type")
        if vlan == "--":
            owner = port
        else:
            owners = [n for n, net in subnets.items() if ipaddress.ip_address(ip) in net]
            if len(owners) != 1 or not owners[0].startswith("Vlan"):
                raise ValueError(f"ARP neighbor {ip} has no unique VLAN interface subnet")
            owner = owners[0]
        target = interface(owner)["ietf-ip:ipv4"].setdefault("neighbor", [])
        target.append(
            {
                "ip": ip,
                "link-layer-address": ieee_mac(mac).replace("-", ":").lower(),
                "origin": "dynamic",
            }
        )
        unmapped.append(
            {
                "device": device,
                "arp": ip,
                "aging_minutes": int(aging),
                "port": port,
                "vlan": vlan,
                "placed_on": owner,
            }
        )

    access = {}
    bridge_rows = source(device, "interfaces").split("interfaces in bridge mode:", 1)[1]
    for line in bridge_rows.splitlines():
        fields = line.split()
        if not fields or not re.fullmatch(r"GE\d+/\d+", fields[0]):
            continue
        name, link, _speed, _duplex, mode, pvid = fields[:6]
        if mode != "A" or link not in {"UP", "DOWN"}:
            raise ValueError("Unreviewed bridge-mode port")
        access[name] = int(pvid)
        selected[name] = {
            "name": name,
            "type": "iana-if-type:ethernetCsmacd",
            "oper-status": link.lower(),
            "ieee802-dot1q-bridge:bridge-port": {
                # 802.1Q gates pvid on a referenced non-D-bridge component.
                "bridge-name": BRIDGE,
                "component-name": COMPONENT,
                "pvid": int(pvid),
            },
        }
    vlans = []
    for line in source(device, "vlan").splitlines():
        match = re.fullmatch(r"(\d+)\s+(VLAN \d{4})\s*(\S*)\s*", line)
        if not match:
            continue
        vid, name, ports = int(match[1]), match[2], match[3].split(",") if match[3] else []
        if any(access.get(p) != vid for p in ports):
            raise ValueError("VLAN member is not a reviewed access port of this VLAN")
        vlan = {"vid": vid, "name": name}
        if ports:
            vlan["egress-ports"] = ports
            vlan["untagged-ports"] = ports
        vlans.append(vlan)
    mac_rows = [line.split() for line in source(device, "mac").splitlines() if line[:1].isdigit()]
    for mac, vid, state, port, aging in mac_rows:
        unmapped.append(
            {
                "device": device,
                "mac": ieee_mac(mac),
                "vlan": int(vid),
                "state": state,
                "port": port,
                "aging": aging,
                "reason": "filtering-entry needs database-id and bridge port number",
            }
        )

    def capabilities(text):
        return {
            kind: " ".join(
                CAPABILITIES[c.strip()]
                for c in re.search(rf"System capabilities {kind}\s*:\s*(.+)", text)[1].split(",")
            )
            for kind in ("supported", "enabled")
        }

    local = source(device, "lldp_local")
    caps = capabilities(local)
    ports = []
    for block in re.split(r"\nLLDP local-information of port ", local)[1:]:
        match = re.match(r"\d+\[GigabitEthernet(\d+/\d+)\]:", block)
        port_id = re.search(r"Port ID\s*:\s*(\S+)", block)[1]
        if re.search(r"Port ID type\s*:\s*(.+)", block)[1].strip() != "Interface name":
            raise ValueError("Unreviewed LLDP port ID subtype")
        name = f"GE{match[1]}"
        if name not in selected:
            interface(name)
        ports.append(
            {
                "name": name,
                "dest-mac-address": NEAREST_BRIDGE,
                "port-id-subtype": "interface-name",
                "port-id": port_id,
                "port-desc": re.search(r"Port description\s*:\s*(.+)", block)[1].strip(),
            }
        )
    by_port = {p["name"]: p for p in ports}
    for block in re.split(
        r"(?:^|\n)LLDP neighbor-information of port ", source(device, "lldp_verbose")
    )[1:]:
        name = "GE" + re.match(r"\d+\[GigabitEthernet(\d+/\d+)\]:", block)[1]

        def field(label, text=block):
            return re.search(rf"\n {label}\s*: *(.*)", text)[1].strip()

        if block.split("\n")[1] != "LLDP agent nearest-bridge:":
            raise ValueError("Unreviewed LLDP agent")
        if (field("Chassis type"), field("Port ID type")) != ("MAC address", "Interface name"):
            raise ValueError("Unreviewed LLDP remote ID subtypes")
        # P2-11 live check: 'Update time' stayed constant across rounds while uptime grew,
        # so it is sysUpTime at the last change, i.e. the time-mark (seconds as displayed).
        d, h, m, s = map(int, re.findall(r"\d+", field("Update time")))
        address = ipaddress.IPv4Address(field("Management address"))
        if (field("Management address type"), field("Management address interface type")) != (
            "IPv4",
            "IfIndex",
        ):
            raise ValueError("Unreviewed LLDP management address")
        remote = {
            "time-mark": (((d * 24 + h) * 60 + m) * 60 + s) * 100,
            "remote-index": int(field("LLDP neighbor index")),
            "chassis-id-subtype": "mac-address",
            "chassis-id": ieee_mac(field("Chassis ID")),
            "port-id-subtype": "interface-name",
            "port-id": field("Port ID"),
            "port-desc": field("Port description"),
            "system-name": field("System name"),
            "management-address": [
                {
                    "address-subtype": "ietf-routing:ipv4",
                    "address": address.packed.hex().upper(),
                    # LLDP-MIB ifIndex(2) is the value-2 enum renamed port-ref in YANG.
                    "if-subtype": "port-ref",
                    "if-id": int(field("Management address interface ID")),
                }
            ],
        }
        caps_remote = capabilities(block)
        remote["system-capabilities-supported"] = caps_remote["supported"]
        remote["system-capabilities-enabled"] = caps_remote["enabled"]
        by_port[name].setdefault("remote-systems-data", []).append(remote)
    tree = {
        "ietf-interfaces:interfaces": {"interface": [selected[n] for n in sorted(selected)]},
        "ieee802-dot1ab-lldp:lldp": {
            "local-system-data": {
                "chassis-id-subtype": "mac-address",
                "chassis-id": ieee_mac(re.search(r"Chassis ID\s*:\s*(\S+)", local)[1]),
                "system-name": re.search(r"System name\s*:\s*(\S+)", local)[1],
                "system-capabilities-supported": caps["supported"],
                "system-capabilities-enabled": caps["enabled"],
            },
            "port": ports,
        },
        "ieee802-dot1q-bridge:bridges": {
            "bridge": [
                {
                    "name": BRIDGE,
                    # RFC 7951 section 6.8: same-module identities use the simple form.
                    "bridge-type": "customer-vlan-bridge",
                    "component": [
                        {
                            "name": COMPONENT,
                            "type": "c-vlan-component",
                            "bridge-vlan": {"vlan": vlans},
                        }
                    ],
                }
            ]
        },
    }
    return tree, unmapped


LAB = "urn:example:vsr-lab:"
# Capability -> fixture file; parsed by the App parsers, built by the App builder.
TOPOLOGY_INPUTS = {
    "lldp_local": "lldp_local",
    "lldp": "lldp_verbose",
    "interfaces": "interfaces",
    "ip": "ip_interfaces",
    "arp": "arp",
    "mac": "mac",
}


def topology(source, devices):
    """One implementation: the App's parsers and SIMAP builder, never a second copy."""
    sys.path.insert(0, str(ROOT))
    from src.apps.topology.backend.collectors import parse as parsers
    from src.apps.topology.backend.model.builder import build_topology

    # Topology inputs only need the generic tier; later tiers are not installed here.
    parse = getattr(parsers, "parse_generic", parsers.parse)

    facts = {}
    for device in devices:
        entry = {"platform": "comware"}
        for capability, name in TOPOLOGY_INPUTS.items():
            result = parse({"os_family": "comware"}, capability, source(device, name))
            if result.status != "complete":
                raise ValueError(f"{device} {capability} did not parse completely")
            entry[capability] = result.summary if capability == "lldp_local" else result.records
        facts[device] = entry
    return build_topology(facts, LAB)


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

    elements = []
    chassis = {}
    omissions = []
    l2_unmapped = []
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
        l2, l2_omissions = layer2(device, source, interfaces)
        save(
            f"{device}-l2.partial.json",
            l2,
            "device",
            [f"{device}/{c}.txt" for c in ("interfaces", "arp", "vlan", "lldp_local", "mac")],
            "missing-data: expected 'statistics'",
        )
        l2_unmapped.extend(l2_omissions)
        counts[device] = {
            "routes": len(routes),
            "partial_interfaces": len(interfaces),
            "arp_neighbors": sum(
                len(i.get("ietf-ip:ipv4", {}).get("neighbor", []))
                for i in l2["ietf-interfaces:interfaces"]["interface"]
            ),
        }

    tree, topology_evidence = topology(source, ("vsr-1", "vsr-2"))
    save(
        "topology.json",
        tree,
        "topology",
        [f"{d}/{c}.txt" for d in chassis for c in ("lldp_local", "lldp_list", "arp", "mac")]
        + [f"{d}/{c}.txt" for d in chassis for c in ("interfaces", "ip_interfaces")],
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
        "unmapped_l2_fields": l2_unmapped,
        "topology_evidence": topology_evidence,
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
            "Hub, unmanaged switches, IPv6, counters and physical wiring "
            "remain outside these subsets.",
            "ARP rows on VLAN interfaces are placed on the unique L3 interface whose "
            "captured subnet contains the neighbor; the physical port stays in evidence.",
            "LLDP remote entries use Update time as time-mark: a P2-11 live check showed it "
            "constant across rounds while uptime grew (sysUpTime at change, second "
            "resolution as displayed).",
            "MAC filtering entries need database-id and a bridge port number; the CLI shows "
            "an interface name only, so the MAC table is not synthesized.",
            "Bridge address (the MAC STP bridge identifiers derive from) has no source: VSR "
            "rejects display stp; bridge/component keys are projection labels.",
            "Only the nearest-bridge LLDP agent is mapped; multi-line system descriptions and "
            "local management-address TLVs stay in evidence.",
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

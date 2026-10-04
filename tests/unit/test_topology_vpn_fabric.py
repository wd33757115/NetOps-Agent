"""VPN / EVPN / VXLAN fabric lab (implementation plan §8 M5): parsing and overlay facts.

Real captures of scripts/topology/vpn_fabric (batch 20261003-225812): an EVPN-VXLAN
fabric (IOL spines/leaves, an H3C VSR leaf) and an MPLS L3VPN (IOL PE/P, VSR PE).
"""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_topology_campus_wan import IDENTITY

from src.apps.topology.backend.collectors.parse import ParseResult, parse
from src.apps.topology.backend.model.overlay import egress_instances, prepare
from src.apps.topology.backend.model.path import trace
from src.apps.topology.backend.service import topology_observation

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/topology/vpn-fabric"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
DEVICES = {
    "spine1": ("10.10.1.21", "ios"),
    "spine2": ("10.10.1.22", "ios"),
    "leaf1": ("10.10.1.23", "ios"),
    "leaf2": ("10.10.1.24", "ios"),
    "leaf3": ("10.10.1.25", "comware"),
    "pe1": ("10.10.1.26", "ios"),
    "p": ("10.10.1.27", "ios"),
    "pe2": ("10.10.1.28", "comware"),
}
ADDRESS = {name: address for name, (address, _) in DEVICES.items()}
NAME = {address: name for name, address in ADDRESS.items()}
# Collector capability <- fixture file (the sampler's command ids), per platform.
FACTS = {
    "ios": {
        "version": "version",
        "lldp": "lldp_verbose",
        "interfaces": "interfaces_description",
        "ip": "ip_interface",
        "arp": "arp",
        "mac": "mac",
        "vlan": "vlan",
        "trunk": "trunk",
        "switchport": "switchport",
        "routes": "routes",
        "vrfs": "vrfs",
        "ospf_interfaces": "ospf_interfaces",
        "ospf_process": "ospf_process",
        "ospf_neighbors": "ospf",
        "bgp_peers": "bgp",
        "vrf_detail": "vrf_detail",
        "bgp_vpnv4": "bgp_vpnv4",
        "bgp_evpn": "bgp_evpn",
        "ldp_peers": "ldp",
        "mpls_lfib": "mpls_forwarding",
        "evpn_instances": "evpn_evi",
        "evpn_peers": "nve_peers",
        "vxlan_vnis": "nve_vni",
        "evpn_mac": "evpn_mac",
    },
    "comware": {
        "version": "version",
        "lldp_local": "lldp_local",
        "lldp": "lldp_verbose",
        "interfaces": "interfaces",
        "ip": "ip_interfaces",
        "arp": "arp",
        "mac": "mac",
        "routes": "routes",
        "vrfs": "vrfs",
        "ospf_interfaces": "ospf_interfaces",
        "ospf_process": "ospf_process",
        "ospf_neighbors": "ospf",
        "bgp_peers": "bgp",
        "bgp_vpnv4": "bgp_vpnv4",
        "bgp_evpn": "bgp_evpn",
        "ldp_peers": "ldp",
        "mpls_lfib": "mpls_lsp",
        "evpn_instances": "evpn_instances",
        "evpn_peers": "evpn_imet",
        "vxlan_vnis": "vsi",
        "tunnel_interfaces": "tunnel_interfaces",
        "evpn_mac": "evpn_mac",
    },
}
# Per-VRF commands, merged the way the collector merges its scopes.
SCOPED = {"ios": {"routes": "routes_vrf_", "arp": "arp_vrf_"}, "comware": {"routes": "routes_vrf_"}}
INSTANCE = {"comware": {"vrf_detail": "vrf_"}}


def _scoped(result, capability, scope, parsed):
    assert parsed.status == "complete", (capability, scope)
    for row in parsed.records:
        row = {**row, "vrf": scope}
        if "next_hops" in row:
            row["next_hops"] = [{**hop, "vrf": scope} for hop in row["next_hops"]]
        result.records.append(row)


def parsed(device):
    _, platform = DEVICES[device]
    identity = IDENTITY[platform]
    folder = FIXTURES / device
    out = {}
    for cap, file in FACTS[platform].items():
        path = folder / f"{file}.txt"
        if path.exists():
            out[cap] = parse(identity, cap, path.read_text("utf-8"))
    for cap, prefix in {**SCOPED.get(platform, {}), **INSTANCE.get(platform, {})}.items():
        if cap not in out:
            # An instance-only capability starts empty, as the collector's merge does.
            out[cap] = ParseResult(status="complete")
        for path in sorted(folder.glob(f"{prefix}*.txt")):
            if path.name.endswith(".raw.txt"):
                continue
            scope = path.stem[len(prefix) :]
            _scoped(out[cap], cap, scope, parse(identity, cap, path.read_text("utf-8")))
    return out


def observation(device):
    _, platform = DEVICES[device]
    identity = IDENTITY[platform]
    return topology_observation(
        SimpleNamespace(
            parsed=parsed(device),
            identity=identity,
            profile_id=identity.profile_id,
            mode="topology",
            driver="paramiko",
        )
    )


@pytest.fixture(scope="module")
def fabric():
    return {
        address: {**observation(name)["facts"], "platform": DEVICES[name][1]}
        for name, address in ADDRESS.items()
    }


def test_every_capability_parses_completely_or_is_a_verified_absence():
    unsupported = {
        (o["device"], o["capability"])
        for o in MANIFEST["observations"]
        if o["expected_status"] == "unsupported"
    }
    assert unsupported == {("pe2", "tunnel_interfaces")}  # no tunnel interface type yet
    for device, (_, platform) in DEVICES.items():
        for cap, result in parsed(device).items():
            if (device, FACTS[platform].get(cap, cap)) in unsupported:
                assert result.status == "unsupported", (device, cap)
                continue
            assert result.status == "complete", (device, cap, result.warnings[:3])
            assert not result.rejected_count, (device, cap)


def test_vrf_detail_reads_route_targets_per_family_on_both_platforms():
    pe1 = {r["vrf"]: r for r in parsed("pe1")["vrf_detail"].records}
    assert pe1["CUST-A"]["rd"] == "65200:1"
    assert pe1["CUST-A"]["route_targets"]["ipv4"] == {
        "import": ["65200:1", "65200:99"],
        "export": ["65200:1"],
    }
    assert pe1["MGMT"]["rd"] is None and pe1["CUST-A"]["interfaces"] == ["Ethernet1/0"]
    leaf2 = {r["vrf"]: r for r in parsed("leaf2")["vrf_detail"].records}
    # IOS-XE "stitching" targets are the ones the VRF uses with EVPN.
    assert leaf2["BLUE"]["route_targets"]["evpn"]["export"] == ["65100:50020"]
    pe2 = {r["vrf"]: r for r in parsed("pe2")["vrf_detail"].records}
    assert pe2["SHARED"]["interfaces"] == ["GigabitEthernet4/0"]
    assert pe2["SHARED"]["route_targets"]["ipv4"]["import"] == ["65200:99", "65200:1"]


def test_overlay_facts_match_the_lab_design():
    leaf1, leaf3 = parsed("leaf1"), parsed("leaf3")
    (evi,) = leaf1["evpn_instances"].records
    assert (evi["vni"], evi["vtep"], evi["peers"]) == (10010, "10.255.10.11", ["10.255.10.13"])
    tunnels = {r["destination"]: r["state"] for r in leaf3["tunnel_interfaces"].records}
    assert tunnels == {"10.255.10.11": "up", "10.255.10.12": "up"}
    vsis = {r["instance"]: (r["type"], r.get("vni")) for r in leaf3["vxlan_vnis"].records}
    assert vsis == {
        "Auto_L3VNI50020_920": ("l3", 50020),
        "BLUE-20": ("l2", 10021),
        "RED": ("l2", 10010),
    }
    ldp = {r["peer_lsr_id"]: r["state"] for r in parsed("p")["ldp_peers"].records}
    assert ldp == {"10.255.20.1": "operational", "10.255.20.3": "operational"}


def test_prepare_marks_vpn_tunnels_and_local_leaks(fabric):
    devices = prepare(fabric)

    def hops(name, vrf, prefix):
        (route,) = [
            r for r in devices[ADDRESS[name]]["routes"] if r["vrf"] == vrf and r["prefix"] == prefix
        ]
        return route["next_hops"]

    # PE1 CUST-A: a VPNv4 route to the remote PE, resolved in the default instance.
    (hop,) = hops("pe1", "CUST-A", "10.2.1.0/24")
    assert hop["vrf"] == "default"
    assert hop["tunnel"] == {
        "type": "mpls",
        "endpoint": "10.255.20.3",
        "import": ["65200:1", "65200:99"],
    }
    # PE2 CUST-A imports SHARED's subnet locally: the next hop resolves in SHARED.
    (hop,) = hops("pe2", "CUST-A", "10.99.1.0/24")
    assert hop["vrf"] == "SHARED" and "tunnel" not in hop
    # LEAF2 BLUE: symmetric IRB over the L3 VNI to LEAF3's VTEP.
    (hop,) = hops("leaf2", "BLUE", "192.168.20.0/24")
    assert hop["tunnel"] == {"type": "vxlan", "vni": 50020, "endpoint": "10.255.10.13"}
    assert hop["vrf"] == "default"
    (hop,) = hops("leaf3", "BLUE", "192.168.10.0/24")
    assert hop["tunnel"] == {"type": "vxlan", "vni": 50020, "endpoint": "10.255.10.12"}


def test_comware_arp_rows_take_the_instance_of_their_interface_or_vsi(fabric):
    devices = prepare(fabric)
    pe2 = {r["ip"]: r["vrf"] for r in devices[ADDRESS["pe2"]]["arp"]}
    assert pe2["10.2.1.10"] == "CUST-A" and pe2["10.99.1.10"] == "SHARED"
    assert pe2["10.20.23.1"] == "default"
    leaf3 = {r["ip"]: r["vrf"] for r in devices[ADDRESS["leaf3"]]["arp"]}
    assert leaf3["192.168.20.14"] == "BLUE"  # learned in VSI BLUE-20, gateway Vsi-interface20


def test_mpls_vpn_egress_is_the_originating_instance_not_a_local_import(fabric):
    devices = prepare(fabric)
    pe2 = devices[ADDRESS["pe2"]]
    tunnel = {"type": "mpls", "endpoint": "10.255.20.3", "import": ["65200:1", "65200:99"]}
    # SHARED also carries 10.2.1.0/24, but only as a BGP import from CUST-A.
    assert egress_instances(pe2, tunnel, "10.2.1.10") == ["CUST-A"]
    assert egress_instances(pe2, tunnel, "10.99.1.10") == ["SHARED"]
    # CUST-B imports neither PE2 instance's targets.
    assert egress_instances(pe2, {**tunnel, "import": ["65200:2"]}, "10.2.1.10") == []
    leaf3 = devices[ADDRESS["leaf3"]]
    assert egress_instances(leaf3, {"type": "vxlan", "vni": 50020}, "192.168.20.14") == ["BLUE"]


BASE = "urn:netops:topology:vpn-fabric:"
# Hosts (scripts/topology/vpn_fabric/README.md): A1/B1 both 10.1.1.10 in CUST-A/CUST-B.
A1, B1, A2, S2 = "10.1.1.10", "10.1.1.10", "10.2.1.10", "10.99.1.10"
H_R1, H_B2, H_B3 = "192.168.10.11", "192.168.10.12", "192.168.20.14"


def route(path):
    """[(device, instance, tunnel type or None)] of a traced path."""
    return [
        (
            NAME[hop["device"]["address"]],
            hop.get("vrf"),
            ((hop.get("route") or {}).get("tunnel") or {}).get("type"),
        )
        for hop in path["hops"]
    ]


def test_mpls_vpn_path_runs_in_the_underlay_between_the_customer_instances(fabric):
    result = trace(fabric, BASE, A1, A2, src_vrf="CUST-A", dst_vrf="CUST-A")
    (forward,) = result["forward"]["paths"]
    assert result["forward"]["status"] == result["reverse"]["status"] == "complete"
    assert route(forward) == [
        ("pe1", "CUST-A", "mpls"),
        ("p", "default", None),
        ("pe2", "CUST-A", None),
    ]
    assert forward["end"]["status"] == "delivered"
    (reverse,) = result["reverse"]["paths"]
    assert route(reverse)[0] == ("pe2", "CUST-A", "mpls")
    assert result["symmetric"] is True


def test_route_target_leak_reaches_the_shared_instance(fabric):
    result = trace(fabric, BASE, A1, S2, src_vrf="CUST-A", dst_vrf="SHARED")
    (forward,) = result["forward"]["paths"]
    assert route(forward)[-1] == ("pe2", "SHARED", None)
    assert result["forward"]["status"] == result["reverse"]["status"] == "complete"


def test_another_customer_with_the_same_address_is_isolated(fabric):
    result = trace(fabric, BASE, B1, S2, src_vrf="CUST-B", dst_vrf="SHARED")
    (forward,) = result["forward"]["paths"]
    assert route(forward) == [("pe1", "CUST-B", None)] and forward["end"]["status"] == "no_route"
    # The way back reaches 10.1.1.10 too, but A1 in CUST-A, not B1.
    (reverse,) = result["reverse"]["paths"]
    assert reverse["end"]["status"] == "other_instance"
    assert (reverse["end"]["instance"], reverse["end"]["expected"]) == ("CUST-A", "CUST-B")
    assert result["forward"]["status"] == result["reverse"]["status"] == "partial"


def test_symmetric_irb_crosses_the_l3_vni_over_both_spines(fabric):
    result = trace(fabric, BASE, H_B2, H_B3, src_vrf="BLUE", dst_vrf="BLUE")
    paths = result["forward"]["paths"]
    assert result["forward"]["status"] == "complete" and len(paths) == 2
    assert {tuple(route(path)) for path in paths} == {
        (("leaf2", "BLUE", "vxlan"), (spine, "default", None), ("leaf3", "BLUE", None))
        for spine in ("spine1", "spine2")
    }
    tunnels = {path["hops"][0]["route"]["tunnel"]["vni"] for path in paths}
    assert tunnels == {50020}


def test_tenants_sharing_a_subnet_are_not_given_a_path(fabric):
    # RED is a pure L2 VNI: no gateway proves where 192.168.10.11 sits, so the shared
    # subnet with BLUE's host is not a path.
    result = trace(fabric, BASE, H_R1, H_B2)
    assert result["forward"]["paths"] == []
    assert result["forward"]["gaps"] == [{"status": "same_subnet_unproven"}]
    assert trace(fabric, BASE, H_R1, H_B3, src_vrf="RED")["forward"]["status"] == "no_gateway"


def test_a_vxlan_tunnel_observed_down_ends_the_path(fabric):
    devices = copy.deepcopy(fabric)
    for row in devices[ADDRESS["leaf2"]]["evpn_peers"]:
        if row["peer"] == "10.255.10.13" and row["vni"] == 50020:
            row["state"] = "down"
    (path, *_) = trace(devices, BASE, H_B2, H_B3, src_vrf="BLUE")["forward"]["paths"]
    assert path["end"] == {"status": "tunnel_down", "endpoint": "10.255.10.13"}


def test_a_missing_label_for_the_remote_pe_is_a_gap_not_a_path(fabric):
    devices = copy.deepcopy(fabric)
    pe1 = devices[ADDRESS["pe1"]]
    pe1["mpls_lfib"] = [row for row in pe1["mpls_lfib"] if row["fec"] != "10.255.20.3/32"]
    result = trace(devices, BASE, A1, A2, src_vrf="CUST-A", dst_vrf="CUST-A")
    (forward,) = result["forward"]["paths"]
    assert {"status": "lsp_not_observed", "fec": "10.255.20.3"} in forward["hops"][0]["gaps"]
    assert result["forward"]["status"] == "partial"

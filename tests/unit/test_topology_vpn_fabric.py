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
from src.apps.topology.backend.model.builder import build_topology_layers, device_node_ids
from src.apps.topology.backend.model.device_control import project_device_control
from src.apps.topology.backend.model.overlay import egress_instances, prepare
from src.apps.topology.backend.model.path import trace
from src.apps.topology.backend.model.protocols import adjacencies, project_protocols
from src.apps.topology.backend.model.services import project_services, typed
from src.apps.topology.backend.service import topology_observation
from tests.unit.test_topology_path import without_policies

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
        address: without_policies({**observation(name)["facts"], "platform": DEVICES[name][1]})
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


# --------------------------------------------------------------------- standard projections


@pytest.fixture(scope="module")
def ids(fabric):
    return device_node_ids(fabric, BASE)


def device_of(ids, node_id):
    return next(NAME[k] for k, v in ids.items() if v == node_id)


def services(devices):
    return project_services(devices, BASE)


def l3_services(result):
    return result["l3nm"]["tree"]["ietf-l3vpn-ntw:l3vpn-ntw"]["vpn-services"]["vpn-service"]


def l2_services(result):
    return result["l2nm"]["tree"]["ietf-l2vpn-ntw:l2vpn-ntw"]["vpn-services"]["vpn-service"]


def by_name(service_list, ids):
    """{vpn-name or vpn-id: (service, {device name: vpn-node})}."""
    return {
        service.get("vpn-name", service["vpn-id"]): (
            service,
            {device_of(ids, n["ne-id"]): n for n in service["vpn-nodes"]["vpn-node"]},
        )
        for service in service_list
    }


@pytest.fixture(scope="module")
def projected(fabric):
    return services(fabric)


def test_cli_route_targets_take_their_rfc_8294_type():
    assert typed("65200:1") == "0:65200:1"
    assert typed("10.255.10.12:2") == "1:10.255.10.12:2"
    assert typed("4200000000:7") == "2:4200000000:7"
    assert typed("65200:4294967296") is None  # no type holds it
    assert typed("1.10:5") is None  # asdot is not read
    assert typed("10.0.0.1:70000") is None


def test_l3vpn_services_are_the_instances_sharing_an_exported_target(projected, ids):
    l3 = by_name(l3_services(projected), ids)
    assert {name: sorted(nodes) for name, (_, nodes) in l3.items()} == {
        "BLUE": ["leaf2", "leaf3"],
        "CUST-A": ["pe1", "pe2"],
        "CUST-B": ["pe1"],
        "SHARED": ["pe2"],
    }
    service, nodes = l3["CUST-A"]
    assert service["vpn-id"] == BASE + "l3vpn:0:65200:1"
    assert service["vpn-type"] == "ietf-vpn-common:l3vpn"
    assert service["vpn-service-topology"] == "ietf-vpn-common:any-to-any"
    assert service["underlay-transport"] == {"protocol": ["ietf-vpn-common:ldp"]}
    pe2 = nodes["pe2"]
    assert pe2["vpn-node-id"] == ids[ADDRESS["pe2"]] + ":ni:CUST-A"
    assert (pe2["local-as"], pe2["router-id"]) == (65200, "10.255.20.3")
    (profile,) = pe2["active-vpn-instance-profiles"]["vpn-instance-profile"]
    assert profile["rd"] == "0:65200:11"
    ipv4 = next(
        a for a in profile["address-family"] if a["address-family"] == "ietf-vpn-common:ipv4"
    )
    assert [
        (t["route-target-type"], [r["route-target"] for r in t["route-targets"]])
        for t in ipv4["vpn-targets"]["vpn-target"]
    ] == [("both", ["0:65200:1"]), ("import", ["0:65200:99"])]
    (access,) = pe2["vpn-network-accesses"]["vpn-network-access"]
    assert access["ip-connection"] == {"ipv4": {"local-address": "10.2.1.1", "prefix-length": 24}}
    assert access["status"]["oper-status"] == {"status": "ietf-vpn-common:op-up"}
    # The management VRF has no route target: a local instance, not a VPN.
    assert "MGMT" not in l3


def test_shared_services_leak_is_a_relation_not_a_merged_service(projected):
    relations = {
        (r["service"].rsplit(":", 1)[1], r["imports_from"].rsplit(":", 1)[1])
        for r in projected["l3nm"]["relations"]
    }
    assert relations == {("1", "99"), ("99", "1")}


def test_evpn_ip_vrf_is_carried_over_vxlan_and_its_l3_vni_is_no_access(projected, ids):
    service, nodes = by_name(l3_services(projected), ids)["BLUE"]
    assert service["underlay-transport"] == {"protocol": ["ietf-vpn-common:vxlan-trans"]}
    accesses = {
        name: [
            (a["id"], a.get("vpn-network-access-type"))
            for a in node["vpn-network-accesses"]["vpn-network-access"]
        ]
        for name, node in nodes.items()
    }
    assert accesses == {
        "leaf2": [("Vlan20", "ietf-vpn-common:irb")],
        "leaf3": [("Vsi-interface20", "ietf-vpn-common:irb")],
    }


def test_evpn_instances_join_across_vendors_by_their_common_target(projected, ids):
    l2 = by_name(l2_services(projected), ids)
    # IOS EVI 10 and the Comware VSI RED share 65100:10010; the local names differ.
    service, nodes = l2[BASE + "l2vpn:0:65100:10"]
    assert sorted(nodes) == ["leaf1", "leaf3"]
    assert service["vpn-type"] == "ietf-vpn-common:vxlan-evpn"
    assert service["bgp-ad-enabled"] is True
    assert service["vpn-service-topology"] == "ietf-vpn-common:any-to-any"
    assert nodes["leaf3"]["bgp-auto-discovery"]["rd"] == "1:10.255.10.13:10"
    accesses = nodes["leaf1"]["vpn-network-accesses"]["vpn-network-access"]
    assert [a["id"] for a in accesses] == ["Ethernet1/0"]
    # VNI 10020 (LEAF2) and 10021 (LEAF3) are different segments, joined only at L3.
    assert {"20", "BLUE-20"} <= set(l2)


def test_evpn_targets_without_a_bgp_evpn_session_are_a_gap(fabric):
    devices = copy.deepcopy(fabric)
    devices[ADDRESS["leaf1"]]["bgp_evpn"]["peers"] = []
    result = services(devices)
    service = next(s for s in l2_services(result) if s["vpn-id"] == BASE + "l2vpn:0:65100:10")
    assert "bgp-ad-enabled" not in service
    assert all("bgp-auto-discovery" not in n for n in service["vpn-nodes"]["vpn-node"])
    gap = {"code": "bgp_auto_discovery_not_proven", "device": ADDRESS["leaf1"], "instance": "10"}
    assert gap in result["l2nm"]["gaps"]


def overlays(devices):
    tree, evidence, _, gaps = build_topology_layers(devices, BASE)
    networks = {
        n["network-id"].rsplit(":", 1)[1]: n for n in tree["ietf-network:networks"]["network"]
    }
    return tree, networks, evidence, gaps


def test_vxlan_network_links_vteps_per_vni_over_their_loopbacks(fabric, ids):
    _, networks, evidence, gaps = overlays(fabric)
    assert list(networks) == ["l2", "l3", "vxlan", "fabric"] and gaps == []
    vxlan = networks["vxlan"]
    assert vxlan["supporting-network"] == [{"network-ref": BASE + "l3"}]
    nodes = {device_of(ids, n["node-id"]): n for n in vxlan["node"]}
    assert sorted(nodes) == ["leaf1", "leaf2", "leaf3"]
    tp = nodes["leaf1"]["ietf-network-topology:termination-point"][0]
    assert tp["ietf-l2-topology:l2-termination-point-attributes"] == {
        "encapsulation-type": "ietf-l2-topology:vxlan",
        "vxlan": {"vni-id": 10010},
    }
    assert tp["supporting-termination-point"] == [
        {
            "network-ref": BASE + "l3",
            "node-ref": ids[ADDRESS["leaf1"]],
            "tp-ref": BASE + "if:Loopback0",
        }
    ]
    links = {
        tuple(
            sorted(
                (device_of(ids, t["node-ref"]), t["tp-ref"].rsplit(":", 1)[1])
                for t in link["ietf-simap-topology:tp"]
            )
        ): evidence[link["link-id"]]
        for link in vxlan["ietf-network-topology:link"]
    }
    assert set(links) == {
        (("leaf1", "10010"), ("leaf3", "10010")),
        (("leaf2", "50020"), ("leaf3", "50020")),
    }
    # Seen from both VTEPs (IOS nve peer, Comware tunnel bound to the VSI).
    assert len(links[(("leaf1", "10010"), ("leaf3", "10010"))]) == 2


def test_a_vxlan_peer_outside_the_collection_is_a_gap_not_a_link(fabric):
    devices = {k: v for k, v in fabric.items() if k != ADDRESS["leaf1"]}
    _, networks, _, gaps = overlays(devices)
    gap = {
        "code": "vtep_outside_collection",
        "device": ADDRESS["leaf3"],
        "vni": 10010,
        "peer": "10.255.10.11",
    }
    assert gap in gaps
    assert all(
        not t["tp-ref"].endswith(":10010")
        for link in networks["vxlan"]["ietf-network-topology:link"]
        for t in link["ietf-simap-topology:tp"]
    )


def test_fabric_is_the_evpn_speakers_with_vteps_spines_and_leaves(fabric, ids):
    _, networks, _, _ = overlays(fabric)
    (node,) = networks["fabric"]["node"]
    attributes = node["ietf-dc-fabric-topology:fabric-attributes"]
    assert attributes["fabric-id"] == node["node-id"] == BASE + "fabric:10.255.10.1"
    assert attributes["type"] == "ietf-dc-fabric-types:vxlan-fabric"
    roles = {
        device_of(ids, d["device-ref"].split("node-id='")[1][:-2]): d["role"]
        for d in attributes["device-nodes"]
    }
    assert roles == {
        "spine1": ["ietf-dc-fabric-types:spine"],
        "spine2": ["ietf-dc-fabric-types:spine"],
        "leaf1": ["ietf-dc-fabric-types:leaf"],
        "leaf2": ["ietf-dc-fabric-types:leaf"],
        "leaf3": ["ietf-dc-fabric-types:leaf"],
    }
    assert len(attributes["device-links"]) == 6  # each leaf to each spine
    # The MPLS core speaks no EVPN: no fabric without VTEPs.
    core = {ADDRESS[n]: fabric[ADDRESS[n]] for n in ("pe1", "p", "pe2")}
    assert "fabric" not in overlays(core)[1]


def test_overlay_networks_are_not_ports_and_cables(fabric):
    from src.apps.topology.backend.model.snapshot_state import _l2

    tree, *_ = overlays(fabric)
    assert [n["network-id"] for n in _l2(tree)] == [BASE + "l2"]


def test_overlay_export_validates_against_the_frozen_modules(fabric):
    yangson = pytest.importorskip("yangson", reason="Install tests/topology-requirements.txt")
    content = pytest.importorskip("yangson.enumerations").ContentType.all
    yang = Path(__file__).resolve().parents[2] / "src/apps/topology/resources/yang"
    model = yangson.DataModel((yang / "library.json").read_text(), [str(yang)])
    tree, *_ = overlays(fabric)
    model.from_raw(tree).validate(ctype=content)
    # L3NM/L2NM: yangson ignores the when conditions above their mandatory leaves and
    # cannot compile L3NM's leafrefs; libyang validates them in the M1 cases.


def bgp_instance(facts):
    (bgp,) = [
        i for i in project_protocols(facts)["instances"]["default"] if i["type"] == "ietf-bgp:bgp"
    ]
    return bgp["ietf-bgp:bgp"]


def test_vpn_routers_run_bgp_in_the_families_they_show(fabric):
    # The IPv4 unicast table is empty on every router; the VPN families carry BGP.
    pe1 = bgp_instance(fabric[ADDRESS["pe1"]])
    assert pe1["global"] == {
        "as": 65200,
        "identifier": "10.255.20.1",
        "afi-safis": {"afi-safi": [{"name": "iana-bgp-afi-safi-types:l3vpn-ipv4-unicast"}]},
    }
    (neighbour,) = pe1["neighbors"]["neighbor"]
    assert (neighbour["remote-address"], neighbour["peer-type"]) == ("10.255.20.3", "internal")
    assert neighbour["afi-safis"]["afi-safi"] == [
        {"name": "iana-bgp-afi-safi-types:l3vpn-ipv4-unicast", "active": True}
    ]
    spine1 = bgp_instance(fabric[ADDRESS["spine1"]])
    assert [n["remote-address"] for n in spine1["neighbors"]["neighbor"]] == [
        "10.255.10.11",
        "10.255.10.12",
        "10.255.10.13",
    ]


def test_evpn_sessions_pair_the_leaves_with_both_route_reflectors(fabric):
    sessions = {
        tuple(sorted((NAME[x["a"]["device"]], NAME[x["b"]["device"]]))): (
            x["status"],
            x["afi_safis"],
        )
        for x in adjacencies(fabric)["bgp"]
        if "b" in x
    }
    evpn = ("established", ["l2vpn-evpn"])
    assert sessions == {
        ("leaf1", "spine1"): evpn,
        ("leaf1", "spine2"): evpn,
        ("leaf2", "spine1"): evpn,
        ("leaf2", "spine2"): evpn,
        ("leaf3", "spine1"): evpn,
        ("leaf3", "spine2"): evpn,
        ("pe1", "pe2"): ("established", ["l3vpn-ipv4-unicast"]),
    }


def default_routing(device):
    outcome = observation(device)
    data = {k: outcome[k] for k in ("facts", "capabilities", "fact_metadata", "platform")}
    (rib,) = [r for r in project_device_control({"data": data})["routing"] if r["vrf"] == "default"]
    return rib


def test_ldp_sessions_and_hello_adjacencies_with_mpls_interfaces():
    rib = default_routing("p")
    routing = rib["tree"]["ietf-routing:routing"]
    (ldp,) = [
        c
        for c in routing["control-plane-protocols"]["control-plane-protocol"]
        if c["type"] == "ietf-mpls-ldp:mpls-ldp"
    ]
    body = ldp["ietf-mpls-ldp:mpls-ldp"]
    assert body["global"] == {"lsr-id": "10.255.20.2"}
    assert body["peers"]["peer"] == [
        {"lsr-id": "10.255.20.1", "label-space-id": 0, "session-state": "operational"},
        {"lsr-id": "10.255.20.3", "label-space-id": 0, "session-state": "operational"},
    ]
    adjacencies = {
        i["name"]: [
            (a["adjacent-address"], a["peer"]["lsr-id"])
            for a in i["address-families"]["ipv4"]["hello-adjacencies"]["hello-adjacency"]
        ]
        for i in body["discovery"]["interfaces"]["interface"]
    }
    assert adjacencies == {
        "Ethernet0/1": [("10.20.12.1", "10.255.20.1")],
        "Ethernet0/2": [("10.20.23.2", "10.255.20.3")],
    }
    assert routing["ietf-mpls:mpls"]["interfaces"]["interface"] == [
        {"name": "Ethernet0/1", "mpls-enabled": True},
        {"name": "Ethernet0/2", "mpls-enabled": True},
    ]
    # An LDP interface must carry IPv4 in the same tree (RFC 9070 must).
    copied = {i["name"]: i for i in rib["tree"]["ietf-interfaces:interfaces"]["interface"]}
    assert copied["Ethernet0/1"]["ietf-ip:ipv4"]["address"] == [
        {"ip": "10.20.12.2", "prefix-length": 30}
    ]


def test_comware_ldp_without_discovery_sources_still_names_its_peer():
    routing = default_routing("pe2")["tree"]["ietf-routing:routing"]
    (ldp,) = [
        c["ietf-mpls-ldp:mpls-ldp"]
        for c in routing["control-plane-protocols"]["control-plane-protocol"]
        if c["type"] == "ietf-mpls-ldp:mpls-ldp"
    ]
    assert "discovery" not in ldp and "global" not in ldp
    assert ldp["peers"]["peer"] == [
        {"lsr-id": "10.255.20.2", "label-space-id": 0, "session-state": "operational"}
    ]
    # Labels pushed out of GE2/0 prove MPLS there.
    assert routing["ietf-mpls:mpls"]["interfaces"]["interface"] == [
        {"name": "GigabitEthernet2/0", "mpls-enabled": True}
    ]

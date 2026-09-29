from pathlib import Path

import pytest

from src.apps.topology.backend.collectors.parse import parse
from src.apps.topology.backend.model import path as paths
from src.apps.topology.backend.model.path import trace

LAB = Path(__file__).resolve().parents[1] / "fixtures/topology/h3c/vsr1000-7.1.064"
FILES = {
    "lldp_local": "lldp_local",
    "lldp": "lldp_verbose",
    "interfaces": "interfaces",
    "ip": "ip_interfaces",
    "arp": "arp",
    "mac": "mac",
    "routes": "routes",
}
BASE = "urn:netops:topology:test:"


def lab():
    devices = {}
    for address, name in (("10.10.1.1", "vsr-1"), ("10.10.1.2", "vsr-2")):
        facts = {"platform": "comware"}
        for cap, file in FILES.items():
            parsed = parse(
                {"os_family": "comware"},
                cap,
                (LAB / name / f"{file}.txt").read_text(encoding="utf-8"),
            )
            assert parsed.status == "complete", (name, cap)
            facts[cap] = parsed.summary if cap == "lldp_local" else parsed.records
        devices[address] = facts
    return devices


def hops(path):
    return [
        (h["device"]["address"], h["in_interface"], h.get("out_interface")) for h in path["hops"]
    ]


def test_lab_hosts_route_across_both_routers_and_back():
    result = trace(lab(), BASE, "1.1.1.2", "192.168.1.2")
    forward, reverse = result["forward"], result["reverse"]
    assert forward["status"] == reverse["status"] == "complete"
    assert forward["first_hop"] == reverse["first_hop"] == "single"
    [path] = forward["paths"]
    assert hops(path) == [
        ("10.10.1.2", "Vlan-interface100", "GigabitEthernet2/0"),
        ("10.10.1.1", "GigabitEthernet2/0", "Vlan-interface200"),
    ]
    first, last = path["hops"]
    assert first["route"] == {
        "prefix": "192.168.1.0/24",
        "protocol": "O_INTRA",
        "preference": 10,
        "metric": 2,
        "next_hop": "2.2.2.2",
    }
    # The routed hop names the observed L2 link between the two GigabitEthernet2/0 ports.
    assert first["link_id"].startswith(BASE + "l2-link:")
    assert last["route"]["protocol"] == "Direct"
    assert path["end"] == {"status": "delivered", "to": "subnet", "interface": "Vlan-interface200"}
    assert hops(reverse["paths"][0]) == [
        ("10.10.1.1", "Vlan-interface200", "GigabitEthernet2/0"),
        ("10.10.1.2", "GigabitEthernet2/0", "Vlan-interface100"),
    ]
    assert result["symmetric"] is True and result["gaps"] == []
    assert result["destination"]["status"] == "confirmed"


def test_lab_device_address_and_same_subnet_need_no_route_lookup():
    to_loopback = trace(lab(), BASE, "192.168.1.2", "4.4.4.4")["forward"]["paths"][0]
    assert to_loopback["end"] == {"status": "delivered", "to": "device", "interface": "LoopBack0"}
    same = trace(lab(), BASE, "1.1.1.2", "1.1.1.9")
    assert same["forward"]["status"] == same["reverse"]["status"] == "same_subnet"
    assert same["symmetric"] is None


def test_lab_unknown_destination_ends_where_the_data_ends():
    result = trace(lab(), BASE, "1.1.1.2", "8.8.8.8")
    [path] = result["forward"]["paths"]
    assert hops(path) == [("10.10.1.2", "Vlan-interface100", None)]
    assert path["end"] == {"status": "no_route"}
    assert path["hops"][0]["route"] is None and path["hops"][0]["out_interface"] is None
    assert result["forward"]["status"] == "partial"
    assert result["reverse"]["status"] == "no_gateway"
    assert [(g["direction"], g["status"]) for g in result["gaps"]] == [
        ("forward", "no_route"),
        ("reverse", "no_gateway"),
    ]
    assert result["symmetric"] is None


def test_several_possible_gateways_are_candidates_not_asymmetry():
    result = trace(lab(), BASE, "10.10.1.254", "1.1.1.2")
    forward = result["forward"]
    assert forward["first_hop"] == "ambiguous" and forward["status"] == "complete"
    assert [p["hops"][0]["device"]["address"] for p in forward["paths"]] == [
        "10.10.1.1",
        "10.10.1.2",
    ]
    assert result["symmetric"] is None


@pytest.mark.parametrize(
    "src,dst", [("224.0.0.5", "1.1.1.2"), ("x", "1.1.1.2"), ("1.1.1.2", "1.1.1.2")]
)
def test_only_two_different_unicast_addresses(src, dst):
    with pytest.raises(ValueError):
        trace(lab(), BASE, src, dst)


# Synthetic routers: R1 serves 172.16.1.0/24 and reaches R4's 172.16.4.0/24 over R2 and R3.
def router(chassis, ips, routes=None):
    facts = {
        "platform": "comware",
        "lldp_local": {"chassis_id": chassis},
        "ip": [
            {"ifname": ifname, "address": address, "prefix_len": length, "vrf": "default"}
            for ifname, address, length in ips
        ],
    }
    if routes is not None:
        facts["routes"] = routes
    return facts


def route(prefix, *next_hops, protocol="Static", preference=60):
    return {
        "vrf": "default",
        "prefix": prefix,
        "protocol": protocol,
        "preference": preference,
        "metric": 0,
        "next_hops": [{"ip": ip, "ifname": ifname, "vrf": "default"} for ip, ifname in next_hops],
    }


def network(r3_routes=True):
    delivered = [route("172.16.4.0/24", ("10.1.24.2", "GigabitEthernet2/0"))]
    return {
        "10.0.0.1": router(
            "00:00:00:00:00:01",
            [
                ("Vlan-interface10", "172.16.1.1", 24),
                ("GigabitEthernet1/0", "10.1.12.1", 30),
                ("GigabitEthernet2/0", "10.1.13.1", 30),
            ],
            [
                route("172.16.1.0/24", ("172.16.1.1", "Vlan-interface10"), protocol="Direct"),
                route(
                    "172.16.4.0/24",
                    ("10.1.12.2", "GigabitEthernet1/0"),
                    ("10.1.13.2", "GigabitEthernet2/0"),
                    protocol="O_INTRA",
                    preference=10,
                ),
                route("172.16.0.0/16", ("10.1.12.2", "GigabitEthernet1/0")),
                route("172.16.9.0/24", ("0.0.0.0", "NULL0")),
                route("172.16.8.0/24", ("10.1.12.9", "GigabitEthernet1/0")),
                route("172.16.7.0/24", ("10.1.12.2", "GigabitEthernet1/0")),
            ],
        ),
        "10.0.0.2": router(
            "00:00:00:00:00:02",
            [("GigabitEthernet1/0", "10.1.12.2", 30), ("GigabitEthernet2/0", "10.1.24.1", 30)],
            [*delivered, route("172.16.7.0/24", ("10.1.12.1", "GigabitEthernet1/0"))],
        ),
        "10.0.0.3": router(
            "00:00:00:00:00:03",
            [("GigabitEthernet1/0", "10.1.13.2", 30), ("GigabitEthernet2/0", "10.1.34.1", 30)],
            [route("172.16.4.0/24", ("10.1.34.2", "GigabitEthernet2/0"))] if r3_routes else None,
        ),
        "10.0.0.4": router(
            "00:00:00:00:00:04",
            [
                ("GigabitEthernet2/0", "10.1.24.2", 30),
                ("GigabitEthernet3/0", "10.1.34.2", 30),
                ("Vlan-interface40", "172.16.4.1", 24),
            ],
            [route("172.16.4.0/24", ("172.16.4.1", "Vlan-interface40"), protocol="Direct")],
        ),
    }


def ends(result):
    return [
        ([h["device"]["address"] for h in p["hops"]], p["end"]["status"])
        for p in result["forward"]["paths"]
    ]


def test_equal_cost_next_hops_branch_and_longest_prefix_wins():
    result = trace(network(), BASE, "172.16.1.10", "172.16.4.20")
    assert ends(result) == [
        (["10.0.0.1", "10.0.0.2", "10.0.0.4"], "delivered"),
        (["10.0.0.1", "10.0.0.3", "10.0.0.4"], "delivered"),
    ]
    # The /24 beat the covering /16 on R1.
    assert result["forward"]["paths"][0]["hops"][0]["route"]["prefix"] == "172.16.4.0/24"
    assert result["forward"]["status"] == "complete"


@pytest.mark.parametrize(
    "dst,expected",
    [
        ("172.16.9.1", [(["10.0.0.1"], "blackhole")]),
        ("172.16.8.1", [(["10.0.0.1"], "unmanaged_next_hop")]),
        ("172.16.7.1", [(["10.0.0.1", "10.0.0.2", "10.0.0.1"], "loop")]),
    ],
)
def test_each_gap_ends_its_branch_with_the_reason(dst, expected):
    result = trace(network(), BASE, "172.16.1.10", dst)
    assert ends(result) == expected
    assert result["forward"]["status"] == "partial"
    assert result["gaps"][0]["direction"] == "forward"
    assert result["gaps"][0]["status"] == expected[0][1]


def test_unmanaged_next_hop_is_named():
    gap = trace(network(), BASE, "172.16.1.10", "172.16.8.1")["gaps"][0]
    assert gap["next_hop"] == "10.1.12.9" and gap["device"]["address"] == "10.0.0.1"


def test_a_device_without_a_collected_table_is_a_gap_not_a_dead_end_guess():
    result = trace(network(r3_routes=False), BASE, "172.16.1.10", "172.16.4.20")
    assert ends(result) == [
        (["10.0.0.1", "10.0.0.2", "10.0.0.4"], "delivered"),
        (["10.0.0.1", "10.0.0.3"], "routes_missing"),
    ]


def test_branches_beyond_the_limit_are_reported_as_truncated(monkeypatch):
    monkeypatch.setattr(paths, "MAX_PATHS", 1)
    forward = trace(network(), BASE, "172.16.1.10", "172.16.4.20")["forward"]
    assert len(forward["paths"]) == 1
    assert forward["truncated"] is True and forward["status"] == "partial"

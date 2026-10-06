from pathlib import Path

import pytest

from src.apps.topology.backend.collectors.parse import parse
from src.apps.topology.backend.model import path as paths
from src.apps.topology.backend.model.path import COMPUTED_TABLES, trace

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


def without_policies(facts):
    """A device known to have no ACL, NAT or policy routing: each table observed, empty.
    Routing fixtures predate their collection; without them no hop is computed."""
    return {**{table: [] for table in COMPUTED_TABLES}, **facts}


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
        devices[address] = without_policies(facts)
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
        "protocol": "ietf-ospf:ospf",
        "preference": 10,
        "metric": None,  # RFC 9129 holds the metric only for a known OSPF version
        "next_hop": "2.2.2.2",
    }
    # The routed hop names the observed L2 link between the two GigabitEthernet2/0 ports.
    assert first["link_id"].startswith(BASE + "l2-link:")
    assert last["route"]["protocol"] == "ietf-routing:direct"
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
    assert same["forward"]["status"] == same["reverse"]["status"] == "partial"
    assert same["forward"]["relation"] == "same_subnet"
    assert {gap["status"] for gap in same["gaps"]} == {"same_subnet_unproven"}
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
    assert forward["first_hop"] == "ambiguous" and forward["status"] == "partial"
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
        "interfaces": [
            {"ifname": ifname, "type": "other", "oper_status": "up"} for ifname, _, _ in ips
        ],
        "ip": [
            {"ifname": ifname, "address": address, "prefix_len": length, "vrf": "default"}
            for ifname, address, length in ips
        ],
    }
    if routes is not None:
        facts["routes"] = routes
    return without_policies(facts)


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
    devices = {
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
    # These tests exercise route selection and interface state on an observed
    # network. Route prefixes alone cannot establish any of these adjacencies.
    neighbors = {
        "10.0.0.1": [
            ("172.16.1.10", "Vlan-interface10", "00:00:00:00:01:10"),
            ("10.1.12.2", "GigabitEthernet1/0", "00:00:00:00:00:02"),
            ("10.1.13.2", "GigabitEthernet2/0", "00:00:00:00:00:03"),
        ],
        "10.0.0.2": [
            ("10.1.12.1", "GigabitEthernet1/0", "00:00:00:00:00:01"),
            ("10.1.24.2", "GigabitEthernet2/0", "00:00:00:00:00:04"),
        ],
        "10.0.0.3": [("10.1.34.2", "GigabitEthernet2/0", "00:00:00:00:00:04")],
        "10.0.0.4": [("172.16.4.20", "Vlan-interface40", "00:00:00:00:04:20")],
    }
    for key, rows in neighbors.items():
        devices[key]["arp"] = [
            {"ip": ip, "ifname": ifname, "mac": mac, "vrf": "default"} for ip, ifname, mac in rows
        ]
    return devices


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


def test_an_address_only_next_hop_resolves_through_the_rib_or_is_a_gap():
    # RFC 8349: a next hop may be an address without an outgoing interface (iBGP's
    # loopback next hops). R1 resolves 172.16.4.1 through its own /24 route over both
    # equal-cost uplinks instead of jumping to R4, which owns that address; R2 and R3
    # carry no route for the prefix. An address with no route at all is unresolved.
    devices = network()
    r1 = devices["10.0.0.1"]["routes"]
    r1.append(route("172.16.6.0/24", ("172.16.4.1", None), protocol="BGP", preference=255))
    r1.append(route("172.16.5.0/24", ("10.9.9.9", None), protocol="BGP", preference=255))
    resolved = trace(devices, BASE, "172.16.1.10", "172.16.6.20")
    assert ends(resolved) == [
        (["10.0.0.1", "10.0.0.2"], "no_route"),
        (["10.0.0.1", "10.0.0.3"], "no_route"),
    ]
    first = resolved["forward"]["paths"][0]["hops"][0]
    assert first["route"]["next_hop"] == "172.16.4.1"
    assert first["resolved_via"][0]["prefix"] == "172.16.4.0/24"
    gap = trace(devices, BASE, "172.16.1.10", "172.16.5.20")["gaps"][0]
    assert (gap["status"], gap["next_hop"]) == ("unresolved_next_hop", "10.9.9.9")


def set_interface(devices, address, ifname, **fields):
    row = next(row for row in devices[address]["interfaces"] if row["ifname"] == ifname)
    row.update(fields)
    return row


@pytest.mark.parametrize(
    "status", ["down", "testing", "dormant", "not-present", "lower-layer-down"]
)
def test_explicit_nonforwarding_egress_ends_only_its_ecmp_branch_with_evidence(status):
    devices = network()
    set_interface(devices, "10.0.0.1", "GigabitEthernet1/0", oper_status=status)
    observed = {"10.0.0.1": {"evidence_id": "current-r1", "observed_at": "2026-10-03T03:00:00Z"}}
    result = trace(devices, BASE, "172.16.1.10", "172.16.4.20", observed)
    assert ends(result) == [
        (["10.0.0.1"], "interface_unavailable"),
        (["10.0.0.1", "10.0.0.3", "10.0.0.4"], "delivered"),
    ]
    end = result["forward"]["paths"][0]["end"]
    assert end["interface"] == "GigabitEthernet1/0" and end["interface_role"] == "out"
    assert end["oper_status"] == status and end["evidence_id"] == "current-r1"
    assert end["observed_at"] == "2026-10-03T03:00:00Z"
    assert result["forward"]["status"] == "partial"
    gap = next(g for g in result["gaps"] if g["status"] == "interface_unavailable")
    assert gap["oper_status"] == status and gap["evidence_id"] == "current-r1"


def test_real_next_hop_ingress_is_checked_before_route_or_device_delivery():
    devices = network()
    set_interface(devices, "10.0.0.2", "GigabitEthernet1/0", oper_status="down")
    result = trace(devices, BASE, "172.16.1.10", "172.16.4.20")
    path = result["forward"]["paths"][0]
    assert path["end"]["status"] == "interface_unavailable"
    assert path["end"]["interface_role"] == "in"
    assert path["end"]["interface"] == "GigabitEthernet1/0"
    assert path["hops"][-1]["out_interface"] is None and path["hops"][-1]["route"] is None


def test_raw_administrative_down_blocks_even_with_operational_up_without_rewriting_it():
    devices = network()
    set_interface(devices, "10.0.0.1", "GigabitEthernet1/0", admin_status="down")
    result = trace(devices, BASE, "172.16.1.10", "172.16.4.20")
    end = result["forward"]["paths"][0]["end"]
    assert end["status"] == "interface_unavailable"
    assert end["oper_status"] == "up" and end["admin_status"] == "down"


@pytest.mark.parametrize("damage", ["unknown", "missing_status", "missing_interface", "duplicate"])
def test_missing_or_ambiguous_egress_keeps_candidate_but_cannot_claim_complete(damage):
    devices = network()
    rows = devices["10.0.0.1"]["interfaces"]
    row = next(row for row in rows if row["ifname"] == "GigabitEthernet1/0")
    if damage == "unknown":
        row["oper_status"] = "unknown"
    elif damage == "missing_status":
        row.pop("oper_status")
    elif damage == "missing_interface":
        rows.remove(row)
    else:
        rows.append({**row, "oper_status": "down", "admin_status": "down"})
    result = trace(devices, BASE, "172.16.1.10", "172.16.4.20")
    assert all(path["end"]["status"] == "delivered" for path in result["forward"]["paths"])
    assert result["forward"]["status"] == "partial"
    first = result["forward"]["paths"][0]["hops"][0]
    assert first["interface_states"]["out"]["oper_status"] == "unknown"
    assert any(
        g["status"] == "interface_state_unknown" and g["interface"] == "GigabitEthernet1/0"
        for g in result["gaps"]
    )


def test_wholly_unobserved_interfaces_never_become_default_up():
    devices = network()
    devices["10.0.0.1"].pop("interfaces")
    result = trace(devices, BASE, "172.16.1.10", "172.16.4.20")
    assert result["forward"]["status"] == "partial"
    assert all(path["end"]["status"] == "delivered" for path in result["forward"]["paths"])
    assert {
        gap["interface_role"]
        for gap in result["gaps"]
        if gap["status"] == "interface_state_unknown"
    } == {"in", "out"}


def test_recursive_next_hop_checks_the_resolved_physical_egress():
    devices = network()
    devices["10.0.0.1"]["routes"].append(
        route("172.16.6.0/24", ("172.16.4.1", None), protocol="BGP")
    )
    set_interface(devices, "10.0.0.1", "GigabitEthernet1/0", oper_status="lower-layer-down")
    result = trace(devices, BASE, "172.16.1.10", "172.16.6.20")
    first = result["forward"]["paths"][0]
    assert first["end"]["status"] == "interface_unavailable"
    assert first["end"]["interface"] == "GigabitEthernet1/0"
    assert first["hops"][0]["resolved_via"][0]["prefix"] == "172.16.4.0/24"


def test_unknown_gateway_ingress_marks_partial_even_when_local_device_delivery_succeeds():
    devices = lab()
    set_interface(devices, "10.10.1.1", "Vlan-interface200", oper_status="unknown")
    result = trace(devices, BASE, "192.168.1.2", "4.4.4.4")
    assert result["forward"]["paths"][0]["end"]["status"] == "delivered"
    assert result["forward"]["status"] == "partial"
    assert any(
        g["status"] == "interface_state_unknown" and g["interface_role"] == "in"
        for g in result["gaps"]
    )


def test_device_loopback_delivery_requires_destination_interface_available():
    devices = lab()
    set_interface(devices, "10.10.1.1", "LoopBack0", oper_status="down")
    result = trace(devices, BASE, "192.168.1.2", "4.4.4.4")
    forward = result["forward"]
    assert forward["status"] == "partial"
    hop = forward["paths"][0]["hops"][0]
    assert hop["out_interface"] is None and set(hop["interface_states"]) == {"in", "destination"}
    assert forward["paths"][0]["end"] == {
        "status": "interface_unavailable",
        "interface": "LoopBack0",
        "interface_role": "destination",
        "oper_status": "down",
    }


def test_null_needs_no_egress_and_inloop_does_not_invent_a_local_destination():
    devices = network()
    blackhole = trace(devices, BASE, "172.16.1.10", "172.16.9.1")["forward"]["paths"][0]
    assert blackhole["end"]["status"] == "blackhole"
    assert set(blackhole["hops"][0]["interface_states"]) == {"in"}
    devices["10.0.0.1"]["routes"].append(route("172.16.11.0/24", ("0.0.0.0", "InLoopBack0")))
    forward = trace(devices, BASE, "172.16.1.10", "172.16.11.1")["forward"]
    assert forward["status"] == "partial"
    # The tree holds the InLoopBack route as special-next-hop receive, without interface.
    assert forward["paths"][0]["end"] == {
        "status": "endpoint_unresolved",
        "destination": "172.16.11.1",
    }


def test_forward_and_reverse_through_different_routers_are_asymmetric():
    devices = network()
    r1, r3, r4 = (devices[key] for key in ("10.0.0.1", "10.0.0.3", "10.0.0.4"))
    # R1 forwards via R2 only; R4 returns via R3. Both directions are fully observed.
    next(r for r in r1["routes"] if r["prefix"] == "172.16.4.0/24")["next_hops"].pop()
    r4["routes"].append(route("172.16.1.0/24", ("10.1.34.1", "GigabitEthernet3/0")))
    r3["routes"].append(route("172.16.1.0/24", ("10.1.13.1", "GigabitEthernet1/0")))
    for facts, ip, ifname, mac in (
        (r4, "10.1.34.1", "GigabitEthernet3/0", "00:00:00:00:00:03"),
        (r3, "10.1.13.1", "GigabitEthernet1/0", "00:00:00:00:00:01"),
    ):
        facts["arp"].append({"ip": ip, "ifname": ifname, "mac": mac, "vrf": "default"})
    result = trace(devices, BASE, "172.16.1.10", "172.16.4.20")
    assert result["forward"]["status"] == result["reverse"]["status"] == "complete"
    assert ends(result) == [(["10.0.0.1", "10.0.0.2", "10.0.0.4"], "delivered")]
    reverse = [h["device"]["address"] for h in result["reverse"]["paths"][0]["hops"]]
    assert reverse == ["10.0.0.4", "10.0.0.3", "10.0.0.1"]
    assert result["symmetric"] is False and result["gaps"] == []

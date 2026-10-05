"""Policy routes and NAT bound to interfaces: read through rule governance, flagged on paths.

Real outputs from the campus lab (tests/fixtures/topology/campus-wan/policies): a test
policy route and NAT for 192.0.2.0/24 only, configured on CORE1 (IOS) and BR1 (Comware),
sampled, then removed. The path is still the route-table path; a hop through a bound
interface carries a gap naming the device, interface and policy, and the direction is
partial. The policies themselves are not evaluated.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.apps.topology.backend.collectors.parse import parse
from src.apps.topology.backend.model.path import trace
from src.apps.topology.backend.service import topology_observation
from tests.unit.test_topology_campus_wan import BASE, EXPECTED, IDENTITY, parsed

POLICIES = Path(__file__).parents[1] / "fixtures/topology/campus-wan/policies"
OBSERVED = {"10.10.1.11": ("br1", "comware"), "10.10.1.13": ("core1", "ios")}
CAPS = ("pbr_bindings", "nat_bindings")
PC1, INTERNET, BR1_LOOPBACK, PC4 = "172.16.10.11", "198.51.100.10", "10.255.0.1", "172.16.20.14"


def bindings(device, family, cap):
    return parse(IDENTITY[family], cap, (POLICIES / device / f"{cap}.txt").read_text("utf-8"))


def observe(extra):
    """The campus facts as topology stores them, with extra parsed tables per address."""
    result = {}
    for device, spec in EXPECTED["devices"].items():
        outcome = topology_observation(
            SimpleNamespace(
                parsed=parsed(device) | extra.get(spec["management"], {}),
                identity=IDENTITY[spec["platform"]],
                profile_id=IDENTITY[spec["platform"]].profile_id,
                mode="topology",
                driver="paramiko",
            )
        )
        result[spec["management"]] = {**outcome["facts"], "platform": spec["platform"]}
    return result


@pytest.fixture(scope="module")
def plain():
    return observe({})


@pytest.fixture(scope="module")
def bound():
    return observe(
        {
            address: {cap: bindings(device, family, cap) for cap in CAPS}
            for address, (device, family) in OBSERVED.items()
        }
    )


def hops(path):
    return [(h["device"]["name"].split(".")[0], h["in_interface"], h["out_interface"]) for h in path["hops"]]


def by_core(direction):
    """Paths keyed by the core router they cross."""
    return {
        next(name for name, *_ in hops(path) if name.startswith("CORE")): path
        for path in direction["paths"]
    }


def gaps(path, device):
    [hop] = [h for h in path["hops"] if h["device"]["name"].split(".")[0] == device]
    return hop.get("gaps", [])


PBR = "policy_may_redirect"
NAT = "nat_may_translate"


@pytest.mark.parametrize(
    "device,family,cap,records",
    [
        ("br1", "comware", "pbr_bindings", [{"ifname": "GigabitEthernet3/0", "policy": "PBR-TEST"}]),
        (
            "br1",
            "comware",
            "nat_bindings",
            [
                {"ifname": "GigabitEthernet2/0", "kind": "outbound", "rule": "2999"},
                {"ifname": "GigabitEthernet2/0", "kind": "internal server"},
                {"ifname": "GigabitEthernet2/0", "kind": "static"},
            ],
        ),
        ("core1", "ios", "pbr_bindings", [{"ifname": "Ethernet1/0", "policy": "PBR-TEST"}]),
        (
            "core1",
            "ios",
            "nat_bindings",
            # "Ethernet0/1, Ethernet0/2" on one line: one record per interface.
            [
                {"ifname": "Ethernet0/1", "kind": "outside"},
                {"ifname": "Ethernet0/2", "kind": "outside"},
                {"ifname": "Ethernet1/0", "kind": "inside"},
                {"ifname": "Ethernet1/1", "kind": "inside"},
            ],
        ),
    ],
)
def test_real_binding_tables_read_through_rule_governance(device, family, cap, records):
    result = bindings(device, family, cap)
    assert (result.status, result.tier) == ("complete", "rules")
    assert result.records == records


@pytest.mark.parametrize(
    "family,cap,text",
    [
        # The same devices before the policies were configured (2026-10-05).
        ("comware", "pbr_bindings", "PBR4 is not configured.\n"),
        ("comware", "nat_bindings", "NAT is not configured.\n"),
        ("ios", "pbr_bindings", "Interface      Route map\n"),
        ("ios", "nat_bindings", ""),
    ],
)
def test_unconfigured_output_is_an_empty_table_not_a_failure(family, cap, text):
    result = parse(IDENTITY[family], cap, text)
    assert (result.status, result.records) == ("complete", [])


@pytest.mark.parametrize("device,family", [("br1", "comware"), ("core1", "ios")])
@pytest.mark.parametrize("cap", CAPS)
def test_output_after_the_policies_were_removed_is_an_empty_table(device, family, cap):
    # Once configured, neither platform says "not configured" again: IOS keeps the empty
    # interface headings, Comware the PBR heading and the global NAT sections.
    text = (POLICIES / "removed" / device / f"{cap}.txt").read_text("utf-8")
    result = parse(IDENTITY[family], cap, text)
    assert (result.status, result.records) == ("complete", [])


@pytest.mark.parametrize(
    "family,text",
    [
        ("comware", "Policy name              Type     Interface \nPBR-L                    Local    N/A\n"),
        ("ios", "Interface      Route map\nlocal          PBR-L\n"),
    ],
)
def test_a_policy_for_locally_generated_packets_binds_no_interface(family, text):
    result = parse(IDENTITY[family], "pbr_bindings", text)
    assert (result.status, result.records) == ("complete", [])


def test_bindings_the_templates_do_not_know_are_never_passed_over():
    # A policy type other than Forward/Local (one applied to every interface, say).
    unknown_type = parse(
        IDENTITY["comware"],
        "pbr_bindings",
        "Policy name              Type     Interface \nPBR-G                    Global   N/A\n",
    )
    assert unknown_type.status == "failed"
    # An interface line in a display nat section the template does not know.
    text = (POLICIES / "br1/nat_bindings.txt").read_text("utf-8")
    unknown_section = parse(
        IDENTITY["comware"],
        "nat_bindings",
        text.replace("NAT logging:", "NAT example information:\n  Interface: GigabitEthernet1/0\n\nNAT logging:"),
    )
    assert unknown_section.status == "partial" and unknown_section.rejected_count >= 1
    odd = parse(IDENTITY["ios"], "pbr_bindings", "Interface      Route map\nEthernet1/0    PBR-TEST   extra\n")
    assert odd.status in {"partial", "failed"}


def test_a_partial_binding_table_is_not_stored():
    """A partial table cannot say an interface has no binding; the device stays unobserved."""
    text = (POLICIES / "br1/nat_bindings.txt").read_text("utf-8")
    partial = parse(
        IDENTITY["comware"],
        "nat_bindings",
        text.replace("NAT logging:", "NAT example information:\n  Interface: GigabitEthernet1/0\n\nNAT logging:"),
    )
    devices = observe(
        {"10.10.1.11": {"pbr_bindings": bindings("br1", "comware", "pbr_bindings"), "nat_bindings": partial}}
    )
    assert "pbr_bindings" in devices["10.10.1.11"] and "nat_bindings" not in devices["10.10.1.11"]
    path = by_core(trace(devices, BASE, PC1, BR1_LOOPBACK)["forward"])["CORE2"]
    [br1] = [h for h in path["hops"] if h["device"]["name"] == "BR1"]
    assert br1["policy_bindings"] == "not_observed"


def test_hops_through_bound_interfaces_are_flagged(plain, bound):
    before = trace(plain, BASE, PC1, INTERNET)["forward"]
    after = trace(bound, BASE, PC1, INTERNET)["forward"]
    # The route-table path itself does not change.
    assert sorted(map(hops, after["paths"])) == sorted(map(hops, before["paths"]))
    assert [p["end"] for p in after["paths"]] == [p["end"] for p in before["paths"]]
    paths = by_core(after)
    assert gaps(paths["CORE1"], "CORE1") == [
        {"status": PBR, "interface": "Ethernet1/0", "policy": "PBR-TEST"},
        {"status": NAT, "interface": "Ethernet1/0", "kind": "inside"},
        {"status": NAT, "interface": "Ethernet0/1", "kind": "outside"},
    ]
    nat_out = [
        {"status": NAT, "interface": "GigabitEthernet2/0", "kind": "outbound", "rule": "2999"},
        {"status": NAT, "interface": "GigabitEthernet2/0", "kind": "internal server"},
        {"status": NAT, "interface": "GigabitEthernet2/0", "kind": "static"},
    ]
    assert gaps(paths["CORE1"], "BR1") == [
        {"status": PBR, "interface": "GigabitEthernet3/0", "policy": "PBR-TEST"},
        *nat_out,
    ]
    # Through CORE2, BR1 is entered on GE4/0, where no policy route is applied.
    assert gaps(paths["CORE2"], "CORE2") == []
    assert gaps(paths["CORE2"], "BR1") == nat_out


def test_a_flag_makes_a_delivered_direction_partial(plain, bound):
    assert trace(plain, BASE, PC1, BR1_LOOPBACK)["forward"]["status"] == "complete"
    result = trace(bound, BASE, PC1, BR1_LOOPBACK)
    assert result["forward"]["status"] == "partial"
    assert {p["end"]["status"] for p in result["forward"]["paths"]} == {"delivered"}
    flagged = {
        (g["device"]["name"].split(".")[0], g["interface"], g["status"])
        for g in result["gaps"]
        if g["direction"] == "forward"
    }
    assert flagged == {
        ("CORE1", "Ethernet1/0", PBR),
        ("CORE1", "Ethernet1/0", NAT),
        ("CORE1", "Ethernet0/1", NAT),
        ("BR1", "GigabitEthernet3/0", PBR),
    }


def test_a_policy_route_is_read_on_ingress_only(bound):
    # On the way back BR1 sends out of GE3/0 and CORE1 out of Ethernet1/0, both carrying a
    # policy route for arriving packets: it does not apply to leaving ones. NAT does.
    reverse = trace(bound, BASE, PC1, BR1_LOOPBACK)["reverse"]
    [path] = [p for p in reverse["paths"] if hops(p)[1] == ("CORE1", "Ethernet0/1", "Ethernet1/0")]
    assert hops(path)[0] == ("BR1", None, "GigabitEthernet3/0")
    assert gaps(path, "BR1") == []
    assert gaps(path, "CORE1") == [
        {"status": NAT, "interface": "Ethernet0/1", "kind": "outside"},
        {"status": NAT, "interface": "Ethernet1/0", "kind": "inside"},
    ]


def test_unobserved_bindings_are_noted_without_changing_the_status(plain, bound):
    result = trace(plain, BASE, PC1, PC4)["forward"]
    assert result["status"] == "complete"
    [path] = result["paths"]
    assert [(h["policy_bindings"], h.get("gaps", [])) for h in path["hops"]] == [("not_observed", [])]
    # Where both tables were read completely there is nothing to note.
    paths = by_core(trace(bound, BASE, PC1, BR1_LOOPBACK)["forward"])
    assert all("policy_bindings" not in h for h in paths["CORE1"]["hops"][1:])

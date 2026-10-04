"""Topology reads ARP, routes, VRF detail and EVPN peers through rule governance.

The system base check items, their implementations and TextFSM templates belong to rule
governance (docs/topology-rule-consumption.md); topology only consumes the public
contract ``src.apps.rule_governance.runtime`` and projects the same-name Values.
"""

import hashlib
import re
from pathlib import Path

import pytest

from src.apps.rule_governance.backend.check_items import system_items
from src.apps.rule_governance.runtime import CheckItemSelectionReader, SelectionError
from src.apps.topology.backend.collectors import rules
from src.apps.topology.backend.collectors.parse import parse, parse_generic
from src.apps.topology.backend.facts.repository import FactInput, validate_records
from tests.unit.test_topology_generic_parsers import STYLES, lab

FIXTURES = Path(__file__).parents[1] / "fixtures/topology"


def run(cap, text, family="comware", **kwargs):
    return parse({"os_family": family}, cap, text, **kwargs)


def fixture(path):
    return (FIXTURES / path).read_text("utf-8")


@pytest.mark.parametrize("device,routes", [("vsr-1", 18), ("vsr-2", 19)])
@pytest.mark.parametrize("capability", ["arp", "routes"])
def test_real_comware_tables_through_the_published_check_item(device, routes, capability):
    result = run(capability, lab(capability, device))
    assert result.status == "complete" and result.tier == "rules"
    assert result.confidence == "high"
    # The parser reference names the implementation and the pinned template text.
    assert result.parser_ref.startswith(f"rules:topology.{capability}.comware:H3C/Comware7/")
    assert re.fullmatch(r".*\.textfsm@[0-9a-f]{64}", result.parser_ref)
    assert all(source["lines"] for source in result.sources)
    validate_records(
        FactInput(
            capability=capability,
            observed_at="2026-10-04T12:00:00Z",
            status=result.status,
            source="offline",
            platform_profile="h3c-comware7",
            parser_ref=result.parser_ref,
            evidence_ref="test",
            records=result.records,
            summary=result.fact_summary(),
        )
    )
    if capability == "routes":
        assert len(result.records) == routes
        assert any(row["prefix"] == "0.0.0.0/32" for row in result.records)
        assert not any(row["prefix"] == "0.0.0.0/0" for row in result.records)
        hops = [hop for row in result.records for hop in row["next_hops"]]
        assert {"InLoopBack0", "NULL0"} <= {hop["ifname"] for hop in hops}
        remote = "1.1.1.0/24" if device == "vsr-1" else "4.4.4.4/32"
        assert next(r for r in result.records if r["prefix"] == remote)["protocol"] == "O_INTRA"
    else:
        # The check item reads what the generic parser read, with the same evidence lines.
        generic = parse_generic({"os_family": "comware"}, "arp", lab("arp", device))
        assert result.records == generic.records
        assert [s["lines"] for s in result.sources] == [s["lines"] for s in generic.sources]
        texts = [
            (s["raw"]["text"], g["raw"]["text"]) for s, g in zip(result.sources, generic.sources)
        ]
        assert all(ours == theirs for ours, theirs in texts)


@pytest.mark.parametrize(
    "case", [c for c in STYLES if c["family"] in {"comware", "ios"}], ids=lambda c: c["family"]
)
def test_synthetic_arp_reads_like_the_generic_parser(case):
    result = run("arp", case["arp"], case["family"])
    generic = parse_generic({"os_family": case["family"]}, "arp", case["arp"])
    assert result.status == generic.status == "complete" and result.tier == "rules"
    assert result.records == generic.records


def test_real_ios_tables():
    arp = run("arp", fixture("campus-wan/core1/arp.txt"), "ios")
    routes = run("routes", fixture("campus-wan/core1/routes.txt"), "ios")
    assert arp.status == routes.status == "complete"
    assert arp.records and all(r["age_s"] % 60 == 0 for r in arp.records if "age_s" in r)
    assert routes.parser_ref.startswith("rules:topology.routes.ios:Cisco/IOS/")
    assert all(hop["ip"] or hop["ifname"] for r in routes.records for hop in r["next_hops"])
    assert run("routes", "some unknown text", "ios").status == "failed"


def test_arp_bad_row_is_rejected_and_the_rest_kept():
    result = run("arp", lab("arp").replace("0c22-57e9-0001", "not-a-mac"))
    assert result.status == "partial" and result.rejected_count == 1
    assert len(result.records) == 3 and "collection_incomplete" not in result.warnings
    assert "line:5:template_rejected:unrecognized_display_arp_line" in result.warnings
    assert [s["lines"] for s in result.sources] == [[3], [4], [6]]
    tail = run("arp", lab("arp") + "\nBROKEN ROW unexpected syntax\n")
    assert tail.status == "partial" and len(tail.records) == 4
    assert run("arp", "some unknown text").status == "failed"


def test_arp_cut_off_output_is_incomplete_not_defective():
    paged = run("arp", lab("arp") + "\n---- More ----")
    assert paged.status == "partial" and "collection_incomplete" in paged.warnings
    assert len(paged.records) == 4 and paged.rejected_count == 0
    assert run("arp", lab("arp"), complete=False).status == "partial"
    assert "age_unit_unknown" in run("arp", lab("arp")).warnings[0]


def test_vsi_arp_entries_keep_the_vsi_and_skip_link_ids():
    result = run("arp", fixture("vpn-fabric/leaf3/arp.txt"))
    assert result.status == "complete"
    by_ip = {r["ip"]: (r, s) for r, s in zip(result.records, result.sources)}
    host, source = by_ip["192.168.20.14"]
    assert host["vsi"] == "BLUE-20" and "ifname" not in host  # link ID 0, not an interface
    gateway, source = by_ip["10.255.10.12"]
    assert gateway["vsi"] == "Auto_L3VNI50020_920" and gateway["ifname"] == "Tunnel1"
    assert source["lines"] == [8, 9]  # the wrapped VSI name is that entry's evidence


@pytest.mark.parametrize("suffix", ["\n---- More ----", "\n[truncated]"])
def test_incomplete_routes_never_replace_current(suffix):
    result = run("routes", lab("routes") + suffix)
    assert result.status in {"partial", "failed"}
    assert run("routes", lab("routes"), complete=False).status == "partial"


@pytest.mark.parametrize(
    "change",
    [
        lambda text: text.replace("Routes : 18", "Routes : 19"),
        lambda text: text.replace("Destinations : 18", "Destinations : 19"),
        lambda text: text.replace("1.1.1.0/24", "1.1.1.1/24"),
        lambda text: text.replace("1.1.1.0/24", "999.1.1.0/24"),
        lambda text: text.replace("GE2/0", "made-up-interface"),
        lambda text: text.replace("GE2/0", "GE2/0 UNPARSED"),
        lambda text: text + "\nBROKEN ROW\n",
        lambda text: "UNKNOWN HEADER\n" + text,
    ],
    ids=[
        "route-count",
        "destination-count",
        "host-bits",
        "bad-address",
        "bad-interface",
        "trailing-text",
        "unknown-tail",
        "unknown-header",
    ],
)
def test_a_route_table_with_any_defect_fails_whole(change):
    result = run("routes", change(lab("routes")))
    assert result.status == "failed" and result.records == []


def test_ecmp_preserves_prefix_evidence_and_counts_forwarding_rows():
    text = (
        "Destinations : 1\tRoutes : 2\n"
        "Destination/Mask   Proto   Pre Cost        NextHop         Interface\n"
        "192.0.2.0/24 O_INTRA 10 2 198.51.100.1 GE1/0\n"
        "                           198.51.100.2 GE2/0\n"
    )
    result = run("routes", text)
    assert result.status == "complete"
    assert len(result.records) == 1 and len(result.records[0]["next_hops"]) == 2
    assert result.sources[0]["lines"] == [3, 4]
    assert result.candidate_count == 2


def test_empty_routes_requires_known_header_and_counts():
    text = (
        "Destinations : 0\tRoutes : 0\n"
        "Destination/Mask   Proto   Pre Cost        NextHop         Interface\n"
    )
    result = run("routes", text)
    assert result.status == "complete" and result.records == []
    assert run("routes", "unrecognized empty result").status == "failed"
    assert run("routes", text.replace("Routes : 0", "Routes : 1")).status == "failed"
    assert run("routes", "Destinations : 0\tRoutes : 0").status == "failed"


def test_a_model_exception_wins_over_the_family_default(monkeypatch):
    base = system_items.spec()
    default = next(
        row
        for row in base["implementations"]
        if row["check_item"] == "topology.arp" and row["platform"] == "ios"
    )
    exception = {**default, "variant": "c9300", "model_patterns": ["^C9300-"]}
    patched = {**base, "implementations": [*base["implementations"], exception]}
    monkeypatch.setattr(system_items, "spec", lambda path=None: patched)
    reader = CheckItemSelectionReader(source="shipped")
    device = {"vendor": "Cisco", "platform": "ios"}

    def chosen(**extra):
        return reader.select(["topology.arp"], {**device, **extra})["topology.arp"]

    assert chosen(model="C9300-48P").implementation_id == "topology.arp.ios.c9300"
    assert chosen(model="ISR4451-X/K9").implementation_id == "topology.arp.ios"
    assert chosen().implementation_id == "topology.arp.ios"


def _changed_templates(monkeypatch, change, *, repin):
    original_text, original_templates = system_items.template_text, system_items.templates

    def text(path):
        return change(original_text(path))

    def pinned():
        return {
            key: {**bound, "sha256": hashlib.sha256(text(bound["template"]).encode()).hexdigest()}
            for key, bound in original_templates().items()
        }

    monkeypatch.setattr(system_items, "template_text", text)
    if repin:
        monkeypatch.setattr(system_items, "templates", pinned)


def test_a_changed_template_is_refused_until_its_pin_is_reviewed(monkeypatch):
    _changed_templates(monkeypatch, lambda text: text + "\n", repin=False)
    with pytest.raises(SelectionError, match="without a reviewed pin"):
        CheckItemSelectionReader(source="shipped").select(
            ["topology.routes"], {"vendor": "H3C", "platform": "comware"}
        )
    result = run("routes", lab("routes"))
    assert result.status == "failed" and result.records == []
    assert result.warnings[0].startswith("implementation_invalid:")


def test_a_template_outside_the_contract_is_refused(monkeypatch):
    _changed_templates(
        monkeypatch,
        lambda text: text.replace("Value TYPE", "Value KIND").replace("${TYPE}", "${KIND}"),
        repin=True,
    )
    with pytest.raises(SelectionError, match="outside the contract"):
        CheckItemSelectionReader(source="shipped").select(
            ["topology.arp"], {"vendor": "H3C", "platform": "comware"}
        )
    result = run("arp", lab("arp"))
    assert result.status == "failed" and result.records == []
    assert result.warnings[0].startswith("implementation_invalid:")


def test_a_device_without_an_implementation_gets_none(monkeypatch):
    base = system_items.spec()
    without = [row for row in base["implementations"] if row["platform"] != "ios"]
    monkeypatch.setattr(
        system_items, "spec", lambda path=None: {**base, "implementations": without}
    )
    monkeypatch.setattr(rules, "_readers", {})
    result = run("arp", fixture("campus-wan/core1/arp.txt"), "ios")
    assert result.status == "failed" and result.warnings == ["no_implementation"]


def test_system_check_items_cannot_be_deleted_or_lose_required_values():
    with pytest.raises(system_items.SystemCheckItemError):
        system_items.ensure_deletable(check_item_id="topology.routes")
    with pytest.raises(system_items.SystemCheckItemError):
        system_items.ensure_deletable(implementation="topology.routes.comware")
    system_items.ensure_deletable(check_item_id="interface.status")
    item = next(i for i in system_items.check_item_payloads() if i["id"] == "topology.arp")
    contract = item["canonical_contract"]
    system_items.ensure_compatible("topology.arp", contract)
    dropped = {
        **contract,
        "observations": [
            row for row in contract["observations"] if row["canonical_field"] != "MAC_ADDRESS"
        ],
    }
    with pytest.raises(system_items.SystemCheckItemError, match="MAC_ADDRESS"):
        system_items.ensure_compatible("topology.arp", dropped)


def test_collection_runs_the_published_commands():
    from src.apps.topology.backend.collectors import collect

    allowed = collect.command_allowlist()
    assert {
        "show ip arp",
        "display arp",
        "show ip route",
        "display ip routing-table",
        "show vrf detail",
        "show nve peers",
        "display evpn auto-discovery imet",
    } <= allowed
    assert not any("{vrf}" in command for command in allowed)
    # Per-VRF forms come from the same implementations and are granted per job.
    assert collect.scoped_command("routes", "BLUE", "comware") == (
        "display ip routing-table vpn-instance BLUE"
    )
    assert collect.scoped_command("arp", "MGMT", "ios") == "show ip arp vrf MGMT"
    # A Comware instance detail names the instance itself; it extends the enumeration.
    assert collect.scoped_command("vrf_detail", "RED", "comware") == (
        "display ip vpn-instance instance-name RED"
    )
    assert collect.scope_base("vrf_detail", "comware") == "display ip vpn-instance"
    assert collect.scope_base("routes", "ios") == "show ip route"
    with pytest.raises(ValueError):
        collect.scoped_command("arp", "BLUE", "comware")  # no per-VRF ARP form published


def test_a_published_command_must_still_be_read_only(monkeypatch):
    from src.apps.topology.backend.collectors import collect

    base = system_items.spec()
    changed = [
        {**row, "command": "reset arp all"} if row["check_item"] == "topology.arp" else row
        for row in base["implementations"]
    ]
    monkeypatch.setattr(
        system_items, "spec", lambda path=None: {**base, "implementations": changed}
    )
    monkeypatch.setattr(rules, "_readers", {})
    identity = collect.Identity("H3C", "comware")
    assert collect.published_command(identity, "arp") == ("command_not_read_only", None)
    # Nor is it ever granted to the job.
    assert "reset arp all" not in collect.command_allowlist()
    assert collect.published_command(identity, "routes") == (None, "display ip routing-table")
    assert collect.published_command(identity, "vrf_detail") == (None, None)  # per instance


LFIB_HEADER = (
    "Local      Outgoing   Prefix           Bytes Label   Outgoing   Next Hop    \n"
    "Label      Label      or Tunnel Id     Switched      interface              \n"
)


def test_ios_label_table_reads_wrapped_prefixes_and_vpn_aggregates():
    text = LFIB_HEADER + (
        "16         Pop Label  10.255.20.2/32   0             Et0/1      10.20.12.2  \n"
        "18         No Label   10.1.1.0/24[V]   293314        aggregate/CUST-A \n"
        "21         17         192.0.2.128/25[V] \\\n"
        "                                       0             Et0/2      10.20.12.6  \n"
    )
    result = run("mpls_lfib", text, "ios")
    assert result.status == "complete"
    assert result.records == [
        {
            "fec": "10.255.20.2/32",
            "in_label": 16,
            "out_label": "implicit-null",
            "ifname": "Ethernet0/1",
            "next_hop": "10.20.12.2",
        },
        {"fec": "10.1.1.0/24", "in_label": 18, "out_label": "untagged", "vrf": "CUST-A"},
        {
            "fec": "192.0.2.128/25",
            "in_label": 21,
            "out_label": 17,
            "ifname": "Ethernet0/2",
            "next_hop": "10.20.12.6",
        },
    ]
    assert result.sources[2]["lines"] == [5, 6]  # the wrapped row is one entry
    bad = run("mpls_lfib", text.replace("17         192", "2000000    192"), "ios")
    assert bad.status == "partial" and bad.rejected_count == 1  # not an MPLS label
    assert (
        run("mpls_lfib", "no MPLS apps enabled or MPLS not enabled on any interfaces", "ios").status
        == "complete"
    )


@pytest.mark.parametrize(
    "capability,family,text",
    [
        ("ldp_peers", "comware", "Peer LDP ID   State   Role   GR   MD5  KA Sent/Rcvd\nnonsense\n"),
        ("evpn_mac", "ios", "MAC Address    EVI   VLAN  ESI   Ether Tag  Next Hop(s)\nnonsense\n"),
        ("vxlan_vnis", "comware", "VSI Name: RED\nnonsense\n"),
        ("tunnel_interfaces", "comware", "nonsense\n"),
    ],
)
def test_unknown_lines_are_never_silently_dropped(capability, family, text):
    result = run(capability, text, family)
    assert result.status in {"partial", "failed"} and result.rejected_count >= 1


@pytest.mark.parametrize("device,vid", [("vsr-1", 200), ("vsr-2", 100)])
def test_comware_vlan_names_keep_their_spaces_and_ports_are_members(device, vid):
    result = run("vlan", lab("vlan", device))
    assert result.status == "complete" and result.tier == "rules"
    assert result.records == [
        {"vlan_id": 1, "name": "VLAN 0001"},
        {"vlan_id": vid, "name": f"VLAN {vid:04d}"},
    ]
    assert result.summary["members"][1] == {"vlan_id": vid, "ports": ["GigabitEthernet3/0"]}


def test_malformed_vlan_rows_are_rejected_not_guessed():
    text = lab("vlan")
    wrapped = run("vlan", text + "\n          wrapped-ports GE4/0\n")
    assert wrapped.status == "partial" and wrapped.rejected_count
    invalid = run("vlan", text.replace("200       VLAN", "4095      VLAN"))
    assert invalid.status == "partial" and invalid.rejected_count
    assert all(row["vlan_id"] != 4095 for row in invalid.records)
    unknown_header = run("vlan", "UNKNOWN HEADER\n" + text)
    assert unknown_header.status == "partial" and unknown_header.rejected_count

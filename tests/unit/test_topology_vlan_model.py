"""RFC 8944 tags describe encapsulated traffic, not PVID or forwarding eligibility."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.apps.topology.backend.collectors.identify import Identity
from src.apps.topology.backend.collectors.parse import parse as parse_cli
from src.apps.topology.backend.model.builder import L2T, NT, build_topology
from src.apps.topology.backend.service import topology_observation

YANG = Path(__file__).resolve().parents[2] / "src/apps/topology/resources/yang"
CAPTURES = Path(__file__).resolve().parents[1] / "fixtures/topology/campus-wan/vlan-trunk"
PORT = "Ethernet0/1"


def read(device, capability):
    return (CAPTURES / device / f"{capability}.txt").read_text()


def trunk(**changes):
    return {
        "ifname": PORT,
        "l2_mode": "trunk",
        "encapsulation": "dot1q",
        "native_vlan": 1,
        "trunk_vlans": "10,20",
        "active_vlans": "10,20",
        "forwarding_vlans": "10",
        **changes,
    }


def export(trunks=None, switchports=None, **extra):
    facts = {
        "platform": "ios",
        "lldp_local": {"chassis_id": "aa:aa:aa:aa:aa:aa", "hostname": "switch"},
        "interfaces": [{"ifname": PORT, "type": "ethernet"}],
        **extra,
    }
    if trunks is not None:
        facts["trunk"] = {"ports": trunks}
    if switchports is not None:
        facts["switchport"] = {"ports": switchports}
    tree, _ = build_topology({"10.0.0.1": facts}, "urn:test:vlan:")
    return tree


def attributes(tree, name=PORT, node_name="switch"):
    network = tree["ietf-network:networks"]["network"][0]
    node = next(
        n for n in network["node"] if n.get(L2T + "l2-node-attributes", {}).get("name") == node_name
    )
    return next(
        tp[L2T + "l2-termination-point-attributes"]
        for tp in node[NT + "termination-point"]
        if tp[L2T + "l2-termination-point-attributes"]["interface-name"] == name
    )


def test_non_native_allowed_tags_are_exported_in_standard_ranges():
    tree = export([trunk(trunk_vlans="10,11,12,20,30-32")])
    attrs = attributes(tree)
    assert attrs["outer-tag"] == "10-12,20,30-32"
    assert attrs["encapsulation-type"] == L2T + "vlan"
    assert "inner-tag" not in attrs and "outer-tpid" not in attrs


@pytest.mark.parametrize(
    ("tagged", "expected"),
    [(None, None), (False, "11-12,20"), (True, "10-12,20")],
)
def test_allowed_native_vlan_requires_explicit_tagging_evidence(tagged, expected):
    port = {"ifname": PORT, "l2_mode": "trunk", "native_vlan": 10}
    if tagged is not None:
        port["native_tagged"] = tagged
        port["admin_native_tagged"] = not tagged
    attrs = attributes(export([trunk(native_vlan=10, trunk_vlans="10-12,20")], [port]))
    assert attrs.get("outer-tag") == expected
    assert attrs["encapsulation-type"] == L2T + ("vlan" if expected else "ethernet")


@pytest.mark.parametrize("admin_tagged", [False, True])
def test_administrative_native_tagging_cannot_establish_operational_tags(admin_tagged):
    port = {
        "ifname": PORT,
        "l2_mode": "trunk",
        "oper_mode": "trunk",
        "admin_native_tagged": admin_tagged,
    }
    assert "outer-tag" not in attributes(export([trunk(trunk_vlans="1,10,20")], [port]))


@pytest.mark.parametrize("side", ["trunk", "switchport"])
@pytest.mark.parametrize("mode", ["down", "access", "routed", None, 1])
def test_explicit_non_trunk_operational_mode_prevents_tag_inference(side, mode):
    observed = trunk(oper_mode=mode) if side == "trunk" else trunk()
    port = {"ifname": PORT, "l2_mode": "trunk", "oper_mode": mode} if side == "switchport" else None
    assert "outer-tag" not in attributes(export([observed], [port] if port else None))


def test_confirmed_operational_trunk_still_exposes_observed_non_native_tags():
    port = {"ifname": PORT, "l2_mode": "trunk", "oper_mode": "trunk"}
    assert attributes(export([trunk(oper_mode="trunk")], [port]))["outer-tag"] == "10,20"


def test_access_vlan_and_pvid_are_not_outer_tags():
    access = {"ifname": PORT, "l2_mode": "access", "access_vlan": 10, "pvid": 10}
    tree = export(switchports=[access], vlan=[{"vlan_id": 10, "ports": [PORT]}])
    attrs = attributes(tree)
    assert "outer-tag" not in attrs and "pvid" not in attrs and "access_vlan" not in attrs
    assert attrs["encapsulation-type"] == L2T + "ethernet"


def test_operational_active_and_stp_lists_do_not_replace_allowed_tags():
    attrs = attributes(export([trunk(active_vlans="10", forwarding_vlans="")]))
    assert attrs["outer-tag"] == "10,20"


@pytest.mark.parametrize(
    "allowed",
    [
        "0",
        "4095",
        "20-10",
        "10,10",
        "10-20,20-30",
        "20,10",
        "01",
        "1-4095",
        "10,,20",
        "all",
        None,
        10,
    ],
)
def test_malformed_or_semantically_invalid_vid_ranges_are_not_published(allowed):
    assert "outer-tag" not in attributes(export([trunk(trunk_vlans=allowed)]))


@pytest.mark.parametrize("native", [None, True, "1", 0, 4095])
def test_missing_or_invalid_native_vlan_keeps_tags_unknown(native):
    assert "outer-tag" not in attributes(export([trunk(native_vlan=native)]))


@pytest.mark.parametrize(
    "port",
    [
        {"ifname": PORT, "l2_mode": "access", "access_vlan": 10},
        {"ifname": PORT, "l2_mode": "routed"},
        {"ifname": PORT, "l2_mode": "trunk", "native_vlan": 10},
        {"ifname": PORT, "l2_mode": "trunk", "native_vlan": True},
        {"ifname": PORT, "l2_mode": "trunk", "trunk_vlans": "10"},
        {"ifname": PORT, "l2_mode": "trunk", "trunk_vlans": "20,10"},
    ],
)
def test_conflicting_same_port_observations_do_not_publish_a_tag_set(port):
    assert "outer-tag" not in attributes(export([trunk()], [port]))


@pytest.mark.parametrize("encapsulation", [None, "unknown", "isl", "qinq"])
def test_unobserved_or_other_encapsulation_does_not_assume_single_dot1q(encapsulation):
    assert "outer-tag" not in attributes(export([trunk(encapsulation=encapsulation)]))


def test_native_only_untagged_trunk_has_no_outer_tag():
    port = {"ifname": PORT, "l2_mode": "trunk", "native_tagged": False}
    assert "outer-tag" not in attributes(export([trunk(trunk_vlans="1")], [port]))


def test_empty_allowed_set_has_no_tag_encapsulation():
    assert "outer-tag" not in attributes(export([trunk(trunk_vlans="")]))


def test_router_without_switchport_or_trunk_facts_remains_modelable():
    attrs = attributes(export())
    assert attrs["encapsulation-type"] == L2T + "ethernet"
    assert "outer-tag" not in attrs


@pytest.mark.parametrize("summary", [None, [], {}, {"ports": None}, {"ports": "10"}])
def test_missing_or_malformed_optional_trunk_facts_add_no_tags(summary):
    assert "outer-tag" not in attributes(export(trunk=summary))


def test_duplicate_port_facts_are_not_silently_overwritten():
    assert "outer-tag" not in attributes(export([trunk(), trunk(trunk_vlans="30")]))


def test_duplicate_switchport_facts_are_not_treated_as_absent_evidence():
    ports = [{"ifname": PORT, "l2_mode": "trunk"}, {"ifname": PORT, "l2_mode": "access"}]
    assert "outer-tag" not in attributes(export([trunk()], ports))


@pytest.mark.parametrize("summary", [[], {"ports": None}, {"ports": "10"}, {"ports": [{}]}])
def test_malformed_switchport_facts_are_not_silently_ignored(summary):
    assert "outer-tag" not in attributes(export([trunk()], switchport=summary))


@pytest.mark.parametrize("summary", [None, {}, {"ports": []}])
def test_uncollected_or_unsupported_optional_switchport_does_not_negate_trunk_tags(summary):
    assert attributes(export([trunk()], switchport=summary))["outer-tag"] == "10,20"


@pytest.mark.parametrize("side", ["trunk", "switchport"])
def test_parser_invalid_port_markers_block_only_that_ports_attributes(side):
    summary = {"ports": [trunk()], "invalid_ports": [PORT]}
    tree = (
        export(switchport=summary, trunk={"ports": [trunk()]})
        if side == "switchport"
        else export(trunk=summary)
    )
    assert "outer-tag" not in attributes(tree)


@pytest.mark.parametrize("side", ["trunk", "switchport"])
def test_parser_unknown_structure_blocks_vlan_attributes_for_that_device(side):
    summary = {"ports": [trunk()], "invalid_structure": True}
    tree = (
        export(switchport=summary, trunk={"ports": [trunk()]})
        if side == "switchport"
        else export(trunk=summary)
    )
    assert "outer-tag" not in attributes(tree)


@pytest.mark.parametrize("damage", ["prefix", "tail", "repeated_header"])
def test_real_unknown_trunk_structure_stays_a_veto_through_observation_and_builder(damage):
    identity = Identity("Cisco", "ios", "cisco-ios", "show", "high", "identified")
    text = (CAPTURES / "acc1/trunk.txt").read_text()
    if damage == "prefix":
        text = "Unknown global trunk syntax\n" + text
    elif damage == "tail":
        text += "Unknown global trunk syntax\n"
    else:
        text = text.splitlines()[0] + "\n" + text
    parsed = {
        "trunk": parse_cli(identity, "trunk", text),
        "switchport": parse_cli(
            identity, "switchport", (CAPTURES / "acc1/switchport.txt").read_text()
        ),
        "lldp": parse_cli(
            identity, "lldp", (CAPTURES.parent / "acc1/lldp_verbose.txt").read_text()
        ),
        "interfaces": parse_cli(
            identity,
            "interfaces",
            (CAPTURES.parent / "acc1/interfaces_description.txt").read_text(),
        ),
    }
    assert parsed["trunk"].status == "partial"
    assert parsed["trunk"].summary["invalid_structure"] is True
    observation = topology_observation(
        SimpleNamespace(
            parsed=parsed,
            identity=identity,
            profile_id=identity.profile_id,
            mode="topology",
            driver="paramiko",
        )
    )
    assert observation["complete"] and observation["failure_code"] is None
    assert observation["capabilities"]["trunk"] == "partial"
    assert observation["facts"]["trunk"]["invalid_structure"] is True
    assert {p["ifname"] for p in observation["facts"]["trunk"]["ports"]} == {
        "Ethernet0/1",
        "Ethernet0/2",
    }
    tree, _ = build_topology(
        {
            "10.0.0.1": {
                **observation["facts"],
                "platform": "ios",
                "lldp_local": {"chassis_id": "aa:aa:aa:aa:aa:aa", "hostname": "switch"},
            }
        },
        "urn:test:vlan:",
    )
    for port in ("Ethernet0/1", "Ethernet0/2"):
        assert "outer-tag" not in attributes(tree, port)


def test_invalid_marker_for_a_different_port_does_not_erase_good_tag_observations():
    tree = export([trunk()], switchport={"ports": [], "invalid_ports": ["Ethernet0/2"]})
    assert attributes(tree)["outer-tag"] == "10,20"


@pytest.mark.parametrize(
    "port",
    [
        {"oper_mode": "trunk (member of bundle Ethernet0/2)"},
        {"oper_mode": "trunk (member of bundle Po1) extra"},
        {"oper_mode": "trunk (member of bundle Po1)", "lag_parent": "Port-channel2"},
    ],
)
def test_unverified_or_conflicting_bundle_operational_modes_add_no_tags(port):
    row = {"ifname": PORT, "l2_mode": "trunk", **port}
    assert "outer-tag" not in attributes(export([trunk()], [row]))


@pytest.mark.parametrize("device", ["dist1", "dist2"])
def test_real_bundle_member_mode_accepts_independent_same_port_trunk_evidence(device):
    observed = parse_cli({"os_family": "ios"}, "switchport", read(device, "switchport"))
    assert observed.status == "complete"
    members = [p for p in observed.summary["ports"] if p.get("lag_parent") == "Port-channel1"]
    assert {p["ifname"] for p in members} == {"Ethernet1/0", "Ethernet1/1"}
    for member in members:
        # show interfaces trunk lists the aggregate only. This synthetic same-port
        # observation checks that the real member mode is not incorrectly rejected.
        same_port = trunk(
            ifname=member["ifname"],
            native_vlan=member["native_vlan"],
            trunk_vlans=member["trunk_vlans"],
        )
        attrs = attributes(export([same_port], [member]), member["ifname"])
        assert attrs["outer-tag"] == "10,20"
        assert member["oper_mode"] == "trunk (member of bundle Po1)"


@pytest.mark.parametrize("device", ["dist1", "dist2"])
def test_real_dist_aggregate_tags_are_not_copied_to_member_ports(device):
    trunks = parse_cli({"os_family": "ios"}, "trunk", read(device, "trunk"))
    switchports = parse_cli({"os_family": "ios"}, "switchport", read(device, "switchport"))
    assert trunks.status == switchports.status == "complete"
    tree = export(
        trunks.summary["ports"],
        switchports.summary["ports"],
        lag=[{"ifname": "Port-channel1", "members": ["Ethernet1/0", "Ethernet1/1"]}],
    )
    assert attributes(tree, "Port-channel1")["outer-tag"] == "10,20"
    assert "outer-tag" not in attributes(tree, "Ethernet1/0")
    assert "outer-tag" not in attributes(tree, "Ethernet1/1")


def test_tags_on_a_lag_are_not_invented_on_its_members():
    tree = export(
        [trunk(ifname="Port-channel1")],
        lag=[{"ifname": "Port-channel1", "members": ["Ethernet1/0", "Ethernet1/1"]}],
    )
    assert attributes(tree, "Port-channel1")["outer-tag"] == "10,20"
    assert "outer-tag" not in attributes(tree, "Ethernet1/0")
    assert "outer-tag" not in attributes(tree, "Ethernet1/1")


def test_collected_tags_are_not_invented_on_the_neighbours_port():
    tree = export(
        [trunk()],
        lldp=[
            {
                "local_if": PORT,
                "remote_chassis_id": "bb:bb:bb:bb:bb:bb",
                "remote_port_id": "Ethernet0/2",
                "remote_port_subtype": "Interface name",
                "remote_sysname": "peer",
            }
        ],
    )
    assert attributes(tree)["outer-tag"] == "10,20"
    assert "outer-tag" not in attributes(tree, "Ethernet0/2", "peer")


@pytest.mark.parametrize("allowed", ["10,20", "1-4094"])
def test_tagged_topology_validates_against_the_frozen_vlan_feature(allowed):
    yangson = pytest.importorskip("yangson", reason="Install tests/topology-requirements.txt")
    content = pytest.importorskip("yangson.enumerations").ContentType.all
    tree = export(
        [trunk(trunk_vlans=allowed)],
        [{"ifname": PORT, "l2_mode": "trunk", "native_tagged": True}],
    )
    model = yangson.DataModel((YANG / "library.json").read_text(), [str(YANG)])
    model.from_raw(tree).validate(ctype=content)
    assert attributes(tree)["outer-tag"] == allowed

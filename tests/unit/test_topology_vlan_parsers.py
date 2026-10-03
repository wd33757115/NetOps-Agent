"""Real campus VLAN/trunk/switchport captures, plus explicit corruptions."""

import json
import re
from pathlib import Path

import pytest

from src.apps.topology.backend.collectors.parse import framed_text
from src.apps.topology.backend.collectors.parse import parse as parse_cli
from src.apps.topology.backend.collectors.tiers import (
    generic_switchport,
    generic_trunk,
    generic_vlan,
)
from src.apps.topology.backend.facts.repository import FactInput, validate_records

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/topology"
CAMPUS = FIXTURES / "campus-wan"
NEW = CAMPUS / "vlan-trunk"
OBSERVATIONS = json.loads((NEW / "manifest.json").read_text("utf-8"))["observations"]


def source(device, capability, *, new=False, raw=False):
    return (
        ((NEW if new else CAMPUS) / device / f"{capability}{'.raw' if raw else ''}.txt")
        .read_bytes()
        .decode("utf-8")
    )


def port_rows(parsed):
    assert parsed.records == []
    return {row["ifname"]: row for row in parsed.summary.get("ports", [])}


def switchport_block(name="Et1/0"):
    blocks = re.split(r"(?m)(?=^Name: )", source("dist1", "switchport", new=True))
    return next(block for block in blocks if block.startswith(f"Name: {name}\n"))


@pytest.mark.parametrize(
    "observation",
    OBSERVATIONS,
    ids=lambda item: f"{item['device']}-{item['capability']}",
)
def test_new_real_captures_through_public_parser_and_fact_contract(observation):
    family = "comware" if observation["device"].startswith("br") else "ios"
    identity = {
        "os_family": family,
        "profile_id": "h3c-comware7" if family == "comware" else "cisco-ios",
    }
    clean = (NEW / observation["files"]["txt"]["path"]).read_bytes().decode("utf-8")
    raw = (NEW / observation["files"]["raw.txt"]["path"]).read_bytes().decode("utf-8")
    prompt = [line.strip() for line in raw.splitlines() if line.strip()][-1]
    parsed = parse_cli(identity, observation["capability"], clean)
    original = parse_cli(
        identity, observation["capability"], raw, command=observation["command"], prompt=prompt
    )
    assert parsed.status == original.status == observation["expected_status"], parsed.warnings
    assert not parsed.rejected_count and not original.rejected_count
    assert parsed.records == original.records and parsed.summary == original.summary
    validate_records(
        FactInput(
            capability=observation["capability"],
            observed_at=observation["observed_at"],
            status=parsed.status,
            source="offline",
            platform_profile=identity["profile_id"],
            parser_ref=parsed.parser_ref,
            evidence_ref="test:new-vlan-capture",
            records=parsed.records,
            summary=parsed.fact_summary(),
        )
    )
    if observation["device"].startswith("br") and observation["capability"] == "trunk":
        assert parsed.status == "complete" and parsed.records == [] and parsed.summary == {}
    if observation["device"].startswith("core"):
        assert parsed.status == "unsupported" and not parsed.records and not parsed.summary


@pytest.mark.parametrize("device", ["dist1", "dist2", "acc1", "acc2"])
def test_campus_vlan_inventory_and_printed_members_are_separate(device):
    parsed = generic_vlan.parse(source(device, "vlan"), "ios")
    assert not parsed.rejected and not parsed.warnings
    assert {row["vlan_id"] for row in parsed.records} == {1, 10, 20, 1002, 1003, 1004, 1005}
    assert all(set(row) == {"vlan_id", "name"} for row in parsed.records)
    members = {row["vlan_id"]: row for row in parsed.summary["members"]}
    assert members[10]["status"] == "active"
    assert members[1002]["status"] == "act/unsup"
    assert members[10]["ports"] == (["Ethernet1/0"] if device.startswith("acc") else [])
    assert members[20]["ports"] == (["Ethernet1/1"] if device == "acc1" else [])
    # show vlan brief does not list trunks, even though they carry these VLANs.
    assert all("Ethernet0/1" not in row["ports"] for row in members.values())
    assert len(parsed.sources) == len(parsed.records)
    assert all(item["lines"] for item in parsed.sources)


@pytest.mark.parametrize("device,vid", [("vsr-1", 200), ("vsr-2", 100)])
def test_old_comware_access_vlan_members_are_real_observations(device, vid):
    text = (FIXTURES / "h3c/vsr1000-7.1.064" / device / "vlan.txt").read_text("utf-8")
    parsed = generic_vlan.parse(text, "comware")
    assert not parsed.rejected and not parsed.warnings
    members = {row["vlan_id"]: row for row in parsed.summary["members"]}
    assert members[vid]["ports"] == ["GigabitEthernet3/0"]
    assert "status" not in members[vid]


@pytest.mark.parametrize("device", ["dist1", "dist2", "acc1", "acc2"])
def test_campus_trunk_allowed_active_forwarding_sets_are_distinct(device):
    parsed = generic_trunk.parse(source(device, "trunk"), "ios")
    assert not parsed.rejected and not parsed.warnings
    rows = port_rows(parsed)
    assert len(rows) == (2 if device == "acc1" else 3)
    for row in rows.values():
        assert row["trunk_vlans"] == row["active_vlans"] == "10,20"
        assert row["native_vlan"] == 1
        assert row["l2_mode"] == "trunk" and row["encapsulation"] == "dot1q"
        assert "native_tagged" not in row
    if device == "acc1":
        assert rows["Ethernet0/1"]["forwarding_vlans"] == "10"
        assert rows["Ethernet0/2"]["forwarding_vlans"] == "20"
    elif device == "acc2":
        assert rows["Ethernet0/1"]["forwarding_vlans"] == "20"
        assert rows["Ethernet0/2"]["forwarding_vlans"] == "10"
        assert rows["Ethernet1/1"]["forwarding_vlans"] == "10,20"
    else:
        assert "Port-channel1" in rows
    assert len(parsed.sources) == len(rows)
    assert all(len(item["lines"]) == 4 for item in parsed.sources)


@pytest.mark.parametrize(
    "device,capability,module,command",
    [
        ("acc1", "vlan", generic_vlan, "show vlan brief"),
        ("acc2", "trunk", generic_trunk, "show interfaces trunk"),
        ("dist1", "switchport", generic_switchport, "show interfaces switchport"),
    ],
)
def test_raw_framing_and_cleaned_capture_have_the_same_facts(device, capability, module, command):
    new = capability == "switchport"
    clean = module.parse(source(device, capability, new=new), "ios")
    raw = source(device, capability, new=new, raw=True)
    prompt = [line.strip() for line in raw.splitlines() if line.strip()][-1]
    framed = module.parse(framed_text(raw, command=command, prompt=prompt), "ios")
    assert not clean.rejected and not framed.rejected
    assert framed.records == clean.records and framed.summary == clean.summary
    assert all(item["lines"] for item in framed.sources)


def test_vlan_unknown_row_and_duplicate_vid_cannot_become_complete():
    text = source("acc1", "vlan")
    unknown = generic_vlan.parse(text + "new unexplained table row\n", "ios")
    assert unknown.rejected == 1
    assert any("invalid_vlan" in warning for warning in unknown.warnings)
    row = next(line for line in text.splitlines() if line.startswith("10 "))
    duplicate = generic_vlan.parse(text + row + "\n", "ios")
    assert duplicate.rejected and any(
        "duplicate_vlan_id" in warning for warning in duplicate.warnings
    )
    assert 10 not in {r["vlan_id"] for r in duplicate.records}
    assert 10 not in {r["vlan_id"] for r in duplicate.summary["members"]}


def test_vlan_member_with_an_unknown_or_non_bridge_interface_is_rejected():
    text = source("acc1", "vlan")
    for bad in ("UnknownPort9", "Vlan10"):
        parsed = generic_vlan.parse(text.replace("active    Et1/0", f"active    {bad}"), "ios")
        assert parsed.rejected
        assert 10 not in {row["vlan_id"] for row in parsed.records}


def test_trunk_missing_section_and_port_never_fabricate_the_missing_set():
    text = source("acc1", "trunk")
    missing_section = text.split("Port           Vlans in spanning tree", 1)[0]
    parsed = generic_trunk.parse(missing_section, "ios")
    assert parsed.rejected and parsed.summary == {"invalid_structure": True}
    public = parse_cli({"os_family": "ios"}, "trunk", missing_section)
    assert public.status == "partial" and public.summary == {"invalid_structure": True}
    missing_port = text.replace("Et0/1          10\n", "")
    parsed = generic_trunk.parse(missing_port, "ios")
    assert parsed.rejected
    assert set(port_rows(parsed)) == {"Ethernet0/2"}
    assert parsed.summary["invalid_ports"] == ["Ethernet0/1"]


@pytest.mark.parametrize("bad", ["0", "4095", "20-10", "10,10", "10-20,15-30", "10,"])
def test_invalid_trunk_vlan_set_drops_its_port(bad):
    text = source("acc1", "trunk").replace("Et0/1          10,20", f"Et0/1          {bad}", 1)
    parsed = generic_trunk.parse(text, "ios")
    assert parsed.rejected
    assert "Ethernet0/1" not in port_rows(parsed)
    assert parsed.summary["invalid_ports"] == ["Ethernet0/1"]


def test_range_normalization_and_explicit_empty_are_bounded():
    assert generic_trunk.vid_range("20,10-11,12") == "10-12,20"
    assert generic_trunk.vid_range("ALL") == "1-4094"
    assert generic_trunk.vid_range("none") == ""
    assert generic_trunk.parse("\n", "comware").empty_verified
    nonempty = generic_trunk.parse("Unreviewed Comware trunk layout\n", "comware")
    assert not nonempty.empty_verified and nonempty.summary == {"invalid_structure": True}
    assert "trunk_format_not_verified" in nonempty.warnings


@pytest.mark.parametrize(
    "mutation",
    [
        lambda text: "Unknown global trunk syntax\n" + text,
        lambda text: text + "Unknown global trunk syntax\n",
        lambda text: text.splitlines()[0] + "\n" + text,
        lambda text: text + "Vlan10          10\n",
    ],
)
def test_unknown_trunk_structure_remains_a_global_veto_through_public_parser(mutation):
    # Preserve valid observations, but their mere survival must not permit a tag projection.
    parsed = parse_cli({"os_family": "ios"}, "trunk", mutation(source("acc1", "trunk")))
    assert parsed.status == "partial"
    assert parsed.summary["invalid_structure"] is True
    assert {row["ifname"] for row in parsed.summary["ports"]} == {"Ethernet0/1", "Ethernet0/2"}


def test_known_trunk_port_damage_is_a_local_veto_and_other_port_is_preserved():
    damaged = source("acc1", "trunk") + "Et0/1          invalid forwarding row\n"
    parsed = parse_cli({"os_family": "ios"}, "trunk", damaged)
    assert parsed.status == "partial"
    assert parsed.summary["invalid_ports"] == ["Ethernet0/1"]
    assert "invalid_structure" not in parsed.summary
    assert [row["ifname"] for row in parsed.summary["ports"]] == ["Ethernet0/2"]


def test_unknown_trunk_format_with_no_valid_rows_does_not_disappear_as_failed():
    parsed = parse_cli({"os_family": "ios"}, "trunk", "Unknown global trunk syntax\n")
    assert parsed.status == "partial" and parsed.records == []
    assert parsed.summary == {"invalid_structure": True}


@pytest.mark.parametrize("device", ["dist1", "dist2"])
def test_real_switchport_native_tagging_and_bundle_members(device):
    parsed = generic_switchport.parse(source(device, "switchport", new=True), "ios")
    assert not parsed.rejected and not parsed.warnings
    rows = port_rows(parsed)
    assert rows["Port-channel1"]["l2_mode"] == "trunk"
    assert rows["Port-channel1"]["trunk_vlans"] == "10,20"
    for member in ("Ethernet1/0", "Ethernet1/1"):
        assert rows[member]["lag_parent"] == "Port-channel1"
        assert rows[member]["oper_mode"] == "trunk (member of bundle Po1)"
    for row in rows.values():
        # The separate private-VLAN native-tagging field says enabled.
        assert row["admin_native_tagged"] is False
        assert "native_tagged" not in row
        assert row["native_vlan"] == 1
    idle = rows["Ethernet0/3"]
    assert idle["admin_mode"] == "dynamic auto" and idle["oper_mode"] == "down"
    assert idle["access_vlan"] == 1 and idle["trunk_vlans"] == "1-4094"
    assert "l2_mode" not in idle and "pvid" not in idle


@pytest.mark.parametrize("device", ["acc1", "acc2"])
def test_real_access_switchport_and_native_vlan_have_distinct_meanings(device):
    parsed = generic_switchport.parse(source(device, "switchport", new=True), "ios")
    assert not parsed.rejected and not parsed.warnings
    rows = port_rows(parsed)
    access = rows["Ethernet1/0"]
    assert access["admin_mode"] == access["oper_mode"] == "static access"
    assert access["l2_mode"] == "access" and access["access_vlan"] == access["pvid"] == 10
    assert access["native_vlan"] == 1
    assert "encapsulation" not in access
    if device == "acc1":
        assert rows["Ethernet1/1"]["l2_mode"] == "access"
        assert rows["Ethernet1/1"]["access_vlan"] == rows["Ethernet1/1"]["pvid"] == 20
    else:
        assert rows["Ethernet1/1"]["l2_mode"] == "trunk"
        assert rows["Ethernet1/1"]["trunk_vlans"] == "10,20"
    for name in ("Ethernet0/1", "Ethernet0/2"):
        assert rows[name]["l2_mode"] == "trunk"
        assert rows[name]["pvid"] == rows[name]["native_vlan"] == 1
        assert rows[name]["admin_native_tagged"] is False
        assert "native_tagged" not in rows[name]


def test_switchport_unknown_native_tagging_stays_unknown():
    block = switchport_block().replace("Administrative Native VLAN tagging: disabled\n", "")
    parsed = generic_switchport.parse(block, "ios")
    assert not parsed.rejected
    [row] = port_rows(parsed).values()
    assert row["l2_mode"] == "trunk" and "native_tagged" not in row
    assert "admin_native_tagged" not in row


def test_synthetic_admin_native_tagging_does_not_establish_operational_tagging():
    block = switchport_block().replace(
        "Administrative Native VLAN tagging: disabled",
        "Administrative Native VLAN tagging: enabled",
    )
    parsed = generic_switchport.parse(block, "ios")
    assert not parsed.rejected
    [row] = port_rows(parsed).values()
    assert row["admin_native_tagged"] is True and "native_tagged" not in row
    explicit = generic_switchport.parse(
        block + "Operational Native VLAN tagging: disabled\n", "ios"
    )
    assert not explicit.rejected
    [row] = port_rows(explicit).values()
    assert row["admin_native_tagged"] is True and row["native_tagged"] is False


def test_synthetic_static_access_down_uses_explicit_admin_mode():
    # Derived from a real trunk block; this is a synthetic mode mutation, not a capture.
    block = switchport_block().replace(
        "Administrative Mode: trunk", "Administrative Mode: static access"
    )
    block = block.replace(
        "Operational Mode: trunk (member of bundle Po1)", "Operational Mode: down"
    )
    block = block.replace("Access Mode VLAN: 1 (default)", "Access Mode VLAN: 10 (USERS-A)")
    parsed = generic_switchport.parse(block, "ios")
    assert not parsed.rejected
    [row] = port_rows(parsed).values()
    assert row["l2_mode"] == "access" and row["pvid"] == row["access_vlan"] == 10
    assert row["oper_mode"] == "down"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda block: block.replace("Trunking VLANs Enabled: 10,20\n", ""),
        lambda block: block.replace(
            "Administrative Native VLAN tagging: disabled",
            "Administrative Native VLAN tagging: maybe",
        ),
        lambda block: block + "Unrecognized VLAN feature: enabled\n",
        lambda block: block + "Access Mode VLAN: 20 (USERS-B)\n",
    ],
)
def test_incomplete_or_ambiguous_switchport_block_provides_no_port_facts(mutation):
    parsed = generic_switchport.parse(mutation(switchport_block()), "ios")
    assert parsed.rejected
    assert port_rows(parsed) == {}
    assert parsed.summary == {"invalid_ports": ["Ethernet1/0"]}


def test_duplicate_switchport_interface_is_not_arbitrarily_selected():
    block = switchport_block()
    parsed = generic_switchport.parse(block + block, "ios")
    assert parsed.rejected and port_rows(parsed) == {}
    assert parsed.summary == {"invalid_ports": ["Ethernet1/0"]}
    assert any("duplicate_switchport_interface" in warning for warning in parsed.warnings)


def test_bad_explicit_access_block_keeps_its_veto_while_valid_trunk_remains():
    text = source("acc1", "switchport", new=True)
    blocks = re.split(r"(?m)(?=^Name: )", text)
    access = next(block for block in blocks if block.startswith("Name: Et1/0\n"))
    trunk = next(block for block in blocks if block.startswith("Name: Et0/2\n"))
    # The access-mode evidence conflicts with a trunk observation for Et1/0.
    # An extra unknown field must not turn this block into apparent absence.
    damaged = access + "Unknown switchport feature: enabled\n"
    parsed = parse_cli({"os_family": "ios"}, "switchport", damaged + trunk)
    assert parsed.status == "partial"
    assert parsed.summary["invalid_ports"] == ["Ethernet1/0"]
    assert [row["ifname"] for row in parsed.summary["ports"]] == ["Ethernet0/2"]
    assert parsed.summary["ports"][0]["l2_mode"] == "trunk"


def test_all_bad_known_port_blocks_stay_partial_so_the_veto_survives_service_filtering():
    parsed = parse_cli(
        {"os_family": "ios"},
        "switchport",
        switchport_block() + "Unknown switchport feature: enabled\n",
    )
    assert parsed.status == "partial" and parsed.records == []
    assert parsed.summary == {"invalid_ports": ["Ethernet1/0"]}


@pytest.mark.parametrize(
    "damage",
    [
        "Unknown table preamble\n",
        "Name:\n",
        "Name: UnrecognizedPhysicalPort9\nSwitchport: Enabled\n",
    ],
)
def test_unidentifiable_switchport_structure_is_a_global_veto(damage):
    parsed = parse_cli({"os_family": "ios"}, "switchport", damage + switchport_block())
    assert parsed.status == "partial"
    assert parsed.summary["invalid_structure"] is True
    assert [row["ifname"] for row in parsed.summary["ports"]] == ["Ethernet1/0"]
    global_only = parse_cli({"os_family": "ios"}, "switchport", damage)
    assert global_only.status == "partial"
    assert global_only.summary == {"invalid_structure": True}

"""Real VSR captures and explicitly synthetic five-style parser contracts."""

import json
from pathlib import Path

import pytest

from src.apps.topology.backend.collectors.normalize import parser_text
from src.apps.topology.backend.collectors.parse import MAX_BYTES, parse
from src.apps.topology.backend.facts.repository import FactInput, validate_records

ROOT = Path(__file__).parents[1] / "fixtures/topology"
LAB = ROOT / "h3c/vsr1000-7.1.064"
STYLES = json.loads((ROOT / "synthetic/generic-styles.json").read_text("utf-8"))["cases"]
FILES = {"lldp": "lldp_verbose", "ip": "ip_interfaces"}


def lab(capability, device="vsr-1", raw=False):
    name = FILES.get(capability, capability) + (".raw" if raw else "") + ".txt"
    return (LAB / device / name).read_bytes().decode("utf-8")


def run(capability, text, family="comware", **kwargs):
    return parse(
        {"os_family": family, "profile_id": None}, capability, text, use_templates=False, **kwargs
    )


@pytest.mark.parametrize("device", ["vsr-1", "vsr-2"])
@pytest.mark.parametrize(
    "capability,count",
    [
        ("lldp", 2),
        ("arp", 4),
        ("mac", 1),
        ("interfaces", 10),
        ("ip", 4),
        ("lldp_local", 0),
        ("version", 0),
    ],
)
def test_real_raw_and_clean_without_profile(device, capability, count):
    result = run(capability, lab(capability, device))
    raw = lab(capability, device, raw=True)
    original = run(capability, raw, command=raw.splitlines()[0], prompt="<H3C>")
    assert result.status == original.status == "complete"
    assert result.records == original.records
    assert result.summary == original.summary
    assert len(result.records) == count
    assert result.tier == "generic" and result.confidence == "medium"
    assert result.sources
    assert all(source["lines"] for source in result.sources)
    # Evidence metadata lives alongside rows, not in forbidden typed-table columns.
    body = FactInput(
        capability=capability,
        observed_at="2026-09-28T12:00:00Z",
        status=result.status,
        source="offline",
        platform_profile="generic",
        parser_ref=result.parser_ref,
        evidence_ref="test",
        records=result.records,
        summary=result.fact_summary(),
    )
    validate_records(body)


def test_real_lldp_exact_keys_multiline_and_multi_address_identity():
    result = run("lldp", lab("lldp"))
    first, second = result.records
    assert first["remote_chassis_id"] == second["remote_chassis_id"] == "74:25:8a:e4:12:52"
    assert first["remote_mgmt_addresses"] == ["10.10.1.2"]
    assert second["remote_mgmt_addresses"] == ["2.2.2.1"]
    assert len(first["remote_system_description"].splitlines()) == 3
    assert first["remote_port_id"] == "GigabitEthernet1/0"
    assert first["remote_port_subtype"] == "Interface name"
    assert 14 in result.sources[0]["lines"]


def test_real_local_identity_med_and_interface_tables():
    local = run("lldp_local", lab("lldp_local"))
    assert local.summary["serial"] == "0cd25bc50000e49826062710"
    assert local.summary["model"] == "VSR1000"
    assert len(local.summary["management_addresses"]) == 3  # Repeated LLDP agents deduplicate.
    interfaces = {r["ifname"]: r for r in run("interfaces", lab("interfaces")).records}
    assert interfaces["GigabitEthernet3/0"]["access_vlan"] == 200
    assert interfaces["GigabitEthernet3/0"]["speed_mbps"] == 1000
    assert interfaces["NULL0"]["type"] == "null"
    assert interfaces["InLoopBack0"]["type"] == "loopback"
    assert interfaces["REG0"]["type"] == "register"
    assert "admin_status" not in interfaces["GigabitEthernet4/0"]
    ip_rows = run("ip", lab("ip")).records
    assert "GigabitEthernet3/0" not in {r["ifname"] for r in ip_rows}
    assert {r["address"] for r in ip_rows} == {"10.10.1.1", "2.2.2.2", "4.4.4.4", "192.168.1.1"}
    arp = run("arp", lab("arp"))
    assert all("age_s" not in r for r in arp.records)
    assert "age_unit_unknown" in arp.warnings[0]


@pytest.mark.parametrize("case", STYLES, ids=lambda c: c["family"])
@pytest.mark.parametrize("capability", ["lldp", "arp", "mac"])
def test_synthetic_five_styles(case, capability):
    result = run(capability, case[capability], case["family"])
    assert result.status == "complete", result.warnings
    assert len(result.records) == 1
    row = result.records[0]
    assert row[{"lldp": "local_if", "arp": "ifname", "mac": "port"}[capability]] == case["port"]
    assert row["remote_chassis_id" if capability == "lldp" else "mac"] == "aa:bb:cc:dd:ee:ff"
    if capability == "lldp":
        assert row["remote_mgmt_addresses"] == ["192.0.2.2"]
    if capability == "arp":
        assert row["ip"] == "192.0.2.2"
        if case["family"] in {"ios", "ruijie"}:
            assert row["age_s"] == 720


@pytest.mark.parametrize(
    "message",
    [
        "% Incomplete command found at '^' position.",
        "% Unrecognized command found at '^' position.",
        "% Wrong parameter found at '^' position.",
        "% Too many parameters found at '^' position.",
        "The operation is not supported on the specified slot or subslot.",
    ],
)
def test_command_unsupported(message):
    result = run("inventory", message)
    assert result.status == "unsupported" and result.records == []


@pytest.mark.parametrize(
    "capability,text",
    [
        ("lag", "No link aggregation group exists."),
        ("vrfs", "L3VPN is not configured."),
        ("vrfs", "Total VPN-Instances configured : 0"),
        ("lldp", "No LLDP neighbors found."),
        ("mac", "MAC Address  VLAN ID  State  Port/Nickname  Aging\n"),
        (
            "ip",
            "Interface Physical Protocol IP address/Mask VPN instance Description\n"
            "GE1/0 down down -- -- --\n",
        ),
    ],
)
def test_verified_empty(capability, text):
    result = run(capability, text)
    assert result.status == "complete" and result.records == []
    assert run(capability, text, complete=False).status == "failed"


@pytest.mark.parametrize(
    "capability", ["lldp", "arp", "mac", "interfaces", "ip", "version", "routes"]
)
@pytest.mark.parametrize("text", ["", "   ", "some unknown text"])
def test_unknown_is_never_empty_complete(capability, text):
    result = run(capability, text)
    assert result.status == "failed" and result.records == []
    assert result.fact_summary() == {}


@pytest.mark.parametrize(
    "suffix", ["\n---- More ----", "\n[truncated]", "\nPress any key to continue"]
)
def test_truncated_and_paged_data_never_complete(suffix):
    result = run("arp", lab("arp") + suffix)
    assert result.status == "partial"
    assert "collection_incomplete" in result.warnings


@pytest.mark.parametrize("capability", ["arp", "mac", "interfaces", "ip"])
def test_unrecognized_tail_does_not_silently_drop_rows(capability):
    assert run(capability, lab(capability) + "\nBROKEN ROW unexpected syntax\n").status == "partial"


def test_count_mismatch_transport_budget_and_routes():
    assert run("arp", lab("arp") + "\nTotal entries: 5").status == "partial"
    assert run("arp", lab("arp") + "\nTotal entries: 4").status == "complete"
    assert run("arp", lab("arp"), complete=False).status == "partial"
    assert run("routes", lab("routes")).status == "failed"
    assert run("mac", "x" * (MAX_BYTES + 1)).status == "failed"
    assert run("mac", b"bytes").status == "failed"


def test_lldp_incomplete_neighbor_and_bad_address():
    text = STYLES[0]["lldp"]
    assert run("lldp", text + "\nChassis ID: 0011-2233-4455\n").status == "partial"
    assert run("lldp", text.replace("192.0.2.2", "999.1.1.1")).status == "partial"
    assert run("lldp", text.replace("aabb-ccdd-eeff", "not-a-mac")).status == "failed"
    assert run("lldp", "Port ID: Gi1/0\nManagement address: 192.0.2.1").status == "failed"
    multiple = text + "\nChassis type: MAC address\nChassis ID: 0011-2233-4455\nPort ID: Gi2/0\n"
    result = run("lldp", multiple)
    assert result.status == "complete" and len(result.records) == 2
    assert result.records[1]["remote_mgmt_addresses"] == []


def test_ip_masks_ipv6_vrf_and_no_fabricated_prefixes():
    result = run("ip", "Interface IP-Address Mask\nGi1/0/1 192.0.2.1 255.255.255.0\n", "ios")
    assert result.status == "complete"
    assert result.records[0]["prefix_len"] == 24
    assert run("ip", "Gi1/0/1 192.0.2.1 up up", "ios").status == "failed"
    assert run("ip", "Gi1/0/1 192.0.2.1/99 up up", "ios").status == "failed"
    invalid = "Interface IP address/Mask VPN instance\nGE1/0 999.1.1.1 --"
    assert run("ip", invalid).status == "failed"
    result = run("ip", "Interface IP address/Mask VPN instance\nGE0/0/1 2001:db8::1/64 blue", "vrp")
    assert result.status == "complete" and result.records[0]["vrf"] == "blue"
    assert result.records[0]["prefix_len"] == 64
    assert (
        run("ip", "Interface IP address/Mask VPN instance\nGE0/0/1 192.0.2.1/24", "vrp").status
        == "failed"
    )


def test_arp_ipv6_scoped_vrf_and_invalid_rows():
    result = run("arp", "VRF: blue\n2001:db8::2 aa:bb:cc:dd:ee:ff GE1/0")
    assert result.status == "complete"
    assert result.records[0]["vrf"] == "blue"
    assert result.records[0]["ip"] == "2001:db8::2"
    assert run("arp", "192.0.2.1 incomplete GE1/0").status == "failed"
    assert run("arp", "999.1.1.1 aa:bb:cc:dd:ee:ff GE1/0").status == "failed"


def test_raw_evidence_line_coordinates_and_version():
    text = lab("arp", raw=True)
    result = run("arp", text)
    lines = parser_text(text).text.splitlines()
    for source in result.sources:
        assert lines[source["lines"][0] - 1] == source["raw"]["text"]
    version = run("version", lab("version"))
    assert version.summary == {"model": "VSR1000", "os_version": "7.1.064, Release 1362P12"}
    assert "hostname" not in version.summary
    assert "serial" not in version.summary  # VSR serial is in LLDP-MED, not version.


@pytest.mark.parametrize(
    "family,port,canonical",
    [
        ("comware", "GE1/0", "GigabitEthernet1/0"),
        ("vrp", "GE0/0/1", "GigabitEthernet0/0/1"),
        ("ios", "Gi1/0/1", "GigabitEthernet1/0/1"),
        ("nxos", "Eth1/1", "Ethernet1/1"),
        ("ruijie", "GigabitEthernet0/1", "GigabitEthernet0/1"),
    ],
)
def test_synthetic_interface_and_explicit_mask_rows(family, port, canonical):
    interfaces = run("interfaces", f"Interface Physical Protocol\n{port} up up\n", family)
    assert interfaces.status == "complete"
    assert interfaces.records[0]["ifname"] == canonical
    assert interfaces.records[0]["oper_status"] == "up"
    addresses = run("ip", f"Interface IP address/Mask\n{port} 192.0.2.1/24\n", family)
    assert addresses.status == "complete"
    assert addresses.records[0] == {
        "ifname": canonical,
        "address": "192.0.2.1",
        "prefix_len": 24,
        "vrf": "default",
    }


def test_explicit_zero_total_and_contradictory_body():
    assert run("lldp", "Total entries: 0").status == "complete"
    assert run("mac", "Total entries: 0").status == "complete"
    assert run("mac", "BROKEN DATA\nTotal entries: 0").status == "failed"
    assert run("lldp", STYLES[0]["lldp"] + "\nTotal entries: 0").status == "partial"


@pytest.mark.parametrize("device", ["vsr-1", "vsr-2"])
def test_actual_empty_vrf_includes_license_metadata(device):
    text = lab("vrfs", device)
    assert run("vrfs", text).status == "complete"
    assert run("vrfs", text.replace("configured : 0", "configured : 1")).status == "failed"
    assert run("vrfs", text + "\nUnexpected VPN row").status == "failed"
    assert run("vrfs", text, complete=False).status == "failed"


def test_framing_only_removed_at_known_edges():
    text = "display mac-address\nMAC Address  VLAN ID  State  Port/Nickname  Aging\n<H3C>"
    assert run("mac", text).status == "failed"
    assert run("mac", text, command="display mac-address", prompt="<H3C>").status == "complete"
    assert run("mac", text, command="show mac address-table", prompt="<H3C>").status == "failed"


def test_duplicate_interfaces_and_bad_bridge_rows():
    assert run("interfaces", "GE1/0 UP UP\nGE1/0 DOWN DOWN").status == "partial"
    assert run("interfaces", "GE1/0 unknown state description up").status == "failed"
    assert (
        run("interfaces", "Brief information on interfaces in bridge mode:\nGE1/0 UP 1G F A").status
        == "failed"
    )
    assert (
        run("interfaces", "GE1/0 administratively down down").records[0]["admin_status"] == "down"
    )

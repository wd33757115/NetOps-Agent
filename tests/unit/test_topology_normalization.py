"""Canonical identity inputs, safe unknowns, real Comware whitespace and explicit units."""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.apps.topology.backend.collectors.ifname import normalize_ifname
from src.apps.topology.backend.collectors.normalize import (
    age_seconds,
    interface_address,
    ip_address,
    mac_address,
    network_prefix,
    observed_time,
    parser_text,
    speed_mbps,
    vrf_name,
)


@pytest.mark.parametrize(
    "platform,raw,canonical,kind",
    [
        ("comware", "GE1/0", "GigabitEthernet1/0", "ethernet"),
        ("comware", "GE1/0/1", "GigabitEthernet1/0/1", "ethernet"),
        ("comware", "gigabitethernet1/0", "GigabitEthernet1/0", "ethernet"),
        ("comware", "XGE1/0/1", "Ten-GigabitEthernet1/0/1", "ethernet"),
        ("comware", "BAGG1", "Bridge-Aggregation1", "lag"),
        ("comware", "RAGG2", "Route-Aggregation2", "lag"),
        ("comware", "Vlan200", "Vlan-interface200", "vlanif"),
        ("comware", "Loop0", "LoopBack0", "loopback"),
        ("comware", "InLoop0", "InLoopBack0", "loopback"),
        ("comware", "NULL0", "NULL0", "null"),
        ("comware", "REG0", "REG0", "register"),
        ("nxos", "Eth1/1", "Ethernet1/1", "ethernet"),
        ("nxos", "Po10", "port-channel10", "lag"),
        ("nxos", "mgmt0", "mgmt0", "mgmt"),
        ("ios", "Gi0/1", "GigabitEthernet0/1", "ethernet"),
        ("ios", "Te1/0/1", "TenGigabitEthernet1/0/1", "ethernet"),
        ("ios", "Fa0/0", "FastEthernet0/0", "ethernet"),
        ("ios", "Po1", "Port-channel1", "lag"),
        ("vrp", "GE0/0/1", "GigabitEthernet0/0/1", "ethernet"),
        ("vrp", "XGE0/0/1", "XGigabitEthernet0/0/1", "ethernet"),
        ("vrp", "Eth-Trunk1", "Eth-Trunk1", "lag"),
        ("vrp", "Vlanif100", "Vlanif100", "vlanif"),
    ],
)
def test_platform_aliases_are_idempotent_and_preserve_raw(platform, raw, canonical, kind):
    result = normalize_ifname(raw, platform)
    assert result.name == canonical and result.kind == kind and result.raw_name == raw
    assert normalize_ifname(result.name, platform).name == canonical


@pytest.mark.parametrize(
    "name", ["InLoop0", "Loop0", "NULL0", "REG0", "Vlan200", "BAGG1", "GE1/0.200"]
)
def test_logical_and_internal_ports_are_not_physical(name):
    assert not normalize_ifname(name, "comware").physical


@pytest.mark.parametrize(
    "name,platform",
    [
        ("GE1/0", "unknown"),
        ("Po1", "comware"),
        ("GigabİtEthernet1/0", "comware"),
        ("GE１/０", "comware"),
        ("GE1/0/1:1", "comware"),
        ("GE1/0 other", "comware"),
    ],
)
def test_unknown_or_unicode_names_are_preserved_without_guessing(name, platform):
    result = normalize_ifname(name, platform)
    assert result.name == name and not result.recognized and not result.physical


@pytest.mark.parametrize("name", ["", " ", "GE1/0\n", "GE1/0\x00", "GE\t1/0", None])
def test_invalid_interface_input_rejected(name):
    with pytest.raises(ValueError):
        normalize_ifname(name, "comware")


@pytest.mark.parametrize(
    "raw",
    ["AABBCCDDEEFF", "aa:bb:cc:dd:ee:ff", "AA-BB-CC-DD-EE-FF", "aabb-ccdd-eeff", "aabb.ccdd.eeff"],
)
def test_mac_formats_equivalent(raw):
    assert mac_address(raw) == "aa:bb:cc:dd:ee:ff"


@pytest.mark.parametrize(
    "raw",
    [
        "aabb.ccdd.eef",
        "aa:bb-cc:dd:ee:ff",
        " aa:bb:cc:dd:ee:ff",
        "aabb/ccdd/eeff",
        "００:11:22:33:44:55",
        "aabb.ccdd.eeff\n",
        None,
    ],
)
def test_invalid_mac_not_silently_repaired(raw):
    with pytest.raises(ValueError):
        mac_address(raw)


def test_ipv6_ip_prefix_and_interface_host_address_have_distinct_semantics():
    assert ip_address("2001:0DB8:0000::1") == "2001:db8::1"
    assert network_prefix("2001:0DB8::/64") == "2001:db8::/64"
    assert interface_address("192.0.2.13/24") == {"address": "192.0.2.13", "prefix_len": 24}
    with pytest.raises(ValueError):
        network_prefix("192.0.2.13/24")


@pytest.mark.parametrize(
    "raw", ["10.0.0.01", "10.0.0.1 ", "fe80::1%GE1/0", "１０.0.0.1", "example.com"]
)
def test_ambiguous_ip_rejected(raw):
    with pytest.raises(ValueError):
        ip_address(raw)


def test_vrf_is_case_sensitive_and_public_alias_is_platform_scoped():
    assert vrf_name(None, "vrp") == vrf_name("--", "comware") == "default"
    assert vrf_name("_public_", "vrp") == "default"
    assert vrf_name("_public_", "comware") == "_public_"
    assert vrf_name("BLUE", "ios") != vrf_name("blue", "ios")
    with pytest.raises(ValueError):
        vrf_name("BLUE\u2003", "ios")


def test_observation_time_uses_aware_command_completion():
    when = datetime(2026, 9, 28, 16, 32, tzinfo=timezone(timedelta(hours=8)))
    assert observed_time(when) == datetime(2026, 9, 28, 8, 32, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        observed_time(when.replace(tzinfo=None))


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1G", 1000),
        ("1G(a)", 1000),
        ("2.5G", 2500),
        ("100M", 100),
        ("100000Kbps", 100),
        ("--", None),
    ],
)
def test_explicit_speed_units(raw, expected):
    assert speed_mbps(raw) == expected


@pytest.mark.parametrize("raw", ["1000", "-1G", "NaNG", "10K", True, []])
def test_speed_does_not_guess_units_or_round_away_fraction(raw):
    with pytest.raises(ValueError):
        speed_mbps(raw)


def test_age_requires_units_and_preserves_unknown():
    assert age_seconds("19", unit="min") == 1140
    assert age_seconds(19, unit="s") == 19
    assert age_seconds("--", unit="s") is None
    for value in (True, -1, 1.5, []):
        with pytest.raises(ValueError):
            age_seconds(value, unit="s")


def test_paging_cleanup_retains_incomplete_flag_and_original_raw():
    raw = (
        "\x00\x1b[31mfirst\r\r\n---- More ----\x1b[16D                "
        "\x08\nsecond\rthird\n\rlast\x1b[0m"
    )
    result = parser_text(raw)
    assert result.had_paging and result.text == "first\n\nsecond\nthird\nlast"
    assert "\x00" in raw and "More" in raw
    assert parser_text(result.text).text == result.text


def test_real_lab_ports_and_line_endings_match_both_tables_and_lldp():
    root = Path(__file__).resolve().parents[1] / "fixtures/topology/h3c/vsr1000-7.1.064"
    for device in ("vsr-1", "vsr-2"):
        text = (root / device / "interfaces.txt").read_text()
        ports = [
            line.split()[0]
            for line in text.splitlines()
            if re.match(r"^(GE|Loop|InLoop|NULL|REG|Vlan)[0-9]", line)
        ]
        normalized = {normalize_ifname(name, "comware").name for name in ports}
        assert {"GigabitEthernet1/0", "GigabitEthernet2/0", "GigabitEthernet3/0"} <= normalized
        neighbors = (root / device / "lldp_verbose.txt").read_text()
        for name in re.findall(r"of port \d+\[([^]]+)\]", neighbors):
            assert normalize_ifname(name, "comware").name in normalized
        raw = (root / device / "interfaces.raw.txt").read_bytes().decode()
        result = parser_text(raw)
        assert not result.had_paging and "\r" not in result.text
        assert "route mode:\nLink:" in result.text

"""Validate evidence and E1–E17 observations, without implementing production parsers."""

import hashlib
import json
import re
from pathlib import Path

import pytest

from scripts.topology import collect_lab_samples as capture
from scripts.topology.import_lab_fixtures import CAPABILITIES, prepare

ROOT = Path(__file__).resolve().parents[1] / "fixtures/topology/h3c/vsr1000-7.1.064"
MANIFEST = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
EXPECTED = json.loads((ROOT / "expected.json").read_text(encoding="utf-8"))


def read(device, capability):
    return (ROOT / device / f"{capability}.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "observation", MANIFEST["observations"], ids=lambda o: o["device"] + "-" + o["capability"]
)
def test_each_capture_preserves_bytes_and_replays_clean_copy(observation):
    files = observation["files"]
    for descriptor in files.values():
        raw = (ROOT / descriptor["path"]).read_bytes()
        assert raw.isascii()
        assert len(raw) == descriptor["bytes"]
        assert hashlib.sha256(raw).hexdigest() == descriptor["sha256"]
    original = (ROOT / files["raw.txt"]["path"]).read_bytes()
    clean = (ROOT / files["txt"]["path"]).read_bytes()
    assert b"\r\r\n" in original  # E8, before universal-newline decoding.
    assert b"\r" not in clean
    assert capture.clean(original.decode(), observation["command"]).encode() == clean


def test_capabilities_are_complete_and_unsupported_is_not_transport_success():
    for device in EXPECTED["devices"]:
        rows = [o for o in MANIFEST["observations"] if o["device"] == device]
        assert {o["capability"] for o in rows} == CAPABILITIES
        inventory = next(o for o in rows if o["capability"] == "inventory")
        assert inventory["transport_status"] == "ok"
        assert inventory["expected_status"] == "unsupported"


def test_e1_e2_same_hostname_but_different_strong_identities():
    for device, expected in EXPECTED["devices"].items():
        local = read(device, "lldp_local")
        assert re.search(r"System name\s*:\s*H3C\n", local)
        assert expected["serial"] in local and expected["chassis_mac"] in local
        assert re.search(r"Model name\s*:\s*VSR1000", local)
    assert EXPECTED["devices"]["vsr-1"]["serial"] != EXPECTED["devices"]["vsr-2"]["serial"]
    assert (
        EXPECTED["devices"]["vsr-1"]["chassis_mac"] != EXPECTED["devices"]["vsr-2"]["chassis_mac"]
    )


@pytest.mark.parametrize("device", ["vsr-1", "vsr-2"])
def test_e3_e4_e5_e6_port_headers_full_keys_multiline_and_peer_aliases(device):
    expected = EXPECTED["devices"][device]
    peer = EXPECTED["devices"][expected["peer"]]
    blocks = re.split(r"LLDP neighbor-information of port ", read(device, "lldp_verbose"))[1:]
    assert len(blocks) == 2
    for block in blocks:
        header = re.match(r"(17|33)\[(GigabitEthernet[12]/0)\]:", block)
        assert header is not None
        assert peer["chassis_mac"] in block
        keys = re.findall(r"^\s*(Management address[^:]*?)\s*:", block, re.M)
        assert keys == [
            "Management address type",
            "Management address",
            "Management address interface type",
            "Management address interface ID",
            "Management address OID",
        ]
        ip = re.search(r"^\s*Management address\s*:\s*(\S+)", block, re.M).group(1)
        assert ip == expected["remote_management_by_port"][header.group(2)]
        assert re.search(r"System description\s*:\s*\n\s+H3C Comware[^\n]+\n\s+H3C VSR1000", block)
    # Same chassis, two different addresses: neither sysname nor login IP is identity.
    assert len(set(expected["remote_management_by_port"].values())) == 2


@pytest.mark.parametrize("device", ["vsr-1", "vsr-2"])
def test_e7_e15_interface_modes_and_names(device):
    interfaces, ip = read(device, "interfaces"), read(device, "ip_interfaces")
    route, bridge = interfaces.split("Brief information on interfaces in bridge mode:")
    assert "in route mode:" in route
    assert "GE1/0" in route and "GE2/0" in route and "Loop0" in route
    assert f"Vlan{EXPECTED['devices'][device]['vlan']}" in route
    assert "GE3/0" in bridge and "GE3/0" not in ip
    assert "GigabitEthernet1/0" in read(device, "lldp_verbose")


@pytest.mark.parametrize("device", ["vsr-1", "vsr-2"])
def test_e9_e10_unsupported_and_valid_empty_are_distinct(device):
    assert "The operation is not supported" in read(device, "inventory")
    assert "% Incomplete command" in read(device, "irf")
    assert "No link aggregation group exists." in read(device, "lag")
    assert "Total VPN-Instances configured : 0" in read(device, "vrfs")


@pytest.mark.parametrize("device", ["vsr-1", "vsr-2"])
def test_e11_route_count_protocols_and_special_interfaces(device):
    text = read(device, "routes")
    count = EXPECTED["devices"][device]["route_count"]
    assert f"Destinations : {count}\tRoutes : {count}" in text
    rows = re.findall(r"^\d+\.\d+\.\d+\.\d+/\d+\s+\S+.*$", text, re.M)
    assert len(rows) == count
    assert "O_INTRA" in text and "Direct" in text
    assert "224.0.0.0/4" in text and "NULL0" in text
    assert "127.0.0.0/8" in text and "InLoop0" in text


@pytest.mark.parametrize("device", ["vsr-1", "vsr-2"])
def test_e13_e14_e16_shared_segment_and_endpoint_evidence(device):
    expected = EXPECTED["devices"][device]
    arp = read(device, "arp")
    peer_ip = EXPECTED["devices"][expected["peer"]]["management"]
    assert re.search(rf"^{re.escape(peer_ip)}\s+\S+\s+--\s+GE1/0", arp, re.M)
    assert re.search(r"^10\.10\.1\.254\s+\S+\s+--\s+GE1/0", arp, re.M)
    assert "10.10.1.254" not in read(device, "lldp_verbose")
    assert re.search(
        rf"^{re.escape(expected['endpoint_ip'])}\s+{expected['endpoint_mac']}"
        rf"\s+{expected['vlan']}\s+GE3/0",
        arp,
        re.M,
    )
    mac_rows = [line.split() for line in read(device, "mac").splitlines()[1:] if line.strip()]
    assert len(mac_rows) == 1 and mac_rows[0][0] == expected["endpoint_mac"]
    assert mac_rows[0][3] == "GE3/0"
    # Hidden switches are lab ground truth; a single learned MAC cannot prove direct attachment.
    assert EXPECTED["lab_wiring"]["endpoint_attachment_confidence"] == "medium"


def test_e17_one_direction_has_no_matching_route_to_loopback():
    assert "3.3.3.3/32" not in read("vsr-1", "routes")
    assert re.search(
        r"^4\.4\.4\.4/32\s+O_INTRA\s+10\s+1\s+2\.2\.2\.2\s+GE2/0", read("vsr-2", "routes"), re.M
    )


@pytest.mark.parametrize("question", [False, True])
def test_e12_conditional_wake_never_answers_interactive_question(monkeypatch, question):
    # Synthetic transport: the command fixtures don't include the initial banner.
    tick, sent = [0.0], []
    chunks = ["Password expired. New password:" if question else "Welcome\r\n"]

    class Channel:
        closed = False

        def recv_ready(self):
            return bool(chunks)

        def recv(self, size):
            return chunks.pop(0).encode()

        def send(self, value):
            sent.append(value)
            chunks.append("<H3C>")

    monkeypatch.setattr(capture.time, "monotonic", lambda: tick[0])
    monkeypatch.setattr(capture.time, "sleep", lambda amount: tick.__setitem__(0, tick[0] + amount))
    if question:
        with pytest.raises(capture.PromptTimeout):
            capture.read_until_prompt(Channel(), 5, wake=True)
        assert not sent
    else:
        assert capture.read_until_prompt(Channel(), 5, wake=True).endswith("<H3C>")
        assert sent == ["\r"]


def test_import_scan_checks_nul_files_and_does_not_print_secret(tmp_path, capsys):
    # Deliberately NUL-padded so the existing scanner's binary skip is exercised.
    secret = "fixture-only-secret"
    source_meta = []
    for device, expected in EXPECTED["devices"].items():
        folder = tmp_path / expected["management"]
        folder.mkdir()
        entries = []
        for record in MANIFEST["observations"]:
            if record["device"] != device:
                continue
            entries.append(
                {
                    "id": record["capability"],
                    "command": record["command"],
                    "status": record["transport_status"],
                    "finished_at": record["observed_at"],
                }
            )
            for descriptor in record["files"].values():
                (folder / Path(descriptor["path"]).name).write_bytes(
                    (ROOT / descriptor["path"]).read_bytes()
                )
        source_meta.append({"host": expected["management"], "commands": entries})
    (tmp_path / "meta.json").write_text(json.dumps(source_meta), encoding="utf-8")
    assert len(prepare(tmp_path, [secret])) == 57
    (tmp_path / "10.10.1.1/version.raw.txt").write_bytes(b"\x00" + secret.encode())
    with pytest.raises(ValueError) as error:
        prepare(tmp_path, [secret])
    assert secret not in str(error.value) and secret not in capsys.readouterr().out

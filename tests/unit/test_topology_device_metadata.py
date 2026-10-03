"""Transport provenance survives typed Observation facts and gates native exports."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.apps.topology.backend.collectors.identify import Identity
from src.apps.topology.backend.collectors.parse import ParseResult, parse
from src.apps.topology.backend.model.device_ip import enrich_device_ip
from src.apps.topology.backend.model.device_state import project_device_state
from src.apps.topology.backend.service import topology_observation

CAPTURES = Path(__file__).resolve().parents[2] / "tests/fixtures/topology/campus-wan"
IDENTITY = Identity("Cisco", "ios", "cisco-ios", "show", "high", "identified")
FILES = {
    "interfaces": "interfaces_description.txt",
    "ip": "ip_interface.txt",
    "arp": "arp.txt",
    "vrrp": "vrrp.txt",
}
OWNED = {"ip": "address", "arp": "neighbor", "vrrp": "ietf-vrrp:vrrp"}


def observed(parsed):
    return {
        "id": "current-capture",
        "data": topology_observation(
            SimpleNamespace(
                parsed=parsed,
                identity=IDENTITY,
                profile_id=IDENTITY.profile_id,
                mode="topology",
                driver="paramiko",
            )
        ),
    }


def campus(device, *, incomplete=None, replace=None):
    parsed = {}
    for cap, filename in FILES.items():
        path = CAPTURES / device / filename
        if not path.exists():
            continue
        raw = path.read_text("utf-8")
        if replace and cap == replace[0]:
            assert replace[1] in raw
            raw = raw.replace(replace[1], replace[2], 1)
        parsed[cap] = parse(IDENTITY, cap, raw, complete=cap != incomplete)
    return observed(parsed), parsed


def export(observation):
    return enrich_device_ip(project_device_state(observation), observation)


def native_fields(projected, field):
    return [
        value
        for interface in projected["tree"]["ietf-interfaces:interfaces"]["interface"]
        for af in ("ietf-ip:ipv4", "ietf-ip:ipv6")
        for value in interface.get(af, {}).get(field, [])
    ]


def has_vrrp(projected):
    return any(
        "ietf-vrrp:vrrp" in interface.get(af, {})
        for interface in projected["tree"]["ietf-interfaces:interfaces"]["interface"]
        for af in ("ietf-ip:ipv4", "ietf-ip:ipv6")
    )


@pytest.mark.parametrize(
    "capability,device", [("ip", "core1"), ("arp", "core1"), ("vrrp", "dist1")]
)
def test_real_public_parse_transport_incomplete_remains_a_native_export_veto(capability, device):
    obs, parsed = campus(device, incomplete=capability)
    baseline, _ = campus(device)
    assert parsed[capability].status == "partial"
    assert "collection_incomplete" in parsed[capability].warnings
    # The original typed records stay accessible as evidence.  Their separate
    # transport provenance must survive the real service adapter as well.
    assert isinstance(obs["data"]["facts"][capability], list)
    assert obs["data"]["facts"][capability] == parsed[capability].records
    assert obs["data"]["facts"][capability]
    metadata = obs["data"]["fact_metadata"][capability]
    assert "collection_incomplete" in metadata["warnings"]
    assert "parse_metadata" not in metadata
    before = deepcopy(obs)
    projected = export(obs)
    healthy = export(baseline)
    assert obs == before
    assert {
        "code": "device_ip_collection_unusable",
        "capability": capability,
    } in projected["gaps"]
    if capability == "vrrp":
        assert has_vrrp(healthy) and not has_vrrp(projected)
    else:
        assert native_fields(healthy, OWNED[capability])
        assert not native_fields(projected, OWNED[capability])
    if capability == "ip":
        assert len(native_fields(healthy, "address")) == 7
        # Incomplete IP facts cannot prove the interface scope for ARP either.
        assert not native_fields(projected, "neighbor")
        assert projected["metadata"]["interface_scopes"] == {}
    else:
        assert native_fields(projected, "address") == native_fields(healthy, "address")


@pytest.mark.parametrize(
    "capability,device,old,new",
    [
        ("ip", "core1", "10.10.1.13/24", "10.10.1.13/99"),
        ("arp", "core1", "0cb1.0000.0002", "not-a-mac"),
        ("vrrp", "dist1", "172.16.10.1", "invalid-address"),
    ],
)
def test_local_bad_row_partial_preserves_remaining_public_parse_facts(capability, device, old, new):
    obs, parsed = campus(device, replace=(capability, old, new))
    assert parsed[capability].status == "partial"
    assert parsed[capability].rejected_count > 0
    assert "collection_incomplete" not in obs["data"]["fact_metadata"][capability]["warnings"]
    projected = export(obs)
    assert not any(
        gap.get("code") == "device_ip_collection_unusable" and gap.get("capability") == capability
        for gap in projected["gaps"]
    )
    if capability == "vrrp":
        assert has_vrrp(projected)
    else:
        assert native_fields(projected, OWNED[capability])


@pytest.mark.parametrize("status", ["failed", "unsupported"])
def test_unusable_capability_still_has_parse_provenance_but_no_typed_facts(status):
    parsed = ParseResult(status=status, warnings=["collection_incomplete"])
    obs = observed({"ip": parsed})
    assert "ip" not in obs["data"]["facts"]
    assert obs["data"]["capabilities"]["ip"] == status
    assert obs["data"]["fact_metadata"]["ip"]["warnings"] == ["collection_incomplete"]
    assert not native_fields(export(obs), "address")


def test_metadata_warning_vetoes_transport_loss_even_with_complete_capability_status():
    obs, _ = campus("core1")
    obs["data"]["fact_metadata"]["ip"]["warnings"] = ["collection_incomplete"]
    assert obs["data"]["capabilities"]["ip"] == "complete"
    assert not native_fields(export(obs), "address")

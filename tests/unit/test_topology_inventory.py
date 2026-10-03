"""IVY network elements from real version output (campus-wan lab captures)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_topology_campus_wan import EXPECTED, IDENTITY, parsed

from src.apps.topology.backend.model.inventory import project_inventory
from src.apps.topology.backend.service import topology_observation

YANG = Path(__file__).resolve().parents[2] / "src/apps/topology/resources/yang"
NE = "ietf-network-inventory:network-inventory"


def observation(device):
    identity = IDENTITY[EXPECTED["devices"][device]["platform"]]
    return {
        "data": topology_observation(
            SimpleNamespace(
                parsed=parsed(device),
                identity=identity,
                profile_id=identity.profile_id,
                mode="topology",
                driver="paramiko",
            )
        )
    }


def element(result):
    return result["tree"][NE]["network-elements"]["network-element"][0]


def codes(result):
    return {gap["code"] for gap in result["gaps"]}


def test_vsr_is_a_virtual_element_with_its_running_images():
    result = project_inventory(observation("br1"), "urn:test:device:br1")
    assert element(result) == {
        "ne-id": "urn:test:device:br1",
        "ne-type": "ietf-network-inventory-sw-ext:ne-virtual",
        "name": "BR1",
        "mfg-name": "H3C",
        "product-name": "VSR1000",
        "software-rev": [
            {
                "name": "VSR1000-CMW710-BOOT-R1362P12-X64.bin",
                "revision": "7.1.064, Release 1362P12",
            },
            {
                "name": "VSR1000-CMW710-SYSTEM-R1362P12-X64.bin",
                "revision": "7.1.064, Release 1362P12",
            },
        ],
    }
    assert result["metadata"]["ne_type_evidence"] == ["virtual_cpus", "virtio_nic"]
    # LLDP reports a serial, but its chassis component has no observable id.
    assert "chassis_component_id_not_observed" in codes(result)
    assert "serial" not in json.dumps(result["tree"])


@pytest.mark.parametrize("device", ["core1", "dist1", "acc1"])
def test_iol_is_a_software_element_running_on_the_host_os(device):
    result = project_inventory(observation(device), f"urn:test:device:{device}")
    ne = element(result)
    assert ne["ne-type"] == "ietf-network-inventory-sw-ext:ne-software"
    assert ne["name"] == device.upper() and ne["mfg-name"] == "Cisco"
    assert ne["software-rev"][0]["revision"] == "17.15.1"
    # IOL prints a "Processor board ID", which proves no physical chassis.
    assert "product-name" not in ne and "serial" not in json.dumps(ne)


def test_an_unproven_element_kind_is_not_defaulted_to_physical():
    obs = observation("dist1")
    version = obs["data"]["facts"]["version"]
    version.pop("unix_processor")
    result = project_inventory(obs, "urn:test:device:dist1")
    assert result["tree"] == {}
    assert "ne_type_not_proven" in codes(result)


def test_missing_version_or_identity_projects_nothing():
    obs = observation("br1")
    assert "stable_ne_identity_not_observed" in codes(project_inventory(obs, None))
    obs["data"]["facts"].pop("version")
    assert project_inventory(obs, "urn:test:device:br1")["tree"] == {}


def test_two_elements_with_the_same_hostname_keep_their_own_identity():
    first = project_inventory(observation("dist1"), "urn:test:device:a")
    second = project_inventory(observation("dist1"), "urn:test:device:b")
    assert element(first)["name"] == element(second)["name"] == "DIST1"
    assert element(first)["ne-id"] != element(second)["ne-id"]


@pytest.mark.parametrize("device", ["br1", "dist1"])
def test_elements_validate_against_the_frozen_ivy_modules(device):
    yangson = pytest.importorskip("yangson", reason="Install tests/topology-requirements.txt")
    content = pytest.importorskip("yangson.enumerations").ContentType.all
    model = yangson.DataModel((YANG / "inventory-library.json").read_text(), [str(YANG)])
    tree = project_inventory(observation(device), f"urn:test:device:{device}")["tree"]
    model.from_raw(tree).validate(ctype=content)

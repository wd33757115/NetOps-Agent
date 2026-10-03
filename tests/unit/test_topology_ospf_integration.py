"""Real campus observations through public collection facts and native topology validation."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_topology_campus_wan import BASE, FIXTURES, IDENTITY, devices  # noqa: F401

from src.apps.topology.backend.collectors.parse import parse
from src.apps.topology.backend.model.builder import L3T, NT, ST, build_topology_details
from src.apps.topology.backend.service import topology_observation


@pytest.fixture
def ospf_devices(request):
    result = deepcopy(request.getfixturevalue("devices"))
    for address, facts in result.items():
        device = ("br1", "br2", "core1", "core2", "dist1", "dist2", "acc1", "acc2")
        name = device[int(address.rsplit(".", 1)[1]) - 11]
        caps = {
            cap: parse(
                IDENTITY[facts["platform"]],
                cap,
                (FIXTURES / "ospf" / name / f"{cap}.txt").read_text("utf-8"),
            )
            for cap in ("ospf_interfaces", "ospf_process")
        }
        assert all(p.status == "complete" for p in caps.values())
        observation = topology_observation(
            SimpleNamespace(
                parsed=caps,
                identity=IDENTITY[facts["platform"]],
                profile_id=IDENTITY[facts["platform"]].profile_id,
                mode="topology",
                driver="paramiko",
            )
        )
        # OSPF does not make a missing LLDP result complete, but its independent
        # facts survive in the same observation for later explicit scope binding.
        assert observation["complete"] is False
        facts.update(observation["facts"])
    return result


def test_real_campus_ospf_ids_costs_and_topology_are_native(ospf_devices):
    from yangson import DataModel
    from yangson.enumerations import ContentType

    tree, _, protocols = build_topology_details(ospf_devices, BASE)
    root = Path(__file__).resolve().parents[2] / "src/apps/topology/resources/yang"
    DataModel((root / "library.json").read_text("utf-8"), [str(root)]).from_raw(tree).validate(
        ctype=ContentType.all,
    )
    networks = tree["ietf-network:networks"]["network"]
    default = next(n for n in networks if n["network-id"] == BASE + "l3")
    ids = [
        rid
        for n in default["node"]
        for rid in n.get(L3T + "l3-node-attributes", {}).get("router-id", [])
    ]
    assert sorted(ids) == [f"10.255.0.{i}" for i in range(1, 7)]
    mgmt_nodes = [n for n in default["node"] if n.get(ST + "name") == "MGMT"]
    assert len(mgmt_nodes) == 6
    assert all("router-id" not in n.get(L3T + "l3-node-attributes", {}) for n in mgmt_nodes)
    for n in default["node"]:
        for tp in n.get(NT + "termination-point", []):
            attrs = tp.get(L3T + "l3-termination-point-attributes", {})
            assert "ip-address" in attrs and "interface-name" not in attrs
    metrics = protocols["links"]
    asymmetric = {
        key: value
        for key, value in metrics.items()
        if any(g["code"] == "asymmetric_ospf_cost" for g in value["gaps"])
    }
    assert asymmetric
    for network in networks:
        for link in network.get(NT + "link", []):
            attrs = link.get(L3T + "l3-link-attributes", {})
            assert "metric2" not in attrs
            if link["link-id"] in asymmetric:
                assert "metric1" not in attrs
    assert any(value.get("metric1") == "10" for value in metrics.values())


def test_corrupted_current_ospf_never_leaks_a_router_id_or_cost(ospf_devices):
    ospf_devices["10.10.1.11"]["ospf_process"]["invalid_structure"] = True
    tree, _, protocols = build_topology_details(ospf_devices, BASE)
    assert protocols["devices"]["10.10.1.11"]["router_ids"] == {}
    assert not protocols["devices"]["10.10.1.11"]["interfaces"]
    for network in tree["ietf-network:networks"]["network"]:
        for node in network["node"]:
            assert "10.255.0.1" not in node.get(L3T + "l3-node-attributes", {}).get("router-id", [])


def test_truncated_transport_is_preserved_through_service_and_blocks_projection(ospf_devices):
    identity = IDENTITY["ios"]
    parsed = {
        cap: parse(
            identity,
            cap,
            (FIXTURES / "ospf/core1" / f"{cap}.txt").read_text("utf-8"),
            complete=cap != "ospf_interfaces",
        )
        for cap in ("ospf_interfaces", "ospf_process")
    }
    assert parsed["ospf_interfaces"].status == "partial"
    outcome = topology_observation(
        SimpleNamespace(
            parsed=parsed,
            identity=identity,
            profile_id=identity.profile_id,
            mode="topology",
            driver="paramiko",
        )
    )
    ospf_devices["10.10.1.13"].update(outcome["facts"])
    _, _, protocols = build_topology_details(ospf_devices, BASE)
    projected = protocols["devices"]["10.10.1.13"]
    assert projected["router_ids"] == {} and projected["interfaces"] == []
    assert projected["observations"]  # Original captured facts remain evidence.


def test_truncated_ip_scope_cannot_bind_ospf_router_id(ospf_devices):
    from src.apps.topology.backend.service import _facts

    identity = IDENTITY["ios"]
    ip = parse(
        identity,
        "ip",
        (FIXTURES / "core1/ip_interface.txt").read_text("utf-8"),
        complete=False,
    )
    assert ip.status == "partial" and ip.records
    data = topology_observation(
        SimpleNamespace(
            parsed={"ip": ip},
            identity=identity,
            profile_id=identity.profile_id,
            mode="topology",
            driver="paramiko",
        )
    )
    data["facts"] = {**ospf_devices["10.10.1.13"], **data["facts"]}
    assert "collection_incomplete" in data["fact_metadata"]["ip"]["warnings"]
    assert data["facts"]["ip"]  # Preserve the original partial evidence.
    ospf_devices["10.10.1.13"] = _facts(data)
    assert "ip" not in ospf_devices["10.10.1.13"]
    _, _, protocols = build_topology_details(ospf_devices, BASE)
    assert protocols["devices"]["10.10.1.13"]["router_ids"] == {}

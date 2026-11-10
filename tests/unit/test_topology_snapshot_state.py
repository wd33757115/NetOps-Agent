"""Current device state and immutable observed-history association, without IO."""

from copy import deepcopy
from pathlib import Path

import pytest

from src.apps.topology.backend.model.builder import L2T, NT, ST
from src.apps.topology.backend.model.snapshot_state import enrich_snapshot_state

BASE = "urn:test:snapshot-state:"
YANG = Path(__file__).resolve().parents[2] / "src/apps/topology/resources/yang"
ADDRESSES = {"A": "192.0.2.1", "B": "192.0.2.2", "C": "192.0.2.3"}
PORT = "Ethernet0/1"


def tree(edges=(), base=BASE):
    return {
        "ietf-network:networks": {
            "network": [
                {
                    "network-id": base + "l2",
                    "network-types": {L2T + "l2-topology": {}},
                    ST + "network-category": ST + "network-category-live",
                    "node": [
                        {
                            "node-id": base + name,
                            NT + "termination-point": [
                                {
                                    "tp-id": base + PORT,
                                    L2T + "l2-termination-point-attributes": {
                                        "interface-name": PORT
                                    },
                                }
                            ],
                        }
                        for name in "ABC"
                    ],
                    NT + "link": [
                        {
                            "link-id": base + a + b,
                            ST + "link-type": ST + "link-type-p2p",
                            ST + "link-direction": ST + "link-direction-bi",
                            ST + "tp": [
                                {
                                    "network-ref": base + "l2",
                                    "node-ref": base + name,
                                    "tp-ref": base + PORT,
                                    "tp-direction": ST + "tp-direction-symmetric",
                                }
                                for name in (a, b)
                            ],
                        }
                        for a, b in edges
                    ],
                }
            ]
        }
    }


def observations(oper="up", complete=True, interface_cap="complete", lldp_cap="complete"):
    return [
        {
            "id": "current-" + name,
            "address": address,
            "observed_at": "2026-10-03T03:00:00Z",
            "data": {
                "complete": complete,
                "capabilities": {"interfaces": interface_cap, "lldp": lldp_cap},
                "facts": {
                    "interfaces": [{"ifname": PORT, "type": "ethernet", "oper_status": oper}],
                    "lldp": [{"local_if": PORT}],
                },
            },
        }
        for name, address in ADDRESSES.items()
    ]


def collected():
    return {
        BASE + name: {
            "address": address,
            "evidence_id": "current-" + name,
            "observed_at": "2026-10-03T03:00:00Z",
        }
        for name, address in ADDRESSES.items()
    }


def test_truncated_interfaces_cannot_prove_up_or_admin_down():
    current = observations(interface_cap="partial")
    for observation in current:
        observation["data"]["fact_metadata"] = {
            "interfaces": {"warnings": ["collection_incomplete"]}
        }
        observation["data"]["facts"]["interfaces"][0]["admin_status"] = "down"
    _, state = enrich_snapshot_state(tree([("A", "B")]), collected(), current, [])
    link = state["links"][BASE + "AB"]
    assert link["available"] is None and link["state"] == "unknown"
    assert all(source["oper_status"] == "unknown" for source in link["state_sources"])
    assert all("admin_status" not in source for source in link["state_sources"])


def prior(model=None, state=None, snapshot_id="prior-1", created_at="2026-10-03T02:00:00Z"):
    graph = {
        "simap": model if model is not None else tree([("A", "B")]),
        "nodes": [
            {"id": BASE + name, "address": address, "identity_status": "collected"}
            for name, address in ADDRESSES.items()
        ],
        "evidence": {BASE + "AB": ["prior-lldp-evidence"]},
    }
    if state is not None:
        graph["state"] = deepcopy(state)
    return {"snapshot_id": snapshot_id, "created_at": created_at, "graph": graph}


def links(model):
    return model["ietf-network:networks"]["network"][0][NT + "link"]


def device(state, name):
    return next(d for d in state["devices"] if d["address"] == ADDRESSES[name])


def interface(state, name):
    return device(state, name)["tree"]["ietf-interfaces:interfaces"]["interface"]


def test_current_interface_instances_and_metadata_stay_outside_standard_topology():
    model, known, current, baseline = tree([("A", "B")]), collected(), observations(), prior()
    inputs = deepcopy((model, known, current, baseline))
    result, state = enrich_snapshot_state(model, known, current, [], baseline)
    assert (model, known, current, baseline) == inputs and result == model
    assert state["links"][BASE + "AB"]["state"] == "up"
    assert state["links"][BASE + "AB"]["available"] is True
    assert state["links"][BASE + "AB"]["last_seen_source"] == "lldp_observation"
    assert interface(state, "A") == [
        {"name": PORT, "type": "iana-if-type:ethernetCsmacd", "oper-status": "up"}
    ]
    assert device(state, "A")["metadata"]["completeness"] == "partial"
    assert any(
        gap["code"] == "mandatory_statistics_not_observed" for gap in device(state, "A")["gaps"]
    )


@pytest.mark.parametrize(
    "oper,expected,available",
    [
        ("up", "up", True),
        ("down", "down", False),
        ("unknown", "unknown", None),
        ("testing", "unavailable", False),
        ("dormant", "unavailable", False),
        ("not-present", "unavailable", False),
        ("lower-layer-down", "unavailable", False),
    ],
)
def test_cached_current_lldp_relation_uses_current_native_interface_state(
    oper, expected, available
):
    result, state = enrich_snapshot_state(tree([("A", "B")]), collected(), observations(oper), [])
    link = state["links"][BASE + "AB"]
    assert len(links(result)) == 1 and link["state"] == expected
    assert link["available"] is available
    assert {source["oper_status"] for source in link["state_sources"]} == {oper}


def test_lldp_failure_does_not_hide_current_successful_interface_capture_or_restore_old_up():
    _, state = enrich_snapshot_state(
        tree(), {}, observations("down", complete=False, lldp_cap="failed"), [], prior()
    )
    assert interface(state, "A")[0]["oper-status"] == "down"
    assert device(state, "A")["collection_status"] == "failed"
    link = state["links"][BASE + "AB"]
    assert link["historical"] is True and link["available"] is False
    assert link["state"] == "down" and link["prior_snapshot_id"] == "prior-1"
    assert all(source["evidence_id"].startswith("current-") for source in link["state_sources"])


@pytest.mark.parametrize("cap", ["failed", "unsupported", "not_collected"])
def test_failed_interface_capability_never_promotes_carried_up_records(cap):
    _, state = enrich_snapshot_state(
        tree(), {}, observations(interface_cap=cap, complete=False), [], prior()
    )
    assert interface(state, "A") == []
    link = state["links"][BASE + "AB"]
    assert link["state"] == "unknown" and link["available"] is None
    assert all(source["oper_status"] == "unknown" for source in link["state_sources"])


def test_target_only_failure_exports_empty_current_device_instance_and_unknown_link():
    result, state = enrich_snapshot_state(
        tree(),
        {},
        [],
        [
            {"address": address, "status": "failed", "reason": "ssh_failed"}
            for address in ADDRESSES.values()
        ],
        prior(),
    )
    assert len(links(result)) == 1 and interface(state, "A") == []
    assert device(state, "A")["target_reason"] == "ssh_failed"
    assert state["links"][BASE + "AB"]["available"] is None


def test_interfaces_without_stable_identity_export_independent_tree_without_guessing_node():
    _, state = enrich_snapshot_state(tree(), {}, observations(complete=False), [], None)
    assert interface(state, "A")[0]["oper-status"] == "up"
    assert device(state, "A")["node_id"] is None and state["links"] == {}
    assert any(
        g["code"] == "stable_topology_identity_not_observed" for g in device(state, "A")["gaps"]
    )


def test_new_identity_at_same_address_cannot_borrow_old_identity_or_relationship():
    known = collected()
    known[BASE + "new-A"] = known.pop(BASE + "A")
    result, state = enrich_snapshot_state(tree(), known, observations("down"), [], prior())
    assert device(state, "A")["node_id"] == BASE + "new-A"
    assert links(result) == [] and state["links"] == {}


def test_current_identity_at_new_address_is_not_assigned_to_failed_old_address():
    known = {BASE + "A": {"address": "192.0.2.99"}}
    result, state = enrich_snapshot_state(
        tree(), known, [], [{"address": ADDRESSES["A"], "status": "failed"}], prior()
    )
    assert device(state, "A")["node_id"] is None
    assert len(links(result)) == 1  # The stable identity itself still exists at its new address.
    source = next(
        s for s in state["links"][BASE + "AB"]["state_sources"] if s["node_id"] == BASE + "A"
    )
    assert source["address"] == "192.0.2.99"


def test_prior_other_network_is_not_an_identity_source_or_structural_baseline():
    result, state = enrich_snapshot_state(
        tree(), {}, observations(complete=False), [], prior(tree([("A", "B")], "urn:other:"))
    )
    assert links(result) == [] and all(d["node_id"] is None for d in state["devices"])


def test_historical_link_with_two_current_up_ports_has_unknown_availability_without_lldp():
    result, state = enrich_snapshot_state(
        tree(), {}, observations("up", complete=False, lldp_cap="failed"), [], prior()
    )
    assert len(links(result)) == 1
    record = state["links"][BASE + "AB"]
    assert {source["oper_status"] for source in record["state_sources"]} == {"up"}
    assert record["historical"] is True and record["available"] is None
    assert record["state"] == "unknown" and record["current_lldp_proven"] is False


def test_recursive_history_keeps_original_provenance_and_never_refreshes_last_seen():
    first_tree, first_state = enrich_snapshot_state(
        tree(), {}, observations(complete=False), [], prior()
    )
    saved = deepcopy(first_state)
    later = prior(first_tree, first_state, snapshot_id="prior-2", created_at="2026-10-03T09:00:00Z")
    result, state = enrich_snapshot_state(tree(), {}, [], [], later)
    record = state["links"][BASE + "AB"]
    assert len(links(result)) == 1 and first_state == saved
    assert record["prior_snapshot_id"] == "prior-1"
    assert record["last_seen"] == "2026-10-03T02:00:00Z"
    assert record["last_seen_source"] == "snapshot_creation"
    assert record["prior_evidence"] == ["prior-lldp-evidence"]
    assert all(source["oper_status"] == "unknown" for source in record["state_sources"])


def test_missing_original_time_remains_missing_across_later_snapshot_creation():
    baseline = prior(created_at=None)
    first_tree, first_state = enrich_snapshot_state(tree(), {}, [], [], baseline)
    assert "last_seen" not in first_state["links"][BASE + "AB"]
    later = prior(first_tree, first_state, snapshot_id="later", created_at="2026-10-03T09:00:00Z")
    _, state = enrich_snapshot_state(tree(), {}, [], [], later)
    assert "last_seen" not in state["links"][BASE + "AB"]


def test_original_lldp_observation_time_beats_snapshot_creation_time():
    current_tree, current_state = enrich_snapshot_state(
        tree([("A", "B")]), collected(), observations(), []
    )
    baseline = prior(current_tree, current_state, created_at="2026-10-03T04:00:00Z")
    _, state = enrich_snapshot_state(tree(), {}, [], [], baseline)
    record = state["links"][BASE + "AB"]
    assert record["last_seen"] == "2026-10-03T03:00:00Z"
    assert record["last_seen_source"] == "lldp_observation"


def test_carried_historical_relation_in_current_tree_stays_historical_without_lldp_proof():
    first_tree, first_state = enrich_snapshot_state(tree(), {}, [], [], prior())
    _, state = enrich_snapshot_state(
        first_tree, {}, observations(complete=False), [], prior(first_tree, first_state)
    )
    record = state["links"][BASE + "AB"]
    assert record["historical"] is True and record["available"] is None
    assert record["prior_snapshot_id"] == "prior-1"


def test_complete_lldp_new_unmanaged_peer_replaces_prior_known_peer():
    known = collected()
    known.pop(BASE + "C")
    result, state = enrich_snapshot_state(
        tree([("A", "C")]), known, observations("down"), [], prior()
    )
    assert [link["link-id"] for link in links(result)] == [BASE + "AC"]
    assert BASE + "AB" not in state["links"]
    assert state["links"][BASE + "AC"]["historical"] is False


@pytest.mark.parametrize("legacy", [False, True])
def test_lldp_ports_require_complete_current_llpd_capability_with_explicit_legacy_compatibility(
    legacy,
):
    current = observations("down")
    for observation in current:
        if legacy:
            observation["data"].pop("capabilities")
        else:
            observation["data"]["capabilities"]["lldp"] = "partial"
    result, _ = enrich_snapshot_state(tree([("A", "C")]), collected(), current, [], prior())
    assert len(links(result)) == (1 if legacy else 2)


def test_retained_standard_tree_has_closed_references_under_frozen_full_content_validation():
    yangson = pytest.importorskip("yangson", reason="Install tests/topology-requirements.txt")
    content = pytest.importorskip("yangson.enumerations").ContentType.all
    validator = yangson.DataModel((YANG / "library.json").read_text(), [str(YANG)])
    current = tree()
    current["ietf-network:networks"]["network"][0]["node"] = []
    result, state = enrich_snapshot_state(current, {}, [], [], prior())
    assert len(links(result)) == 1 and state["links"][BASE + "AB"]["historical"] is True
    validator.from_raw(result).validate(ctype=content)


def test_a_device_read_as_standard_records_exports_their_trees():
    """NETCONF → standard records → the trees in the snapshot (BR1's replies)."""
    import yaml

    from src.apps.netconf_api.backend import standard

    root = Path(__file__).resolve().parents[2]
    seed = root / "src/apps/netconf_api/backend/mappings/h3c_comware7.yaml"
    mapping = standard.load(yaml.safe_load(seed.read_text(encoding="utf-8")))
    folder = root / "tests/fixtures/netconf/h3c-comware7"
    texts = [
        p.read_text(encoding="utf-8")
        for p in sorted((folder / "br1").glob("*.xml"))
        if p.name != "acl.xml"
    ]  # BR1 later configured with NAT, and the ACLs selecting its sources:
    texts += [
        (folder / "br1-policy" / n).read_text(encoding="utf-8")
        for n in ("nat.xml", "acl.xml", "pbr.xml")
    ]
    records, _ = standard.convert(mapping, texts)
    current = observations()
    current[0]["data"]["standard"] = {"mapping": "h3c_comware7@test", "records": records}
    current[0]["data"]["platform"] = {"identity": {"os_family": "comware"}}
    _, state = enrich_snapshot_state(tree([("A", "B")]), collected(), current, [])
    device = next(d for d in state["devices"] if d["address"] == ADDRESSES["A"])
    names = {row["name"] for row in device["tree"]["ietf-interfaces:interfaces"]["interface"]}
    assert "GigabitEthernet3/0" in names and PORT not in names  # from NETCONF, not facts
    control = device["control"]
    assert control["source"] == {"netconf_mapping": "h3c_comware7@test"}
    (routing,) = control["routing"]
    ribs = routing["tree"]["ietf-routing:routing"]["ribs"]["rib"]
    assert {rib["name"] for rib in ribs} == {"ipv4", "ipv6"}
    assert control["lldp"]["tree"]["ieee802-dot1ab-lldp:lldp"]["port"]
    assert control["nat"]["tree"]["ietf-nat:nat"]["instances"]["instance"][0]["policy"]
    # ACL (RFC 8519/9899) and PBR (OpenConfig) from the same records.
    acls = control["acl"]["tree"]["ietf-access-control-list:acls"]
    assert {acl["name"] for acl in acls["acl"]} >= {"3990", "T-NAMED"}
    points = acls["attachment-points"]["interface"]
    assert [p["interface-id"] for p in points] == ["GigabitEthernet5/0"]
    pbr = control["pbr"]["tree"]["openconfig-network-instance:network-instances"]
    (instance,) = pbr["network-instance"]
    assert instance["policy-forwarding"]["policies"]["policy"][0]["policy-id"] == "TEST-PBR"
    # The other devices keep their CLI projections, now with PBR.
    other = next(d for d in state["devices"] if d["address"] == ADDRESSES["B"])
    assert "pbr" in other["control"] and "source" not in other["control"]

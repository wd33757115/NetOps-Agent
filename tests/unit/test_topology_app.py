from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from src.apps.topology.backend import service
from src.apps.topology.backend.collectors import collect as collectors
from src.apps.topology.backend.models import Base, Network, Observation, Run, Snapshot, Target, now
from src.apps.topology.backend.normalize import build_graph, parse, rfc8345
from src.apps.topology.backend.schemas import Discovery, Import, Resolve
from src.platform.device_access.contracts import CommandResult, DeviceAccessError

# Synthetic fixtures use the field ordering documented by H3C/Cisco; no real credentials/devices.
COMWARE = """LLDP neighbor-information of port 1 [GigabitEthernet1/0/1]:
LLDP agent nearest-bridge:
 LLDP Neighbor index : 1
 Chassis type : MAC address
 Chassis ID : 0011-2233-4455
 Port ID type : Interface name
 Port ID : GigabitEthernet1/0/2
 System name : edge-b
 Management address type : IPv4
 Management address : 10.0.0.2
"""
NXOS = """Capability codes:
Chassis id: 0011.2233.4455
Port id: Ethernet1/2
Local Port id: Eth1/1
System Name: edge-b
Management Address: 10.0.0.2
Management Address IPV6: not advertised
Total entries displayed: 1
"""


@pytest.fixture
def world(monkeypatch):
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda db, _: db.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)

    @contextmanager
    def sessions():
        with Session(engine) as session, session.begin():
            yield session

    class Credentials:
        values = {}

        def put(self, owner, domain, addresses, username, password):
            ref = uuid4().hex
            self.values[ref] = (username, password)
            return ref

        def get(self, ref, *args):
            if ref not in self.values:
                raise DeviceAccessError("credential_expired", "expired")
            return self.values[ref]

        def revoke(self, ref):
            self.values.pop(ref, None)

    blobs, dispatches, executions = {}, [], []

    def put(run, target, attempt, data):
        key = f"{run}/{target}/{attempt}"
        blobs[key] = data
        return key

    class Access:
        output = COMWARE
        failure = None
        after_connect = None
        versions = {}
        outputs = {}
        sent = []

        def __init__(self, **kwargs):
            self.address = kwargs["target_resolver"](None).host

        def execute_batch(self, context, request, on_result, should_continue, command_filter=None):
            executions.append(request)
            assert should_continue()
            if self.failure:
                raise DeviceAccessError(self.failure, "failure")
            if self.after_connect:
                self.after_connect()
            assert should_continue()
            results = []
            for command in request.commands:
                if command_filter is not None and not command_filter(command, tuple(results)):
                    continue
                self.sent.append((self.address, command.text))
                output = (
                    self.outputs.get(self.address, self.output)
                    if command.command_id.endswith("neighbors")
                    else (
                        self.versions.get(
                            (self.address, command.command_id),
                            (
                                "H3C Comware Software, Version 7.1"
                                if command.command_id == "detect-display"
                                else ""
                            ),
                        )
                    )
                )
                item = CommandResult(
                    execution_id="execution",
                    command_id=command.command_id,
                    started_at=now().isoformat(),
                    finished_at=now().isoformat(),
                    execution_status="succeeded",
                    output_complete=True,
                    output=output,
                )
                on_result(item)
                results.append(item)
            return SimpleNamespace(execution_status="succeeded")

    def collect_node(
        access, context, target_ref, profile_ref, *, mode, should_continue, on_result, **_
    ):
        """Stand-in for the real collector: real identification, profiles and parsers.

        Transport is the Access double above, so failure/cancel/output knobs apply.
        """
        executions.append((target_ref, mode))
        address = access.address
        assert should_continue()
        if Access.failure:
            raise DeviceAccessError(Access.failure, "failure")
        if Access.after_connect:
            Access.after_connect(None)
        if not should_continue():
            raise DeviceAccessError("cancelled", "cancelled")

        def run(command_id, text, output):
            Access.sent.append((address, text))
            item = CommandResult(
                execution_id="execution",
                command_id=command_id,
                started_at=now().isoformat(),
                finished_at=now().isoformat(),
                execution_status="succeeded",
                output_complete=True,
                output=output,
            )
            on_result(item)
            return item

        probes, identity = [], collectors.Identity()
        for text in collectors.PROBES:
            command_id = "detect-" + text.split()[0]
            default = "H3C Comware Software, Version 7.1" if command_id == "detect-display" else ""
            item = run(command_id, text, Access.versions.get((address, command_id), default))
            probes.append(collectors.Probe(text, item.output, True))
            identity = collectors.identify(probes)
            if identity.status in {"identified", "generic"}:
                break
        profile = collectors.select_profile(identity, collectors.load_profiles())
        result = collectors.CollectionResult(identity, profile.id, mode, "paramiko")
        for cap, spec in profile.selected(mode).items():
            if cap == "version":
                continue
            output = Access.outputs.get(address, Access.output) if cap == "lldp" else ""
            result.results[cap] = run(cap, spec.command, output)
            result.parsed[cap] = collectors.parse(identity, cap, output)
        return result

    monkeypatch.setattr(service, "collect_node", collect_node)
    monkeypatch.setattr(service, "sessions", sessions)
    monkeypatch.setattr(service, "CredentialStore", Credentials)
    monkeypatch.setattr(service, "authorize_address", lambda *args: None)
    monkeypatch.setattr(service, "dispatch", dispatches.append)
    monkeypatch.setattr(service.storage, "put", put)
    monkeypatch.setattr(service.storage, "get", blobs.__getitem__)
    monkeypatch.setattr(service, "DeviceAccessService", Access)
    yield SimpleNamespace(
        sessions=sessions,
        credentials=Credentials,
        access=Access,
        blobs=blobs,
        executions=executions,
        dispatches=dispatches,
    )
    engine.dispose()


def discovery(**kwargs):
    return Discovery(
        request_id="discovery-1",
        seeds=["10.0.0.1"],
        allowed_cidrs=["10.0.0.0/24"],
        profile_id=kwargs.pop("profile_id", "auto"),
        credentials={"username": "operator", "password": "SENSITIVE"},
        **kwargs,
    )


def offline(**kwargs):
    return Import(
        request_id="offline-1",
        profile_id="comware-lldp-v1",
        captures=[
            {
                "address": "10.0.0.1",
                "observed_at": "2026-09-28T00:00:00+00:00",
                "outputs": {"neighbors": COMWARE},
                "complete": True,
            }
        ],
        **kwargs,
    )


@pytest.mark.parametrize(
    "profile,text,local",
    [("comware-lldp-v1", COMWARE, "GigabitEthernet1/0/1"), ("nxos-lldp-v1", NXOS, "Eth1/1")],
)
def test_vendor_field_order_and_management_address(profile, text, local):
    result = parse(profile, {"neighbors": text})
    assert result["neighbors"][0]["local_port"] == local
    assert result["neighbors"][0]["addresses"] == ["10.0.0.2"]


@pytest.mark.parametrize(
    "text,complete",
    [
        ("", True),
        ("% Invalid command", True),
        (COMWARE, False),
        (NXOS.replace("Total entries displayed: 1", "Total entries displayed: 2"), True),
        (NXOS.replace("Port id: Ethernet1/2\n", ""), True),
    ],
)
def test_invalid_or_partial_table_never_means_empty(text, complete):
    with pytest.raises(DeviceAccessError):
        parse("nxos-lldp-v1", {"neighbors": text}, complete)


def test_explicit_zero_is_valid():
    assert parse("nxos-lldp-v1", {"neighbors": "Total entries displayed: 0"})["neighbors"] == []


def test_address_does_not_automatically_merge_advertised_identity():
    data = parse("comware-lldp-v1", {"neighbors": COMWARE})
    observations = [
        dict(id="a", address="10.0.0.1", observed_at="2026-09-28T00:00:00Z", data=data),
        dict(
            id="b", address="10.0.0.2", observed_at="2026-09-28T00:00:00Z", data={"neighbors": []}
        ),
    ]
    graph = build_graph("network", "site-a", observations)
    assert len(graph["nodes"]) == 3
    assert len({node["id"] for node in graph["nodes"]}) == 3
    other = build_graph("network", "site-b", observations)
    assert {node["id"] for node in graph["nodes"]}.isdisjoint(
        {node["id"] for node in other["nodes"]}
    )


def test_snapshot_overlay_keeps_unobserved_and_rejects_late_regression():
    observation = dict(
        id="a",
        address="10.0.0.1",
        observed_at="2026-09-28T00:00:00Z",
        data=parse("comware-lldp-v1", {"neighbors": COMWARE}),
    )
    first = build_graph("network", "default", [observation])
    missing = build_graph("network", "default", [], first)
    assert missing["links"][0]["freshness"] == "not_observed"
    assert first["links"][0]["freshness"] == "observed"
    old = {**observation, "observed_at": "2026-09-27T00:00:00Z", "data": {"neighbors": []}}
    assert (
        build_graph("network", "default", [old], first)["links"][0]["freshness"] == "not_observed"
    )
    new = {**old, "observed_at": "2026-09-29T00:00:00Z"}
    assert (
        build_graph("network", "default", [new], first)["links"][0]["freshness"]
        == "absent_in_observation"
    )


def test_new_advertisement_updates_candidate_without_mutating_history():
    old = dict(
        id="old",
        address="10.0.0.1",
        observed_at="2026-09-28T00:00:00Z",
        data=parse("comware-lldp-v1", {"neighbors": COMWARE}),
    )
    first = build_graph("network", "default", [old])
    new = {
        **old,
        "id": "new",
        "observed_at": "2026-09-28T00:01:00Z",
        "data": parse(
            "comware-lldp-v1",
            {"neighbors": COMWARE.replace("edge-b", "renamed-b").replace("10.0.0.2", "10.0.0.22")},
        ),
    }
    latest = build_graph("network", "default", [new], first)
    candidate = next(n for n in latest["nodes"] if n["identity_status"] == "candidate")
    assert candidate["name"] == "renamed-b"
    assert candidate["addresses"] == ["10.0.0.22"]
    assert candidate["evidence_id"] == "new"
    original = next(n for n in first["nodes"] if n["identity_status"] == "candidate")
    assert original["name"] == "edge-b"
    assert original["addresses"] == ["10.0.0.2"]
    assert original["id"] == candidate["id"]
    # An older report from another source must not roll back the shared candidate.
    late = build_graph("network", "default", [{**old, "address": "10.0.0.3"}], latest)
    after_late = next(n for n in late["nodes"] if n["id"] == candidate["id"])
    assert after_late["name"] == "renamed-b"
    assert after_late["observed_at"] == new["observed_at"]
    assert after_late["evidence_id"] == "new"


def test_submit_is_idempotent_and_never_persists_password(world):
    first = service.submit("user", discovery())
    second = service.submit("user", discovery())
    assert first["id"] == second["id"]
    assert len(world.credentials.values) == 1
    with world.sessions() as session:
        rows = session.scalars(select(Run)).all()
        assert len(rows) == 1
        assert "SENSITIVE" not in repr(rows[0].settings)
    with pytest.raises(HTTPException) as exc:
        service.submit("user", discovery(max_depth=1))
    assert exc.value.status_code == 409


def test_failure_waits_and_only_explicit_resolution_retries(world):
    row = service.submit("user", discovery(max_depth=0))
    world.access.failure = "authentication_failed"
    service.execute_run(row["id"], lambda *args: None)
    failed = service.read("user", row["id"])
    assert failed["status"] == "waiting_for_input"
    assert failed["targets"][0]["reason"] == "authentication_failed"
    service.reconcile()
    service.execute_run(row["id"], lambda *args: None)
    assert len(world.executions) == 1
    target = failed["targets"][0]
    resolution = Resolve(
        request_id="fix-1",
        version=target["version"],
        action="retry",
        credentials={"username": "new-user", "password": "new-password"},
    )
    service.resolve("user", row["id"], target["id"], resolution)
    service.resolve("user", row["id"], target["id"], resolution)
    world.access.failure = None
    service.execute_run(row["id"], lambda *args: None)
    done = service.read("user", row["id"])
    assert done["status"] == "completed"
    assert len(world.executions) == 2
    assert done["snapshot_id"]
    with world.sessions() as session:
        assert len(session.scalars(select(Observation)).all()) == 2
        assert len(session.scalars(select(Snapshot)).all()) == 1
    assert service.evidence("user", done["targets"][0]["evidence_id"])["complete"]


def test_neighbor_credentials_are_not_implicitly_reused(world):
    row = service.submit("user", discovery())
    service.execute_run(row["id"], lambda *args: None)
    result = service.read("user", row["id"])
    assert result["status"] == "waiting_for_input"
    neighbor = next(t for t in result["targets"] if t["address"] == "10.0.0.2")
    assert neighbor["reason"] == "credentials_required"
    with pytest.raises(HTTPException):
        service.resolve(
            "user",
            row["id"],
            neighbor["id"],
            Resolve(request_id="no-creds", version=1, action="retry"),
        )
    resolved = service.resolve(
        "user", row["id"], neighbor["id"], Resolve(request_id="skip", version=1, action="skip")
    )
    assert resolved["status"] == "partial"


def test_shared_neighbor_credentials_still_respect_scope_depth_and_dedup(world):
    row = service.submit("user", discovery(credentials_for_neighbors=True, max_depth=1))
    service.execute_run(row["id"], lambda *args: None)
    service.execute_run(row["id"], lambda *args: None)
    result = service.read("user", row["id"])
    assert result["status"] == "completed"
    assert len(result["targets"]) == 2


@pytest.mark.parametrize("seed_profile", ["auto", "comware-lldp-v1"])
def test_mixed_platform_neighbors_are_identified_independently(world, seed_profile):
    world.access.versions[("10.0.0.2", "detect-display")] = "% Invalid command"
    world.access.versions[("10.0.0.2", "detect-show")] = (
        "Cisco Nexus Operating System (NX-OS) Software"
    )
    world.access.outputs["10.0.0.2"] = NXOS.replace("10.0.0.2", "10.0.0.1")
    row = service.submit("user", discovery(profile_id=seed_profile, credentials_for_neighbors=True))
    service.execute_run(row["id"], lambda *args: None)
    service.execute_run(row["id"], lambda *args: None)
    result = service.read("user", row["id"])
    assert result["status"] == "completed"
    # A human-chosen seed platform keeps the LLDP-only plan; automatic identification
    # uses the installed profile and collects every topology fact.
    assert {t["address"]: t["platform"]["profile_id"] for t in result["targets"]} == {
        "10.0.0.1": "h3c-comware7" if seed_profile == "auto" else "comware-lldp-v1",
        "10.0.0.2": "cisco-nxos",
    }
    neighbor_commands = [command for address, command in world.access.sent if address == "10.0.0.2"]
    nxos = collectors.load_profiles()["cisco-nxos"].selected("topology")
    assert neighbor_commands == [
        "display version",
        "show version",
        *(spec.command for cap, spec in nxos.items() if cap != "version"),
    ]
    assert len(world.executions) == 2


def test_unknown_platform_waits_and_manual_override_is_target_local(world):
    world.access.versions[("10.0.0.1", "detect-display")] = "Unsupported OS version"
    world.access.versions[("10.0.0.1", "detect-show")] = "Unsupported OS version"
    row = service.submit("user", discovery(credentials_for_neighbors=True))
    service.execute_run(row["id"], lambda *args: None)
    failed = service.read("user", row["id"])
    target = failed["targets"][0]
    assert target["reason"] == "platform_unrecognized"
    assert world.access.sent == [("10.0.0.1", "display version"), ("10.0.0.1", "show version")]
    evidence = service.evidence("user", target["evidence_id"])
    assert set(evidence["outputs"]) == {"detect-display", "detect-show"}
    service.reconcile()
    service.execute_run(row["id"], lambda *args: None)
    assert len(world.executions) == 1
    service.resolve(
        "user",
        row["id"],
        target["id"],
        Resolve(
            request_id="choose",
            version=target["version"],
            action="retry",
            profile_id="comware-lldp-v1",
        ),
    )
    service.execute_run(row["id"], lambda *args: None)
    world.access.versions[("10.0.0.2", "detect-display")] = "% Invalid command"
    world.access.versions[("10.0.0.2", "detect-show")] = (
        "Cisco Nexus Operating System (NX-OS) Software"
    )
    world.access.outputs["10.0.0.2"] = "Total entries displayed: 0"
    service.execute_run(row["id"], lambda *args: None)
    result = service.read("user", row["id"])
    assert result["status"] == "completed"
    assert (
        next(t for t in result["targets"] if t["depth"] == 1)["platform"]["profile_id"]
        == "cisco-nxos"
    )


def test_cancellation_fences_inflight_publication(world):
    row = service.submit("user", discovery())
    world.access.after_connect = lambda self: service.cancel("user", row["id"])
    service.execute_run(row["id"], lambda *args: None)
    result = service.read("user", row["id"])
    assert result["status"] == "cancelled"
    assert not result["graph"]["nodes"]
    with world.sessions() as session:
        assert not session.scalars(select(Observation)).all()


def test_lost_worker_requires_human_confirmation(world):
    row = service.submit("user", discovery())
    with world.sessions() as session:
        target = session.get(Target, row["targets"][0]["id"])
        target.status = "running"
        target.lease_until = now() - timedelta(seconds=1)
        target.lease_token = "lost-worker"
    service.reconcile()
    result = service.read("user", row["id"])
    assert result["status"] == "waiting_for_input"
    assert result["targets"][0]["reason"] == "worker_interrupted"
    assert not world.executions


def test_offline_import_projects_valid_references_and_protects_evidence(world):
    row = service.import_captures("user", offline())
    assert service.import_captures("user", offline())["id"] == row["id"]
    assert row["status"] == "completed"
    network = rfc8345(row["graph"])["ietf-network:networks"]["network"][0]
    node_ids = {node["node-id"] for node in network["node"]}
    for link in network["ietf-network-topology:link"]:
        assert link["source"]["source-node"] in node_ids
        assert link["destination"]["dest-node"] in node_ids
    assert not world.executions
    with pytest.raises(HTTPException) as exc:
        service.evidence("stranger", row["targets"][0]["evidence_id"])
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException):
        service.snapshot("stranger", row["snapshot_id"])


def test_offline_invalid_import_has_no_business_side_effect(world):
    body = offline()
    body.captures[0].complete = False
    with pytest.raises(DeviceAccessError):
        service.import_captures("user", body)
    with world.sessions() as session:
        assert session.scalars(select(Network)).all() == []


def test_auto_identification_collects_topology_facts_in_one_budget(world):
    row = service.submit("user", discovery(max_depth=0))
    service.execute_run(row["id"], lambda *args: None)
    result = service.read("user", row["id"])
    assert result["status"] == "completed"
    assert world.executions == [(result["targets"][0]["id"], "topology")]
    comware = collectors.load_profiles()["h3c-comware7"].selected("topology")
    assert [command for _, command in world.access.sent] == [
        "display version",
        *(spec.command for cap, spec in comware.items() if cap != "version"),
    ]
    with world.sessions() as session:
        data = session.scalars(select(Observation)).one().data
    assert data["platform"]["mode"] == "topology"
    assert data["capabilities"]["lldp"] == "complete"
    assert data["facts"]["lldp"][0]["remote_mgmt_addresses"] == ["10.0.0.2"]
    # Facts that did not parse are reported, never stored as empty tables.
    assert data["capabilities"]["arp"] != "complete" and "arp" not in data["facts"]
    assert service.LEASE_SECONDS > service.NODE_BUDGET_SECONDS


def test_unparsed_lldp_waits_for_a_human_instead_of_meaning_no_neighbours(world):
    world.access.output = "LLDP neighbor-information of port 1 [GigabitEthernet1/0/1]:\n garbage\n"
    row = service.submit("user", discovery(max_depth=0))
    service.execute_run(row["id"], lambda *args: None)
    target = service.read("user", row["id"])["targets"][0]
    assert target["status"] == "waiting_for_input"
    assert target["reason"] in {"parse_failed", "parse_incomplete"}

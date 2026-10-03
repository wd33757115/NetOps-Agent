from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from src.apps.topology.backend import logins, service
from src.apps.topology.backend.collectors import collect as collectors
from src.apps.topology.backend.models import Base, Network, Observation, Run, Snapshot, Target, now
from src.apps.topology.backend.normalize import parse
from src.apps.topology.backend.schemas import Credentials, Discovery, Import, Resolve
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

    class Store:
        values = {}

        def put(self, owner, domain, addresses, username, password, ttl=None):
            ref = uuid4().hex
            self.values[ref] = (username, password)
            return ref

        def get(self, ref, *args):
            if ref not in self.values:
                raise DeviceAccessError("credential_expired", "expired")
            return self.values[ref]

        def revoke(self, ref):
            self.values.pop(ref, None)

    blobs, dispatches, executions, trust_used, used_logins = {}, [], [], [], []

    class Trust:
        verified = []

        def verify(self, host, port, key, *, auto_trust=False):
            self.verified.append((host, auto_trust))
            return True

        def known_hosts(self, host, port):
            return ""

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
        captures = {}
        sent = []

        def __init__(self, **kwargs):
            self.address = kwargs["target_resolver"](None).host
            self.driver = kwargs.get("driver")
            profile = kwargs["profile_resolver"](None)
            used_logins.append((self.address, profile.username, profile.password))

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
        trust_used.append(
            (
                access.driver.host_key_verifier is not None,
                _.get("known_hosts_provider") is not None,
            )
        )
        access.driver.host_key_verifier(SimpleNamespace(host=access.address), 22, None)
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
            output = Access.captures.get(
                (address, cap), Access.outputs.get(address, Access.output) if cap == "lldp" else ""
            )
            result.results[cap] = run(cap, spec.command, output)
            result.parsed[cap] = collectors.parse(identity, cap, output)
        return result

    monkeypatch.setattr(service, "collect_node", collect_node)
    monkeypatch.setattr(service, "HostKeyStore", Trust)
    monkeypatch.setattr(service, "sessions", sessions)
    monkeypatch.setattr(logins, "sessions", sessions)
    monkeypatch.setattr(service, "CredentialStore", Store)
    monkeypatch.setattr(logins, "CredentialStore", Store)
    monkeypatch.setenv("DEVICE_ACCESS_CREDENTIAL_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(service, "dispatch", dispatches.append)
    monkeypatch.setattr(service.storage, "put", put)
    monkeypatch.setattr(service.storage, "get", blobs.__getitem__)
    monkeypatch.setattr(service, "DeviceAccessService", Access)
    yield SimpleNamespace(
        sessions=sessions,
        credentials=Store,
        access=Access,
        blobs=blobs,
        executions=executions,
        dispatches=dispatches,
        trust=Trust,
        trust_used=trust_used,
        logins=used_logins,
    )
    engine.dispose()


def discovery(**kwargs):
    return Discovery(
        request_id="discovery-1",
        seeds=["10.0.0.1"],
        allowed_cidrs=["10.0.0.0/24"],
        profile_id=kwargs.pop("profile_id", "auto"),
        credentials=kwargs.pop("credentials", {"username": "operator", "password": "SENSITIVE"}),
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


def test_new_neighbours_use_the_runs_login(world):
    row = service.submit("user", discovery())
    service.execute_run(row["id"], lambda *args: None)
    service.execute_run(row["id"], lambda *args: None)
    result = service.read("user", row["id"])
    assert result["status"] == "completed"
    assert {t["address"] for t in result["targets"]} == {"10.0.0.1", "10.0.0.2"}
    assert world.logins == [
        ("10.0.0.1", "operator", "SENSITIVE"),
        ("10.0.0.2", "operator", "SENSITIVE"),
    ]
    # The temporary login is not saved: it is revoked when the run ends.
    assert world.credentials.values == {}
    assert logins.read_default("user")["configured"] is False


def test_neighbours_still_respect_scope_depth_and_dedup(world):
    row = service.submit("user", discovery(max_depth=1))
    service.execute_run(row["id"], lambda *args: None)
    service.execute_run(row["id"], lambda *args: None)
    result = service.read("user", row["id"])
    assert result["status"] == "completed"
    assert len(result["targets"]) == 2


def test_the_default_login_is_used_without_a_temporary_one(world):
    with pytest.raises(HTTPException) as missing:
        service.submit("user", discovery(credentials=None, max_depth=0))
    assert missing.value.status_code == 422
    logins.save_default("user", Credentials(username="admin", password="DEFAULT"))
    assert logins.read_default("user") == {"username": "admin", "configured": True}
    row = service.submit("user", discovery(credentials=None, max_depth=0))
    service.execute_run(row["id"], lambda *args: None)
    assert service.read("user", row["id"])["status"] == "completed"
    # A temporary login wins over the default for its run.
    other = discovery(max_depth=0)
    other.request_id = "discovery-2"
    service.submit("user", other)
    service.execute_run(service.networks("user")[0]["active_run"], lambda *args: None)
    assert [login[1:] for login in world.logins] == [
        ("admin", "DEFAULT"),
        ("operator", "SENSITIVE"),
    ]
    assert logins.resolve("user", "10.0.0.1") == ("admin", "DEFAULT")


def test_a_login_typed_for_one_node_applies_to_that_node_only(world):
    row = service.submit("user", discovery())
    world.access.failure = "authentication_failed"
    service.execute_run(row["id"], lambda *args: None)
    target = service.read("user", row["id"])["targets"][0]
    assert target["reason"] == "authentication_failed"
    world.access.failure = None
    service.resolve(
        "user",
        row["id"],
        target["id"],
        Resolve(
            request_id="typed",
            version=target["version"],
            action="retry",
            credentials={"username": "typed-user", "password": "typed"},
        ),
    )
    service.execute_run(row["id"], lambda *args: None)
    service.execute_run(row["id"], lambda *args: None)
    assert service.read("user", row["id"])["status"] == "completed"
    assert world.logins == [
        ("10.0.0.1", "operator", "SENSITIVE"),
        ("10.0.0.1", "typed-user", "typed"),
        ("10.0.0.2", "operator", "SENSITIVE"),
    ]


def test_connection_failure_can_be_retried_or_skipped(world):
    row = service.submit("user", discovery(max_depth=0))
    world.access.failure = "connection_failed"
    service.execute_run(row["id"], lambda *args: None)
    target = service.read("user", row["id"])["targets"][0]
    assert target["reason"] == "connection_failed"
    retried = service.resolve(
        "user",
        row["id"],
        target["id"],
        Resolve(request_id="again", version=target["version"], action="retry"),
    )
    service.execute_run(row["id"], lambda *args: None)
    target = service.read("user", row["id"])["targets"][0]
    assert retried["targets"][0]["status"] == "queued" and target["reason"] == "connection_failed"
    skipped = service.resolve(
        "user",
        row["id"],
        target["id"],
        Resolve(request_id="skip", version=target["version"], action="skip"),
    )
    assert skipped["status"] == "partial"


@pytest.mark.parametrize("seed_profile", ["auto", "comware-lldp-v1"])
def test_mixed_platform_neighbors_are_identified_independently(world, seed_profile):
    world.access.versions[("10.0.0.2", "detect-display")] = "% Invalid command"
    world.access.versions[("10.0.0.2", "detect-show")] = (
        "Cisco Nexus Operating System (NX-OS) Software"
    )
    world.access.outputs["10.0.0.2"] = NXOS.replace("10.0.0.2", "10.0.0.1")
    row = service.submit("user", discovery(profile_id=seed_profile))
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
    row = service.submit("user", discovery())
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
    exported = service.export("user", row["snapshot_id"])
    for network in exported["ietf-network:networks"]["network"]:
        tps = {
            (node["node-id"], tp["tp-id"])
            for node in network["node"]
            for tp in node["ietf-network-topology:termination-point"]
        }
        for link in network["ietf-network-topology:link"]:
            ends = link["ietf-simap-topology:tp"]
            assert link["ietf-simap-topology:link-direction"].endswith("link-direction-bi")
            assert all((end["node-ref"], end["tp-ref"]) in tps for end in ends)
    # An LLDP-only import still yields the seed and its advertised neighbour, once each.
    assert len(row["graph"]["nodes"]) == 2 and len(row["graph"]["links"]) == 1
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


LAB = Path(__file__).resolve().parents[1] / "fixtures/topology/h3c/vsr1000-7.1.064"
LAB_FILES = {
    "lldp_local": "lldp_local",
    "lldp": "lldp_verbose",
    "interfaces": "interfaces",
    "ip": "ip_interfaces",
    "arp": "arp",
    "mac": "mac",
    "routes": "routes",
}


def test_discovery_of_the_lab_publishes_one_simap_topology(world):
    for address, device in (("10.10.1.1", "vsr-1"), ("10.10.1.2", "vsr-2")):
        for cap, name in LAB_FILES.items():
            world.access.captures[(address, cap)] = (LAB / device / f"{name}.txt").read_text(
                encoding="utf-8"
            )
    body = discovery(max_depth=1)
    body.seeds, body.allowed_cidrs = ["10.10.1.1"], ["10.10.1.0/24"]
    row = service.submit("user", body)
    service.execute_run(row["id"], lambda *args: None)
    service.execute_run(row["id"], lambda *args: None)
    result = service.read("user", row["id"])
    assert result["status"] == "completed"
    assert {t["address"] for t in result["targets"]} == {"10.10.1.1", "10.10.1.2"}
    graph = result["graph"]
    kinds = sorted(n["identity_status"] for n in graph["nodes"])
    assert kinds == ["collected", "collected", "endpoint", "endpoint", "endpoint", "segment"]
    segment = next(n for n in graph["nodes"] if n["identity_status"] == "segment")
    assert segment["members"] == 3 and segment["subnets"] == ["10.10.1.0/24"]
    assert sorted(link["link_type"] for link in graph["links"]) == ["mp2mp"] * 3 + ["p2p"] * 3
    # The device-to-device link is one bidirectional SIMAP link showing both ends.
    direct = next(
        link
        for link in graph["links"]
        if link["link_type"] == "p2p" and "GigabitEthernet2/0" in link["label"]
    )
    assert direct["direction"] == "bi"
    assert direct["label"] == "GigabitEthernet2/0 ⇄ GigabitEthernet2/0"
    assert sorted(e.split()[1] for e in direct["ends"]) == ["10.10.1.1", "10.10.1.2"]
    exported = service.export("user", result["snapshot_id"])
    assert [
        n["network-id"].rsplit(":", 1)[1] for n in exported["ietf-network:networks"]["network"]
    ] == [
        "l2",
        "l3",
    ]
    # 2.2.2.x is advertised on the direct link but lies outside the task scope.
    assert {u["address"] for u in graph["coverage"]["unexplored"]} == {"2.2.2.1", "2.2.2.2"}


def lab_run(world):
    for address, device in (("10.10.1.1", "vsr-1"), ("10.10.1.2", "vsr-2")):
        for cap, name in LAB_FILES.items():
            world.access.captures[(address, cap)] = (LAB / device / f"{name}.txt").read_text(
                encoding="utf-8"
            )
    body = discovery(max_depth=1)
    body.seeds, body.allowed_cidrs = ["10.10.1.1"], ["10.10.1.0/24"]
    row = service.submit("user", body)
    service.execute_run(row["id"], lambda *args: None)
    service.execute_run(row["id"], lambda *args: None)
    return service.read("user", row["id"])


def test_snapshot_state_is_independent_partial_tree_and_owner_scoped(world):
    result = lab_run(world)
    state = service.snapshot_state("user", result["snapshot_id"])
    assert state["snapshot_id"] == result["snapshot_id"]
    assert len(state["devices"]) == 2
    device = state["devices"][0]
    assert set(device["tree"]) == {"ietf-interfaces:interfaces"}
    assert device["metadata"]["completeness"] == "partial"
    assert device["metadata"]["observation_id"]
    exported_device = service.device_model("user", result["snapshot_id"], device["address"])
    assert exported_device["tree"] == device["tree"]
    with pytest.raises(HTTPException) as missing:
        service.device_model("user", result["snapshot_id"], "192.0.2.99")
    assert missing.value.status_code == 404
    assert any(g["code"] == "mandatory_statistics_not_observed" for g in device["gaps"])
    assert all(
        "state" not in n
        for n in service.export("user", result["snapshot_id"])["ietf-network:networks"]["network"]
    )
    with pytest.raises(HTTPException) as error:
        service.snapshot_state("stranger", result["snapshot_id"])
    assert error.value.status_code == 404


def test_older_snapshot_state_uses_its_run_without_rewriting_it(world):
    result = lab_run(world)
    with world.sessions() as session:
        snap = session.get(Snapshot, result["snapshot_id"])
        graph = dict(snap.graph)
        graph.pop("state")
        snap.graph = graph
        # A newer attempt in this run with failed LLDP still has valid current
        # interfaces. It must replace the old UP interface observation for state.
        target = session.scalar(select(Target).where(Target.run_id == snap.run_id))
        target.version = 2
        session.add(
            Observation(
                id="latest-state",
                run_id=snap.run_id,
                target_id=target.id,
                attempt=2,
                observed_at=now(),
                evidence_key="latest-state",
                data={
                    "complete": False,
                    "capabilities": {"interfaces": "complete", "lldp": "failed"},
                    "facts": {
                        "interfaces": [
                            {
                                "ifname": "GigabitEthernet2/0",
                                "type": "ethernet",
                                "oper_status": "down",
                            }
                        ]
                    },
                },
            )
        )
    state = service.snapshot_state("user", result["snapshot_id"])
    device = next(
        d for d in state["devices"] if d["metadata"].get("observation_id") == "latest-state"
    )
    assert device["collection_status"] == "failed"
    assert device["tree"]["ietf-interfaces:interfaces"]["interface"][0]["oper-status"] == "down"
    with world.sessions() as session:
        assert "state" not in session.get(Snapshot, result["snapshot_id"]).graph


def test_stored_snapshot_state_does_not_change_when_new_attempt_arrives(world):
    result = lab_run(world)
    original = service.snapshot_state("user", result["snapshot_id"])
    with world.sessions() as session:
        target = session.scalar(select(Target).where(Target.run_id == result["id"]))
        session.add(
            Observation(
                id="too-late",
                run_id=result["id"],
                target_id=target.id,
                attempt=2,
                observed_at=now(),
                evidence_key="too-late",
                data={"complete": False},
            )
        )
    assert service.snapshot_state("user", result["snapshot_id"]) == original


def test_graph_keeps_same_network_l2_history_without_old_current_state(world):
    from src.apps.topology.backend.model.builder import L2T, NT

    result = lab_run(world)
    with world.sessions() as session:
        network = session.get(Network, result["network_id"])
        run = Run(
            id="failed-run",
            owner_id="user",
            network_id=network.id,
            request_id="failed-run",
            input_hash="x",
            settings={
                "mode": "ssh",
                "allowed_cidrs": ["10.10.1.0/24"],
                "max_depth": 1,
                "profile_id": "auto",
            },
            status="partial",
        )
        session.add(run)
        session.flush()
        for index in (1, 2):
            session.add(
                Target(
                    id=f"failed-{index}",
                    run_id=run.id,
                    depth=0,
                    target_key=f"failed-{index}",
                    address=f"10.10.1.{index}",
                    status="failed",
                    reason="connection_failed",
                )
            )
        session.flush()
        graph = service._graph(session, run)
    l2 = next(
        n
        for n in graph["simap"]["ietf-network:networks"]["network"]
        if L2T + "l2-topology" in n["network-types"]
    )
    # Only managed two-ended L2 history is retained; host/shared-media links and
    # every old L3 subnet are not asserted as current observations.
    assert len(l2[NT + "link"]) == 1
    assert all(d["collection_status"] != "complete" for d in graph["state"]["devices"])
    link = graph["links"][0]
    assert link["freshness"] == "historical" and link["available"] is None
    assert link["prior_snapshot_id"] == result["snapshot_id"]
    assert all(n["identity_status"] == "known" for n in graph["nodes"])


def test_graph_rejects_previous_snapshot_from_another_network(world):
    result = lab_run(world)
    with world.sessions() as session:
        network = Network(
            id="other-network",
            owner_id="user",
            name="Other",
            domain="default",
            current_snapshot=result["snapshot_id"],
        )
        session.add(network)
        session.flush()
        run = Run(
            id="other-run",
            owner_id="user",
            network_id=network.id,
            request_id="other-run",
            input_hash="x",
            settings={
                "mode": "ssh",
                "allowed_cidrs": ["10.10.1.0/24"],
                "max_depth": 1,
                "profile_id": "auto",
            },
            status="completed",
        )
        session.add(run)
        session.flush()
        graph = service._graph(session, run)
        assert graph["state"] == {"devices": [], "links": {}}
        assert graph["nodes"] == []


def test_failed_current_attempt_without_evidence_cannot_borrow_old_up(world):
    result = lab_run(world)
    with world.sessions() as session:
        target = session.scalar(select(Target).where(Target.run_id == result["id"]))
        target.version += 1
        target.status, target.reason = "waiting_for_input", "evidence_unavailable"
        session.flush()
        current = service._all_observations(session, result["id"])
        assert target.address not in {o["address"] for o in current}
        graph = service._graph(session, session.get(Run, result["id"]))
        device = next(d for d in graph["state"]["devices"] if d["address"] == target.address)
        assert device["collection_status"] != "complete"
        assert device["tree"]["ietf-interfaces:interfaces"]["interface"] == []
        assert all(link["available"] is not True for link in graph["state"]["links"].values())


def test_discovery_keeps_each_route_table_for_path_tracing(world):
    lab_run(world)
    with world.sessions() as session:
        observations = [o.data for o in session.scalars(select(Observation))]
    assert {len(data["facts"]["routes"]) for data in observations} == {18, 19}
    assert all(data["capabilities"]["routes"] == "complete" for data in observations)
    route = next(
        r
        for data in observations
        for r in data["facts"]["routes"]
        if r["prefix"] == "192.168.1.0/24" and r["protocol"] == "O_INTRA"
    )
    hop = {"ip": "2.2.2.2", "ifname": "GigabitEthernet2/0", "vrf": "default"}
    assert route["next_hops"] == [hop]


def test_locate_answers_from_a_snapshot_with_evidence(world):
    result = lab_run(world)
    found = service.locate("user", result["snapshot_id"], "192.168.1.2")
    assert found["status"] == "confirmed" and found["snapshot_id"] == result["snapshot_id"]
    port = found["locations"][0]["ports"][0]
    assert (port["address"], port["port"], port["vlan"]) == (
        "10.10.1.1",
        "GigabitEthernet3/0",
        "200",
    )
    assert port["evidence_id"] == next(
        t["evidence_id"] for t in result["targets"] if t["address"] == "10.10.1.1"
    )
    # Highlighted ids are canvas ids of the same snapshot.
    canvas_ids = {n["id"] for n in result["graph"]["nodes"]}
    assert {port["node_id"], found["locations"][0]["host_node"]} <= canvas_ids
    assert service.locate("user", result["snapshot_id"], "10.10.1.254")["status"] == (
        "shared_segment"
    )
    with pytest.raises(HTTPException) as bad:
        service.locate("user", result["snapshot_id"], "not-an-address")
    assert bad.value.status_code == 422
    with pytest.raises(HTTPException) as foreign:
        service.locate("stranger", result["snapshot_id"], "192.168.1.2")
    assert foreign.value.status_code == 404


def test_locate_searches_the_current_snapshot_of_every_network(world):
    lab_run(world)
    found = service.locate_current("user", "0050.7966.6801")
    assert [f["status"] for f in found] == ["confirmed"]
    assert service.locate_current("user", "10.200.0.1") == []
    assert service.locate_current("stranger", "0050.7966.6801") == []


def test_trace_answers_both_directions_from_a_snapshot(world):
    result = lab_run(world)
    found = service.trace("user", result["snapshot_id"], "1.1.1.2", "192.168.1.2")
    assert found["snapshot_id"] == result["snapshot_id"]
    assert found["analysis_profile"] == service.MODEL_PROFILE
    assert found["snapshot_model_profile"] == result["graph"]["model_profile"]
    assert found["forward"]["status"] == found["reverse"]["status"] == "complete"
    first = found["forward"]["paths"][0]["hops"][0]
    assert (first["device"]["address"], first["route"]["next_hop"]) == ("10.10.1.2", "2.2.2.2")
    # Each hop carries the observation it came from, like located ports do.
    assert first["device"]["evidence_id"] == next(
        t["evidence_id"] for t in result["targets"] if t["address"] == "10.10.1.2"
    )
    assert found["symmetric"] is True
    # Timestamp the observations without claiming to know the traffic's actual path.
    assert found["meaning"].startswith("这是根据 ") and "候选路径" in found["meaning"]
    assert "（北京时间）" in found["meaning"] and "+00:00" not in found["meaning"]
    with pytest.raises(HTTPException) as bad:
        service.trace("user", result["snapshot_id"], "1.1.1.2", "224.0.0.5")
    assert bad.value.status_code == 422
    with pytest.raises(HTTPException) as foreign:
        service.trace("stranger", result["snapshot_id"], "1.1.1.2", "192.168.1.2")
    assert foreign.value.status_code == 404


def test_trace_searches_current_snapshots_that_know_either_end(world):
    lab_run(world)
    [found] = service.trace_current("user", "192.168.1.2", "1.1.1.2")
    assert [h["device"]["address"] for h in found["forward"]["paths"][0]["hops"]] == [
        "10.10.1.1",
        "10.10.1.2",
    ]
    assert "候选路径" in found["meaning"] and "不能单凭差异断定" in found["meaning"]
    assert service.trace_current("user", "10.200.0.1", "10.200.0.2") == []
    assert service.trace_current("stranger", "192.168.1.2", "1.1.1.2") == []


def test_offline_snapshots_without_facts_find_nothing(world):
    row = service.import_captures("user", offline())
    assert service.locate("user", row["snapshot_id"], "10.0.0.2")["status"] == "not_found"


def test_discovery_records_host_keys_itself_for_both_sessions(world):
    row = service.submit("user", discovery(max_depth=0))
    service.execute_run(row["id"], lambda *args: None)
    assert world.trust_used == [(True, True)]
    assert world.trust.verified[-1] == ("10.0.0.1", True)

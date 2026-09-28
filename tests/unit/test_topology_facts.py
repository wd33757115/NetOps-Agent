"""Shared facts enforce live device grants, atomic evidence and current-set semantics."""

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.apps.device_access.backend.grants import GrantCreate, GrantState, GrantStore
from src.apps.device_access.backend.models import Base as AccessBase
from src.apps.topology.backend.facts.models import ARP, FactSet
from src.apps.topology.backend.facts.repository import FactInput, FactRepository
from src.apps.topology.backend.models import Base, Network, Run, Snapshot
from src.auth.models import CurrentUser
from src.platform.kernel.migrations import configuration
from src.platform.kernel.registry import discover

WHEN = datetime(2026, 9, 28, 8, 32, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'facts.db'}")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    AccessBase.metadata.create_all(engine)

    @contextmanager
    def sessions():
        with Session(engine) as session, session.begin():
            yield session

    grants, evidence = GrantStore(sessions=sessions), {}
    for owner in ("alice", "bob"):
        grants.create(
            "admin",
            GrantCreate(owner_id=owner, domain="lab", cidr="10.10.1.0/24", action="device.collect"),
        )

    def repo(owner="alice", role="operator"):
        return FactRepository(
            CurrentUser(user_id=owner, username=owner, role=role),
            sessions=sessions,
            authorize=grants.authorize,
            evidence_reader=evidence.__getitem__,
        )

    device = repo().create_device("lab", ["10.10.1.1"], hostname="H3C")
    uid = device["device_uid"]

    def body(*, device_uid=uid, **kw):
        payload = {"domain": "lab", "device_uid": device_uid, "outputs": {"test": "raw"}}
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        key = f"apps/topology/facts/{device_uid}/batch/{digest}.json"
        evidence[key] = payload
        return FactInput(
            **(
                dict(
                    capability="arp",
                    observed_at=WHEN,
                    status="complete",
                    source="offline",
                    platform_profile="h3c",
                    parser_ref="fixture:v1",
                    evidence_ref=key,
                    records=[{"vrf": "default", "ip": "10.10.1.2", "mac": "00:11:22:33:44:55"}],
                )
                | kw
            )
        )

    yield SimpleNamespace(
        engine=engine,
        sessions=sessions,
        grants=grants,
        evidence=evidence,
        repo=repo,
        uid=uid,
        body=body,
    )
    engine.dispose()


def test_team_shares_one_candidate_identity_and_both_users_can_append(env):
    alice, bob = env.repo(), env.repo("bob")
    assert bob.create_device("lab", ["10.10.1.1"])["device_uid"] == env.uid
    first = alice.append("lab", env.uid, env.body())
    assert bob.read_set("lab", env.uid, first["id"])["fact_set"]["collected_by"] == "alice"
    second = bob.append("lab", env.uid, env.body(observed_at=WHEN + timedelta(seconds=1)))
    current = alice.current("lab", env.uid, "arp")["current"]
    assert current["fact_set"]["id"] == second["id"]
    assert current["fact_set"]["collected_by"] == "bob"


def test_empty_complete_replaces_previous_nonempty_and_history_remains(env):
    repo = env.repo()
    first = repo.append("lab", env.uid, env.body())
    second = repo.append(
        "lab", env.uid, env.body(records=[], observed_at=WHEN + timedelta(seconds=1))
    )
    assert repo.current("lab", env.uid, "arp")["current"] == {"fact_set": second, "records": []}
    assert len(repo.read_set("lab", env.uid, first["id"])["records"]) == 1


@pytest.mark.parametrize("status", ["partial", "failed", "unsupported"])
def test_unsuccessful_latest_never_overwrites_complete_but_is_visible(env, status):
    repo = env.repo()
    first = repo.append("lab", env.uid, env.body())
    latest = repo.append(
        "lab", env.uid, env.body(status=status, records=[], observed_at=WHEN + timedelta(seconds=1))
    )
    view = repo.current("lab", env.uid, "arp")
    assert view["current"]["fact_set"]["id"] == first["id"]
    assert view["latest_attempt"]["id"] == latest["id"]


def test_old_late_arrival_does_not_replace_new_observation(env):
    repo = env.repo()
    first = repo.append("lab", env.uid, env.body())
    repo.append("lab", env.uid, env.body(observed_at=WHEN - timedelta(days=1)))
    assert repo.current("lab", env.uid, "arp")["current"]["fact_set"]["id"] == first["id"]


def test_tie_break_is_stable(env, monkeypatch):
    from src.apps.topology.backend.facts import repository

    monkeypatch.setattr(repository, "now", lambda: WHEN)
    rows = [env.repo().append("lab", env.uid, env.body()) for _ in range(2)]
    expected = max(row["id"] for row in rows)
    assert env.repo().current("lab", env.uid, "arp")["current"]["fact_set"]["id"] == expected


def test_cross_domain_unknown_and_revoked_users_cannot_read_or_write(env):
    item = env.repo().append("lab", env.uid, env.body())
    for repo, domain in ((env.repo(), "production"), (env.repo("eve"), "lab")):
        with pytest.raises(HTTPException) as error:
            repo.read_set(domain, env.uid, item["id"])
        assert error.value.status_code == 404
        with pytest.raises(HTTPException):
            repo.append(domain, env.uid, env.body())
    grant = env.grants.list("bob")[0]
    env.grants.set_state("admin", grant["id"], GrantState(revision=1, enabled=False))
    with pytest.raises(HTTPException):
        env.repo("bob").current("lab", env.uid, "arp")


def test_authorized_viewer_reads_shared_facts_but_cannot_submit(env):
    env.repo().append("lab", env.uid, env.body())
    viewer = env.repo("bob", "viewer")
    assert viewer.current("lab", env.uid, "arp")["current"]
    with pytest.raises(HTTPException) as error:
        viewer.append("lab", env.uid, env.body())
    assert error.value.status_code == 403


@pytest.mark.parametrize(
    "changes",
    [
        {"records": [{"device_uid": "another"}]},
        {"records": [{"vrf": "default"}]},
        {"records": [{"vrf": "default", "ip": "10.0.0.1", "mac": "x", "vlan": True}]},
        {"status": "unsupported"},
        {"observed_at": WHEN.replace(tzinfo=None)},
    ],
)
def test_invalid_records_never_leave_fact_set(env, changes):
    with pytest.raises(ValueError):
        env.repo().append("lab", env.uid, env.body(**changes))
    with env.sessions() as session:
        assert session.scalar(select(func.count()).select_from(FactSet)) == 0


def test_evidence_must_match_device_domain_and_hash(env):
    body = env.body()
    env.evidence[body.evidence_ref]["domain"] = "production"
    with pytest.raises(ValueError):
        env.repo().append("lab", env.uid, body)
    env.evidence[body.evidence_ref]["domain"] = "lab"
    env.evidence[body.evidence_ref]["outputs"] = {"test": "changed"}
    with pytest.raises(ValueError):
        env.repo().append("lab", env.uid, body)


def test_row_insert_failure_rolls_back_whole_fact_set(env):
    def fail(*args):
        raise RuntimeError("fact row unavailable")

    event.listen(ARP, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError):
            env.repo().append("lab", env.uid, env.body())
    finally:
        event.remove(ARP, "before_insert", fail)
    with env.sessions() as session:
        assert session.scalar(select(func.count()).select_from(FactSet)) == 0


def test_database_prevents_row_device_and_fact_set_mismatch(env):
    row = env.repo().append("lab", env.uid, env.body())
    other = env.repo().create_device("lab", ["10.10.1.2"])
    with pytest.raises(IntegrityError):
        with env.sessions() as session:
            session.add(
                ARP(
                    id="invalid",
                    device_uid=other["device_uid"],
                    fact_set_id=row["id"],
                    vrf="default",
                    ip="10.10.1.1",
                    mac="00:11:22:33:44:55",
                )
            )


def test_migration_preserves_old_network_run_and_snapshot(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    manifest = discover()["topology"]
    with engine.begin() as connection:
        config, _ = configuration(manifest)
        config.attributes.update(connection=connection, version_table="alembic_app_topology")
        command.upgrade(config, "topology_0001")
        connection.execute(
            Network.__table__.insert().values(
                id="old-net", owner_id="alice", name="old", domain="lab"
            )
        )
        connection.execute(
            Run.__table__.insert().values(
                id="old-run",
                network_id="old-net",
                owner_id="alice",
                request_id="old",
                input_hash="old",
                settings={},
                status="done",
            )
        )
        connection.execute(
            Snapshot.__table__.insert().values(
                id="old-snapshot", network_id="old-net", run_id="old-run", graph={"nodes": ["old"]}
            )
        )
        command.upgrade(config, "topology_0002")
        command.upgrade(config, "topology_0002")
        assert connection.scalar(select(Snapshot.graph)) == {"nodes": ["old"]}
        assert set(inspect(connection).get_table_names()) == {
            "alembic_app_topology",
            *manifest.data["database"]["tables"],
        }
        assert inspect(connection).get_foreign_keys("app_twin_arp")[0]["constrained_columns"] == [
            "fact_set_id",
            "device_uid",
        ]
    engine.dispose()


def test_lab_arp_rows_replay_to_shared_repository_with_raw_evidence(env):
    # Explicit fixture transformation for storage acceptance; production parser is P2-8.
    root = Path(__file__).resolve().parents[1] / "fixtures/topology/h3c/vsr1000-7.1.064"
    for device, host in (("vsr-1", "10.10.1.1"), ("vsr-2", "10.10.1.2")):
        uid = env.repo().create_device("lab", [host])["device_uid"]
        records = []
        for line in (root / device / "arp.txt").read_text().splitlines()[2:]:
            ip, mac, vlan, interface, age, kind = line.split()
            compact = mac.replace("-", "")
            records.append(
                {
                    "vrf": "default",
                    "ip": ip,
                    "mac": ":".join(compact[i : i + 2] for i in range(0, 12, 2)),
                    "vlan": None if vlan == "--" else int(vlan),
                    "ifname": interface,
                    "age_s": int(age) * 60,
                    "type": "dynamic",
                }
            )
        body = env.body(device_uid=uid, records=records)
        payload = {
            "domain": "lab",
            "device_uid": uid,
            "outputs": {"display arp": (root / device / "arp.raw.txt").read_bytes().decode()},
        }
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        key = f"apps/topology/facts/{uid}/lab/{digest}.json"
        env.evidence[key] = payload
        body = body.model_copy(update={"evidence_ref": key})
        env.repo().append("lab", uid, body)
        current = env.repo("bob").current("lab", uid, "arp")["current"]
        assert len(current["records"]) == 4
        assert any(row["ip"] == "10.10.1.254" for row in current["records"])
        assert current["fact_set"]["evidence_sha256"]


@pytest.mark.parametrize(
    "capability,record",
    [
        ("interfaces", {"ifname": "GigabitEthernet1/0", "raw_name": "GE1/0"}),
        ("ip", {"ifname": "GE1/0", "vrf": "default", "address": "10.10.1.1", "prefix_len": 24}),
        ("mac", {"vlan_or_bd": "200", "mac": "00:11:22:33:44:55", "port": "GE3/0"}),
        (
            "lldp",
            {
                "local_if": "GE1/0",
                "remote_chassis_id": "00:11:22:33:44:55",
                "remote_port_id": "GE1/0",
                "remote_mgmt_addresses": ["10.10.1.2"],
            },
        ),
        (
            "routes",
            {
                "vrf": "default",
                "prefix": "0.0.0.0/0",
                "next_hops": [{"ip": "10.10.1.2", "ifname": "GE1/0", "vrf": "default"}],
            },
        ),
        ("vlan", {"vlan_id": 200}),
        ("inventory", {"member_id": "1", "serial": "lab-serial", "role": "master"}),
    ],
)
def test_each_typed_fact_table_round_trips(env, capability, record):
    row = env.repo().append("lab", env.uid, env.body(capability=capability, records=[record]))
    result = env.repo("bob").read_set("lab", env.uid, row["id"])["records"][0]
    assert all(result[key] == value for key, value in record.items())


def test_summary_capabilities_and_missing_complete_view(env):
    repo = env.repo()
    assert repo.current("lab", env.uid, "version") == {"current": None, "latest_attempt": None}
    row = repo.append(
        "lab", env.uid, env.body(capability="version", records=[], summary={"vendor": "H3C"})
    )
    assert repo.read_set("lab", env.uid, row["id"])["fact_set"]["summary"] == {"vendor": "H3C"}


@pytest.mark.parametrize("owner,domain", [("bob", "lab"), ("alice", "other")])
def test_source_run_cannot_be_forged_across_account_or_domain(env, owner, domain):
    with env.sessions() as session:
        session.add(Network(id="network", owner_id=owner, domain=domain, name="network"))
        session.flush()
        session.add(
            Run(
                id="run",
                network_id="network",
                owner_id=owner,
                request_id="source",
                input_hash="hash",
                settings={},
                status="running",
            )
        )
    with pytest.raises(HTTPException):
        env.repo().append("lab", env.uid, env.body(run_id="run"))


def test_evidence_storage_roundtrip_is_device_bound(env, monkeypatch):
    from src.apps.topology.backend import storage

    data = {}

    class Backend:
        def upload_file(self, key, content, content_type):
            data[key] = content
            return True

        def download_file(self, key):
            return data.get(key)

    monkeypatch.setattr(storage, "backend", Backend)
    repo = FactRepository(env.repo().actor, sessions=env.sessions, authorize=env.grants.authorize)
    key = repo.save_evidence("lab", env.uid, {"version": "raw output"})
    row = repo.append("lab", env.uid, env.body(evidence_ref=key))
    assert row["evidence_ref"] == key

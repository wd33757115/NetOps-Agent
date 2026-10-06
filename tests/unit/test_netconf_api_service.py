"""NETCONF API App: seeds, drafts and publishing, capabilities and trials (no device)."""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.apps.netconf_api.backend import service
from src.apps.netconf_api.backend.models import Base, Capability
from src.platform.device_access.contracts import DeviceAccessError

BR1 = Path(__file__).resolve().parents[2] / "tests/fixtures/netconf/h3c-comware7/br1"
ALICE = SimpleNamespace(user_id="alice")
DEVICE = {
    "device_id": "br1",
    "device_name": "BR1",
    "ip": "10.10.1.11",
    "username": "admin",
    "credential_ref": "env:NETOPS_LAB_PASSWORD",
    "vendor": "H3C",
    "platform": "comware",
    "model": "VSR1000",
}
REPLIES = {"Ifmgr": "ifmgr-interfaces.xml", "ARP": "arp.xml", "Route": "route.xml"}


@pytest.fixture(autouse=True)
def database(monkeypatch):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)

    @contextmanager
    def sessions():
        with Session(engine) as session, session.begin():
            yield session

    monkeypatch.setattr(service, "sessions", sessions)
    monkeypatch.setattr(service, "RULES_SOURCE", "shipped")
    with sessions() as session:
        service.import_seeds(session)
    yield sessions
    engine.dispose()


class FakeSession:
    """A platform NETCONF session answering from BR1's replies."""

    def __init__(self, fail=None):
        self.capabilities = tuple((BR1 / "hello-capabilities.txt").read_text("utf-8").split())
        self.asked = []
        self.fail = fail

    def get(self, subtree):
        self.asked.append(subtree)
        if self.fail:
            raise DeviceAccessError(self.fail, "NETCONF reply was not received before timeout")
        module = subtree.split("><", 1)[1].split(">", 1)[0].lstrip("<")
        return (BR1 / REPLIES[module]).read_text("utf-8")


@pytest.fixture
def device(monkeypatch):
    fake = SimpleNamespace(session=FakeSession(), refuse=None)

    @contextmanager
    def netconf(actor, found):
        assert actor is ALICE and found is DEVICE
        if fake.refuse:
            raise DeviceAccessError(fake.refuse, "authentication failed")
        yield fake.session, lambda: None

    monkeypatch.setattr(service, "_device", lambda device_id: DEVICE)
    monkeypatch.setattr(service, "_netconf", netconf)
    return fake


def status(call):
    with pytest.raises(HTTPException) as error:
        call()
    return error.value.status_code


def test_seeds_are_published_once_and_a_user_version_is_kept(database):
    listed = {row["id"]: row for row in service.list_definitions(ALICE)}
    assert set(listed) == {
        "h3c-comware7.acl-bindings",
        "h3c-comware7.acl-groups",
        "h3c-comware7.acl-rules",
        "h3c-comware7.arp",
        "h3c-comware7.interfaces",
        "h3c-comware7.nat-bindings",
        "h3c-comware7.nat-rules",
        "h3c-comware7.packet-filter-default",
        "h3c-comware7.packet-filter-run",
        "h3c-comware7.pbr-bindings",
        "h3c-comware7.routes",
    }
    assert {row["published_version"] for row in listed.values()} == {1}
    assert listed["h3c-comware7.routes"]["check_item"] == "topology.routes"
    with database() as session:
        assert service.import_seeds(session) == []  # unchanged
    # Someone publishes their own version; a later seed import leaves it.
    body = service.read_definition(ALICE, "h3c-comware7.arp")["published"]["body"]
    body = {**body, "description": "ARP，含说明"}
    saved = service.save_draft(ALICE, "h3c-comware7.arp", service.Draft(body=body, revision=1))
    service.publish(ALICE, "h3c-comware7.arp", service.Publish(revision=saved["revision"]))
    with database() as session:
        assert service.import_seeds(session) == []
    read = service.read_definition(ALICE, "h3c-comware7.arp")
    assert read["published"]["version"] == 2 and read["published"]["published_by"] == "alice"
    assert [v["version"] for v in read["versions"]] == [2, 1]


def test_a_draft_is_published_only_when_it_serves_its_check_item(database):
    current = service.read_definition(ALICE, "h3c-comware7.routes")
    body = current["published"]["body"]
    narrowed = {**body, "values": {k: v for k, v in body["values"].items() if k != "PROTOCOL"}}
    saved = service.save_draft(
        ALICE, "h3c-comware7.routes", service.Draft(body=narrowed, revision=current["revision"])
    )
    with pytest.raises(HTTPException) as refused:
        service.publish(ALICE, "h3c-comware7.routes", service.Publish(revision=saved["revision"]))
    assert refused.value.status_code == 409
    assert refused.value.detail["gaps"] == ["required_value_unmapped:PROTOCOL:comware"]
    # The draft stays, and the published version is still in use.
    read = service.read_definition(ALICE, "h3c-comware7.routes")
    assert read["has_draft"] and read["published_version"] == 1
    assert any(
        rule.name == "PROTOCOL" for rule in service.published()["h3c-comware7.routes"].values
    )
    service.discard_draft(ALICE, "h3c-comware7.routes", service.Discard(revision=read["revision"]))
    assert not service.read_definition(ALICE, "h3c-comware7.routes")["has_draft"]


def test_drafts_are_checked_when_saved_and_edits_do_not_cross(database):
    current = service.read_definition(ALICE, "h3c-comware7.arp")
    body = current["published"]["body"]
    draft = service.Draft(body={**body, "path": "top/ARP/<ArpTable>"}, revision=current["revision"])
    assert status(lambda: service.save_draft(ALICE, "h3c-comware7.arp", draft)) == 422
    other = service.Draft(body=body, revision=current["revision"])
    assert status(lambda: service.save_draft(ALICE, "h3c-comware7.routes", other)) == 422  # id
    service.save_draft(ALICE, "h3c-comware7.arp", other)
    assert status(lambda: service.save_draft(ALICE, "h3c-comware7.arp", other)) == 409  # stale
    # A new definition starts at revision 0; its lookups must name a published one.
    lookup = {"definition": "x.missing", "match": "IfIndex", "take": "Name"}
    new = {
        **body,
        "id": "h3c-comware7.arp2",
        "values": {**body["values"], "INTERFACE": {"leaf": "IfIndex", "lookup": lookup}},
    }
    saved = service.save_draft(ALICE, "h3c-comware7.arp2", service.Draft(body=new, revision=0))
    assert saved["revision"] == 1
    publish = service.Publish(revision=1)
    assert status(lambda: service.publish(ALICE, "h3c-comware7.arp2", publish)) == 422


def test_a_trial_keeps_the_rows_problems_and_filters(device):
    result = service.trial(
        ALICE, service.TrialRequest(device_id="br1", definition_id="h3c-comware7.arp")
    )
    assert result["status"] == "succeeded" and result["version"] == 1
    assert (result["read_count"], result["row_count"], result["problem_count"]) == (6, 6, 0)
    row = next(row for row in result["rows"] if row["IP_ADDRESS"] == "10.0.13.2")
    assert row == {
        "ref": 4,
        "IP_ADDRESS": "10.0.13.2",
        "MAC_ADDRESS": "aabb-cc00-0210",
        "INTERFACE": "GE3/0",
        "TYPE": "D",
        "VRF": "--",
    }
    assert [item["definition"] for item in result["filters"]] == [
        "h3c-comware7.interfaces",
        "h3c-comware7.arp",
    ]
    assert device.session.asked == [item["filter"] for item in result["filters"]]
    assert service.read_trial(ALICE, result["id"])["rows"] == result["rows"]
    assert status(lambda: service.read_trial(SimpleNamespace(user_id="bob"), result["id"])) == 404
    assert [t["id"] for t in service.list_trials(ALICE, "h3c-comware7.arp")] == [result["id"]]
    # The hello of the session is kept as the device's capabilities.
    capabilities = service.read_capabilities(ALICE, "br1")
    assert capabilities["status"] == "available" and capabilities["capability_count"] == 384
    assert capabilities["definitions"] == [
        "h3c-comware7.acl-bindings",
        "h3c-comware7.acl-groups",
        "h3c-comware7.acl-rules",
        "h3c-comware7.arp",
        "h3c-comware7.interfaces",
        "h3c-comware7.nat-bindings",
        "h3c-comware7.nat-rules",
        "h3c-comware7.packet-filter-default",
        "h3c-comware7.packet-filter-run",
        "h3c-comware7.pbr-bindings",
        "h3c-comware7.routes",
    ]


def test_a_draft_trial_reports_what_the_draft_cannot_map(database, device):
    current = service.read_definition(ALICE, "h3c-comware7.routes")
    body = current["published"]["body"]
    codes = {k: v for k, v in body["values"]["PROTOCOL"]["codes"].items() if k != "6/6"}
    draft = {
        **body,
        "values": {**body["values"], "PROTOCOL": {**body["values"]["PROTOCOL"], "codes": codes}},
    }
    service.save_draft(
        ALICE, "h3c-comware7.routes", service.Draft(body=draft, revision=current["revision"])
    )
    request = service.TrialRequest(device_id="br1", definition_id="h3c-comware7.routes", draft=True)
    result = service.trial(ALICE, request)
    assert result["status"] == "succeeded" and result["version"] is None
    assert (result["read_count"], result["row_count"]) == (47, 44)  # the three BGP routes
    kinds = {(p["value"], p["kind"], p["raw"]) for p in result["problems"]}
    assert kinds == {("PROTOCOL", "unmapped_code", "6/6"), ("PROTOCOL", "required_missing", None)}
    published = service.trial(
        ALICE, service.TrialRequest(device_id="br1", definition_id="h3c-comware7.routes")
    )
    assert (published["row_count"], published["problem_count"]) == (47, 0)


def test_a_failed_session_is_kept_as_a_failed_trial(device):
    device.refuse = "authentication_failed"
    request = service.TrialRequest(device_id="br1", definition_id="h3c-comware7.routes")
    result = service.trial(ALICE, request)
    assert (result["status"], result["failure_code"]) == ("failed", "authentication_failed")
    assert service.read_capabilities(ALICE, "br1")["status"] == "unavailable"
    device.refuse = None
    device.session.fail = "timeout"
    result = service.trial(ALICE, request)
    assert (result["status"], result["failure_code"], result["filters"][0]["definition"]) == (
        "failed",
        "timeout",
        "h3c-comware7.routes",
    )
    # The session was up: its hello still counts.
    assert service.read_capabilities(ALICE, "br1")["status"] == "available"


def test_a_definition_runs_only_on_its_platform(monkeypatch, device):
    monkeypatch.setattr(
        service, "_device", lambda device_id: {**DEVICE, "vendor": "Cisco", "platform": "ios"}
    )
    request = service.TrialRequest(device_id="br1", definition_id="h3c-comware7.arp")
    assert status(lambda: service.trial(ALICE, request)) == 409
    assert (
        status(
            lambda: service.trial(ALICE, service.TrialRequest(device_id="br1", definition_id="x"))
        )
        == 404
    )


def test_discover_records_the_hello(database, device):
    view = service.discover(ALICE, "br1")
    assert view["module_count"] == 165 + 6 + 1  # H3C data and action modules, ietf monitoring
    assert device.session.asked == []  # the hello only, no RPC
    device.refuse = "host_key_rejected"
    assert service.discover(ALICE, "br1")["failure_code"] == "host_key_rejected"
    with database() as session:
        assert len(session.get(Capability, "br1").capabilities) == 384  # the last good hello


def test_devices_are_listed_with_their_last_discovery(database, device, monkeypatch):
    other = {**DEVICE, "device_id": "sw1", "device_name": "SW1", "ip": "10.10.1.21"}
    monkeypatch.setattr(service, "_inventory", lambda: [DEVICE, other])
    listed = service.list_devices(ALICE)
    assert [(d["device_id"], d["capability"]) for d in listed] == [("br1", None), ("sw1", None)]
    # Only what identifies the device: no login name or credential reference.
    assert set(listed[0]) == {
        "device_id",
        "device_name",
        "ip",
        "vendor",
        "platform",
        "model",
        "capability",
    }
    service.discover(ALICE, "br1")
    [br1, sw1] = service.list_devices(ALICE)
    assert sw1["capability"] is None
    capability = br1["capability"]
    assert capability["status"] == "available" and capability["module_count"] == 172
    assert len(capability["modules"]) == 172
    arp = next(m for m in capability["modules"] if m["name"] == "ARP")
    assert arp["namespace"] and arp["revision"]
    assert "h3c-comware7.arp" in capability["definitions"]
    assert capability["unavailable"] == []
    # A module the device no longer announces: its definitions are listed as unavailable.
    with database() as session:
        row = session.get(Capability, "br1")
        row.capabilities = [c for c in row.capabilities if "module=ARP&" not in c + "&"]
    capability = service.list_devices(ALICE)[0]["capability"]
    assert {"id": "h3c-comware7.arp", "module": "ARP"} in capability["unavailable"]
    assert "h3c-comware7.arp" not in capability["definitions"]


def test_the_session_logs_in_with_the_inventory_credential_reference():
    access = service._access(DEVICE)
    profile = access.profile_resolver("br1")
    assert (profile.username, profile.port, profile.password) == ("admin", 830, None)
    assert profile.credential_ref == "env:NETOPS_LAB_PASSWORD"
    assert access.target_resolver("br1").host == "10.10.1.11"
    assert access.netconf_connector.host_key_verifier is not None


def test_a_large_table_keeps_its_first_rows_and_whole_counts(monkeypatch, device):
    monkeypatch.setattr(service, "TRIAL_ROWS", 10)
    request = service.TrialRequest(device_id="br1", definition_id="h3c-comware7.routes")
    result = service.trial(ALICE, request)
    assert (result["read_count"], result["row_count"], len(result["rows"])) == (47, 47, 10)
    assert [row["ref"] for row in result["rows"]] == list(range(1, 11))


def test_a_second_definition_for_the_same_family_is_not_published(database):
    body = service.read_definition(ALICE, "h3c-comware7.arp")["published"]["body"]
    copy = {**body, "id": "h3c-comware7.arp-copy"}
    service.save_draft(ALICE, copy["id"], service.Draft(body=copy, revision=0))
    with pytest.raises(HTTPException) as refused:
        service.publish(ALICE, copy["id"], service.Publish(revision=1))
    assert refused.value.status_code == 409 and "h3c-comware7.arp" in refused.value.detail
    # Another family is no conflict, but it still needs the check item there (no_contract).
    other = {**copy, "platforms": ["comware5"]}
    service.save_draft(ALICE, copy["id"], service.Draft(body=other, revision=1))
    assert status(lambda: service.publish(ALICE, copy["id"], service.Publish(revision=2))) == 409

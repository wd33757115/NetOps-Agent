"""NETCONF first, the CLI when NETCONF cannot serve a table, and the issues kept for both
(BR1: the campus-wan CLI capture and the NETCONF replies of the same lab)."""

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.apps.netconf_api.backend import mapping
from src.apps.netconf_api.runtime import DefinitionReader, StandardReader
from src.apps.topology.backend import issues
from src.apps.topology.backend.collectors import collect as runtime
from src.apps.topology.backend.collectors import netconf as nc
from src.apps.topology.backend.models import CollectionIssue
from src.apps.topology.backend.service import topology_observation
from src.platform.device_access.contracts import (
    AccessContext,
    AccessGrant,
    ConnectionProfile,
    DeviceAccessError,
    Target,
)
from src.platform.device_access.service import DeviceAccessService

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "tests/fixtures/topology/campus-wan"
NETCONF = ROOT / "tests/fixtures/netconf/h3c-comware7/br1"
SEEDS = ROOT / "src/apps/netconf_api/backend/seeds/h3c_comware7.yaml"
POLICY = ROOT / "tests/fixtures/netconf/h3c-comware7/br1-policy"
REPLIES = {
    "Ifmgr": NETCONF / "ifmgr-interfaces.xml",
    "ARP": NETCONF / "arp.xml",
    "Route": NETCONF / "route.xml",
    "ACL": POLICY / "acl.xml",
    "NAT": POLICY / "nat.xml",
    "PBR": POLICY / "pbr.xml",
}
# The policy tables of the same moment as POLICY's replies.
POLICY_CLI = {
    "display acl all": "cli-display-acl-all.txt",
    "display packet-filter interface": "cli-display-packet-filter-interface.txt",
    "display nat all": "cli-display-nat-all.txt",
    "display ip policy-based-route setup": "cli-display-ip-policy-based-route-setup.txt",
    "display ip policy-based-route": "cli-display-ip-policy-based-route.txt",
}
POLICY_CAPS = (
    "acl_rules",
    "acl_bindings",
    "nat_rules",
    "nat_bindings",
    "pbr_bindings",
    "pbr_rules",
)
NO_VRFS = "L3VPN is not configured."


def cli_outputs():
    manifest = json.loads((CLI / "manifest.json").read_text(encoding="utf-8"))
    outputs = {
        row["command"]: (CLI / row["files"]["txt"]["path"]).read_text(encoding="utf-8")
        for row in manifest["observations"]
        if row["device"] == "br1"
    }
    outputs["display ip vpn-instance"] = NO_VRFS
    for command, name in POLICY_CLI.items():
        outputs[command] = (POLICY / name).read_text(encoding="utf-8")
    return outputs


@pytest.fixture(autouse=True)
def definitions(monkeypatch):
    bodies = yaml.safe_load(SEEDS.read_text(encoding="utf-8"))["definitions"]
    loaded = mapping.load_all(bodies)
    reader = DefinitionReader(loader=lambda: {key: (d, 1, "f" * 64) for key, d in loaded.items()})
    monkeypatch.setitem(nc._cache, "reader", (reader, float("inf")))
    # The standard tables are read only where a test asks for them (standard_tables).
    monkeypatch.setitem(nc._cache, "standard", (StandardReader(loader=dict), float("inf")))
    return loaded


@pytest.fixture
def standard_tables(monkeypatch):
    """The shipped H3C mapping, and BR1's replies for the tables it adds."""
    monkeypatch.setitem(nc._cache, "standard", (StandardReader(), float("inf")))
    for module, name in (
        ("IPV4ADDRESS", "ipv4address.xml"),
        ("OSPF", "ospf.xml"),
        ("LLDP", "lldp.xml"),
    ):
        monkeypatch.setitem(REPLIES, module, NETCONF / name)


@pytest.fixture
def br1():
    state = SimpleNamespace(
        commands=[],
        evidence=[],
        outputs=cli_outputs(),
        replies={},  # module -> reply text overriding the fixture
        hello=tuple((NETCONF / "hello-capabilities.txt").read_text("utf-8").split()),
        refuse=None,  # a failure code for the NETCONF login
        fail_on=None,  # a module whose read breaks the session
        refused=None,  # a module whose read the device refuses (an RPC error)
        asked=[],
        sessions=0,
    )

    class Driver:
        name, version = "paramiko", "fixture"

        def connect(self, *args, **kwargs):
            return self

        def send_command(self, command, read_timeout):
            state.commands.append(command)
            return state.outputs.get(
                command, "               ^\n % Unrecognized command found at '^' position."
            )

        def disconnect(self):
            pass

        @contextmanager
        def hold(self, *args):
            yield lambda: None

    class Session:
        def __init__(self):
            self.capabilities = state.hello

        def get(self, subtree):
            module = subtree.split("><", 1)[1].split(">", 1)[0].lstrip("<")
            if module == state.fail_on:
                raise DeviceAccessError("timeout", "NETCONF reply was not received before timeout")
            if module == state.refused:
                raise DeviceAccessError("netconf_rpc_error", "unknown element")
            state.asked.append(subtree)
            return state.replies.get(module) or REPLIES[module].read_text("utf-8")

        def close(self):
            pass

    class Connector:
        def connect(self, target, profile, operation):
            assert profile.port == 830 and profile.username == "test-user"
            state.sessions += 1
            if state.refuse:
                raise DeviceAccessError(state.refuse, "NETCONF login failed")
            return Session()

    driver = Driver()
    access = DeviceAccessService(
        target_resolver=lambda _: Target("node", "10.10.1.11"),
        profile_resolver=lambda _: ConnectionProfile("connection", "test-user", "test-password"),
        driver=driver,
        admission=driver,
        netconf=Connector(),
    )
    grants = (
        AccessGrant("node", "connection", runtime.command_allowlist()),
        AccessGrant("node", "connection", action="device.netconf"),
    )

    def collect(netconf="prefer", grants=grants):
        return runtime.collect_node(
            access,
            AccessContext("actor", "topology", grants),
            "node",
            "connection",
            mode="topology",
            should_continue=lambda: True,
            on_result=lambda result: state.evidence.append(result),
            netconf=netconf,
        )

    state.collect = collect
    state.cli_only_grants = grants[:1]
    return state


def test_tables_netconf_serves_are_read_over_netconf_and_kept_as_evidence(br1):
    result = br1.collect()
    for cap, rows in (("arp", 6), ("routes", 42)):
        parsed = result.parsed[cap]
        assert (parsed.tier, parsed.status) == ("netconf", "complete")
        assert parsed.netconf["status"] == "used" and parsed.netconf["reason"] is None
        assert len(parsed.records) == rows  # routes: 47 next hops of 42 destinations
    assert result.parsed["arp"].netconf["definition"] == "h3c-comware7.arp@v1"
    assert "display arp verbose" not in br1.commands
    assert "display ip routing-table" not in br1.commands
    assert "display lldp neighbor-information verbose" in br1.commands
    for cap in POLICY_CAPS:
        parsed = result.parsed[cap]
        assert (parsed.tier, parsed.status, parsed.netconf["status"]) == (
            "netconf",
            "complete",
            "used",
        ), (cap, parsed.warnings, parsed.netconf)
    assert not set(POLICY_CLI) & set(br1.commands)
    assert br1.sessions == 1  # one NETCONF session for every table
    evidence = {e.command_id: e for e in br1.evidence if e.driver == "netconf"}
    assert {"netconf-arp-0", "netconf-arp-1", "netconf-routes-0"} <= set(evidence)
    assert {cap for cap in POLICY_CAPS if f"netconf-{cap}-0" in evidence} == set(POLICY_CAPS)
    assert evidence["netconf-arp-0"].driver_version == "h3c-comware7.interfaces"
    hop = next(r for r in result.parsed["arp"].records if r["ip"] == "10.0.13.2")
    assert hop["ifname"] == "GigabitEthernet3/0" and hop["mac"] == "aa:bb:cc:00:02:10"
    source = result.parsed["arp"].sources[0]
    assert source["raw"]["netconf"] == "h3c-comware7.arp@v1" and source["lines"] == [1]
    metadata = topology_observation(result)["fact_metadata"]
    assert metadata["routes"]["tier"] == "netconf"
    assert metadata["routes"]["netconf"]["rows"] == 47


def test_netconf_facts_are_the_cli_facts(br1):
    result = br1.collect("compare")
    for cap in ("arp", "routes"):
        parsed = result.parsed[cap]
        assert parsed.tier == "rules"  # compared: the CLI stays the fact
        comparison = parsed.netconf["comparison"]
        assert parsed.netconf["status"] == "compared"
        # ARP ages move and BR1's routes were captured a day apart from NETCONF's; the
        # routes agree fact by fact, the ARP facts besides the CLI-only age.
        assert comparison["status"] == "same", comparison
    assert result.parsed["arp"].netconf["comparison"]["cli_only_fields"] == ["age_s"]
    assert "display arp verbose" in br1.commands and br1.sessions == 1
    # The policy tables of one moment agree but for how one unknown condition is written
    # (dscp ef: both leave that rule out of the computation).
    for cap in POLICY_CAPS:
        comparison = result.parsed[cap].netconf["comparison"]
        expected = "different" if cap == "acl_rules" else "same"
        assert comparison["status"] == expected, (cap, comparison)
    acl = result.parsed["acl_rules"].netconf["comparison"]
    assert acl["differing"] == 2 and acl["only_cli"][0]["unsupported"] == ["condition:dscp ef"]


def test_an_unmapped_code_sends_the_table_to_the_cli(br1):
    route = (NETCONF / "route.xml").read_text("utf-8")
    br1.replies["Route"] = route.replace(
        "<ProtocolID>6</ProtocolID><SubProtocolID>6</SubProtocolID>",
        "<ProtocolID>6</ProtocolID><SubProtocolID>9</SubProtocolID>",
        1,
    )
    result = br1.collect()
    routes = result.parsed["routes"]
    assert (routes.tier, routes.status) == ("rules", "complete")  # read over the CLI
    assert "display ip routing-table" in br1.commands
    meta = routes.netconf
    assert (meta["status"], meta["reason"], meta["problem_count"]) == ("fallback", "problems", 2)
    assert meta["problems"][0]["kind"] == "unmapped_code" and meta["problems"][0]["raw"] == "6/9"
    assert result.parsed["arp"].tier == "netconf"  # the other table is unaffected


def test_a_netconf_login_failure_sends_every_table_to_the_cli(br1):
    br1.refuse = "authentication_failed"
    result = br1.collect()
    for cap in ("arp", "routes"):
        assert result.parsed[cap].tier == "rules"
        assert result.parsed[cap].netconf["reason"] == "session:authentication_failed"
    assert {"display arp verbose", "display ip routing-table"} <= set(br1.commands)


def test_a_session_lost_midway_sends_the_rest_to_the_cli(br1):
    br1.fail_on = "Route"  # the route table is read last (by definition id)
    result = br1.collect()
    assert result.parsed["arp"].tier == "netconf"
    assert all(result.parsed[cap].tier == "netconf" for cap in POLICY_CAPS)
    assert result.parsed["routes"].netconf["reason"] == "session:timeout"
    assert result.parsed["routes"].tier == "rules"


def test_without_a_netconf_grant_the_cli_serves(br1):
    result = br1.collect(grants=br1.cli_only_grants)
    assert result.parsed["arp"].netconf["reason"] == "session:not_authorized"
    assert br1.sessions == 0 and result.parsed["arp"].tier == "rules"


def test_a_module_the_device_does_not_announce_is_not_read(br1):
    br1.hello = tuple(c for c in br1.hello if "module=ARP&" not in c)
    result = br1.collect()
    assert result.parsed["arp"].netconf["reason"] == "module_not_announced"
    assert result.parsed["arp"].tier == "rules" and result.parsed["routes"].tier == "netconf"
    assert not any("<ARP>" in subtree for subtree in br1.asked)


def test_a_route_table_on_a_device_with_vrfs_stays_on_the_cli(br1):
    vsr = ROOT / "tests/fixtures/topology/h3c/vsr1000-7.1.064/vsr-1/vrfs.raw.txt"
    br1.outputs["display ip vpn-instance"] = vsr.read_text(encoding="utf-8")
    result = br1.collect()
    assert result.parsed["routes"].netconf["reason"] == "per_vrf_unverified"
    assert result.parsed["routes"].tier in {"rules", "generic"}
    assert result.parsed["arp"].tier == "netconf"  # one table with every instance


def test_off_never_opens_netconf(br1):
    result = br1.collect("off")
    assert br1.sessions == 0
    assert not result.parsed["arp"].netconf and result.parsed["arp"].tier == "rules"


@pytest.fixture
def database():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    CollectionIssue.__table__.create(engine)

    @contextmanager
    def sessions():
        with Session(engine) as session, session.begin():
            yield session

    yield sessions
    engine.dispose()


def keep(sessions, data, reason=None, run="run-1"):
    with sessions() as session:
        return issues.record(
            session,
            owner="alice",
            network_id="net",
            run_id=run,
            observation_id="obs",
            address="10.10.1.11",
            data=data,
            reason=reason,
        )


def open_issues(sessions):
    with sessions() as session:
        return issues.listing(session, "alice")


def test_issues_name_the_device_table_transport_and_what_to_fix(br1, database, caplog):
    route = (NETCONF / "route.xml").read_text("utf-8")
    br1.replies["Route"] = route.replace("<ProtocolID>2</ProtocolID>", "<ProtocolID>7</ProtocolID>")
    br1.outputs["display arp verbose"] += "\nan arp line the template does not know\n"
    data = topology_observation(br1.collect())
    with caplog.at_level("WARNING", logger=issues.__name__):
        keep(database, data)
    found = {(i["capability"], i["transport"], i["kind"]): i for i in open_issues(database)}
    assert set(found) == {("routes", "netconf", "problems")}  # ARP was read over NETCONF
    detail = found["routes", "netconf", "problems"]["detail"]
    assert detail["definition"] == "h3c-comware7.routes@v1"
    assert (
        detail["problems"][0]["raw"] == "7/1"
        and found["routes", "netconf", "problems"]["device"] == "BR1"
    )
    assert "10.10.1.11 (BR1) routes over netconf: problems" in caplog.text
    # Over the CLI (NETCONF off), the damaged ARP output is the issue.
    data = topology_observation(br1.collect("off"))
    keep(database, data, run="run-2")
    kinds = {(i["capability"], i["transport"], i["kind"]) for i in open_issues(database)}
    assert ("arp", "ssh", "partial") in kinds
    arp = next(i for i in open_issues(database) if i["capability"] == "arp")
    assert any("template_rejected" in warning for warning in arp["detail"]["warnings"])


def test_an_issue_is_counted_and_cleared_by_a_clean_collection(br1, database):
    br1.refuse = "authentication_failed"
    failed = topology_observation(br1.collect())
    keep(database, failed)
    keep(database, failed, run="run-2")
    [arp] = [i for i in open_issues(database) if i["capability"] == "arp"]
    assert (arp["kind"], arp["count"], arp["last_run_id"]) == (
        "session:authentication_failed",
        2,
        "run-2",
    )
    br1.refuse = None
    keep(database, topology_observation(br1.collect()), run="run-3")
    assert open_issues(database) == []
    with database() as session:
        cleared = issues.listing(session, "alice", include_cleared=True)
    assert {i["capability"] for i in cleared} == {"arp", "routes", *POLICY_CAPS}
    assert all(i["cleared_at"] for i in cleared)


def test_a_failed_node_is_an_issue_and_unsupported_commands_are_not(database):
    data = {
        "capabilities": {"mac": "unsupported", "evpn_mac": "failed"},
        "fact_metadata": {"evpn_mac": {"tier": "rules", "warnings": ["unparsed_output"]}},
        "platform": {"profile_id": "cisco-ios"},
    }
    keep(database, data, reason="parse_incomplete")
    kinds = {(i["capability"], i["kind"]) for i in open_issues(database)}
    assert kinds == {("node", "parse_incomplete"), ("evpn_mac", "failed")}


def test_standard_tables_are_read_in_the_same_session_and_reach_the_route_table(
    br1, standard_tables
):
    """NETCONF → standard records (netconf-api) → RFC 8349 tree → route query table."""
    from src.apps.topology.backend.model.route_table import route_tables
    from src.apps.topology.backend.service import _facts

    result = br1.collect()
    assert br1.sessions == 1  # one session for the definitions and the standard tables
    read = result.standard
    assert read["mapping"].startswith("h3c_comware7@") and read["failed"] == {}
    counts = {name: len(rows) for name, rows in read["records"].items()}
    expected = {"interface": 9, "ipv4-address": 7, "route": 50, "ospf-neighbor": 3}
    assert expected.items() <= counts.items() and counts["lldp-neighbor"] == 3
    data = topology_observation(result)
    facts = _facts(data)
    table = route_tables(facts)
    # The builder's rows come from the same trees: LLDP with its management addresses.
    assert {(row["local_if"], tuple(row["remote_mgmt_addresses"])) for row in facts["lldp"]} == {
        ("GigabitEthernet3/0", ("10.0.13.2",)),
        ("GigabitEthernet4/0", ("10.0.14.2",)),
        ("GigabitEthernet5/0", ("10.0.12.2",)),
    }
    assert len(facts["ip"]) == 6 and "age_s" not in facts["arp"][0]
    rows = {(r["rib"], r["destination-prefix"]): r for r in table["routes"]}
    # OSPFv2, known from NETCONF (the CLI gives only "O_INTRA").
    assert rows[("ipv4", "10.255.0.3/32")]["source-protocol"] == "ietf-ospf:ospfv2"
    assert ("ipv6", "::1/128") in rows
    assert any(e.command_id.startswith("netconf-standard-") for e in br1.evidence)


def test_a_table_the_device_refuses_is_failed_and_the_rest_still_read(br1, standard_tables):
    br1.refused = "LLDP"
    result = br1.collect()
    assert set(result.standard["failed"]) == {"lldp-neighbor", "lldp-management-address"}
    assert "lldp-neighbor" not in result.standard["records"]  # not collected, not empty
    assert len(result.standard["records"]["route"]) == 50

    br1.fail_on = "OSPF"  # a session lost midway: the standard read stops, the CLI serves
    result = br1.collect()
    assert result.standard["failed"].startswith("session:")


def test_acl_and_pbr_from_the_standard_records_are_the_cli_facts(br1):
    """BR1's ACL entries, packet filters and policy routes through the conversion layer
    against the facts its CLI gives at the same moment (br1-policy)."""
    from src.apps.netconf_api.backend import standard
    from src.apps.topology.backend.model.standard_tables import policy_rows

    seed = ROOT / "src/apps/netconf_api/backend/mappings/h3c_comware7.yaml"
    mapping = standard.load(yaml.safe_load(seed.read_text(encoding="utf-8")))
    texts = [p.read_text(encoding="utf-8") for p in sorted(POLICY.glob("*.xml"))]
    texts.append((NETCONF / "ifmgr-interfaces.xml").read_text(encoding="utf-8"))
    records, problems = standard.convert(mapping, texts)
    assert problems == []
    rows = policy_rows(records)
    cli = topology_observation(br1.collect(netconf="off"))["facts"]

    def same(key):
        def text(row):
            # An unknown condition is named by each reading in its own words.
            row = dict(row)
            if "unsupported" in row:
                row["unsupported"] = [u.split(":")[0] for u in row["unsupported"]]
            return json.dumps(row, sort_keys=True, default=str)

        return sorted(map(text, cli[key])) == sorted(map(text, rows[key]))

    assert all(same(key) for key in ("acl_rules", "acl_bindings", "pbr_rules", "pbr_bindings"))
    dscp = next(r for r in rows["acl_rules"] if r["acl"] == "3990" and r["seq"] == 50)
    assert dscp["unsupported"] == ["condition:DSCP=46"]  # the CLI: "dscp ef"

"""Installed profile validation and bounded collection through the public SSH service."""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest
import yaml

from src.apps.topology.backend.collectors import collect as runtime
from src.apps.topology.backend.collectors.identify import Probe, identify
from src.apps.topology.backend.collectors.profiles import (
    DIRECTORY,
    Profile,
    load_profiles,
    select_profile,
    verified_driver,
)
from src.platform.device_access.contracts import (
    AccessContext,
    AccessGrant,
    ConnectionProfile,
    DeviceAccessError,
    Target,
)
from src.platform.device_access.drivers.netmiko_driver import NetmikoDriver
from src.platform.device_access.errors import CommandExecutionError
from src.platform.device_access.service import DeviceAccessService

LAB = Path(__file__).parents[1] / "fixtures/topology/h3c/vsr1000-7.1.064/vsr-1"
# Capabilities the first lab never captured (VRRP) come from the same Comware release there.
CAMPUS_BR1 = Path(__file__).parents[1] / "fixtures/topology/campus-wan/br1"
VLAN_BR1 = Path(__file__).parents[1] / "fixtures/topology/campus-wan/vlan-trunk/br1"


def identity(text=None):
    return identify([Probe("display version", text or (LAB / "version.txt").read_text())])


def test_profiles_modes_identity_and_verification():
    profiles = load_profiles()
    assert set(profiles) == {"h3c-comware7", "cisco-nxos", "cisco-ios"}
    profile = select_profile(identity(), profiles)
    assert [len(profile.selected(mode)) for mode in ("fast", "standard", "full")] == [4, 11, 14]
    assert profile.verified[0].unsupported == ["inventory", "irf"]
    assert profiles["cisco-ios"].verified == [] and profiles["cisco-nxos"].verified == []
    iosxe = identify([Probe("show version", "Cisco IOS XE Software, Version 17.9")])
    assert select_profile(iosxe, profiles).id == "cisco-ios"
    with pytest.raises(DeviceAccessError, match="档案"):
        select_profile(identity("H3C Comware Software, Version 5.2"), profiles)
    with pytest.raises(DeviceAccessError):
        select_profile(identify([]), profiles)
    with pytest.raises(ValueError):
        profile.selected("everything")


@pytest.mark.parametrize(
    "command",
    [
        "reboot",
        "display version; reboot",
        "display version\nreboot",
        "display version | save x",
        "configure terminal",
        "ping 192.0.2.1",
        "screen-length disable",
        "dis arp",
    ],
)
def test_non_collection_or_noncanonical_commands_rejected(command):
    data = load_profiles()["h3c-comware7"].model_dump()
    data["capabilities"]["arp"]["command"] = command
    with pytest.raises(ValueError):
        Profile.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("parser", "generic_mac"),
        ("parser", "os.system"),
        ("timeout", 0),
        ("timeout", 301),
        ("timeout", True),
        ("command", "show clock"),
    ],
)
def test_bad_version_contract(field, value):
    data = load_profiles()["h3c-comware7"].model_dump()
    data["capabilities"]["version"][field] = value
    with pytest.raises(ValueError):
        Profile.model_validate(data)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["session"].update(paging_off=["screen-length disable", "save"]),
        lambda d: d["session"].update(encoding="unicode-escape"),
        lambda d: d["session"].update(max_output_bytes=1_000_000_000),
        lambda d: d["budgets"].update(full=601),
        lambda d: d.update(identity_profiles=["huawei-vrp"]),
        lambda d: d["capabilities"].update(secret={"command": "display secret"}),
        lambda d: d["capabilities"]["mac"].update(command="display arp"),
        lambda d: d["verified"][0].update(parsed=["inventory"]),
    ],
)
def test_invalid_manifest_fields_rejected(mutation):
    data = load_profiles()["h3c-comware7"].model_dump()
    mutation(data)
    with pytest.raises(ValueError):
        Profile.model_validate(data)


@pytest.mark.parametrize(
    "bad", ["id: a\nid: b", "x: &x [a]\ny: *x", "x" * 65537], ids=["duplicate", "alias", "oversize"]
)
def test_bad_yaml_rejected_before_use(tmp_path, bad):
    (tmp_path / "h3c-comware7.yaml").write_text(bad)
    with pytest.raises(ValueError):
        load_profiles(tmp_path)


def test_no_profiles_and_mismatched_filename(tmp_path):
    with pytest.raises(ValueError):
        load_profiles(tmp_path)
    (tmp_path / "wrong.yaml").write_text((DIRECTORY / "h3c-comware7.yaml").read_text())
    with pytest.raises(ValueError):
        load_profiles(tmp_path)


def test_driver_qualification_is_model_and_release_specific():
    profile = load_profiles()["h3c-comware7"]
    assert (
        verified_driver(profile, {"model": "VSR1000", "os_version": "7.1.064, Release 1362P12"})
        == "netmiko"
    )
    assert (
        verified_driver(profile, {"model": "S6850", "os_version": "7.1.064, Release 1362P12"})
        == "paramiko"
    )
    assert verified_driver(profile, {"model": "VSR1000", "os_version": "7.1.070"}) == "paramiko"
    assert verified_driver(profile, {}) == "paramiko"


class Transport:
    version = "test"
    host_key_verifier = object()

    def __init__(self, trace, name="paramiko"):
        self.trace, self.name = trace, name

    def connect(self, target, profile, interactive=False):
        self.trace.connections.append((self.name, profile))
        return self

    def send_command(self, command, read_timeout):
        self.trace.commands.append((self.name, command, read_timeout))
        if self.trace.on_command:
            self.trace.on_command(command)
        value = self.trace.outputs.get(command, "No data fixture")
        if isinstance(value, Exception):
            raise value
        return value

    def disconnect(self):
        self.trace.closes += 1

    @contextmanager
    def hold(self, target, profile):
        yield lambda: None


@pytest.fixture
def setup(monkeypatch):
    profiles = load_profiles()
    mapping = {"lldp": "lldp_verbose", "ip": "ip_interfaces"}
    outputs = {
        spec.command: next(
            path
            for path in (
                LAB / f"{mapping.get(cap, cap)}.txt",
                CAMPUS_BR1 / f"{cap}.txt",
                VLAN_BR1 / f"{cap}.txt",
            )
            if path.exists()
        ).read_text()
        for cap, spec in profiles["h3c-comware7"].capabilities.items()
    }
    trace = SimpleNamespace(
        outputs=outputs,
        commands=[],
        connections=[],
        closes=0,
        on_command=None,
    )
    transport = Transport(trace)

    def provider(target, port):
        return "fixture-known-hosts"

    def netmiko(platform, *, known_hosts_provider=None):
        assert platform == "comware"
        assert known_hosts_provider is provider
        return Transport(trace, "netmiko")

    monkeypatch.setattr(
        runtime, "NetmikoDriver", create_autospec(NetmikoDriver, side_effect=netmiko)
    )
    access = DeviceAccessService(
        target_resolver=lambda _: Target("node", "192.0.2.1", management_domain="lab"),
        profile_resolver=lambda _: ConnectionProfile("connection", "lab", "unit-secret"),
        driver=transport,
        admission=transport,
    )
    context = AccessContext(
        "actor",
        "topology",
        (AccessGrant("node", "connection", runtime.command_allowlist(profiles)),),
    )
    evidence = []

    def save(result):
        evidence.append(result)
        return "evidence/" + result.command_id

    def collect(**kwargs):
        return runtime.collect_node(
            access,
            context,
            "node",
            "connection",
            profiles=profiles,
            should_continue=lambda: True,
            on_result=save,
            known_hosts_provider=provider,
            **kwargs,
        )

    return SimpleNamespace(
        trace=trace,
        collect=collect,
        profiles=profiles,
        access=access,
        context=context,
        evidence=evidence,
    )


@pytest.mark.parametrize("mode,count", [("fast", 4), ("standard", 11), ("full", 14)])
def test_real_fixtures_through_public_batch_pipeline(setup, mode, count):
    result = setup.collect(mode=mode)
    trace = setup.trace
    assert result.profile_id == "h3c-comware7" and result.driver == "netmiko"
    assert len(result.results) == count
    assert len(trace.commands) == count  # No repeated version command, no show fallback.
    assert [profile.username for _, profile in trace.connections] == ["lab", "lab"]
    assert trace.closes == 2 and len(trace.connections) == 2
    assert trace.connections[0][1].platform is None
    assert trace.connections[1][1].platform == "comware"
    assert result.results["version"].evidence_ref == "evidence/detect-display"
    assert result.parsed["lldp"].status == "complete"
    if mode != "fast":
        assert result.parsed["inventory"].status == "unsupported"
        assert result.parsed["arp"].status == "complete"
    if mode == "full":
        assert result.parsed["irf"].status == "unsupported"
        assert result.parsed["vrfs"].status == "complete"
        assert result.parsed["routes"].status == "complete"
        assert result.parsed["routes"].tier == "template"
        assert result.missing["vxlan"] == "no_profile_command"


def test_unverified_model_stays_paramiko_with_the_same_login(setup):
    setup.trace.outputs["display version"] = (
        "H3C Comware Software, Version 7.1.064\nH3C S6850 uptime is 1 day"
    )
    result = setup.collect(mode="fast")
    assert result.driver == "paramiko"
    assert [p.password for _, p in setup.trace.connections] == ["unit-secret", "unit-secret"]


@pytest.mark.parametrize(
    "version",
    [
        "Unknown device",
        "H3C Comware Software, Version 5.2",
        "Ruijie Operating System, Version 11.0",
    ],
)
def test_unknown_or_uninstalled_profile_stops_for_human(setup, version):
    setup.trace.outputs["display version"] = version
    setup.trace.outputs["show version"] = "% Invalid input"
    with pytest.raises(DeviceAccessError):
        setup.collect()
    assert len(setup.trace.connections) == 1
    assert not any("lldp" in c[1] for c in setup.trace.commands)


def test_show_fallback_uses_own_profile_and_reports_missing_local_identity(setup):
    setup.trace.outputs["display version"] = "% Unrecognized command"
    setup.trace.outputs["show version"] = "Cisco IOS XE Software, Version 17.9"
    result = setup.collect(mode="fast")
    assert result.profile_id == "cisco-ios"
    assert result.missing["lldp_local"] == "no_profile_command"
    assert [c[1] for c in setup.trace.commands] == [
        "display version",
        "show version",
        "show lldp neighbors detail",
        "show interfaces description",
    ]
    assert setup.trace.connections[1][1].platform == "ios"


def test_probe_transport_failure_stops_before_reconnect(setup):
    setup.trace.outputs["display version"] = CommandExecutionError("timeout", "test", "partial")
    with pytest.raises(DeviceAccessError):
        setup.collect()
    assert len(setup.trace.connections) == 1


def test_confirmed_version_alias_does_not_require_a_duplicate_probe(setup):
    setup.trace.outputs["display version"] = "Cisco IOS Software, Version 15.2"
    result = setup.collect(mode="fast")
    assert result.profile_id == "cisco-ios"
    assert result.results["version"].command_id == "detect-display"
    assert "show version" not in [c[1] for c in setup.trace.commands]


def test_budget_includes_probe_and_reconnect(monkeypatch, setup):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock.now)
    setup.trace.on_command = lambda command: (
        setattr(clock, "now", 110.0) if command == "display version" else None
    )
    setup.collect(mode="fast", budget_seconds=12)
    assert all(
        timeout <= 2 for _, command, timeout in setup.trace.commands if command != "display version"
    )
    assert setup.trace.connections[1][1].session_setup_timeout <= 2


def test_exhausted_budget_does_not_reconnect(monkeypatch, setup):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock.now)
    setup.trace.on_command = lambda command: setattr(clock, "now", 120.0)
    with pytest.raises(DeviceAccessError) as exc:
        setup.collect(mode="fast", budget_seconds=10)
    assert exc.value.failure_code == "budget_exceeded"
    assert len(setup.trace.connections) == 1


def test_auth_required_before_profile_resolves(setup):
    setup.access.profile_resolver = lambda _: pytest.fail("unauthorized resolution")
    with pytest.raises(DeviceAccessError):
        runtime.collect_node(setup.access, AccessContext("actor", "topology"), "node", "connection")
    assert not setup.trace.connections


@pytest.mark.parametrize("budget", [0, -1, 601, float("inf"), True])
def test_invalid_budget_is_rejected_without_ssh(setup, budget):
    with pytest.raises(ValueError):
        setup.collect(budget_seconds=budget)
    assert not setup.trace.connections


def test_invalid_yaml_command_file_is_never_loaded(tmp_path):
    data = load_profiles()["cisco-ios"].model_dump()
    data["capabilities"]["interfaces"]["command"] = "reload"
    (tmp_path / "cisco-ios.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError):
        load_profiles(tmp_path)


def test_db_host_verifier_is_preserved_without_export_provider(setup):
    result = runtime.collect_node(
        setup.access, setup.context, "node", "connection", mode="fast", should_continue=lambda: True
    )
    assert result.driver == "paramiko"
    assert result.driver_warning == "netmiko_requires_matching_known_hosts_provider"
    assert [name for name, _ in setup.trace.connections] == ["paramiko", "paramiko"]


def test_revoked_scope_stops_before_reconnect(setup):
    def authorized():
        return len(setup.trace.commands) == 0

    with pytest.raises(DeviceAccessError) as exc:
        runtime.collect_node(
            setup.access,
            setup.context,
            "node",
            "connection",
            mode="fast",
            should_continue=authorized,
        )
    assert exc.value.failure_code == "cancelled"
    assert len(setup.trace.connections) == 1


def test_reconnect_failure_is_not_retried_with_another_login(setup, monkeypatch):
    original = Transport.connect

    def fail_identified(self, target, profile, interactive=False):
        if profile.platform:
            raise DeviceAccessError("auth_failed", "fixture authentication rejected")
        return original(self, target, profile, interactive=interactive)

    monkeypatch.setattr(Transport, "connect", fail_identified)
    with pytest.raises(DeviceAccessError):
        setup.collect(mode="fast")
    assert [profile.platform for _, profile in setup.trace.connections] == [None]

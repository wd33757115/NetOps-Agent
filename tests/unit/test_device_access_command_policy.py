"""P1-3 command matrix (policy tests, not device-version interoperability claims).

Command                              Level
dis version / sh version             L1 (canonical display / show)
display/show ... | include/begin/count L1 (filter argument case preserved)
ping <host>                          L2 (explicit count, size, timeout injected)
tracert <host> (Comware/VRP)          L2 (per-hop probes, timeout and hop cap)
traceroute <host> (IOS/NX-OS)         L3 (no verified portable bounded syntax)
display diagnostic-information       L3
show tech-support / debug            L3
terminal monitor / unknown command   L3
conf t / sys / reset / wr / delete   L4
redirection / shell / multiline      L4
out-of-range or duplicate probe args L4
wrong/missing required VRF           L4

Each family receives >= 40 positive/negative cases below. No SSH is performed.
"""

import socket
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml

from src.platform.device_access import command_policy as policy

FAMILIES = ["comware", "vrp", "ios", "nxos"]


def query(family):
    return "display" if family in {"comware", "vrp"} else "show"


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize(
    "suffix",
    [
        "version",
        "interface",
        "interface Ethernet1/1",
        "arp",
        "lldp neighbors",
        "ip route",
        "vlan",
        "clock",
        "logging",
        "running-config",
        "version | include VERSION",
        "version | begin Uptime",
        "interface | count",
        "interface | include DOWN | count",
        "version | include reboot",
    ],
)
def test_read_queries(family, suffix):
    assert policy.classify(family, f"{query(family)} {suffix}").level == "L1"


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize(
    "command",
    [
        "system-view",
        "sys",
        "configure terminal",
        "conf t",
        "config t",
        "reset counters",
        "reboot",
        "reb",
        "reload",
        "rel",
        "save",
        "sa",
        "write memory",
        "wr",
        "delete flash:config",
        "del file",
        "format flash:",
        "copy running-config startup-config",
        "rename old new",
        "mkdir test",
        "undo shutdown",
        "shutdown",
        "clear counters",
        "erase startup-config",
        "no shutdown",
        "do reload",
        "bash",
        "run bash",
        "python",
        "tclsh",
        "guestshell run bash",
    ],
)
def test_mutations_and_abbreviations_never_reach_approval(family, command):
    assert policy.classify(family, command).level == "L4"


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize(
    "tail",
    [
        "; reboot",
        "\nreboot",
        "\rreload",
        "\r\nreset",
        "\x00",
        "\x1b[A",
        "\x08",
        "\u200b",
        "\u2028reboot",
        "\u2029reload",
        "\x85save",
        "\vsave",
        "\fsave",
        " > file",
        " >> file",
        " < input",
        " && reload",
        " & reload",
        " `reload`",
        " $(reload)",
        " | redirect file",
        " | red file",
        " | tee file",
        " | append file",
        " || reload",
        " | bash",
        " |",
        " | count extra",
        " | include",
        " | begin",
    ],
)
def test_injection_redirection_and_invalid_filters(family, tail):
    assert policy.classify(family, f"{query(family)} version{tail}").level == "L4"


@pytest.mark.parametrize("family", FAMILIES)
def test_normalization_keeps_argument_case_and_is_idempotent(family):
    alias = "DIS" if family in {"comware", "vrp"} else "SH"
    decision = policy.classify(
        family, f"  {alias}\tinterface\u00a0Eth1/1\u202f | INCLUDE\tCaseSensitive "
    )
    assert decision == policy.Decision(
        "L1", "read_only_query", f"{query(family)} interface Eth1/1 | include CaseSensitive"
    )
    assert policy.classify(family, decision.normalized) == decision


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize(
    "command",
    [
        "debug all",
        "debugging all",
        "terminal monitor",
        "enable",
        "some-unknown-command",
        "ping",
        "show",
        "display",
        "show version ?",
    ],
)
def test_unknown_interactive_and_approval_commands(family, command):
    assert policy.classify(family, command).level == "L3"


@pytest.mark.parametrize(
    "family,command",
    [
        ("comware", "dis diagnostic-information"),
        ("comware", "dis diag"),
        ("vrp", "display diag"),
        ("vrp", "display diagnostic-information"),
        ("ios", "sh tech-support"),
        ("ios", "sh te"),
        ("nxos", "SHOW TECH-SUPPORT"),
        ("nxos", "sh tech"),
    ],
)
def test_expensive_queries_and_abbreviations_need_approval(family, command):
    assert policy.classify(family, command).level == "L3"


@pytest.mark.parametrize(
    "family,command",
    [
        ("comware", "dis diag diagnostic.txt"),
        ("vrp", "display diagnostic-information flash:/out"),
        ("ios", "sh tech-support file output"),
        ("nxos", "sh te bootflash:output"),
    ],
)
def test_diagnostic_file_output_cannot_be_approved(family, command):
    assert policy.classify(family, command).level == "L4"


# Explicit independently specified device dialect expectations.
PING = [
    ("comware", "ping -c 3 -s 56 -t 2000 10.0.0.1", "-c", "-s", "-t", "before", 2000),
    ("vrp", "ping -c 3 -s 56 -t 2000 10.0.0.1", "-c", "-s", "-t", "before", 2000),
    ("ios", "ping 10.0.0.1 repeat 3 size 56 timeout 2", "repeat", "size", "timeout", "after", 2),
    (
        "nxos",
        "ping 10.0.0.1 count 3 packet-size 56 timeout 2",
        "count",
        "packet-size",
        "timeout",
        "after",
        2,
    ),
]


def probe_command(layout, options):
    return f"ping {options} 10.0.0.1" if layout == "before" else f"ping 10.0.0.1 {options}"


@pytest.mark.parametrize("family,expected,count,size,timeout,layout,limit", PING)
def test_probe_defaults_are_explicit_and_stable(
    family, expected, count, size, timeout, layout, limit
):
    decision = policy.classify(family, "ping 10.0.0.1")
    assert decision == policy.Decision("L2", "bounded_probe", expected)
    assert policy.classify(family, expected) == decision


@pytest.mark.parametrize("family,expected,count,size,timeout,layout,limit", PING)
@pytest.mark.parametrize(
    "case",
    [
        "max",
        "count-high",
        "count-zero",
        "size-high",
        "timeout-high",
        "negative",
        "float",
        "infinite",
        "duplicate",
        "missing",
        "unknown",
    ],
)
def test_probe_parameter_limits(family, expected, count, size, timeout, layout, limit, case):
    opts, level = {
        "max": (f"{count} 5 {size} 1500 {timeout} {limit}", "L2"),
        "count-high": (f"{count} 6", "L4"),
        "count-zero": (f"{count} 0", "L4"),
        "size-high": (f"{size} 1501", "L4"),
        "timeout-high": (f"{timeout} {limit + 1}", "L4"),
        "negative": (f"{count} -1", "L4"),
        "float": (f"{count} 1.0", "L4"),
        "infinite": (f"{count} unlimited", "L4"),
        "duplicate": (f"{count} 1 {count} 5", "L4"),
        "missing": (f"{count}", "L4"),
        "unknown": ("unknown-option 5", "L3"),
    }[case]
    assert policy.classify(family, probe_command(layout, opts)).level == level


@pytest.mark.parametrize(
    "family,command,normalized",
    [
        (
            "comware",
            "ping -vpn-instance Blue -a 10.0.0.2 10.0.0.1",
            "ping -c 3 -s 56 -t 2000 -a 10.0.0.2 -vpn-instance Blue 10.0.0.1",
        ),
        (
            "vrp",
            "ping -vpn-instance Blue 10.0.0.1",
            "ping -c 3 -s 56 -t 2000 -vpn-instance Blue 10.0.0.1",
        ),
        ("ios", "ping vrf Blue ip 10.0.0.1", "ping vrf Blue 10.0.0.1 repeat 3 size 56 timeout 2"),
        (
            "nxos",
            "ping 10.0.0.1 vrf Blue",
            "ping 10.0.0.1 count 3 packet-size 56 timeout 2 vrf Blue",
        ),
    ],
)
def test_required_vrf_is_explicit_case_sensitive_and_never_inferred(family, command, normalized):
    assert policy.classify(family, command, required_vrf="Blue") == policy.Decision(
        "L2", "bounded_probe", normalized
    )
    assert policy.classify(family, command, required_vrf="blue").level == "L4"
    assert policy.classify(family, "ping 10.0.0.1", required_vrf="Blue").level == "L4"
    assert policy.classify(family, normalized, required_vrf="Blue").normalized == normalized


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize(
    "target",
    [
        "ipv6",
        "224.0.0.1",
        "255.255.255.255",
        "0.0.0.0",
        "999.1.1.1",
        "https://example.com",
        "-f",
        "2001:db8::1",
    ],
)
def test_unverified_target_forms_do_not_auto_execute(family, target):
    assert policy.classify(family, "ping " + target).level not in {"L0", "L1", "L2"}


@pytest.mark.parametrize("family", ["comware", "vrp"])
def test_trace_caps_and_millisecond_units(family):
    decision = policy.classify(family, "tracert 10.0.0.1")
    assert decision.level == "L2"
    assert "-q 3 -w 2000 -m 30" in decision.normalized
    for option in ["-q 6", "-w 2001", "-m 31", "-m 0", "-q 2 -q 3"]:
        assert policy.classify(family, f"tracert {option} 10.0.0.1").level == "L4"
    assert policy.classify(family, "tracert -q 5 -w 1 -m 1 10.0.0.1").level == "L2"


@pytest.mark.parametrize("family", ["ios", "nxos"])
def test_trace_without_verified_budget_grammar_requires_review(family):
    assert policy.classify(family, "traceroute 10.0.0.1").level == "L3"


@pytest.mark.parametrize("platform", [None, "", "unknown", "linux", "../../ios"])
def test_unsupported_platform_fails_closed(platform):
    assert policy.classify(platform, "show version").level == "L4"


@pytest.mark.parametrize("command", [None, "", "  ", "a" * 4097, "| count", "ｒｅｌｏａｄ"])
def test_invalid_command_fails_closed(command):
    assert policy.classify("ios", command).level == "L4"


def test_alias_platform_and_no_io_after_startup(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("classify must not perform I/O")

    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(yaml, "load", forbidden)
    for alias in ["nx-os", "NX-OS", "cisco_nxos", "cisco_ios"]:
        assert policy.classify(alias, "sh version").level == "L1"
        assert policy.classify(alias, "ping router.example.test").level == "L2"
    assert policy.classify("hp_comware", "dis version").level == "L1"
    assert policy.classify("huawei", "dis version").level == "L1"


def test_decisions_and_compiled_configuration_are_immutable():
    decision = policy.classify("ios", "show version")
    with pytest.raises(FrozenInstanceError):
        decision.level = "L0"
    with pytest.raises(TypeError):
        policy._RULES["ios"].aliases["reload"] = "show"


def test_yaml_duplicate_keys_and_invalid_limits_fail_at_load():
    with pytest.raises(ValueError, match="Duplicate"):
        yaml.load("platform: ios\nplatform: nxos", Loader=policy._UniqueLoader)
    raw = yaml.safe_load(Path("src/platform/device_access/policy_rules/ios.yaml").read_text())
    for mutate in [
        lambda d: d["probes"]["ping"]["options"]["timeout"].update(max=3),
        lambda d: d["aliases"].update(ping="show"),
        lambda d: d["probes"]["ping"]["options"].pop("repeat"),
        lambda d: d.update(readonly=["show", "dir", "configure"]),
        lambda d: d.update(unknown_field=True),
    ]:
        data = deepcopy(raw)
        mutate(data)
        with pytest.raises(ValueError):
            policy._compile(data, "ios")

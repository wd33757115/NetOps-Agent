"""Version signatures are evidence; banners, LLDP and prompt shapes remain hints."""

from pathlib import Path

import pytest

from src.apps.topology.backend.collectors.identify import Probe, TopologyDiscoveryPlan, identify
from src.platform.device_access.contracts import (
    AccessContext,
    AccessGrant,
    BatchRequest,
    ConnectionProfile,
    Target,
)
from src.platform.device_access.service import DeviceAccessService
from tests.unit.test_device_access_detection import Transport

COMWARE = "H3C Comware Platform Software, Software Version 7.1.064, Release 1362P12"
NXOS = "Cisco Nexus Operating System (NX-OS) Software"


@pytest.mark.parametrize(
    "command,version,family,style",
    [
        ("display version", COMWARE, "comware", "display"),
        ("display version", "HPE Comware Software, Version 7.1", "comware", "display"),
        (
            "display version",
            "Huawei Versatile Routing Platform Software\nVRP (R) software, Version 5.170",
            "vrp",
            "display",
        ),
        ("show version", NXOS, "nxos", "show"),
        ("show version", "Cisco IOS Software, Version 15.2", "ios", "show"),
        (
            "show version",
            "Cisco IOS XE Software, Version 17.9\nCisco IOS Software, Version 17.9",
            "iosxe",
            "show",
        ),
        (
            "show version",
            "Ruijie Operating System Software\nRGOS Software, Version 11.0",
            "ruijie",
            "show",
        ),
        ("show version", "JUNOS Software Release [22.1]", "junos", "show"),
        ("show version", "Arista DCS-7050\nSoftware image version: 4.25", "eos", "show"),
        ("show version", "Linux version 5.15.0", "linux", "unknown"),
    ],
)
def test_version_signatures_sample_only_except_comware(command, version, family, style):
    result = identify([Probe(command, version)])
    assert result.os_family == family and result.command_style == style
    assert result.confidence == "high" and result.status == "identified"
    assert result.evidence[0]["command"] == command


def test_comware5_is_not_assigned_comware7_profile():
    result = identify([Probe("display version", "Comware Software, Version 5.2")])
    assert result.os_family == "comware" and result.profile_id is None


@pytest.mark.parametrize(
    "signals",
    [
        {"lldp_description": COMWARE},
        {"ssh_banner": COMWARE},
        {"prompt": "<H3C>"},
        {"prompt": "Cisco#", "paging_style": "--More--"},
        {"lldp_description": NXOS, "ssh_banner": NXOS},
    ],
)
def test_weak_signals_do_not_confirm_neighbor_platform(signals):
    result = identify(**signals)
    assert result.status == "waiting_for_input" and result.os_family is None


def test_own_version_overrides_conflicting_neighbor_hint_with_warning():
    result = identify([Probe("display version", COMWARE)], lldp_description=NXOS)
    assert result.os_family == "comware" and "upstream_hint_disagrees" in result.warnings
    assert [row["source"] for row in result.evidence] == ["lldp", "version"]


def test_conflicting_device_version_outputs_require_human():
    result = identify([Probe("display version", COMWARE), Probe("show version", NXOS)])
    assert result.status == "ambiguous" and result.profile_id is None


@pytest.mark.parametrize(
    "probe",
    [Probe("display version", COMWARE, False), Probe("display version", COMWARE + "\n--More--")],
)
def test_partial_probe_cannot_confirm_even_if_signature_seen(probe):
    assert identify([probe]).status == "waiting_for_input"


@pytest.mark.parametrize(
    "output", ["% Invalid command", "host-name NX-OS-Comware", "", "unknown software"]
)
def test_unknown_version_does_not_inherit_lldp_choice(output):
    assert identify([Probe("display version", output)], lldp_description=COMWARE).os_family is None


@pytest.mark.parametrize(
    "command,style", [("display version", "display"), ("show version", "show")]
)
def test_unknown_vendor_valid_version_can_identify_only_cli_style(command, style):
    result = identify([Probe(command, "Example OS, Software Version 1.2.3")])
    assert result.command_style == style and result.os_family is None and result.status == "generic"


def collect(outputs):
    driver, plan = Transport(outputs), TopologyDiscoveryPlan()
    service = DeviceAccessService(
        target_resolver=lambda _: Target("target", "192.0.2.1"),
        profile_resolver=lambda _: ConnectionProfile("profile", "operator", "test"),
        driver=driver,
        admission=driver,
    )
    context = AccessContext(
        "actor",
        "topology",
        (AccessGrant("target", "profile", frozenset(c.text for c in plan.commands)),),
    )
    result = service.execute_batch(
        context, BatchRequest("target", "profile", plan.commands), command_filter=plan.select
    )
    return driver, plan, result


def test_compatibility_plan_stops_probing_after_comware_identification():
    driver, plan, result = collect({"display version": COMWARE})
    assert driver.connects == 1 and driver.sent == [
        "display version",
        "screen-length disable",
        "display lldp neighbor-information verbose",
    ]
    assert plan.metadata()["identity"]["os_family"] == "comware"
    assert result.execution_status == "succeeded"


def test_compatibility_plan_records_show_command_accurately():
    driver, plan, _ = collect({"display version": "% Invalid command", "show version": NXOS})
    assert driver.connects == 1 and plan.profile_id == "nxos-lldp-v1"
    evidence = plan.metadata()["identity"]["evidence"]
    assert [row["command"] for row in evidence] == ["display version", "show version"]


def test_two_real_lab_versions_and_upstream_descriptions_are_recognized():
    root = Path(__file__).resolve().parents[1] / "fixtures/topology/h3c/vsr1000-7.1.064"
    for device in ("vsr-1", "vsr-2"):
        result = identify(
            [Probe("display version", (root / device / "version.txt").read_text())],
            lldp_description=COMWARE,
            prompt="<H3C>",
        )
        assert result.os_family == "comware" and result.profile_id == "h3c-comware7"
        assert result.vendor == "H3C"


def test_a_new_family_is_a_row_of_data_not_code(monkeypatch):
    import re

    from src.apps.topology.backend.collectors import families

    row = {
        "id": "acme",
        "vendor": "Acme",
        "profile_id": "acme-os",
        "command_style": "display",
        "cli": "vrp",
        "pattern": r"^\s*Acme Network OS Software\b",
    }
    rows = (*families.signatures(), (row, re.compile(row["pattern"], re.I | re.M)))
    monkeypatch.setattr(families, "signatures", lambda: rows)
    result = identify([Probe("display version", "Acme Network OS Software, Version 3.1")])
    assert (result.status, result.os_family, result.vendor, result.profile_id) == (
        "identified",
        "acme",
        "Acme",
        "acme-os",
    )
    assert families.cli("acme") == "vrp" and "acme" in families.members("vrp")
    assert families.version_command("vrp") == "display version"

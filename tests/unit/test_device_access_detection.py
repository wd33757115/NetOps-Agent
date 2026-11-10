"""Platform detection uses one authorized session and never assumes neighbor type."""

from contextlib import contextmanager

import pytest

from src.platform.device_access.contracts import (
    AccessContext,
    AccessGrant,
    BatchRequest,
    ConnectionProfile,
    DeviceAccessError,
    Target,
)
from src.platform.device_access.detection import DiscoveryPlan, identify_platform
from src.platform.device_access.errors import CommandExecutionError
from src.platform.device_access.service import DeviceAccessService


class Transport:
    name, version = "fixture", "1"

    def __init__(self, outputs):
        self.outputs, self.sent, self.connects, self.closed = outputs, [], 0, False

    def connect(self, *args):
        self.connects += 1
        return self

    def send_command(self, command, read_timeout):
        self.sent.append(command)
        value = self.outputs.get(command, "")
        if isinstance(value, Exception):
            raise value
        return value

    def disconnect(self):
        self.closed = True

    @contextmanager
    def hold(self, *args):
        yield lambda: None


def collect(outputs, grant=True):
    driver = Transport(outputs)
    plan = DiscoveryPlan()
    service = DeviceAccessService(
        target_resolver=lambda _: Target("target", "192.0.2.1"),
        profile_resolver=lambda _: ConnectionProfile("profile", "operator", password="fixture"),
        driver=driver,
        admission=driver,
    )
    context = AccessContext(
        "user",
        "topology",
        (
            (AccessGrant("target", "profile", frozenset(c.text for c in plan.commands)),)
            if grant
            else ()
        ),
    )
    evidence = []
    try:
        result = service.execute_batch(
            context,
            BatchRequest("target", "profile", plan.commands),
            command_filter=plan.select,
            on_result=evidence.append,
        )
        return driver, plan, evidence, result
    except Exception as exc:
        return driver, plan, evidence, exc


@pytest.mark.parametrize(
    "version,expected",
    [
        ("H3C Comware Software, Version 7.1", "comware-lldp-v1"),
        ("Comware Software, Version 5.2", "comware-lldp-v1"),
        ("Cisco Nexus Operating System (NX-OS) Software", "nxos-lldp-v1"),
        ("Cisco IOS Software", None),
        ("Huawei Versatile Routing Platform Software", None),
        ("hostname NX-OS-Comware", None),
    ],
)
def test_platform_signatures_do_not_guess_from_vendor_or_hostname(version, expected):
    assert identify_platform(version) == expected


def test_nxos_one_login_and_only_its_lldp_commands():
    driver, plan, evidence, result = collect(
        {
            "display version": "% Invalid command",
            "show version": "Cisco Nexus Operating System (NX-OS) Software",
        }
    )
    assert driver.connects == 1 and driver.closed
    assert driver.sent == [
        "display version",
        "show version",
        "terminal length 0",
        "show lldp neighbors detail",
    ]
    assert result.execution_status == "succeeded"
    assert plan.profile_id == "nxos-lldp-v1"
    assert len(evidence) == 4


def test_comware_stops_probing_once_identified():
    driver, plan, _, result = collect({"display version": "H3C Comware Software, Version 7.1"})
    assert driver.sent == [
        "display version",
        "screen-length disable",
        "display lldp neighbor-information verbose",
    ]
    assert driver.connects == 1 and driver.closed
    assert result.execution_status == "succeeded"


@pytest.mark.parametrize(
    "output,code",
    [
        ("unknown software", "platform_unrecognized"),
        (
            "H3C Comware Software\nCisco Nexus Operating System (NX-OS) Software",
            "platform_ambiguous",
        ),
        (CommandExecutionError("timeout", "timeout", "partial"), "timeout"),
        (CommandExecutionError("output_incomplete", "paging", "--More--"), "output_incomplete"),
    ],
)
def test_unknown_ambiguous_or_partial_probe_never_sends_lldp(output, code):
    driver, _, evidence, error = collect({"display version": output})
    assert isinstance(error, DeviceAccessError) and error.failure_code == code
    assert driver.closed and driver.connects == 1
    assert not any("lldp" in text or "length" in text for text in driver.sent)
    assert evidence


def test_selector_cannot_bypass_grants():
    driver, _, _, error = collect({}, grant=False)
    assert isinstance(error, DeviceAccessError) and error.failure_code == "not_authorized"
    assert driver.connects == 0

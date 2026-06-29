from __future__ import annotations

import pytest

from src.core.device_ops.identity_discovery import (
    bootstrap_identity_commands,
    discover_device_identity,
    platform_for_identity,
)
from src.core.parser.identity import DeviceIdentity


class FakeConnection:
    def __init__(self, outputs: dict[str, str]):
        self.outputs = outputs
        self.commands: list[str] = []
        self.disconnected = False

    def find_prompt(self) -> str:
        return "<H3C>"

    def send_command(self, command: str, read_timeout: int = 30) -> str:
        self.commands.append(command)
        return self.outputs.get(command, "")

    def disconnect(self) -> None:
        self.disconnected = True


class FakeIdentityResolver:
    def __init__(self, identity: DeviceIdentity):
        self.identity = identity
        self.blocks = []
        self.vendor = None
        self.model = None

    def resolve(self, *, blocks, vendor=None, model=None):
        self.blocks = list(blocks)
        self.vendor = vendor
        self.model = model
        return self.identity


def test_bootstrap_identity_commands_uses_h3c_minimal_commands():
    assert bootstrap_identity_commands({"vendor": "H3C"}) == [
        "screen-length disable",
        "display version",
        "display device manuinfo",
    ]


def test_discover_device_identity_collects_minimal_evidence_without_profile():
    connection = FakeConnection(
        {
            "display version": "H3C S5130S-54S-HI-G uptime is 5 weeks\nH3C Comware Software",
            "display device manuinfo": "DEVICE_NAME : S5130S-54S-HI-G",
        }
    )
    resolver = FakeIdentityResolver(
        DeviceIdentity(
            vendor="H3C",
            model="S5130S-54S-HI-G",
            family="Comware7",
            confidence=0.99,
            evidence_command="display version",
            evidence_text="H3C S5130S-54S-HI-G uptime is 5 weeks",
            source="generic_fingerprint",
        )
    )

    result = discover_device_identity(
        {"device_name": "gns3-h3c-1", "vendor": "H3C"},
        connector=lambda device: connection,
        identity_resolver=resolver,
    )

    assert result.success is True
    assert result.identity_status == "known"
    assert result.identity.model == "S5130S-54S-HI-G"
    assert result.commands == [
        "screen-length disable",
        "display version",
        "display device manuinfo",
    ]
    assert [block.command for block in resolver.blocks] == result.commands
    assert resolver.vendor == "H3C"
    assert connection.commands == result.commands
    assert connection.disconnected is True


def test_discover_device_identity_returns_connection_failed():
    def failing_connector(device):
        raise RuntimeError("auth failed")

    result = discover_device_identity(
        {"device_name": "gns3-h3c-1", "vendor": "H3C"},
        connector=failing_connector,
        identity_resolver=FakeIdentityResolver(
            DeviceIdentity(vendor="H3C", model="S5130S-54S-HI-G", family="Comware7", confidence=1.0)
        ),
    )

    assert result.success is False
    assert result.identity_status == "unknown"
    assert result.failure_code == "connection_failed"
    assert result.error == "auth failed"


@pytest.mark.parametrize(
    ("identity", "current", "expected"),
    [
        (DeviceIdentity("H3C", "S5130S-54S-HI-G", "Comware7", 1.0), None, "comware"),
        (DeviceIdentity("Cisco", "N9K-C9364C", "NXOS9K", 1.0), None, "nxos"),
        (DeviceIdentity("Cisco", "WS-C2960XR-24TS-I", "IOS-C2960", 1.0), None, "ios"),
        (DeviceIdentity("H3C", "S5130S-54S-HI-G", "Comware7", 1.0), "hp_comware", "hp_comware"),
    ],
)
def test_platform_for_identity(identity, current, expected):
    assert platform_for_identity(identity, current) == expected

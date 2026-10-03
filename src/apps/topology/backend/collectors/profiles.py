"""Validate installed, server-owned collection profiles before any SSH activity."""

from pathlib import Path
from types import MappingProxyType
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.platform.device_access.catalog import TERMINAL_PREPARATION
from src.platform.device_access.command_policy import classify
from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.drivers import netmiko_driver

from .parse import PARSERS

DIRECTORY = Path(__file__).with_suffix("")
CAPABILITIES = frozenset(
    {*PARSERS, "inventory", "vlan", "lag", "routes", "vrfs", "irf", "mlag", "vxlan", "evpn"}
)
MODE_CAPABILITIES = {
    "fast": ("version", "lldp_local", "lldp", "interfaces"),
    # The facts the SIMAP L2/L3 builder consumes, plus routes for path tracing (last, so a
    # large table cannot starve the adjacency facts of the node budget).
    "topology": (
        "version",
        "lldp_local",
        "lldp",
        "interfaces",
        "ip",
        "arp",
        "mac",
        "vlan",
        "trunk",
        "switchport",
        "lag",
        "vrrp",
        "ospf_interfaces",
        "ospf_process",
        "routes",
    ),
    "standard": (
        "version",
        "lldp_local",
        "lldp",
        "interfaces",
        "inventory",
        "ip",
        "arp",
        "mac",
        "vlan",
        "trunk",
        "switchport",
        "lag",
        "ospf_interfaces",
        "ospf_process",
    ),
    "full": (
        "version",
        "lldp_local",
        "lldp",
        "interfaces",
        "inventory",
        "ip",
        "arp",
        "mac",
        "vlan",
        "trunk",
        "switchport",
        "lag",
        "ospf_interfaces",
        "ospf_process",
        "routes",
        "vrfs",
        "irf",
        "mlag",
        "vxlan",
        "evpn",
    ),
}
GENERIC_NAMES = {
    "interfaces": "generic_ifbrief",
    "ip": "generic_ipif",
    **{cap: "generic_" + cap for cap in PARSERS if cap not in {"interfaces", "ip"}},
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CapabilitySpec(StrictModel):
    command: str = Field(min_length=1, max_length=256)
    timeout: int = Field(default=30, ge=1, le=300)
    parser: str | None = (
        None  # Explicitly pending template/structured parser, not fabricated support.
    )


class SessionSpec(StrictModel):
    paging_off: list[str] = Field(min_length=1, max_length=2)
    encoding: Literal["utf-8"] = "utf-8"
    max_output_bytes: int = Field(default=4194304, ge=1024, le=8388608)


class BudgetSpec(StrictModel):
    fast: int = Field(default=120, ge=1, le=600)
    topology: int = Field(default=240, ge=1, le=600)
    standard: int = Field(default=300, ge=1, le=600)
    full: int = Field(default=600, ge=1, le=600)


class VerificationSpec(StrictModel):
    model: str
    version: str
    source: str
    collected: list[str]
    unsupported: list[str]
    parsed: list[str]


class Profile(StrictModel):
    schema_version: Literal[1]
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    vendor: str
    os_family: Literal["comware", "nxos", "ios"]
    identity_profiles: list[str] = Field(min_length=1, max_length=2)
    preferred_driver: Literal["paramiko", "netmiko"] = "paramiko"
    session: SessionSpec
    budgets: BudgetSpec = Field(default_factory=BudgetSpec)
    capabilities: dict[str, CapabilitySpec] = Field(min_length=3, max_length=20)
    verified: list[VerificationSpec]

    @model_validator(mode="after")
    def safe(self):
        if set(self.capabilities) - CAPABILITIES:
            raise ValueError("unknown capability")
        if {"version", "lldp", "interfaces"} - self.capabilities.keys():
            raise ValueError("fast discovery capabilities are required")
        if tuple(self.session.paging_off) != TERMINAL_PREPARATION[self.os_family]:
            raise ValueError("initialization must exactly match the public driver catalog")
        expected = {
            "comware": ["h3c-comware7"],
            "nxos": ["cisco-nxos"],
            "ios": ["cisco-ios", "cisco-iosxe"],
        }[self.os_family]
        if self.id != expected[0] or self.identity_profiles != expected:
            raise ValueError("profile identity must match installed version signatures")
        version_command = "display version" if self.os_family == "comware" else "show version"
        if self.capabilities["version"].command != version_command:
            raise ValueError("profile version command must match the identification probe")
        seen = set()
        for cap, spec in self.capabilities.items():
            decision = classify(self.os_family, spec.command)
            if (
                decision.level != "L1"
                or decision.normalized != spec.command
                or spec.command in seen
            ):
                raise ValueError("profile requires distinct normalized read-only commands")
            allowed = {GENERIC_NAMES.get(cap)}
            if cap in {"routes", "vlan"} and self.os_family == "comware":
                allowed.add("template_" + cap)
            if cap == "routes" and self.os_family == "ios":
                allowed.add("template_routes")
            if spec.parser is not None and spec.parser not in allowed:
                raise ValueError("unknown or mismatched parser reference")
            seen.add(spec.command)
        for evidence in self.verified:
            if (
                set(evidence.collected) | set(evidence.unsupported) | set(evidence.parsed)
            ) - self.capabilities.keys():
                raise ValueError("verification references absent capability")
            if set(evidence.parsed) - set(evidence.collected) or set(evidence.collected) & set(
                evidence.unsupported
            ):
                raise ValueError("contradictory verification")
        return self

    def selected(self, mode):
        if mode not in MODE_CAPABILITIES:
            raise ValueError("unknown collection mode")
        return {
            cap: self.capabilities[cap]
            for cap in MODE_CAPABILITIES[mode]
            if cap in self.capabilities
        }


class UniqueLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ValueError("profile YAML aliases are not allowed")
        return super().compose_node(parent, index)


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ValueError("invalid or duplicate profile key")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def load_profiles(directory=DIRECTORY):
    profiles = {}
    for path in sorted(Path(directory).glob("*.yaml")):
        if path.stat().st_size > 65536:
            raise ValueError("profile file exceeds size budget")
        profile = Profile.model_validate(yaml.load(path.read_text("utf-8"), Loader=UniqueLoader))
        if path.stem != profile.id or profile.id in profiles:
            raise ValueError("duplicate or mismatched profile filename")
        profiles[profile.id] = profile
    if not profiles:
        raise ValueError("no installed collection profiles")
    return MappingProxyType(profiles)


def select_profile(identity, profiles):
    if identity.status != "identified":
        raise DeviceAccessError("platform_unrecognized", "需要完整的设备版本证据")
    matches = [p for p in profiles.values() if identity.profile_id in p.identity_profiles]
    if len(matches) != 1:
        raise DeviceAccessError("platform_unsupported", "此节点尚无已安装的采集档案")
    profile = matches[0]
    if identity.os_family not in (
        {"ios", "iosxe"} if profile.os_family == "ios" else {profile.os_family}
    ):
        raise DeviceAccessError("platform_ambiguous", "平台身份与档案冲突")
    return profile


def verified_driver(profile, version_summary):
    """Never promote an entire family from a single model's driver smoke test."""
    if profile.preferred_driver != "netmiko":
        return "paramiko"
    path = Path(netmiko_driver.__file__).with_name("verification.yaml")
    evidence = yaml.safe_load(path.read_text("utf-8"))["verified"]

    def normalize(value):
        return " ".join(str(value).replace(",", " ").split()).casefold()

    for row in evidence:
        if (
            row["platform"] == profile.os_family
            and row["model"] == version_summary.get("model")
            and normalize(row["os_version"]) == normalize(version_summary.get("os_version"))
            and "netmiko" in row["drivers"]
        ):
            return "netmiko"
    return "paramiko"

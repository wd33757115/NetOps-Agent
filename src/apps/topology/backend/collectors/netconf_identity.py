"""NETCONF hello and standard platform data used before any CLI probe."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs

import yaml
from defusedxml import ElementTree

from .identify import Identity
from src.platform.device_access.contracts import DeviceAccessError

IETF_HARDWARE = "urn:ietf:params:xml:ns:yang:ietf-hardware"
OC_PLATFORM = "http://openconfig.net/yang/platform"


@dataclass(frozen=True)
class PlatformEvidence:
    identity: Identity | None
    vendor: str | None
    model: str | None
    software: str | None
    source: str
    reason: str | None = None
    reply: str = ""


def announced_modules(capabilities):
    """{module: revision} from RFC 6020 module capabilities in <hello>."""
    found = {}
    for capability in capabilities:
        query = parse_qs(capability.partition("?")[2])
        for module in query.get("module", []):
            found[module] = (query.get("revision", [None])[0])
    return found


def _text(element, *names):
    wanted = set(names)
    return next(
        ((child.text or "").strip() for child in element.iter() if child.tag.rpartition("}")[2] in wanted and (child.text or "").strip()),
        None,
    )


def identity_from_platform(vendor, model, software, *, source):
    """Match generic platform inventory to an installed identity rule."""
    raw = yaml.safe_load(Path(__file__).with_name("netconf_identity.yaml").read_text("utf-8"))
    if raw.get("schema_version") != 1:
        raise ValueError("unknown NETCONF identity schema")
    for row in raw.get("identities", []):
        if (
            vendor
            and model
            and software
            and re.fullmatch(row["vendor"], vendor, re.I)
            and re.fullmatch(row["model"], model, re.I)
            and re.fullmatch(row["software"], software, re.I)
        ):
            return Identity(
                vendor=row["vendor"],
                model=model,
                os_family=row["os_family"],
                profile_id=row["profile_id"],
                command_style=row["command_style"],
                confidence="high",
                status="identified",
                evidence=(
                    {
                        "source": source,
                        "vendor": vendor,
                        "model": model,
                        "software": software,
                        "rule": f"netconf_identity:{row['profile_id']}",
                    },
                ),
            )
    return None


def identity_from_vendor_modules(modules, model=None):
    """Match a vendor-private YANG family only after generic platform models fail."""
    raw = yaml.safe_load(Path(__file__).with_name("netconf_identity.yaml").read_text("utf-8"))
    for row in raw.get("identities", []):
        pattern = row.get("module_pattern")
        matches = sorted(module for module in modules if pattern and re.search(pattern, module, re.I))
        if matches:
            return Identity(
                vendor=row["vendor"],
                model=model,
                os_family=row["os_family"],
                profile_id=row["profile_id"],
                command_style=row["command_style"],
                confidence="medium",
                status="identified",
                evidence=(
                    {
                        "source": "netconf_private_yang_capabilities",
                        "modules": matches,
                        "rule": f"netconf_identity:{row['profile_id']}",
                    },
                ),
                warnings=("model_and_software_not_observed",),
            )
    return None


def read_netconf_identity(session, guard=None):
    """Try RFC 8348, OpenConfig platform, then the configured vendor YANG signature."""
    modules = announced_modules(session.capabilities)
    last = PlatformEvidence(None, None, None, None, "capabilities", "platform_model_not_announced")
    for module, subtree in (
        (
            "ietf-hardware",
            f'<hardware xmlns="{IETF_HARDWARE}"><component><name/><mfg-name/><model-name/><software-rev/></component></hardware>',
        ),
        (
            "openconfig-platform",
            f'<components xmlns="{OC_PLATFORM}"><component><name/><state><mfg-name/><model-name/><software-version/></state></component></components>',
        ),
    ):
        if module not in modules:
            continue
        if guard:
            guard()
        try:
            reply = session.get(subtree)
        except DeviceAccessError as exc:
            if exc.failure_code != "netconf_rpc_error":
                raise
            last = PlatformEvidence(None, None, None, None, module, "platform_read_failed")
            continue
        try:
            root = ElementTree.fromstring(reply)
        except Exception:
            last = PlatformEvidence(None, None, None, None, module, "platform_reply_not_xml", reply)
            continue
        components = [item for item in root.iter() if item.tag.rpartition("}")[2] == "component"]
        candidates = []
        for component in components:
            values = (
                _text(component, "mfg-name"),
                _text(component, "model-name"),
                _text(component, "software-rev", "software-version"),
            )
            if any(values):
                candidates.append(values)
        candidates.sort(key=lambda row: sum(value is not None for value in row), reverse=True)
        vendor, model, software = candidates[0] if candidates else (None, None, None)
        identity = identity_from_platform(vendor, model, software, source=module)
        last = PlatformEvidence(
            identity,
            vendor,
            model,
            software,
            module,
            None if identity else "platform_identity_rule_not_matched",
            reply,
        )
        if identity:
            return last, modules
    if identity := identity_from_vendor_modules(modules, model=last.model if last else None):
        return PlatformEvidence(identity, last.vendor, last.model, last.software, "vendor_private_yang", reply=last.reply), modules
    return last, modules

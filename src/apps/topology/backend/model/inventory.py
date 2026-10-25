"""Partial IVY network inventory: one network element per collected device.

Standards: draft-ietf-ivy-network-inventory-yang-20 and its software extension -04 (the
plan's fixed IVY baseline).  IVY's ne-type defaults to ne-physical, so an element is
projected only when the version output proves what kind of element it is:

- hypervisor vCPUs and virtio NIC drivers (Comware VSR): ne-virtual, "instantiated
  inside a virtual machine";
- IOS running as a Linux process ("Linux Unix (...) processor", Cisco IOL): ne-software,
  "runs directly on a host OS";
- anything else: no element, and the gap says the kind is unproven.  A serial number
  proves neither (IOL reports one) and is never invented: it belongs to a chassis
  component whose component-id the CLI does not give, so it stays evidence.

ne-id is the device's stable topology identity, an identifier policy of the application
(implementation plan §4.1), not a value read from the device.  Physical elements and
stacks need real samples before a rule is added for them.
"""

from __future__ import annotations

from copy import deepcopy

NWI = "ietf-network-inventory:"
SWX = "ietf-network-inventory-sw-ext:"


def _text(value):
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value == value.strip()
        and len(value) <= 255
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def _kind(version):
    """(ne-type identity, evidence) or (None, None)."""
    if version.get("virtual_cpus") and version.get("virtio_nic"):
        return SWX + "ne-virtual", ["virtual_cpus", "virtio_nic"]
    if version.get("unix_processor"):
        return SWX + "ne-software", ["unix_processor"]
    return None, None


def _software(version):
    images = []
    for name, revision in (
        ("boot_image", "boot_image_version"),
        ("system_image", "system_image_version"),
        ("image_name", "image_version"),
    ):
        if _text(version.get(name)):
            entry = {"name": version[name]}
            if _text(version.get(revision)):
                entry["revision"] = version[revision]
            images.append(entry)
    names = [entry["name"] for entry in images]
    return [entry for entry in images if names.count(entry["name"]) == 1]


def project_inventory(observation, ne_id):
    """Return ``{tree, metadata, gaps}`` with at most one IVY network element."""
    result = {
        "tree": {},
        "metadata": {
            "standard": "draft-ietf-ivy-network-inventory-yang-20, -software-04",
            "content": "operational",
            "completeness": "partial",
            "full_validation": "not_performed",
        },
        "gaps": [],
    }
    data = observation.get("data", observation) if isinstance(observation, dict) else None
    facts = data.get("facts", data) if isinstance(data, dict) else None
    if not isinstance(facts, dict):
        result["gaps"].append({"code": "invalid_observation"})
        return result
    version = facts.get("version")
    if not isinstance(version, dict):
        result["gaps"].append({"code": "version_not_observed"})
        return result
    if not _text(ne_id):
        result["gaps"].append({"code": "stable_ne_identity_not_observed"})
        return result
    kind, evidence = _kind(version)
    result["metadata"]["version_facts"] = deepcopy(version)
    if kind is None:
        result["gaps"].append({"code": "ne_type_not_proven", "field": "ne-type"})
        return result
    element = {"ne-id": ne_id, "ne-type": kind}
    local = facts.get("lldp_local") if isinstance(facts.get("lldp_local"), dict) else {}
    name = local.get("hostname") or version.get("hostname")
    if _text(name):
        element["name"] = name
    identity = (data.get("platform") or {}).get("identity") if isinstance(data, dict) else None
    vendor = identity.get("vendor") if isinstance(identity, dict) else None
    if _text(vendor):
        element["mfg-name"] = vendor
    if _text(version.get("model")):
        element["product-name"] = version["model"]
    software = _software(version)
    if software:
        element["software-rev"] = software
    if version.get("serial") or local.get("serial"):
        result["gaps"].append(
            {"code": "chassis_component_id_not_observed", "field": "serial-number"}
        )
    result["metadata"]["ne_type_evidence"] = evidence
    result["tree"] = {
        NWI + "network-inventory": {"network-elements": {"network-element": [element]}}
    }
    return result

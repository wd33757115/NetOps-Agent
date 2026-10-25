"""Partial RFC 8343 interface state, kept separate from the SIMAP tree.

Only observed name, type, oper-status, enabled (shutdown) and description are projected.
The native mandatory statistics/discontinuity-time remains absent, with a
completeness gap: this is not a fully validated device instance.  Nothing that was
not observed (admin state, timestamps, statistics) is fabricated.  Snapshot
identity/evidence metadata stays outside the YANG tree under the application's
Q-IETF-1 association policy.
"""

from __future__ import annotations

from copy import deepcopy

OPER_STATUSES = frozenset(
    {"up", "down", "testing", "unknown", "dormant", "not-present", "lower-layer-down"}
)
INTERFACE_TYPES = {
    "ethernet": "iana-if-type:ethernetCsmacd",
    "lag": "iana-if-type:ieee8023adLag",
    "vlanif": "iana-if-type:l3ipvlan",
    "loopback": "iana-if-type:softwareLoopback",
    # IANA tunnel(131), "encapsulation interface". NVE and VSI interfaces have no IANA type.
    "tunnel": "iana-if-type:tunnel",
}


def _name(value):
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= 256
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def project_interfaces(records):
    """Return ``{tree, metadata, gaps}`` from parsed interface fact records.

    Interface types come from explicit facts, never from a name guessed here.
    Missing/invalid leaves are omitted and diagnosed; duplicate names are not
    resolved by choosing a row.  ``tree`` is RFC 7951 JSON for an independent,
    partial RFC 8343 instance.  A native full validation must still report its
    missing mandatory fields.
    """
    gaps, interfaces, duplicates = [], {}, set()
    if records is None:
        gaps.append({"code": "interfaces_not_observed"})
        records = []
    elif not isinstance(records, list):
        gaps.append({"code": "invalid_interfaces"})
        records = []
    for index, record in enumerate(records):
        if not isinstance(record, dict) or not _name(record.get("ifname")):
            gaps.append({"code": "invalid_interface_record", "record_index": index})
            continue
        name = record["ifname"]
        if name in interfaces:
            duplicates.add(name)
            continue
        entry = {"name": name}
        kind = record.get("type")
        if isinstance(kind, str) and kind in INTERFACE_TYPES:
            entry["type"] = INTERFACE_TYPES[kind]
        elif isinstance(kind, str) and kind in INTERFACE_TYPES.values():
            entry["type"] = kind
        else:
            gaps.append({"code": "interface_type_unmapped", "interface": name, "field": "type"})
        status = record.get("oper_status")
        if isinstance(status, str) and status in OPER_STATUSES:
            entry["oper-status"] = status
        else:
            gaps.append(
                {
                    "code": "oper_status_not_observed_or_invalid",
                    "interface": name,
                    "field": "oper-status",
                }
            )
        # Shutdown is the configured state: RFC 8343 "enabled" (base model). admin-status
        # needs the if-mib feature, which also makes if-index mandatory. "enabled"
        # defaults to true, so an unobserved state is stated as a gap.
        admin = record.get("admin_status")
        if admin in ("up", "down"):
            entry["enabled"] = admin == "up"
        else:
            gaps.append({"code": "admin_state_not_observed", "interface": name, "field": "enabled"})
        description = record.get("description")
        if isinstance(description, str) and description and _name(description):
            entry["description"] = description
        interfaces[name] = entry
    for name in sorted(duplicates):
        del interfaces[name]
        gaps = [gap for gap in gaps if gap.get("interface") != name]
        gaps.append({"code": "duplicate_interface", "interface": name})
    for name in sorted(interfaces):
        gaps.append(
            {
                "code": "mandatory_statistics_not_observed",
                "interface": name,
                "field": "statistics/discontinuity-time",
            }
        )
    return {
        "tree": {
            "ietf-interfaces:interfaces": {
                "interface": [interfaces[name] for name in sorted(interfaces)]
            }
        },
        "metadata": {
            "standard": "RFC 8343",
            "content": "operational",
            "completeness": "partial",
            "full_validation": "not_performed",
        },
        "gaps": gaps,
    }


def project_device_state(observation):
    """Project one snapshot Observation, independently of LLDP success.

    Accept a serialized Observation with ``data.facts.interfaces``, its ``data``
    payload, or already extracted facts.  Capability failures cannot turn old
    carried records into current state.  The caller owns snapshot selection and
    authorization; this function neither fetches facts nor associates YANG trees.
    """
    if not isinstance(observation, dict):
        result = project_interfaces(None)
        result["gaps"].append({"code": "invalid_observation"})
        return result
    data = observation.get("data", observation)
    if not isinstance(data, dict):
        result = project_interfaces(None)
        result["gaps"].append({"code": "invalid_observation_data"})
        return result
    facts = data.get("facts", data)
    records = facts.get("interfaces") if isinstance(facts, dict) else None
    sidecar = data.get("fact_metadata")
    metadata = sidecar.get("interfaces") if isinstance(sidecar, dict) else None
    wrapper = facts.get("interfaces") if isinstance(facts, dict) else None
    embedded = wrapper.get("parse_metadata") if isinstance(wrapper, dict) else None
    incomplete = any(
        isinstance(item, dict)
        and isinstance(item.get("warnings"), list)
        and "collection_incomplete" in item["warnings"]
        for item in (metadata, embedded)
    )
    if isinstance(wrapper, dict):
        records = wrapper.get("records")
    capabilities = data.get("capabilities")
    status = capabilities.get("interfaces") if isinstance(capabilities, dict) else None
    unusable = capabilities is not None and (
        not isinstance(capabilities, dict)
        or not isinstance(status, str)
        or status not in {"complete", "partial"}
    )
    if isinstance(wrapper, dict) and "status" in wrapper:
        wrapped_status = wrapper["status"]
        unusable = (
            unusable
            or not isinstance(wrapped_status, str)
            or wrapped_status not in {"complete", "partial"}
        )
    result = project_interfaces(None if unusable or incomplete else records)
    result["metadata"]["interface_records"] = (
        deepcopy(records) if isinstance(records, list) else None
    )
    if isinstance(metadata, dict):
        result["metadata"]["fact_metadata"] = {"interfaces": deepcopy(metadata)}
    if isinstance(embedded, dict):
        result["metadata"]["embedded_fact_metadata"] = {"interfaces": deepcopy(embedded)}
    if unusable:
        result["gaps"].append({"code": "interfaces_capability_unusable"})
    if incomplete:
        result["gaps"].append({"code": "interfaces_transport_incomplete"})
    elif status == "partial":
        result["gaps"].append({"code": "interfaces_collection_partial"})
    if isinstance(status, str):
        result["metadata"]["capability_status"] = status
    for source, target in (("id", "observation_id"), ("observed_at", "observed_at")):
        if isinstance(observation.get(source), str):
            result["metadata"][target] = observation[source]
    return result


def read_interface_state(projected, ifname):
    """Read one standard interface entry by its exact fact name, or return None.

    Return a copy so application metadata cannot accidentally enter the standard
    tree.  Missing status stays missing; it never becomes an assumed up/down.
    """
    if not isinstance(projected, dict) or not _name(ifname):
        return None
    tree = projected.get("tree")
    interfaces = tree.get("ietf-interfaces:interfaces") if isinstance(tree, dict) else None
    records = interfaces.get("interface") if isinstance(interfaces, dict) else None
    if not isinstance(records, list):
        return None
    matches = [
        record for record in records if isinstance(record, dict) and record.get("name") == ifname
    ]
    return dict(matches[0]) if len(matches) == 1 else None

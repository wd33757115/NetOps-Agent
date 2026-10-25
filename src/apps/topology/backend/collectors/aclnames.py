"""The names a CLI dialect prints in ACL entries instead of numbers (aclnames.yaml)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml


@lru_cache(maxsize=1)
def _tables():
    data = yaml.safe_load(Path(__file__).with_name("aclnames.yaml").read_text(encoding="utf-8"))
    if data["schema_version"] != 1:
        raise ValueError("unknown ACL name schema")
    tables = {}
    for dialect, raw in data["dialects"].items():
        protocols = {
            name: None if number == "any" else int(number)
            for name, number in raw["protocols"].items()
        }
        ports = {
            protocols[kind]: {name: int(port) for name, port in names.items()}
            for kind, names in raw["ports"].items()
        }
        icmp = {name: (int(kind), code) for name, (kind, code) in raw["icmp"].items()}
        roles = {kind.lower(): role for kind, role in (raw.get("nat_roles") or {}).items()}
        tables[dialect] = {"protocols": protocols, "ports": ports, "icmp": icmp, "nat_roles": roles}
    return tables


def names(dialect):
    """{"protocols": {name: number or None}, "ports": {protocol: {name: port}},
    "icmp": {name: (type, code or None)}, "nat_roles": {binding kind: role}} for a
    dialect; empty tables for an unknown one."""
    return _tables().get(dialect) or {"protocols": {}, "ports": {}, "icmp": {}, "nat_roles": {}}

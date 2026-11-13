"""NETCONF first, the CLI when NETCONF cannot serve a table (owner decision 2026-10-05).

netconf-api publishes NETCONF definitions for rule-governance check items. Through its
public contract (``src.apps.netconf_api.runtime``) a definition gives the same-name
Value rows the CLI template gives, so topology projects them with the same code
(``rules.project_netconf``). Topology opens the read-only NETCONF session itself, with
the node's login, on port 830, under a ``device.netconf`` grant.

A table is taken from NETCONF only when it is read whole and clean: the device
announces the definition's module, every row maps without a problem, and the table is
not empty (an empty reply cannot tell "no entries" from a filter that matched nothing).
A table the CLI collects per VRF is taken only from a device without VRFs, until a
definition is verified per instance. Anything else falls back to the CLI, and the reason
stays with the capability (``ParseResult.netconf``) for whoever fixes the definition.

Modes: ``prefer`` (default), ``off``, and ``compare``: both are read, the CLI stays the
fact, and the two are compared fact by fact.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from collections import Counter
from datetime import datetime, timezone
from uuid import uuid4

from src.platform.device_access.contracts import CommandResult, DeviceAccessError

from . import rules
from .rules_tables import ASSEMBLE

MODES = ("prefer", "off", "compare")
REFRESH_SECONDS = 60  # a newly published definition is used by the next jobs
MAX_PROBLEMS = 20  # problems kept per capability; the count is whole
MAX_EXAMPLES = 5  # differing facts kept per comparison
logger = logging.getLogger(__name__)
_cache = {}
_openconfig_lldp_mapping = None


def reader():
    from src.apps.netconf_api.runtime import DefinitionReader

    cached = _cache.get("reader")
    if cached is None or time.monotonic() - cached[1] > REFRESH_SECONDS:
        cached = _cache["reader"] = (DefinitionReader(), time.monotonic())
    return cached[0]


def standard_reader():
    from src.apps.netconf_api.runtime import StandardReader

    cached = _cache.get("standard")
    if cached is None or time.monotonic() - cached[1] > REFRESH_SECONDS:
        cached = _cache["standard"] = (StandardReader(), time.monotonic())
    return cached[0]


def standard_selection(identity, model=None):
    """The netconf-api mapping that reads this device's tables as standard records."""
    try:
        return standard_reader().select(rules.device(identity, model))
    except Exception as exc:  # mappings unavailable: the device keeps its CLI trees
        logger.warning("netconf standard mappings unavailable: %s", exc)
        return None


def read_standard(session, guard, selection, on_result=None):
    """The device's tables as standard records (netconf-api's conversion layer), each
    reply kept as evidence; topology builds the standard trees from them."""
    source_attempts = {
        "lldp": [
            {"source": "ietf_yang", "status": "no_ietf_lldp_module_announced"}
        ]
    }
    modules = _announced_modules(session.capabilities)
    projected = None
    if "openconfig-lldp" in modules:
        try:
            from src.apps.netconf_api.backend import standard as standard_mapping

            global _openconfig_lldp_mapping
            if _openconfig_lldp_mapping is None:
                _openconfig_lldp_mapping = standard_mapping.shipped()["openconfig_lldp"]

            class RefusedError(Exception):
                pass

            def fetch(subtree):
                if guard is not None:
                    guard()
                try:
                    return session.get(subtree)
                except DeviceAccessError as exc:
                    if exc.failure_code == "netconf_rpc_error":
                        raise RefusedError(str(exc)) from exc
                    raise

            projected = standard_mapping.read(
                _openconfig_lldp_mapping, fetch, refused=(RefusedError,)
            )
            _evidence(
                "standard",
                [
                    {
                        "definition": f"openconfig-lldp@{modules['openconfig-lldp']}:{item['table']}",
                        "reply": item["reply"],
                    }
                    for item in projected["replies"]
                ],
                on_result,
            )
            clean = not projected["failed"] and not projected["problems"]
            if clean:
                source_attempts["lldp"].append(
                    {
                        "source": "openconfig_yang",
                        "status": "used",
                        "module": "openconfig-lldp",
                        "neighbor_count": len(projected["records"].get("lldp-neighbor", [])),
                    }
                )
            else:
                source_attempts["lldp"].append(
                    {
                        "source": "openconfig_yang",
                        "status": "unusable",
                        "reason": "empty_or_incomplete_reply",
                        "failed": projected["failed"],
                        "problem_count": len(projected["problems"]),
                    }
                )
                source_attempts["lldp"].append(
                    {"source": "vendor_private_yang", "status": "used", "mapping": selection.ref}
                )
        except (KeyError, ValueError) as exc:
            source_attempts["lldp"].append(
                {
                    "source": "openconfig_yang",
                    "status": "unusable",
                    "reason": "mapping_or_rpc_error",
                    "detail": str(exc)[:300],
                }
            )
            source_attempts["lldp"].append(
                {"source": "vendor_private_yang", "status": "used", "mapping": selection.ref}
            )
    else:
        source_attempts["lldp"].append(
            {"source": "openconfig_yang", "status": "module_not_announced"}
        )
        source_attempts["lldp"].append(
            {"source": "vendor_private_yang", "status": "used", "mapping": selection.ref}
        )
    vendor_selection = selection
    if projected is not None and not projected["failed"] and not projected["problems"]:
        vendor_selection = replace(
            selection,
            mapping=replace(
                selection.mapping,
                tables=tuple(
                    table
                    for table in selection.mapping.tables
                    if table.name not in {"lldp-neighbor", "lldp-management-address"}
                ),
            ),
        )
    data = standard_reader().read(vendor_selection, session, guard=guard)
    records = data["records"]
    if vendor_selection is not selection:
        for table in ("lldp-neighbor", "lldp-management-address"):
            records[table] = projected["records"].get(table, [])
    elif not data["failed"] and not data["problems"]:
        # NETCONF servers commonly omit an empty non-presence/list subtree. A successful
        # LLDP query with no converted rows is still an observation: do not force SSH/CLI
        # fallback just because this device currently has no LLDP neighbors.
        records.setdefault("lldp-neighbor", [])
        records.setdefault("lldp-management-address", [])
    _evidence(
        "standard",
        [
            {"definition": f"{selection.ref}:{r['table']}", "reply": r["reply"]}
            for r in data["replies"]
        ],
        on_result,
    )
    return {
        "mapping": selection.ref,
        "records": records,
        "problem_count": len(data["problems"]),
        "problem_count_by_table": {
            table: sum(1 for problem in data["problems"] if problem.get("table") == table)
            for table in sorted({problem.get("table") for problem in data["problems"]})
        },
        "problems": data["problems"][:MAX_PROBLEMS],
        "failed": data["failed"],
        "source_attempts": source_attempts,
    }


def _announced_modules(capabilities):
    from urllib.parse import parse_qs

    return {
        module: parse_qs(capability.partition("?")[2]).get("revision", [None])[0]
        for capability in capabilities
        for module in parse_qs(capability.partition("?")[2]).get("module", [])
    }


def selections(identity, capabilities, model=None):
    """{capability: NetconfSelection} a published definition serves on this device.

    Tables assembled with vetoes (trunk, switchport, OSPF) stay on the CLI: their
    assembly reads CLI blocks, not rows.
    """
    wanted = {
        rules.CHECK_ITEMS[cap]: cap
        for cap in capabilities
        if cap in rules.CHECK_ITEMS and cap not in ASSEMBLE
    }
    if not wanted:
        return {}
    try:
        chosen = reader().select(wanted, rules.device(identity, model))
    except Exception as exc:  # NETCONF definitions unavailable: the CLI serves every table
        logger.warning("netconf definitions unavailable, using the CLI: %s", exc)
        return {}
    return {wanted[item]: selection for item, selection in chosen.items()}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _evidence(capability, replies, on_result):
    """Each reply as a command result, so it is kept with the node's CLI outputs."""
    if on_result is None:
        return
    for number, reply in enumerate(replies):
        on_result(
            CommandResult(
                execution_id=uuid4().hex,
                command_id=f"netconf-{capability}-{number}",
                started_at=None,
                finished_at=_now(),
                execution_status="succeeded",
                output_complete=True,
                output=reply["reply"],
                driver="netconf",
                driver_version=reply["definition"],
            )
        )


def read(session, guard, identity, capability, selection, model=None, on_result=None):
    """(ParseResult or None, metadata) for one capability over an open session.

    ``metadata["reason"]`` is why the table cannot be used as read (None: it can).
    """
    from src.apps.netconf_api.runtime import (
        DefinitionError,
        MappingError,
        announced,
    )

    meta = {"definition": selection.ref, "sha256": selection.sha256, "reason": None}
    chosen = rules.selection(identity, capability, model)
    if chosen is None:
        return None, {**meta, "reason": "no_contract"}
    if not announced(selection, session.capabilities):
        return None, {**meta, "reason": "module_not_announced", "module": selection.module}
    try:
        data = reader().read(selection, session, fields=chosen.fields, guard=guard)
    except MappingError as exc:
        return None, {**meta, "reason": "reply_unreadable", "detail": str(exc)[:300]}
    except DefinitionError as exc:
        return None, {**meta, "reason": "definition_invalid", "detail": str(exc)[:300]}
    except DeviceAccessError as exc:
        if exc.failure_code != "netconf_rpc_error":
            raise  # the session is gone (or the budget): the caller falls back
        return None, {**meta, "reason": "rpc_error", "detail": str(exc)[:300]}
    _evidence(capability, data.replies, on_result)
    parsed = rules.project_netconf(identity, capability, chosen, data, selection.ref)
    meta.update(
        read=data.read,
        rows=len(data.rows),
        problem_count=len(data.problems),
        problems=data.problems[:MAX_PROBLEMS],
    )
    if parsed.status != "complete":
        meta["reason"] = "problems" if data.problems else "not_complete"
    elif not data.read:
        meta["reason"] = "empty_unverified"
    return parsed, meta


def _canonical(value):
    """A fact as comparable text: lists (next hops, members) in a stable order."""
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        items = [_canonical(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, default=str))
    return value


def compare(cli, netconf):
    """How the facts of two readings of one table differ.

    Fields only one side ever gives (the CLI's ARP age, say) are listed and left out;
    every other field must agree, fact by fact.
    """
    if cli is None or netconf is None:
        return {"status": "not_compared"}
    left, right = cli.records, netconf.records
    left_fields = set().union(*(record.keys() for record in left)) if left else set()
    right_fields = set().union(*(record.keys() for record in right)) if right else set()
    common = left_fields & right_fields

    def key(record):
        return json.dumps(
            _canonical({name: record.get(name) for name in common}), sort_keys=True, default=str
        )

    a, b = Counter(map(key, left)), Counter(map(key, right))
    return {
        "status": "same" if a == b and cli.status == netconf.status else "different",
        "cli_status": cli.status,
        "netconf_status": netconf.status,
        "cli_facts": len(left),
        "netconf_facts": len(right),
        "cli_only_fields": sorted(left_fields - right_fields),
        "netconf_only_fields": sorted(right_fields - left_fields),
        "only_cli": [json.loads(item) for item in list((a - b).elements())[:MAX_EXAMPLES]],
        "only_netconf": [json.loads(item) for item in list((b - a).elements())[:MAX_EXAMPLES]],
        "differing": sum((a - b).values()) + sum((b - a).values()),
    }

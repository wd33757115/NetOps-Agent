"""Collection issues: what a node could not give over SSH (the CLI) or NETCONF.

Derived from each observation, so a human can see what to fix and where:
- a node that failed as a whole;
- a capability the CLI could not read completely (a rejected line, no implementation
  for the platform, a cut-off output);
- a capability NETCONF could not serve (session failure, module not announced, an
  unmapped code or lookup miss in the definition) or whose facts differ from the CLI's.

One issue per (owner, network, device, capability, transport, kind). Every occurrence
updates it and is logged; a later observation that reads the capability cleanly over
that transport clears it.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from sqlalchemy import select

from .models import CollectionIssue, now

logger = logging.getLogger(__name__)
MAX_WARNINGS = 20
# A CLI table that could not be read; "unsupported" is the device lacking the command
# (a router without switching), which nothing on this side can fix.
SSH_FAULTS = frozenset({"failed", "partial"})
# Expected limits, not faults: an empty NETCONF table is read again over the CLI.
NOT_FAULTS = frozenset({"empty_unverified"})


def derive(data, reason):
    """([{capability, transport, kind, detail}], {(capability, transport) read cleanly})."""
    found, clean = [], set()
    if reason:
        found.append(
            {
                "capability": "node",
                "transport": "ssh",
                "kind": reason,
                "detail": {"platform": data.get("platform") or {}},
            }
        )
    metadata = data.get("fact_metadata") or {}
    for capability, status in (data.get("capabilities") or {}).items():
        meta = metadata.get(capability) or {}
        netconf = meta.get("netconf") or {}
        if meta.get("tier") != "netconf":
            if status == "complete":
                clean.add((capability, "ssh"))
            elif status in SSH_FAULTS:
                found.append(
                    {
                        "capability": capability,
                        "transport": "ssh",
                        "kind": status,
                        "detail": {"warnings": (meta.get("warnings") or [])[:MAX_WARNINGS]},
                    }
                )
        if not netconf:
            continue
        comparison = netconf.get("comparison") or {}
        kind = netconf.get("reason") or (
            "comparison_mismatch" if comparison.get("status") == "different" else None
        )
        if kind in NOT_FAULTS:
            continue
        if kind:
            found.append(
                {"capability": capability, "transport": "netconf", "kind": kind, "detail": netconf}
            )
        elif netconf.get("status") in {"used", "compared"}:
            clean.add((capability, "netconf"))
    return found, clean


def _hostname(data):
    facts = data.get("facts") or {}
    for summary in (facts.get("lldp_local"), facts.get("version")):
        if isinstance(summary, dict) and summary.get("hostname"):
            return str(summary["hostname"])[:255]
    return None


def record(session, *, owner, network_id, run_id, observation_id, address, data, reason):
    """Keep this observation's issues and clear the ones it shows fixed."""
    found, clean = derive(data, reason)
    device = _hostname(data)
    at = now()
    for issue in found:
        row = session.scalar(
            select(CollectionIssue).where(
                CollectionIssue.owner_id == owner,
                CollectionIssue.network_id == network_id,
                CollectionIssue.address == address,
                CollectionIssue.capability == issue["capability"],
                CollectionIssue.transport == issue["transport"],
                CollectionIssue.kind == issue["kind"][:128],
            )
        )
        if row is None:
            row = CollectionIssue(
                id=uuid4().hex,
                owner_id=owner,
                network_id=network_id,
                address=address,
                capability=issue["capability"],
                transport=issue["transport"],
                kind=issue["kind"][:128],
                count=0,
                first_seen=at,
            )
            session.add(row)
        row.count = (row.count or 0) + 1
        row.detail, row.device = issue["detail"], device or row.device
        row.last_seen, row.last_run_id, row.last_observation_id = at, run_id, observation_id
        row.cleared_at = None
        logger.warning(
            "topology collection issue: %s (%s) %s over %s: %s (seen %d times, run %s)",
            address,
            device or "-",
            issue["capability"],
            issue["transport"],
            issue["kind"],
            row.count,
            run_id,
        )
    for capability, transport in clean:
        for row in session.scalars(
            select(CollectionIssue).where(
                CollectionIssue.owner_id == owner,
                CollectionIssue.network_id == network_id,
                CollectionIssue.address == address,
                CollectionIssue.capability == capability,
                CollectionIssue.transport == transport,
                CollectionIssue.cleared_at.is_(None),
            )
        ):
            row.cleared_at = at
            logger.info(
                "topology collection issue cleared: %s %s over %s: %s",
                address,
                capability,
                transport,
                row.kind,
            )
    return found


def view(row):
    return {
        "id": row.id,
        "network_id": row.network_id,
        "address": row.address,
        "device": row.device,
        "capability": row.capability,
        "transport": row.transport,
        "kind": row.kind,
        "detail": row.detail,
        "count": row.count,
        "first_seen": row.first_seen.isoformat() if row.first_seen else None,
        "last_seen": row.last_seen.isoformat() if row.last_seen else None,
        "last_run_id": row.last_run_id,
        "last_observation_id": row.last_observation_id,
        "cleared_at": row.cleared_at.isoformat() if row.cleared_at else None,
    }


def listing(session, owner, *, network_id=None, include_cleared=False, limit=200):
    query = select(CollectionIssue).where(CollectionIssue.owner_id == owner)
    if network_id:
        query = query.where(CollectionIssue.network_id == network_id)
    if not include_cleared:
        query = query.where(CollectionIssue.cleared_at.is_(None))
    rows = session.scalars(query.order_by(CollectionIssue.last_seen.desc()).limit(limit))
    return [view(row) for row in rows]

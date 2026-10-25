"""Durable discovery with explicit human retry and fenced target execution."""

import hashlib
import ipaddress
import json
import logging
import os
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from src.apps.device_access.backend.host_keys import HostKeyStore
from src.platform.clock import beijing
from src.platform.device_access import AccessContext, AccessGrant, BatchRequest, DeviceAccessService
from src.platform.device_access.contracts import (
    ConnectionProfile,
    DeviceAccessError,
)
from src.platform.device_access.contracts import (
    Target as AccessTarget,
)
from src.platform.device_access.drivers.paramiko_driver import ParamikoDriver
from src.platform.device_access.security import CredentialStore

from . import issues, logins, storage
from .collectors.collect import collect_node, command_allowlist, scope_base, scoped_command
from .collectors.families import discovery_families
from .collectors.identify import TopologyDiscoveryPlan as DiscoveryPlan
from .collectors.tiers.common import valid_mac
from .logins import DOMAIN
from .model.builder import build_topology_layers, device_node_ids
from .model.standard_tables import builder_rows
from .model.l2_forwarding import analyse as analyse_l2_forwarding
from .model.path import locate_with_exits as locate_in
from .model.path import trace as trace_in
from .model.protocols import adjacencies as protocol_adjacencies
from .model.services import project_services
from .model.snapshot_state import enrich_snapshot_state
from .model.view import canvas
from .models import (
    Alias,
    Link,
    Network,
    Node,
    Observation,
    Resolution,
    Run,
    Snapshot,
    Target,
    TerminationPoint,
    now,
)
from .normalize import diff, parse, rfc8345
from .repository import owned, sessions

APP_ID = "topology"
MODEL_PROFILE = "rfc8345+simap-yang-03:l2-l3:scoped-v2"
ACTIVE = ("queued", "running", "waiting_for_input")
# Tables a path checks a packet against: only a complete one says what is not there.
POLICY_FACTS = frozenset(
    {
        "pbr_bindings",
        "pbr_rules",
        "nat_bindings",
        "nat_rules",
        "acl_rules",
        "acl_bindings",
        "security_zones",
    }
)
TOPOLOGY_FACTS = (
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
    "stp",
    "vrrp",
    "routes",
    "ospf_interfaces",
    "ospf_process",
    "ospf_neighbors",
    "bgp_peers",
    "vrfs",
    "vrf_detail",
    "bgp_vpnv4",
    "bgp_evpn",
    "ldp_peers",
    "mpls_lfib",
    "evpn_instances",
    "evpn_peers",
    "vxlan_vnis",
    "tunnel_interfaces",
    "evpn_mac",
    "pbr_bindings",
    "pbr_rules",
    "nat_bindings",
    "acl_rules",
    "acl_bindings",
    "security_zones",
    "nat_rules",
)
NODE_BUDGET_SECONDS = 240
# A lease outlives the node budget, so a live collection is never reclaimed mid-way.
LEASE_SECONDS = NODE_BUDGET_SECONDS + 60
logger = logging.getLogger(__name__)


def signature(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def scope(address, networks):
    return any(ipaddress.ip_address(address) in ipaddress.ip_network(cidr) for cidr in networks)


def dispatch(run_id):
    from .tasks import execute

    try:
        execute.apply_async(args=[run_id], queue="app.topology")
    except Exception:
        logger.warning(
            "topology queue unavailable; reconciler will dispatch committed run %s", run_id
        )


def release_credentials(run_id):
    """Revoke after commit. Redis TTL is the fail-safe if revocation is unavailable."""
    with sessions() as session:
        run = session.get(Run, run_id)
        if not run or run.status in ACTIVE:
            return
        refs = set(session.scalars(select(Target.credential_ref).where(Target.run_id == run_id)))
        refs.add(run.settings.get("credential_ref"))
    refs.discard(None)
    if refs:
        try:
            store = CredentialStore()
            for ref in refs:
                store.revoke(ref)
        except Exception:
            logger.warning("topology credential revocation deferred to TTL for run %s", run_id)


def _new_run(session, owner, body, settings, mode):
    digest = signature({"mode": mode, **settings})
    existing = session.scalar(
        select(Run).where(Run.owner_id == owner, Run.request_id == body.request_id)
    )
    if existing:
        if existing.input_hash != digest:
            raise HTTPException(409, "请求编号已用于不同任务")
        return existing, False
    if body.network_id:
        network = session.scalar(
            select(Network)
            .where(Network.id == body.network_id, Network.owner_id == owner)
            .with_for_update()
        )
        if not network:
            raise HTTPException(404, "网络不存在")
        if network.active_run:
            raise HTTPException(409, "该网络已有未结束任务")
    else:
        network = Network(id=uuid4().hex, owner_id=owner, name=body.name, domain=DOMAIN)
        session.add(network)
        session.flush()
        _activate_if_none(session, owner, network)
    run = Run(
        id=uuid4().hex,
        network_id=network.id,
        owner_id=owner,
        request_id=body.request_id,
        input_hash=digest,
        settings={**settings, "mode": mode},
        status="queued",
    )
    session.add(run)
    session.flush()
    network.active_run = run.id
    return run, True


def submit(owner, body):
    settings = body.model_dump(exclude={"credentials", "request_id"})
    if len(body.seeds) > body.max_targets or any(
        not scope(ip, body.allowed_cidrs) for ip in body.seeds
    ):
        raise HTTPException(422, "种子须在允许网段内，且数量不得超过节点上限")
    if body.credentials is None and not logins.has_default(owner):
        raise HTTPException(422, "请填写本次用户名和密码，或先保存默认用户名密码")
    ref = None
    try:
        with sessions() as session:
            run, created = _new_run(session, owner, body, settings, "ssh")
            run_id = run.id
            if created:
                if body.credentials is not None:
                    # Every node of this run, new neighbours included, uses this login.
                    ref = logins.put(owner, body.allowed_cidrs, body.credentials)
                # The reference, never a secret, is the only credential value persisted/queued.
                run.settings = {**run.settings, "credential_ref": ref}
                for address in body.seeds:
                    session.add(
                        Target(
                            id=uuid4().hex,
                            run_id=run.id,
                            target_key=f"{DOMAIN}|{address}|{body.port}",
                            address=address,
                            depth=0,
                            status="queued",
                        )
                    )
    except Exception:
        if ref:
            CredentialStore().revoke(ref)
        raise
    dispatch(run_id)
    return read(owner, run_id)


def _observations(session, run_id):
    # Only the current attempt can prove current adjacency. Older structure may
    # be retained from an authorized snapshot, explicitly marked historical.
    return [o for o in _all_observations(session, run_id) if o["data"].get("complete")]


def _all_observations(session, run_id):
    """Latest attempt per address in this run, even when LLDP failed.

    A usable interface observation is independent of adjacency discovery. Never
    borrow the previous attempt's UP state when the latest attempt is unusable.
    """
    rows = session.execute(
        select(Observation, Target.address)
        .join(Target, Target.id == Observation.target_id)
        .where(Observation.run_id == run_id, Observation.attempt == Target.version)
        .order_by(Observation.attempt, Observation.received_at, Observation.id)
    ).all()
    latest = {}
    for row, address in rows:
        latest[address] = {
            "id": row.id,
            "address": address,
            "data": row.data,
            "observed_at": row.observed_at.isoformat(),
        }
    return list(latest.values())


def _state_inputs(session, run_id):
    targets = session.scalars(select(Target).where(Target.run_id == run_id)).all()
    return _all_observations(session, run_id), [
        {"address": t.address, "status": t.status, "reason": t.reason} for t in targets
    ]


def _facts(data):
    """Builder input for one observation; LLDP-only observations become lldp records."""
    platform = data.get("platform") or {}
    if "facts" in data:
        facts = dict(data["facts"])
    else:
        facts = {
            "lldp": [
                {
                    "local_if": n["local_port"],
                    # Same normalisation as the generic parser, so one device stays one node.
                    "remote_chassis_id": valid_mac(n["chassis_id"]) or n["chassis_id"],
                    "remote_chassis_subtype": n.get("chassis_subtype"),
                    "remote_port_id": n["remote_port"],
                    "remote_port_subtype": n.get("port_subtype"),
                    "remote_sysname": n.get("name"),
                    "remote_mgmt_addresses": n.get("addresses", []),
                }
                for n in data.get("neighbors", [])
            ]
        }
    identity = platform.get("identity") or {}
    # Typed facts keep their existing list schema. Explicit transport truncation
    # is carried separately and cannot establish current modeling inputs.
    metadata = data.get("fact_metadata", {})
    if isinstance(metadata, dict):
        for cap in TOPOLOGY_FACTS:
            details = metadata.get(cap, {})
            warnings = details.get("warnings", []) if isinstance(details, dict) else []
            if cap == "lldp_local" and any(
                isinstance(warning, str)
                and warning.rsplit(":", 1)[-1] == "conflicting_local_identity"
                for warning in warnings
            ):
                # The parser retains the last conflicting value for diagnostics,
                # not as a chassis identity eligible for device merging.
                facts.pop(cap, None)
                continue
            if isinstance(details, dict) and "collection_incomplete" in details.get("warnings", []):
                if cap in {"trunk", "switchport", "ospf_interfaces", "ospf_process"}:
                    # Keep an explicit structural veto. Erasing the summary
                    # could let another partial capability infer missing state.
                    facts[cap] = {"invalid_structure": True}
                    continue
                facts.pop(cap, None)
                if cap == "vlan":
                    facts.pop("vlan_members", None)
    capabilities = data.get("capabilities", {})
    if (
        isinstance(capabilities, dict)
        and "lldp_local" in capabilities
        and capabilities["lldp_local"] != "complete"
    ):
        facts.pop("lldp_local", None)
    # Historical observations without capability metadata remain compatible;
    # explicit conflicts and truncation above still veto their local identity.
    # Observations from the manual LLDP-only plan or offline import name only the
    # discovery profile.
    facts["platform"] = identity.get("os_family") or discovery_families().get(
        platform.get("profile_id"), ""
    )
    if isinstance(data.get("standard"), dict) and isinstance(
        data["standard"].get("records"), dict
    ):
        # The device's NETCONF tables as standard records: its trees are built from them,
        # and the builder's interface, address, neighbour and LLDP rows from those trees.
        facts["standard"] = data["standard"]
        facts.update(builder_rows(data["standard"]["records"], facts["platform"]))
    return facts


def _graph(session, run):
    """The run's own observations as SIMAP L2/L3, plus a canvas view and coverage."""
    network = session.get(Network, run.network_id)
    observations = _observations(session, run.id)
    base = f"urn:netops:topology:{network.id}:"
    devices = {o["address"]: _facts(o["data"]) for o in observations}
    tree, links_evidence, protocols, overlay_gaps = build_topology_layers(devices, base)
    l2_forwarding = analyse_l2_forwarding(devices, tree, base)
    ids = device_node_ids(devices, base)
    collected = {
        ids[o["address"]]: {
            "address": o["address"],
            "evidence_id": o["id"],
            "observed_at": o["observed_at"],
        }
        for o in observations
    }
    previous = session.get(Snapshot, network.current_snapshot) if network.current_snapshot else None
    prior = None
    if previous and previous.network_id == network.id and previous.run_id != run.id:
        prior = {
            "snapshot_id": previous.id,
            "graph": previous.graph,
            "created_at": previous.created_at.isoformat(),
        }
    all_observations, state_targets = _state_inputs(session, run.id)
    tree, state = enrich_snapshot_state(tree, collected, all_observations, state_targets, prior)
    nodes, links = canvas(tree, links_evidence, collected, base, state)
    graph = {
        "network_id": network.id,
        "model_profile": MODEL_PROFILE,
        "nodes": nodes,
        "links": links,
        "simap": tree,
        "evidence": links_evidence,
        "state": state,
        "protocols": {"ospf": protocols, "adjacencies": protocol_adjacencies(devices)},
        "overlay": {"gaps": overlay_gaps},
        "l2_forwarding": l2_forwarding,
    }
    targets = session.scalars(select(Target).where(Target.run_id == run.id)).all()
    addresses = {target.address: target for target in targets}
    unexplored = []
    for observation in observations:
        for neighbor in observation["data"]["neighbors"]:
            if not neighbor["addresses"]:
                unexplored.append(
                    {"chassis_id": neighbor["chassis_id"], "reason": "no_management_address"}
                )
            for address in neighbor["addresses"]:
                if address in addresses or run.settings["mode"] != "ssh":
                    continue
                reason = "target_budget"
                if not scope(address, run.settings["allowed_cidrs"]):
                    reason = "outside_task_scope"
                elif addresses[observation["address"]].depth >= run.settings["max_depth"]:
                    reason = "depth_budget"
                unexplored.append({"address": address, "reason": reason})
    graph["coverage"] = {
        "mode": run.settings["mode"],
        "seed_profile_id": run.settings["profile_id"],
        "platform_selection": "per_target",
        "targets": [
            {"address": t.address, "status": t.status, "reason": t.reason, "depth": t.depth}
            for t in targets
        ],
        "unexplored": unexplored,
        "observed_from": min((o["observed_at"] for o in observations), default=None),
        "observed_to": max((o["observed_at"] for o in observations), default=None),
    }
    return graph


def _finish(session, run, status=None):
    targets = session.scalars(select(Target).where(Target.run_id == run.id)).all()
    if status is None:
        if any(t.status in {"queued", "running"} for t in targets):
            run.status = "running"
            return
        if any(t.status == "waiting_for_input" for t in targets):
            run.status = "waiting_for_input"
            return
        status = "partial" if any(t.status != "succeeded" for t in targets) else "completed"
    graph = _graph(session, run)
    snap = Snapshot(id=uuid4().hex, network_id=run.network_id, run_id=run.id, graph=graph)
    session.add(snap)
    # The old projection tables have no reader; the snapshot holds the model.
    for model in (Link, TerminationPoint, Alias, Node):
        session.execute(delete(model).where(model.network_id == run.network_id))
    network = session.get(Network, run.network_id)
    network.current_snapshot, network.active_run = snap.id, None
    run.status, run.finished_at = status, now()


def read(owner, run_id):
    with sessions() as session:
        run = owned(session, Run, run_id, owner)
        targets = session.scalars(
            select(Target).where(Target.run_id == run_id).order_by(Target.depth, Target.address)
        ).all()
        snap = session.scalar(select(Snapshot).where(Snapshot.run_id == run_id))
        return {
            "id": run.id,
            "network_id": run.network_id,
            "status": run.status,
            "created_at": run.created_at.isoformat(),
            "mode": run.settings["mode"],
            "port": run.settings.get("port"),
            "snapshot_id": snap.id if snap else None,
            "targets": [
                {
                    "id": t.id,
                    "address": t.address,
                    "depth": t.depth,
                    "status": t.status,
                    "reason": t.reason,
                    "version": t.version,
                    "platform": target_platform(session, t),
                    "evidence_id": session.scalar(
                        select(Observation.id)
                        .where(Observation.target_id == t.id)
                        .order_by(Observation.attempt.desc())
                        .limit(1)
                    ),
                }
                for t in targets
            ],
            "graph": snap.graph if snap else _graph(session, run),
        }


def target_platform(session, target):
    observation = session.scalar(
        select(Observation)
        .where(Observation.target_id == target.id)
        .order_by(Observation.attempt.desc())
        .limit(1)
    )
    return observation.data.get("platform") if observation else None


def networks(owner):
    with sessions() as session:
        return [
            {
                "id": n.id,
                "name": n.name,
                "active_run": n.active_run,
                "current_snapshot": n.current_snapshot,
                "active": n.active,
            }
            for n in session.scalars(
                select(Network)
                .where(Network.owner_id == owner)
                .order_by(Network.created_at.desc())
                .limit(200)
            )
        ]


def runs(owner, network_id):
    with sessions() as session:
        owned(session, Network, network_id, owner)
        return [
            {
                "id": r.id,
                "status": r.status,
                "created_at": r.created_at.isoformat(),
                "snapshot_id": session.scalar(select(Snapshot.id).where(Snapshot.run_id == r.id)),
            }
            for r in session.scalars(
                select(Run)
                .where(Run.network_id == network_id)
                .order_by(Run.created_at.desc())
                .limit(100)
            )
        ]


def snapshot(owner, snapshot_id):
    with sessions() as session:
        row = session.get(Snapshot, snapshot_id)
        if not row:
            raise HTTPException(404, "快照不存在")
        owned(session, Network, row.network_id, owner)
        return row.graph


def snapshot_state(owner, snapshot_id):
    """Independent partial RFC 8343 trees associated with one immutable snapshot.

    Older snapshots are projected from their own observations on read; they are
    never rewritten and never use the current FactRepository or another run.
    """
    with sessions() as session:
        row = session.get(Snapshot, snapshot_id)
        if not row:
            raise HTTPException(404, "快照不存在")
        owned(session, Network, row.network_id, owner)
        state = row.graph.get("state")
        if state is None:
            if "simap" not in row.graph:
                raise HTTPException(422, "该快照格式较旧，请重新发现后查看接口状态")
            collected = {
                node["id"]: {
                    key: node[key]
                    for key in ("address", "evidence_id", "observed_at")
                    if key in node
                }
                for node in row.graph.get("nodes", [])
                if node.get("identity_status") == "collected" and node.get("address")
            }
            observations, targets = _state_inputs(session, row.run_id)
            _, state = enrich_snapshot_state(row.graph["simap"], collected, observations, targets)
        return {"network_id": row.network_id, "snapshot_id": row.id, **state}


def device_model(owner, snapshot_id, address):
    """One observed device's independent native trees and completeness diagnostics."""
    state = snapshot_state(owner, snapshot_id)
    devices = [d for d in state["devices"] if d["address"] == address]
    if len(devices) != 1:
        raise HTTPException(404, "该快照没有此设备的观测")
    return {"network_id": state["network_id"], "snapshot_id": snapshot_id, **devices[0]}


def _hidden_facts():
    """Fact tables left out of analysis (NETOPS_TOPOLOGY_HIDE_FACTS, comma-separated): an
    evaluation control that answers as if they had not been collected. Unset by default."""
    names = os.environ.get("NETOPS_TOPOLOGY_HIDE_FACTS", "")
    return frozenset(name.strip() for name in names.split(",") if name.strip())


def _analyse(session, snap, question, *args, **options):
    """Rebuild the snapshot's facts and answer one read-only question over them."""
    network = session.get(Network, snap.network_id)
    if "simap" not in snap.graph:
        raise HTTPException(422, "该快照格式较旧，不支持定位与路径计算，请重新发现")
    observations = _observations(session, snap.run_id)
    hidden = _hidden_facts()
    devices = {
        o["address"]: {name: value for name, value in _facts(o["data"]).items() if name not in hidden}
        for o in observations
    }
    observed = {
        o["address"]: {"evidence_id": o["id"], "observed_at": o["observed_at"]}
        for o in observations
    }
    try:
        result = question(devices, f"urn:netops:topology:{network.id}:", *args, observed, **options)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        "network_id": network.id,
        "network": network.name,
        "snapshot_id": snap.id,
        "snapshot_model_profile": snap.graph.get("model_profile"),
        "analysis_profile": MODEL_PROFILE,
        **result,
    }


def _locate(session, snap, query):
    return _analyse(session, snap, locate_in, query)


def locate(owner, snapshot_id, query):
    """Where an IP or MAC attaches in one snapshot, with its gateways and evidence."""
    with sessions() as session:
        snap = session.get(Snapshot, snapshot_id)
        if not snap:
            raise HTTPException(404, "快照不存在")
        owned(session, Network, snap.network_id, owner)
        return _locate(session, snap, query)


NO_ACTIVE = "当前没有活跃拓扑：请在网络拓扑 App 中把要查询的网络设为活跃拓扑"


def _activate_if_none(session, owner, network):
    """An owner's first network becomes the active topology; a later one does not."""
    if session.scalar(select(Network.id).where(Network.owner_id == owner, Network.active)):
        return
    try:
        with session.begin_nested():
            network.active = True
            session.flush()
    except IntegrityError:
        network.active = False  # another network became active meanwhile


def activate(owner, network_id):
    """Make one network the owner's active topology, the one the assistant reads."""
    with sessions() as session:
        owned_networks = session.scalars(
            select(Network).where(Network.owner_id == owner).with_for_update()
        ).all()
        chosen = next((n for n in owned_networks if n.id == network_id), None)
        if chosen is None:
            raise HTTPException(404, "网络不存在")
        for network in owned_networks:
            network.active = False
        session.flush()  # clear the old one first: the index allows one active per owner
        chosen.active = True
        session.flush()
    return networks(owner)


def _active(session, owner):
    """The owner's active topology and its current snapshot: all the assistant reads."""
    network = session.scalar(select(Network).where(Network.owner_id == owner, Network.active))
    if network is None:
        raise HTTPException(409, NO_ACTIVE)
    snap = session.get(Snapshot, network.current_snapshot) if network.current_snapshot else None
    if snap is None or "simap" not in snap.graph:
        raise HTTPException(409, f"活跃拓扑“{network.name}”还没有可查询的快照：请先完成一次发现")
    return network, snap


def locate_current(owner, query):
    """The same question over the current snapshot of the owner's active topology."""
    with sessions() as session:
        network, snap = _active(session, owner)
        try:
            result = _locate(session, snap, query)
        except Exception:
            # A failed analysis is reported as unanswered, never as "not found".
            logger.exception("locate failed", extra={"network_id": network.id})
            return [_unanswered(network, snap)]
        return [] if result["status"] == "not_found" else [result]


def _unanswered(network, snap):
    return {
        "network_id": network.id,
        "network": network.name,
        "snapshot_id": snap.id,
        "status": "analysis_failed",
        "note": "该网络的快照分析失败，这里没有它的答案；不能据此推断正常或故障",
    }


PATH_MEANING = (
    "这是根据 {when} 的路由、接口、邻居、ACL 和 NAT 观测推导的候选路径，用于确定后续查证的设备和接口。"
    "complete 仅表示当前推导所需的观测齐备，blocked 表示每条路径都被 ACL 拒绝，"
    "partial 表示存在缺口；三层设备接口上的 IPv4 ACL 和 NAT 已按设备自己的规则计算"
    "（没采到这些表的设备记 policy_not_computed，不在其列），"
    "策略路由只标出可能改变走向的位置；没有验证二层设备上的 ACL、安全策略、"
    "其余二层转发规则或数据面流量，也不等于物理接线。"
    "与现场不同可能来自网络变化、采集覆盖或推导限制，不能单凭差异断定发生了变更。"
)


def _observed_times(value):
    if isinstance(value, dict):
        own = [value["observed_at"]] if isinstance(value.get("observed_at"), str) else []
        return own + [t for item in value.values() for t in _observed_times(item)]
    if isinstance(value, list):
        return [t for item in value for t in _observed_times(item)]
    return []


def _as_history(result):
    """Describe the observation window and the limits of the path inference."""
    times = sorted(beijing(datetime.fromisoformat(t)) for t in set(_observed_times(result)))
    if not times:
        when = "采集时间未知"
    else:
        when = (times[0] if len(times) == 1 else f"{times[0]} 至 {times[-1]}") + "（北京时间）"
    return {**result, "meaning": PATH_MEANING.format(when=when)}


def trace(owner, snapshot_id, src, dst, src_vrf=None, dst_vrf=None, flow=None):
    """Forwarding paths src->dst and dst->src in one snapshot, ending at data gaps;
    ``flow``: the protocol and ports ACLs are checked with ({} leaves them open)."""
    with sessions() as session:
        snap = session.get(Snapshot, snapshot_id)
        if not snap:
            raise HTTPException(404, "快照不存在")
        owned(session, Network, snap.network_id, owner)
        return _as_history(
            _analyse(
                session,
                snap,
                trace_in,
                src,
                dst,
                src_vrf=src_vrf,
                dst_vrf=dst_vrf,
                **(flow or {}),
            )
        )


def trace_current(owner, src, dst, src_vrf=None, dst_vrf=None, flow=None):
    """The same question over the current snapshot of the owner's active topology."""
    with sessions() as session:
        network, snap = _active(session, owner)
        try:
            result = _analyse(
                session,
                snap,
                trace_in,
                src,
                dst,
                src_vrf=src_vrf,
                dst_vrf=dst_vrf,
                **(flow or {}),
            )
        except HTTPException:
            raise  # the question itself is invalid (an address, a port)
        except Exception:
            logger.exception("trace failed", extra={"network_id": network.id})
            return [_unanswered(network, snap)]
        if {result["forward"]["status"], result["reverse"]["status"]} == {"no_gateway"}:
            return []  # neither end is known to this network
        return [_as_history(result)]


def _services(devices, base, observed):
    return project_services(devices, base)


def services(owner, snapshot_id):
    """RFC 9182 L3NM and RFC 9291 L2NM services read from one snapshot's observations."""
    with sessions() as session:
        snap = session.get(Snapshot, snapshot_id)
        if not snap:
            raise HTTPException(404, "快照不存在")
        owned(session, Network, snap.network_id, owner)
        return _analyse(session, snap, _services)


def services_current(owner):
    """The same projection over the current snapshot of the owner's active topology."""
    with sessions() as session:
        _, snap = _active(session, owner)
        result = _analyse(session, snap, _services)
        return [result] if result["l3nm"]["tree"] or result["l2nm"]["tree"] else []


def compare(owner, before_id, after_id):
    before, after = snapshot(owner, before_id), snapshot(owner, after_id)
    if before["network_id"] != after["network_id"]:
        raise HTTPException(422, "只能比较同一网络的快照")
    return diff(before, after)


def export(owner, snapshot_id):
    graph = snapshot(owner, snapshot_id)
    # Snapshots before the SIMAP builder keep their original RFC 8345 base export.
    return graph["simap"] if "simap" in graph else rfc8345(graph)


def collection_issues(owner, network_id=None, include_cleared=False):
    """What nodes could not give over SSH or NETCONF, newest first (issues.py)."""
    with sessions() as session:
        if network_id:
            owned(session, Network, network_id, owner)
        return issues.listing(
            session, owner, network_id=network_id, include_cleared=include_cleared
        )


def evidence(owner, observation_id):
    with sessions() as session:
        row = session.get(Observation, observation_id)
        if not row:
            raise HTTPException(404, "证据不存在")
        owned(session, Run, row.run_id, owner)
        key = row.evidence_key
    return storage.get(key)


def resolve(owner, run_id, target_id, body):
    ref = stale = None
    try:
        with sessions() as session:
            run = session.scalar(
                select(Run).where(Run.id == run_id, Run.owner_id == owner).with_for_update()
            )
            if not run:
                raise HTTPException(404, "任务不存在")
            target = session.get(Target, target_id)
            if not target or target.run_id != run.id:
                raise HTTPException(404, "目标不存在")
            digest = signature(body.model_dump(exclude={"credentials"}))
            old = session.get(Resolution, (target_id, body.request_id))
            if old:
                if old.input_hash != digest:
                    raise HTTPException(409, "请求编号已用于不同操作")
            else:
                if (
                    run.status not in ACTIVE
                    or target.status != "waiting_for_input"
                    or target.version != body.version
                ):
                    raise HTTPException(409, "节点状态已经变化，请刷新")
                if body.action == "retry":
                    if body.credentials:
                        # Only this node retries with the newly typed login.
                        ref = logins.put(owner, [target.address], body.credentials)
                        stale, target.credential_ref = target.credential_ref, ref
                    if body.profile_id is not None:
                        run.settings = {
                            **run.settings,
                            "target_profiles": {
                                **run.settings.get("target_profiles", {}),
                                target.id: body.profile_id,
                            },
                        }
                    target.status, target.reason = "queued", None
                else:
                    target.status, target.reason = "skipped", "human_skipped"
                target.version += 1
                session.add(
                    Resolution(target_id=target_id, request_id=body.request_id, input_hash=digest)
                )
                _finish(session, run)
    except Exception:
        if ref:
            CredentialStore().revoke(ref)
        raise
    if stale:
        try:
            CredentialStore().revoke(stale)
        except Exception:
            logger.warning("topology credential revocation deferred to TTL for run %s", run_id)
    dispatch(run_id)
    release_credentials(run_id)
    return read(owner, run_id)


def cancel(owner, run_id):
    with sessions() as session:
        run = session.scalar(
            select(Run).where(Run.id == run_id, Run.owner_id == owner).with_for_update()
        )
        if not run:
            raise HTTPException(404, "任务不存在")
        if run.status in ACTIVE:
            session.execute(
                update(Target)
                .where(Target.run_id == run_id, Target.status.in_(ACTIVE))
                .values(status="cancelled", lease_token=None, version=Target.version + 1)
            )
            _finish(session, run, "cancelled")
    release_credentials(run_id)
    return read(owner, run_id)


def _recorder():
    outputs, command_metadata = {}, []

    def capture(item):
        outputs[item.command_id] = item.output
        command_metadata.append(
            {key: value for key, value in asdict(item).items() if key != "output"}
        )

    return outputs, command_metadata, capture


def _failure(exc):
    return exc.failure_code if isinstance(exc, DeviceAccessError) else "execution_unavailable"


def _collect_lldp(job, choice):
    """Manual platform override: the original single-session LLDP plan."""
    plan = DiscoveryPlan(choice)
    outputs, command_metadata, capture = _recorder()
    data, reason = {"complete": False}, None
    try:
        commands = plan.commands
        access = _access(job)
        result = access.execute_batch(
            AccessContext(
                job.owner,
                APP_ID,
                (AccessGrant(job.target_id, job.target_id, frozenset(c.text for c in commands)),),
                job.run_id,
            ),
            BatchRequest(job.target_id, job.target_id, commands, budget_seconds=90),
            on_result=capture,
            should_continue=job.guard,
            command_filter=plan.select,
        )
        if result.execution_status != "succeeded":
            raise DeviceAccessError(
                result.failure_code or "output_incomplete", "设备采集未完整完成"
            )
        data = parse(plan.profile_id, plan.parser_outputs(outputs))
    except Exception as exc:
        reason = _failure(exc)
    return outputs, command_metadata, {**data, "platform": plan.metadata()}, reason


def _access(job):
    """One login for this node; a first-seen host key is recorded, a changed one stops it."""
    username, password = logins.resolve(
        job.owner, job.address, job.ref, job.settings.get("credential_ref")
    )
    trust = HostKeyStore()
    profile = ConnectionProfile(
        profile_id=job.target_id, username=username, password=password, port=job.settings["port"]
    )
    return DeviceAccessService(
        target_resolver=lambda _: AccessTarget(
            job.target_id, job.address, management_domain=DOMAIN
        ),
        profile_resolver=lambda _: profile,
        driver=ParamikoDriver(
            host_key_verifier=lambda target, port, key: trust.verify(
                target.host, port, key, auto_trust=True
            )
        ),
    )


def _collect_topology(job, choice):
    """Identify the platform, then collect every topology fact in one node budget."""
    outputs, command_metadata, capture = _recorder()
    data = {"complete": False, "platform": {"source": "version_probe", "status": "failed"}}
    reason = None
    try:
        access = _access(job)
        context = AccessContext(
            job.owner,
            APP_ID,
            (
                AccessGrant(job.target_id, job.target_id, command_allowlist()),
                # NETCONF first for the tables netconf-api publishes, on the same login.
                AccessGrant(job.target_id, job.target_id, action="device.netconf"),
            ),
            job.run_id,
        )
        trust = HostKeyStore()
        result = collect_node(
            access,
            context,
            job.target_id,
            job.target_id,
            mode="topology",
            budget_seconds=NODE_BUDGET_SECONDS,
            should_continue=job.guard,
            on_result=capture,
            known_hosts_provider=lambda target, port: trust.known_hosts(target.host, port),
            scoped_context=lambda capability, scopes, family: _scoped_context(
                context, job.target_id, job.target_id, scopes, capability, family
            ),
            netconf=job.settings.get("netconf", "prefer"),
        )
        data = topology_observation(result)
        reason = data.get("failure_code")
    except Exception as exc:
        reason = _failure(exc)
    return outputs, command_metadata, data, reason


def _scoped_context(context, target_ref, connection_profile_id, scopes, capability, family):
    """Issue exact backend grants for a bounded, validated per-VRF expansion.

    Target and connection come from the already authorized job, which must already be
    allowed the global command. Device-provided VRF names cannot introduce command
    syntax or broaden the target scope.
    """
    from src.platform.device_access.contracts import Command
    from src.platform.device_access.policy import authorize

    authorize(
        context,
        BatchRequest(
            target_ref,
            connection_profile_id,
            # The command the expansion extends (collect.scope_base); a Comware instance
            # detail extends the instance enumeration itself.
            (Command(capability, scope_base(capability, family)),),
        ),
    )
    if len(scopes) > 64:
        raise ValueError("VRF collection scope exceeds node limit")
    commands = frozenset(scoped_command(capability, scope, family) for scope in scopes)
    return replace(
        context,
        grants=(*context.grants, AccessGrant(target_ref, connection_profile_id, commands)),
    )


def topology_observation(result):
    """Parsed facts for the SIMAP builder plus LLDP neighbours for expansion.

    LLDP must be complete: it drives expansion and device adjacency. Other facts
    degrade the topology (no hosts or subnets) instead of failing the node.
    """
    status = {cap: parsed.status for cap, parsed in result.parsed.items()}
    facts = {
        cap: parsed.fact_summary()
        if cap in {"ospf_interfaces", "ospf_process"}
        else parsed.summary
        if cap in {"version", "lldp_local", "trunk", "switchport"}
        else parsed.records
        for cap, parsed in result.parsed.items()
        if cap in TOPOLOGY_FACTS
        and (
            parsed.status == "complete"
            # A partial binding table cannot say an interface has none; it stays unobserved.
            or (parsed.status == "partial" and cap not in POLICY_FACTS)
        )
    }
    for cap in ("bgp_peers", "bgp_vpnv4", "bgp_evpn"):
        if cap in facts:
            # The local identity (router ID, AS) and the peer rows belong together.
            facts[cap] = {**result.parsed[cap].summary, "peers": facts[cap]}
    vlan = result.parsed.get("vlan")
    if vlan is not None and vlan.status in {"complete", "partial"} and "members" in vlan.summary:
        facts["vlan_members"] = vlan.summary["members"]
    lldp = result.parsed.get("lldp")
    complete = lldp is not None and lldp.status == "complete"
    neighbors = [
        {
            "local_port": rec["local_if"],
            "chassis_id": rec["remote_chassis_id"],
            "chassis_subtype": rec.get("remote_chassis_subtype") or "unknown",
            "port_subtype": rec.get("remote_port_subtype") or "unknown",
            "remote_port": rec["remote_port_id"],
            "name": rec.get("remote_sysname") or rec["remote_chassis_id"],
            "system_description": (rec.get("remote_system_description") or None),
            "addresses": sorted(set(rec.get("remote_mgmt_addresses", []))),
        }
        for rec in (lldp.records if complete else [])
    ]
    return {
        "complete": complete,
        "parser": "topology-facts-v1",
        "neighbors": neighbors,
        "facts": facts,
        "capabilities": status,
        "fact_metadata": {
            cap: {
                "tier": parsed.tier,
                "confidence": parsed.confidence,
                "warnings": parsed.warnings,
                "sources": parsed.sources,
                "line_coordinates": "parser_text_v1_1based",
                "candidate_count": parsed.candidate_count,
                "rejected_count": parsed.rejected_count,
                **(
                    {"scope_coverage": parsed.summary["scope_coverage"]}
                    if "scope_coverage" in parsed.summary
                    else {}
                ),
                # Read over NETCONF, fallen back from it, or compared with it.
                **({"netconf": parsed.netconf} if parsed.netconf else {}),
            }
            for cap, parsed in result.parsed.items()
            if cap in TOPOLOGY_FACTS
        },
        # NETCONF tables as standard records, for the standard trees (netconf-api).
        **({"standard": result.standard} if getattr(result, "standard", None) else {}),
        "failure_code": None
        if complete
        else (
            "parse_incomplete" if lldp is not None and lldp.status == "partial" else "parse_failed"
        ),
        "platform": {
            "profile_id": result.profile_id,
            "source": "version_probe",
            "status": "identified",
            "mode": result.mode,
            "driver": result.driver,
            "identity": asdict(result.identity),
        },
    }


def execute_run(run_id, authority):
    def authorize_owner(owner):
        decision = authority(owner, APP_ID)
        if decision is not None and not decision.can_execute_skills():
            raise HTTPException(403, "当前账号不能执行设备采集")

    with sessions() as session:
        run = session.get(Run, run_id)
        if not run or run.status not in ACTIVE:
            return
        owner = run.owner_id
    try:
        authorize_owner(owner)
    except Exception:
        # Authorization loss suspends the run; never repeatedly attempt a device.
        with sessions() as session:
            session.execute(
                update(Target)
                .where(Target.run_id == run_id, Target.status == "queued")
                .values(
                    status="waiting_for_input",
                    reason="authorization_unavailable",
                    version=Target.version + 1,
                )
            )
            run = session.get(Run, run_id)
            if run and run.status in ACTIVE:
                _finish(session, run)
        return
    with sessions() as session:
        run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if run.status not in ACTIVE:
            return
        target = session.scalar(
            select(Target)
            .where(Target.run_id == run_id, Target.status == "queued")
            .order_by(Target.depth, Target.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if target is None:
            return
        token = uuid4().hex
        target.status, target.lease_token, target.lease_until = (
            "running",
            token,
            now() + timedelta(seconds=LEASE_SECONDS),
        )
        run.status = "running"
        target_id, address, attempt, depth, ref = (
            target.id,
            target.address,
            target.version,
            target.depth,
            target.credential_ref,
        )
        settings = dict(run.settings)

    def guard():
        authorize_owner(owner)
        with sessions() as session:
            return (
                session.scalar(
                    select(Target.id)
                    .join(Run)
                    .where(
                        Target.id == target_id,
                        Target.lease_token == token,
                        Target.status == "running",
                        Target.lease_until > now(),
                        Run.status.in_(ACTIVE),
                    )
                )
                is not None
            )

    choice = settings.get("target_profiles", {}).get(
        target_id, settings.get("profile_id", "auto") if depth == 0 else "auto"
    )
    job = SimpleNamespace(
        owner=owner,
        run_id=run_id,
        target_id=target_id,
        address=address,
        ref=ref,
        settings=settings,
        guard=guard,
    )
    # Automatic identification collects every topology fact; a human-chosen platform
    # keeps the LLDP-only plan because collect_node never skips identification.
    collector = _collect_topology if choice == "auto" else _collect_lldp
    try:
        proceed = guard()
    except Exception as exc:
        proceed = True
        outputs, command_metadata, reason = {}, [], _failure(exc)
        data = {"complete": False, "platform": {"source": "version_probe", "status": "failed"}}
    else:
        if not proceed:
            return
        outputs, command_metadata, data, reason = collector(job, choice)
    observed_at = now()
    try:
        key = storage.put(
            run_id,
            target_id,
            attempt,
            {
                "outputs": outputs,
                "commands": command_metadata,
                "observed_at": observed_at.isoformat(),
                "profile_id": data["platform"].get("profile_id"),
                "platform": data["platform"],
                "complete": data.get("complete", False),
                "failure_code": reason,
            },
        )
    except Exception:
        key, reason, data = None, "evidence_unavailable", {"complete": False}
    with sessions() as session:
        run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        target = session.get(Target, target_id)
        if run.status not in ACTIVE or target.lease_token != token or target.status != "running":
            return
        expiry = (
            target.lease_until.replace(tzinfo=timezone.utc)
            if target.lease_until.tzinfo is None
            else target.lease_until
        )
        if expiry <= now():
            return
        target.lease_token, target.lease_until = None, None
        target.status, target.reason = (
            ("waiting_for_input", reason) if reason else ("succeeded", None)
        )
        observation_id = uuid4().hex if key else None
        if key:
            session.add(
                Observation(
                    id=observation_id,
                    run_id=run_id,
                    target_id=target_id,
                    attempt=attempt,
                    observed_at=observed_at,
                    data=data,
                    evidence_key=key,
                )
            )
        issues.record(
            session,
            owner=owner,
            network_id=run.network_id,
            run_id=run_id,
            observation_id=observation_id,
            address=address,
            data=data,
            reason=reason,
        )
        if not reason:
            count = session.scalar(
                select(func.count()).select_from(Target).where(Target.run_id == run_id)
            )
            for neighbor in data["neighbors"]:
                for candidate in neighbor["addresses"]:
                    if (
                        depth >= settings["max_depth"]
                        or count >= settings["max_targets"]
                        or not scope(candidate, settings["allowed_cidrs"])
                    ):
                        continue
                    target_key = f"{DOMAIN}|{candidate}|{settings['port']}"
                    if session.scalar(
                        select(Target.id).where(
                            Target.run_id == run_id, Target.target_key == target_key
                        )
                    ):
                        continue
                    # New neighbours use the run's login, like the seeds.
                    session.add(
                        Target(
                            id=uuid4().hex,
                            run_id=run_id,
                            target_key=target_key,
                            address=candidate,
                            depth=depth + 1,
                            status="queued",
                        )
                    )
                    session.flush()
                    count += 1
        session.flush()
        _finish(session, run)
    release_credentials(run_id)
    dispatch(run_id)


def import_captures(owner, body):
    if (
        sum(len(value.encode()) for capture in body.captures for value in capture.outputs.values())
        > 4 * 1024 * 1024
    ):
        raise HTTPException(413, "一次离线观测导入不能超过 4 MB")
    if len({c.address for c in body.captures}) != len(body.captures):
        raise HTTPException(422, "一次导入不能包含重复设备地址")
    # Validate the entire bundle before creating its task.
    parsed = [
        parse(body.profile_id, capture.outputs, capture.complete) for capture in body.captures
    ]
    settings = body.model_dump(exclude={"request_id", "captures"})
    settings["capture_digest"] = signature([c.model_dump() for c in body.captures])
    with sessions() as session:
        run, created = _new_run(session, owner, body, settings, "offline")
        run_id = run.id
        if created:
            for capture, data in zip(body.captures, parsed):
                target = Target(
                    id=uuid4().hex,
                    run_id=run_id,
                    address=capture.address,
                    target_key=f"{DOMAIN}|{capture.address}",
                    depth=0,
                    status="succeeded",
                    version=1,
                )
                session.add(target)
                session.flush()
                key = storage.put(
                    run_id,
                    target.id,
                    1,
                    {"outputs": capture.outputs, "complete": True, "source": "user_import"},
                )
                session.add(
                    Observation(
                        id=uuid4().hex,
                        run_id=run_id,
                        target_id=target.id,
                        attempt=1,
                        observed_at=datetime.fromisoformat(capture.observed_at),
                        data={**data, "source": "user_import"},
                        evidence_key=key,
                    )
                )
            session.flush()
            _finish(session, run)
    return read(owner, run_id)


def reconcile():
    with sessions() as session:
        # A lost executor may have reached the device. Only a human may authorize another attempt.
        session.execute(
            update(Target)
            .where(Target.status == "running", Target.lease_until < now())
            .values(
                status="waiting_for_input",
                reason="worker_interrupted",
                lease_token=None,
                version=Target.version + 1,
            )
        )
        ids = []
        for run in session.scalars(select(Run).where(Run.status.in_(ACTIVE)).with_for_update()):
            _finish(session, run)
            if run.status == "running":
                ids.append(run.id)
    for run_id in ids:
        dispatch(run_id)
    return len(ids)


def pending():
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(Run).where(Run.status.in_(ACTIVE)))


def drain():
    with sessions() as session:
        items = [
            (r.owner_id, r.id) for r in session.scalars(select(Run).where(Run.status.in_(ACTIVE)))
        ]
    for owner, run_id in items:
        cancel(owner, run_id)
    return pending()

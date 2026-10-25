"""Traceable per-device policy plan, built before factoring or CLI rendering."""

import json
from collections import defaultdict
from hashlib import sha256

from policy_optimizer import Rule, optimize
from policy_sets import address_text, addresses, port_text, ports, union
from policy_window import display, extend, settings


def _setting(row, key):
    """A window or long-link cell of the request frame; absent in older frames."""
    value = row.get(key)
    if value is None or isinstance(value, float) or value == "":
        return None
    return int(value) if key == "long_link_hours" else tuple(value)


def digest(value):
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def rule_view(rule):
    window, hours = settings(rule.context)
    return {
        "id": digest(rule.key)[:16],
        "device": rule.context[0],
        "network_context": rule.context[1],
        "source_zone": rule.context[2],
        "destination_zone": rule.context[3],
        "protocol": rule.context[4],
        "sources": address_text(rule.sources),
        "destinations": address_text(rule.destinations),
        "ports": port_text(rule.ports) if rule.context[4] != "icmp" else [],
        "action": "permit",
        "valid_from": window[0] if window else None,
        "valid_until": window[1] if window else None,
        "validity": display(window),
        "long_link_hours": hours,
        "evidence_ids": list(rule.refs),
    }


def build_plan(df, topology, ticket_id, merge=True):
    original, evidence, requests = [], [], []
    for _, row in df.iterrows():
        row_number = int(row["_row"])
        request = {key: str(row[key]) for key in ("src_ip", "dst_ip", "port", "proto", "action")}
        request.update(row.get("_original", {}))
        request["row"] = row_number
        window, hours = _setting(row, "window"), _setting(row, "long_link_hours")
        request["validity"] = display(window)
        request["long_link_hours"] = hours
        requests.append(request)
        try:
            source_parts = topology.split_ownership(addresses(row["src_ip"]))
            destination_parts = topology.split_ownership(addresses(row["dst_ip"]))
            groups = []
            for parts in (source_parts, destination_parts):
                by_owner = defaultdict(list)
                for part in parts:
                    by_owner[part["owner"]].append(part)
                groups.append(by_owner)
            for source, src in sorted(groups[0].items()):
                for destination, dst in sorted(groups[1].items()):
                    path = topology.find_shortest_path(source, destination)
                    if not path:
                        raise ValueError(f"没有从 {source} 到 {destination} 的拓扑路径")
                    hops = [(a, b) for a, b in zip(path, path[1:]) if a[0] == b[0]]
                    if not hops:
                        raise ValueError("路径没有需要配置的域间策略，请人工确认同域处理方式")
                    for a, b in zip(path, path[1:]):
                        if not topology.check_global_acl(*a, *b):
                            raise ValueError(f"全局 ACL 禁止 {a} → {b}")
                    ref = f"E{len(evidence) + 1:05d}"
                    evidence.append(
                        {
                            "id": ref,
                            "row": row_number,
                            "path": path,
                            "path_source": "explicit"
                            if (source, destination) in topology.explicit_paths
                            else "topology",
                            "source_segments": [
                                {**p, "addresses": address_text((p["interval"],))} for p in src
                            ],
                            "destination_segments": [
                                {**p, "addresses": address_text((p["interval"],))} for p in dst
                            ],
                        }
                    )
                    for proto in str(row["proto"]).split("/"):
                        port_set = ports(row["port"]) if proto != "icmp" else ((0, 1),)
                        for a, b in hops:
                            original.append(
                                Rule(
                                    # Long link is TCP only; the UDP half of tcp/udp keeps none.
                                    extend(
                                        (a[0], topology.network_context, a[1], b[1], proto),
                                        window,
                                        hours if proto == "tcp" else None,
                                    ),
                                    union(p["interval"] for p in src),
                                    union(p["interval"] for p in dst),
                                    port_set,
                                    (ref,),
                                )
                            )
                            if len(original) > 10000:
                                raise ValueError("分段后规则超过 10000 条，请拆分申请")
        except ValueError as exc:
            raise ValueError(f"申请表第 {row_number} 行：{exc}") from exc
    optimized, verification = optimize(original, enabled=merge)
    plan = {
        "schema_version": 1,
        "ticket_id": ticket_id,
        "network_context": topology.network_context,
        "requests": requests,
        "evidence": evidence,
        "original_rules": [rule_view(r) for r in original],
        "rules": [rule_view(r) for r in optimized],
        "optimization": verification,
        "warnings": topology.warnings,
    }
    return plan, optimized


def vendor_rules(rules, ticket_id):
    result = defaultdict(list)
    for rule in rules:
        window, hours = settings(rule.context)
        result[rule.context[0]].append(
            {
                "rule_key": (rule.context[2], rule.context[3]),
                "sources": address_text(rule.sources),
                "destinations": address_text(rule.destinations),
                "ports": port_text(rule.ports) if rule.context[4] != "icmp" else [],
                "proto": rule.context[4],
                "action": "permit",
                "ticket_id": ticket_id,
                "window": window,
                "long_link": hours,
            }
        )
    return result

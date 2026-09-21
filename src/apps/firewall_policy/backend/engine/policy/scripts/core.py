"""Topology ownership and paths. Ownership is calculated at prefix boundaries."""

import heapq
import json
from collections import defaultdict
from ipaddress import IPv4Address, IPv4Network
from types import SimpleNamespace

from policy_sets import addresses


class NetworkTopology:
    def __init__(self, json_path):
        for encoding in ("utf-8-sig", "gbk"):
            try:
                with open(json_path, encoding=encoding) as stream:
                    data = json.load(stream)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("拓扑文件编码无效")
        self.firewalls = {}
        self.graph = defaultdict(set)
        self.explicit_paths = {}
        self.global_acl = data.get("global_acl", [])
        self.network_context = data.get("network_context", "default")
        self.warnings = []
        self.prefixes = []
        for fw in data.get("firewalls", []):
            name = fw["name"]
            if name in self.firewalls:
                raise ValueError(f"拓扑设备重复：{name}")
            domains = {}
            for domain in fw.get("security_domains", []):
                zone = domain["name"]
                if zone in domains:
                    raise ValueError(f"安全域重复：{name}.{zone}")
                domains[zone] = domain
                for value in domain.get("ip_ranges", []):
                    net = IPv4Network(value, strict=False)
                    self.prefixes.append(
                        (
                            int(net.network_address),
                            int(net.broadcast_address) + 1,
                            net.prefixlen,
                            (name, zone),
                            str(net),
                        )
                    )
            if not domains:
                raise ValueError(f"防火墙 {name} 未配置安全域")
            self.firewalls[name] = SimpleNamespace(name=name, type=fw["type"], domains=domains)
        if not self.firewalls:
            raise ValueError("拓扑文件中未定义任何防火墙")
        self.nodes = {(fw.name, zone) for fw in self.firewalls.values() for zone in fw.domains}
        for fw in self.firewalls.values():
            for zone, domain in fw.domains.items():
                node = (fw.name, zone)
                internal = domain.get("intra_connections")
                if internal is None:
                    # Preserve existing files. Explicit [] means no internal edges.
                    internal = [other for other in fw.domains if other != zone]
                    if internal:
                        self.warnings.append(
                            f"{fw.name}.{zone} 沿用旧拓扑的域间互连；路径为拓扑推导，请人工核对"
                        )
                for other in internal:
                    target = (fw.name, other)
                    if target not in self.nodes:
                        raise ValueError(f"拓扑安全域不存在：{target}")
                    self.graph[node].add(target)
                for connection in domain.get("connected_firewalls", []):
                    target = (connection["firewall"], connection["via_domain"])
                    if target not in self.nodes:
                        self.warnings.append(
                            f"未使用指向不存在设备/安全域的连接：{node} → {target}"
                        )
                        continue
                    self.graph[node].add(target)
                    self.graph[target].add(node)
        for entry in data.get("explicit_paths", []):
            start = (entry["start"]["firewall"], entry["start"]["domain"])
            end = (entry["end"]["firewall"], entry["end"]["domain"])
            path = tuple(tuple(node) for node in entry["path"])
            if (start, end) in self.explicit_paths:
                raise ValueError("同一源/目的安全域配置了重复指定路径")
            self.explicit_paths[(start, end)] = path
            if not self._valid_path(start, end, path):
                self.warnings.append(f"指定路径 {start} → {end} 无效；申请匹配该路径时将阻断生成")

    def _valid_path(self, start, end, path):
        return (
            bool(path)
            and path[0] == start
            and path[-1] == end
            and len(path) == len(set(path))
            and all(node in self.nodes for node in path)
            and all(b in self.graph[a] for a, b in zip(path, path[1:]))
        )

    def split_ownership(self, intervals):
        result = []
        for low, high in intervals:
            candidates = [p for p in self.prefixes if p[0] < high and p[1] > low]
            bounds = sorted(
                {low, high}
                | {max(low, p[0]) for p in candidates}
                | {min(high, p[1]) for p in candidates}
            )
            for a, b in zip(bounds, bounds[1:]):
                matches = [p for p in candidates if p[0] <= a < p[1]]
                if not matches:
                    raise ValueError(
                        f"地址 {IPv4Address(a)}-{IPv4Address(b - 1)} 无法匹配拓扑中的安全域"
                    )
                longest = max(p[2] for p in matches)
                winners = [p for p in matches if p[2] == longest]
                owners = {p[3] for p in winners}
                if len(owners) != 1:
                    raise ValueError(
                        f"地址 {IPv4Address(a)}-{IPv4Address(b - 1)} 的安全域归属冲突：{sorted(owners)}"
                    )
                result.append({"interval": (a, b), "owner": winners[0][3], "prefix": winners[0][4]})
        return result

    def find_ip_owner(self, value):
        segments = self.split_ownership(addresses(value))
        owners = {part["owner"] for part in segments}
        return next(iter(owners)) if len(owners) == 1 else None

    def find_shortest_path(self, start, end):
        if (start, end) in self.explicit_paths:
            path = self.explicit_paths[(start, end)]
            if not self._valid_path(start, end, path):
                raise ValueError("指定路径的节点、连接、起终点或环路校验失败")
            return list(path)
        heap = [(0, start, ())]
        visited = set()
        while heap:
            cost, node, path = heapq.heappop(heap)
            if node in visited:
                continue
            visited.add(node)
            path = (*path, node)
            if node == end:
                return list(path)
            for neighbor in sorted(self.graph[node]):
                if neighbor not in visited:
                    heapq.heappush(
                        heap, (cost + (1 if node[0] == neighbor[0] else 5), neighbor, path)
                    )
        return []

    def check_global_acl(self, src_fw, src_zone, dst_fw, dst_zone):
        for acl in self.global_acl:
            if (
                acl.get("source_fw"),
                acl.get("source_zone"),
                acl.get("destination_fw"),
                acl.get("destination_zone"),
            ) == (src_fw, src_zone, dst_fw, dst_zone):
                return acl.get("action", "permit").lower() != "deny"
        return True

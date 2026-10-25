"""Endpoint location: where does an IP or MAC attach, and which router serves its subnet.

Reuses the builder's attachment decision: a host's L2 link in the built tree is its
attachment. The link's other ends classify the answer:

- one device port: ``confirmed``;
- several observed ports, or a port learning other MACs: ``shared_segment``
  (shared attachment evidence, not proof of one broadcast domain or a hidden switch);
- a port on a device that was only advertised: ``behind_uncollected``;
- no attachment evidence: ``not_found``;
- an address or MAC of a collected device itself: ``device``.
- a VRRP virtual address: ``virtual_router``, every member with its RFC 8347 state.
- one IP answered by several MACs: ``conflict``, every MAC located.

A port is backed by the MAC table when a MAC row names it, otherwise it is inferred
from ARP (``basis``). Nothing is guessed: missing facts give ``not_found``.
"""

from __future__ import annotations

import ipaddress

from ..collectors.tiers.common import valid_mac
from .builder import L2T, NT, ST, _Topology

# A model once read the subnet's router interface as the host's own gateway and
# ruled out a misconfigured host on that "fact". Say plainly what is not seen.
HOST_SETTINGS_UNSEEN = (
    "router_interfaces 是该网段在路由器上的三层接口；终端自己配置的网关、掩码看不到，不能当作已证实"
)
VIRTUAL_ROUTER_NOTE = (
    "VRRP 虚拟地址（RFC 5798/8347）：由组内 state 为 master 的成员应答；"
    "各成员的 state 与 last_adv_source（主设备地址）取自采集时各自的 VRRP 状态"
)


def _query(value):
    value = value.strip()
    try:
        return "ip", str(ipaddress.ip_address(value))
    except ValueError:
        mac = valid_mac(value)
        if mac:
            return "mac", mac
    raise ValueError("请输入 IP 地址或 MAC 地址")


class _Locator:
    def __init__(self, devices, base, observed):
        self.devices, self.base, self.observed = devices, base, observed
        self.topo = _Topology(devices, base)
        tree, self.evidence = self.topo.build()
        networks = tree["ietf-network:networks"]["network"]
        self.l2 = next(n for n in networks if n["network-id"] == base + "l2")
        self.nodes = {n["node-id"]: n for n in self.l2["node"]}
        self.collected = {
            self.topo.device_node(chassis): key for key, chassis in self.topo.chassis.items()
        }

    def device(self, node_id):
        node = self.nodes.get(node_id, {})
        name = node.get(L2T + "l2-node-attributes", {}).get("name") or node_id
        key = self.collected.get(node_id)
        item = {"node_id": node_id, "name": name, "address": key, "collected": key is not None}
        if key is not None:
            # The identified platform lets a diagnosis log in with the right command policy.
            item["platform"] = self.devices[key].get("platform") or None
        if key in self.observed:
            item.update(self.observed[key])
        return item

    def vrrp(self):
        """VRRP instances (RFC 8347 state) on collected devices: [(device key, record)]."""
        return [
            (key, rec)
            for key, facts in sorted(self.devices.items())
            for rec in facts.get("vrrp", [])
        ]

    def virtual_router(self, ip):
        """Every collected member of the VRRP groups that hold this virtual address."""
        return [
            {
                **self.device(self.topo.device_node(self.topo.chassis[key])),
                "interface": rec["ifname"],
                "vrid": rec["vrid"],
                "state": rec["state"],
                "priority": rec.get("priority"),
                "last_adv_source": rec.get("last_adv_source"),
            }
            for key, rec in self.vrrp()
            if ip in rec["virtual_addresses"]
        ]

    def router_interfaces(self, ip):
        found = []
        address = ipaddress.ip_address(ip)
        for key, facts in sorted(self.devices.items()):
            for rec in facts.get("ip", []):
                network = ipaddress.ip_interface(f"{rec['address']}/{rec['prefix_len']}").network
                if network.prefixlen < network.max_prefixlen and address in network:
                    found.append(
                        {
                            **self.device(self.topo.device_node(self.topo.chassis[key])),
                            "interface": rec["ifname"],
                            "interface_ip": rec["address"],
                            "subnet": str(network),
                            "vrf": rec.get("vrf", "default"),
                        }
                    )
        return found

    def own(self, kind, value):
        """All matching device-local identities; an IP is not a global identity.

        RFC 8529 network-instance names are device-local. A MAC learned by ARP
        identifies an owner only when that observation establishes its adjacency.
        """
        if kind == "ip":
            candidates = self.topo.ip_candidates(value)
        else:
            if value in self.topo.ambiguous_macs:
                return []
            candidates = {
                candidate
                for key, facts in self.devices.items()
                for rec in facts.get("arp", [])
                if rec["mac"] == value
                for candidate in self.topo.neighbour_candidates(
                    key, rec.get("vrf", "default"), rec["ip"], rec["ifname"]
                )
            }
        return [
            {
                "status": "device",
                "device": self.device(self.topo.device_node(self.topo.chassis[key])),
                "interface": ifname,
                "vrf": vrf,
            }
            for key, ifname, vrf in sorted(candidates)
        ]

    def arp(self, ip=None, mac=None):
        rows = []
        for key, facts in sorted(self.devices.items()):
            for rec in facts.get("arp", []):
                if (ip and rec["ip"] == ip) or (mac and rec["mac"] == mac):
                    rows.append({"device": key, **rec})
        return rows

    def attachment(self, mac):
        host = self.topo.host_node(mac)
        nic = self.topo.nic_tp(mac)
        link = next(
            (
                link
                for link in self.l2.get(NT + "link", [])
                if any(t["node-ref"] == host and t["tp-ref"] == nic for t in link[ST + "tp"])
            ),
            None,
        )
        if link is None:
            return None
        ports = []
        for end in link[ST + "tp"]:
            if end["node-ref"].startswith(self.base + "host:"):
                continue
            device = self.device(end["node-ref"])
            tp = next(
                t
                for t in self.nodes[end["node-ref"]][NT + "termination-point"]
                if t["tp-id"] == end["tp-ref"]
            )
            port = tp[L2T + "l2-termination-point-attributes"]["interface-name"]
            basis, vlan = "arp", None
            facts = self.devices.get(device["address"]) or {}
            for rec in facts.get("mac", []):
                if rec["mac"] == mac and rec["port"] == port:
                    basis, vlan = "mac", rec.get("vlan_or_bd")
            ports.append(
                {**device, "port": port, "tp_id": end["tp-ref"], "basis": basis, "vlan": vlan}
            )
        if any(not p["collected"] for p in ports):
            status = "behind_uncollected"
        elif (
            len(ports) == 1
            and len(link[ST + "tp"]) == 2
            and not any(
                row["port"] == ports[0]["port"] and row["mac"] != mac
                for row in self.devices[ports[0]["address"]].get("mac", [])
            )
        ):
            status = "confirmed"
        else:
            status = "shared_segment"
        ports.sort(key=lambda p: (not p["collected"], p["name"], p["port"]))
        return {
            "status": status,
            "host_node": host,
            "link_id": link["link-id"],
            "ports": ports,
            "inferred": not any(p["basis"] == "mac" for p in ports if p["collected"]),
            "evidence": self.evidence.get(link["link-id"], []),
        }


def locate(devices, base, query, observed=None):
    """devices: builder input keyed by collected address; observed: {address: evidence}.

    Returns the router interfaces in an IP's subnet and one location per MAC seen for it
    (normally one;
    several MACs for one IP are reported, never resolved by choosing one).
    """
    kind, value = _query(query)
    return answer(_Locator(devices, base, observed or {}), kind, value)


def answer(locator, kind, value):
    """One query against an already built locator (path tracing asks twice)."""
    result = {"query": value, "kind": kind, "router_interfaces": [], "locations": []}
    own = locator.own(kind, value)
    if own:
        result["locations"].extend(own)
        if kind == "ip":
            # An address owned somewhere does not identify an ARP endpoint in an
            # unrelated device-local NI. Preserve observations we cannot associate.
            unassociated = {
                (row["device"], row.get("vrf", "default"), row["ifname"], row["mac"])
                for row in locator.arp(ip=value)
                if not locator.topo.ip_candidates(
                    value, device=row["device"], vrf=row.get("vrf", "default")
                )
                and not locator.topo.neighbour_candidates(
                    row["device"], row.get("vrf", "default"), value, row["ifname"]
                )
            }
            result["locations"].extend(
                {
                    "status": "not_found",
                    "ambiguity": "address_scope",
                    "ports": [],
                    "mac": mac,
                    "ips": [value],
                    "arp": [{"device": key, "vrf": vrf, "interface": ifname, "ip": value}],
                }
                for key, vrf, ifname, mac in sorted(unassociated)
            )
        result["status"] = "device" if len(result["locations"]) == 1 else "conflict"
        if result["status"] == "conflict":
            result["note"] = "地址匹配多个设备/本地网络实例；缺少作用域，不能选定其中一个。"
        return result
    if kind == "ip":
        members = locator.virtual_router(value)
        if members:
            # A gateway, not a host: each member's own VRRP state answers who holds it.
            result.update(
                status="virtual_router",
                members=members,
                router_interfaces=locator.router_interfaces(value),
                note=VIRTUAL_ROUTER_NOTE,
            )
            return result
        result["router_interfaces"] = locator.router_interfaces(value)
        # The network side only; what the host itself has configured is not observed.
        result["note"] = HOST_SETTINGS_UNSEEN
        rows = locator.arp(ip=value)
        macs = sorted({row["mac"] for row in rows})
    else:
        rows = locator.arp(mac=value)
        macs = [value]
    for mac in macs:
        if mac in locator.topo.ambiguous_macs:
            fdb = [
                {"device": key, **row}
                for key, facts in sorted(locator.devices.items())
                for row in facts.get("mac", [])
                if row["mac"] == mac
            ]
            vlans = {locator.topo.mac_vlan(row) for row in fdb} - {None}
            location = {
                "status": "conflict",
                "ports": [],
                "ambiguity": "vlan_scope" if len(vlans) > 1 else "attachment_scope",
                "note": "同一 MAC 出现在多个作用域或未证明相连的观察端口，不能合并为一个端点。",
                "fdb": fdb,
            }
        else:
            location = locator.attachment(mac) or {"status": "not_found", "ports": []}
        location["mac"] = mac
        location["ips"] = sorted({row["ip"] for row in locator.arp(mac=mac)})
        location["arp"] = [
            {
                "device": row["device"],
                "ip": row["ip"],
                "interface": row["ifname"],
                "vrf": row.get("vrf", "default"),
                **({"vlan": row["vlan"]} if "vlan" in row else {}),
            }
            for row in rows
            if row["mac"] == mac
        ]
        result["locations"].append(location)
    locations = result["locations"]
    # Several MACs for one IP is itself a finding; none is chosen as the answer.
    result["status"] = (
        "not_found"
        if not locations
        else locations[0]["status"]
        if len(locations) == 1
        else "conflict"
    )
    return result

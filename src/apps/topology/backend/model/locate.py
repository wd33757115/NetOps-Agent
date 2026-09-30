"""Endpoint location: where does an IP or MAC attach, and which router serves its subnet.

Reuses the builder's attachment decision: a host's L2 link in the built tree is its
attachment. The link's other ends classify the answer:

- one device port: ``confirmed``;
- several ports, all on collected devices: ``shared_segment`` (hub or unmanaged switch);
- a port on a device that was only advertised: ``behind_uncollected``;
- no attachment evidence: ``not_found``;
- an address or MAC of a collected device itself: ``device``.
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
        """A collected device's own address, or the MAC its ARP peers learned for it."""
        for (vrf, address), (key, ifname) in sorted(self.topo.owner.items()):
            owned = address == value if kind == "ip" else self._mac_of(vrf, address) == value
            if owned:
                device = self.device(self.topo.device_node(self.topo.chassis[key]))
                return {"status": "device", "device": device, "interface": ifname, "vrf": vrf}
        return None

    def _mac_of(self, vrf, address):
        for facts in self.devices.values():
            for rec in facts.get("arp", []):
                if rec.get("vrf", "default") == vrf and rec["ip"] == address:
                    return rec["mac"]
        return None

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
        elif len(ports) == 1:
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
        result["locations"].append(own)
        result["status"] = "device"
        return result
    if kind == "ip":
        result["router_interfaces"] = locator.router_interfaces(value)
        # The network side only; what the host itself has configured is not observed.
        result["note"] = HOST_SETTINGS_UNSEEN
        rows = locator.arp(ip=value)
        macs = sorted({row["mac"] for row in rows})
    else:
        rows = locator.arp(mac=value)
        macs = [value]
    for mac in macs:
        location = locator.attachment(mac) or {"status": "not_found", "ports": []}
        location["mac"] = mac
        location["ips"] = sorted({row["ip"] for row in locator.arp(mac=mac)})
        location["arp"] = [
            {"device": row["device"], "ip": row["ip"], "interface": row["ifname"]}
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

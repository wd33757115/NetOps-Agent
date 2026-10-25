"""Build SIMAP (RFC 8345 + draft-havel-nmop-simap-yang-03) L2/L3 networks from parsed facts.

Input is per collected device: parser records for lldp_local (summary), lldp, interfaces,
ip, arp and mac. Output is RFC 7951 JSON plus the evidence behind every link.

- A device is identified by the chassis ID it reports in lldp_local, the same key its
  neighbours advertise, so a collected device and an advertised neighbour are one node.
- Links use SIMAP bidirectional links with an st:tp list instead of RFC 8345 pairs of
  unidirectional links. Shared media is one mp2mp link, never an invented hub node.
- An L2 link is L2 adjacency, not a proven cable. Unmanaged switches stay invisible.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import quote

from ..collectors.ifname import normalize_ifname
from .fabric import overlay_networks
from .ospf import link_metric, project_ospf
from .overlay import prepare

ST = "ietf-simap-topology:"
NT = "ietf-network-topology:"
L2T = "ietf-l2-topology:"
L3T = "ietf-l3-unicast-topology:"
L2_PORT_TYPES = frozenset({"ethernet", "lag"})
# Virtual router MACs are gateways, never hosts: VRRP 00-00-5E-00-01/02-{VRID} for IPv4/IPv6
# (RFC 5798 §7.3) and HSRP 00-00-0C-07-AC-{group} (RFC 2281).
VIRTUAL_ROUTER_MACS = ("00:00:5e:00:01:", "00:00:5e:00:02:", "00:00:0c:07:ac:")


@dataclass(frozen=True)
class _TargetChassis:
    """An unreported chassis cannot collide with an actual LLDP string value."""

    key: str


def _components(pairs):
    """Connected components of member pairs: {member: representative}."""
    parent = {}

    def find(member):
        parent.setdefault(member, member)
        while parent[member] != member:
            parent[member] = parent[parent[member]]
            member = parent[member]
        return member

    for a, b in pairs:
        parent[find(a)] = find(b)
    return {member: find(member) for member in parent}


_MAC = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}")


def _is_ip(value):
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _uri(value):
    return quote(str(value), safe="-._~:")


def _dash(mac):
    return mac.replace(":", "-")


def _vlan_ids(value):
    """IEEE 802.1Q vid-range-type: ascending, nonoverlapping VIDs in 1..4094."""
    if not isinstance(value, str):
        return None
    if value == "":
        return set()
    if not re.fullmatch(
        r"[1-9][0-9]{0,3}(?:-[1-9][0-9]{0,3})?"
        r"(?:,[1-9][0-9]{0,3}(?:-[1-9][0-9]{0,3})?)*",
        value,
    ):
        return None
    result, previous = set(), 0
    for part in value.split(","):
        bounds = list(map(int, part.split("-")))
        first, last = bounds[0], bounds[-1]
        if first <= previous or last < first or last > 4094:
            return None
        result.update(range(first, last + 1))
        previous = last
    return result


def _vlan_ranges(vids):
    """Serialize a known tagged set as the standard VID range string."""
    ranges = []
    for vid in sorted(vids):
        if ranges and vid == ranges[-1][1] + 1:
            ranges[-1][1] = vid
        else:
            ranges.append([vid, vid])
    return ",".join(str(first) if first == last else f"{first}-{last}" for first, last in ranges)


def _ports(summary):
    """Only unambiguous parsed port observations; malformed facts add no attributes."""
    if summary is None:
        return {}
    if not isinstance(summary, dict) or not isinstance(summary.get("ports", []), list):
        return None
    if summary.get("invalid_structure", False) is not False:
        return None
    invalid = summary.get("invalid_ports", [])
    if not isinstance(invalid, list) or any(
        not isinstance(name, str) or not name for name in invalid
    ):
        return None
    ports, duplicates = {}, set(invalid)
    for port in summary.get("ports", []):
        if not isinstance(port, dict) or not isinstance(port.get("ifname"), str):
            return None
        name = port["ifname"]
        if not name:
            return None
        if name in ports:
            duplicates.add(name)
        ports[name] = port
    return {**ports, **dict.fromkeys(duplicates)}


def _operational_trunk(port):
    """Accept the verified IOS operational trunk and aggregate-member forms."""
    if "oper_mode" not in port or port["oper_mode"] == "trunk":
        return True
    mode = port["oper_mode"]
    if not isinstance(mode, str):
        return False
    member = re.fullmatch(r"trunk \(member of bundle Po([1-9][0-9]*)\)", mode)
    return bool(member) and port.get("lag_parent", f"Port-channel{member[1]}") == (
        f"Port-channel{member[1]}"
    )


def _outer_tag(trunk, switchport=None):
    """RFC 8944 outer-tag is tagged traffic, not access VLAN, PVID or STP state.

    Ordinary non-native 802.1Q trunk VLANs are tagged. If the native VLAN is allowed,
    its operational tagging must be observed before the complete tag set can be stated.
    Administrative native-tagging settings alone do not establish operational tagging.
    Disagreement between same-port observations keeps the tag set unknown.
    """
    if trunk.get("l2_mode") != "trunk" or trunk.get("encapsulation") != "dot1q":
        return None
    if not _operational_trunk(trunk):
        return None
    allowed = _vlan_ids(trunk.get("trunk_vlans"))
    native = trunk.get("native_vlan")
    if not allowed or type(native) is not int or not 1 <= native <= 4094:
        return None
    if switchport is not None:
        if switchport.get("l2_mode") != "trunk":
            return None
        if not _operational_trunk(switchport):
            return None
        if "native_vlan" in switchport:
            if type(switchport["native_vlan"]) is not int or switchport["native_vlan"] != native:
                return None
        if "trunk_vlans" in switchport and _vlan_ids(switchport["trunk_vlans"]) != allowed:
            return None
    if native in allowed:
        tagged = (switchport or {}).get("native_tagged")
        if type(tagged) is not bool:
            return None
        if not tagged:
            allowed.remove(native)
    return _vlan_ranges(allowed) if allowed else None


class _Topology:
    def __init__(self, devices, base):
        # Read the facts as the path engine does: instance bindings made explicit
        # (e.g. Comware ARP rows in the instance of their interface or VSI).
        self.devices = prepare(devices)
        self.protocol = {**project_ospf(devices), "links": {}}
        self.base = base
        self.l2_net = base + "l2"
        self.l3_net = base + "l3"
        self.chassis = {}
        for key, facts in devices.items():
            chassis = (facts.get("lldp_local") or {}).get("chassis_id")
            self.chassis[key] = chassis or _TargetChassis(key)
        # RFC 8529 network-instance names are device-local. Preserve every address
        # owner; neither a matching VRF name nor input order chooses a remote device.
        owners = {}
        for key, facts in sorted(devices.items()):
            for rec in facts.get("ip", []):
                owners.setdefault(rec["address"], set()).add(
                    (key, rec["ifname"], rec.get("vrf", "default"))
                )
        self.address_owners = {ip: tuple(sorted(rows)) for ip, rows in sorted(owners.items())}
        reported = list(self.chassis.values())
        self.conflicting_chassis = {chassis for chassis in reported if reported.count(chassis) > 1}
        for key, chassis in self.chassis.items():
            if reported.count(chassis) > 1:
                self.chassis[key] = _TargetChassis(key)
        self.advertised_identity()
        self.by_chassis = {c: k for k, c in self.chassis.items()}
        self.l2_nodes = {}
        self.management = {}  # chassis -> addresses it is managed or advertised by
        self.host_ips = {}
        scopes = {}
        for facts in self.devices.values():
            for row in facts.get("mac", []):
                vid = self.mac_vlan(row)
                if vid is not None:
                    scopes.setdefault(row["mac"], set()).add(vid)
            for row in facts.get("arp", []):
                for vid in (self.interface_vlan(facts, row["ifname"]), self.arp_vlan(row)):
                    if vid is not None:
                        scopes.setdefault(row["mac"], set()).add(vid)
        # Until bridge/FDB instances are projected, a global MAC node cannot
        # represent independent VLAN attachments. Keep the raw observations and
        # let locate report their conflict instead of joining the VLANs.
        self.ambiguous_macs = frozenset(mac for mac, vids in scopes.items() if len(vids) > 1)
        self.host_vlans = {mac: next(iter(vids)) for mac, vids in scopes.items() if len(vids) == 1}

    def ip_candidates(self, ip, *, device=None, vrf=None):
        """Sorted (device, interface, local NI) candidates; NI filtering is local."""
        if vrf is not None and device is None:
            raise ValueError("a network-instance name needs a device scope")
        return tuple(
            row
            for row in self.address_owners.get(ip, ())
            if (device is None or row[0] == device) and (vrf is None or row[2] == vrf)
        )

    def neighbour_candidates(self, device, vrf, ip, ifname=None):
        """Owners supported by scoped ARP or an exact LLDP remote port.

        LLDP can disambiguate duplicate IPs. An ARP row alone can associate an
        address only when there is one possible remote owner; it cannot select
        between duplicate owners using their independently named local NIs.
        """
        candidates = tuple(row for row in self.ip_candidates(ip) if row[0] != device)
        if self.ip_candidates(ip, device=device, vrf=vrf):
            return ()
        facts = self.devices[device]
        local_ifs = {
            row["ifname"]
            for row in facts.get("ip", [])
            if row.get("vrf", "default") == vrf and (ifname is None or row["ifname"] == ifname)
        }
        if not local_ifs:
            return ()
        direct = set()
        for row in facts.get("lldp", []):
            if row["local_if"] not in local_ifs:
                continue
            peer = row["remote_chassis_id"]
            port = self.remote_port(peer, row["remote_port_id"], row.get("remote_port_subtype"))
            for candidate in candidates:
                if self.chassis[candidate[0]] == peer and candidate[1] == port:
                    direct.add(candidate)
        if direct:
            return tuple(sorted(direct))
        arp = [
            row
            for row in facts.get("arp", [])
            if row.get("vrf", "default") == vrf
            and row["ip"] == ip
            and any(self.arp_interface(facts, row, name) for name in local_ifs)
            and self.attachment(facts, row)
        ]
        if len(candidates) == 1 and len({row["mac"] for row in arp}) == 1:
            # The address must be on the observed remote interface's subnet;
            # a route to a loopback or a different subnet is not adjacency.
            remote, remote_if, remote_vrf = candidates[0]
            remote_networks = {
                ipaddress.ip_interface(f"{row['address']}/{row['prefix_len']}").network
                for row in self.devices[remote].get("ip", [])
                if row["ifname"] == remote_if
                and row.get("vrf", "default") == remote_vrf
                and row["address"] == ip
            }
            if any(
                ipaddress.ip_address(row["address"]) in network
                for row in facts.get("ip", [])
                if row["ifname"] in local_ifs and row.get("vrf", "default") == vrf
                for network in remote_networks
            ):
                return candidates
        return ()

    def advertised_identity(self):
        """A device that cannot report its own chassis (IOS has no local LLDP command) is
        the chassis its neighbours advertise with one of its own addresses as the LLDP
        management address. Only a one-to-one claim counts; anything ambiguous stays apart.
        """
        holders = {}
        for address, rows in self.address_owners.items():
            holders[address] = {key for key, _, _ in rows}
        claims = {}
        for observer, facts in self.devices.items():
            for rec in facts.get("lldp", []):
                for address in rec.get("remote_mgmt_addresses", []):
                    keys = holders.get(address, set())
                    if len(keys) == 1 and observer not in keys:
                        claims.setdefault(next(iter(keys)), set()).add(rec["remote_chassis_id"])
        reported = {
            c for c in self.chassis.values() if not isinstance(c, _TargetChassis)
        } | self.conflicting_chassis
        unresolved = {
            key: next(iter(chassis))
            for key, chassis in claims.items()
            if isinstance(self.chassis[key], _TargetChassis) and len(chassis) == 1
        }
        wanted = list(unresolved.values())
        for key, chassis in unresolved.items():
            if chassis not in reported and wanted.count(chassis) == 1:
                self.chassis[key] = chassis

    # identifiers -------------------------------------------------------------
    def device_node(self, chassis):
        if isinstance(chassis, _TargetChassis):
            value = "target:" + quote(chassis.key, safe="-._~")
        elif _MAC.fullmatch(chassis):
            value = _dash(chassis)  # Preserve existing canonical MAC identities.
        else:
            # Colon is encoded too: a reported chassis must not impersonate a
            # fallback target, a MAC identity, or the suffix of a logical NI node.
            value = "chassis:" + quote(chassis, safe="-._~")
        return self.base + "device:" + value

    def port_tp(self, ifname):
        return self.base + "if:" + _uri(ifname)

    def host_node(self, mac):
        return self.base + "host:" + _dash(mac)

    def nic_tp(self, mac):
        return self.base + "nic:" + _dash(mac)

    def ref(self, member):
        if member[0] == "port":
            return self.device_node(member[1]), self.port_tp(member[2])
        return self.host_node(member[1]), self.nic_tp(member[1])

    # L2 ------------------------------------------------------------------------
    def remote_port(self, chassis, port_id, subtype):
        """A collected neighbour's port in its own spelling. IOS does not print the port ID
        subtype (it sends "interface name"); an unknown subtype counts only when the name
        is one of the neighbour platform's interface names."""
        key = self.by_chassis.get(chassis)
        if key and (subtype is None or str(subtype).lower() == "interface name"):
            name = normalize_ifname(port_id, self.devices[key].get("platform", ""))
            if name.recognized or subtype is not None:
                return name.name
        return port_id

    def observations(self):
        """Pairs of L2 endpoints with evidence.

        Hosts attach where the evidence is most specific: an edge port (no LLDP
        neighbour) from the MAC table or ARP beats a sighting on an LLDP port or an
        aggregate, which in a switch chain only means "somewhere behind that neighbour".
        A host seen only there is placed when it has an IP and every sighting is on one
        shared segment (a hub); otherwise placing it would join separate links.
        Virtual router MACs are gateways, not hosts.
        """
        found, hosts, bundles = [], {}, {}
        managed_macs = {
            c for c in self.chassis.values() if isinstance(c, str) and _MAC.fullmatch(c)
        }
        for key, facts in self.devices.items():
            me = self.chassis[key]
            for rec in facts.get("lag", []):
                bundles[("port", me, rec["ifname"])] = [("port", me, m) for m in rec["members"]]
            platform = facts.get("platform", "")
            lldp_ports = {rec["local_if"] for rec in facts.get("lldp", [])}
            # LLDP runs on an aggregate's members (RFC 8944 member-link-tp): what the
            # aggregate learns is behind that peer. Without membership facts every
            # aggregate is taken to face a peer rather than join separate links.
            if "lag" in facts:
                lldp_ports |= {
                    rec["ifname"]
                    for rec in facts["lag"]
                    if any(member in lldp_ports for member in rec["members"])
                }
            else:
                lldp_ports |= {
                    rec["port"]
                    for rec in facts.get("mac", [])
                    if normalize_ifname(rec["port"], platform).kind == "lag"
                }
            advertised = {}
            for rec in facts.get("lldp", []):
                local, peer = rec["local_if"], rec["remote_chassis_id"]
                port = self.remote_port(peer, rec["remote_port_id"], rec.get("remote_port_subtype"))
                self.l2_node(peer, rec.get("remote_sysname"))
                self.management.setdefault(peer, set()).update(rec.get("remote_mgmt_addresses", []))
                for address in rec.get("remote_mgmt_addresses", []):
                    advertised[(local, address)] = ("port", peer, port)
                found.append(
                    (
                        ("port", me, local),
                        ("port", peer, port),
                        f"{key} lldp {local} -> {peer} {port}",
                    )
                )
            for rec in facts.get("arp", []):
                vrf, port = rec.get("vrf", "default"), self.attachment(facts, rec)
                owners = self.neighbour_candidates(key, vrf, rec["ip"], rec["ifname"])
                if self.ip_candidates(rec["ip"]):
                    managed_macs.add(rec["mac"])
                    if len(owners) != 1:
                        continue  # ambiguous ownership is not an observed endpoint identity
                    other_key, other_if, _ = owners[0]
                    other_kind = normalize_ifname(
                        other_if, self.devices[other_key].get("platform", "")
                    ).kind
                    if port and port not in lldp_ports and other_kind in L2_PORT_TYPES:
                        other = ("port", self.chassis[other_key], other_if)
                        why = f"{key} arp {rec['ip']} {rec['mac']} {port}"
                        found.append((("port", me, port), other, why))
                    continue  # on an LLDP port the adjacency is already recorded
                if (rec["ifname"], rec["ip"]) in advertised:
                    managed_macs.add(rec["mac"])
                    continue
                self.host_ips.setdefault(rec["mac"], set()).add(
                    (key, vrf, rec["ifname"], rec["ip"])
                )
                if port:
                    strength = "weak" if port in lldp_ports else "strong"
                    hosts.setdefault(rec["mac"], {"strong": [], "weak": []})[strength].append(
                        (("port", me, port), f"{key} arp {rec['ip']} {rec['mac']} {port}")
                    )
            for rec in facts.get("mac", []):
                port = rec["port"]
                strength = "weak" if port in lldp_ports else "strong"
                hosts.setdefault(rec["mac"], {"strong": [], "weak": []})[strength].append(
                    (
                        ("port", me, port),
                        f"{key} mac {rec['mac']} vlan {rec.get('vlan_or_bd')} {port}",
                    )
                )
        segment = _components((a, b) for a, b, _ in found)
        for mac, seen in sorted(hosts.items()):
            if (
                mac in managed_macs
                or mac in self.ambiguous_macs
                or mac.startswith(VIRTUAL_ROUTER_MACS)
            ):
                continue  # a collected or advertised device's own interface, or a gateway
            members = seen["strong"]
            if len({segment.get(member, member) for member, _ in members}) > 1:
                # The same MAC at unrelated edge ports is not evidence that the
                # ports share media. It may be reused in independent bridges.
                self.ambiguous_macs |= {mac}
                continue
            if not members and mac in self.host_ips:
                # An aggregate sighting lies on each member's link, separate cables.
                places = {
                    segment.get(port, port)
                    for member, _ in seen["weak"]
                    for port in bundles.get(member, [member])
                }
                if len(places) == 1:
                    members = seen["weak"]
            for member, why in members:
                found.append((member, ("nic", mac), why))
        return found

    def attachment(self, facts, arp):
        """Own port, or a unique MAC/FDB observation in the SVI's exact VLAN.

        An unknown logical interface, mismatched VID or several same-VID ports
        cannot be reduced to the first MAC row. The observations remain facts.
        """
        platform = facts.get("platform", "")
        name = normalize_ifname(arp["ifname"], platform)
        if name.kind in L2_PORT_TYPES:
            return name.name
        vid = self.interface_vlan(facts, arp["ifname"])
        if vid is None:
            return None
        if self.arp_vlan(arp) is not None and self.arp_vlan(arp) != vid:
            return None
        ports = {
            rec["port"]
            for rec in facts.get("mac", [])
            if rec["mac"] == arp["mac"] and self.mac_vlan(rec) == vid
        }
        return next(iter(ports)) if len(ports) == 1 else None

    @staticmethod
    def interface_vlan(facts, ifname):
        name = normalize_ifname(ifname, facts.get("platform", ""))
        if name.kind != "vlanif":
            return None
        match = re.search(r"([0-9]+)$", name.name)
        return int(match[1]) if match and 1 <= int(match[1]) <= 4094 else None

    @staticmethod
    def mac_vlan(row):
        value = str(row.get("vlan_or_bd", ""))
        return int(value) if value.isdecimal() and 1 <= int(value) <= 4094 else None

    @staticmethod
    def arp_vlan(row):
        value = str(row.get("vlan", ""))
        return int(value) if value.isdecimal() and 1 <= int(value) <= 4094 else None

    def arp_interface(self, facts, arp, ifname):
        """Match an ARP interface, including Comware's explicit physical-port VID."""
        vid = self.interface_vlan(facts, ifname)
        if arp["ifname"] == ifname:
            return vid is None or self.arp_vlan(arp) in {None, vid}
        return vid is not None and self.arp_vlan(arp) == vid

    def l2_node(self, chassis, name=None, collected=False):
        node_id = self.device_node(chassis)
        node = self.l2_nodes.setdefault(node_id, {"node-id": node_id, NT + "termination-point": []})
        if name and (collected or L2T + "l2-node-attributes" not in node):
            node[L2T + "l2-node-attributes"] = {"name": name}
        return node

    def l2_port(self, chassis, ifname):
        node = self.l2_node(chassis)
        tp_id = self.port_tp(ifname)
        tps = node[NT + "termination-point"]
        if not any(tp["tp-id"] == tp_id for tp in tps):
            attrs = {"interface-name": ifname}
            if normalize_ifname(ifname, self.platform_of(chassis)).kind in L2_PORT_TYPES:
                attrs["encapsulation-type"] = L2T + "ethernet"
            tps.append({"tp-id": tp_id, L2T + "l2-termination-point-attributes": attrs})

    def management_attributes(self):
        """RFC 8944 l2-node-attributes: management-address is the address a device was
        collected by plus those LLDP advertises for it; management-mac is an LLDP chassis
        ID of the MAC subtype (the bridge base MAC)."""
        for key, chassis in self.chassis.items():
            if _is_ip(key):
                self.management.setdefault(chassis, set()).add(key)
        for chassis in {*self.chassis.values(), *self.management}:
            node = self.l2_nodes.get(self.device_node(chassis))
            if node is None:
                continue
            attrs = node.setdefault(L2T + "l2-node-attributes", {})
            addresses = sorted(a for a in self.management.get(chassis, ()) if _is_ip(a))
            if addresses:
                attrs["management-address"] = addresses
            if isinstance(chassis, str) and _MAC.fullmatch(chassis):
                attrs["management-mac"] = chassis

    def l2_lag(self, chassis, ifname, members):
        """RFC 8944: the aggregate's TP is a lag whose member-link-tp are its member ports."""
        for member in members:
            self.l2_port(chassis, member)
        self.l2_port(chassis, ifname)
        tp_id = self.port_tp(ifname)
        tp = next(t for t in self.l2_node(chassis)[NT + "termination-point"] if t["tp-id"] == tp_id)
        attrs = tp[L2T + "l2-termination-point-attributes"]
        attrs["lag"] = True
        attrs["member-link-tp"] = [self.port_tp(member) for member in members]

    def platform_of(self, chassis):
        key = self.by_chassis.get(chassis)
        return self.devices[key].get("platform", "") if key else ""

    def vlan_attributes(self):
        """Expose observed 802.1Q tags on the collected TP, never on inferred peers."""
        for key, facts in self.devices.items():
            switchports = _ports(facts.get("switchport"))
            trunks = _ports(facts.get("trunk"))
            if switchports is None or trunks is None:
                continue
            chassis = self.chassis[key]
            for ifname, trunk in trunks.items():
                if trunk is None or (ifname in switchports and switchports[ifname] is None):
                    continue
                if normalize_ifname(ifname, facts.get("platform", "")).kind not in L2_PORT_TYPES:
                    continue
                outer = _outer_tag(trunk, switchports.get(ifname))
                if outer is None:
                    continue
                self.l2_port(chassis, ifname)
                tp_id = self.port_tp(ifname)
                tps = self.l2_node(chassis)[NT + "termination-point"]
                tp = next(t for t in tps if t["tp-id"] == tp_id)
                attrs = tp[L2T + "l2-termination-point-attributes"]
                attrs["encapsulation-type"] = L2T + "vlan"
                attrs["outer-tag"] = outer

    # assembly ------------------------------------------------------------------
    def build(self):
        for key, facts in self.devices.items():
            me = self.chassis[key]
            self.l2_node(me, (facts.get("lldp_local") or {}).get("hostname"), collected=True)
            for rec in facts.get("interfaces", []):
                if rec.get("type") in L2_PORT_TYPES:
                    self.l2_port(me, rec["ifname"])
            for rec in facts.get("lag", []):
                self.l2_lag(me, rec["ifname"], rec["members"])
        segments = []
        for a, b, why in self.observations():
            for member in (a, b):
                if member[0] == "port":
                    self.l2_port(member[1], member[2])
            nic = next((m for m in (a, b) if m[0] == "nic"), None)
            vid = self.host_vlans.get(nic[1]) if nic else None
            merged = ({a, b}, {why}, vid)
            for seg in [s for s in segments if s[2] == vid and (a in s[0] or b in s[0])]:
                segments.remove(seg)
                merged[0].update(seg[0])
                merged[1].update(seg[1])
            segments.append(merged)
        for mac in sorted({m[1] for s in segments for m in s[0] if m[0] == "nic"}):
            self.l2_nodes[self.host_node(mac)] = {
                "node-id": self.host_node(mac),
                NT + "termination-point": [
                    {
                        "tp-id": self.nic_tp(mac),
                        L2T + "l2-termination-point-attributes": {
                            "mac-address": mac,
                            "encapsulation-type": L2T + "ethernet",
                        },
                    }
                ],
            }
        evidence, l2_links, link_of = {}, [], {}
        self.link_vlans = {}
        for members, why, vid in sorted(segments, key=lambda s: sorted(map(str, s[0]))):
            refs = sorted(self.ref(m) for m in members)
            link_id = self.base + "l2-link:" + hashlib.sha256(repr(refs).encode()).hexdigest()[:12]
            l2_links.append(self.link(self.l2_net, link_id, refs))
            self.link_vlans[link_id] = vid
            evidence[link_id] = sorted(why)
            for m in members:
                link_of.setdefault(m, set()).add(link_id)
        self.management_attributes()
        self.vlan_attributes()
        l2_nodes = dict(sorted(self.l2_nodes.items()))
        self.navigation(self.l2_net, l2_nodes, l2_links)
        networks = [self.network(self.l2_net, L2T + "l2-topology", l2_nodes, l2_links)]
        nodes, links = self.layer3(link_of, evidence)
        self.navigation(self.l3_net, nodes, links)
        networks.append(
            self.network(
                self.l3_net,
                L3T + "l3-unicast-topology",
                nodes,
                links,
                {"supporting-network": [{"network-ref": self.l2_net}]},
            )
        )
        # Overlays (VXLAN, DC fabric) above the L3 network; the L2 network stays first.
        overlays, overlay_evidence, self.overlay_gaps = overlay_networks(self, l2_links)
        networks += overlays
        evidence.update(overlay_evidence)
        return {"ietf-network:networks": {"network": networks}}, evidence

    def l3_node(self, device, vrf):
        node = self.device_node(self.chassis[device])
        return node if vrf == "default" else node + ":ni:" + _uri(vrf)

    def layer3(self, link_of, evidence):
        """An observed L3 graph of device-local routing instances.

        The network is an observation container, not a global VRF. RFC 8345 nodes
        may represent logical entities; each local NI is a separate node supported
        by its L2 device. A common prefix describes addressing, not adjacency.
        Only scoped ARP or exact LLDP endpoints join a prefix's components.
        """
        nodes, subnets, endpoints = {}, {}, []
        for key, facts in sorted(self.devices.items()):
            me = self.chassis[key]
            platform = facts.get("platform", "")
            tps, prefixes = {"default": {}}, {"default": set()}
            for rec in sorted(
                facts.get("ip", []),
                key=lambda r: (r["ifname"], r["address"], r.get("vrf", "default")),
            ):
                vrf = rec.get("vrf", "default")
                address = ipaddress.ip_interface(f"{rec['address']}/{rec['prefix_len']}")
                kind = normalize_ifname(rec["ifname"], platform).kind
                carriers = set()
                if kind in L2_PORT_TYPES:
                    carriers.add(("port", me, rec["ifname"]))
                else:
                    # Match the device-local NI and explicit ARP interface/VID;
                    # another SVI using the same prefix is not this interface.
                    for arp in facts.get("arp", []):
                        if (
                            arp.get("vrf", "default") == vrf
                            and self.arp_interface(facts, arp, rec["ifname"])
                            and (ipaddress.ip_address(arp["ip"]) in address.network)
                        ):
                            port = self.attachment(facts, arp)
                            if port and ("port", me, port) in link_of:
                                carriers.add(("port", me, port))
                tp = tps.setdefault(vrf, {}).setdefault(
                    rec["ifname"],
                    {
                        "tp-id": self.port_tp(rec["ifname"]),
                        ST + "name": rec["ifname"],
                        L3T + "l3-termination-point-attributes": {"ip-address": []},
                    },
                )
                addresses = tp[L3T + "l3-termination-point-attributes"]["ip-address"]
                if str(address.ip) not in addresses:
                    addresses.append(str(address.ip))
                if carriers:
                    tp["supporting-termination-point"] = [
                        {"network-ref": self.l2_net, "node-ref": n, "tp-ref": t}
                        for n, t in sorted(self.ref(c) for c in carriers)
                    ]
                prefixes.setdefault(vrf, set()).add(str(address.network))
                if address.network.prefixlen < address.max_prefixlen:
                    ref = (self.l3_node(key, vrf), self.port_tp(rec["ifname"]))
                    entry = {
                        "ref": ref,
                        "key": key,
                        "vrf": vrf,
                        "ifname": rec["ifname"],
                        "ip": str(address.ip),
                        "network": address.network,
                        "carriers": carriers,
                        "vid": self.interface_vlan(facts, rec["ifname"]),
                    }
                    endpoints.append(entry)
                    subnets.setdefault(address.network, {})[ref] = entry
            for vrf in tps:
                node_id = self.l3_node(key, vrf)
                attributes = {"prefix": [{"prefix": p} for p in sorted(prefixes[vrf])]}
                router_ids = self.protocol["devices"].get(key, {}).get("router_ids", {}).get(vrf)
                if router_ids:
                    attributes["router-id"] = router_ids
                nodes[node_id] = {
                    "node-id": node_id,
                    ST + "name": vrf,
                    "supporting-node": [
                        {"network-ref": self.l2_net, "node-ref": self.device_node(me)}
                    ],
                    L3T + "l3-node-attributes": attributes,
                    NT + "termination-point": list(tps[vrf].values()),
                }
        pairs, reasons = {}, {}
        for endpoint in endpoints:
            network, ref = endpoint["network"], endpoint["ref"]
            for other in endpoints:
                if other["network"] != network or other["ref"] == ref:
                    continue
                candidate = (other["key"], other["ifname"], other["vrf"])
                if candidate in self.neighbour_candidates(
                    endpoint["key"], endpoint["vrf"], other["ip"], endpoint["ifname"]
                ):
                    pair = tuple(sorted((ref, other["ref"])))
                    pairs.setdefault(network, set()).add(pair)
                    reasons.setdefault((network, pair), set()).add(
                        f"{endpoint['key']} NI {endpoint['vrf']} {endpoint['ifname']} "
                        f"observed neighbour {other['ip']}"
                    )
        for mac, addresses in sorted(self.host_ips.items()):
            nic = ("nic", mac)
            if nic not in link_of:
                continue  # no L2 attachment evidence for this host
            node_id = self.host_node(mac)
            for key, vrf, observed_if, ip in sorted(addresses):
                tp_id = self.base + "ip:" + ip
                node = nodes.setdefault(
                    node_id,
                    {
                        "node-id": node_id,
                        "supporting-node": [{"network-ref": self.l2_net, "node-ref": node_id}],
                        NT + "termination-point": [],
                    },
                )
                if not any(tp["tp-id"] == tp_id for tp in node[NT + "termination-point"]):
                    node[NT + "termination-point"].append(
                        {
                            "tp-id": tp_id,
                            L3T + "l3-termination-point-attributes": {"ip-address": [ip]},
                            "supporting-termination-point": [
                                {
                                    "network-ref": self.l2_net,
                                    "node-ref": node_id,
                                    "tp-ref": self.nic_tp(mac),
                                }
                            ],
                        }
                    )
                rows = [
                    row
                    for row in self.devices[key].get("arp", [])
                    if row["ifname"] == observed_if
                    and row["ip"] == ip
                    and row["mac"] == mac
                    and row.get("vrf", "default") == vrf
                ]
                for endpoint in endpoints:
                    network = endpoint["network"]
                    if (
                        endpoint["key"] != key
                        or endpoint["vrf"] != vrf
                        or ipaddress.ip_address(ip) not in network
                    ):
                        continue
                    if not any(
                        self.arp_interface(self.devices[key], row, endpoint["ifname"])
                        for row in rows
                    ):
                        continue
                    ref = (node_id, tp_id)
                    subnets[network][ref] = {"carriers": {nic}, "vid": self.host_vlans.get(mac)}
                    pair = tuple(sorted((endpoint["ref"], ref)))
                    pairs.setdefault(network, set()).add(pair)
                    reasons.setdefault((network, pair), set()).add(
                        f"{key} NI {vrf} arp {ip} {mac} {observed_if}"
                    )
        links = []
        for network, edges in sorted(pairs.items(), key=lambda item: str(item[0])):
            components = _components(sorted(edges))
            groups = {}
            for ref, representative in components.items():
                groups.setdefault(representative, set()).add(ref)
            for refs in sorted(map(sorted, groups.values())):
                if len(refs) < 2:
                    continue
                identity = repr((str(network), refs)).encode()
                link_id = self.base + "l3-link:" + hashlib.sha256(identity).hexdigest()[:12]
                link = self.link(self.l3_net, link_id, refs)
                link[L3T + "l3-link-attributes"] = {"name": str(network)}
                members = [subnets[network][ref] for ref in refs]
                terminations = [
                    {
                        "address": entry["key"],
                        "vrf": entry["vrf"],
                        "ifname": entry["ifname"],
                        "address_ip": entry["ip"],
                    }
                    if "key" in entry
                    else {}
                    for entry in members
                ]
                metric = link_metric(self.protocol, terminations)
                self.protocol["links"][link_id] = metric
                if "metric1" in metric:
                    link[L3T + "l3-link-attributes"]["metric1"] = metric["metric1"]
                supporting = sorted(
                    {
                        link_id
                        for entry in members
                        for c in entry["carriers"]
                        for link_id in link_of.get(c, ())
                        if self.link_vlans[link_id] is None
                        or self.link_vlans[link_id] == entry["vid"]
                    }
                )
                if supporting:
                    link["supporting-link"] = [
                        {"network-ref": self.l2_net, "link-ref": ref} for ref in supporting
                    ]
                links.append(link)
                why = {
                    reason
                    for pair in edges
                    if set(pair) <= set(refs)
                    for reason in reasons[(network, pair)]
                }
                evidence[link_id] = [f"observed subnet {network}", *sorted(why)]
        return dict(sorted(nodes.items())), links

    @staticmethod
    def link(network, link_id, refs):
        return {
            "link-id": link_id,
            ST + "link-direction": ST + "link-direction-bi",
            ST + "link-type": ST + ("link-type-p2p" if len(refs) == 2 else "link-type-mp2mp"),
            ST + "tp": [
                {
                    "network-ref": network,
                    "node-ref": node,
                    "tp-ref": tp,
                    "tp-direction": ST + "tp-direction-symmetric",
                }
                for node, tp in refs
            ],
        }

    @staticmethod
    def navigation(network, nodes, links):
        """SIMAP read-only navigation: each TP/node lists what its links reach."""
        for link in links:
            ends = [(t["node-ref"], t["tp-ref"]) for t in link[ST + "tp"]]
            for node_ref, tp_ref in ends:
                node = nodes[node_ref]
                tp = next(t for t in node[NT + "termination-point"] if t["tp-id"] == tp_ref)
                for other_node, other_tp in ends:
                    if (other_node, other_tp) == (node_ref, tp_ref):
                        continue
                    tp.setdefault(ST + "linked-termination-point", []).append(
                        {"network-ref": network, "node-ref": other_node, "tp-ref": other_tp}
                    )
                    linked = node.setdefault(ST + "linked-node", [])
                    entry = {"network-ref": network, "node-ref": other_node}
                    if other_node != node_ref and entry not in linked:
                        linked.append(entry)

    @staticmethod
    def network(network_id, network_type, nodes, links, extra=None):
        return {
            "network-id": network_id,
            "network-types": {network_type: {}},
            ST + "network-category": ST + "network-category-live",
            **(extra or {}),
            "node": list(nodes.values()),
            NT + "link": links,
        }


def device_node_ids(devices, base):
    """{target_key: node-id} for collected devices, using the builder's identity rule."""
    topology = _Topology(devices, base)
    return {key: topology.device_node(chassis) for key, chassis in topology.chassis.items()}


def host_node_prefix(base):
    return base + "host:"


def build_topology(devices, base):
    """devices: {target_key: {"platform", "lldp_local", "lldp", "interfaces", "ip", "arp", "mac"}}.

    Returns (RFC 7951 ietf-network tree with an L2 and an L3 network, {link-id: evidence}).
    """
    return _Topology(devices, base).build()


def build_topology_details(devices, base):
    """Standard tree, adjacency evidence, and independent OSPF projection metadata."""
    topology = _Topology(devices, base)
    tree, evidence = topology.build()
    return tree, evidence, topology.protocol


def build_topology_layers(devices, base):
    """build_topology_details plus what the overlay networks could not prove."""
    topology = _Topology(devices, base)
    tree, evidence = topology.build()
    return tree, evidence, topology.protocol, topology.overlay_gaps

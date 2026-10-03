"""EVPN/VXLAN facts: instances, peers, VNIs, VXLAN tunnels and EVPN MAC routes.

Layouts from the vpn-fabric lab (batch 20261003-225812, plan §8 M5):

- ``evpn_instances``: IOS "show l2vpn evpn evi detail" (one record per EVI and VLAN, with
  its L2/L3 VNI, VRF, VTEP address, access ports and peers) and Comware "display evpn
  instance verbose" (one record per VSI). Common keys: ``instance``, ``rd``,
  ``rt_import``, ``rt_export``, ``encapsulation``, ``vni``.
- ``evpn_peers``: IOS "show nve peers" and Comware "display evpn auto-discovery imet":
  the remote VTEPs per VNI (``vni``, ``type`` l2/l3, ``peer``; IOS adds ``state``).
- ``vxlan_vnis``: IOS "show nve vni" (VNI to VLAN/VRF) and Comware "display l2vpn vsi
  verbose" (VSI to VNI, gateway interface, access circuits, tunnels). Comware names the
  VSI it creates for an L3 VNI "Auto_L3VNI<vni>_<interface>"; that device-made name is
  the evidence for ``type`` l3.
- ``tunnel_interfaces``: Comware "display interface tunnel": VXLAN tunnel source and
  destination per tunnel interface.
- ``evpn_mac``: IOS "show l2vpn evpn mac" and Comware "display evpn route mac": per MAC,
  the instance and either the local access port or the remote VTEP.
"""

import re

from .common import Parsed, interface, valid_ip, valid_mac
from .generic_vpn import TARGET

FIELD = re.compile(r"\s*([A-Za-z][\w ./()-]*?)\s*:\s*(.*?)\s*")
RD_VALUE = re.compile(r"(\S+)(?:\s+\(auto\))?")


def _rd(value):
    match = RD_VALUE.fullmatch(value)
    if not match or not TARGET.fullmatch(match[1]):
        raise ValueError("invalid_route_distinguisher")
    return match[1]


def _targets(value):
    tokens = value.split()
    if not all(TARGET.fullmatch(t) for t in tokens):
        raise ValueError("invalid_route_target")
    return tokens


def _vni(value):
    if not value.isdigit() or not 1 <= int(value) <= 16777215:
        raise ValueError("invalid_vni")
    return int(value)


IOS_EVI = re.compile(r"EVPN instance:\s+(\d+) \((VLAN Based|VLAN Aware|Port Based)\)\s*")
IOS_PORT = re.compile(r"\s*(\S+) service instance (\d+)\s*")
IOS_ROUTES = re.compile(r"\s*Routes: .*")


def parse_evpn_instances(text, family):
    return _ios_instances(text) if family in {"ios", "iosxe"} else _comware_instances(text)


def _ios_instances(text):
    result = Parsed()
    evi, segment, lines, listing = None, None, [], None
    segments = []

    def close():
        if segment is not None:
            segments.append((segment, list(lines)))

    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        match = IOS_EVI.fullmatch(line)
        if match:
            close()
            evi = {
                "instance": match[1],
                "service": match[2].lower().replace(" ", "-"),
                "rt_import": [],
                "rt_export": [],
            }
            segment, lines, listing = None, [number], None
            result.handled_lines.add(number)
            continue
        if evi is None:
            result.reject(number, "evpn_line_before_instance")
            continue
        try:
            field = FIELD.fullmatch(line)
            if field and field[1] == "Vlan":
                close()
                segment = {**evi, "vlan": int(field[2])}
                lines, listing = [*lines[:1], number], None
                result.candidates += 1
            elif field and field[1] in {"Pseudoports", "Peers"}:
                listing = field[1]
            elif field and not (listing and IOS_ROUTES.fullmatch(line)):
                listing = None
                _ios_field(evi if segment is None else segment, field[1], field[2])
            elif listing == "Pseudoports" and segment is not None and IOS_PORT.fullmatch(line):
                port = IOS_PORT.fullmatch(line)
                segment.setdefault("ports", []).append(
                    {"ifname": interface(port[1], "ios").name, "service_instance": int(port[2])}
                )
            elif listing == "Peers" and segment is not None and valid_ip(line.strip()):
                segment.setdefault("peers", []).append(valid_ip(line.strip()))
            elif listing and IOS_ROUTES.fullmatch(line):
                pass
            else:
                raise ValueError("unrecognized_evpn_instance_line")
        except ValueError as exc:
            result.reject(number, str(exc))
            if segment is not None:
                segment["invalid"] = True
            continue
        lines.append(number)
        result.handled_lines.add(number)
    close()
    for record, record_lines in segments:
        if record.pop("invalid", False):
            continue
        result.add(record, record_lines, {"text": f"EVI {record['instance']}"})
    return result


def _ios_field(record, key, value):
    if key == "RD":
        record["rd"] = _rd(value)
    elif key == "Import-RTs":
        record["rt_import"] = _targets(value)
    elif key == "Export-RTs":
        record["rt_export"] = _targets(value)
    elif key == "State":
        record["state"] = value.lower()
    elif key == "Encapsulation":
        record["encapsulation"] = value.lower()
    elif key == "L2 VNI" and value != "0":
        record["vni"] = _vni(value)
    elif key == "L3 VNI" and value != "0":
        record["l3_vni"] = _vni(value)
    elif key == "VTEP IP":
        record["vtep"] = valid_ip(value)
        if record["vtep"] is None:
            raise ValueError("invalid_vtep_address")
    elif key == "VRF" and value:
        record["vrf"] = value
    elif key == "Access If" and value:
        record["gateway"] = interface(value, "ios").name
    elif key == "Core If" and value:
        record["core_interface"] = interface(value, "ios").name
    # Other "key: value" lines (replication, flooding, labels, RMAC...) are not modeled.


COMWARE_VSI = re.compile(r"\s*VSI Name:\s*(\S+)\s*")


def _comware_instances(text):
    result = Parsed()
    current, lines = None, []
    records = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        match = COMWARE_VSI.fullmatch(line)
        if match:
            if current is not None:
                records.append((current, lines))
            current, lines = {"instance": match[1], "rt_import": [], "rt_export": []}, [number]
            result.candidates += 1
            continue
        field = FIELD.fullmatch(line)
        if current is None or not field:
            result.reject(number, "unrecognized_evpn_instance_line")
            continue
        try:
            key, value = field[1], field[2]
            if key == "Route distinguisher":
                current["rd"] = _rd(value)
            elif key == "Export VPN targets":
                current["rt_export"] = _targets(value)
            elif key == "Import VPN targets":
                current["rt_import"] = _targets(value)
            elif key == "Encapsulation":
                current["encapsulation"] = value.lower()
            elif key == "VXLAN ID":
                current["vni"] = _vni(value)
        except ValueError as exc:
            result.reject(number, str(exc))
            current["invalid"] = True
            continue
        lines.append(number)
    if current is not None:
        records.append((current, lines))
    for record, record_lines in records:
        if record.pop("invalid", False):
            continue
        result.add(record, record_lines, {"text": f"VSI {record['instance']}"})
    return result


IOS_NVE_LEGEND = re.compile(r"\s*'\S+' - .*")
IOS_PEERS_HEADER = re.compile(
    r"\s*Interface\s+VNI\s+Type\s+Peer-IP\s+RMAC/Num_RTs\s+eVNI\s+state\s+flags\s+UP time\s*"
)
IOS_PEER_ROW = re.compile(
    r"\s*(\S+)\s+(\d+)\s+(L2CP|L3CP|L2DP)\s+(\S+)\s+(\S+)\s+(\d+)\s+(UP|DOWN|\S+)\s+(\S+)\s+(\S+)\s*"
)
COMWARE_IMET_HEADER = re.compile(r"\s*RD\s+PE_address\s+Tunnel_address\s+Tunnel mode\s+VXLAN ID\s*")
COMWARE_IMET_ROW = re.compile(r"\s*(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)\s*")
COMWARE_IMET_NOISE = re.compile(r"\s*Total number of automatically discovered peers: \d+\s*")
COMWARE_VSI_NAME = re.compile(r"\s*VSI name:\s*(\S+)\s*", re.I)
TYPES = {"L2CP": "l2", "L2DP": "l2", "L3CP": "l3"}


def parse_evpn_peers(text, family):
    result = Parsed()
    vsi = None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if family in {"ios", "iosxe"}:
            if IOS_NVE_LEGEND.fullmatch(line) or IOS_PEERS_HEADER.fullmatch(line):
                result.handled_lines.add(number)
                continue
            match = IOS_PEER_ROW.fullmatch(line)
            if not match:
                result.reject(number, "unrecognized_nve_peer_row")
                continue
            result.candidates += 1
            peer = valid_ip(match[4])
            if peer is None:
                result.reject(number, "invalid_nve_peer")
                continue
            record = {
                "nve": interface(match[1], "ios").name,
                "vni": _vni(match[2]),
                "type": TYPES[match[3]],
                "peer": peer,
                "state": match[7].lower(),
            }
            result.add(record, [number], {"text": line.strip()})
            continue
        if COMWARE_IMET_NOISE.fullmatch(line) or COMWARE_IMET_HEADER.fullmatch(line):
            result.handled_lines.add(number)
            continue
        if match := COMWARE_VSI_NAME.fullmatch(line):
            vsi = match[1]
            result.handled_lines.add(number)
            continue
        match = COMWARE_IMET_ROW.fullmatch(line)
        if not match or vsi is None:
            result.reject(number, "unrecognized_imet_row")
            continue
        result.candidates += 1
        peer, tunnel = valid_ip(match[2]), valid_ip(match[3])
        if peer is None or tunnel is None or not TARGET.fullmatch(match[1]):
            result.reject(number, "invalid_imet_row")
            continue
        result.add(
            {
                "instance": vsi,
                "vni": _vni(match[5]),
                "type": "l2",
                "peer": peer,
                "rd": match[1],
                "tunnel_address": tunnel,
                "mode": match[4].lower(),
            },
            [number],
            {"text": line.strip()},
        )
    result.empty_verified = not result.records and not result.rejected
    return result


IOS_VNI_HEADER = re.compile(
    r"\s*Interface\s+VNI\s+Multicast-group\s+VNI state\s+Mode\s+(VLAN|BD)\s+cfg\s+vrf\s*"
)
IOS_VNI_ROW = re.compile(
    r"\s*(\S+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(L2CP|L3CP|L2DP)\s+(\d+)\s+(\S+)\s+(\S+)\s*"
)
L3VNI_VSI = re.compile(r"Auto_L3VNI(\d+)_\d+")


def parse_vxlan_vnis(text, family):
    return _ios_vnis(text) if family in {"ios", "iosxe"} else _comware_vsis(text)


def _ios_vnis(text):
    result = Parsed()
    column = None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if match := IOS_VNI_HEADER.fullmatch(line):
            column = match[1].lower()
            result.handled_lines.add(number)
            continue
        match = IOS_VNI_ROW.fullmatch(line) if column else None
        if not match:
            result.reject(number, "unrecognized_nve_vni_row")
            continue
        result.candidates += 1
        try:
            record = {
                "nve": interface(match[1], "ios").name,
                "vni": _vni(match[2]),
                "state": match[4].lower(),
                "type": TYPES[match[5]],
                column: int(match[6]),
            }
        except ValueError as exc:
            result.reject(number, str(exc))
            continue
        if match[8] != "N/A":
            record["vrf"] = match[8]
        result.add(record, [number], {"text": line.strip()})
    result.empty_verified = column is not None and not result.records and not result.rejected
    return result


COMWARE_TABLE = re.compile(r"\s*(Tunnels|ACs):\s*")
COMWARE_TUNNEL_HEADER = re.compile(
    r"\s*Tunnel Name\s+Link ID\s+State\s+Type\s+Flood Proxy\s+Split horizon\s*"
)
COMWARE_TUNNEL_ROW = re.compile(r"\s*(\S+)\s+0x[0-9A-Fa-f]+\s+(\S+)\s+\S+\s+\S+\s+\S+\s*")
COMWARE_AC_HEADER = re.compile(r"\s*AC\s+Link ID\s+State\s*")
COMWARE_AC_ROW = re.compile(r"\s*(\S+)\s+0x[0-9A-Fa-f]+\s+(\S+)\s*")


def _comware_vsis(text):
    result = Parsed()
    current, lines, table = None, [], None
    records = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        match = COMWARE_VSI.fullmatch(line)
        if match:
            if current is not None:
                records.append((current, lines))
            name = match[1]
            current, lines, table = {"instance": name}, [number], None
            if l3 := L3VNI_VSI.fullmatch(name):
                current.update(type="l3", vni=int(l3[1]))
            else:
                current["type"] = "l2"
            result.candidates += 1
            continue
        if current is None:
            result.reject(number, "vsi_line_before_name")
            continue
        try:
            if match := COMWARE_TABLE.fullmatch(line):
                table = match[1]
            elif COMWARE_TUNNEL_HEADER.fullmatch(line) or COMWARE_AC_HEADER.fullmatch(line):
                pass
            elif table == "Tunnels" and (match := COMWARE_TUNNEL_ROW.fullmatch(line)):
                current.setdefault("tunnels", []).append(
                    {"ifname": interface(match[1], "comware").name, "state": match[2].lower()}
                )
            elif table == "ACs" and (match := COMWARE_AC_ROW.fullmatch(line)):
                current.setdefault("ports", []).append(
                    {"ifname": interface(match[1], "comware").name, "state": match[2].lower()}
                )
            elif field := FIELD.fullmatch(line):
                table = None if field[1] != "Statistics" else table
                key, value = field[1], field[2]
                if key == "VSI State":
                    current["state"] = value.lower()
                elif key == "VXLAN ID":
                    current["vni"] = _vni(value)
                elif key == "Gateway Interface":
                    current["gateway"] = interface(value.replace(" ", ""), "comware").name
            else:
                raise ValueError("unrecognized_vsi_line")
        except ValueError as exc:
            result.reject(number, str(exc))
            current["invalid"] = True
            continue
        lines.append(number)
    if current is not None:
        records.append((current, lines))
    for record, record_lines in records:
        if record.pop("invalid", False):
            continue
        result.add(record, record_lines, {"text": f"VSI {record['instance']}"})
    return result


TUNNEL_NAME = re.compile(r"(Tunnel\d+)\s*")
TUNNEL_ENDS = re.compile(r"\s*Tunnel source (\S+), destination (\S+)\s*")
TUNNEL_MODE = re.compile(r"\s*Tunnel protocol/transport (\S+)\s*")
TUNNEL_STATE = re.compile(r"\s*(Current state|Line protocol state):\s*(\S+)\s*")


def parse_tunnel_interfaces(text, family):
    result = Parsed()
    current = None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if match := TUNNEL_NAME.fullmatch(line):
            current = {"ifname": interface(match[1], family).name}
            result.candidates += 1
            result.add(current, [number], {"text": match[1]})
            continue
        if current is None:
            result.reject(number, "tunnel_line_before_name")
            continue
        lines = result.sources[-1]["lines"]
        if match := TUNNEL_ENDS.fullmatch(line):
            source, destination = valid_ip(match[1]), valid_ip(match[2])
            if source is None or destination is None:
                result.reject(number, "invalid_tunnel_address")
                continue
            current.update(source=source, destination=destination)
            lines.append(number)
        elif match := TUNNEL_MODE.fullmatch(line):
            current["mode"] = "vxlan" if match[1].upper().startswith("UDP_VXLAN") else match[1]
            lines.append(number)
        elif match := TUNNEL_STATE.fullmatch(line):
            key = "state" if match[1] == "Current state" else "protocol_state"
            current[key] = match[2].lower()
            lines.append(number)
        # Counters, queues, bandwidth and MTU lines belong to the same block and are not modeled.
    return result


IOS_MAC_HEADER = re.compile(r"\s*MAC Address\s+EVI\s+VLAN\s+ESI\s+Ether Tag\s+Next Hop\(s\)\s*")
IOS_MAC_ROW = re.compile(r"\s*(\S+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\d+)\s+(\S+)\s*")
IOS_LOCAL_HOP = re.compile(r"(\S+):(\d+)")
COMWARE_MAC_LEGEND = re.compile(r"\s*Flags:.*|\s+[A-Z] - .*")


def parse_evpn_mac(text, family):
    result = Parsed()
    if family in {"ios", "iosxe"}:
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip() or re.fullmatch(r"[-\s]+", line) or IOS_MAC_HEADER.fullmatch(line):
                result.handled_lines.add(number)
                continue
            match = IOS_MAC_ROW.fullmatch(line)
            if not match:
                result.reject(number, "unrecognized_evpn_mac_row")
                continue
            result.candidates += 1
            mac = valid_mac(match[1])
            try:
                local = IOS_LOCAL_HOP.fullmatch(match[6])
                hop = (
                    {"vtep": valid_ip(match[6])}
                    if valid_ip(match[6])
                    else {
                        "ifname": interface(local[1], "ios").name,
                        "service_instance": int(local[2]),
                    }
                )
            except (TypeError, ValueError):
                hop = None
            if mac is None or hop is None:
                result.reject(number, "invalid_evpn_mac_row")
                continue
            result.add(
                {"mac": mac, "instance": match[2], "vlan": int(match[3]), "next_hop": hop},
                [number],
                {"text": line.strip()},
            )
        return result
    vsi, current = None, None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or COMWARE_MAC_LEGEND.fullmatch(line):
            continue
        if match := COMWARE_VSI_NAME.fullmatch(line):
            vsi = match[1]
            continue
        field = FIELD.fullmatch(line)
        if not field or vsi is None:
            result.reject(number, "unrecognized_evpn_mac_line")
            continue
        key, value = field[1], field[2]
        if key == "MAC address":
            mac = valid_mac(value)
            result.candidates += 1
            if mac is None:
                result.reject(number, "invalid_evpn_mac")
                current = None
                continue
            current = {"mac": mac, "instance": vsi}
            result.add(current, [number], {"text": value})
        elif current is not None:
            result.sources[-1]["lines"].append(number)
            if key == "Flags":
                current["flags"] = value
            elif key == "Next hop" and value != "-":
                if valid_ip(value) is None:
                    result.reject(number, "invalid_evpn_next_hop")
                    continue
                current["next_hop"] = {"vtep": valid_ip(value)}
    for record in result.records:
        if family == "comware" and "next_hop" not in record and "L" in record.get("flags", ""):
            record["next_hop"] = {"local": True}
    return result

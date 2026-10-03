"""VPN facts: VRF/VPN-instance detail, LDP peers and the MPLS label forwarding table.

Layouts from the vpn-fabric lab (batch 20261003-225812, plan §8 M5):

- ``vrf_detail``: IOS "show vrf detail" (every VRF) and Comware "display ip vpn-instance
  instance-name <name>" (one instance per command). One record per VRF: ``vrf``, ``rd``
  (None when not set), ``interfaces`` and ``route_targets`` {family: {import, export}}.
  Families are ``ipv4``/``ipv6`` and ``evpn``; IOS-XE "stitching" route targets are the
  ones a VRF uses with EVPN, so they are the ``evpn`` family.
- ``ldp_peers``: IOS "show mpls ldp neighbor", Comware "display mpls ldp peer". One
  record per peer LDP identifier: ``peer_lsr_id``, ``label_space``, ``state`` (RFC 9070
  session-state names); IOS adds the local identifier, discovery interfaces and the
  addresses the peer bound.
- ``mpls_lfib``: IOS "show mpls forwarding-table", Comware "display mpls lsp". One record
  per row: ``fec``, ``in_label``, ``out_label`` (a number, ``implicit-null`` for a pop,
  ``untagged`` for unlabeled forwarding, or None), ``ifname``, ``next_hop``, and ``vrf``
  for a VPN aggregate label.
"""

import re

from .common import Parsed, interface, valid_ip

TARGET = re.compile(r"(?:\d{1,10}|\d{1,3}(?:\.\d{1,3}){3}):\d{1,10}")

IOS_VRF = re.compile(r"VRF (\S+) \(VRF Id = \d+\); default RD (<not set>|\S+); default VPNID .*")
IOS_FAMILY = re.compile(
    r"\s*Address family (ipv4|ipv6) (unicast|multicast)( \(Table ID = .*\):| not active)\s*"
)
IOS_TARGETS = re.compile(r"\s*(No )?(Export|Import) VPN route-target (stitching )?communities\s*")
IOS_VRF_NOISE = re.compile(
    r"\s*(?:New CLI format, supports multiple address-families|Flags: 0x[0-9A-Fa-f]+|"
    r"No (?:import|global export|export) route-map|(?:Import|Export|Global export) route-map: \S+|"
    r"VRF label distribution protocol: .*|VRF label allocation mode: .*)\s*"
)
COMWARE_VRF = re.compile(r"\s*VPN-Instance Name and Index\s*:\s*(\S+),\s*\d+\s*")
COMWARE_FIELD = re.compile(r"\s*([A-Za-z][\w /-]*?)\s*:\s*(.*?)\s*")
COMWARE_FAMILY = re.compile(r"\s*Address-family (IPv4|IPv6|EVPN)( Flowspec)?:\s*")
COMWARE_TARGETS = re.compile(r"\s*(Export|Import) VPN Targets\s*:\s*(.*?)\s*")
COMWARE_VRF_NOISE = re.compile(
    r"\s*(?:TTL mode|Diffserv-mode information|Apply Label Type|Create time|Up time|"
    r"Description|Maximum number of Routes|Label Policy)\s*:.*"
)


def _targets(tokens):
    if not tokens or not all(TARGET.fullmatch(t) for t in tokens):
        raise ValueError("invalid_route_target")
    return tokens


def parse_vrf_detail(text, family):
    result = Parsed()
    records, current, lines = [], None, []
    section = None  # "interfaces" | (family, direction)
    af = None

    def close():
        if current is not None:
            records.append((current, lines))

    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        match = (
            IOS_VRF.fullmatch(line) if family in {"ios", "iosxe"} else COMWARE_VRF.fullmatch(line)
        )
        if match:
            close()
            current = {"vrf": match[1], "rd": None, "interfaces": [], "route_targets": {}}
            if family in {"ios", "iosxe"} and match[2] != "<not set>":
                current["rd"] = match[2]
            lines, section, af = [number], None, None
            result.candidates += 1
            result.handled_lines.add(number)
            continue
        if current is None:
            result.reject(number, "vrf_detail_before_header")
            continue
        try:
            if family in {"ios", "iosxe"}:
                section, af = _ios_vrf_line(line, current, section, af)
            else:
                section, af = _comware_vrf_line(line, current, section, af, family)
        except ValueError as exc:
            result.reject(number, str(exc))
            current["invalid"] = True
            continue
        lines.append(number)
        result.handled_lines.add(number)
    close()
    for record, record_lines in records:
        if record.pop("invalid", False):
            continue
        result.add(record, record_lines, {"text": record["vrf"]})
    return result


def _ios_vrf_line(line, current, section, af):
    if line.strip() == "Interfaces:":
        return "interfaces", af
    match = IOS_FAMILY.fullmatch(line)
    if match:
        active = match[3] != " not active"
        name = match[1] if match[2] == "unicast" else f"{match[1]}-multicast"
        return None, name if active else None
    match = IOS_TARGETS.fullmatch(line)
    if match:
        if af is None:
            raise ValueError("route_targets_outside_family")
        target_family = "evpn" if match[3] else af
        targets = current["route_targets"].setdefault(target_family, {"import": [], "export": []})
        return (None if match[1] else (targets, match[2].lower())), af
    if IOS_VRF_NOISE.fullmatch(line):
        return None, af
    tokens = line.split()
    if section == "interfaces":
        current["interfaces"] += [interface(t, "ios").name for t in tokens]
        return section, af
    if isinstance(section, tuple) and all(t.startswith("RT:") for t in tokens):
        targets, direction = section
        targets[direction] += _targets([t[3:] for t in tokens])
        return section, af
    raise ValueError("unrecognized_vrf_detail_line")


def _comware_vrf_line(line, current, section, af, family):
    match = COMWARE_FAMILY.fullmatch(line)
    if match:
        name = match[1].lower() + ("-flowspec" if match[2] else "")
        current["route_targets"].setdefault(name, {"import": [], "export": []})
        return None, name
    match = COMWARE_TARGETS.fullmatch(line)
    if match:
        # Targets set on the instance itself (no family header yet) apply to every family.
        name = af or "instance"
        targets = current["route_targets"].setdefault(name, {"import": [], "export": []})
        direction = match[1].lower()
        if match[2]:
            targets[direction] += _targets(match[2].split())
        return (targets, direction), af
    if COMWARE_VRF_NOISE.fullmatch(line):
        return None, af
    match = COMWARE_FIELD.fullmatch(line)
    if match and match[1] == "Route Distinguisher":
        current["rd"] = match[2] if match[2] not in {"", "-", "--"} else None
        return None, af
    if match and match[1] == "Interfaces":
        current["interfaces"] += [
            interface(t, family).name for t in re.split(r"[,\s]+", match[2]) if t
        ]
        return "interfaces", af
    tokens = line.split()
    if isinstance(section, tuple) and all(TARGET.fullmatch(t) for t in tokens):
        targets, direction = section
        targets[direction] += tokens
        return section, af
    if section == "interfaces" and line.startswith("   "):
        current["interfaces"] += [
            interface(t, family).name for t in re.split(r"[,\s]+", line.strip()) if t
        ]
        return section, af
    raise ValueError("unrecognized_vrf_detail_line")


# RFC 9070 session-state names.
LDP_STATES = {
    "oper": "operational",
    "operational": "operational",
    "nonexistent": "non-existent",
    "non-existent": "non-existent",
    "initialized": "initialized",
    "openrec": "openrec",
    "opensent": "opensent",
}
IOS_LDP_PEER = re.compile(r"\s*Peer LDP Ident: (\S+):(\d+); Local LDP Ident (\S+):(\d+)\s*")
IOS_LDP_STATE = re.compile(r"\s*State: (\w+); Msgs sent/rcvd: \d+/\d+; \w+\s*")
IOS_LDP_SOURCE = re.compile(r"\s*(\S+), Src IP addr: (\S+)\s*")
IOS_LDP_NOISE = re.compile(
    r"\s*(?:TCP connection: .*|Up time: .*|LDP discovery sources:|"
    r"Addresses bound to peer LDP Ident:|Password: .*|Clients: .*)\s*"
)
COMWARE_LDP_HEADER = re.compile(r"\s*Peer LDP ID\s+State\s+Role\s+GR\s+MD5\s+KA Sent/Rcvd\s*")
COMWARE_LDP_ROW = re.compile(
    r"\s*(\S+):(\d+)\s+(\S+(?: \S+)?)\s+(Active|Passive)\s+\S+\s+\S+\s+\d+/\d+\s*"
)
COMWARE_LDP_NOISE = re.compile(
    r"\s*(?:VPN instance: public instance|Total number of peers: \d+)\s*"
)


def parse_ldp_peers(text, family):
    result = Parsed()
    current = None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if family in {"ios", "iosxe"}:
            match = IOS_LDP_PEER.fullmatch(line)
            if match:
                result.candidates += 1
                peer, local = valid_ip(match[1]), valid_ip(match[3])
                if peer is None or local is None:
                    result.reject(number, "invalid_ldp_identifier")
                    current = None
                    continue
                current = {
                    "peer_lsr_id": peer,
                    "label_space": int(match[2]),
                    "local_lsr_id": local,
                    "local_label_space": int(match[4]),
                    "discovery": [],
                    "addresses": [],
                }
                result.add(current, [number], {"text": line.strip()})
                continue
            if current is None:
                result.reject(number, "ldp_line_before_peer")
                continue
            source = result.sources[-1]["lines"]
            match = IOS_LDP_STATE.fullmatch(line)
            if match:
                state = LDP_STATES.get(match[1].lower())
                if state is None:
                    result.reject(number, "unknown_ldp_state")
                    continue
                current["state"] = state
            elif match := IOS_LDP_SOURCE.fullmatch(line):
                try:
                    current["discovery"].append(
                        {"ifname": interface(match[1], family).name, "address": valid_ip(match[2])}
                    )
                except ValueError as exc:
                    result.reject(number, str(exc))
                    continue
            elif IOS_LDP_NOISE.fullmatch(line):
                pass
            elif all(valid_ip(t) for t in line.split()):
                current["addresses"] += [valid_ip(t) for t in line.split()]
            else:
                result.reject(number, "unrecognized_ldp_line")
                continue
            source.append(number)
            continue
        if COMWARE_LDP_HEADER.fullmatch(line) or COMWARE_LDP_NOISE.fullmatch(line):
            result.handled_lines.add(number)
            continue
        match = COMWARE_LDP_ROW.fullmatch(line)
        if not match:
            result.reject(number, "unrecognized_ldp_line")
            continue
        result.candidates += 1
        state = LDP_STATES.get(match[3].lower().replace(" ", ""))
        if valid_ip(match[1]) is None or state is None:
            result.reject(number, "invalid_ldp_peer_row")
            continue
        result.add(
            {
                "peer_lsr_id": valid_ip(match[1]),
                "label_space": int(match[2]),
                "state": state,
                "role": match[4].lower(),
            },
            [number],
            {"text": line.strip()},
        )
    for record in result.records:
        if "state" not in record:
            record["state_missing"] = True
    return result


IOS_LFIB_HEADER = (
    re.compile(r"\s*Local\s+Outgoing\s+Prefix\s+Bytes Label\s+Outgoing\s+Next Hop\s*"),
    re.compile(r"\s*Label\s+Label\s+or Tunnel Id\s+Switched\s+interface\s*"),
)
IOS_LFIB_ROW = re.compile(
    r"\s*(\d+|None)\s+(Pop Label|No Label|\d+|\[T\]\s*\d+)\s+(\S+)\s+(\d+)\s+(\S+)(?:\s+(\S+))?\s*"
)
COMWARE_LFIB_HEADER = re.compile(r"\s*FEC\s+Proto\s+In/Out Label\s+Out Inter/NHLFE/LSINDEX\s*")
COMWARE_LFIB_ROW = re.compile(r"\s*(\S+)\s+(\S+)\s+(\S+)/(\S+)\s+(\S+)\s*")


def _label(value):
    if value in {"-", "None"}:
        return None
    if value in {"Pop Label", "3"}:
        return "implicit-null"
    if value == "No Label":
        return "untagged"
    if not value.isdigit() or int(value) > 1048575:
        raise ValueError("invalid_mpls_label")
    return int(value)


def _joined(text):
    """(line numbers, text): IOS wraps a long prefix column, ending the row with '\\'."""
    rows, pending = [], None
    for number, line in enumerate(text.splitlines(), 1):
        if pending is not None:
            rows.append(([pending[0], number], pending[1] + " " + line.strip()))
            pending = None
        elif line.rstrip().endswith("\\"):
            pending = (number, line.rstrip()[:-1].rstrip())
        else:
            rows.append(([number], line))
    if pending is not None:
        rows.append(([pending[0]], pending[1]))
    return rows


def parse_mpls_lfib(text, family):
    result = Parsed()
    for numbers, line in _joined(text):
        number = numbers[0]
        if not line.strip():
            continue
        if any(h.fullmatch(line) for h in IOS_LFIB_HEADER) or COMWARE_LFIB_HEADER.fullmatch(line):
            result.handled_lines.add(number)
            continue
        ios = IOS_LFIB_ROW.fullmatch(line) if family in {"ios", "iosxe"} else None
        comware = COMWARE_LFIB_ROW.fullmatch(line) if family == "comware" else None
        if not ios and not comware:
            result.reject(number, "unrecognized_lfib_row")
            continue
        result.candidates += 1
        try:
            if ios:
                local, out, fec, _bytes, egress, hop = ios.groups()
                record = {"in_label": _label(local), "out_label": _label(out)}
                if fec.endswith("[V]"):
                    fec = fec[:-3]
                    if egress.startswith("aggregate/"):
                        record["vrf"] = egress.split("/", 1)[1]
                        egress = None
                record["fec"] = fec
                if egress and egress != "drop":
                    record["ifname"] = interface(egress, family).name
                if hop:
                    record["next_hop"] = valid_ip(hop) or hop
            else:
                fec, protocol, local, out, egress = comware.groups()
                record = {
                    "fec": fec,
                    "protocol": protocol.lower(),
                    "in_label": _label(local),
                    "out_label": _label(out),
                }
                if egress != "-":
                    record["ifname"] = interface(egress, family).name
        except ValueError as exc:
            result.reject(number, str(exc))
            continue
        result.add(record, numbers, {"text": line.strip()})
    result.empty_verified = (
        not result.records and not result.rejected and bool(result.handled_lines)
    )
    return result

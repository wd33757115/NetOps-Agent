"""OSPF neighbours: IOS "show ip ospf neighbor", Comware "display ospf peer".

Layouts from lab samples (campus-wan 20261003-010038):
- IOS: "Neighbor ID Pri State Dead Time Address Interface" rows, all processes in one
  table; the process and area of a neighbour follow from its interface.
- Comware: "OSPF Process 1 with Router ID ..." and "Area: 0.0.0.0" headers before a
  "Router ID Address Pri Dead-Time State Interface" table.

One record per neighbour: ``neighbor_router_id``, ``address``, ``priority``, ``state``
(RFC 9129 nbr-state-type), ``role`` (DR/BDR/DROther as shown, "-" when none),
``ifname``, and ``process``/``area`` when the output names them.
"""

import re

from .common import Parsed, interface

STATES = {
    "full": "full",
    "loading": "loading",
    "exchange": "exchange",
    "exstart": "exstart",
    "2way": "2-way",
    "2-way": "2-way",
    "init": "init",
    "attempt": "attempt",
    "down": "down",
}
IPV4 = r"\d{1,3}(?:\.\d{1,3}){3}"
IOS_HEADER = re.compile(r"Neighbor ID\s+Pri\s+State\s+Dead Time\s+Address\s+Interface\s*")
IOS_ROW = re.compile(rf"({IPV4})\s+(\d+)\s+([\w-]+)/\s*(\S+)\s+(\S+)\s+({IPV4})\s+(\S+)\s*")
COMWARE_PROCESS = re.compile(rf"\s*OSPF Process (\d+) with Router ID ({IPV4})\s*")
COMWARE_AREA = re.compile(rf"\s*Area:\s*({IPV4})\s*")
COMWARE_HEADER = re.compile(r"\s*Router ID\s+Address\s+Pri\s+Dead-Time\s+State\s+Interface\s*")
# "Full/ -" on a point-to-point link (vpn-fabric LEAF3/PE2), like IOS "FULL/  -".
COMWARE_ROW = re.compile(rf"\s*({IPV4})\s+({IPV4})\s+(\d+)\s+(\S+)\s+([\w-]+)/\s*(\S+)\s+(\S+)\s*")


def parse(text, family):
    result = Parsed()
    process = area = None
    table = False
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or re.fullmatch(r"\s*Neighbor Brief Information\s*", line):
            continue
        header = COMWARE_PROCESS.fullmatch(line)
        if header:
            process, area, table = header[1], None, False
            result.summary.setdefault("router_ids", {})[process] = header[2]
            result.handled_lines.add(number)
            continue
        match = COMWARE_AREA.fullmatch(line)
        if match:
            area = match[1]
            result.handled_lines.add(number)
            continue
        if IOS_HEADER.fullmatch(line) or COMWARE_HEADER.fullmatch(line):
            table = True
            result.handled_lines.add(number)
            continue
        if not table:
            result.reject(number, "unrecognized_ospf_neighbor_line")
            continue
        ios, comware = IOS_ROW.fullmatch(line), COMWARE_ROW.fullmatch(line)
        if ios:
            rid, priority, state, role, dead, address, port = ios.groups()
        elif comware:
            rid, address, priority, dead, state, role, port = comware.groups()
        else:
            result.reject(number, "unrecognized_ospf_neighbor_row")
            continue
        result.candidates += 1
        result.handled_lines.add(number)
        normalized = STATES.get(state.lower())
        if normalized is None:
            result.reject(number, "unknown_ospf_neighbor_state")
            continue
        try:
            name = interface(port, family).name
        except ValueError as exc:
            result.reject(number, str(exc))
            continue
        record = {
            "neighbor_router_id": rid,
            "address": address,
            "priority": int(priority),
            "state": normalized,
            "role": role,
            "dead_time": dead,
            "ifname": name,
        }
        if process is not None:
            record["process"] = process
        if area is not None:
            record["area"] = area
        result.add(record, [number], {"text": line.strip()})
    return result

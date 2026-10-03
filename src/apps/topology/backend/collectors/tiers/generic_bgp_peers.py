"""BGP peers: IOS "show ip bgp summary", Comware "display bgp peer ipv4".

Layouts from lab samples (campus-wan 20261003-010038):
- IOS: "BGP router identifier X, local AS number N", then a "Neighbor V AS ...
  State/PfxRcd" table whose last column is the prefix count once Established;
- Comware: "BGP local router ID", "Local AS number", then a "Peer AS MsgRcvd MsgSent
  OutQ PrefRcv Up/Down State" table.

One record per peer: ``peer``, ``peer_as``, ``state`` (the draft-ietf-idr-bgp-model
session-state names), ``prefixes_received`` when shown, ``up_down``. The summary keeps
``router_id`` and ``local_as``.
"""

import ipaddress
import re

from .common import Parsed

STATES = {
    "idle": "idle",
    "connect": "connect",
    "active": "active",
    "opensent": "opensent",
    "openconfirm": "openconfirm",
    "established": "established",
}
IOS_ID = re.compile(r"BGP router identifier (\S+), local AS number (\d+)\s*")
IOS_HEADER = re.compile(
    r"Neighbor\s+V\s+AS\s+MsgRcvd\s+MsgSent\s+TblVer\s+InQ\s+OutQ\s+Up/Down\s+State/PfxRcd\s*"
)
IOS_ROW = re.compile(r"(\S+)\s+4\s+(\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+(\S+)\s+(.+?)\s*")
COMWARE_ID = re.compile(r"\s*BGP local router ID:\s*(\S+)\s*")
COMWARE_AS = re.compile(r"\s*Local AS number:\s*(\d+)\s*")
COMWARE_HEADER = re.compile(
    r"\s*Peer\s+AS\s+MsgRcvd\s+MsgSent\s+OutQ\s+PrefRcv\s+Up/Down\s+State\s*"
)
COMWARE_ROW = re.compile(r"\s*\*?\s*(\S+)\s+(\d+)\s+\d+\s+\d+\s+\d+\s+(\d+)\s+(\S+)\s+(\S+)\s*")


def _address(value):
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def parse(text, family):
    result = Parsed()
    table = False
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        for pattern, keys in (
            (IOS_ID, ("router_id", "local_as")),
            (COMWARE_ID, ("router_id",)),
            (COMWARE_AS, ("local_as",)),
        ):
            match = pattern.fullmatch(line)
            if match:
                for key, value in zip(keys, match.groups()):
                    result.summary[key] = int(value) if key == "local_as" else value
                result.handled_lines.add(number)
                break
        else:
            if IOS_HEADER.fullmatch(line) or COMWARE_HEADER.fullmatch(line):
                table = True
                result.handled_lines.add(number)
                continue
            if not table:
                continue  # memory, table version and legend lines carry nothing modeled
            ios, comware = IOS_ROW.fullmatch(line), COMWARE_ROW.fullmatch(line)
            if comware and not ios:
                peer, peer_as, received, up_down, state = comware.groups()
            elif ios:
                peer, peer_as, up_down, last = ios.groups()
                received, state = (last, "established") if last.isdigit() else (None, last)
            else:
                result.reject(number, "unrecognized_bgp_peer_row")
                continue
            result.candidates += 1
            result.handled_lines.add(number)
            address = _address(peer)
            normalized = STATES.get(re.sub(r"\s*\(.*\)$", "", state).lower())
            if address is None or normalized is None:
                result.reject(number, "invalid_bgp_peer_row")
                continue
            record = {
                "peer": address,
                "peer_as": int(peer_as),
                "state": normalized,
                "up_down": up_down,
            }
            if received is not None:
                record["prefixes_received"] = int(received)
            if normalized != state.lower():
                record["state_detail"] = state  # e.g. "Idle (Admin)"
            result.add(record, [number], {"text": line.strip()})
    return result

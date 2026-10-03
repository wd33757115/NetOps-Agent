"""Spanning-tree port role and state per instance (IOS "show spanning-tree").

Layout from lab samples (campus-wan 20261003-010038, IOL rapid-PVST): one block per
instance ("VLAN0010" for PVST/rapid-PVST, "MST0" for MSTP) with the protocol, root and
bridge identifiers, then an "Interface Role Sts Cost Prio.Nbr Type" table.

One record per (instance, port): ``instance``, ``vlan`` (PVST blocks only), ``protocol``,
``ifname``, ``role`` and ``state`` in IEEE 802.1Q terms, ``cost``, ``port_id``,
``link_type`` and ``edge``. ``summary["instances"]`` keeps each block's root and bridge
identifiers. Per-VLAN (PVST) state has no IEEE or IETF YANG node; it stays evidence for
the L2 forwarding analysis.
"""

import re

from .common import Parsed, interface

INSTANCE = re.compile(r"(VLAN(\d{4})|MST(\d+))\s*")
PROTOCOL = re.compile(r"\s*Spanning tree enabled protocol (\w+)\s*")
SECTION = re.compile(r"\s*(Root|Bridge) ID\s+Priority\s+(\d+)\b.*")
ADDRESS = re.compile(r"\s*Address\s+([0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4})\s*")
IS_ROOT = re.compile(r"\s*This bridge is the root\s*")
ROOT_PORT = re.compile(r"\s*Port\s+\d+\s+\((\S+)\)\s*")
HEADER = re.compile(r"Interface\s+Role\s+Sts\s+Cost\s+Prio\.Nbr\s+Type\s*")
ROW = re.compile(r"(\S+)\s+(\w+)\s+(\w+)\*?\s+(\d+)\s+(\d+\.\d+)\s+(.*?)\s*")
ROLES = {
    "Root": "root",
    "Desg": "designated",
    "Altn": "alternate",
    "Back": "backup",
    "Mstr": "master",
    "Dis": "disabled",
}
STATES = {
    "FWD": "forwarding",
    "BLK": "blocking",
    "LRN": "learning",
    "LIS": "listening",
    "DIS": "disabled",
    "BKN": "broken",
}


def parse(text, family):
    result = Parsed()
    instances, current, section, in_table, ports = [], None, None, False, set()
    for number, line in enumerate(text.splitlines(), 1):
        start = INSTANCE.fullmatch(line)
        if start:
            current = {
                "instance": start[1],
                **({"vlan": int(start[2])} if start[2] else {"msti": int(start[3])}),
            }
            instances.append(current)
            section, in_table, ports = None, False, set()
            result.handled_lines.add(number)
            continue
        if current is None or not line.strip() or re.fullmatch(r"[-\s]+", line):
            continue
        for pattern, action in (
            (PROTOCOL, lambda m: current.update(protocol=m[1])),
            (SECTION, None),
            (ADDRESS, None),
            (IS_ROOT, lambda m: current.update(is_root=True)),
            (ROOT_PORT, None),
            (HEADER, None),
        ):
            match = pattern.fullmatch(line)
            if not match:
                continue
            result.handled_lines.add(number)
            if pattern is SECTION:
                section = match[1].lower()
                current[f"{section}_priority"] = int(match[2])
            elif pattern is ADDRESS and section:
                current[f"{section}_address"] = match[1]
            elif pattern is ROOT_PORT:
                try:
                    current["root_port"] = interface(match[1], family).name
                except ValueError as exc:
                    result.reject(number, str(exc))
            elif pattern is HEADER:
                in_table = True
            elif action:
                action(match)
            break
        else:
            if not in_table:
                continue  # timers and aging lines carry nothing the model uses
            row = ROW.fullmatch(line)
            if not row or row[2] not in ROLES or row[3] not in STATES:
                result.reject(number, "unrecognized_stp_port_row")
                continue
            result.candidates += 1
            result.handled_lines.add(number)
            try:
                name = interface(row[1], family).name
            except ValueError as exc:
                result.reject(number, str(exc))
                continue
            if name in ports:
                result.reject(number, "duplicate_stp_port")
                continue
            ports.add(name)
            kind = row[6].split()
            record = {
                "instance": current["instance"],
                **{k: current[k] for k in ("vlan", "msti") if k in current},
                "protocol": current.get("protocol"),
                "ifname": name,
                "role": ROLES[row[2]],
                "state": STATES[row[3]],
                "cost": int(row[4]),
                "port_id": row[5],
                "link_type": "point-to-point"
                if kind and kind[0] == "P2p"
                else "shared"
                if kind and kind[0] == "Shr"
                else None,
                "edge": "Edge" in kind,
            }
            result.add(record, [number], {"text": line.strip()})
    for block in instances:
        block.setdefault("is_root", False)
    result.summary["instances"] = instances
    return result

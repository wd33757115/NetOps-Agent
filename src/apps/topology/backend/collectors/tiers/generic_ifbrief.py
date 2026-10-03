"""Interface states with separate route/bridge brief table interpretation."""

import re

from ..normalize import speed_mbps
from .common import IFNAME, Parsed, interface, vlan

STATE = re.compile(
    r"^(up(?:\(s\))?|down|\*down|adm|administratively down|admin down|"
    r"connected|notconnect|disabled)$",
    re.I,
)


def parse(text, family):
    result = Parsed()
    mode = None
    seen = set()
    for number, line in enumerate(text.splitlines(), 1):
        if "interfaces in route mode:" in line.lower():
            mode = "route"
        elif "interfaces in bridge mode:" in line.lower():
            mode = "bridge"
        elif re.fullmatch(r"Interface\s+Status\s+Protocol\s+Description\s*", line):
            mode = "description"  # IOS "show interfaces description"
        tokens = re.sub(r"administratively\s+down|admin\s+down", "ADM", line, flags=re.I).split()
        if not tokens or not IFNAME.fullmatch(tokens[0]):
            continue
        result.candidates += 1
        result.handled_lines.add(number)
        name = interface(tokens[0], family)
        states = [tokens[1].lower()] if len(tokens) > 1 and STATE.fullmatch(tokens[1]) else []
        if not states or name.name in seen:
            result.reject(number, "missing_state_or_duplicate_interface")
            continue
        link = states[0]
        record = {
            "ifname": name.name,
            "raw_name": tokens[0],
            "type": name.kind,
            "oper_status": "up" if link in {"up", "up(s)", "connected"} else "down",
        }
        if link in {"adm", "*down", "disabled"}:
            record["admin_status"] = "down"
        elif mode in {"route", "bridge", "description"}:
            # These tables report administrative shutdown in the state column itself
            # (Comware legend "ADM - administratively down", IOS "admin down"), so any
            # other state is a port that is administratively up. Other tables say nothing.
            record["admin_status"] = "up"
        if mode == "bridge":
            try:
                if len(tokens) < 6 or tokens[4] not in {"A", "T", "H"}:
                    raise ValueError("incomplete_bridge_row")
                record["speed_mbps"] = speed_mbps(tokens[2])
                record["l2_mode"] = {"A": "access", "T": "trunk", "H": "hybrid"}[tokens[4]]
                pvid = vlan(tokens[5])
                # PVID on trunk/hybrid is not evidence of an access VLAN.
                if tokens[4] == "A":
                    record["access_vlan"] = pvid
                record["description"] = " ".join(tokens[6:]) or None
            except ValueError as exc:
                result.reject(number, str(exc))
                continue
        elif mode == "route":
            if len(tokens) < 4:
                result.reject(number, "incomplete_route_interface_row")
                continue
            record["description"] = " ".join(tokens[4:]) or None
        elif mode == "description":
            if len(tokens) < 3:
                result.reject(number, "incomplete_description_row")
                continue
            record["description"] = " ".join(tokens[3:]) or None
        result.add(record, [number], {"text": line})
        seen.add(name.name)
    return result

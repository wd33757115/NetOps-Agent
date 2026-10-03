"""Link aggregation membership: the aggregate and its member ports (RFC 8944 lag).

One record per aggregate: ``ifname``, every listed member, the members bundled into
it (IOS flag P) and each member's raw flags (``member_flags``: s suspended, D down,
I stand-alone, H hot-standby, w waiting). Layout from lab samples (campus-wan 20261003-010038), IOS
"show etherchannel summary": a "Group  Port-channel  Protocol  Ports" table, members
wrapping onto indented lines, checked against "Number of channel-groups in use".
"""

import re

from .common import Parsed, interface

ROW = re.compile(r"\s*(\d+)\s+(\S+)\((\w+)\)\s+(\S+)\s*((?:\S+\(\w+\)\s*)*)")
MORE = re.compile(r"\s+((?:\S+\(\w+\)\s*)+)")
MEMBER = re.compile(r"(\S+)\((\w+)\)")
COUNT = re.compile(r"\s*Number of channel-groups in use:\s*(\d+)\s*")
TABLE = re.compile(r"Group\s+Port-channel\s+Protocol\s+Ports\s*")


def parse(text, family):
    result = Parsed()
    in_table, advertised, current = False, None, None
    for number, line in enumerate(text.splitlines(), 1):
        count = COUNT.fullmatch(line)
        if count:
            advertised = int(count[1])
            result.handled_lines.add(number)
            continue
        if TABLE.fullmatch(line):
            in_table = True
            result.handled_lines.add(number)
            continue
        if not in_table or not line.strip() or re.fullmatch(r"[-+\s]+", line):
            continue
        row = ROW.fullmatch(line)
        more = MORE.fullmatch(line) if current is not None else None
        if not row and not more:
            result.reject(number, "unrecognized_lag_line")
            continue
        result.handled_lines.add(number)
        try:
            if row:
                result.candidates += 1
                current = {
                    "ifname": interface(row[2], family).name,
                    "members": [],
                    "bundled": [],
                    "member_flags": {},
                }
                result.add(current, [number], {"text": line.strip()})
                members = row[5]
            else:
                result.sources[-1]["lines"].append(number)
                members = more[1]
            for name, flags in MEMBER.findall(members):
                port = interface(name, family).name
                current["members"].append(port)
                current["member_flags"][port] = flags
                if "P" in flags:
                    current["bundled"].append(port)
        except ValueError as exc:
            result.reject(number, str(exc))
    if advertised is not None and advertised != len(result.records):
        result.warnings.append("advertised_count_mismatch")
        result.rejected += 1
    # A switch with no channel groups prints the empty table with a zero count.
    result.empty_verified = in_table and advertised == 0 and not result.records
    return result

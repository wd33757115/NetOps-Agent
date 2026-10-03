"""OSPFv2 observations from the campus 20261003-015834 CLI captures.

These are summary facts, not new typed database records or a protocol YANG tree.
Comware's brief table does not identify interfaces: its address observations must
be checked against the full process output before attaching costs to an ifname.
No RFC 8346 metric direction or missing interface cost is inferred here.
"""

import ipaddress
import re

from .common import Parsed, interface

COMWARE_PROCESS = re.compile(r"OSPF Process (\d+) with Router ID (\S+)")
IOS_PROCESS = re.compile(r'Routing Process "ospf (\d+)" with ID (\S+)')
IOS_TABLE = re.compile(r"Interface\s+PID\s+Area\s+IP Address/Mask\s+Cost\s+State\s+Nbrs F/C")
COMWARE_TABLE = re.compile(r"IP Address\s+Type\s+State\s+Cost\s+Pri\s+DR\s+BDR")
COMWARE_AREA = re.compile(r"Area:\s*(\S+)(?:\s+\(MPLS TE not enabled\))?")
COMWARE_INTERFACE = re.compile(r"Interface:\s*(\S+)\s+\((\S+)\)")
COMWARE_COST = re.compile(r"Cost:\s*(\S+)\s+State:\s*(\S+)\s+Type:\s*(\S+)\s+MTU:\s*\d+")
DEFAULT_COMWARE = "output:Multi-VPN-Instance is not enabled"

# Deliberately bounded to fields actually present in this batch.  Recognizing a
# label is not permission to swallow arbitrary trailing text or scope headers.
COMWARE_NOISE = tuple(
    re.compile(pattern)
    for pattern in (
        r"OSPF Protocol Information",
        r"Route tag: \d+",
        r"Ext-community type: Domain ID 0x[0-9a-fA-F]+, "
        r"Route Type 0x[0-9a-fA-F]+, Router ID 0x[0-9a-fA-F]+",
        r"Domain ID: \d+\.\d+\.\d+\.\d+",
        r"Opaque capable",
        r"Isolation: Disabled",
        r"ISPF is enabled",
        r"SPF-schedule-interval: \d+ \d+ \d+",
        r"LSA generation interval: \d+ \d+ \d+",
        r"LSA arrival interval: \d+",
        r"Transmit pacing: Interval: \d+ Count: \d+",
        r"Default ASE parameters: Metric: \d+ Tag: \d+ Type: \d+",
        r"(?:ASE )?Route preference: \d+",
        r"ASE route preference: \d+",
        r"SPF calculation count: \d+",
        r"RFC 1583 compatible",
        r"Fast-reroute: Remote-lfa Disabled",
        r"Maximum-cost: \d+",
        r"Node-Protecing Preference: \d+",
        r"Lowest-cost Preference: \d+",
        r"Graceful restart interval: \d+",
        r"SNMP trap rate limit interval: \d+\s+Count: \d+",
        r"Area count: \d+\s+NSSA area count: \d+",
        r"ExChange/Loading neighbors: \d+",
        r"MPLS segment routing: Disabled",
        r"Segment routing adjacency\s*: Disabled",
        r"Effective SRGB\s*: \d+\s+\d+",
        r"Segment routing local block\s*: \d+\s+\d+",
        r"Segment routing tunnel count: \d+",
        r"Authentication type: None\s+Area flag: Normal",
        r"SPF scheduled count: \d+",
        r"Priority: \d+",
        r"(?:Designated|Backup designated) router: \d+\.\d+\.\d+\.\d+",
        r"Timers: Hello \d+, Dead \d+, Poll \d+, Retransmit \d+, Transmit Delay \d+",
        r"FRR (?:backup|TI-LFA|remote-lfa): Enabled",
        r"Enabled by interface configuration \(including secondary IP addresses\)",
    )
)
IOS_NOISE = tuple(
    re.compile(pattern)
    for pattern in (
        r"Start time: [0-9:.]+, Time elapsed: [0-9:.]+",
        r"Supports only single TOS\(TOS0\) routes",
        r"Supports opaque LSA",
        r"Supports Link-local Signaling \(LLS\)",
        r"Supports area transit capability",
        r"Supports NSSA \(compatible with RFC 3101\)",
        r"Supports Database Exchange Summary List Optimization \(RFC 5243\)",
        r"Maximum number of non self-generated LSA allowed \d+",
        r"Current number of non self-generated LSA \d+",
        r"Threshold for warning message \d+%",
        r"Ignore-time \d+ minutes, reset-time \d+ minutes",
        r"Ignore-count allowed \d+, current ignore-count \d+",
        r"Event-log enabled, Maximum number of events: \d+, Mode: cyclic",
        r"It is an area border router",
        r"Router is not originating router-LSAs with maximum metric",
        r"Initial SPF schedule delay \d+ msecs",
        r"Minimum hold time between two consecutive SPFs \d+ msecs",
        r"Maximum wait time between two consecutive SPFs \d+ msecs",
        r"Incremental-SPF disabled",
        r"Per-prefix-distribution disabled",
        r"Initial LSA throttle delay \d+ msecs",
        r"Minimum hold time for LSA throttle \d+ msecs",
        r"Maximum wait time for LSA throttle \d+ msecs",
        r"Minimum LSA arrival \d+ msecs",
        r"LSA group pacing timer \d+ secs",
        r"Interface flood pacing timer \d+ msecs",
        r"Retransmission pacing timer \d+ msecs",
        r"EXCHANGE/LOADING adjacency limit: initial \d+, process maximum \d+",
        r"Number of (?:external|opaque AS) LSA \d+\. Checksum Sum 0x[0-9a-fA-F]+",
        r"Number of (?:DCbitless external and opaque AS|DoNotAge external and opaque AS) LSA \d+",
        r"Number of areas in this router is \d+\. \d+ normal \d+ stub \d+ nssa",
        r"Number of areas transit capable is \d+",
        r"External flood list length \d+",
        r"(?:IETF|Cisco) NSF helper support enabled",
        r"Reference bandwidth unit is \d+ mbps",
        r"Number of interfaces in this area is \d+(?: \(\d+ loopback\))?",
        r"Area has no authentication",
        r"SPF algorithm last executed [0-9:.]+ ago",
        r"SPF algorithm executed \d+ times",
        r"Area ranges are",
        r"Number of LSA \d+\. Checksum Sum 0x[0-9a-fA-F]+",
        r"Number of opaque link LSA \d+\. Checksum Sum 0x[0-9a-fA-F]+",
        r"Number of (?:opaque link|DCbitless|indication|DoNotAge) LSA \d+",
        r"Flood list length \d+",
        r"Maintenance Mode ID:\s*\d+",
        r"Maintenance Mode:\s*disabled",
        r"Maintenance Mode Timer:\s*stopped \(0\)",
        r"Graceful Reload FSU Global status : None \(global: None\)",
    )
)


def _ipv4(value):
    return str(ipaddress.IPv4Address(value))


def _process_id(value):
    if not re.fullmatch(r"[0-9]+", value) or int(value) < 1:
        raise ValueError("invalid_ospf_process_id")
    return str(int(value))


def _area(value):
    if re.fullmatch(r"[0-9]+", value):
        if int(value) > 0xFFFFFFFF:
            raise ValueError("invalid_ospf_area")
    else:
        _ipv4(value)
    return value


def _cost(value):
    if not re.fullmatch(r"[0-9]+", value) or not 0 <= int(value) <= 65535:
        raise ValueError("invalid_ospf_cost")
    return int(value)


def _evidence(result, lines, raw):
    result.sources.append({"lines": sorted(set(lines)), "raw": {"text": raw}})


def _invalid_interface(result, record):
    key = {key: record[key] for key in ("process_id", "address", "ifname") if key in record}
    if not key:
        result.summary["invalid_structure"] = True
    elif key not in result.summary.setdefault("invalid_interfaces", []):
        result.summary["invalid_interfaces"].append(key)


def _invalid_process(result, process_id):
    if process_id:
        if re.fullmatch(r"[0-9]+", process_id):
            process_id = str(int(process_id))
        invalid = result.summary.setdefault("invalid_processes", [])
        if process_id not in invalid:
            invalid.append(process_id)
    else:
        result.summary["invalid_structure"] = True


def _finish_interfaces(result, rows):
    invalid = result.summary.get("invalid_interfaces", [])
    invalid_processes = result.summary.get("invalid_processes", [])
    valid = []
    for row, lines, raw in rows:
        if row["process_id"] in invalid_processes:
            continue
        if any(all(row.get(key) == value for key, value in bad.items()) for bad in invalid):
            continue
        valid.append(row)
        _evidence(result, lines, raw)
    if valid:
        result.summary["interfaces"] = valid


def parse_interfaces(text, family):
    """Brief interface costs; the IOS header and Comware sections are audited."""
    result = Parsed()
    if not text.strip() and family in {"ios", "iosxe"}:
        # Both actual ACC captures are empty, with completed command transports.
        result.empty_verified = True
        return result
    if family not in {"comware", "ios", "iosxe"}:
        result.reject(1, "ospf_format_not_verified")
        result.summary["invalid_structure"] = True
        return result
    process_id = router_id = area = None
    header_lines, headers, seen, rows, areas = [], set(), set(), [], set()
    interface_labels = set()
    table = False
    for number, original in enumerate(text.splitlines(), 1):
        line = original.strip()
        if not line:
            continue
        match = COMWARE_PROCESS.fullmatch(line) if family == "comware" else None
        if match:
            try:
                process_id, router_id = _process_id(match[1]), _ipv4(match[2])
                if process_id in headers:
                    _invalid_process(result, process_id)
                    raise ValueError("duplicate_ospf_process")
                headers.add(process_id)
                area, table = None, False
                header_lines = [number]
                result.handled_lines.add(number)
            except ValueError as exc:
                _invalid_process(result, match[1])
                process_id = router_id = area = None
                table = False
                result.reject(number, str(exc))
            continue
        if family == "comware" and line == "Interfaces" and process_id:
            if process_id in interface_labels:
                result.reject(number, "duplicate_ospf_interface_label")
                _invalid_process(result, process_id)
            interface_labels.add(process_id)
            result.handled_lines.add(number)
            continue
        match = COMWARE_AREA.fullmatch(line) if family == "comware" else None
        if match and process_id:
            try:
                area, table = _area(match[1]), False
                if (process_id, area) in areas:
                    raise ValueError("duplicate_ospf_area")
                areas.add((process_id, area))
                header_lines = [header_lines[0], number]
                result.handled_lines.add(number)
            except ValueError as exc:
                _invalid_process(result, process_id)
                result.reject(number, str(exc))
            continue
        pattern = COMWARE_TABLE if family == "comware" else IOS_TABLE
        if pattern.fullmatch(line):
            if table or (family == "comware" and (not process_id or area is None)):
                result.reject(number, "duplicate_or_unscoped_ospf_table")
                result.summary["invalid_structure"] = True
            table = True
            result.handled_lines.add(number)
            continue
        row = {}
        try:
            tokens = line.split()
            if not table:
                raise ValueError("unscoped_ospf_interface_row")
            result.candidates += 1
            if family == "comware":
                row.update(process_id=process_id, router_id=router_id, area=area)
                row["address"] = _ipv4(tokens[0])
                if len(tokens) != 7:
                    raise ValueError("invalid_ospf_interface_columns")
                row.update(cost=_cost(tokens[3]), state=tokens[2])
                if not tokens[4].isdigit():
                    raise ValueError("invalid_ospf_priority")
                _ipv4(tokens[5])
                _ipv4(tokens[6])
                lines = [*header_lines, number]
            else:
                row.update(ifname=interface(tokens[0], family).name, raw_name=tokens[0])
                row["process_id"] = _process_id(tokens[1])
                if len(tokens) != 7:
                    raise ValueError("invalid_ospf_interface_columns")
                if not re.fullmatch(r"[0-9.]+/[0-9]+", tokens[3]):
                    raise ValueError("invalid_ospf_address_mask")
                address = ipaddress.IPv4Interface(tokens[3])
                row.update(
                    area=_area(tokens[2]),
                    address=str(address.ip),
                    prefix_length=address.network.prefixlen,
                )
                row.update(cost=_cost(tokens[4]), state=tokens[5])
                if not re.fullmatch(r"\d+/\d+", tokens[6]):
                    raise ValueError("invalid_ospf_neighbor_counts")
                lines = [number]
            key = (row["process_id"], row["address"])
            if key in seen:
                _invalid_interface(result, {"process_id": key[0], "address": key[1]})
                raise ValueError("duplicate_ospf_interface")
            seen.add(key)
            rows.append((row, lines, original))
            result.handled_lines.add(number)
        except (ValueError, IndexError) as exc:
            # Unknown syntax has no reliable key and must block the whole output.
            if row.get("address") or row.get("ifname"):
                _invalid_interface(result, row)
            else:
                result.summary["invalid_structure"] = True
            result.reject(number, str(exc) or "invalid_ospf_interface_row")
    if not table:
        result.reject(1, "ospf_table_missing")
        result.summary["invalid_structure"] = True
    _finish_interfaces(result, rows)
    result.empty_verified = bool(table and not rows and not result.rejected)
    return result


def parse_process(text, family):
    """Router IDs, explicit scope evidence, and Comware address/ifname mappings."""
    result = Parsed()
    if not text.strip() and family in {"ios", "iosxe"}:
        result.empty_verified = True
        return result
    if family not in {"comware", "ios", "iosxe"}:
        result.reject(1, "ospf_format_not_verified")
        result.summary["invalid_structure"] = True
        return result
    processes, rows, seen, current, area, block = {}, [], set(), None, None, None
    areas = set()

    def close_block():
        nonlocal block
        if block is None:
            return
        row, lines, raw = block
        if "cost" not in row:
            result.reject(lines[0], "ospf_interface_cost_missing")
            _invalid_interface(result, row)
        else:
            key = (row["process_id"], row["address"])
            if key in seen:
                result.reject(lines[0], "duplicate_ospf_interface")
                _invalid_interface(result, {"process_id": key[0], "address": key[1]})
            seen.add(key)
            rows.append((row, lines, "\n".join(raw)))
        block = None

    for number, original in enumerate(text.splitlines(), 1):
        line = original.strip()
        if not line:
            continue
        pattern = COMWARE_PROCESS if family == "comware" else IOS_PROCESS
        match = pattern.fullmatch(line)
        if match:
            close_block()
            try:
                pid, rid = _process_id(match[1]), _ipv4(match[2])
                if pid in processes:
                    _invalid_process(result, pid)
                    raise ValueError("duplicate_ospf_process")
                current = {"process_id": pid, "router_id": rid}
                processes[pid] = (current, [number], [original])
                area = None
                result.candidates += 1
                result.handled_lines.add(number)
            except ValueError as exc:
                _invalid_process(result, match[1])
                current = None
                result.reject(number, str(exc))
            continue
        if current is None:
            result.reject(number, "unscoped_ospf_process_line")
            result.summary["invalid_structure"] = True
            continue
        pid = current["process_id"]
        if family == "comware" and line == "Multi-VPN-Instance is not enabled":
            if "vrf" in current:
                _invalid_process(result, pid)
                result.reject(number, "duplicate_ospf_vrf_evidence")
            current.update(vrf="default", vrf_evidence=DEFAULT_COMWARE)
            processes[pid][1].append(number)
            processes[pid][2].append(original)
            result.handled_lines.add(number)
            continue
        match = re.fullmatch(r"RouterID:\s*(\S+)\s+Router type:\s*", line)
        if family == "comware" and match:
            try:
                if "router_id_confirmed" in current or _ipv4(match[1]) != current["router_id"]:
                    raise ValueError("duplicate_or_conflicting_ospf_router_id")
                current["router_id_confirmed"] = True
                processes[pid][1].append(number)
                processes[pid][2].append(original)
                result.handled_lines.add(number)
            except ValueError as exc:
                _invalid_process(result, pid)
                result.reject(number, str(exc))
            continue
        match = (
            COMWARE_AREA.fullmatch(line)
            if family == "comware"
            else re.fullmatch(r"Area (?:BACKBONE\((0)\)|([0-9.]+))", line)
        )
        if match:
            close_block()
            try:
                area = _area(next(value for value in match.groups() if value is not None))
                if (pid, area) in areas:
                    raise ValueError("duplicate_ospf_area")
                areas.add((pid, area))
                result.handled_lines.add(number)
            except ValueError as exc:
                _invalid_process(result, pid)
                result.reject(number, str(exc))
            continue
        match = COMWARE_INTERFACE.fullmatch(line) if family == "comware" else None
        if match:
            close_block()
            row = {"process_id": pid, "router_id": current["router_id"]}
            try:
                row["address"] = _ipv4(match[1])
                row.update(ifname=interface(match[2], family).name, raw_name=match[2])
                if area is None:
                    raise ValueError("ospf_interface_area_missing")
                row["area"] = area
                block = (row, [number], [original])
                result.candidates += 1
                result.handled_lines.add(number)
            except ValueError as exc:
                _invalid_interface(result, row)
                result.reject(number, str(exc))
            continue
        match = COMWARE_COST.fullmatch(line) if family == "comware" else None
        if match and block is not None:
            try:
                if "cost" in block[0]:
                    raise ValueError("duplicate_ospf_cost")
                block[0].update(cost=_cost(match[1]), state=match[2])
                block[1].append(number)
                block[2].append(original)
                result.handled_lines.add(number)
            except ValueError as exc:
                _invalid_interface(result, block[0])
                result.reject(number, str(exc))
            continue
        noise = COMWARE_NOISE if family == "comware" else IOS_NOISE
        if any(pattern.fullmatch(line) for pattern in noise):
            result.handled_lines.add(number)
            continue
        result.reject(number, "unrecognized_ospf_process_line")
        # Unknown text could introduce a new process or VPN scope.  Keeping the
        # current block does not prove that an unfamiliar line belongs to it.
        result.summary["invalid_structure"] = True
        if block is not None:
            _invalid_interface(result, block[0])
    close_block()
    if not processes:
        result.reject(1, "ospf_process_header_missing")
        result.summary["invalid_structure"] = True
    good = []
    for pid, (record, lines, raw) in processes.items():
        record.pop("router_id_confirmed", None)
        if pid in result.summary.get("invalid_processes", []):
            continue
        good.append(record)
        _evidence(result, lines, "\n".join(raw))
    if good:
        result.summary["processes"] = good
    _finish_interfaces(result, rows)
    return result

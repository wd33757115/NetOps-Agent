"""Exact LLDP keys, block boundaries, and indented continuation evidence."""

import re
from functools import lru_cache
from pathlib import Path

import yaml

from .common import Parsed, interface, valid_ip, valid_mac

HEADER = re.compile(r"^\s*LLDP (?:neighbor|local)-information of port \d+\s*\[([^]]+)\]:\s*$", re.I)
VRP_HEADER = re.compile(r"^\s*(\S+)\s+has\s+\d+\s+neighbor(?:s|\(s\))?:?\s*$", re.I)
KEY = re.compile(r"^(\s*)([A-Za-z][A-Za-z0-9 ()/_-]*?)\s*:\s*(.*)$")


@lru_cache(maxsize=1)
def aliases():
    data = yaml.safe_load(Path(__file__).with_name("lldp_aliases.yaml").read_text("utf-8"))
    return {key: field for field, keys in data.items() for key in keys}


def fields(text):
    """Yield (field, value, line numbers); unknown keys terminate a continuation."""
    pending = None
    for number, line in enumerate(text.splitlines(), 1):
        header = HEADER.fullmatch(line) or VRP_HEADER.fullmatch(line)
        match = KEY.fullmatch(line)
        if header or match:
            if pending:
                yield pending[:3]
            if header:
                pending = ["local_if", header.group(1), [number], 0]
            else:
                key = re.sub(r"\s+", " ", match.group(2)).lower().strip()
                pending = [
                    aliases().get(key, "unknown"),
                    match.group(3).strip(),
                    [number],
                    len(match.group(1)),
                ]
        elif (
            pending
            and pending[0] == "description"
            and line.strip()
            and len(line) - len(line.lstrip()) > pending[3]
        ):
            pending[1] = (pending[1] + "\n" + line.strip()).lstrip("\n")
            pending[2].append(number)
        else:
            if pending:
                yield pending[:3]
            pending = None
    if pending:
        yield pending[:3]


def parse(text, family):
    """A header (Comware/VRP) gives the local port to every block under it.

    A "Local ..." key belongs to one neighbour and may come before the chassis
    (IOS "Local Intf") or after chassis and port (NX-OS "Local Port id").
    """
    result = Parsed()
    local = None
    local_lines = []
    block = {}
    lines = []
    block_local = False
    header_local = False

    def flush():
        if not block:
            return
        result.candidates += 1
        if not local or not block_local or not block.get("chassis") or not block.get("port"):
            result.reject(lines[0], "incomplete_lldp_block")
            return
        try:
            local_name = interface(local, family).name
        except ValueError:
            result.reject(lines[0], "invalid_local_interface")
            return
        chassis = block["chassis"]
        if "mac" in block.get("chassis_subtype", "").lower() and not valid_mac(chassis):
            result.reject(lines[0], "invalid_chassis_mac")
            return
        if any(
            len(block.get(key, "")) > limit
            for key, limit in {
                "chassis": 256,
                "port": 256,
                "sysname": 256,
                "chassis_subtype": 32,
                "port_subtype": 32,
            }.items()
        ):
            result.reject(lines[0], "lldp_field_over_limit")
            return
        record = {
            "local_if": local_name,
            "remote_chassis_id": valid_mac(chassis) or chassis,
            "remote_chassis_subtype": block.get("chassis_subtype"),
            "remote_port_id": block["port"],
            "remote_port_subtype": block.get("port_subtype"),
            "remote_sysname": block.get("sysname"),
            "remote_system_description": block.get("description"),
            "remote_mgmt_addresses": [],
        }
        for raw in block.get("mgmt", []):
            address = valid_ip(raw)
            if address:
                if address not in record["remote_mgmt_addresses"]:
                    record["remote_mgmt_addresses"].append(address)
            else:
                result.reject(lines[0], "invalid_management_address")
        result.add(record, lines, {"local_if": local, **block})

    for key, value, source in fields(text):
        if key == "local_port" and "chassis" in block and not block_local:
            local, block_local = value, True
            lines.extend(source)
        elif key in {"local_if", "local_port"}:
            flush()
            local, block, lines = value, {}, source[:]
            local_lines = source[:]
            header_local = key == "local_if"
            block_local = True
        elif key in {"chassis_subtype", "chassis"} and "chassis" in block:
            flush()
            block, block_local = {key: value}, header_local
            lines = (local_lines if header_local else []) + source
        elif key in {
            "chassis",
            "port",
            "sysname",
            "description",
            "mgmt",
            "chassis_subtype",
            "port_subtype",
        }:
            if key == "mgmt":
                block.setdefault(key, []).append(value)
            else:
                if key in block and block[key] != value:
                    result.reject(source[0], "conflicting_lldp_field")
                block[key] = value
            lines.extend(source)
    flush()
    return result

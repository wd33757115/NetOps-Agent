"""Versioned LLDP adapters. Unrecognized or incomplete output is never an empty graph."""

import ipaddress
import re

from src.platform.device_access.catalog import DISCOVERY_PROFILES as PROFILES
from src.platform.device_access.contracts import DeviceAccessError


def parse(profile, outputs, complete=True):
    if profile not in PROFILES or not complete:
        raise DeviceAccessError("parse_incomplete", "采集不完整，不能作为拓扑观测")
    text = outputs.get("neighbors", "")
    if len(text.encode()) > 4 * 1024 * 1024:
        raise DeviceAccessError("output_limit", "输出超过限制")
    if re.search(
        r"(?im)(unrecognized command|invalid command|% invalid|permission denied|error:)", text
    ):
        raise DeviceAccessError("command_rejected", "设备拒绝了拓扑命令")
    chassis_count = len(re.findall(r"(?im)^\s*Chassis (?:ID|id)\s*:", text))
    if not chassis_count and re.search(
        r"(?im)(total (?:number of )?entries displayed\s*:\s*0\s*$|"
        r"^\s*no (?:lldp )?neighbors?\s*$|total\s+(?:number of\s+)?neighbors\s*:\s*0\s*$)",
        text,
    ):
        return {"neighbors": [], "parser": profile, "complete": True}
    if profile == "comware-lldp-v1":
        pieces = re.split(
            r"(?im)^\s*LLDP neighbor-information of port\s+\d+\s*\[([^\]]+)\]:\s*$", text
        )
        entries = []
        for pos in range(1, len(pieces), 2):
            body = pieces[pos + 1]
            matches = list(re.finditer(r"(?im)^\s*Chassis (?:ID|id)\s*:\s*", body))
            for i, match in enumerate(matches):
                prefix = body[matches[i - 1].end() if i else 0 : match.start()]
                types = re.findall(r"(?im)^\s*Chassis type\s*:\s*(.+?)\s*$", prefix)
                end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
                entries.append(
                    (pieces[pos], body[match.end() : end], types[-1] if types else "unknown")
                )
    else:
        entries = []
        for block in re.split(r"(?im)^\s*Chassis (?:ID|id)\s*:\s*", text)[1:]:
            local = re.search(r"(?im)^\s*Local Port id\s*:\s*(\S+)\s*$", block)
            if not local:
                raise DeviceAccessError("parse_failed", "LLDP 邻居缺少本地端口")
            entries.append((local.group(1), block, "unknown"))
    neighbors = []
    for local, block, subtype in entries:
        chassis = block.splitlines()[0].strip()
        port = re.search(r"(?im)^\s*Port (?:ID|id)\s*:\s*(.+?)\s*$", block)
        if not port or not chassis:
            raise DeviceAccessError("parse_failed", "LLDP 邻居缺少机箱或端口标识")
        name = re.search(r"(?im)^\s*System (?:name|Name)\s*:\s*(.+?)\s*$", block)
        description = re.search(
            r"(?im)^\s*System (?:description|Description)\s*:[ \t]*(.*)$", block
        )
        port_type = re.search(r"(?im)^\s*Port ID type\s*:\s*(.+?)\s*$", block)
        addresses = []
        for value in re.findall(
            r"(?im)^\s*(?:Management address|Management Address|IP)\s*:\s*(\S+)", block
        ):
            try:
                addresses.append(str(ipaddress.ip_address(value)))
            except ValueError:
                continue
        neighbors.append(
            {
                "local_port": local.strip(),
                "chassis_id": chassis,
                "chassis_subtype": subtype,
                "port_subtype": port_type.group(1) if port_type else "unknown",
                "remote_port": port.group(1).strip(),
                "name": name.group(1) if name else chassis,
                "system_description": description.group(1).strip()[:1024] if description else None,
                "addresses": sorted(set(addresses)),
            }
        )
    # Recognizing the header alone is insufficient to infer zero neighbors.
    if not neighbors or len(neighbors) != chassis_count:
        raise DeviceAccessError("parse_failed", "无法识别该版本的 LLDP 输出，请保留证据并补充适配")
    count = re.search(r"(?im)^\s*Total entries displayed\s*:\s*(\d+)", text)
    if count and int(count.group(1)) != len(neighbors):
        raise DeviceAccessError("parse_incomplete", "LLDP 邻居数量与解析结果不一致")
    if any(
        len(str(v)) > 256
        for n in neighbors
        for v in (n["local_port"], n["remote_port"], n["chassis_id"], n["name"])
    ):
        raise DeviceAccessError("parse_failed", "LLDP 标识超过限制")
    return {"neighbors": neighbors, "parser": profile, "complete": True}


def rfc8345(graph):
    """RFC 7951 JSON using the RFC 8345 base modules; no unregistered augments."""
    return {
        "ietf-network:networks": {
            "network": [
                {
                    "network-id": graph["network_id"],
                    "network-types": graph.get("network_types", {}),
                    "supporting-network": graph.get("supporting_networks", []),
                    "node": [
                        {
                            "node-id": n["id"],
                            "supporting-node": n.get("supporting_nodes", []),
                            "ietf-network-topology:termination-point": [
                                {
                                    "tp-id": tp["id"],
                                    "supporting-termination-point": tp.get("supporting_tps", []),
                                }
                                for tp in n["tps"]
                            ],
                        }
                        for n in graph["nodes"]
                    ],
                    "ietf-network-topology:link": [
                        {
                            "link-id": edge["id"],
                            "supporting-link": edge.get("supporting_links", []),
                            "source": {
                                "source-node": edge["source"],
                                "source-tp": edge["source_tp"],
                            },
                            "destination": {
                                "dest-node": edge["target"],
                                "dest-tp": edge["target_tp"],
                            },
                        }
                        for edge in graph["links"]
                        if edge["freshness"] == "observed"
                    ],
                }
            ]
        }
    }


def diff(before, after):
    result = {}
    for kind in ("nodes", "links"):
        old, new = ({x["id"]: x for x in g.get(kind, [])} for g in (before, after))
        result[kind] = {
            "added": sorted(new.keys() - old.keys()),
            "removed": sorted(old.keys() - new.keys()),
            "changed": sorted(key for key in old.keys() & new.keys() if old[key] != new[key]),
        }
    return result

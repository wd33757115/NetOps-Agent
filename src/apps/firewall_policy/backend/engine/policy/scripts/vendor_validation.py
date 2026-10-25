"""Read the emitted subset of vendor CLI back into match sets before publishing."""

import re

from policy_optimizer import BudgetExceeded, Rule, verify
from policy_sets import addresses, ports, union
from policy_window import extend, read_h3c, read_huawei


def validate_output(vendor, text, expected):
    address_objects, service_objects, time_ranges = {}, {}, {}
    object_kind = object_name = None
    current = None
    parsed = []

    def finish():
        if current is None:
            return
        if not all(
            current.get(key) for key in ("src", "dst", "src_zone", "dst_zone", "proto", "action")
        ):
            raise ValueError("CLI 回读发现缺少地址、安全域、服务或放行动作")
        if current["action"] not in {"permit", "pass"}:
            raise ValueError("CLI 回读发现非放行动作")
        ps = union(current["ports"]) if current["proto"] != "icmp" else ((0, 1),)
        if not ps:
            raise ValueError("CLI 回读发现端口条件缺失")
        if current.get("long_link_enabled", False) != ("long_link" in current and vendor == "华为"):
            raise ValueError("CLI 回读发现长连接命令不完整")
        parsed.append(
            Rule(
                extend(
                    (
                        expected[0].context[0],
                        expected[0].context[1],
                        current["src_zone"],
                        current["dst_zone"],
                        current["proto"],
                    ),
                    current.get("window"),
                    current.get("long_link"),
                ),
                union(current["src"]),
                union(current["dst"]),
                ps,
            )
        )

    def set_service(proto, ranges):
        if current.get("proto") and current["proto"] != proto:
            raise ValueError("CLI 回读发现一条规则混合协议")
        current["proto"] = proto
        current["ports"].extend(ranges)

    for raw in text.splitlines():
        line = raw.strip()
        # Time ranges: H3C defines one per line, Huawei in a view closed by quit.
        if line.startswith("time-range ") and " from " in line:
            name, window = read_h3c(line)
            time_ranges[name] = window
            continue
        if re.fullmatch(r"time-range \S+", line) and current is None and not object_kind:
            object_kind, object_name = "time_range", line.split()[1]
            continue
        if line.startswith("object-group ip address "):
            object_kind, object_name = "address", line.split()[-1]
            address_objects[object_name] = []
            continue
        if line.startswith("object-group service "):
            object_kind, object_name = "service", line.split()[-1]
            service_objects[object_name] = []
            continue
        if vendor == "山石" and line.startswith("service NP_") and current is None:
            object_kind, object_name = "stone_service", line.split()[-1]
            service_objects[object_name] = []
            continue
        if line in ("quit", "exit"):
            if object_kind:
                object_kind = object_name = None
            elif vendor == "山石" and current is not None:
                finish()
                current = None
            continue
        if object_kind:
            if object_kind == "time_range":
                time_ranges[object_name] = read_huawei(line)
            elif object_kind == "address":
                match = re.fullmatch(r"\d+ network (host|range|subnet) (.+)", line)
                if not match:
                    raise ValueError("无法回读地址对象命令")
                kind, value = match.groups()
                a = value.split()
                value = a[0] if kind == "host" else a[0] + ("-" if kind == "range" else "/") + a[1]
                address_objects[object_name].extend(addresses(value))
            else:
                match = re.fullmatch(
                    r"(?:\d+ service )?(tcp|udp) (?:destination (?:eq|range)|dst-port) (\d+)(?: (\d+))?",
                    line,
                )
                if match:
                    proto, a, b = match.groups()
                    service_objects[object_name].append((proto, ports(a + ("-" + b if b else ""))))
                elif re.fullmatch(r"\d+ service icmp", line):
                    service_objects[object_name].append(("icmp", ()))
                else:
                    raise ValueError("无法回读服务对象命令")
            continue
        if re.fullmatch(r"rule(?: name .+| \d+ name .+)?", line):
            finish()
            current = {"src": [], "dst": [], "ports": []}
            continue
        if current is None:
            continue
        match = re.fullmatch(r"time-range (\S+)", line)
        if match:
            if match[1] not in time_ranges:
                raise ValueError("CLI 回读发现规则引用了未定义的时间段")
            current["window"] = time_ranges[match[1]]
            continue
        if line == "long-link enable":
            current["long_link_enabled"] = True
            continue
        match = re.fullmatch(r"(?:long-link|session persistent) aging-time (\d+)", line)
        if match:
            current["long_link"] = int(match[1])
            continue
        match = re.fullmatch(r"(source-zone|destination-zone|src-zone|dst-zone) (\S+)", line)
        if match:
            current["src_zone" if match[1] in ("source-zone", "src-zone") else "dst_zone"] = match[
                2
            ]
            continue
        match = re.fullmatch(r"(source|destination)-address (.+)", line)
        if match:
            side, value = match.groups()
            words = value.split()
            value = words[1] + "-" + words[2] if words[0] == "range" else words[0] + "/" + words[1]
            current["src" if side == "source" else "dst"].extend(addresses(value))
            continue
        match = re.fullmatch(r"(source|destination)-ip-(host|range|subnet) (.+)", line)
        if match:
            side, kind, value = match.groups()
            words = value.split()
            value = (
                words[0]
                if kind == "host"
                else words[0] + ("-" if kind == "range" else "/") + words[1]
            )
            current["src" if side == "source" else "dst"].extend(addresses(value))
            continue
        match = re.fullmatch(r"(source|destination)-ip (\S+)", line)
        if match:
            current["src" if match[1] == "source" else "dst"].extend(address_objects[match[2]])
            continue
        match = re.fullmatch(r"(src|dst)-(ip|range) (.+)", line)
        if match:
            side, kind, value = match.groups()
            current[side].extend(addresses(value.replace(" ", "-") if kind == "range" else value))
            continue
        match = re.fullmatch(
            r"service protocol (tcp|udp) destination-port (\d+)(?: to (\d+))?", line
        )
        if not match:
            match = re.fullmatch(
                r"service-port (tcp|udp) destination (?:eq|range) (\d+)(?: (\d+))?", line
            )
        if match:
            proto, a, b = match.groups()
            set_service(proto, ports(a + ("-" + b if b else "")))
            continue
        match = re.fullmatch(r"service (tcp|udp) port (.+)", line)
        if match:
            set_service(match[1], ports(match[2]))
            continue
        if line.startswith("service "):
            service = line.split()[1]
            if service.lower() == "icmp":
                set_service("icmp", ())
            elif service in service_objects:
                for proto, ps in service_objects[service]:
                    set_service(proto, ps)
            else:
                raise ValueError("CLI 回读发现未知服务")
        if line.startswith("action "):
            current["action"] = line.split()[1]
    finish()
    if len(parsed) != len(expected):
        raise ValueError("CLI 回读规则数量与策略计划不一致")
    # Rule-by-rule is bounded and retains correspondence, stronger than sampling.
    for wanted, actual in zip(expected, parsed):
        try:
            if not verify([wanted], [actual])["equivalent"]:
                raise ValueError("CLI 回读结果与策略计划不等价")
        except BudgetExceeded as exc:
            raise ValueError("CLI 回读校验超出预算，请拆分申请") from exc

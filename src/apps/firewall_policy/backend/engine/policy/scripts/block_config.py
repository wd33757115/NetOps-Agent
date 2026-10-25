"""Address books and zone-free deny rules of one block batch, for Huawei USG and H3C Comware.

A rule names no security zone, destination or service, so it matches traffic from the listed
sources in every zone. Each batch renders its own objects, a cleanup that removes exactly
them, and a read-back that re-parses both texts and checks them against the plan.
"""

import re
from ipaddress import IPv4Address

from policy_sets import subtract, union
from policy_window import h3c_range, huawei_range, read_h3c, read_huawei

VENDORS = {"华为": "huawei", "H3C": "h3c", "H3CF1000": "h3c"}


def member(low, high):
    """The fewest-words form of one interval: host, subnet when exact, otherwise a range."""
    size, first = high - low, str(IPv4Address(low))
    if size & (size - 1) == 0 and low % size == 0:
        return ("host", first) if size == 1 else ("subnet", first, 33 - size.bit_length())
    return ("range", first, str(IPv4Address(high - 1)))


def huawei(batch, description):
    lines = ["system-view"]
    for item in batch["time_ranges"]:
        lines += [f"time-range {item['name']}", f" {huawei_range(item['window'])}", "quit"]
    for book in batch["books"]:
        lines += [f"ip address-set {book['name']} type object", f" description {description}"]
        for index, (low, high) in enumerate(book["members"]):
            kind, *args = member(low, high)
            if kind == "range":
                lines.append(f" address {index} range {args[0]} {args[1]}")
            else:
                lines.append(f" address {index} {args[0]} mask {args[1] if args[1:] else 32}")
        lines.append("quit")
    lines.append("security-policy")
    for rule in batch["rules"]:
        lines += [f" rule name {rule['name']}", f"  description {description}"]
        lines += [f"  source-address address-set {name}" for name in rule["books"]]
        if rule["time_range"]:
            lines.append(f"  time-range {rule['time_range']}")
        lines += ["  action deny", " quit", f" rule move {rule['name']} top"]
    lines += ["quit", "quit", "save", "y", "y"]
    cleanup = ["system-view", "security-policy"]
    cleanup += [f" undo rule name {rule['name']}" for rule in batch["rules"]]
    cleanup.append("quit")
    cleanup += [f"undo ip address-set {book['name']}" for book in batch["books"]]
    cleanup += [f"undo time-range {item['name']}" for item in batch["time_ranges"]]
    cleanup += ["quit", "save", "y", "y"]
    return "\n".join(lines) + "\n", "\n".join(cleanup) + "\n"


def h3c(batch, description):
    lines = ["system-view"]
    lines += [h3c_range(item["name"], item["window"]) for item in batch["time_ranges"]]
    for book in batch["books"]:
        lines += [f"object-group ip address {book['name']}", f" description {description}"]
        for index, (low, high) in enumerate(book["members"], 1):
            kind, *args = member(low, high)
            target = {
                "host": f"host address {args[0]}",
                "subnet": f"subnet {args[0]} {args[-1]}",
                "range": f"range {args[0]} {args[-1]}",
            }[kind]
            lines.append(f" {index * 10} network {target}")
        lines.append("quit")
    lines.append("security-policy ip")
    for rule in batch["rules"]:
        lines += [f" rule name {rule['name']}", f"  description {description}", "  action drop"]
        lines += [f"  source-ip {name}" for name in rule["books"]]
        if rule["time_range"]:
            lines.append(f"  time-range {rule['time_range']}")
        lines += ["  logging enable", "  counting enable", " quit"]
        lines.append(f" move rule name {rule['name']} top")
    lines += ["return", "save force"]
    cleanup = ["system-view", "security-policy ip"]
    cleanup += [f" undo rule name {rule['name']}" for rule in batch["rules"]]
    cleanup.append("quit")
    cleanup += [f"undo object-group ip address {book['name']}" for book in batch["books"]]
    cleanup += [f"undo time-range {item['name']}" for item in batch["time_ranges"]]
    cleanup += ["return", "save force"]
    return "\n".join(lines) + "\n", "\n".join(cleanup) + "\n"


def render(vendor, batch, description):
    return (huawei if VENDORS[vendor] == "huawei" else h3c)(batch, description)


# Read-back: each view accepts only the statements this module writes. Anything else,
# such as a zone, destination, service or permit, fails the batch before it is saved.
def _interval(kind, first, extra=None):
    low = int(IPv4Address(first))
    if kind == "range":
        return low, int(IPv4Address(extra)) + 1
    length = 32 if extra is None else int(extra)
    if not 0 < length <= 32 or low % (1 << (32 - length)):
        raise ValueError("地址本成员掩码无效")
    return low, low + (1 << (32 - length))


def read(vendor, text):
    family = VENDORS[vendor]
    seen = dict(ranges={}, books={}, rules={}, moved=[], undone=dict(rules=[], books=[], ranges=[]))
    views = []

    def fail(line):
        raise ValueError(f"命令回读失败：{line.strip()}")

    def statement(pattern, line):
        return re.fullmatch(pattern, line.strip())

    for line in text.splitlines():
        if not line.strip():
            continue
        view = views[-1][0] if views else "user"
        target = views[-1][1] if views else None
        if line.strip() == "quit":
            if not views:
                fail(line)
            views.pop()
        elif line.strip() == "return" and family == "h3c":
            views.clear()
        elif view == "user":
            if line.strip() == "system-view":
                views.append(("system", None))
            elif line.strip() not in ({"save", "y"} if family == "huawei" else {"save force"}):
                fail(line)
        elif view == "system":
            if family == "huawei" and (m := statement(r"time-range (\S+)", line)):
                seen["ranges"][m[1]] = None
                views.append(("range", m[1]))
            elif family == "h3c" and line.strip().startswith("time-range "):
                name, window = read_h3c(line.strip())
                seen["ranges"][name] = window
            elif m := statement(
                r"ip address-set (\S+) type object"
                if family == "huawei"
                else r"object-group ip address (\S+)",
                line,
            ):
                if m[1] in seen["books"]:
                    fail(line)
                seen["books"][m[1]] = []
                views.append(("book", m[1]))
            elif line.strip() == (
                "security-policy" if family == "huawei" else "security-policy ip"
            ):
                views.append(("policy", None))
            elif m := statement(
                r"undo ip address-set (\S+)"
                if family == "huawei"
                else r"undo object-group ip address (\S+)",
                line,
            ):
                seen["undone"]["books"].append(m[1])
            elif m := statement(r"undo time-range (\S+)", line):
                seen["undone"]["ranges"].append(m[1])
            else:
                fail(line)
        elif view == "range":
            if not line.strip().startswith("absolute-range "):
                fail(line)
            seen["ranges"][target] = read_huawei(line.strip())
        elif view == "book":
            if statement(r"description .+", line):
                continue
            if family == "huawei":
                m = statement(r"address (\d+) (\S+) mask (\d+)", line)
                interval = m and _interval("subnet", m[2], m[3])
                if not m and (m := statement(r"address (\d+) range (\S+) (\S+)", line)):
                    interval = _interval("range", m[2], m[3])
            else:
                m = statement(r"(\d+) network host address (\S+)", line)
                interval = m and _interval("host", m[2])
                if not m and (m := statement(r"(\d+) network subnet (\S+) (\d+)", line)):
                    interval = _interval("subnet", m[2], m[3])
                if not m and (m := statement(r"(\d+) network range (\S+) (\S+)", line)):
                    interval = _interval("range", m[2], m[3])
            if not m:
                fail(line)
            seen["books"][target].append((int(m[1]), interval))
        elif view == "policy":
            if m := statement(r"rule name (\S+)", line):
                if m[1] in seen["rules"]:
                    fail(line)
                seen["rules"][m[1]] = dict(books=[], time_range=None, action=None)
                views.append(("rule", m[1]))
            elif m := statement(
                r"rule move (\S+) top" if family == "huawei" else r"move rule name (\S+) top", line
            ):
                seen["moved"].append(m[1])
            elif m := statement(r"undo rule name (\S+)", line):
                seen["undone"]["rules"].append(m[1])
            else:
                fail(line)
        elif view == "rule":
            rule = seen["rules"][target]
            source = (
                r"source-address address-set (\S+)" if family == "huawei" else r"source-ip (\S+)"
            )
            if m := statement(source, line):
                rule["books"].append(m[1])
            elif m := statement(r"time-range (\S+)", line):
                rule["time_range"] = m[1]
            elif m := statement(r"action (\S+)", line):
                rule["action"] = m[1]
            elif not statement(
                r"description .+"
                if family == "huawei"
                else r"description .+|logging enable|counting enable",
                line,
            ):
                fail(line)
        else:
            fail(line)
    if views:
        raise ValueError("命令回读失败：视图未退出")
    return seen


def verify(vendor, batch, commands, cleanup, limit):
    """Raise unless the texts create exactly the planned objects and remove exactly them."""
    deny = "deny" if VENDORS[vendor] == "huawei" else "drop"
    seen = read(vendor, commands)
    if any(seen["undone"].values()):
        raise ValueError("封禁命令中不应包含删除操作")
    planned = {book["name"]: book for book in batch["books"]}
    if set(seen["books"]) != set(planned):
        raise ValueError("地址本与计划不一致")
    for name, members in seen["books"].items():
        ids = [identity for identity, _ in members]
        if len(members) > limit or len(set(ids)) != len(ids):
            raise ValueError(f"地址本 {name} 成员超过 {limit} 条或编号重复")
        if [interval for _, interval in members] != list(planned[name]["members"]):
            raise ValueError(f"地址本 {name} 的成员与计划不一致")
    windows = {item["name"]: tuple(item["window"]) for item in batch["time_ranges"]}
    if {k: tuple(v or ()) for k, v in seen["ranges"].items()} != windows:
        raise ValueError("时间段与到期时间不一致")
    rules = {rule["name"]: rule for rule in batch["rules"]}
    if set(seen["rules"]) != set(rules) or sorted(seen["moved"]) != sorted(rules):
        raise ValueError("封禁规则与计划不一致，或未全部移到最前")
    blocked = []
    for name, rule in seen["rules"].items():
        if rule["action"] != deny:
            raise ValueError(f"规则 {name} 的动作不是拒绝")
        if rule["books"] != rules[name]["books"] or rule["time_range"] != rules[name]["time_range"]:
            raise ValueError(f"规则 {name} 引用的地址本或时间段与计划不一致")
        blocked += [interval for book in rule["books"] for _, interval in seen["books"][book]]
    if union(blocked) != tuple(batch["blocked"]):
        raise ValueError("封禁范围与名单计算结果不一致")
    if subtract(batch["blocked"], batch["allowed"]) != tuple(batch["blocked"]):
        raise ValueError("封禁范围与白名单重叠")
    undo = read(vendor, cleanup)
    if any((undo["books"], undo["rules"], undo["ranges"], undo["moved"])):
        raise ValueError("清理命令中不应新建对象")
    expected = dict(rules=sorted(rules), books=sorted(planned), ranges=sorted(windows))
    if {key: sorted(value) for key, value in undo["undone"].items()} != expected:
        raise ValueError("清理命令与本批次对象不一致")

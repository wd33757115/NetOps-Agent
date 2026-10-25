"""Exact IPv4/port interval sets. Half-open bounds; never enumerate hosts."""

import re
from ipaddress import IPv4Address, IPv4Network, summarize_address_range


def union(intervals):
    result = []
    for low, high in sorted(intervals):
        if low >= high:
            raise ValueError("无效区间")
        if result and low <= result[-1][1]:
            result[-1] = (result[-1][0], max(high, result[-1][1]))
        else:
            result.append((low, high))
    return tuple(result)


def subtract(intervals, removed):
    """``intervals`` minus ``removed``; both sorted and disjoint, as ``union`` returns them."""
    result, index = [], 0
    for low, high in intervals:
        while index < len(removed) and removed[index][1] <= low:
            index += 1
        cursor, scan = low, index
        while scan < len(removed) and removed[scan][0] < high:
            if removed[scan][0] > cursor:
                result.append((cursor, removed[scan][0]))
            cursor = max(cursor, removed[scan][1])
            scan += 1
        if cursor < high:
            result.append((cursor, high))
    return tuple(result)


def tokens(value):
    return [v for v in re.split(r"[,，;；、\s]+", str(value).strip()) if v]


def addresses(value):
    intervals = []
    for token in tokens(value):
        if "-" in token:
            first, last = token.split("-", 1)
            if "." not in last:
                last = first.rsplit(".", 1)[0] + "." + last
            intervals.append((int(IPv4Address(first)), int(IPv4Address(last)) + 1))
        else:
            # IPv4Network also accepts dotted masks; all downstream consumers use
            # these normalized bounds, never re-parse the original with regex.
            net = IPv4Network(token, strict=False)
            intervals.append((int(net.network_address), int(net.broadcast_address) + 1))
    if not intervals:
        raise ValueError("地址集合不能为空")
    return union(intervals)


def ports(value):
    result = []
    for token in tokens(value):
        if not re.fullmatch(r"\d+(?:-\d+)?", token):
            raise ValueError("端口必须是数字或数字范围")
        parts = [int(n) for n in token.split("-")]
        if not 1 <= parts[0] <= parts[-1] <= 65535:
            raise ValueError("端口范围必须在 1–65535 内")
        result.append((parts[0], parts[-1] + 1))
    return union(result)


def address_text(intervals):
    return [
        str(IPv4Address(a)) if b == a + 1 else f"{IPv4Address(a)}-{IPv4Address(b - 1)}"
        for a, b in intervals
    ]


def cidrs(value):
    return [
        net
        for a, b in addresses(value)
        for net in summarize_address_range(IPv4Address(a), IPv4Address(b - 1))
    ]


def port_text(intervals):
    return [str(a) if b == a + 1 else f"{a}-{b - 1}" for a, b in intervals]

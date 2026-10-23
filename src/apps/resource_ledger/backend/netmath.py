"""IPv4 arithmetic for the ledger. Ranges are inclusive integer pairs (first, last)."""

from collections import Counter
from ipaddress import AddressValueError, IPv4Address, IPv4Network, summarize_address_range


def ip_value(text):
    try:
        return int(IPv4Address(str(text).strip()))
    except (AddressValueError, ValueError):
        raise ValueError(f"“{text}”不是有效的 IPv4 地址") from None


def ip_text(value):
    return str(IPv4Address(value))


def parse_network(text):
    """A CIDR exactly as written: host bits must be zero, so typos are not silently moved."""
    raw = str(text).strip()
    if "/" not in raw:
        raise ValueError(f"“{raw}”缺少掩码长度，请写成 10.1.1.0/24")
    try:
        net = IPv4Network(raw, strict=False)
    except (AddressValueError, ValueError):
        raise ValueError(f"“{raw}”不是有效的网段") from None
    if int(IPv4Address(raw.split("/")[0])) != int(net.network_address):
        raise ValueError(f"“{raw}”不是网段的起始地址，应写成 {net}")
    return int(net.network_address), int(net.broadcast_address), net.prefixlen, str(net)


def usable(first, last, length):
    """Addresses that can be handed out: no network or broadcast address below /31."""
    return (first, last) if length >= 31 else (first + 1, last - 1)


def size(first, last):
    return last - first + 1


def contains(outer, inner):
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def merge(ranges):
    result = []
    for first, last in sorted(ranges):
        if result and first <= result[-1][1] + 1:
            result[-1] = (result[-1][0], max(result[-1][1], last))
        else:
            result.append((first, last))
    return result


def covered(ranges, within):
    """How many addresses of ``within`` the ranges cover, counting overlaps once."""
    lo, hi = within
    total = 0
    for first, last in merge(ranges):
        a, b = max(first, lo), min(last, hi)
        if a <= b:
            total += b - a + 1
    return total


def gaps(outer, children):
    """Unplanned stretches of ``outer`` between its direct children."""
    result, cursor = [], outer[0]
    for first, last in merge(children):
        if first > cursor:
            result.append((cursor, first - 1))
        cursor = max(cursor, last + 1)
    if cursor <= outer[1]:
        result.append((cursor, outer[1]))
    return result


def aligned_blocks(gap, length):
    """Number of whole /length blocks that fit in a gap on their natural boundaries."""
    step = 1 << (32 - length)
    start = -(-gap[0] // step) * step
    return 0 if start + step - 1 > gap[1] else (gap[1] - start + 1) // step


def common_length(children, fallback=24):
    lengths = Counter(length for length in children)
    return lengths.most_common(1)[0][0] if lengths else fallback


def largest_blocks(gap):
    """The CIDR blocks that exactly tile a gap, largest first."""
    blocks = summarize_address_range(IPv4Address(gap[0]), IPv4Address(gap[1]))
    return sorted((str(b) for b in blocks), key=lambda b: (int(b.split("/")[1]), b))

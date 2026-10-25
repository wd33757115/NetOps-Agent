"""IP block list → disjoint address sets grouped by expiry, ready for address books.

Every import is a batch of its own: its books, rules and time ranges are new objects named
after the batch, so applying or cleaning up one batch never touches another. Intervals are
half-open ints as in policy_sets; hosts are never enumerated.
"""

import json
import re
from bisect import bisect_right
from datetime import date, datetime, timedelta
from hashlib import sha256
from ipaddress import IPv4Address, IPv4Network

from policy_sets import addresses, subtract, tokens, union
from policy_window import FORMAT, moment

BOOK_LIMIT = 2000
BOOKS_PER_RULE = 32
MAX_EXPIRIES = 50
MAX_ROWS = 200_000
# A row wider than a /16 is blocked only when the request allows broad ranges.
BROAD = 1 << 16
PERMANENT = {"永久", "长期", "不过期", "permanent", "never"}
SPECIAL = [
    (IPv4Network(net), label)
    for net, label in [
        ("10.0.0.0/8", "内网"),
        ("172.16.0.0/12", "内网"),
        ("192.168.0.0/16", "内网"),
        ("100.64.0.0/10", "运营商级 NAT"),
        ("127.0.0.0/8", "环回"),
        ("169.254.0.0/16", "链路本地"),
        ("224.0.0.0/4", "组播"),
        ("240.0.0.0/4", "保留"),
        ("0.0.0.0/8", "保留"),
    ]
]
BLOCK_COLUMNS = {
    "ip": {"ip地址", "ip", "地址", "封禁地址", "ip_address", "address", "src_ip", "源地址"},
    "expiry": {
        "到期时间",
        "到期",
        "过期时间",
        "截止时间",
        "expiry",
        "expire",
        "expires",
        "end_time",
    },
    "note": {"备注", "说明", "原因", "note", "remark", "comment", "description"},
}
WHITELIST_COLUMNS = {"ip": BLOCK_COLUMNS["ip"], "note": BLOCK_COLUMNS["note"]}


def cell(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime(FORMAT)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def table(rows, columns):
    """(row number, {column: text}) for the non-blank rows of one sheet.

    The header row is optional: a first row naming no known column is data, read in the
    column order given.
    """
    mapping, result = None, []
    for number, values in rows:
        values = [cell(value) for value in values]
        if not any(values):
            continue
        if mapping is None:
            header = {
                name: index
                for index, value in enumerate(values)
                for name, aliases in columns.items()
                if re.sub(r"\s+", "", value).lower() in aliases
            }
            if header:
                if "ip" not in header:
                    raise ValueError("找不到“IP地址”列")
                mapping = header
                continue
            mapping = {name: index for index, name in enumerate(columns)}
        result.append(
            (number, {name: values[i] if i < len(values) else "" for name, i in mapping.items()})
        )
        if len(result) > MAX_ROWS:
            raise ValueError(f"名单超过 {MAX_ROWS} 行，请拆分后分批导入")
    return result


def count(intervals):
    return sum(high - low for low, high in intervals)


def label(low, high):
    first = IPv4Address(low)
    size = high - low
    if size & (size - 1) == 0 and low % size == 0:
        return str(first) if size == 1 else f"{first}/{33 - size.bit_length()}"
    return f"{first}-{IPv4Address(high - 1)}"


def until(expiry):
    """An expiry for people: the end of a day reads 24:00 of that day."""
    if not expiry:
        return "永久"
    end = datetime.strptime(expiry, FORMAT)
    if (end.hour, end.minute) == (0, 0):
        return f"{end - timedelta(days=1):%Y-%m-%d} 24:00"
    return f"{end:%Y-%m-%d %H:%M}"


def parse_addresses(text, allow_broad):
    """Intervals of one cell and notes on it; ValueError names the first bad entry."""
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"(\d+\.\d+\.\d+\.\d+)\s+(255\.\d+\.\d+\.\d+)", r"\1/\2", text)
    intervals, notes = [], []
    for token in tokens(text):
        if ":" in token:
            raise ValueError(f"暂不支持 IPv6 地址“{token}”")
        try:
            ((low, high),) = addresses(token)
        except ValueError:
            raise ValueError(
                f"无法识别的地址“{token}”，请写 IP、网段（CIDR 或掩码）或地址范围"
            ) from None
        if high - low == 1 << 32:
            raise ValueError(f"不能封禁全部地址“{token}”")
        if high - low > BROAD and not allow_broad:
            raise ValueError(
                f"“{token}”包含 {high - low} 个地址，超过一个 /16；确需封禁请勾选“允许大范围封禁”"
            )
        if "/" in token and str(IPv4Address(low)) != token.split("/")[0]:
            notes.append(f"“{token}”按网段 {label(low, high)} 封禁")
        intervals.append((low, high))
    if not intervals:
        raise ValueError("IP地址为空")
    labels = sorted(
        {
            f"{name}（{net}）"
            for low, high in intervals
            for net, name in SPECIAL
            if low < int(net.broadcast_address) + 1 and high > int(net.network_address)
        }
    )
    if labels:
        notes.append(f"包含{'、'.join(labels)}地址")
    return union(intervals), notes


def parse_expiry(text, now):
    value = text.strip()
    if not value or value.lower() in PERMANENT:
        return None
    end = moment(value, end=True)
    if end <= now:
        raise ValueError(f"到期时间 {until(end.strftime(FORMAT))} 已经过去")
    if end.year > 2099:
        raise ValueError("到期时间不能晚于 2099 年")
    return end.strftime(FORMAT)


def read_rows(rows, sheet, now, *, allow_broad, whitelist=False):
    """Valid entries and per-row issues of one sheet. Bad rows are reported, never blocked."""
    entries, issues = [], []
    for number, values in rows:
        text = values.get("ip", "")
        issue = dict(
            sheet=sheet,
            row=number,
            value=text,
            expiry_text=values.get("expiry", ""),
            note=values.get("note", ""),
        )
        try:
            intervals, notes = parse_addresses(text, allow_broad or whitelist)
            expiry = None if whitelist else parse_expiry(values.get("expiry", ""), now)
        except ValueError as exc:
            issues.append({**issue, "level": "error", "message": str(exc)})
            continue
        if not whitelist:
            issues += [{**issue, "level": "warning", "message": note} for note in notes]
        entries.append(
            dict(
                sheet=sheet,
                row=number,
                text=text,
                expiry_text=values.get("expiry", ""),
                note=values.get("note", ""),
                intervals=intervals,
                expiry=expiry,
            )
        )
    return entries, issues


def batch_prefix(ticket_id, groups):
    """BLK_<ticket>_<digest>; the digest keeps a reused ticket number from colliding."""
    content = json.dumps([ticket_id, [[g["expiry"], g["intervals"]] for g in groups]])
    digest = sha256(content.encode()).hexdigest()[:4].upper()
    # Names stay within the 32 characters both vendors allow: 4 + 16 + 5 + 5.
    compact = re.sub(r"[^A-Za-z0-9]", "", ticket_id)[:16]
    return f"BLK_{compact}_{digest}" if compact else f"BLK_{digest}"


def build(entries, whitelist, *, ticket_id, limit, now):
    """The batch: rules (one or more per expiry), their books and time ranges, and the
    row-by-row account of where each listed address went."""
    allowed = union(i for item in whitelist for i in item["intervals"]) if whitelist else ()
    listed = {}
    for entry in entries:
        listed.setdefault(entry["expiry"], []).extend(entry["intervals"])
    if len(listed) > MAX_EXPIRIES:
        raise ValueError(
            f"名单有 {len(listed)} 种到期时间，每种都会生成一条规则和一个时间段；"
            f"请把到期时间归并（最多 {MAX_EXPIRIES} 种）后重新导入"
        )
    # An address listed more than once stays blocked until its latest expiry, so the
    # permanent group comes first and each later group drops what is already covered.
    order = ([None] if None in listed else []) + sorted((k for k in listed if k), reverse=True)
    groups, covered = [], ()
    for expiry in order:
        kept = subtract(subtract(union(listed[expiry]), allowed), covered)
        covered = union(covered + kept)
        if kept:
            groups.append(dict(expiry=expiry, intervals=kept))
    if not groups:
        raise ValueError("名单中的地址都在白名单内或无效，没有需要封禁的地址")

    prefix = batch_prefix(ticket_id, groups)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).strftime(FORMAT)
    books, rules, ranges = [], [], []
    for group in groups:
        time_range = f"{prefix}_T{len(ranges) + 1:02d}" if group["expiry"] else None
        if time_range:
            ranges.append(dict(name=time_range, window=[start, group["expiry"]]))
        members = list(group["intervals"])
        own = [
            dict(
                name=f"{prefix}_{len(books) + n + 1:03d}",
                members=members[offset : offset + limit],
                expiry=group["expiry"],
            )
            for n, offset in enumerate(range(0, len(members), limit))
        ]
        books += own
        group.update(time_range=time_range, books=[book["name"] for book in own], rules=[])
        for offset in range(0, len(own), BOOKS_PER_RULE):
            rule = dict(
                name=f"{prefix}_R{len(rules) + 1:02d}",
                books=[book["name"] for book in own[offset : offset + BOOKS_PER_RULE]],
                time_range=time_range,
                expiry=group["expiry"],
            )
            rules.append(rule)
            group["rules"].append(rule["name"])
            for book in own[offset : offset + BOOKS_PER_RULE]:
                book["rule"] = rule["name"]

    # Members of all books are disjoint and sorted, so one bisect finds a row's books.
    index = sorted(
        (low, high, position, book["name"])
        for book in books
        for position, (low, high) in enumerate(book["members"])
    )
    lows = [item[0] for item in index]
    sources = {}

    def members_of(intervals):
        for low, high in intervals:
            i = max(bisect_right(lows, low) - 1, 0)
            while i < len(index) and index[i][0] < high:
                if index[i][1] > low:
                    yield index[i]
                i += 1

    expiry_of = {book["name"]: book["expiry"] for book in books}
    for entry in entries:
        blocked = subtract(entry["intervals"], allowed)
        removed = subtract(entry["intervals"], blocked)
        found = list(members_of(blocked))
        for low, high, position, name in found:
            sources.setdefault((name, position), []).append(entry["row"])
        entry.update(
            blocked=count(blocked),
            removed=removed,
            books=sorted({item[3] for item in found}),
            later={expiry_of[item[3]] for item in found} - {entry["expiry"]},
        )

    # Whitelist rows: what each removed from the list and which block rows it touched.
    listed_all = union(i for entry in entries for i in entry["intervals"])
    for item in whitelist:
        hit = subtract(item["intervals"], subtract(item["intervals"], listed_all))
        item.update(removed=hit, rows=[])
    if whitelist:
        segments = list(allowed)
        owners = [[] for _ in segments]
        starts = [low for low, _ in segments]
        for item in whitelist:
            for low, high in item["intervals"]:
                owners[bisect_right(starts, low) - 1].append((low, high, item))
        for entry in entries:
            touched = {}
            for low, high in entry["removed"]:
                for a, b, item in owners[bisect_right(starts, low) - 1]:
                    if a < high and b > low:
                        touched[id(item)] = item
            for item in touched.values():
                item["rows"].append(entry["row"])

    return dict(
        prefix=prefix,
        start=start,
        groups=groups,
        books=books,
        rules=rules,
        time_ranges=ranges,
        allowed=allowed,
        sources=sources,
        blocked=covered,
        removed=subtract(listed_all, covered),
    )

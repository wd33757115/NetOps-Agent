"""Device tables read generically, compared by entry, summarised for a model.

Columns come from how the rows line up under a header; nothing is configured per
command, so it works for whatever a diagnosis turns out to need. As in Batfish's
differential questions or an IP Fabric snapshot comparison, entries are matched by
key and reported as added, removed or changed, naming the columns that changed.
Large sets are summarised by the values most of them share (one interface, one
next hop) with a few examples; the model asks for the rows it needs.
"""

import re
from collections import Counter

_WORD = re.compile(r"\S+")
_PROBE = 8  # a table header sits within the first lines of its block
_SAMPLE = 400  # rows used to find a table; all rows are used to cut it
_NUMERIC = re.compile(r"[-+]?\d+(?:\.\d+)?%?|\d+:\d\d(?::\d\d)?|(?:\d+[wdhms])+")
EXAMPLES = 5


def _layout(header, rows):
    """(column starts, names, aligned): starts where row values begin after a gap.

    A header word over an empty column also starts one when two spaces set it
    apart (a "Description" nobody filled). Words without their own start join the
    column before them ("IP address", "Local Interface").
    """
    width = max(len(line) for line in [header, *rows])
    filled = bytearray(width + 1)
    for row in rows:
        for match in _WORD.finditer(row):
            filled[match.start() : match.end()] = b"\x01" * (match.end() - match.start())
    starts = {p for p in range(width) if filled[p] and (p == 0 or not filled[p - 1])}
    words = [(m.start(), m.end(), m.group()) for m in _WORD.finditer(header)]
    for index, (start, end, _) in enumerate(words):
        apart = index == 0 or start - words[index - 1][1] >= 2
        if apart and not any(filled[start:end]) and (start == 0 or not filled[start - 1]):
            starts.add(start)
    starts = sorted(starts)
    names = []
    for index, start in enumerate(starts):
        stop = starts[index + 1] if index + 1 < len(starts) else width + 1
        lower = start if index else -1
        names.append(" ".join(w for s, _, w in words if lower <= s < stop))
    # A start no header word owns is a value with spaces inside: merge it back.
    merged = [(starts[0], names[0])] if starts else []
    for start, name in zip(starts[1:], names[1:]):
        if name:
            merged.append((start, name))
    aligned = sum(any(abs(s - start) <= 1 for s, _, _ in words) for start, _ in merged)
    return [s for s, _ in merged], [n for _, n in merged], aligned


def _header_like(line):
    words = line.split()
    # Legends ("Type: S-Static ...", "Area: 0.0.0.0", "Destinations : 19") are not
    # headers, nor is prose ("OSPF Process 1 with Router ID ..."): a header lines
    # its columns up, so somewhere two spaces part them.
    return (
        len(words) >= 2
        and not words[0].endswith(":")
        and " : " not in line
        and "  " in line.strip()
    )


def regions(lines):
    """[(header index, end index)] of tables with rows, found block by block."""
    found, index = [], 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        end = index
        while end < len(lines) and lines[end].strip():
            end += 1
        best = None
        for header in range(index, min(index + _PROBE, end - 1)):
            if not _header_like(lines[header]):
                continue
            starts, _, aligned = _layout(lines[header], lines[header + 1 : end][:_SAMPLE])
            if len(starts) >= 2 and aligned >= 2 and aligned >= 0.6 * len(starts):
                if best is None or aligned > best[0]:
                    best = (aligned, header)
        if best:
            found.append((best[1], end))
        index = end
    return found


def _cells(line, starts):
    bounds = list(zip(starts, starts[1:] + [None]))
    return tuple((line[a:b] if b else line[a:]).strip() for a, b in bounds)


def _key(columns, *sides):
    """Column indexes that name each entry: the first column, else the first column
    with the one other column that makes entries unique (prefix + next hop for
    equal-cost routes), else the whole row."""

    def unique(indexes):
        return all(
            len({tuple(row[i] for i in indexes) for row in rows}) == len(rows) for rows in sides
        )

    for indexes in [(0,), *((0, other) for other in range(1, columns))]:
        if unique(indexes):
            return indexes
    return tuple(range(columns))


def _continue(rows):
    """A blank first cell continues the entry above it (equal-cost routes)."""
    out, last = [], ""
    for row in rows:
        if not row[0] and last:
            row = (last, *row[1:])
        last = row[0]
        out.append(row)
    return out


def _mostly(rows, names, key, population):
    """The values most of these rows share: what the change is about.

    A value these rows share more than the whole table does explains them (one
    next hop among many); one the whole table shares (every route is O_INTRA)
    explains nothing, so it comes last. Words before numbers within each.
    """
    if len(rows) < 2:
        return []
    ranked = []
    for index, name in enumerate(names):
        if index in key:
            continue
        value, count = Counter(row[index] for row in rows).most_common(1)[0]
        if not value or value == "--" or count < 0.6 * len(rows):
            continue
        overall = sum(row[index] == value for row in population) / max(len(population), 1)
        distinctive = overall < 0.9 * count / len(rows)
        number = bool(_NUMERIC.fullmatch(value))
        ranked.append(((not distinctive, number, index), f"{name}={value}（{count}/{len(rows)}）"))
    return [text for _, text in sorted(ranked)][:3]


def _line(row):
    text = " | ".join(row)
    return text if len(text) <= 160 else text[:160] + "…"


class Table:
    """One header and its rows on one side, cut with a layout shared by both sides."""

    def __init__(self, lines, header, end):
        self.header = lines[header]
        self.rows = lines[header + 1 : end]
        self.span = range(header, end)

    @classmethod
    def empty(cls, header):
        return cls([header], 0, 1)

    @property
    def signature(self):
        return " ".join(self.header.split())


def signatures(lines):
    return {" ".join(lines[h].split()) for h, _ in regions(lines)}


def pair(old_lines, new_lines):
    """Tables found on both sides with the same header, in order, plus leftover lines.

    A header with no rows under it (every entry gone) still pairs with its table.
    """
    old = [Table(old_lines, h, e) for h, e in regions(old_lines)]
    new = [Table(new_lines, h, e) for h, e in regions(new_lines)]

    def bare(lines, tables, signature):
        used = {i for t in tables for i in t.span}
        for index, line in enumerate(lines):
            following = lines[index + 1] if index + 1 < len(lines) else ""
            if index not in used and " ".join(line.split()) == signature and not following.strip():
                return Table(lines, index, index + 1)
        return None

    pairs = []
    for table in old:
        match = next((t for t in new if t.signature == table.signature and t not in
                      [p[1] for p in pairs]), None) or bare(new_lines, new, table.signature)
        if match is not None:
            pairs.append((table, match))
    for table in new:
        if table not in [p[1] for p in pairs]:
            match = bare(old_lines, old, table.signature)
            if match is not None:
                pairs.append((match, table))
    used_old = {i for a, _ in pairs for i in a.span}
    used_new = {i for _, b in pairs for i in b.span}
    rest_old = [line for i, line in enumerate(old_lines) if i not in used_old]
    rest_new = [line for i, line in enumerate(new_lines) if i not in used_new]
    return pairs, rest_old, rest_new


def pair_all(old_lines, new_lines):
    """Like pair, plus tables found on one side only, set against an empty table.

    A table can vanish header and all (no OSPF neighbours, no LLDP neighbours):
    every entry it had is gone, which a model should read as such, not as text.
    """
    pairs, _, _ = pair(old_lines, new_lines)
    used_old = {i for a, _ in pairs for i in a.span}
    used_new = {i for _, b in pairs for i in b.span}
    for header, end in regions(old_lines):
        if header not in used_old:
            table = Table(old_lines, header, end)
            pairs.append((table, Table.empty(table.header)))
            used_old.update(table.span)
    for header, end in regions(new_lines):
        if header not in used_new:
            table = Table(new_lines, header, end)
            pairs.append((Table.empty(table.header), table))
            used_new.update(table.span)
    rest_old = [line for i, line in enumerate(old_lines) if i not in used_old]
    rest_new = [line for i, line in enumerate(new_lines) if i not in used_new]
    return pairs, rest_old, rest_new


def layout(before, after):
    """Columns for a table pair, cut from both sides' rows together."""
    return _layout(before.header, before.rows + after.rows)[:2]


def keyed(before, after):
    """(names, key indexes, old rows by key, new rows by key) for a table pair."""
    starts, names = layout(before, after)
    old = _continue([_cells(row, starts) for row in before.rows])
    new = _continue([_cells(row, starts) for row in after.rows])
    key = _key(len(names), old, new)

    def by_key(rows):
        return {tuple(row[i] for i in key): row for row in rows}

    return names, key, by_key(old), by_key(new)


def compare(before, after):
    """Entry-level comparison of one table pair, summarised; None if nothing changed."""
    names, key, old, new = keyed(before, after)
    added = [new[k] for k in new if k not in old]
    removed = [old[k] for k in old if k not in new]
    changed = [(old[k], new[k]) for k in old if k in new and old[k] != new[k]]
    if not (added or removed or changed):
        return None
    result = {
        "columns": names,
        "key": [names[i] for i in key],
        "rows": [len(old), len(new)],
    }
    for label, rows, population in (("added", added, new), ("removed", removed, old)):
        if rows:
            part = {"count": len(rows), "examples": [_line(r) for r in rows[:EXAMPLES]]}
            if mostly := _mostly(rows, names, key, list(population.values())):
                part["mostly"] = mostly
            result[label] = part
    if changed:
        columns = {}
        for index, name in enumerate(names):
            pairs = [(a[index], b[index]) for a, b in changed if a[index] != b[index]]
            if not pairs:
                continue
            column = {
                "rows": len(pairs),
                "examples": [
                    f"{a or '空'}→{b or '空'}" for (a, b), _ in Counter(pairs).most_common(3)
                ],
            }
            # Timers and counters still show, as one line: the model decides.
            if all(_NUMERIC.fullmatch(v) for pair_ in pairs for v in pair_ if v):
                column["numeric"] = True
            columns[name] = column
        result["changed"] = {
            "count": len(changed),
            "columns": columns,
            "examples": [
                " | ".join(a[i] for i in key) + "：" + "，".join(
                    f"{n} {x or '空'}→{y or '空'}" for n, x, y in zip(names, a, b) if x != y
                )
                for a, b in changed[:EXAMPLES]
            ],
        }
    return result


def summary(table):
    """One line a person (or a model) reads first."""
    parts = []
    for label, word in (("removed", "消失"), ("added", "新增")):
        if label in table:
            text = f"{word} {table[label]['count']}"
            if table[label].get("mostly"):
                text += "（多数 " + "、".join(table[label]["mostly"]) + "）"
            parts.append(text)
    if "changed" in table:
        columns = "、".join(
            f"{name} {c['rows']} 行" + ("（数值）" if c.get("numeric") else "")
            for name, c in table["changed"]["columns"].items()
        )
        parts.append(f"内容变化 {table['changed']['count']}（{columns}）")
    before, after = table["rows"]
    head = f"表 {table['columns'][0]} 等 {len(table['columns'])} 列，{before}→{after} 条："
    return head + "；".join(parts)


def _sections(lines):
    """Top-level lines with the indented lines under them (configuration blocks)."""
    sections, current = [], None
    for line in lines:
        if not line.strip() or line.strip() == "#":
            current = None
            continue
        if not line[0].isspace() or current is None:
            current = [line]
            sections.append(current)
        else:
            current.append(line)
    return sections


def find(old_lines, new_lines, needle, limit=30, *, tables=True):
    """Rows and text sections containing needle, baseline beside current.

    Table entries line up by key, so each shows whether it is only in the
    baseline, only in the current read, changed (naming the columns) or the same.
    Either side may be missing (a large table read only by a filtered command).
    Configuration (tables=False) is searched as sections only.
    """
    needle = needle.lower()
    # A table on one side only: the other side was not read, read filtered, or empty.
    pairs, rest_old, rest_new = (
        pair_all(old_lines, new_lines) if tables else ([], old_lines, new_lines)
    )
    matched, shown = [], 0
    for before, after in pairs:
        names, key, old, new = keyed(before, after)
        found = []
        for k in list(old) + [k for k in new if k not in old]:
            a, b = old.get(k), new.get(k)
            if not any(row and needle in " ".join(row).lower() for row in (a, b)):
                continue
            item = {"baseline": _line(a) if a else None, "current": _line(b) if b else None}
            if a and b:
                columns = [n for n, x, y in zip(names, a, b) if x != y]
                item["status"] = "changed" if columns else "same"
                if columns:
                    item["changed"] = columns
            else:
                item["status"] = "only_baseline" if a else "only_current"
            found.append(item)
        if found:
            matched.append({
                "columns": names,
                "key": [names[i] for i in key],
                "rows": [len(old), len(new)],
                "matches": len(found),
                "entries": found[: max(0, limit - shown)],
            })
            shown += len(matched[-1]["entries"])
    old_sections = {s[0]: s for s in _sections(rest_old)}
    new_sections = {s[0]: s for s in _sections(rest_new)}
    sections = []
    for head in list(old_sections) + [h for h in new_sections if h not in old_sections]:
        a, b = old_sections.get(head), new_sections.get(head)
        if not any(needle in line.lower() for line in (a or []) + (b or [])):
            continue
        if a == b:
            sections.append({"section": head, "same": True, "lines": a[:60]})
        else:
            sections.append(
                {"section": head, "baseline": (a or [])[:60], "current": (b or [])[:60]}
            )
        if len(sections) >= 10:
            break
    return {"tables": matched, "sections": sections}

"""The conversion layer: a device's NETCONF data → records named after standard leaves.

A vendor's part is data (``mappings/<vendor>.yaml``): for each standard table, where
its rows are in the device's reply and which device leaf gives each standard leaf, with
the device's codes for enumerations and identities. A table is named after the standard
list it fills (``interface`` → ietf-interfaces, ``ipv4-address``/``ipv4-neighbor`` →
ietf-ip, ``route`` → ietf-routing, ``ospf-*`` → ietf-ospf, ``lldp-neighbor`` →
ieee802-dot1ab-lldp) and a field after the leaf, so topology assembles and validates the
trees without knowing any vendor.

Nothing is guessed: a code the mapping does not list, a value of the wrong form or a
lookup that finds no record leaves the field out and is reported with the device's text.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

import yaml
from defusedxml import ElementTree

MAPPINGS = Path(__file__).resolve().parent / "mappings"

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
FIELD = re.compile(r"[a-z][a-z0-9-]*")
RULE_KEYS = frozenset(
    {"leaf", "codes", "convert", "lookup", "format", "when", "none", "default"}
)


class MappingError(ValueError):
    """A mapping that cannot be used, or a reply that is not XML."""


def _integer(text):
    return int(text) if re.fullmatch(r"-?\d+", text) else None


def _netmask(text):
    try:
        return ipaddress.ip_network(f"0.0.0.0/{text}").prefixlen
    except ValueError:
        return None


def _mac(text):
    digits = re.sub(r"[-.:]", "", text)
    if not re.fullmatch(r"[0-9A-Fa-f]{12}", digits):
        return None
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2)).lower()


def _ieee_mac(text):
    """IEEE 802 canonical form (ieee802-types mac-address): 74-25-8A-E3-CF-E7."""
    mac = _mac(text)
    return mac.replace(":", "-").upper() if mac else None


def _address(text):
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


def _base64_hex(text):
    """Octets sent base64-encoded, as the upper-case hex IEEE 802.1AB writes them."""
    import base64
    import binascii

    try:
        return base64.b64decode(text, validate=True).hex().upper() or None
    except (binascii.Error, ValueError):
        return None


def _prefix(text):
    try:
        return str(ipaddress.ip_network(text, strict=False))
    except ValueError:
        return None


CONVERTERS = {
    "integer": _integer,
    "netmask": _netmask,
    "mac": _mac,
    "ieee-mac": _ieee_mac,
    "address": _address,
    "prefix": _prefix,
    "base64-hex": _base64_hex,
}


@dataclass(frozen=True)
class Rule:
    leaves: tuple[str, ...] = ()
    codes: Mapping[str, Any] | None = None
    convert: str | None = None
    lookup: tuple[str, str, str] | None = None  # (table, match field, take field)
    format: str | None = None
    when: tuple[str, frozenset[str]] | None = None  # (leaf, its values for which it applies)
    none: frozenset[str] = frozenset()  # device texts that mean "no value"
    default: Any = None  # the value when the device gives none


@dataclass(frozen=True)
class Table:
    name: str
    paths: tuple[tuple[str, ...], ...]  # where its rows are; several tables can feed one
    fields: Mapping[str, tuple[Rule, ...]]  # alternatives: the first with a value counts
    # Leaves known to carry no condition. With it, a row's other leaves that no field reads
    # are listed in its ``unread`` field (a condition the mapping does not know).
    ignore: frozenset[str] | None = None
    where: tuple[str, frozenset[str]] | None = None  # rows of this table (others are not)


@dataclass(frozen=True)
class StandardMapping:
    vendor: str
    namespace: str
    tables: tuple[Table, ...] = field(default_factory=tuple)
    platforms: tuple[str, ...] = ()
    id: str = ""
    sha256: str = ""


def _leaf_path(text, where):
    if not isinstance(text, str) or not all(NAME.fullmatch(p) for p in text.split("/")):
        raise MappingError(f"{where}: not a leaf path: {text!r}")
    return text


def _rules(raw, where, earlier):
    alternatives = raw if isinstance(raw, list) else [raw]
    if not alternatives:
        raise MappingError(f"{where}: no rule")
    return tuple(_rule(item, where, earlier) for item in alternatives)


def _rule(raw, where, earlier):
    if isinstance(raw, str):
        return Rule(leaves=(_leaf_path(raw, where),))
    if not isinstance(raw, dict) or not set(raw) <= RULE_KEYS or not raw:
        raise MappingError(f"{where}: unknown rule {raw!r}")
    leaves = raw.get("leaf", ())
    leaves = (leaves,) if isinstance(leaves, str) else tuple(leaves)
    leaves = tuple(_leaf_path(leaf, where) for leaf in leaves)
    fmt = raw.get("format")
    if fmt is not None:
        names = re.findall(r"\{([^}]*)\}", fmt)
        leaves = tuple(_leaf_path(name, where) for name in names)
    if not leaves:
        raise MappingError(f"{where}: reads no leaf")
    codes = raw.get("codes")
    if codes is not None and not isinstance(codes, dict):
        raise MappingError(f"{where}: codes must be a mapping")
    convert = raw.get("convert")
    if convert is not None and convert not in CONVERTERS:
        raise MappingError(f"{where}: unknown conversion {convert!r}")
    lookup = raw.get("lookup")
    if lookup is not None:
        if not isinstance(lookup, dict) or set(lookup) != {"table", "match", "take"}:
            raise MappingError(f"{where}: a lookup names table, match and take")
        if lookup["table"] not in earlier:
            raise MappingError(f"{where}: a lookup reads a table mapped before it")
        lookup = (lookup["table"], lookup["match"], lookup["take"])
    when = raw.get("when")
    if when is not None:
        if not isinstance(when, dict) or set(when) != {"leaf", "in"}:
            raise MappingError(f"{where}: a condition names leaf and in")
        when = (_leaf_path(when["leaf"], where), frozenset(str(v) for v in when["in"]))
    none = raw.get("none", [])
    if not isinstance(none, list):
        raise MappingError(f"{where}: none lists device texts")
    return Rule(
        leaves=leaves,
        codes={str(k): v for k, v in codes.items()} if codes is not None else None,
        convert=convert,
        lookup=lookup,
        format=fmt,
        when=when,
        none=frozenset(str(v) for v in none),
        default=raw.get("default"),
    )


def load(raw: Mapping[str, Any], id: str = "", sha256: str = "") -> StandardMapping:
    """A mapping from its data; an unusable one raises MappingError when loaded."""
    if not isinstance(raw, dict) or not isinstance(raw.get("tables"), dict):
        raise MappingError("a mapping has vendor, namespace and tables")
    tables, seen = [], set()
    for name, spec in raw["tables"].items():
        if not FIELD.fullmatch(name) or not isinstance(spec, dict):
            raise MappingError(f"{name}: not a table")
        if not {"path", "fields"} <= set(spec) <= {
            "path",
            "fields",
            "ignore",
            "where",
        } or not isinstance(spec["fields"], dict):
            raise MappingError(f"{name}: a table has path, fields, maybe ignore and where")
        where = spec.get("where")
        if where is not None:
            if not isinstance(where, dict) or set(where) != {"leaf", "in"}:
                raise MappingError(f"{name}: where names leaf and in")
            where = (_leaf_path(where["leaf"], name), frozenset(str(v) for v in where["in"]))
        paths = spec["path"] if isinstance(spec["path"], list) else [spec["path"]]
        paths = tuple(tuple(_leaf_path(path, name).split("/")) for path in paths)
        fields = {}
        for leaf, rule in spec["fields"].items():
            if not FIELD.fullmatch(leaf):
                raise MappingError(f"{name}.{leaf}: not a standard leaf name")
            fields[leaf] = _rules(rule, f"{name}.{leaf}", seen)
        ignore = spec.get("ignore")
        if ignore is not None:
            ignore = frozenset(_leaf_path(leaf, name) for leaf in ignore)
        tables.append(Table(name, paths, fields, ignore, where))
        seen.add(name)
    platforms = raw.get("platforms") or []
    if not isinstance(platforms, list) or not all(isinstance(p, str) for p in platforms):
        raise MappingError("platforms must be a list of names")
    return StandardMapping(
        str(raw.get("vendor", "")),
        str(raw["namespace"]),
        tuple(tables),
        tuple(platforms),
        id,
        sha256,
    )


def shipped() -> dict[str, StandardMapping]:
    """{id: mapping} of the mappings shipped with the app (``mappings/<id>.yaml``)."""
    found = {}
    for path in sorted(MAPPINGS.glob("*.yaml")):
        text = path.read_bytes()
        found[path.stem] = load(
            yaml.safe_load(text.decode("utf-8")), path.stem, hashlib.sha256(text).hexdigest()
        )
    return found


def subtree_filter(mapping: StandardMapping, table: Table) -> str:
    """The subtree filter selecting a table's whole rows (each of its paths)."""
    heads = {path[0] for path in table.paths}
    if len(heads) != 1:
        raise MappingError(f"{table.name}: its paths are under different top elements")
    root = Element(next(iter(heads)), xmlns=mapping.namespace)
    for path in table.paths:
        node = root
        for name in path[1:]:
            child = next((c for c in node if c.tag == name), None)
            node = child if child is not None else SubElement(node, name)
    return tostring(root, encoding="unicode")


def read(mapping: StandardMapping, fetch: Callable[[str], str], refused=()):
    """Read every table of a mapping with ``fetch(subtree filter)`` and convert them.

    {"records", "problems", "replies": [{table, filter, reply}], "failed": {table: why}}.
    An exception in ``refused`` (the device refuses one table's RPC) leaves that table
    out as failed; any other exception (the session is gone) goes to the caller.
    """
    replies, failed = [], {}
    for table in mapping.tables:
        subtree = subtree_filter(mapping, table)
        try:
            reply = fetch(subtree)
        except tuple(refused) as exc:
            failed[table.name] = str(exc)[:300]
            continue
        replies.append({"table": table.name, "filter": subtree, "reply": reply})
    records, problems = convert(mapping, {item["table"]: [item["reply"]] for item in replies})
    return {"records": records, "problems": problems, "replies": replies, "failed": failed}


def _find(roots, path, ns):
    head, *rest = path
    inner = "/".join(ns + name for name in rest)
    return [
        found
        for root in roots
        for top in root.iter(ns + head)
        for found in (top.findall(inner) if rest else [top])
    ]


def _collected(roots, table, ns):
    """Whether a reply holds the table (its container, even with no rows)."""
    return any(_find(roots, path[:-1], ns) for path in table.paths)


def _rows(roots, table, ns):
    return [row for path in table.paths for row in _find(roots, path, ns)]


def _text(element, leaf, ns):
    node = element.find("/".join(ns + name for name in leaf.split("/")))
    if node is None:
        return None
    return (node.text or "").strip()


def _leaves(element, ns, prefix=""):
    """{leaf path: text} of the non-empty leaves under a row element."""
    found = {}
    for child in element:
        name = prefix + child.tag.removeprefix(ns)
        if len(child):
            found.update(_leaves(child, ns, name + "/"))
        elif (child.text or "").strip():
            found[name] = child.text.strip()
    return found


def _value(rule, element, ns, indexes):
    """(value, problem) for one field of a row; (None, None) when the device has none."""
    if rule.when is not None and _text(element, rule.when[0], ns) not in rule.when[1]:
        return None, None  # the field does not apply to this row
    texts = [_text(element, leaf, ns) for leaf in rule.leaves]
    if all(not text for text in texts) or "/".join(t or "" for t in texts) in rule.none:
        return rule.default, None  # an absent or empty leaf, or one meaning none
    if rule.format is not None:
        if not all(texts):
            return None, ("incomplete", "/".join(text or "" for text in texts))
        text = rule.format
        for leaf, value in zip(rule.leaves, texts, strict=True):
            text = text.replace("{" + leaf + "}", value, 1)
    else:
        text = "/".join(text or "" for text in texts)
    raw = text
    if rule.convert is not None:
        text = CONVERTERS[rule.convert](text)
        if text is None:
            return None, ("invalid_value", raw)
    if rule.lookup is not None:
        index = indexes[rule.lookup]
        if text not in index:
            return None, ("lookup_miss", raw)
        if index[text] is None:
            return None, ("lookup_ambiguous", raw)
        text = index[text]
    if rule.codes is not None:
        if str(text) not in rule.codes:
            return None, ("unmapped_code", raw)
        return rule.codes[str(text)], None
    return text, None


def _parse(texts):
    roots = []
    for text in texts:
        try:
            roots.append(ElementTree.fromstring(text))
        except Exception:
            raise MappingError("a device reply is not XML") from None
    return roots


def convert(mapping: StandardMapping, replies: Iterable[str] | Mapping[str, list[str]]):
    """({table: [record]}, [problem]) from the device's replies (rpc-reply or <data>).

    ``replies`` is {table: its replies} (each table read from its own reply only), or a
    list every table is read from. A table whose rows are in none of its replies is
    absent from the result (not collected), not an empty table.
    """
    if isinstance(replies, Mapping):
        roots_of = {name: _parse(texts) for name, texts in replies.items()}
    else:
        shared = _parse(replies)
        roots_of = {table.name: shared for table in mapping.tables}
    ns = f"{{{mapping.namespace}}}"
    found, problems, indexes = {}, [], {}
    for table in mapping.tables:
        roots = roots_of.get(table.name, [])
        if not _collected(roots, table, ns):
            continue
        for rule in (rule for rules in table.fields.values() for rule in rules):
            if rule.lookup is not None and rule.lookup not in indexes:
                source, match, take = rule.lookup
                index = {}
                for record in found.get(source, []):
                    key, value = record.get(match), record.get(take)
                    if key is None or value is None:
                        continue
                    index[key] = value if index.get(key, value) == value else None
                indexes[rule.lookup] = index
        records = []
        rows = _rows(roots, table, ns)
        if table.where is not None:
            rows = [row for row in rows if _text(row, table.where[0], ns) in table.where[1]]
        for number, element in enumerate(rows):
            record = {}
            for leaf, rules in table.fields.items():
                value, problem = None, None
                for rule in rules:
                    value, problem = _value(rule, element, ns, indexes)
                    if value is not None or problem is not None:
                        break
                if problem is not None:
                    # The record says which fields the device gave but the mapping could
                    # not read: a consumer never takes such a record as if they were absent.
                    record.setdefault("problem_fields", []).append(leaf)
                    kind, text = problem
                    problems.append(
                        {
                            "table": table.name,
                            "row": number,
                            "field": leaf,
                            "problem": kind,
                            "device_value": text,
                        }
                    )
                elif value is not None:
                    record[leaf] = value
            if table.ignore is not None:
                read = {leaf for rules in table.fields.values() for r in rules for leaf in r.leaves}
                read |= {r.when[0] for rules in table.fields.values() for r in rules if r.when}
                if table.where is not None:
                    read.add(table.where[0])
                leaves = _leaves(element, ns)
                unread = sorted(set(leaves) - read - table.ignore)
                if unread:
                    record["unread"] = [f"{leaf}={leaves[leaf]}" for leaf in unread]
            records.append(record)
        found[table.name] = records
    return found, problems

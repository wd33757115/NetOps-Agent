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

A device whose data spans several YANG modules (each its own XML namespace, augments
included) names them in ``namespaces`` ({prefix: URI}); a path or leaf step written
``prefix:name`` is in that module, and a step without a prefix is in its parent's, the
way YANG and RFC 7951 name augmented nodes. The first step of a path without a prefix is
in the mapping's ``namespace``.
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

NAME = re.compile(r"(?:[A-Za-z_][A-Za-z0-9_.-]*:)?[A-Za-z_][A-Za-z0-9_.-]*")
FIELD = re.compile(r"[a-z][a-z0-9-]*")
RULE_KEYS = frozenset(
    {"leaf", "codes", "convert", "lookup", "format", "when", "none", "default", "list", "exists"}
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


def _address_hex(text):
    """An address the device writes as text, as the upper-case hex octets IEEE 802.1AB
    management addresses are (``10.255.0.1`` → ``0AFF0001``)."""
    try:
        return ipaddress.ip_address(text).packed.hex().upper()
    except ValueError:
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
    "address-hex": _address_hex,
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
    many: bool = False  # a leaf-list: every value, in the device's order
    exists: Any = None  # the value when the (possibly empty) element is present


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
    namespaces: Mapping[str, str] = field(default_factory=dict)  # {prefix: URI}


def _split(step):
    prefix, _, name = step.rpartition(":")
    return prefix, name


def _qualified(steps, uri, namespaces):
    """Clark names ({URI}name) of path steps, starting in namespace ``uri``."""
    found = []
    for step in steps:
        prefix, name = _split(step)
        if prefix:
            uri = namespaces[prefix]
        found.append(f"{{{uri}}}{name}")
    return found


def _namespace(tag):
    return tag[1:].partition("}")[0] if tag.startswith("{") else ""


def _leaf_path(text, where, *, up=True):
    """A path of steps; a leaf's may start with ``..`` steps (to an ancestor of its row)."""
    steps = text.split("/") if isinstance(text, str) else [None]
    while up and len(steps) > 1 and steps[0] == "..":
        steps = steps[1:]
    if not all(isinstance(p, str) and NAME.fullmatch(p) for p in steps):
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
        many=_flag(raw, "list", where),
        exists=raw.get("exists"),
    )


def _flag(raw, key, where):
    value = raw.get(key, False)
    if not isinstance(value, bool):
        raise MappingError(f"{where}: {key} is true or false")
    if value and (raw.get("format") is not None or raw.get("lookup") is not None):
        raise MappingError(f"{where}: a list reads one leaf without format or lookup")
    return value


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
        paths = tuple(tuple(_leaf_path(path, name, up=False).split("/")) for path in paths)
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
    namespaces = raw.get("namespaces") or {}
    if not isinstance(namespaces, dict) or not all(
        isinstance(k, str) and FIELD.fullmatch(k) and isinstance(v, str) and v
        for k, v in namespaces.items()
    ):
        raise MappingError("namespaces maps prefixes to namespace URIs")
    for table in tables:
        steps = [step for path in table.paths for step in path]
        steps += [
            step
            for rules in table.fields.values()
            for rule in rules
            for leaf in (*rule.leaves, *((rule.when[0],) if rule.when else ()))
            for step in leaf.split("/")
        ]
        steps += [s for leaf in table.ignore or () for s in leaf.split("/")]
        steps += table.where[0].split("/") if table.where else []
        if unknown := sorted({_split(s)[0] for s in steps} - {""} - set(namespaces)):
            raise MappingError(f"{table.name}: undeclared namespace prefix {unknown[0]!r}")
    return StandardMapping(
        str(raw.get("vendor", "")),
        str(raw["namespace"]),
        tuple(tables),
        tuple(platforms),
        id,
        sha256,
        dict(namespaces),
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


def _ancestor_leaves(table):
    """Paths (from the top element) of the leaves a row reads above itself (``..``)."""
    found = []
    leaves = [leaf for rules in table.fields.values() for rule in rules for leaf in rule.leaves]
    leaves += [rule.when[0] for rules in table.fields.values() for rule in rules if rule.when]
    leaves += [table.where[0]] if table.where else []
    for leaf in leaves:
        steps = leaf.split("/")
        ups = 0
        while steps[ups] == "..":
            ups += 1
        if ups:
            found += [path[:-ups] + tuple(steps[ups:]) for path in table.paths if len(path) > ups]
    return found


def subtree_filter(mapping: StandardMapping, table: Table) -> str:
    """The subtree filter selecting a table's whole rows (each of its paths), and the
    leaves its rows read from their ancestors (a nested list's owner, e.g. its name)."""
    heads = {path[0] for path in table.paths}
    if len(heads) != 1:
        raise MappingError(f"{table.name}: its paths are under different top elements")
    # Each element is written with its local name; one whose module differs from its
    # parent's declares its namespace (the default namespace of its subtree).
    head = next(iter(heads))
    prefix, name = _split(head)
    uri = mapping.namespaces[prefix] if prefix else mapping.namespace
    root = Element(name, xmlns=uri)
    for path in (*table.paths, *_ancestor_leaves(table)):
        node, node_uri = root, uri
        for step in path[1:]:
            prefix, name = _split(step)
            step_uri = mapping.namespaces[prefix] if prefix else node_uri
            child = next(
                (c for c in node if c.tag == name and c.get("_uri", node_uri) == step_uri), None
            )
            if child is None:
                child = SubElement(node, name, **({"xmlns": step_uri} if step_uri != node_uri else {}))
                child.set("_uri", step_uri)
            node, node_uri = child, step_uri
    for element in root.iter():
        element.attrib.pop("_uri", None)
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


def _find(roots, path, mapping):
    head, *rest = _qualified(path, mapping.namespace, mapping.namespaces)
    inner = "/".join(rest)
    return [
        found
        for root in roots
        for top in root.iter(head)
        for found in (top.findall(inner) if rest else [top])
    ]


def _collected(roots, table, mapping):
    """Whether a reply holds the table (its container, even with no rows)."""
    return any(_find(roots, path[:-1], mapping) for path in table.paths)


def _rows(roots, table, mapping):
    return [row for path in table.paths for row in _find(roots, path, mapping)]


def _text(element, leaf, mapping, parents):
    """A leaf under a row; its steps start in the row's own namespace. A leading ``..``
    steps up to the row's parent (a list nested in another list names its owner there)."""
    steps = leaf.split("/")
    while steps and steps[0] == "..":
        element = parents.get(element)
        if element is None:
            return None
        steps = steps[1:]
    steps = _qualified(steps, _namespace(element.tag), mapping.namespaces)
    node = element.find("/".join(steps))
    if node is None:
        return None
    return (node.text or "").strip()


def _leaves(element, mapping, prefix=""):
    """{leaf path: text} of the non-empty leaves under a row element, written as the
    mapping writes them (``prefix:name`` where a child is in another module)."""
    prefixes = {uri: name for name, uri in mapping.namespaces.items()}
    parent = _namespace(element.tag)
    found = {}
    for child in element:
        uri, local = _namespace(child.tag), child.tag.rpartition("}")[2]
        step = local if uri == parent else f"{prefixes.get(uri, '{' + uri + '}')}:{local}"
        name = prefix + step
        if len(child):
            found.update(_leaves(child, mapping, name + "/"))
        elif (child.text or "").strip():
            found[name] = child.text.strip()
    return found


def _node(element, leaf, mapping, parents, *, every=False):
    """The element(s) a leaf path names under a row (``..`` steps up first)."""
    steps = leaf.split("/")
    while steps and steps[0] == "..":
        element = parents.get(element)
        if element is None:
            return [] if every else None
        steps = steps[1:]
    path = "/".join(_qualified(steps, _namespace(element.tag), mapping.namespaces))
    return element.findall(path) if every else element.find(path)


def _item(rule, text):
    """(value, problem) of one device text through a rule's conversion and codes."""
    value = text
    if rule.convert is not None:
        value = CONVERTERS[rule.convert](text)
        if value is None:
            return None, ("invalid_value", text)
    if rule.codes is not None:
        if str(value) not in rule.codes:
            return None, ("unmapped_code", text)
        return rule.codes[str(value)], None
    return value, None


def _value(rule, element, mapping, indexes, parents):
    """(value, problem) for one field of a row; (None, None) when the device has none."""
    if rule.when is not None and _text(element, rule.when[0], mapping, parents) not in rule.when[1]:
        return None, None  # the field does not apply to this row
    if rule.exists is not None:
        # A presence leaf or an empty choice case: its being there is the value.
        return (rule.exists, None) if _node(element, rule.leaves[0], mapping, parents) is not None else (None, None)
    if rule.many:
        values = []
        for node in _node(element, rule.leaves[0], mapping, parents, every=True):
            text = (node.text or "").strip()
            if not text or text in rule.none:
                continue
            value, problem = _item(rule, text)
            if problem is not None:
                return None, problem
            values.append(value)
        return (values or rule.default), None
    texts = [_text(element, leaf, mapping, parents) for leaf in rule.leaves]
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
    parents = {
        child: parent
        for roots in roots_of.values()
        for root in roots
        for parent in root.iter()
        for child in parent
    }
    found, problems, indexes = {}, [], {}
    for table in mapping.tables:
        roots = roots_of.get(table.name, [])
        if not _collected(roots, table, mapping):
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
        rows = _rows(roots, table, mapping)
        if table.where is not None:
            rows = [
                row for row in rows if _text(row, table.where[0], mapping, parents) in table.where[1]
            ]
        for number, element in enumerate(rows):
            record = {}
            for leaf, rules in table.fields.items():
                value, problem = None, None
                for rule in rules:
                    value, problem = _value(rule, element, mapping, indexes, parents)
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
                leaves = _leaves(element, mapping)
                unread = sorted(set(leaves) - read - table.ignore)
                if unread:
                    record["unread"] = [f"{leaf}={leaves[leaf]}" for leaf in unread]
            records.append(record)
        found[table.name] = records
    return found, problems

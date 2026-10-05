"""A NETCONF definition maps one device table to check-item Value rows.

A definition is data (docs/netconf-api-handoff.md §5.2): where the table is (namespace,
path to the row element, state or running configuration), which check item its rows
serve, the leaves identifying a row, and for each Value the leaf it is read from and how
it is written the way the CLI prints it. From one definition come

- the subtree filter selecting exactly the leaves it reads, never a whole module;
- from the <data> the device returns, rows of {Value: text} like a TextFSM template's,
  so topology's projections take NETCONF rows unchanged, and the problems found.

Nothing is guessed: a code the definition does not list, a lookup that finds no row or a
leaf that is not of its Value's type leaves the Value empty with a problem, and a row
without a required Value is not output. Names are checked when a definition is loaded
and the filter is built as XML, so a definition cannot put anything else into the RPC
(the platform checks the filter again).
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

from defusedxml import ElementTree

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")  # an XML element name without a prefix
NAMESPACE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:[^\s<>\"'&{}]+")
DEFINITION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
CHECK_ITEM_ID = re.compile(r"[a-z0-9_]+(\.[a-z0-9_]+)+")
VALUE_NAME = re.compile(r"[A-Z][A-Z0-9_]*")  # a TextFSM Value name
DATASTORES = frozenset({"state", "running"})  # <get>, <get-config> of running
FIELDS = frozenset(
    {
        "id",
        "description",
        "module",
        "vendor",
        "platforms",
        "check_item",
        "namespace",
        "datastore",
        "path",
        "keys",
        "values",
    }
)
RULE_FIELDS = frozenset({"leaf", "codes", "lookup", "format"})
UNASSIGNED = frozenset({"unassigned", "--", "none", "n/a", "na"})


class DefinitionError(ValueError):
    """A definition that cannot be used; raised when it is loaded."""


class MappingError(ValueError):
    """Input the mapping cannot read at all (not XML, a lookup table not read)."""


def _mac(separator):
    def write(text):
        digits = re.sub(r"[-.:]", "", text)
        if not re.fullmatch(r"[0-9A-Fa-f]{12}", digits):
            return None
        digits = digits.lower()
        return separator.join(digits[i : i + 4] for i in range(0, 12, 4))

    return write


# How a leaf is written as the CLI prints it; None when the leaf is not of that kind.
FORMATS: dict[str, Callable[[str], str | None]] = {
    "mac_dash4": _mac("-"),  # Comware: aabb-cc00-0210
    "mac_dot4": _mac("."),  # IOS: aabb.cc00.0210
}


@dataclass(frozen=True)
class Lookup:
    """A Value read through another definition's table, which is read first."""

    definition: str
    match: str  # its leaf compared with this Value's leaf
    take: str  # its leaf written as the Value


@dataclass(frozen=True)
class Rule:
    name: str
    leaves: tuple[str, ...]  # several leaves form one code, joined by "/"
    codes: Mapping[str, str] | None = None
    lookup: Lookup | None = None
    format: str | None = None


@dataclass(frozen=True)
class Definition:
    id: str
    vendor: str
    platforms: tuple[str, ...]
    namespace: str
    datastore: str
    path: tuple[str, ...]  # from the top element to the row element
    keys: tuple[str, ...]  # leaves naming a row in problems
    check_item: str | None = None  # None: a table only read through lookups
    values: tuple[Rule, ...] = ()
    description: str = ""
    module: str | None = None  # the YANG module the device announces for the table

    def leaves(self):
        """The leaves the definition reads itself, keys first."""
        found = [*self.keys, *(leaf for rule in self.values for leaf in rule.leaves)]
        return tuple(dict.fromkeys(found))

    def lookups(self):
        return tuple(rule.lookup for rule in self.values if rule.lookup)


@dataclass(frozen=True)
class Problem:
    """``kind``: unmapped_code, lookup_miss, lookup_ambiguous, type_mismatch or
    required_missing (the row was not output)."""

    row: int  # 1-based, in the device's order
    key: str  # the row's key leaves, "Ipv4Address=10.0.13.2"
    value: str  # the Value
    kind: str
    raw: str | None  # the device's text (several leaves joined by "/")


@dataclass(frozen=True)
class Mapped:
    rows: list[dict[str, str]]  # every Value of the definition; "" when it has none
    refs: list[int]  # the device row each output row came from
    problems: list[Problem]
    read: int  # rows the device returned


@dataclass(frozen=True)
class Table:
    """A definition's rows as {leaf: text or None}, read for lookups."""

    definition: str
    rows: list[dict[str, str | None]]


# --- loading ---------------------------------------------------------------------------


def _text(raw, where, pattern=None):
    if not isinstance(raw, str) or not raw.strip():
        raise DefinitionError(f"{where} must be a non-empty string")
    text = raw.strip()
    if pattern is not None and not pattern.fullmatch(text):
        raise DefinitionError(f"{where} is not valid: {text!r}")
    return text


def _path(raw, where):
    """ "a/b/c" (or a list of names) as a tuple of element names."""
    names = raw.split("/") if isinstance(raw, str) else raw
    if not isinstance(names, (list, tuple)) or not names:
        raise DefinitionError(f"{where} must be a path of element names")
    return tuple(_text(name, where, NAME) for name in names)


def _leaf(raw, where):
    return "/".join(_path(raw, where))


def _rule(name, raw, where):
    if isinstance(raw, (str, list)):
        raw = {"leaf": raw}
    if not isinstance(raw, Mapping):
        raise DefinitionError(f"{where} must be a leaf or a mapping")
    if unknown := sorted(set(raw) - RULE_FIELDS):
        raise DefinitionError(f"{where} has unknown fields {unknown}")
    if "leaf" not in raw:
        raise DefinitionError(f"{where} names no leaf")
    leaves = raw["leaf"] if isinstance(raw["leaf"], list) else [raw["leaf"]]
    if not leaves:
        raise DefinitionError(f"{where}.leaf is empty")
    leaves = tuple(_leaf(leaf, f"{where}.leaf") for leaf in leaves)
    if len(set(leaves)) != len(leaves):
        raise DefinitionError(f"{where}.leaf repeats a leaf")
    ways = [way for way in ("codes", "lookup", "format") if raw.get(way) is not None]
    if len(ways) > 1:
        raise DefinitionError(f"{where} may translate one way only, not {ways}")
    if len(leaves) > 1 and ways != ["codes"]:
        raise DefinitionError(f"{where}: several leaves form a code and need codes")
    codes = lookup = form = None
    if "codes" in ways:
        if not isinstance(raw["codes"], Mapping) or not raw["codes"]:
            raise DefinitionError(f"{where}.codes must be a non-empty mapping")
        codes = {}
        for code, written in raw["codes"].items():
            code = str(code)
            if isinstance(written, bool) or not isinstance(written, (str, int)):
                raise DefinitionError(f"{where}.codes[{code}] must be text")
            if len(code.split("/")) != len(leaves):
                raise DefinitionError(f"{where}.codes[{code}] needs {len(leaves)} parts")
            codes[code] = str(written)
    if "lookup" in ways:
        spec = raw["lookup"]
        if not isinstance(spec, Mapping) or set(spec) != {"definition", "match", "take"}:
            raise DefinitionError(f"{where}.lookup needs definition, match and take")
        lookup = Lookup(
            _text(spec["definition"], f"{where}.lookup.definition", DEFINITION_ID),
            _leaf(spec["match"], f"{where}.lookup.match"),
            _leaf(spec["take"], f"{where}.lookup.take"),
        )
    if "format" in ways:
        form = _text(raw["format"], f"{where}.format")
        if form not in FORMATS:
            raise DefinitionError(f"{where}.format is not one of {sorted(FORMATS)}")
    return Rule(name, leaves, codes, lookup, form)


def load(raw: Mapping[str, Any]) -> Definition:
    """A definition from its stored form; DefinitionError when it cannot be used."""
    if not isinstance(raw, Mapping):
        raise DefinitionError("a definition must be a mapping")
    if unknown := sorted(set(raw) - FIELDS):
        raise DefinitionError(f"unknown definition fields {unknown}")
    definition_id = _text(raw.get("id"), "id", DEFINITION_ID)
    where = definition_id
    platforms = raw.get("platforms")
    if not isinstance(platforms, list) or not platforms:
        raise DefinitionError(f"{where}.platforms must be a non-empty list")
    datastore = _text(raw.get("datastore"), f"{where}.datastore")
    if datastore not in DATASTORES:
        raise DefinitionError(f"{where}.datastore must be one of {sorted(DATASTORES)}")
    path = _path(raw.get("path"), f"{where}.path")
    if len(path) < 2:
        raise DefinitionError(f"{where}.path must lead from the top element to a row")
    keys = raw.get("keys") or []
    if not isinstance(keys, list):
        raise DefinitionError(f"{where}.keys must be a list")
    check_item = raw.get("check_item")
    if check_item is not None:
        check_item = _text(check_item, f"{where}.check_item", CHECK_ITEM_ID)
    values = raw.get("values") or {}
    if not isinstance(values, Mapping):
        raise DefinitionError(f"{where}.values must be a mapping")
    if check_item and not values:
        raise DefinitionError(f"{where} serves {check_item} but maps no Value")
    if values and not check_item:
        raise DefinitionError(f"{where} maps Values but serves no check item")
    if not keys and not values:
        raise DefinitionError(f"{where} reads no leaf")
    rules = tuple(
        _rule(_text(name, f"{where}.values", VALUE_NAME), rule, f"{where}.values.{name}")
        for name, rule in values.items()
    )
    if any(rule.lookup and rule.lookup.definition == definition_id for rule in rules):
        raise DefinitionError(f"{where} cannot look up its own table")
    module = raw.get("module")
    if module is not None:
        module = _text(module, f"{where}.module", NAME)
    description = raw.get("description") or ""
    if not isinstance(description, str):
        raise DefinitionError(f"{where}.description must be text")
    return Definition(
        id=definition_id,
        vendor=_text(raw.get("vendor"), f"{where}.vendor"),
        platforms=tuple(_text(name, f"{where}.platforms") for name in platforms),
        namespace=_text(raw.get("namespace"), f"{where}.namespace", NAMESPACE),
        datastore=datastore,
        path=path,
        keys=tuple(_leaf(leaf, f"{where}.keys") for leaf in keys),
        check_item=check_item,
        values=rules,
        description=description,
        module=module,
    )


def load_all(raws: Iterable[Mapping[str, Any]]) -> dict[str, Definition]:
    """Definitions by id; every lookup must name one of them."""
    found = {}
    for raw in raws:
        definition = load(raw)
        if definition.id in found:
            raise DefinitionError(f"{definition.id} is defined twice")
        found[definition.id] = definition
    for definition in found.values():
        for lookup in definition.lookups():
            if lookup.definition not in found:
                raise DefinitionError(f"{definition.id} looks up unknown {lookup.definition}")
    return found


def contract_gaps(definition: Definition, fields: Mapping[str, Mapping[str, Any]]):
    """How a definition falls short of its check item's Values (``Selection.fields``):
    Values outside the contract, required Values it does not map."""
    mapped = {rule.name for rule in definition.values}
    gaps = [f"value_outside_contract:{name}" for name in sorted(mapped - set(fields))]
    gaps += [
        f"required_value_unmapped:{name}"
        for name, spec in sorted(fields.items())
        if spec.get("required") and name not in mapped
    ]
    return gaps


# --- the filter ------------------------------------------------------------------------


def subtree_filter(definition: Definition, extra: Iterable[str] = ()) -> str:
    """The subtree filter selecting the definition's leaves, plus ``extra`` leaves (read
    by a lookup into this table), in every row."""
    for leaf in extra:
        _leaf(leaf, f"{definition.id} lookup leaf")
    root = Element(definition.path[0], xmlns=definition.namespace)
    row = root
    for name in definition.path[1:]:
        row = SubElement(row, name)
    selected = []
    # Shallow leaves first: a selected leaf already brings everything under it.
    for leaf in sorted(dict.fromkeys((*definition.leaves(), *extra)), key=lambda x: x.count("/")):
        if any(leaf.startswith(f"{other}/") for other in selected):
            continue
        selected.append(leaf)
        node = row
        for name in leaf.split("/"):
            child = next((item for item in node if item.tag == name), None)
            node = child if child is not None else SubElement(node, name)
    return tostring(root, encoding="unicode")


def fetcher(session) -> Callable[[Definition, str], str]:
    """Reads a definition's filter over a platform NETCONF session."""

    def fetch(definition, subtree):
        if definition.datastore == "running":
            return session.get_config(subtree, source="running")
        return session.get(subtree)

    return fetch


# --- reading ---------------------------------------------------------------------------


def _elements(definition, data):
    """The row elements of the definition's table in a <data> (or a whole rpc-reply)."""
    try:
        root = ElementTree.fromstring(data)
    except Exception:
        raise MappingError(f"{definition.id}: the device reply is not XML") from None
    ns = f"{{{definition.namespace}}}"
    head, *rest = definition.path
    path = "/".join(ns + name for name in rest)
    return [row for top in root.iter(ns + head) for row in top.findall(path)]


def _read(element, leaf, ns):
    node = element.find("/".join(ns + name for name in leaf.split("/")))
    text = (node.text or "").strip() if node is not None else ""
    return text or None  # an empty leaf is no value


def read_table(definition: Definition, data: str, leaves: Iterable[str]) -> Table:
    ns = f"{{{definition.namespace}}}"
    leaves = tuple(dict.fromkeys(leaves))
    return Table(
        definition.id,
        [
            {leaf: _read(element, leaf, ns) for leaf in leaves}
            for element in _elements(definition, data)
        ],
    )


def _index(table, lookup):
    """{match: take}; a match the table lists with different takes maps to None."""
    found = {}
    for row in table.rows:
        match, take = row.get(lookup.match), row.get(lookup.take)
        if match is None or take is None:
            continue  # a row without the take is no answer (a miss, not a conflict)
        found[match] = take if found.get(match, take) == take else None
    return found


def _write(rule, text, indexes):
    """(written, problem kind) for a Value whose leaves read ``text``."""
    if rule.codes is not None:
        return (rule.codes[text], None) if text in rule.codes else (None, "unmapped_code")
    if rule.lookup is not None:
        index = indexes[rule.lookup]
        if text not in index:
            return None, "lookup_miss"
        written = index[text]
        return (written, None) if written is not None else (None, "lookup_ambiguous")
    if rule.format is not None:
        written = FORMATS[rule.format](text)
        return (written, None) if written is not None else (None, "type_mismatch")
    return text, None


def _typed(spec, value):
    """Whether a written value is of its check-item Value's type."""
    kind = (spec or {}).get("value_type")
    if kind == "integer":
        return re.fullmatch(r"-?\d+", value) is not None
    if kind in {"ip", "ip_or_unassigned"}:
        if kind == "ip_or_unassigned" and value.casefold() in UNASSIGNED:
            return True
        try:
            ipaddress.ip_address(value)
        except ValueError:
            return False
    return True


def map_rows(
    definition: Definition,
    data: str,
    *,
    fields: Mapping[str, Mapping[str, Any]] | None = None,
    tables: Mapping[str, Table] | None = None,
) -> Mapped:
    """Value rows from the <data> a device returned for the definition's filter.

    ``fields`` is the check item's ``Selection.fields`` (types and required Values);
    without it no Value is required or typed. ``tables`` holds the tables the
    definition looks up, read first (``collect`` does both).
    """
    fields = fields or {}
    if fields and (outside := sorted({rule.name for rule in definition.values} - set(fields))):
        raise DefinitionError(f"{definition.id}: Values outside {definition.check_item}: {outside}")
    indexes = {}
    for lookup in definition.lookups():
        table = (tables or {}).get(lookup.definition)
        if table is None:
            raise MappingError(f"{definition.id}: table {lookup.definition} was not read")
        indexes[lookup] = _index(table, lookup)
    ns = f"{{{definition.namespace}}}"
    required = [name for name, spec in fields.items() if spec.get("required")]
    elements = _elements(definition, data)
    rows, refs, problems = [], [], []
    for number, element in enumerate(elements, 1):
        key = ",".join(f"{leaf}={_read(element, leaf, ns) or ''}" for leaf in definition.keys)
        row, found = {}, []
        for rule in definition.values:
            texts = [_read(element, leaf, ns) for leaf in rule.leaves]
            # Part of a code present is still a code (one the definition cannot list).
            text = (
                None
                if all(item is None for item in texts)
                else "/".join(item or "" for item in texts)
            )
            written, kind = _write(rule, text, indexes) if text is not None else (None, None)
            if written and not _typed(fields.get(rule.name), written):
                written, kind = None, "type_mismatch"
            if kind:
                found.append(Problem(number, key, rule.name, kind, text))
            row[rule.name] = written or ""
        missing = [name for name in required if not row.get(name)]
        found += [Problem(number, key, name, "required_missing", None) for name in missing]
        problems += found
        if not missing:
            rows.append(row)
            refs.append(number)
    return Mapped(rows, refs, problems, len(elements))


def collect(
    definitions: Mapping[str, Definition],
    definition_id: str,
    fetch: Callable[[Definition, str], str],
    *,
    fields: Mapping[str, Mapping[str, Any]] | None = None,
) -> Mapped:
    """Reads the tables a definition looks up, then its own table, and maps it.

    ``fetch(definition, filter)`` returns the device's <data> (``fetcher(session)``).
    """
    definition = definitions[definition_id]
    extra = {}
    for lookup in definition.lookups():
        if lookup.definition not in definitions:
            raise DefinitionError(f"{definition.id} looks up unknown {lookup.definition}")
        extra.setdefault(lookup.definition, []).extend((lookup.match, lookup.take))
    tables = {}
    for name, leaves in extra.items():
        table = definitions[name]
        data = fetch(table, subtree_filter(table, leaves))
        tables[name] = read_table(table, data, (*table.leaves(), *leaves))
    data = fetch(definition, subtree_filter(definition))
    return map_rows(definition, data, fields=fields, tables=tables)

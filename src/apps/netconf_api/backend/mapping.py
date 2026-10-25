"""A NETCONF definition maps device tables to check-item Value rows.

A definition is data: where its tables are
(namespace, path to each row element, state or running configuration), which check item
the rows serve, the leaves identifying a row, and for each Value the leaf it is read from
and how it is written the way the CLI prints it. One check item may be spread over
several device tables (Comware keeps basic and advanced ACL rules apart); each is a
part of the definition and their rows are output in turn. From one definition come

- the subtree filter selecting exactly what it reads, never a whole module (a part that
  must see every condition of a row, ``rest``, selects whole rows of its table);
- from the <data> the device returns, rows of {Value: text} like a TextFSM template's,
  so topology's projections take NETCONF rows unchanged, and the problems found.

Nothing is guessed: a code the definition does not list, a lookup that finds no row
(unless the definition says the Value is optional there: a policy node without a next
hop), a row that does not hold what the definition expects of it or a leaf that is not of
its Value's type leaves the Value empty (or the row out) with a problem, and a row without
a required Value is not output. A leaf the definition neither reads nor ignores is written
into its ``rest`` Value (OTHER: a condition the reader does not know), never dropped.
Names are checked when a definition is loaded and the filter is built as XML, so a
definition cannot put anything else into the RPC (the platform checks the filter again).
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
SELF = "."  # the row element's own text (a table whose rows are single leaves)
ANY = "*"  # a condition met by any value present
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
        "parts",
        *("path", "keys", "values", "where", "expect", "rest", "order"),  # one-part form
    }
)
PART_FIELDS = frozenset({"path", "keys", "values", "where", "expect", "rest", "order"})
RULE_FIELDS = frozenset({"leaf", "codes", "lookup", "format", "const", "when", "default", "none"})
UNASSIGNED = frozenset({"unassigned", "--", "none", "n/a", "na"})
UNEXPECTED = object()  # a lookup answered by a row its table does not expect


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
class Condition:
    """A leaf's value is (or, negated, is not) one of ``values``; ``*``: any value."""

    leaf: str
    values: frozenset[str]
    negated: bool = False

    def holds(self, text):
        found = text is not None and (ANY in self.values or text in self.values)
        return found != self.negated


@dataclass(frozen=True)
class Lookup:
    """A Value read through another definition's table, which is read first. Only the
    table's entities count (its ``where``); a row that does not hold its ``expect`` is no
    answer for its match (``lookup_unexpected``)."""

    definition: str
    match: tuple[str, ...]  # its leaves compared with this Value's; (): its only row
    take: str  # its leaf written as the Value
    optional: bool = False  # a match the table does not list is no value, not a miss


@dataclass(frozen=True)
class Rule:
    name: str
    leaves: tuple[str, ...]  # several leaves form one code, joined by "/"
    codes: Mapping[str, str] | None = None
    lookup: Lookup | None = None
    format: str | None = None
    const: str | None = None  # written for every row of the part
    when: tuple[Condition, ...] = ()  # the Value is read only where all hold
    default: str | None = None  # written when the device gives no value
    none: frozenset[str] = frozenset()  # device texts the CLI prints as nothing


@dataclass(frozen=True)
class Rest:
    """Leaves of a row nothing reads and nothing ignores, written into one Value."""

    value: str
    ignore: frozenset[str]


@dataclass(frozen=True)
class Order:
    """The rows of one group are output in the order a leaf numbers them, when all have
    it (a Comware ACL in auto match order lists its rules by RuleID, numbering the match
    order in MatchIndex)."""

    group: str
    by: str


@dataclass(frozen=True)
class Part:
    path: tuple[str, ...]  # from the top element to the row element
    keys: tuple[str, ...]  # leaves naming a row in problems
    values: tuple[Rule, ...] = ()
    where: tuple[Condition, ...] = ()  # the rows of this table that are entities
    expect: tuple[Condition, ...] = ()  # a row that does not hold all is reported
    rest: Rest | None = None
    order: Order | None = None

    @property
    def table(self):
        return "/".join(self.path[1:])

    def leaves(self):
        """The leaves the part reads itself, keys first."""
        found = [*self.keys]
        for rule in self.values:
            found += [*rule.leaves, *(condition.leaf for condition in rule.when)]
        found += [condition.leaf for condition in (*self.where, *self.expect)]
        if self.order:
            found += [self.order.group, self.order.by]
        return tuple(dict.fromkeys(found))


@dataclass(frozen=True)
class Definition:
    id: str
    vendor: str
    platforms: tuple[str, ...]
    namespace: str
    datastore: str
    parts: tuple[Part, ...]
    check_item: str | None = None  # None: a table only read through lookups
    description: str = ""
    module: str | None = None  # the YANG module the device announces for the tables

    # The one-part form, which every lookup table has.
    @property
    def path(self):
        return self.parts[0].path

    @property
    def keys(self):
        return self.parts[0].keys

    @property
    def values(self):
        return tuple(rule for part in self.parts for rule in part.values)

    def names(self):
        """Every Value the definition writes (each row has them all, "" when none)."""
        found = [rule.name for rule in self.values]
        found += [part.rest.value for part in self.parts if part.rest]
        return tuple(dict.fromkeys(found))

    def leaves(self):
        return self.parts[0].leaves()

    def lookups(self):
        return tuple(dict.fromkeys(rule.lookup for rule in self.values if rule.lookup))


@dataclass(frozen=True)
class Problem:
    """``kind``: unmapped_code, lookup_miss, lookup_ambiguous, lookup_unexpected,
    type_mismatch, unexpected_value or required_missing (the last two: the row was not
    output)."""

    row: int  # 1-based, across the definition's tables in reading order
    key: str  # the row's key leaves, "Ipv4Address=10.0.13.2"
    value: str  # the Value (unexpected_value: the leaf)
    kind: str
    raw: str | None  # the device's text (several leaves joined by "/")
    table: str = ""  # the device table, "ARP/ArpTable/ArpEntry"


@dataclass(frozen=True)
class Mapped:
    rows: list[dict[str, str]]  # every Value of the definition; "" when it has none
    refs: list[int]  # the device row each output row came from (Problem.row)
    problems: list[Problem]
    read: int  # rows the device returned that are entities (``where``)


@dataclass(frozen=True)
class Table:
    """A definition's rows as {leaf: text or None}, read for lookups: its entities only
    (``where``); ``unexpected`` numbers the rows that do not hold its ``expect``."""

    definition: str
    rows: list[dict[str, str | None]]
    unexpected: frozenset[int] = frozenset()


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
    if raw == SELF:
        return SELF
    return "/".join(_path(raw, where))


def _leaves(raw, where):
    items = raw if isinstance(raw, list) else [raw]
    if not items:
        raise DefinitionError(f"{where} is empty")
    leaves = tuple(_leaf(item, where) for item in items)
    if len(set(leaves)) != len(leaves):
        raise DefinitionError(f"{where} repeats a leaf")
    return leaves


def _scalar(value, where):
    if isinstance(value, bool):
        return "true" if value else "false"  # YAML reads true/false leaves as booleans
    if not isinstance(value, (str, int)):
        raise DefinitionError(f"{where} must be text")
    return str(value)


def _conditions(raw, where):
    """{leaf: value or [values]} as conditions; values all "!x" negate the list."""
    if raw is None:
        return ()
    if not isinstance(raw, Mapping) or not raw:
        raise DefinitionError(f"{where} must be a non-empty mapping of leaves")
    found = []
    for leaf, values in raw.items():
        values = values if isinstance(values, list) else [values]
        texts = [_scalar(value, f"{where}.{leaf}") for value in values]
        if not texts:
            raise DefinitionError(f"{where}.{leaf} lists no value")
        negated = {text.startswith("!") for text in texts}
        if len(negated) > 1:
            raise DefinitionError(f"{where}.{leaf} mixes values and negations")
        negated = negated.pop()
        texts = [text[1:] if negated else text for text in texts]
        found.append(Condition(_leaf(leaf, f"{where}.{leaf}"), frozenset(texts), negated))
    return tuple(found)


def _codes(raw, parts, where):
    if not isinstance(raw, Mapping) or not raw:
        raise DefinitionError(f"{where} must be a non-empty mapping")
    codes = {}
    for code, written in raw.items():
        code = _scalar(code, where)
        if len(code.split("/")) != parts:
            raise DefinitionError(f"{where}[{code}] needs {parts} parts")
        codes[code] = _scalar(written, f"{where}[{code}]")
    return codes


def _rule(name, raw, where):
    if isinstance(raw, (str, list)):
        raw = {"leaf": raw}
    if not isinstance(raw, Mapping):
        raise DefinitionError(f"{where} must be a leaf or a mapping")
    if unknown := sorted(set(raw) - RULE_FIELDS):
        raise DefinitionError(f"{where} has unknown fields {unknown}")
    when = _conditions(raw.get("when"), f"{where}.when")
    if "const" in raw:
        if set(raw) - {"const", "when"}:
            raise DefinitionError(f"{where}: a constant reads no leaf")
        return Rule(name, (), const=_scalar(raw["const"], f"{where}.const"), when=when)
    lookup = None
    if raw.get("lookup") is not None:
        spec = raw["lookup"]
        if (
            not isinstance(spec, Mapping)
            or not {"definition", "take"} <= set(spec)
            or (set(spec) - {"definition", "match", "take", "optional"})
        ):
            raise DefinitionError(f"{where}.lookup needs definition, take and (or) match")
        match = _leaves(spec["match"], f"{where}.lookup.match") if "match" in spec else ()
        optional = spec.get("optional", False)
        if not isinstance(optional, bool) or (optional and not match):
            raise DefinitionError(f"{where}.lookup.optional is true or false, with a match")
        lookup = Lookup(
            _text(spec["definition"], f"{where}.lookup.definition", DEFINITION_ID),
            match,
            _leaf(spec["take"], f"{where}.lookup.take"),
            optional,
        )
    if "leaf" in raw:
        leaves = _leaves(raw["leaf"], f"{where}.leaf")
    elif lookup is not None and not lookup.match:
        leaves = ()  # the looked-up table's only row
    else:
        raise DefinitionError(f"{where} names no leaf")
    if lookup is not None and len(lookup.match) != len(leaves):
        raise DefinitionError(f"{where}: the lookup matches {len(lookup.match)} leaves")
    ways = [way for way in ("codes", "format") if raw.get(way) is not None]
    if len(ways) > 1:
        raise DefinitionError(f"{where} may translate one way only, not {ways}")
    if len(leaves) > 1 and not (ways == ["codes"] or lookup is not None):
        raise DefinitionError(f"{where}: several leaves form a code and need codes or a lookup")
    codes = form = None
    if "codes" in ways:
        codes = _codes(raw["codes"], 1 if lookup else len(leaves), f"{where}.codes")
    if "format" in ways:
        form = _text(raw["format"], f"{where}.format")
        if form not in FORMATS:
            raise DefinitionError(f"{where}.format is not one of {sorted(FORMATS)}")
    default = _scalar(raw["default"], f"{where}.default") if "default" in raw else None
    none = raw.get("none") or []
    none = frozenset(
        _scalar(text, f"{where}.none") for text in (none if isinstance(none, list) else [none])
    )
    return Rule(name, leaves, codes, lookup, form, None, when, default, none)


def _part(raw, where):
    if unknown := sorted(set(raw) - PART_FIELDS):
        raise DefinitionError(f"{where} has unknown fields {unknown}")
    path = _path(raw.get("path"), f"{where}.path")
    if len(path) < 2:
        raise DefinitionError(f"{where}.path must lead from the top element to a row")
    keys = raw.get("keys") or []
    if not isinstance(keys, list):
        raise DefinitionError(f"{where}.keys must be a list")
    values = raw.get("values") or {}
    if not isinstance(values, Mapping):
        raise DefinitionError(f"{where}.values must be a mapping")
    rules = tuple(
        _rule(_text(name, f"{where}.values", VALUE_NAME), rule, f"{where}.values.{name}")
        for name, rule in values.items()
    )
    rest = None
    if raw.get("rest") is not None:
        spec = raw["rest"]
        if not isinstance(spec, Mapping) or set(spec) - {"value", "ignore"} or "value" not in spec:
            raise DefinitionError(f"{where}.rest needs a value and may list leaves to ignore")
        rest = Rest(
            _text(spec["value"], f"{where}.rest.value", VALUE_NAME),
            frozenset(_leaf(leaf, f"{where}.rest.ignore") for leaf in spec.get("ignore") or []),
        )
        if rest.value in {rule.name for rule in rules}:
            raise DefinitionError(f"{where}.rest writes {rest.value}, which a leaf already does")
    order = None
    if raw.get("order") is not None:
        spec = raw["order"]
        if not isinstance(spec, Mapping) or set(spec) != {"group", "by"}:
            raise DefinitionError(f"{where}.order needs group and by")
        order = Order(_leaf(spec["group"], f"{where}.order"), _leaf(spec["by"], f"{where}.order"))
    part = Part(
        path,
        tuple(_leaf(leaf, f"{where}.keys") for leaf in keys),
        rules,
        _conditions(raw.get("where"), f"{where}.where"),
        _conditions(raw.get("expect"), f"{where}.expect"),
        rest,
        order,
    )
    if not part.leaves() and not rest:
        raise DefinitionError(f"{where} reads no leaf")
    return part


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
    single = {key: raw[key] for key in PART_FIELDS if key in raw}
    if "parts" in raw:
        if single:
            raise DefinitionError(f"{where}: parts and a one-part {sorted(single)} together")
        if not isinstance(raw["parts"], list) or not raw["parts"]:
            raise DefinitionError(f"{where}.parts must be a non-empty list")
        if not all(isinstance(part, Mapping) for part in raw["parts"]):
            raise DefinitionError(f"{where}.parts must be mappings")
        parts = tuple(_part(part, f"{where}.parts[{n}]") for n, part in enumerate(raw["parts"]))
    else:
        parts = (_part(single, where),)
    check_item = raw.get("check_item")
    if check_item is not None:
        check_item = _text(check_item, f"{where}.check_item", CHECK_ITEM_ID)
    writes = any(part.values or part.rest for part in parts)
    if check_item and not writes:
        raise DefinitionError(f"{where} serves {check_item} but maps no Value")
    if writes and not check_item:
        raise DefinitionError(f"{where} maps Values but serves no check item")
    if len(parts) > 1 and not check_item:
        raise DefinitionError(f"{where}: a table read through lookups has one part")
    for part in parts:
        if any(rule.lookup and rule.lookup.definition == definition_id for rule in part.values):
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
        parts=parts,
        check_item=check_item,
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
    mapped = set(definition.names())
    gaps = [f"value_outside_contract:{name}" for name in sorted(mapped - set(fields))]
    gaps += [
        f"required_value_unmapped:{name}"
        for name, spec in sorted(fields.items())
        if spec.get("required") and name not in mapped
    ]
    return gaps


# --- the filter ------------------------------------------------------------------------


def _select(root, part, extra=()):
    """Add one part's selection under ``root``: its leaves in every row, or whole rows."""
    node = root
    for name in part.path[1:]:
        child = next((item for item in node if item.tag == name), None)
        node = child if child is not None else SubElement(node, name)
    if part.rest is not None:
        for child in list(node):  # a whole row: a selection node without children
            node.remove(child)
        return
    selected = []
    leaves = [leaf for leaf in (*part.leaves(), *extra) if leaf != SELF]
    # Shallow leaves first: a selected leaf already brings everything under it.
    for leaf in sorted(dict.fromkeys(leaves), key=lambda x: x.count("/")):
        if any(leaf.startswith(f"{other}/") for other in selected):
            continue
        selected.append(leaf)
        target = node
        for name in leaf.split("/"):
            child = next((item for item in target if item.tag == name), None)
            target = child if child is not None else SubElement(target, name)


def subtree_filter(definition: Definition, extra: Iterable[str] = ()) -> str:
    """The subtree filter selecting what the definition reads, plus ``extra`` leaves (read
    by a lookup into this one-part table), in every row; all parts in one filter."""
    extra = tuple(extra)
    for leaf in extra:
        _leaf(leaf, f"{definition.id} lookup leaf")
    heads = {part.path[0] for part in definition.parts}
    if len(heads) != 1:
        raise DefinitionError(f"{definition.id}: its tables are under different top elements")
    root = Element(definition.parts[0].path[0], xmlns=definition.namespace)
    for number, part in enumerate(definition.parts):
        _select(root, part, extra if number == 0 else ())
    return tostring(root, encoding="unicode")


def fetcher(session) -> Callable[[Definition, str], str]:
    """Reads a definition's filter over a platform NETCONF session."""

    def fetch(definition, subtree):
        if definition.datastore == "running":
            return session.get_config(subtree, source="running")
        return session.get(subtree)

    return fetch


# --- reading ---------------------------------------------------------------------------


def _root(definition, data):
    try:
        return ElementTree.fromstring(data)
    except Exception:
        raise MappingError(f"{definition.id}: the device reply is not XML") from None


def _rows_of(part, root, ns):
    head, *rest = part.path
    path = "/".join(ns + name for name in rest)
    return [row for top in root.iter(ns + head) for row in top.findall(path)]


def _elements(definition, data, part=None):
    """The row elements of a part's table (the first one by default) in a <data> (or a
    whole rpc-reply)."""
    part = part or definition.parts[0]
    return _rows_of(part, _root(definition, data), f"{{{definition.namespace}}}")


def _read(element, leaf, ns):
    node = element if leaf == SELF else element.find("/".join(ns + n for n in leaf.split("/")))
    text = (node.text or "").strip() if node is not None else ""
    return text or None  # an empty leaf is no value


def _all_leaves(element, prefix=""):
    """{leaf path: text} of every leaf under a row element."""
    found = {}
    for child in element:
        name = prefix + child.tag.split("}", 1)[-1]
        if len(child):
            found.update(_all_leaves(child, name + "/"))
        else:
            found[name] = (child.text or "").strip()
    return found


def read_table(definition: Definition, data: str, leaves: Iterable[str]) -> Table:
    ns = f"{{{definition.namespace}}}"
    part = definition.parts[0]
    leaves = tuple(dict.fromkeys((*leaves, *part.leaves())))
    rows = [
        {leaf: _read(element, leaf, ns) for leaf in leaves}
        for element in _elements(definition, data)
    ]
    rows = [row for row in rows if all(c.holds(row.get(c.leaf)) for c in part.where)]
    unexpected = frozenset(
        number
        for number, row in enumerate(rows)
        if not all(c.holds(row.get(c.leaf)) for c in part.expect)
    )
    return Table(definition.id, rows, unexpected)


def _joined(row, leaves):
    texts = [row.get(leaf) for leaf in leaves]
    return None if None in texts else "/".join(texts)


def _index(table, lookup):
    """{match: take}; a match the table lists with different takes maps to None, one a
    row it does not expect lists maps to UNEXPECTED. An unkeyed lookup is the table's only
    row, keyed by ""."""
    found, unexpected = {}, set()
    rows = table.rows
    for number, row in enumerate(rows):
        match = _joined(row, lookup.match) if lookup.match else ""
        if number in table.unexpected:
            if match is not None:
                unexpected.add(match)
            continue
        take = row.get(lookup.take)
        if match is None or take is None:
            continue  # a row without the take is no answer (a miss, not a conflict)
        found[match] = take if found.get(match, take) == take else None
    found.update(dict.fromkeys(unexpected, UNEXPECTED))
    if not lookup.match and len(rows) > 1:
        found[""] = None  # several rows: no single answer
    return found


def _write(rule, element, ns, indexes):
    """(written, problem kind, the device's text) for one Value of a row."""
    if rule.const is not None:
        return rule.const, None, None
    texts = [_read(element, leaf, ns) for leaf in rule.leaves]
    # Part of a code present is still a code (one the definition cannot list).
    text = None if all(item is None for item in texts) else "/".join(item or "" for item in texts)
    if rule.lookup is not None and not rule.lookup.match:
        text = ""  # the looked-up table's only row
    elif text is None:
        return rule.default, None, None
    if text in rule.none:
        return None, None, text
    raw = text
    if rule.lookup is not None:
        index = indexes[rule.lookup]
        if text not in index:
            return None, None if rule.lookup.optional else "lookup_miss", raw
        text = index[text]
        if text is UNEXPECTED:
            return None, "lookup_unexpected", raw
        if text is None:
            return None, "lookup_ambiguous", raw
    if rule.codes is not None:
        return (
            (rule.codes[text], None, raw) if text in rule.codes else (None, "unmapped_code", text)
        )
    if rule.format is not None:
        written = FORMATS[rule.format](text)
        return (written, None, raw) if written is not None else (None, "type_mismatch", raw)
    return text, None, raw


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


def _rest(part, element):
    """The leaves of a row nothing reads or ignores, as "Leaf=value" text (a false flag
    is no condition)."""
    read = {leaf for leaf in part.leaves()} | {
        leaf for rule in part.values if rule.lookup for leaf in rule.leaves
    }
    found = [
        f"{leaf}={text}"
        for leaf, text in _all_leaves(element).items()
        if text not in ("", "false") and leaf not in read and leaf not in part.rest.ignore
    ]
    return " ".join(found)


def _ordered(part, entries, ns):
    """Rows of one group in the order ``part.order.by`` numbers them, when all have it."""
    if part.order is None:
        return entries
    groups, order = {}, []
    for entry in entries:
        group = _read(entry[0], part.order.group, ns)
        if group not in groups:
            groups[group] = []
            order.append(group)
        groups[group].append(entry)
    found = []
    for group in order:
        members = groups[group]
        numbers = [_read(entry[0], part.order.by, ns) for entry in members]
        if all(number is not None and number.isdigit() for number in numbers):
            members = [
                entry for _, entry in sorted(zip(map(int, numbers), members), key=lambda x: x[0])
            ]
        found += members
    return found


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
    names = definition.names()
    if fields and (outside := sorted(set(names) - set(fields))):
        raise DefinitionError(f"{definition.id}: Values outside {definition.check_item}: {outside}")
    indexes = {}
    for lookup in definition.lookups():
        table = (tables or {}).get(lookup.definition)
        if table is None:
            raise MappingError(f"{definition.id}: table {lookup.definition} was not read")
        indexes[lookup] = _index(table, lookup)
    root = _root(definition, data)
    ns = f"{{{definition.namespace}}}"
    required = [name for name, spec in fields.items() if spec.get("required")]
    rows, refs, problems, number = [], [], [], 0
    for part in definition.parts:
        entries = []
        for element in _rows_of(part, root, ns):
            if not all(
                condition.holds(_read(element, condition.leaf, ns)) for condition in part.where
            ):
                continue  # not an entity of this table (an interface without static NAT)
            number += 1
            entries.append((element, number))
        for element, ref in _ordered(part, entries, ns):
            key = ",".join(f"{leaf}={_read(element, leaf, ns) or ''}" for leaf in part.keys)

            def problem(value, kind, raw, ref=ref, key=key):
                return Problem(ref, key, value, kind, raw, part.table)

            unexpected = [
                problem(condition.leaf, "unexpected_value", _read(element, condition.leaf, ns))
                for condition in part.expect
                if not condition.holds(_read(element, condition.leaf, ns))
            ]
            if unexpected:
                problems += unexpected
                continue
            row, found = dict.fromkeys(names, ""), []
            for rule in part.values:
                if not all(c.holds(_read(element, c.leaf, ns)) for c in rule.when):
                    continue
                written, kind, raw = _write(rule, element, ns, indexes)
                if written and not _typed(fields.get(rule.name), written):
                    written, kind = None, "type_mismatch"
                if kind:
                    found.append(problem(rule.name, kind, raw))
                row[rule.name] = written or ""
            if part.rest is not None:
                row[part.rest.value] = _rest(part, element)
            missing = [name for name in required if not row.get(name)]
            found += [problem(name, "required_missing", None) for name in missing]
            problems += found
            if not missing:
                rows.append(row)
                refs.append(ref)
    return Mapped(rows, refs, problems, number)


def collect(
    definitions: Mapping[str, Definition],
    definition_id: str,
    fetch: Callable[[Definition, str], str],
    *,
    fields: Mapping[str, Mapping[str, Any]] | None = None,
) -> Mapped:
    """Reads the tables a definition looks up, then its own tables, and maps them.

    ``fetch(definition, filter)`` returns the device's <data> (``fetcher(session)``).
    """
    definition = definitions[definition_id]
    extra = {}
    for lookup in definition.lookups():
        if lookup.definition not in definitions:
            raise DefinitionError(f"{definition.id} looks up unknown {lookup.definition}")
        extra.setdefault(lookup.definition, []).extend((*lookup.match, lookup.take))
    tables = {}
    for name, leaves in extra.items():
        table = definitions[name]
        data = fetch(table, subtree_filter(table, leaves))
        tables[name] = read_table(table, data, (*table.leaves(), *leaves))
    data = fetch(definition, subtree_filter(definition))
    return map_rows(definition, data, fields=fields, tables=tables)

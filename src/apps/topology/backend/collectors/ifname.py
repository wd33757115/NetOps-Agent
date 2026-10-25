"""Explicit platform aliases; preserve unknown names instead of guessing a port type."""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .families import cli

KINDS = frozenset(
    {"ethernet", "lag", "vlanif", "loopback", "null", "register", "tunnel", "vsi", "nve", "mgmt"}
)


@lru_cache(maxsize=1)
def dialects():
    """{dialect: ((canonical prefix, kind, aliases), ...)} from ifnames.yaml."""
    data = yaml.safe_load(Path(__file__).with_name("ifnames.yaml").read_text(encoding="utf-8"))
    if data["schema_version"] != 1:
        raise ValueError("unknown interface name schema")
    table = {}
    for dialect, rows in data["dialects"].items():
        entries = []
        for row in rows:
            canonical, kind, aliases = row["canonical"], row["kind"], row["aliases"]
            if (
                set(row) != {"canonical", "kind", "aliases"}
                or kind not in KINDS
                or not all(isinstance(v, str) and v for v in (canonical, *aliases))
            ):
                raise ValueError("invalid interface name row")
            entries.append((canonical, kind, tuple(aliases)))
        table[dialect] = tuple(entries)
    return table


@dataclass(frozen=True)
class InterfaceName:
    raw_name: str
    name: str
    kind: str
    recognized: bool

    @property
    def physical(self):
        return self.recognized and self.kind in {"ethernet", "mgmt"} and "." not in self.name


def normalize_ifname(raw, platform):
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 256:
        raise ValueError("invalid interface name")
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise ValueError("control character in interface name")
    text = raw.strip(" ")
    for canonical, kind, aliases in dialects().get(cli(platform) or platform, ()):
        for alias in aliases:
            match = re.fullmatch(
                re.escape(alias) + r"([0-9]+(?:/[0-9]+)*(?:\.[0-9]+)?)", text, re.I | re.ASCII
            )
            if match:
                # Do not erase leading zeroes or lane identifiers; no evidence they are equivalent.
                return InterfaceName(raw, canonical + match.group(1), kind, True)
    return InterfaceName(raw, text, "unknown", False)

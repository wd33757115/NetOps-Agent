"""Device families as data (owner decision 2026-10-04: a new family is a data change).

Everything topology knows about a family is a row of ``signatures.yaml``; code reads the
rows and never names a family. A row has ``id``, ``vendor``, ``profile_id``,
``command_style`` (show/display) and ``pattern`` (its version output), and optionally:

- ``cli``: the device-access CLI dialect it speaks (IOS-XE speaks the IOS CLI); a family
  without one is recognized but not driven;
- ``vendor_pattern``: a regular expression whose first group names the vendor in the
  version output, for a family sold under several names (H3C, HPE, HP Comware);
- ``profile_requires``: what the version output must also show for the family's collection
  profile to apply (the major version it was verified on);
- ``supersedes``: families whose signature the same output also carries (an IOS-XE
  version output has an IOS Software line);
- ``discovery_profile``: the platform's LLDP-only discovery profile for the family;
- ``global_instance``: the name the family gives its global (default) routing instance.
"""

import re
from functools import lru_cache
from pathlib import Path

import yaml

STYLES = frozenset({"display", "show", "unknown"})
OPTIONAL = frozenset(
    {
        "cli",
        "vendor_pattern",
        "profile_requires",
        "supersedes",
        "discovery_profile",
        "global_instance",
    }
)


@lru_cache(maxsize=1)
def signatures():
    """((row, compiled version pattern), ...) in file order."""
    data = yaml.safe_load(Path(__file__).with_name("signatures.yaml").read_text(encoding="utf-8"))
    if data["schema_version"] != 1:
        raise ValueError("unknown signature schema")
    rows, seen = [], set()
    for row in data["families"]:
        if (
            row["id"] in seen
            or row["command_style"] not in STYLES
            or set(row) - {"id", "vendor", "profile_id", "command_style", "pattern"} - OPTIONAL
            or not isinstance(row.get("supersedes", []), list)
        ):
            raise ValueError("invalid platform signature")
        for key in ("vendor_pattern", "profile_requires"):
            if key in row:
                re.compile(row[key])
        seen.add(row["id"])
        rows.append((row, re.compile(row["pattern"], re.I | re.M | re.ASCII)))
    known = {row["id"] for row, _ in rows}
    if any(set(row.get("supersedes", [])) - known for row, _ in rows):
        raise ValueError("a signature supersedes an unknown family")
    return tuple(rows)


def row(family):
    return next((r for r, _ in signatures() if r["id"] == family), None)


def cli(family):
    """The CLI dialect a family speaks, or None (unknown or not drivable)."""
    found = row(family)
    return found.get("cli") if found else None


def members(dialect):
    """The families that speak one CLI dialect."""
    return frozenset(r["id"] for r, _ in signatures() if r.get("cli") == dialect)


def profile_ids(dialect):
    """The identity profiles of the families that speak one dialect."""
    return frozenset(
        r["profile_id"] for r, _ in signatures() if r.get("cli") == dialect and r["profile_id"]
    )


def version_command(dialect):
    """The identification probe of a dialect: its command style's "version" command."""
    styles = {r["command_style"] for r, _ in signatures() if r.get("cli") == dialect}
    if len(styles) != 1:
        raise ValueError("a dialect needs exactly one command style")
    return f"{styles.pop()} version"


def discovery_profile(family):
    found = row(family)
    return found.get("discovery_profile") if found else None


def global_instance(family):
    found = row(family)
    return found.get("global_instance") if found else None


def discovery_families():
    """{LLDP discovery profile: family}, for observations that only name the profile."""
    return {r["discovery_profile"]: r["id"] for r, _ in signatures() if r.get("discovery_profile")}

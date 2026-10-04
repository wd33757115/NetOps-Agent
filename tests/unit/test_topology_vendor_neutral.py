"""Topology code names no device family (owner decision 2026-10-04).

What a family is and how it differs is data: collectors/signatures.yaml (identification,
CLI dialect, vendor names, verified versions), collectors/ifnames.yaml (interface names),
collection profiles, and rule governance's check items and implementations. A string in
code equal to a family or vendor name is a branch on the family and fails here, except the
listed ones, each tied to an open item of the branch list; the list may only shrink.
"""

import ast
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src/apps/topology/backend"
NAMES = frozenset(
    {"comware", "ios", "iosxe", "nxos", "vrp", "junos", "eos", "ruijie"}
    | {"h3c", "hpe", "cisco", "huawei", "juniper", "arista"}
)
# Open items (docs/topology-rule-consumption.md, vendor branch list).
ALLOWED = {
    # #12: NX-OS still runs on the generic tier until rule governance serves it.
    "collectors/collect.py": {"nxos": 1},
    "collectors/tiers/generic_vrfs.py": {"nxos": 1},
    # #2/#6: ARP entry type and aging unit.
    "collectors/rules.py": {"comware": 1, "ios": 1, "iosxe": 1},
    # #3: OSPF interface table structure.
    "collectors/rules_tables.py": {"comware": 1},
    # #1: Comware ARP instances inferred from bindings.
    "model/overlay.py": {"comware": 1},
}


def family_literals(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return Counter(
        node.value.casefold()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and node.value.casefold() in NAMES
    )


def test_topology_code_names_no_family_beyond_the_open_items():
    found = {}
    for path in sorted(ROOT.rglob("*.py")):
        if literals := family_literals(path):
            found[path.relative_to(ROOT).as_posix()] = dict(literals)
    assert found == ALLOWED

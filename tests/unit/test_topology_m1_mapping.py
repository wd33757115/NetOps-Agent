"""M1 model baseline guards: producers emit standard nodes only, the field mapping points at
real nodes, and every locked module has a decision.

Uses the committed M1 artifacts (models/network-twin/m1) and the campus-wan fixtures; no
YANG tool, network or container is needed.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
M1 = ROOT / "models/network-twin/m1"
sys.path.insert(0, str(ROOT / "scripts/topology"))

import m1_mapping  # noqa: E402


@pytest.fixture(scope="module")
def nodes():
    return m1_mapping.inventory()


@pytest.fixture(scope="module")
def app_paths():
    found = {}
    for name, tree in m1_mapping.app_trees():
        for path in m1_mapping.emitted(tree):
            found.setdefault(path, name)
    return found


def test_every_path_the_app_emits_is_a_baseline_node(nodes, app_paths):
    assert app_paths, "the campus-wan fixtures produce no output"
    outside = {path: producer for path, producer in app_paths.items() if path not in nodes}
    assert outside == {}


def test_every_mapped_field_points_at_a_baseline_node(nodes):
    mapping = json.loads((M1 / "fact-mapping.json").read_text(encoding="utf-8"))
    statuses = set(mapping["statuses"])
    for capability, fields in mapping["facts"].items():
        for field, spec in fields.items():
            assert spec["status"] in statuses, (capability, field)
            if spec["status"] == "no-native-node":
                assert "path" not in spec and spec.get("note"), (capability, field)
            else:
                assert spec["path"] in nodes, (capability, field, spec["path"])


def test_mapping_marks_what_the_app_already_exports(nodes, app_paths):
    mapping = json.loads((M1 / "fact-mapping.json").read_text(encoding="utf-8"))
    exported = {
        f"{capability}.{field}"
        for capability, fields in mapping["facts"].items()
        for field, spec in fields.items()
        if spec.get("path") in app_paths and spec["status"] in ("native", "derived")
    }
    # Collected and emitted today; a regression in a projector shows up here.
    for field in ("interfaces.ifname", "ip.address", "arp.mac", "vrrp.state", "lag.members"):
        assert field in exported


def test_every_locked_and_catalog_module_has_a_decision():
    lock = json.loads((M1 / "sources.lock.json").read_text(encoding="utf-8"))
    decisions = json.loads((M1 / "decisions.json").read_text(encoding="utf-8"))["decisions"]
    decided = {d["module"]: d for d in decisions}
    assert {e["module"] for e in lock["modules"]} <= set(decided)
    assert {d["module"] for d in decisions if d["decision"] == "unresolved"} == set()
    revisions = {r["file"] for d in decisions for r in d.get("revisions", [])}
    assert {e["file"] for e in lock["modules"]} == revisions

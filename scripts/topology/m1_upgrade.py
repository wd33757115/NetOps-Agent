"""M1-8: what a draft upgrade does to the model and to stored snapshots.

An upgrade is a module whose candidate-upgrade revision is newer than its runtime-baseline
revision (decisions.json), e.g. ietf-vrrp RFC 8347 -> draft-ietf-rtgwg-vrrp-rfc8347bis.
1. Node diff: the module's data nodes in the baseline and the candidate inventories
   (m1_sets.py) - removed, added, and changed constraints.
2. Replay: every stored snapshot (the M1-6 positive cases and the RFC 9195 files) whose
   schema closure changes is validated again, in libyang operational mode, against the
   candidate revisions. A snapshot that passed and now fails is a breaking change for
   stored data: the upgrade needs a conversion before it is adopted.

Run: venv/Scripts/python.exe scripts/topology/m1_upgrade.py
Output: models/network-twin/m1/reports/upgrade.json
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts/topology")]
from m1_cases import (  # noqa: E402
    CASES,
    MODULES,
    classify,
    closure,
    revisions,
    used_modules,
    yanglint,
)

M1 = ROOT / "models/network-twin/m1"
REPORT = M1 / "reports/upgrade.json"
COMPARED = (
    "kind",
    "config",
    "type",
    "base",
    "mandatory",
    "key",
    "units",
    "default",
    "range",
    "length",
    "pattern",
    "enum",
    "bits",
    "identityref",
    "leafref",
    "union",
    "when",
    "must",
    "if-feature",
    "status",
    "min-elements",
    "max-elements",
    "presence",
)
BOTH = ("runtime-baseline", "candidate-upgrade")


def upgrades() -> dict[str, dict]:
    decisions = json.loads((M1 / "decisions.json").read_text(encoding="utf-8"))["decisions"]
    found = {}
    for d in decisions:
        revs = d.get("revisions", [])
        base = [r for r in revs if r["decision"] == "runtime-baseline"]
        cand = [r for r in revs if r["decision"] == "candidate-upgrade"]
        if base and cand and max(r["file"] for r in cand) > max(r["file"] for r in base):
            found[d["module"]] = {
                "baseline": max(r["file"] for r in base),
                "candidate": max(r["file"] for r in cand),
                "candidate_from": max(cand, key=lambda r: r["file"])["defined_by"],
            }
    return found


def nodes(candidate: bool, modules: set[str]) -> dict[str, dict]:
    spec = json.loads((M1 / "sets.json").read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for set_id in spec["sets"]:
        path = M1 / "inventory" / f"{set_id}.candidate.jsonl.gz"
        if not candidate or not path.exists():
            path = M1 / "inventory" / f"{set_id}.jsonl.gz"
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                node = json.loads(line)
                if node["module"] in modules:
                    out.setdefault(node["path"], node)
    return out


def node_diff(module: str, before: dict, after: dict) -> dict:
    old = {p: n for p, n in before.items() if n["module"] == module}
    new = {p: n for p, n in after.items() if n["module"] == module}
    changed = []
    for path in sorted(set(old) & set(new)):
        delta = {
            k: [old[path].get(k), new[path].get(k)]
            for k in COMPARED
            if old[path].get(k) != new[path].get(k)
        }
        if delta:
            changed.append({"path": path, "changes": delta})
    return {
        "nodes_before": len(old),
        "nodes_after": len(new),
        "removed": sorted(set(old) - set(new)),
        "added": sorted(set(new) - set(old)),
        "changed": changed,
    }


def identity_diff(old: str, new: str) -> dict:
    """Identities a stored value may name (identityref) that the upgrade removes or adds."""

    def identities(file):
        text = (MODULES / file).read_text(encoding="utf-8")
        return set(re.findall(r"^\s*identity\s+([\w.-]+)", text, re.M))

    before, after = identities(old), identities(new)
    return {
        "identities_removed": sorted(before - after),
        "identities_added": sorted(after - before),
    }


def snapshots() -> list[tuple[str, dict]]:
    manifest = json.loads((CASES / "manifest.json").read_text(encoding="utf-8"))
    out = []
    for case in manifest["cases"]:
        if case["kind"] == "instance" and case["mode"] == "data" and case["expect"] == "pass":
            out.append((case["name"], json.loads((CASES / case["data"]).read_text("utf-8"))))
        elif case["kind"] == "instance-data" and case["expect"] == "pass":
            doc = json.loads((CASES / case["data"]).read_text("utf-8"))
            out.append(
                (case["name"], doc["ietf-yang-instance-data:instance-data-set"]["content-data"])
            )
    return out


def roots(tree: dict, by_name: dict, decided: dict, allowed: tuple) -> list[str]:
    files = []
    for name in sorted(used_modules(tree)):
        entries = [e for e in by_name.get(name, []) if decided.get(e["file"]) in allowed]
        if entries:
            files.append(max(entries, key=lambda e: e["revision"])["file"])
    return files


def main() -> int:
    found = upgrades()
    before, after = nodes(False, set(found)), nodes(True, set(found))
    diffs = {
        m: {**u, **node_diff(m, before, after), **identity_diff(u["baseline"], u["candidate"])}
        for m, u in sorted(found.items())
    }
    by_name, decided = revisions()
    replay = []
    for name, tree in snapshots():
        old = closure(roots(tree, by_name, decided, BOTH[:1]), by_name, decided, BOTH[:1])
        new = closure(roots(tree, by_name, decided, BOTH), by_name, decided, BOTH)
        if old == new:
            replay.append({"snapshot": name, "affected": False})
            continue
        (CASES / "_replay.json").write_text(json.dumps(tree), encoding="utf-8")
        try:
            run = yanglint(new, "_replay.json", "data")
        finally:
            (CASES / "_replay.json").unlink()
        kinds = sorted({"/".join(classify(e)) for e in run["errors"]})
        replay.append(
            {
                "snapshot": name,
                "affected": True,
                "revisions_changed": sorted(set(new) - set(old)),
                "passes_after_upgrade": not run["errors"],
                "found": kinds,
                "errors": run["errors"][:20],
            }
        )
    report = {
        "scope": "M1-8: candidate revisions newer than the baseline, node diff and snapshot replay",
        "upgrades": diffs,
        "replay": replay,
        "breaking": [
            r["snapshot"] for r in replay if r.get("affected") and not r["passes_after_upgrade"]
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for m, d in diffs.items():
        print(
            f"{m:28} {d['baseline'][len(m) + 1 : -5]} -> {d['candidate'][len(m) + 1 : -5]} "
            f"removed={len(d['removed'])} added={len(d['added'])} changed={len(d['changed'])} "
            f"identities -{len(d['identities_removed'])} +{len(d['identities_added'])}"
        )
    for r in replay:
        if r["affected"]:
            print(f"replay {r['snapshot']:40} pass={r['passes_after_upgrade']} {r['found']}")
    print("breaking:", report["breaking"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

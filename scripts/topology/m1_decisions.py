"""M1-2: decide, for every catalog module and locked dependency, how it is used.

Decisions (implementation plan §8 M1), made per locked revision and summarised per module:
- runtime-baseline: the newest revision from a published, non-obsoleted RFC (an IANA
  registry file that references an RFC, IANA-maintained modules included), an IEEE
  published module, or a draft the plan fixes as baseline (SIMAP -03, IVY, GREEN, BGP);
- candidate-upgrade: a revision from an active Internet-Draft (incl. the RFC Editor
  queue) that is newer than the baseline revision, e.g. a draft updating an RFC module;
- reference: superseded revisions, obsoleted RFCs, expired/replaced drafts and SMIv2 MIBs
  (mapping basis, not YANG capability, plan §9);
- not-applicable: only with a written reason (example modules, authoring templates).
A dependency revision takes the strongest decision of the revisions that import it.
The decision is about how a module enters the schema sets; it never edits a module.

Run after m1_sources.py. Output: models/network-twin/m1/decisions.json and decisions.md.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "docs/network-digital-twin-ietf-model-catalog.json"
LOCK = ROOT / "models/network-twin/m1/sources.lock.json"
OUT = ROOT / "models/network-twin/m1/decisions.json"
REPORT = ROOT / "models/network-twin/m1/decisions.md"
# Drafts the implementation plan fixes as the running baseline (§3 table, M2, M4).
ADOPTED_DRAFTS = {
    "draft-havel-nmop-simap-yang-03": "SIMAP YANG -03, the owner-adopted 8345bis baseline",
    "draft-ietf-ivy-network-inventory-yang-20": "IVY base inventory, plan §3/M2",
    "draft-ietf-ivy-network-inventory-software-04": "IVY software inventory, plan §3/M2",
    "draft-ietf-ivy-network-inventory-topology-11": "IVY topology mapping, plan §3/M2",
    "draft-ietf-ivy-network-inventory-location-06": "IVY location, plan §3/M2",
    "draft-ietf-ivy-passive-network-inventory-01": "IVY passive inventory, plan §3/M2",
    "draft-ietf-ivy-entitlement-inventory-05": "IVY entitlement, plan §3/M2",
    "draft-ietf-green-power-and-energy-yang-04": "GREEN power and energy, plan §3/M2",
    "draft-ietf-idr-bgp-model-21": "BGP model, fixed draft per plan M4",
}
NOT_APPLICABLE = {
    "ietf-template": "RFC 9907 authoring template; placeholder revision dates by design",
    "iana-template": "RFC 9907 authoring template; placeholder revision dates by design",
}
MIB = re.compile(r"^[A-Z][A-Za-z0-9-]*-MIB$")
RANK = {"runtime-baseline": 0, "candidate-upgrade": 1, "reference": 2, "not-applicable": 3}


def doc_state(doc: dict) -> str:
    status = doc.get("status") or ""
    if doc["id"].startswith("RFC"):
        return "rfc-obsoleted" if doc.get("obsoleted_by") else "rfc"
    if doc["id"] in ADOPTED_DRAFTS:
        return "draft-adopted"
    if re.search(r"Expired|Replaced|过期|取代", status):
        return "draft-expired"
    return "draft-active"


def revision_state(entry: dict, docs: dict) -> tuple[str, str | None]:
    """(state, defining document) of one locked revision."""
    if entry.get("document"):
        doc = docs.get(entry["document"])
        return (doc_state(doc) if doc else "draft-active"), entry["document"]
    if entry["origin"] == "iana-registry":
        rfcs = [r.upper() for r in entry.get("references", []) if re.fullmatch(r"(?i)rfc\d+", r)]
        listed = [r for r in rfcs if r in docs]
        if listed:
            return doc_state(docs[listed[0]]), listed[0]
        if rfcs or entry.get("maintained_by_iana"):
            return "rfc", rfcs[0] if rfcs else "IANA"
        return "draft-active", None
    if "ieee-published" in entry["required_by"] or "/standard/ieee/published/" in entry["url"]:
        return "ieee", None
    return "dependency", None


def main() -> int:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    docs = {doc["id"]: doc for doc in catalog["documents"]}
    by_module = defaultdict(list)
    for doc in catalog["documents"]:
        for name in doc.get("modules") or []:
            if doc not in by_module[name]:
                by_module[name].append(doc)
    revisions = defaultdict(list)
    for entry in lock["modules"]:
        revisions[entry["module"]].append(entry)

    # 1. Per revision.
    per_file: dict[str, dict] = {}
    for name, entries in revisions.items():
        entries.sort(key=lambda e: e["revision"])
        states = {e["file"]: revision_state(e, docs) for e in entries}
        eligible = [
            e
            for e in entries
            if states[e["file"]][0] in ("rfc", "ieee", "draft-adopted")
            and e["revision"] != "unknown"
        ]
        baseline = eligible[-1]["revision"] if eligible else ""
        for e in entries:
            state, basis = states[e["file"]]
            if name in NOT_APPLICABLE or name.startswith("example"):
                decision = "not-applicable"
                reason = NOT_APPLICABLE.get(name, "example module printed in a document")
            elif state == "dependency":
                decision, reason = None, "dependency only"  # settled in step 2
            elif state in ("rfc", "ieee", "draft-adopted") and e["revision"] == baseline:
                decision = "runtime-baseline"
                reason = {
                    "rfc": "published RFC, newest published revision",
                    "ieee": "IEEE published module (YangModels standard/ieee/published)",
                    "draft-adopted": "fixed draft: " + ADOPTED_DRAFTS.get(basis or "", ""),
                }[state]
            elif state == "draft-active" and e["revision"] > baseline:
                decision = "candidate-upgrade"
                reason = (
                    "active Internet-Draft revision newer than the published one"
                    if baseline
                    else "active Internet-Draft; enters a set only after review"
                )
            elif state in ("rfc", "ieee", "draft-adopted", "draft-active"):
                decision, reason = "reference", f"superseded by revision {baseline}"
            elif state == "draft-expired":
                decision, reason = "reference", "expired or replaced Internet-Draft"
            else:
                decision, reason = "reference", "only defined by obsoleted RFCs"
            per_file[e["file"]] = {
                "file": e["file"],
                "module": name,
                "revision": e["revision"],
                "decision": decision,
                "reason": reason,
                "defined_by": basis,
                "origin": e["origin"],
                **({"patch": e["patch"]} if e.get("patch") else {}),
            }

    # 2. Dependency-only revisions take the strongest decision of their importers.
    importers = defaultdict(set)
    for entry in lock["modules"]:
        for by in entry["required_by"]:
            if by.endswith(".yang"):
                importers[entry["file"]].add(by)
    changed = True
    while changed:
        changed = False
        for file, r in per_file.items():
            if r["reason"] != "dependency only" and not r["reason"].startswith("dependency of"):
                continue
            users = [per_file[u]["decision"] for u in importers[file] if per_file[u]["decision"]]
            if not users:
                continue
            best = min(users, key=lambda d: RANK[d])
            if r["decision"] != best:
                r["decision"] = best
                r["reason"] = "dependency of " + ", ".join(sorted(importers[file])[:6])
                changed = True
        # A baseline revision may import a module that has no published revision yet (the
        # adopted BGP draft imports ietf-yang-semver): the newest such revision is required.
        for file, r in per_file.items():
            module_revs = [per_file[e["file"]] for e in revisions[r["module"]]]
            if any(x["decision"] == "runtime-baseline" for x in module_revs) or r["decision"] in (
                None,
                "not-applicable",
            ):
                continue
            if r["revision"] != max(x["revision"] for x in module_revs):
                continue
            users = sorted(
                u for u in importers[file] if per_file[u]["decision"] == "runtime-baseline"
            )
            if users:
                r["decision"] = "runtime-baseline"
                r["reason"] = (
                    f"{r['reason']}; required by baseline " + ", ".join(users[:6])
                    if "required by baseline" not in r["reason"]
                    else r["reason"]
                )
                changed = True

    # 3. Per module.
    decisions = {}
    for name in sorted(set(by_module) | set(revisions)):
        defining = by_module.get(name, [])
        common = {
            "module": name,
            "families": sorted({d["family"] for d in defining}),
            "documents": sorted(d["id"] for d in defining),
        }
        if MIB.match(name):
            decisions[name] = {
                **common,
                "decision": "reference",
                "reason": "SMIv2 MIB: mapping basis, not a YANG capability (plan §9)",
                "kind": "smi",
                "file": None,
            }
            continue
        revs = [per_file[e["file"]] for e in revisions.get(name, [])]
        if not revs:
            decisions[name] = {
                **common,
                "decision": "unresolved",
                "reason": "source not found in IANA, its document or the pinned YangModels commit",
                "file": None,
            }
            continue
        best = min(revs, key=lambda r: (RANK.get(r["decision"] or "", 9), r["revision"] == ""))
        chosen = max(
            (r for r in revs if r["decision"] == best["decision"]), key=lambda r: r["revision"]
        )
        decisions[name] = {
            **common,
            "decision": chosen["decision"] or "unresolved",
            "reason": chosen["reason"],
            "file": chosen["file"],
            "defined_by": chosen["defined_by"],
            **({"standard_body": "IEEE"} if chosen["reason"].startswith("IEEE") else {}),
            **({"dependency_only": True} if not defining else {}),
            "revisions": [
                {k: r[k] for k in ("file", "decision", "reason", "defined_by")}
                for r in sorted(revs, key=lambda r: r["revision"])
            ],
        }

    counts = Counter(d["decision"] for d in decisions.values())
    revision_counts = Counter(r["decision"] for r in per_file.values())
    result = {
        "scope": "M1 module decisions; how each module enters schema sets, not a capability claim",
        "rules": __doc__.split("Decisions (implementation plan §8 M1)", 1)[1]
        .split("The decision", 1)[0]
        .split(":", 1)[1]
        .strip(),
        "adopted_drafts": ADOPTED_DRAFTS,
        "counts": dict(sorted(counts.items())),
        "revision_counts": dict(sorted(revision_counts.items())),
        "decisions": [decisions[k] for k in sorted(decisions)],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    lines = [
        "# M1 模块去留判定",
        "",
        "由 `scripts/topology/m1_decisions.py` 从目录与来源锁生成；规则见 decisions.json。",
        "按修订版判定后汇总到模块：同一模块的已发布修订为运行基线，更新它的在审草案修订为"
        "候选升级，被取代的修订为参考。",
        "",
        "| 判定（模块） | 数量 |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in sorted(counts.items())],
        "",
        "| 判定（修订版文件） | 数量 |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in sorted(revision_counts.items())],
        "",
    ]
    by_family = defaultdict(list)
    for d in result["decisions"]:
        by_family[(d.get("families") or ["（仅作依赖）"])[0]].append(d)
    for family in sorted(by_family):
        lines += [f"## {family}", "", "| 模块 | 判定 | 依据 | 理由 |", "|---|---|---|---|"]
        for d in sorted(by_family[family], key=lambda x: x["module"]):
            basis = d.get("defined_by") or (d.get("file") or "")
            extra = [
                f"{r['file']}：{r['decision']}"
                for r in d.get("revisions", [])
                if r["file"] != d.get("file")
            ]
            reason = d["reason"] + (f"（另有 {'；'.join(extra)}）" if extra else "")
            lines.append(f"| `{d['module']}` | {d['decision']} | {basis} | {reason} |")
        lines.append("")
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(result["counts"]), json.dumps(result["revision_counts"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

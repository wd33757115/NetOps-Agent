"""M1-5: the data node mapping inventory.

Joins three things:
- the data nodes of the compiled baseline schema sets (m1_sets.py inventories);
- every field the platform parsers collect (models/network-twin/m1/fact-mapping.json);
- the paths current producers emit, as RFC 7951 member paths without keys:
  app      - the topology App's SIMAP builder and device projections, run on the
             campus-wan fixtures through the same parsers as discovery (no device access);
  research - the offline lab instances in models/network-twin/instances.
An emitted path that is not a node of any baseline set is a schema error of the producer.
For every emitted list/container the key and mandatory children that are not emitted are
listed: the instance is then a partial native instance, not a complete one.

Run: venv/Scripts/python.exe scripts/topology/m1_mapping.py
Output: models/network-twin/m1/mapping.json and mapping.md
"""

from __future__ import annotations

import gzip
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests/unit")]

import test_topology_campus_wan as campus  # noqa: E402  fixture loading shared with the tests

from src.apps.topology.backend.model.builder import build_topology  # noqa: E402
from src.apps.topology.backend.model.device_control import project_device_control  # noqa: E402
from src.apps.topology.backend.model.device_ip import enrich_device_ip  # noqa: E402
from src.apps.topology.backend.model.device_state import project_device_state  # noqa: E402

M1 = ROOT / "models/network-twin/m1"
INSTANCES = ROOT / "models/network-twin/instances"
CONTAINERS = {"container", "list"}


def inventory() -> dict[str, dict]:
    """path -> node (with the baseline sets that contain it); data tree only."""
    nodes: dict[str, dict] = {}
    spec = json.loads((M1 / "sets.json").read_text(encoding="utf-8"))
    for set_id in spec["sets"]:
        with gzip.open(M1 / "inventory" / f"{set_id}.jsonl.gz", "rt", encoding="utf-8") as f:
            for line in f:
                node = json.loads(line)
                if node.get("operation"):
                    continue
                nodes.setdefault(node["path"], {**node, "sets": []})["sets"].append(set_id)
    return nodes


def emitted(tree: dict) -> dict[str, str]:
    """{path: json kind} of an RFC 7951 tree; member names keep their qualification."""
    found: dict[str, str] = {}

    def walk(value, path):
        if isinstance(value, dict):
            for key, child in value.items():
                if not key.startswith("@"):
                    walk_member(child, f"{path}/{key}")
        elif isinstance(value, list):
            for item in value:
                walk(item, path)

    def walk_member(value, path):
        if isinstance(value, dict):
            found[path] = "container"
            walk(value, path)
        elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            found[path] = "list"
            walk(value, path)
        elif isinstance(value, list) and value != [None]:
            found[path] = "leaf-list"
        else:
            found[path] = "leaf"

    walk(tree, "")
    return found


def app_trees() -> list[tuple[str, dict]]:
    trees, devices = [], {}
    for device, spec in campus.EXPECTED["devices"].items():
        identity = campus.IDENTITY[spec["platform"]]
        outcome = campus.topology_observation(
            SimpleNamespace(
                parsed=campus.parsed(device),
                identity=identity,
                profile_id=identity.profile_id,
                mode="topology",
                driver="paramiko",
            )
        )
        devices[spec["management"]] = {**outcome["facts"], "platform": spec["platform"]}
        observation = {
            "data": {
                "facts": outcome["facts"],
                "capabilities": outcome["capabilities"],
                "fact_metadata": outcome["fact_metadata"],
            }
        }
        state = enrich_device_ip(project_device_state(observation), observation)
        trees.append((f"app:device-state:{device}", state["tree"]))
        control = project_device_control(observation)
        for rib in control["routing"]:
            trees.append((f"app:device-routing:{device}:{rib.get('vrf')}", rib["tree"]))
        trees.append((f"app:device-lldp:{device}", control["lldp"]["tree"]))
    tree, _ = build_topology(devices, "urn:netops:topology:campus:")
    trees.append(("app:topology", tree))
    return trees


def research_trees() -> list[tuple[str, dict]]:
    return [
        (f"research:{path.name}", json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(INSTANCES.glob("*.json"))
        if path.name != "manifest.json"
    ]


def missing_children(path: str, nodes: dict, emitted_paths: set[str]) -> list[dict]:
    """Key and mandatory descendants an emitted list/container still needs.

    Direct children, and the children of non-presence containers that are not emitted
    (their mandatory leaves are required as soon as the parent exists). Nodes inside a
    choice depend on the selected case and are left out; when-conditioned ones are flagged.
    """
    node = nodes.get(path)
    if node is None:
        return []
    keys = (node.get("key") or "").split()
    gaps = []
    for child_path, child in nodes.items():
        if not child_path.startswith(path + "/") or "/" in child_path[len(path) + 1 :]:
            continue
        if child_path in emitted_paths or child.get("choice"):
            continue
        name = child_path.rsplit("/", 1)[1].split(":")[-1]
        if child["kind"] == "container" and not child.get("presence"):
            for gap in missing_children(child_path, nodes, emitted_paths):
                for key in ("when", "if-feature"):  # the container's conditions apply too
                    if child.get(key):
                        gap[key] = child[key] + gap.get(key, [])
                gaps.append(gap)
            continue
        required = (
            name in keys
            or child.get("mandatory") == "true"
            or int(child.get("min-elements", "0")) > 0
        )
        if required:
            gap = {"path": child_path, "why": "key" if name in keys else "mandatory"}
            if child.get("when"):
                gap["when"] = child["when"]
            if child.get("if-feature"):
                gap["if-feature"] = child["if-feature"]
            if child.get("config") is False:
                gap["state"] = True
            gaps.append(gap)
    return gaps


def main() -> int:
    nodes = inventory()
    mapping = json.loads((M1 / "fact-mapping.json").read_text(encoding="utf-8"))
    producers = {}
    by_group: dict[str, set[str]] = defaultdict(set)
    for name, tree in app_trees() + research_trees():
        paths = emitted(tree)
        unknown = sorted(p for p in paths if p not in nodes)
        producers[name] = {"paths": len(paths), "not_in_schema": unknown}
        by_group[name.split(":")[0]] |= set(paths)

    facts, counts = {}, Counter()
    for capability, fields in mapping["facts"].items():
        for field, spec in fields.items():
            path = spec.get("path")
            row = {**spec}
            if path:
                row["in_schema_sets"] = nodes[path]["sets"] if path in nodes else []
                row["emitted_by"] = [g for g in ("app", "research") if path in by_group[g]]
            if path and path not in nodes:
                verdict = "mapping-error: path not in any baseline set"
            elif spec["status"] in ("native", "derived") and "app" in row.get("emitted_by", []):
                verdict = "exported"
            elif spec["status"] in ("native", "derived"):
                verdict = "mappable-not-exported"
            else:
                verdict = spec["status"]
            row["verdict"] = verdict
            counts[verdict] += 1
            facts[f"{capability}.{field}"] = row

    partial = {}
    for group in ("app", "research"):
        emitted_paths = by_group[group]
        for path in sorted(emitted_paths):
            if nodes.get(path, {}).get("kind") in CONTAINERS:
                gaps = missing_children(path, nodes, emitted_paths)
                if gaps:
                    partial.setdefault(group, {})[path] = gaps

    modules: dict[str, Counter] = defaultdict(Counter)
    for path, node in nodes.items():
        m = modules[node["module"]]
        m["nodes"] += 1
        m["config"] += node.get("config") is True
        m["state"] += node.get("config") is False
        m["app"] += path in by_group["app"]
        m["research"] += path in by_group["research"]
    sets_total = Counter(s for node in nodes.values() for s in node["sets"])

    result = {
        "scope": "M1-5 mapping inventory: baseline data nodes x collected fields x emitted paths",
        "baseline_data_nodes": len(nodes),
        "baseline_data_nodes_by_set": dict(sorted(sets_total.items())),
        "emitted": {g: len(p) for g, p in sorted(by_group.items())},
        "producers": producers,
        "fact_verdicts": dict(sorted(counts.items())),
        "facts": facts,
        "partial_instances": partial,
        "modules": {
            m: dict(c) for m, c in sorted(modules.items(), key=lambda kv: (-kv[1]["app"], kv[0]))
        },
    }
    (M1 / "mapping.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    write_report(result)
    print(json.dumps({"emitted": result["emitted"], "facts": result["fact_verdicts"]}))
    errors = [p for p in producers.values() if p["not_in_schema"]]
    errors += [f for f in facts.values() if f["verdict"].startswith("mapping-error")]
    return 1 if errors else 0


def write_report(result: dict) -> None:
    lines = [
        "# M1 数据节点映射清单",
        "",
        "由 `scripts/topology/m1_mapping.py` 生成，明细见 mapping.json。基线数据节点"
        f" {result['baseline_data_nodes']} 个（去重，不含 RPC/通知内容）；当前 App 生产者"
        f"实际输出 {result['emitted'].get('app', 0)} 个路径，研究实例 "
        f"{result['emitted'].get('research', 0)} 个。",
        "",
        "## 生产者输出与 Schema",
        "",
        "| 生产者 | 输出路径 | 不在任何基线集中的路径 |",
        "|---|---|---|",
    ]
    for name, p in result["producers"].items():
        bad = "；".join(p["not_in_schema"][:5]) or "无"
        lines.append(f"| {name} | {p['paths']} | {bad} |")
    lines += [
        "",
        "## 已采集字段的落点",
        "",
        "| 结论 | 数量 |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in result["fact_verdicts"].items()],
        "",
        "| 字段 | 结论 | 标准节点 | 说明 |",
        "|---|---|---|---|",
    ]
    for name, f in result["facts"].items():
        note = f.get("note", "").replace("|", "/")
        lines.append(f"| `{name}` | {f['verdict']} | `{f.get('path', '')}` | {note} |")
    lines += ["", "## 部分实例：已输出容器/列表下缺少的键与必选子节点", ""]
    for group, paths in result["partial_instances"].items():
        lines += [f"### {group}", "", "| 已输出 | 缺少 | 原因 |", "|---|---|---|"]
        for path, gaps in paths.items():
            for gap in gaps:
                why = gap["why"] + ("（有 when 条件）" if gap.get("when") else "")
                why += "（有 if-feature）" if gap.get("if-feature") else ""
                lines.append(f"| `{path}` | `{gap['path'][len(path) + 1 :]}` | {why} |")
        lines.append("")
    lines += [
        "## 模块覆盖（按 App 已输出节点数）",
        "",
        "| 模块 | 数据节点 | 配置 | 状态 | App 输出 | 研究实例 |",
        "|---|---|---|---|---|---|",
    ]
    for module, c in result["modules"].items():
        if c.get("app") or c.get("research"):
            lines.append(
                f"| `{module}` | {c['nodes']} | {c.get('config', 0)} | {c.get('state', 0)} "
                f"| {c.get('app', 0)} | {c.get('research', 0)} |"
            )
    untouched = [m for m, c in result["modules"].items() if not c.get("app")]
    lines += [
        "",
        f"其余 {len(untouched)} 个基线模块尚无 App 输出节点（见 mapping.json 的 modules）。",
        "",
    ]
    (M1 / "mapping.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    raise SystemExit(main())

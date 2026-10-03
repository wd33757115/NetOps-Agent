"""M1-3/M1-4: compile the capability schema sets and inventory their data nodes.

For each set in models/network-twin/m1/sets.json, in two variants:
- baseline: runtime-baseline roots of the set's families/modules (decisions.json);
- candidate: the baseline plus the families' candidate-upgrade modules.
The closure follows import/include through the source lock: a pinned revision-date takes
that file, otherwise the newest locked revision. *-state modules mirroring an NMDA module
are kept out of every set except nmda-state-compat (RFC 8342 §5.3 compatibility).

pyang 2.7.1 validates the exact files with every feature enabled and its diagnostics are
grouped into the acceptance categories of the plan (missing dependency, wrong revision,
unknown identity, unresolved definition/target/reference, conflicts, syntax). yangson
1.6.3 then loads an RFC 7895 validation library of the same files. The data node
inventory (every container, list, leaf, leaf-list, anydata/anyxml, rpc/action/notification
and input/output) with its constraints comes from pyang's expanded schema tree.
Modules are never edited here; known source repairs come from the lock's reviewed patches.

Run with the isolated tool environment, after m1_sources.py and m1_decisions.py:
  .runtime/yang-model-tools/Scripts/python.exe scripts/topology/m1_sets.py [set ...]
Output: models/network-twin/m1/sets/<set>[.candidate].json, inventory/<set>.jsonl.gz and
.runtime/m1/sets/<set>/ (modules, library, RFC 8340 tree).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from m1_sources import dependencies
from pyang import context, error, repository
from yangson import DataModel

ROOT = Path(__file__).resolve().parents[2]
M1 = ROOT / "models/network-twin/m1"
MODULES = ROOT / ".runtime/m1/cache/modules"
WORK = ROOT / ".runtime/m1/sets"
PYANG = Path(sys.executable).with_name("pyang.exe")
# Independent second compiler: the libyang image built for the mount validation.
LIBYANG_IMAGE = "netops-ietf-mount-verify:5.8.6"
DATA = {"container", "list", "leaf", "leaf-list", "anydata", "anyxml"}
OPERATIONS = {"rpc", "action", "notification", "input", "output"}
CATEGORIES = {
    "missing-dependency": {"MODULE_NOT_FOUND", "MISSING_INCLUDE", "BAD_IMPORT", "BAD_INCLUDE"},
    "wrong-revision": {
        "MODULE_NOT_FOUND_REV",
        "BAD_REVISION",
        "WBAD_REVISION",
        "FILENAME_BAD_REVISION",
        "REVISION_ORDER",
    },
    "unknown-identity": {"IDENTITY_NOT_FOUND"},
    "unresolved-definition": {
        "TYPE_NOT_FOUND",
        "GROUPING_NOT_FOUND",
        "FEATURE_NOT_FOUND",
        "EXTENSION_NOT_DEFINED",
        "PREFIX_NOT_DEFINED",
        "WPREFIX_NOT_DEFINED",
        "MODULE_NOT_IMPORTED",
    },
    "unresolved-target": {
        "BAD_TARGET_NODE",
        "NODE_NOT_FOUND",
        "BAD_NODE_IN_AUGMENT",
        "BAD_NODE_IN_REFINE",
        "UNEXPECTED_KEYWORD_AUGMENT",
        "AUGMENT_MANDATORY",
    },
    "conflict": {
        "DUPLICATE_CHILD_NAME",
        "DUPLICATE_NAMESPACE",
        "DUPLICATE_KEY",
        "DUPLICATE_ENUM_NAME",
        "DUPLICATE_ENUM_VALUE",
        "DUPLICATE_BIT_POSITION",
        "DUPLICATE_DEFAULT",
        "DUPLICATE_UNIQUE",
        "IDENTITY_ALREADY_DEFINED",
        "TYPE_ALREADY_DEFINED",
        "GROUPING_ALREADY_DEFINED",
        "FEATURE_ALREADY_DEFINED",
        "EXTENSION_ALREADY_DEFINED",
        "PREFIX_ALREADY_USED",
        "MANDATORY_NODE_IN_DEFAULT_CASE",
        "DEFAULT_CASE_NOT_FOUND",
        "DEFAULT_AND_MANDATORY",
        "DEFAULT_AND_MIN_ELEMENTS",
        "CIRCULAR_DEPENDENCY",
    },
    "style": {"LONG_LINE", "LONG_IDENTIFIER", "UNUSED_IMPORT", "UNUSED_TYPEDEF", "UNUSED_GROUPING"},
}


def category(tag: str) -> str:
    if tag.startswith(("LEAFREF_", "XPATH_")):
        return "reference"
    if tag.startswith(
        ("SYNTAX", "EXPECTED_", "UNEXPECTED_", "EOF_", "ILLEGAL_ESCAPE", "INCOMPLETE_", "TRAILING_")
    ):
        return "syntax"
    return next((name for name, tags in CATEGORIES.items() if tag in tags), "other")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_state_compat(name: str, known: set[str]) -> bool:
    return name.endswith("-state") and name[: -len("-state")] in known


def compose(spec: dict, variant: str, decisions: dict, ieee: set[str]) -> set[str]:
    known = set(decisions)
    roots = set()
    for name, d in decisions.items():
        if d["decision"] not in allowed(variant) or d.get("kind") == "smi" or not d.get("file"):
            continue
        if spec.get("state_compat"):
            chosen = is_state_compat(name, known)
        elif spec.get("ieee"):
            chosen = name in ieee
        else:
            chosen = bool(set(d.get("families") or []) & set(spec.get("families", []))) or (
                name in spec.get("modules", [])
            )
            chosen = chosen and not is_state_compat(name, known)
        if chosen:
            roots.add(name)
    return roots


def allowed(variant: str) -> set[str]:
    return {"runtime-baseline"} | ({"candidate-upgrade"} if variant == "candidate" else set())


def closure(
    roots: set[str], lock: dict, revision_decision: dict[str, str], variant: str
) -> tuple[dict[str, bytes], list[dict]]:
    """{file: bytes} of the roots and everything they import/include; missing imports.

    An import without revision-date takes the newest revision the variant allows (the
    baseline never takes a candidate revision); a pinned revision-date takes that file.
    """
    by_name: dict[str, list[dict]] = {}
    for entry in lock["modules"]:
        by_name.setdefault(entry["module"], []).append(entry)
    files, missing, pending = {}, [], [(name, None, "root") for name in sorted(roots)]
    while pending:
        name, revision, by = pending.pop()
        entries = sorted(by_name.get(name, []), key=lambda e: e["revision"])
        if revision:
            entries = [e for e in entries if e["revision"] == revision]
        else:
            entries = [e for e in entries if revision_decision.get(e["file"]) in allowed(variant)]
        if not entries:
            missing.append({"module": name, "revision": revision, "required_by": by})
            continue
        file = entries[-1]["file"]
        if file in files:
            continue
        files[file] = (MODULES / file).read_bytes()
        if digest(files[file]) != entries[-1]["module_sha256"]:
            raise ValueError(f"{file} differs from the source lock")
        for dep, pinned, _ in dependencies(files[file]):
            pending.append((dep, pinned, file))
    return files, missing


def type_chain(stmt) -> list:
    """The leaf's type statement followed by those of the typedefs it derives from."""
    chain, t = [], stmt.search_one("type")
    while t is not None:
        chain.append(t)
        typedef = getattr(t, "i_typedef", None)
        t = typedef.search_one("type") if typedef is not None else None
    return chain


def type_info(stmt) -> dict:
    chain = type_chain(stmt)
    if not chain:
        return {}
    info = {"type": chain[0].arg, "base": chain[-1].arg}
    restrictions = {}
    for t in chain:
        for keyword in ("range", "length", "fraction-digits", "require-instance"):
            s = t.search_one(keyword)
            if s is not None:
                restrictions.setdefault(keyword, s.arg)
        patterns = [p.arg for p in t.search("pattern")]
        if patterns:
            restrictions.setdefault("pattern", patterns)
        path = t.search_one("path")
        if path is not None:
            restrictions.setdefault("leafref", path.arg)
        bases = [b.arg for b in t.search("base")]
        if bases:
            restrictions.setdefault("identityref", bases)
        enums = [e.arg for e in t.search("enum")]
        if enums:
            restrictions.setdefault("enum", enums)
        bits = [b.arg for b in t.search("bit")]
        if bits:
            restrictions.setdefault("bits", bits)
        members = [m.arg for m in t.search("type")]
        if members:
            restrictions.setdefault("union", members)
    for typedef in [getattr(t, "i_typedef", None) for t in chain]:
        if typedef is None:
            continue
        for keyword in ("units", "default"):
            s = typedef.search_one(keyword)
            if s is not None:
                restrictions.setdefault(keyword, s.arg)
    info.update(restrictions)
    return info


def conditions(stmt) -> dict:
    """when/if-feature of the node itself and of the uses/augment that brought it in."""
    when = [s.arg for s in stmt.search("when")]
    features = [s.arg for s in stmt.search("if-feature")]
    for uses in getattr(stmt, "i_uses", None) or []:
        when += [s.arg for s in uses.search("when")]
        features += [s.arg for s in uses.search("if-feature")]
    augment = getattr(stmt, "i_augment", None)
    if augment is not None:
        when += [s.arg for s in augment.search("when")]
        features += [s.arg for s in augment.search("if-feature")]
    result = {}
    if when:
        result["when"] = when
    if features:
        result["if-feature"] = features
    return result


def merge(first: dict | None, second: dict) -> dict:
    merged = {k: list(v) for k, v in (first or {}).items()}
    for key, values in second.items():
        merged[key] = merged.get(key, []) + values
    return merged


def node_record(stmt, choices: list[str], path: str) -> dict:
    record = {
        "path": path,
        "module": stmt.i_module.i_modulename,
        "kind": stmt.keyword,
    }
    config = getattr(stmt, "i_config", None)
    if stmt.keyword in DATA and config is not None:
        record["config"] = config
    for keyword in (
        "status",
        "mandatory",
        "presence",
        "key",
        "min-elements",
        "max-elements",
        "ordered-by",
        "units",
        "default",
    ):
        s = stmt.search_one(keyword)
        if s is not None:
            record[keyword] = s.arg if keyword != "presence" else True
    unique = [s.arg for s in stmt.search("unique")]
    if unique:
        record["unique"] = unique
    must = [s.arg for s in stmt.search("must")]
    if must:
        record["must"] = must
    if choices:
        record["choice"] = choices
    if stmt.keyword in ("leaf", "leaf-list"):
        info = type_info(stmt)
        for keyword in ("units", "default"):
            if keyword in info and keyword in record:
                info.pop(keyword)
        record.update(info)
    record.update(conditions(stmt))
    return record


def inventory(modules: list) -> list[dict]:
    nodes = []

    def walk(stmt, choices, path, module, operation=False, inherited=None):
        for child in getattr(stmt, "i_children", None) or []:
            # when/if-feature of a choice or case (and of the augment that added it) apply to
            # every data node inside it.
            if child.keyword == "choice":
                own = merge(inherited, conditions(child))
                walk(child, choices + [child.arg], path, module, operation, own)
            elif child.keyword == "case":
                own = merge(inherited, conditions(child))
                case = choices[:-1] + [f"{choices[-1]}/{child.arg}"]
                walk(child, case, path, module, operation, own)
            elif child.keyword in DATA or child.keyword in OPERATIONS:
                # RFC 7951 member names: qualified where the namespace changes; choice and
                # case never appear (pyang's mk_path_str keeps choice names).
                owner = child.i_module.i_modulename
                step = child.arg if owner == module else f"{owner}:{child.arg}"
                inside = operation or child.keyword in OPERATIONS
                nodes.append(node_record(child, choices, f"{path}/{step}"))
                for key, values in (inherited or {}).items():
                    nodes[-1][key] = values + nodes[-1].get(key, [])
                if inside:
                    nodes[-1]["operation"] = True  # rpc/action/notification content
                walk(child, [], f"{path}/{step}", owner, inside)

    for module in modules:
        walk(module, [], "", None)
    return nodes


def data_paths(node) -> set[str]:
    """yangson's data_path() of every data-tree node (operations and notifications aside)."""
    paths = set()
    for child in getattr(node, "children", None) or []:
        kind = type(child).__name__
        if kind in ("RpcActionNode", "NotificationNode"):
            continue
        if kind not in ("ChoiceNode", "CaseNode", "GroupNode"):
            paths.add(child.data_path())
        paths |= data_paths(child)
    return paths


def yangson_nodes(node) -> dict[str, int]:
    """Schema nodes yangson built, by class (schema_digest() fails on identityref defaults)."""
    counts: Counter = Counter()
    for child in getattr(node, "children", None) or []:
        counts[type(child).__name__] += 1
        counts.update(yangson_nodes(child))
    return dict(sorted(counts.items()))


def library(files: dict[str, bytes], ctx) -> dict:
    """RFC 7895 validation library: newest revision implemented, all features enabled."""
    newest: dict[str, str] = {}
    for file in files:
        name, revision = file[:-5].split("@")
        newest[name] = max(newest.get(name, ""), revision)
    entries = []
    for file in sorted(files):
        name, revision = file[:-5].split("@")
        module = ctx.get_module(name, revision)
        if module is None or module.keyword != "module":
            continue
        features = [f.arg for f in module.search("feature")]
        submodules = []
        for include in module.search("include"):
            sub = ctx.get_module(include.arg)
            if sub is not None:
                features += [f.arg for f in sub.search("feature")]
                submodules.append({"name": sub.arg, "revision": sub.i_latest_revision})
        entry = {
            "name": name,
            "revision": revision,
            "namespace": module.search_one("namespace").arg,
            "conformance-type": "implement" if newest[name] == revision else "import",
        }
        if features and entry["conformance-type"] == "implement":
            entry["feature"] = sorted(set(features))
        if submodules:
            entry["submodule"] = submodules
        entries.append(entry)
    return {"ietf-yang-library:modules-state": {"module-set-id": "m1", "module": entries}}


def build(set_id: str, spec: dict, variant: str, decisions: dict, lock: dict, ieee: set[str]):
    started = time.time()
    label = set_id if variant == "baseline" else f"{set_id}.candidate"
    roots = compose(spec, variant, decisions, ieee)
    files, missing = closure(roots, lock, revision_decisions(decisions), variant)
    known = set(decisions)
    leaked = sorted(
        f for f in files if is_state_compat(f.split("@")[0], known) and not spec.get("state_compat")
    )
    work = WORK / label
    shutil.rmtree(work, ignore_errors=True)
    (work / "modules").mkdir(parents=True)
    for file, data in files.items():
        (work / "modules" / file).write_bytes(data)
    ctx = context.Context(repository.FileRepository(str(work / "modules"), use_env=False))
    added = {
        file: ctx.add_module(file, data.decode("utf-8")) for file, data in sorted(files.items())
    }
    ctx.validate()
    diagnostics = []
    for pos, tag, args in ctx.errors:
        severity = "error" if error.is_error(error.err_level(tag)) else "warning"
        diagnostics.append(
            {
                "file": Path(str(pos.ref)).name,
                "line": pos.line,
                "code": tag,
                "category": category(tag),
                "severity": severity,
                "message": error.err_to_str(tag, args),
            }
        )
    errors = [d for d in diagnostics if d["severity"] == "error"]
    lib = library(files, ctx)
    (work / "library.json").write_text(json.dumps(lib, indent=1), encoding="utf-8")
    try:
        model = DataModel(json.dumps(lib), [str(work / "modules")])
        yangson = {"loaded": True, "schema_nodes": yangson_nodes(model.schema)}
        yangson_paths = data_paths(model.schema)
    except Exception as exc:  # the loader's refusal is the result being recorded
        yangson = {"loaded": False, "error": f"{type(exc).__name__}: {exc}"[:2000]}
    implement = {
        f"{e['name']}@{e['revision']}.yang"
        for e in lib["ietf-yang-library:modules-state"]["module"]
        if e["conformance-type"] == "implement"
    }
    implemented = {f: m for f, m in added.items() if f in implement and m is not None}
    nodes = inventory(list(implemented.values()))
    if yangson["loaded"]:
        # The inventory keeps nodes whatever their features; with every feature on, a
        # negated if-feature ("... and not anon-profile") removes the node in yangson.
        negated = [
            p for n in nodes for f in n.get("if-feature", []) if "not " in f for p in [n["path"]]
        ]
        ours = {
            n["path"]
            for n in nodes
            if not n.get("operation") and not any(n["path"].startswith(x) for x in negated)
        }
        yangson["paths_only_in_inventory"] = sorted(ours - yangson_paths)[:50]
        yangson["paths_only_in_yangson"] = sorted(yangson_paths - ours)[:50]
    (M1 / "inventory").mkdir(exist_ok=True)
    text = "".join(json.dumps(n, ensure_ascii=False, sort_keys=True) + "\n" for n in nodes)
    with open(M1 / "inventory" / f"{label}.jsonl.gz", "wb") as raw:
        # mtime 0 and no file name: identical inventories give identical bytes.
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as out:
            out.write(text.encode("utf-8"))
    compiled = libyang(work / "modules", sorted(implemented))
    tree = subprocess.run(
        [str(PYANG), "-p", str(work / "modules"), "-f", "tree", "--tree-line-length", "100"]
        + sorted(implemented),
        capture_output=True,
        cwd=work / "modules",
    )
    (work / "tree.txt").write_bytes(tree.stdout)
    kinds = Counter(n["kind"] for n in nodes)
    report = {
        "set": set_id,
        "variant": variant,
        "title": spec["title"],
        "roots": sorted(roots),
        "modules": [
            {
                "file": file,
                "sha256": digest(data),
                "decision": decisions.get(file.split("@")[0], {}).get("decision"),
                "root": file.split("@")[0] in roots,
            }
            for file, data in sorted(files.items())
        ],
        "missing_dependencies": sorted(missing, key=lambda m: (m["module"], m["required_by"])),
        "state_compat_leaked": leaked,
        "pyang": {
            "passed": not errors,
            "errors_by_category": dict(Counter(d["category"] for d in errors)),
            "warnings_by_category": dict(
                Counter(d["category"] for d in diagnostics if d["severity"] == "warning")
            ),
            "errors": errors[:300],
            "errors_truncated": max(0, len(errors) - 300),
        },
        "yangson": yangson,
        "libyang": compiled,
        "library_sha256": digest(json.dumps(lib, indent=1).encode()),
        "inventory": {
            "file": f"models/network-twin/m1/inventory/{label}.jsonl.gz",
            "nodes": len(nodes),
            "by_kind": dict(sorted(kinds.items())),
            "config": sum(1 for n in nodes if n.get("config") is True),
            "state": sum(1 for n in nodes if n.get("config") is False),
            "with_when": sum(1 for n in nodes if "when" in n),
            "with_must": sum(1 for n in nodes if "must" in n),
            "leafref": sum(1 for n in nodes if "leafref" in n),
            "identityref": sum(1 for n in nodes if "identityref" in n),
            "mandatory": sum(1 for n in nodes if n.get("mandatory") == "true"),
            "deprecated_or_obsolete": sum(
                1 for n in nodes if n.get("status") in ("deprecated", "obsolete")
            ),
        },
        "tree": {"lines": tree.stdout.count(b"\n"), "pyang_exit": tree.returncode},
        "_seconds": round(time.time() - started, 1),
    }
    (M1 / "sets").mkdir(exist_ok=True)
    (M1 / "sets" / f"{label}.json").write_text(
        json.dumps(
            {k: v for k, v in report.items() if not k.startswith("_")}, ensure_ascii=False, indent=1
        )
        + "\n",
        encoding="utf-8",
    )
    return report


def libyang(modules: Path, implemented: list[str]) -> dict:
    """yanglint compile, every module implemented (-i -i) with all features, sealed container."""
    try:
        image = subprocess.run(
            ["docker", "image", "inspect", LIBYANG_IMAGE, "--format", "{{.Id}}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        return {"ran": False, "reason": f"{LIBYANG_IMAGE} unavailable: {exc}"[:300]}
    run = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL"]
        + ["--security-opt", "no-new-privileges", "--user", "65534:65534"]
        + ["--mount", f"type=bind,source={modules},target=/modules,readonly"]
        + [image, "-D", "-i", "-i", "-p", "/modules"]
        + [f"/modules/{f}" for f in implemented],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
    )
    lines = [line for line in (run.stdout + run.stderr).splitlines() if line.strip()]
    errors = [line for line in lines if line.startswith(("libyang err", "YANGLINT[E]"))]
    return {
        "ran": True,
        "image_id": image,
        "exit": run.returncode,
        "passed": run.returncode == 0 and not errors,
        "errors": errors[:100],
        "warnings": [line for line in lines if line.startswith(("libyang warn", "YANGLINT[W]"))][
            :50
        ],
    }


def revision_decisions(decisions: dict) -> dict[str, str]:
    return {r["file"]: r["decision"] for d in decisions.values() for r in d.get("revisions", [])}


def same_as_baseline(spec: dict, decisions: dict, lock: dict, ieee: set[str]) -> bool:
    files = [
        set(closure(compose(spec, v, decisions, ieee), lock, revision_decisions(decisions), v)[0])
        for v in ("baseline", "candidate")
    ]
    return files[0] == files[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sets", nargs="*", help="set ids (default: all)")
    args = parser.parse_args()
    spec = json.loads((M1 / "sets.json").read_text(encoding="utf-8"))
    lock = json.loads((M1 / "sources.lock.json").read_text(encoding="utf-8"))
    decisions = {
        d["module"]: d
        for d in json.loads((M1 / "decisions.json").read_text(encoding="utf-8"))["decisions"]
    }
    ieee = {d for d, v in decisions.items() if v.get("standard_body") == "IEEE"}
    print("tools:", {n: importlib.metadata.version(n) for n in ("pyang", "yangson")})
    for set_id, set_spec in spec["sets"].items():
        if args.sets and set_id not in args.sets:
            continue
        for variant in ("baseline", "candidate"):
            if variant == "candidate" and same_as_baseline(set_spec, decisions, lock, ieee):
                (M1 / "sets" / f"{set_id}.candidate.json").unlink(missing_ok=True)
                (M1 / "inventory" / f"{set_id}.candidate.jsonl.gz").unlink(missing_ok=True)
                print(f"{set_id:20} candidate (no candidate-upgrade module; same as baseline)")
                continue
            r = build(set_id, set_spec, variant, decisions, lock, ieee)
            print(
                f"{set_id:20} {variant:9} files={len(r['modules']):3} "
                f"missing={len(r['missing_dependencies'])} "
                f"pyang_errors={sum(r['pyang']['errors_by_category'].values())} "
                f"{r['pyang']['errors_by_category']} yangson={r['yangson']['loaded']} "
                f"libyang={r['libyang'].get('passed')} "
                f"nodes={r['inventory']['nodes']} {r['_seconds']}s",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

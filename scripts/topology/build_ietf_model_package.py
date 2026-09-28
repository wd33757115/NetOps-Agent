"""Build an offline, traceable model package; never rewrite the upstream source.

Requires the audit cache and isolated pyang/yangson environment. This package
is a validation artifact, not an application or device capability declaration.
"""

from __future__ import annotations

import argparse
import difflib
import importlib.metadata
import json
from pathlib import Path

from audit_ietf_models import ROOT, compile_group, digest, fetch_sources
from pyang import context, repository
from yangson import DataModel

SPEC = ROOT / "models/network-twin"


def apply_syntax_patch(original: bytes) -> tuple[bytes, dict]:
    spec = json.loads((SPEC / "patches/simap-03-syntax.json").read_text(encoding="utf-8"))
    if digest(original) != spec["source_sha256"]:
        raise ValueError("SIMAP source differs from the reviewed patch baseline")
    text = original.decode("utf-8")
    for edit in spec["edits"]:
        if text.count(edit["before"]) != 1:
            raise ValueError(f"Patch context is not unique: {edit['reason']}")
        text = text.replace(edit["before"], edit["after"], 1)
    result = text.encode("utf-8")
    if digest(result) != spec["patched_sha256"]:
        raise ValueError("Patched source differs from the reviewed output")
    rendered = "".join(
        difflib.unified_diff(
            original.decode("utf-8").splitlines(keepends=True),
            text.splitlines(keepends=True),
            fromfile=f"original/{spec['module']}",
            tofile=f"patched/{spec['module']}",
            n=0,
        )
    )
    if rendered != (SPEC / "patches/simap-03-syntax.patch").read_text(encoding="utf-8"):
        raise ValueError("Human-readable patch differs from applied edits")
    return result, spec


def make_library(modules: dict[str, bytes], profile: dict, profile_id: str) -> dict:
    by_name = {filename.split("@")[0]: filename for filename in modules}
    ctx = context.Context(repository.FileRepository(use_env=False))
    statements = {
        name: ctx.add_module(filename, modules[filename].decode("utf-8"))
        for name, filename in by_name.items()
    }
    selected = set(profile["roots"])
    pending = list(selected)
    while pending:
        name = pending.pop()
        statement = statements[name]
        for dependency in [s.arg for s in statement.search("import") + statement.search("include")]:
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    entries = []
    for name in sorted(selected):
        filename = by_name[name]
        statement = statements[name]
        if statement.keyword == "submodule":
            continue
        entry = {
            "name": name,
            "revision": filename.split("@")[1][:-5],
            "namespace": statement.search_one("namespace").arg,
            "conformance-type": "implement"
            if name in profile["roots"] or name.startswith("iana-")
            else "import",
        }
        features = profile.get("features", {}).get(name, [])
        if features:
            entry["feature"] = features
        includes = [s.arg for s in statement.search("include")]
        if includes:
            entry["submodule"] = [
                {"name": sub, "revision": by_name[sub].split("@")[1][:-5]} for sub in includes
            ]
        entries.append(entry)
    return {"ietf-yang-library:modules-state": {"module-set-id": profile_id, "module": entries}}


def load_model(package: Path, profile: str) -> DataModel:
    return DataModel(
        (package / f"{profile}-library.json").read_text(encoding="utf-8"),
        [str(package / "modules")],
    )


def build(output: Path, cache: Path, offline: bool = True) -> dict:
    lock_path = ROOT / "docs/ietf-model-audit/sources.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    cache.mkdir(parents=True, exist_ok=True)
    all_modules = fetch_sources(lock, cache, offline=offline)
    simap = "ietf-simap-topology@2025-01-01.yang"
    modules = {name: all_modules[name] for name in lock["groups"]["rfc-inventory-external"]}
    additional_path = SPEC / "additional-sources.lock.json"
    additional = json.loads(additional_path.read_text(encoding="utf-8"))
    modules.update(fetch_sources(additional, cache, offline=offline))
    patched, patch = apply_syntax_patch(all_modules[simap])
    modules[simap] = patched
    original_dir = output / "original"
    original_dir.mkdir(parents=True, exist_ok=True)
    (original_dir / simap).write_bytes(all_modules[simap])
    compilation = compile_group(sorted(modules), modules, output / "modules")
    if not compilation["passed"]:
        raise ValueError(compilation["diagnostics"])
    profile_specs = json.loads((SPEC / "profiles.json").read_text(encoding="utf-8"))
    profiles = {}
    for name, profile in profile_specs.items():
        fingerprint = json.dumps(
            {"modules": {k: digest(v) for k, v in modules.items()}, "profile": profile},
            sort_keys=True,
        ).encode()
        library = make_library(modules, profile, f"validation-{name}-{digest(fingerprint)}")
        text = json.dumps(library, indent=2) + "\n"
        (output / f"{name}-library.json").write_bytes(text.encode("utf-8"))
        model = load_model(output, name)
        (output / f"{name}-tree.txt").write_bytes(model.ascii_tree().encode("utf-8"))
        profiles[name] = {"library_sha256": digest(text.encode()), "model_loaded": True}
    manifest = {
        "scope": "validation-package; local syntax patch disclosed; not full digital twin delivery",
        "tools": {name: importlib.metadata.version(name) for name in ("pyang", "yangson")},
        "sources_lock_sha256": digest(lock_path.read_bytes()),
        "additional_sources_lock_sha256": digest(additional_path.read_bytes()),
        "patch": patch,
        "modules": {name: digest(data) for name, data in sorted(modules.items())},
        "compile": compilation,
        "profiles": profiles,
    }
    (output / "manifest.json").write_bytes((json.dumps(manifest, indent=2) + "\n").encode())
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache", type=Path, default=ROOT / ".runtime/ietf-study/reproducible-audit/sources"
    )
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/ietf-study/model-package")
    parser.add_argument("--fetch", action="store_true", help="Download missing pinned sources")
    args = parser.parse_args()
    result = build(args.output, args.cache, offline=not args.fetch)
    print(
        json.dumps(
            {
                "modules": len(result["modules"]),
                "compiled": result["compile"]["passed"],
                "profiles": result["profiles"],
                "output": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()

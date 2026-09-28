"""Audit actual mount declarations and historical candidates without altering them."""

from __future__ import annotations

import argparse
import json

from audit_ietf_models import ROOT, digest, extract_modules, fetch_sources
from pyang import context, repository


def scan(statement, ancestors=()):
    found = []
    path = ancestors + (str(statement.arg),)
    if isinstance(statement.keyword, tuple) and statement.keyword[1] == "mount-point":
        found.append(
            {
                "extension": list(statement.keyword),
                "label": statement.arg,
                "declaration_path": list(ancestors),
                "line": statement.pos.line,
            }
        )
    for child in statement.substmts:
        found.extend(scan(child, path))
    return found


def audit(offline: bool) -> dict:
    package = ROOT / ".runtime/ietf-study/model-package"
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    current = {}
    for filename, expected in manifest["modules"].items():
        raw = (package / "modules" / filename).read_bytes()
        if digest(raw) != expected:
            raise ValueError(f"Model changed: {filename}")
        ctx = context.Context(repository.FileRepository(use_env=False))
        statement = ctx.add_module(filename, raw.decode("utf-8"))
        if statement is None:
            raise ValueError(f"Unexpected syntax failure: {filename}")
        current[filename] = scan(statement)
    mount = ROOT / "models/network-twin/mount"
    lock_path = mount / "history-sources.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    cache = ROOT / ".runtime/ietf-study/mount-history"
    cache.mkdir(parents=True, exist_ok=True)
    fetch_sources(lock, cache, offline=offline)
    history = []
    for spec in lock["sources"]:
        raw = (cache / spec["file"]).read_text(encoding="utf-8")
        for filename, data in extract_modules(raw, "rfc-code").items():
            ctx = context.Context(repository.FileRepository(use_env=False))
            statement = ctx.add_module(filename, data.decode("utf-8"))
            history.append(
                {
                    "source": spec["url"],
                    "module": filename,
                    "sha256": digest(data),
                    "syntax_parsed": statement is not None,
                    "diagnostics": [
                        {"line": pos.line, "code": code, "arguments": args}
                        for pos, code, args in ctx.errors
                    ],
                    "mount_declarations": scan(statement) if statement is not None else None,
                    "scope": "Syntax/declaration audit only; historical dependencies not compiled",
                }
            )
    return {
        "scope": "Audit of the pinned package and three historical candidates; not all IETF work",
        "model_package_sha256": digest((package / "manifest.json").read_bytes()),
        "history_lock_sha256": digest(lock_path.read_bytes()),
        "declarations_by_module": current,
        "historical_candidates": history,
        "virtual_end_to_end_mapping_closed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    result = audit(offline=not args.fetch)
    output = ROOT / "models/network-twin/reports/mount-associations.json"
    output.write_bytes((json.dumps(result, indent=2) + "\n").encode())
    print(
        json.dumps(
            {
                "modules": len(result["declarations_by_module"]),
                "mounts": {
                    name: values
                    for name, values in result["declarations_by_module"].items()
                    if values
                },
                "historical_candidates": result["historical_candidates"],
            }
        )
    )


if __name__ == "__main__":
    main()

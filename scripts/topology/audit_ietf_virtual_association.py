"""Audit candidate definitions for virtual topology node -> NE association.

Traces inventory-topology -02..-10 and three other IVY drafts from a pinned lock,
then runs Yangson cases against the historical -05 module and the current -11
package. Historical modules are evidence only: they share the current module
name, so adopting them would replace (downgrade) the pinned -11 baseline.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
from pathlib import Path

from audit_ietf_models import ROOT, compile_group, digest, fetch_sources
from build_ietf_model_package import load_model, make_library
from yangson import DataModel
from yangson.enumerations import ContentType

SPEC = ROOT / "models/network-twin/virtual-association"
PACKAGE = ROOT / ".runtime/ietf-study/model-package"
CACHE = ROOT / ".runtime/ietf-study/association-candidates"
WORK = ROOT / ".runtime/ietf-study/virtual-association"
INVTOPO = "ietf-network-inventory-topology"
NWI = "ietf-network-inventory:"
SW = "ietf-network-inventory-sw-ext:"


def data_nodes(node, prefix=""):
    found = []
    for child in getattr(node, "i_children", []):
        if child.keyword in ("choice", "case"):
            found.extend(data_nodes(child, prefix))
            continue
        path = f"{prefix}/{child.arg}"
        presence = child.search_one("presence")
        leaf_type = child.search_one("type")
        require = leaf_type.search_one("require-instance") if leaf_type else None
        found.append(
            {
                "path": path,
                "keyword": child.keyword,
                "config": getattr(child, "i_config", None),
                "presence": presence.arg if presence else None,
                "type": leaf_type.arg if leaf_type else None,
                "require_instance": require.arg if require else None,
                "if_feature": [f.arg for f in child.search("if-feature")],
            }
        )
        found.extend(data_nodes(child, path))
    return found


def structure(folder: Path, filename: str) -> dict:
    from pyang import context, repository

    ctx = context.Context(repository.FileRepository(str(folder), use_env=False))
    module = ctx.add_module(filename, (folder / filename).read_text(encoding="utf-8"))
    ctx.validate()
    return {
        "namespace": module.search_one("namespace").arg,
        "features": [f.arg for f in module.search("feature")],
        "augments": [
            {
                "target": aug.arg,
                "when": aug.search_one("when").arg if aug.search_one("when") else None,
                "if_feature": [f.arg for f in aug.search("if-feature")],
                "nodes": data_nodes(aug),
            }
            for aug in module.search("augment")
        ],
    }


def compile_candidate(tag: str, filename: str, data: bytes, base: dict[str, bytes]) -> dict:
    folder = WORK / tag
    if folder.exists():
        shutil.rmtree(folder)
    modules = {k: v for k, v in base.items() if not k.startswith(INVTOPO + "@")}
    modules[filename] = data
    result = compile_group(sorted(modules), modules, folder)
    result["files"] = len(result["files"])
    result["diagnostics"] = [d for d in result["diagnostics"] if d["file"] == filename] or [
        d for d in result["diagnostics"] if d["severity"] == "error"
    ]
    if result["passed"]:
        result["structure"] = structure(folder, filename)
    return result


def historical_model(folder: Path, features: list[str]) -> DataModel:
    modules = {p.name: p.read_bytes() for p in folder.glob("*.yang")}
    profile = {
        "roots": [
            "ietf-network",
            "ietf-network-topology",
            "ietf-network-inventory",
            "ietf-network-inventory-sw-ext",
            INVTOPO,
        ],
        "features": {INVTOPO: features} if features else {},
    }
    library = make_library(modules, profile, f"historical-{'-'.join(features) or 'none'}")
    return DataModel(json.dumps(library), [str(folder)])


def inventory(*elements):
    return {
        NWI + "network-inventory": {
            "network-elements": {
                "network-element": [{"ne-id": ne, "ne-type": kind} for ne, kind in elements]
            }
        }
    }


def run_cases(folder05: Path) -> list[dict]:
    results = []

    def check(name, model, data, expected, *, content=ContentType.all, gap=None, error=None):
        diagnostic = None
        try:
            model.from_raw(data).validate(ctype=content)
            accepted = True
        except Exception as exc:
            diagnostic = f"{type(exc).__name__}: {exc}"
            accepted = False
        if not expected and not error:
            raise ValueError(f"Negative check must name its diagnostic: {name}")
        results.append(
            {
                "name": name,
                "accepted": accepted,
                "expected": expected,
                "matched": accepted == expected and (error is None or error in (diagnostic or "")),
                "expected_diagnostic": error,
                "semantic_gap": gap,
                "diagnostic": diagnostic,
            }
        )

    it = INVTOPO + ":"
    h05 = historical_model(
        folder05, ["topology-to-inventory-navigate", "inventory-to-topology-navigate"]
    )
    h05_off = historical_model(folder05, [])
    vsr = inventory(("vsr-1", SW + "ne-virtual"), ("host-1", NWI + "ne-physical"))
    node_map = {
        "ietf-network:networks": {
            "network": [
                {
                    "network-id": "urn:example:l3",
                    "node": [
                        {
                            "node-id": "urn:example:vsr-1",
                            it + "inventory-mapping-attributes": {"ne-ref": "vsr-1"},
                        }
                    ],
                }
            ]
        },
        **vsr,
    }
    check(
        "h05-virtual-node-ne-ref",
        h05,
        node_map,
        True,
        gap="Superseded -05 and mixed with current inventory -19; cannot be co-loaded with -11",
    )
    missing = copy.deepcopy(node_map)
    missing.pop(NWI + "network-inventory")
    check(
        "h05-target-missing-still-accepted",
        h05,
        missing,
        True,
        gap="nwi:ne-ref is require-instance false; acceptance is not resolution",
    )
    wrong = copy.deepcopy(node_map)
    wrong["ietf-network:networks"]["network"][0]["node"][0][it + "inventory-mapping-attributes"][
        "ne-ref"
    ] = "host-1"
    check(
        "h05-wrong-object-physical-host-accepted",
        h05,
        wrong,
        True,
        gap="Schema cannot tell the VM router NE from its hosting server NE",
    )
    # The inventory root is config false itself; test the topology part alone so the
    # rejection is attributable to the mapping leaf rather than to the inventory tree.
    check(
        "h05-mapping-is-not-config",
        h05,
        missing,
        False,
        content=ContentType.config,
        error="member-not-allowed: ietf-network-inventory-topology:inventory-mapping-attributes",
    )
    check(
        "h05-feature-disabled-rejects-mapping",
        h05_off,
        node_map,
        False,
        error="inventory-mapping-attributes",
    )
    reverse = inventory(("vsr-1", SW + "ne-virtual"))
    element = reverse[NWI + "network-inventory"]["network-elements"]["network-element"][0]
    element[it + "network-ref"] = "urn:example:absent"
    element[it + "node-ref"] = "urn:example:absent-node"
    check(
        "h05-reverse-node-ref-target-missing-accepted",
        h05,
        reverse,
        True,
        gap="RFC 8345 node-ref grouping is require-instance false; removed from -06 onward",
    )

    current = load_model(PACKAGE, "topology")
    l3 = {
        "ietf-network:networks": {
            "network": [
                {
                    "network-id": "urn:example:l3",
                    "network-types": {"ietf-l3-unicast-topology:l3-unicast-topology": {}},
                    "node": [
                        {
                            "node-id": "urn:example:vsr-1",
                            it + "inventory-mapping-attributes": {"ne-ref": "vsr-1"},
                        }
                    ],
                }
            ]
        },
        **vsr,
    }
    check(
        "c11-mapping-outside-inventory-topology-rejected",
        current,
        l3,
        False,
        error="inventory-mapping-attributes",
    )
    abstract = {
        "ietf-network:networks": {
            "network": [
                {
                    "network-id": "urn:example:physical",
                    "network-types": {it + "inventory-topology": {}},
                    "node": [{"node-id": "urn:example:vsr-1"}],
                }
            ]
        },
        **vsr,
    }
    check(
        "c11-node-without-mapping-is-abstract",
        current,
        abstract,
        True,
        gap="Valid only as an abstract node; no link to the ne-virtual entry exists",
    )
    hosted = {
        "ietf-network:networks": {
            "network": [
                {
                    "network-id": "urn:example:physical",
                    "network-types": {it + "inventory-topology": {}},
                    "node": [
                        {
                            "node-id": "urn:example:host-1",
                            it + "inventory-mapping-attributes": {"ne-ref": "host-1"},
                        }
                    ],
                },
                {
                    "network-id": "urn:example:l3",
                    "network-types": {"ietf-l3-unicast-topology:l3-unicast-topology": {}},
                    "supporting-network": [{"network-ref": "urn:example:physical"}],
                    "node": [
                        {
                            "node-id": "urn:example:vsr-1",
                            "supporting-node": [
                                {
                                    "network-ref": "urn:example:physical",
                                    "node-ref": "urn:example:host-1",
                                }
                            ],
                        }
                    ],
                },
            ]
        },
        **vsr,
    }
    check(
        "c11-supporting-node-to-physical-host",
        current,
        hosted,
        True,
        gap="Needs hosting-server evidence not collected; still leaves ne-virtual unlinked",
    )
    return results


def audit(offline: bool) -> dict:
    lock_path = SPEC / "sources.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    manifest = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
    base = {}
    for filename, expected in manifest["modules"].items():
        raw = (PACKAGE / "modules" / filename).read_bytes()
        if digest(raw) != expected:
            raise ValueError(f"Model changed: {filename}")
        base[filename] = raw
    CACHE.mkdir(parents=True, exist_ok=True)
    candidates = []
    folder05 = None
    for spec in lock["sources"]:
        # One source per call: -02/-03 reuse a revision date with different bytes.
        modules = fetch_sources({"sources": [spec]}, CACHE, offline=offline)
        text = (CACHE / spec["file"]).read_text(encoding="utf-8")
        tag = spec["file"].removesuffix(".txt")
        entry = {
            "source": spec["url"],
            "sha256": spec["sha256"],
            "imports_ietf_network": "import ietf-network {" in text,
            "code_begins": text.count("<CODE BEGINS>"),
        }
        for filename, data in modules.items():
            entry["module"] = filename
            entry["module_sha256"] = digest(data)
            entry["compile_with_current_package"] = compile_candidate(tag, filename, data, base)
            if tag.endswith("-topology-05"):
                folder05 = WORK / tag
        candidates.append(entry)
    current = compile_candidate(
        "current-11",
        f"{INVTOPO}@2026-09-11.yang",
        base[f"{INVTOPO}@2026-09-11.yang"],
        base,
    )
    checks = run_cases(folder05)
    return {
        "scope": (
            "Candidate audit for virtual node -> NE -> device tree; historical modules are "
            "evidence, not adopted; not all IETF work"
        ),
        "model_package_sha256": digest((PACKAGE / "manifest.json").read_bytes()),
        "lock_sha256": digest(lock_path.read_bytes()),
        "candidates": candidates,
        "current_inventory_topology_11": current,
        "checks": checks,
        "expectations_matched": all(c["matched"] for c in checks),
        "virtual_end_to_end_mapping_closed": False,
        "ne_to_device_datastore_standard_reference": None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "models/network-twin/reports/virtual-association.json"
    )
    args = parser.parse_args()
    result = audit(offline=not args.fetch)
    args.output.write_bytes((json.dumps(result, indent=2) + "\n").encode())
    print(
        json.dumps(
            {
                "candidates": [
                    {
                        "source": c["source"].rsplit("/", 1)[1],
                        "compiled": c.get("compile_with_current_package", {}).get("passed"),
                    }
                    for c in result["candidates"]
                ],
                "checks": len(result["checks"]),
                "matched": result["expectations_matched"],
                "failures": [c for c in result["checks"] if not c["matched"]],
            },
            indent=1,
        )
    )
    raise SystemExit(0 if result["expectations_matched"] else 1)


if __name__ == "__main__":
    main()

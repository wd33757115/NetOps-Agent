"""Reproduce the pinned IETF research audit without changing upstream models.

Run with the isolated research Python (pyang==2.7.1, yangson==1.6.3).
The lock file pins source bytes and extracted modules. Only pagination and
document indentation are removed. Exit 1 means a model failed validation,
even when that failure is a known finding; it does not mean audit success.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import json
import re
import textwrap
from pathlib import Path
from urllib.request import urlopen

from pyang import context, error, repository
from yangson import DataModel
from yangson.enumerations import ContentType

ROOT = Path(__file__).resolve().parents[2]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_modules(text: str, mode: str) -> dict[str, bytes]:
    if mode == "simap-section-9":
        text = text.split("\n9.  YANG Module\n", 1)[1].split("\n10.  Security Considerations\n", 1)[
            0
        ]
        blocks = [text]
    else:
        blocks = re.findall(r"<CODE BEGINS>[^\n]*\n(.*?)<CODE ENDS>", text, re.S)
    extracted = {}
    for block in blocks:
        lines = [
            line
            for line in block.splitlines()
            if not re.match(r"^(Internet-Draft|RFC 9911\b)", line)
            and not re.match(r"^\S.*\[Page", line)
        ]
        block = "\n".join(lines)
        if mode == "simap-section-9":
            block = block[: block.rindex("\n}") + 2]
        start = re.search(r"^ *module ([\w-]+) \{", block, re.M)
        if start is None:
            raise ValueError("Module declaration absent from selected source block")
        block = textwrap.dedent(block[start.start() :]).rstrip() + "\n"
        rev = re.search(r"\brevision\s+[\"']?(\d{4}-\d{2}-\d{2})", block)
        if rev is None:
            raise ValueError("Revision absent from selected source block")
        extracted[f"{start[1]}@{rev[1]}.yang"] = block.encode("utf-8")
    if not extracted:
        raise ValueError("No code blocks found")
    return extracted


def fetch_sources(lock: dict, cache: Path, offline: bool) -> dict[str, bytes]:
    modules = {}
    for spec in lock["sources"]:
        source = cache / spec["file"]
        if not source.exists():
            if offline:
                raise FileNotFoundError(f"Offline source missing: {source}")
            with urlopen(spec["url"], timeout=45) as response:
                source.write_bytes(response.read())
        data = source.read_bytes()
        if digest(data) != spec["sha256"]:
            raise ValueError(f"Source hash mismatch: {spec['file']}")
        if spec["format"] == "yang":
            found = {spec["file"]: data}
        elif spec["format"] == "text":
            continue
        else:
            found = extract_modules(data.decode("utf-8"), spec["format"])
        if set(found) != set(spec["modules"]):
            raise ValueError(f"Extracted module set mismatch: {spec['file']}")
        for filename, content in found.items():
            if digest(content) != spec["modules"][filename]:
                raise ValueError(f"Extracted module hash mismatch: {filename}")
            if filename in modules and modules[filename] != content:
                raise ValueError(f"Conflicting module: {filename}")
            modules[filename] = content
    return modules


def compile_group(files: list[str], modules: dict[str, bytes], folder: Path) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    # Never let leftovers silently satisfy a new dependency.
    existing = {p.name for p in folder.glob("*.yang")}
    if existing - set(files):
        raise ValueError(f"Unexpected cached modules in {folder}; use a new output directory")
    for filename in files:
        (folder / filename).write_bytes(modules[filename])
    ctx = context.Context(repository.FileRepository(str(folder), use_env=False))
    for filename in files:
        ctx.add_module(filename, modules[filename].decode("utf-8"))
    ctx.validate()
    diagnostics = [
        {
            "file": Path(pos.ref).name,
            "line": pos.line,
            "code": tag,
            "severity": "error" if error.is_error(error.err_level(tag)) else "warning",
            "message": error.err_to_str(tag, args),
        }
        for pos, tag, args in ctx.errors
    ]
    return {
        "files": files,
        "passed": not any(d["severity"] == "error" for d in diagnostics),
        "diagnostics": diagnostics,
    }


def semantic_checks(folder: Path, cache: Path) -> list[dict]:
    imports = {"ietf-inet-types", "ietf-yang-types"}
    entries = []
    for path in sorted(folder.glob("*.yang")):
        text = path.read_text(encoding="utf-8")
        name, revision = path.stem.split("@")
        namespace = re.search(r'namespace\s+"([^"]+)"', text)[1]
        entries.append(
            {
                "name": name,
                "revision": revision,
                "namespace": namespace,
                "conformance-type": "import" if name in imports else "implement",
            }
        )
    library = {"ietf-yang-library:modules-state": {"module-set-id": "research", "module": entries}}
    model = DataModel(json.dumps(library), [str(folder)])
    results = []

    def check(name: str, data: dict, expected: bool, interpretation: str = "constraint"):
        diagnostic = None
        try:
            model.from_raw(data).validate(ctype=ContentType.all)
            accepted = True
        except Exception as exc:
            accepted = False
            diagnostic = f"{type(exc).__name__}: {exc}"
        results.append(
            {
                "name": name,
                "expected_accept": expected,
                "accepted": accepted,
                "matched": accepted == expected,
                "interpretation": interpretation,
                "diagnostic": diagnostic,
            }
        )

    base = {
        "ietf-network:networks": {
            "network": [
                {
                    "network-id": "urn:test:a",
                    "node": [{"node-id": "urn:test:r1"}],
                }
            ]
        }
    }
    check("base-network", base, True)
    data = copy.deepcopy(base)
    data["ietf-network:networks"]["network"].append(
        {
            "network-id": "urn:test:b",
            "node": [{"node-id": "urn:test:r1"}],
        }
    )
    check("node-id-is-network-scoped", data, True)
    duplicate = copy.deepcopy(base)
    duplicate["ietf-network:networks"]["network"][0]["node"].append({"node-id": "urn:test:r1"})
    check("duplicate-node-key", duplicate, False)
    custom = copy.deepcopy(base)
    custom["ietf-network:networks"]["network"][0]["node"][0]["confidence"] = "high"
    check("no-custom-confidence", custom, False)
    dangling = copy.deepcopy(base)
    dangling["ietf-network:networks"]["network"][0]["ietf-network-topology:link"] = [
        {
            "link-id": "urn:test:link",
            "source": {"source-node": "urn:test:missing"},
        }
    ]
    check("base-leafref-require-instance-false", dangling, True)
    cycle = copy.deepcopy(dangling)
    net = cycle["ietf-network:networks"]["network"][0]
    net["supporting-network"] = [{"network-ref": "urn:test:a"}]
    net["ietf-network-topology:link"][0]["supporting-link"] = [
        {
            "network-ref": "urn:test:a",
            "link-ref": "urn:test:link",
        }
    ]
    check(
        "supporting-cycle-not-enforced-by-schema", cycle, True, "semantic-gap-not-approved-instance"
    )

    inventory = {
        "ietf-network-inventory:network-inventory": {
            "network-elements": {
                "network-element": [
                    {"ne-id": "ne1", "ne-type": "ietf-network-inventory-sw-ext:ne-virtual"}
                ],
            }
        }
    }
    check("native-virtual-ne-identity", inventory, True)
    wrong = copy.deepcopy(inventory)
    wrong["ietf-network-inventory:network-inventory"]["network-elements"]["network-element"][0][
        "ne-type"
    ] = "ietf-network-inventory:virtual-device"
    check("invented-virtual-identity", wrong, False)
    mapping = copy.deepcopy(base)
    net = mapping["ietf-network:networks"]["network"][0]
    net["network-types"] = {"ietf-network-inventory-topology:inventory-topology": {}}
    node = net["node"][0]
    node["ietf-network-inventory-topology:inventory-mapping-attributes"] = {"ne-ref": "ne1"}
    check("inventory-unresolved-ne-reference-allowed", mapping, True)
    no_type = copy.deepcopy(mapping)
    del no_type["ietf-network:networks"]["network"][0]["network-types"]
    check("inventory-mapping-requires-network-type", no_type, False)
    mixed = copy.deepcopy(mapping)
    mixed.update(copy.deepcopy(inventory))
    check(
        "physical-mapping-to-virtual-ne-not-schema-guarded",
        mixed,
        True,
        "semantic-gap-not-approved-instance",
    )
    component = copy.deepcopy(inventory)
    ne = component["ietf-network-inventory:network-inventory"]["network-elements"][
        "network-element"
    ][0]
    ne["components"] = {"component": [{"component-id": "p1"}]}
    check("inventory-component-class-required", component, False)
    ne["components"]["component"][0]["class"] = "iana-hardware:chassis"
    wrong_port = copy.deepcopy(mapping)
    wrong_port.update(component)
    wrong_port["ietf-network:networks"]["network"][0]["node"][0][
        "ietf-network-topology:termination-point"
    ] = [
        {
            "tp-id": "urn:test:p1",
            "ietf-network-inventory-topology:inventory-mapping-attributes": {
                "ne-ref": "ne1",
                "port-ref": "p1",
            },
        }
    ]
    check(
        "port-ref-class-not-schema-guarded", wrong_port, True, "semantic-gap-not-approved-instance"
    )

    template = {"draft-template:template": {"template": [{"template-id": "urn:test:template"}]}}
    relation = {
        "relation-id": "urn:test:relation",
        "node-ref": "urn:test:r1",
        "template-ref": "urn:test:template",
    }
    ambiguous = copy.deepcopy(data)
    ambiguous.update(template)
    ambiguous["draft-relation:relation"] = {"relation": [relation]}
    check(
        "external-node-ref-ambiguity-not-schema-guarded",
        ambiguous,
        True,
        "semantic-gap-not-approved-instance",
    )
    missing = copy.deepcopy(ambiguous)
    missing["draft-relation:relation"]["relation"][0]["node-ref"] = "urn:test:absent"
    check("external-node-ref-requires-target", missing, False)
    choice = copy.deepcopy(ambiguous)
    choice["draft-relation:relation"]["relation"][0]["network-ref"] = "urn:test:a"
    check("external-target-choice-is-exclusive", choice, False)

    # The original examples use RFC 8792 backslash folding across page breaks.
    original = (cache / "draft-ietf-ivy-network-inventory-topology-11.txt").read_text(
        encoding="utf-8"
    )
    original = "\n".join(
        line
        for line in original.splitlines()
        if not re.match(r"^(Internet-Draft|Wu, et al\.)", line)
    )
    for appendix in ("A", "B"):
        section = original.split(f"\nAppendix {appendix}.", 1)[1]
        start = section.index("\n   {")
        section = re.sub(r"\\\n\s*", "", section[start:]).lstrip()
        instance, _ = json.JSONDecoder().raw_decode(section)
        check(
            f"upstream-inventory-topology-appendix-{appendix}",
            instance,
            True,
            "upstream-example-schema-only-unresolved-inventory",
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".runtime/ietf-study/reproducible-audit"
    )
    args = parser.parse_args()
    lock_path = ROOT / "docs/ietf-model-audit/sources.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    cache = args.output / "sources"
    cache.mkdir(parents=True, exist_ok=True)
    modules = fetch_sources(lock, cache, args.offline)
    groups = {}
    for name, files in lock["groups"].items():
        groups[name] = compile_group(files, modules, args.output / name)
    concept = (cache / "draft-ietf-nmop-simap-concept-13.txt").read_text(encoding="utf-8")
    required = set(re.findall(r"^   (REQ-[A-Z-]+):", concept, re.M))
    matrix = (ROOT / "docs/network-digital-twin-model-audit.md").read_text(encoding="utf-8")
    covered = re.findall(r"^\| (REQ-[A-Z-]+) \|", matrix, re.M)
    coverage = {
        "required": len(required),
        "rows": len(covered),
        "missing": sorted(required - set(covered)),
        "extra": sorted(set(covered) - required),
        "duplicate": sorted(x for x in set(covered) if covered.count(x) > 1),
    }
    coverage["passed"] = not any(coverage[k] for k in ("missing", "extra", "duplicate"))
    semantics = (
        semantic_checks(args.output / "inventory-external", cache)
        if groups["inventory-external"]["passed"]
        else []
    )
    report = {
        "scope": "research-only; no source fixes; no production capability declaration",
        "lock_sha256": digest(lock_path.read_bytes()),
        "audit_script_sha256": digest(Path(__file__).read_bytes()),
        "tools": {name: importlib.metadata.version(name) for name in ("pyang", "yangson")},
        "verified_sources": len(lock["sources"]),
        "verified_modules": len(modules),
        "compile_groups": groups,
        "requirements_coverage": coverage,
        "instance_checks": semantics,
        "not_validated": [
            "SIMAP bidirectional, multipoint, navigation and lifecycle instances: "
            "original schema does not compile",
            "End-to-end virtual topology to device datastore and schema-mount association",
            "Full TE and protocol-extension model coverage",
        ],
    }
    report["all_checks_passed"] = (
        all(g["passed"] for g in groups.values())
        and coverage["passed"]
        and bool(semantics)
        and all(c["matched"] for c in semantics)
    )
    path = args.output / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "report": str(path),
                "all_checks_passed": report["all_checks_passed"],
                "groups": {k: v["passed"] for k, v in groups.items()},
                "requirements": coverage,
                "instance_checks": len(semantics),
                "instance_expectations_matched": sum(c["matched"] for c in semantics),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

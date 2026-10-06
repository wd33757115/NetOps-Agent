"""Check the pinned OpenConfig modules and write their YANG library (RFC 7895).

The modules are those of models/network-twin/openconfig/sources.lock.json: the
policy-forwarding model with its complete dependency closure, from one openconfig/public
commit. The IETF modules they import are the ones the topology App already pins
(src/apps/topology/resources/yang). Every file is checked against its locked SHA-256;
nothing is fetched or patched. The library lets yangson validate a PBR tree:

    python scripts/topology/build_openconfig_library.py [--check]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "models/network-twin/openconfig"
MODULES = PACKAGE / "modules"
IETF = ROOT / "src/apps/topology/resources/yang"
LIBRARY = PACKAGE / "library.json"
# Every OpenConfig module of the closure is implemented: the network-instance tree holds
# leafrefs into routing-policy, defined-sets, qos and keychain data, which yangson resolves
# only in implemented modules. Of the IETF modules only iana-if-type is implemented, so
# that its identities can name interface types; the others are only imported.
# The IETF modules the closure imports, at the revisions the App pins.
IETF_MODULES = {
    "iana-if-type": "2014-05-08",
    "ietf-interfaces": "2018-02-20",
    "ietf-inet-types": "2025-12-22",
    "ietf-yang-types": "2025-12-22",
}


def _statement(text, keyword):
    found = re.search(rf"^\s*{keyword}\s+\"?([^\s;\"{{]+)", text, re.M)
    return found and found.group(1)


def _read(path):
    text = path.read_text("utf-8")
    kind = "submodule" if re.search(r"^\s*submodule\s", text, re.M) else "module"
    return {
        "kind": kind,
        "name": _statement(text, kind),
        "revision": _statement(text, "revision") or "",
        "namespace": _statement(text, "namespace"),
        "belongs_to": _statement(text, "belongs-to"),
    }


def build():
    lock = json.loads((PACKAGE / "sources.lock.json").read_text("utf-8"))
    files = {source["file"]: source for source in lock["sources"]}
    present = {path.name for path in MODULES.glob("*.yang")}
    if present != set(files):
        raise SystemExit(f"modules differ from the lock: {sorted(present ^ set(files))}")
    for name, source in files.items():
        digest = hashlib.sha256((MODULES / name).read_bytes()).hexdigest()
        if digest != source["sha256"]:
            raise SystemExit(f"{name}: SHA-256 differs from the lock")
    modules, submodules = {}, {}
    for path in sorted(MODULES.glob("*.yang")):
        info = _read(path)
        (submodules if info["kind"] == "submodule" else modules)[info["name"]] = info
    for name, revision in IETF_MODULES.items():
        modules[name] = _read(IETF / f"{name}@{revision}.yang")
    entries = []
    for name in sorted(modules):
        info = modules[name]
        entry = {
            "name": name,
            "revision": info["revision"],
            "namespace": info["namespace"],
            "conformance-type": "import" if name in IETF_MODULES and name != "iana-if-type" else "implement",
        }
        own = sorted(s for s, sub in submodules.items() if sub["belongs_to"] == name)
        if own:
            entry["submodule"] = [
                {"name": s, "revision": submodules[s]["revision"]} for s in own
            ]
        entries.append(entry)
    return {
        "ietf-yang-library:modules-state": {
            "module-set-id": "netops-openconfig-policy-forwarding-" + lock["commit"][:12],
            "module": entries,
        }
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if library.json differs")
    args = parser.parse_args()
    text = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if LIBRARY.read_text("utf-8") != text:
            sys.exit("library.json is not what the pinned modules give; rebuild it")
        print("library.json matches the pinned modules")
        return
    LIBRARY.write_text(text, "utf-8", newline="\n")
    print(f"wrote {LIBRARY.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

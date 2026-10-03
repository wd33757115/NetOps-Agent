"""M1-6: positive/negative cases and a validator that tells the gap kinds apart.

Gap kinds (implementation plan §8 M1 acceptance):
- schema: the model cannot hold the data - an unknown member, a module that cannot be
  loaded (missing dependency), an import pinned to a revision nobody has (wrong revision);
- semantic: the data breaks a constraint of the model - type/range, unknown identity,
  data in two cases of one choice, a reference to an object outside the tree's scope,
  when/must - or a mapping declares a unit other than the node's units;
- collection: the tree parses and its references resolve, but key/mandatory nodes were
  not observed (complete validation fails, operational validation passes).
Every instance runs in libyang 5.8.6 (the sealed yanglint container of the mount
validation) in up to three modes: get (parse only: hierarchy, types, identities), data
(operational: references, RFC 8342 §5.3 tolerance) and strict (complete datastore). The
schema each instance is checked against is the lock closure of the modules it uses, at
their runtime-baseline revisions.

Positive cases are real topology App output for the campus-wan fixtures (same parsers and
builders as discovery, no device access); negative cases are single, named mutations of
them, or real catalog modules with real defects.

Run: venv/Scripts/python.exe scripts/topology/m1_cases.py [--build]
Output: models/network-twin/m1/cases/ (instances, manifest) and reports/cases.json.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts/topology")]
from m1_sources import dependencies  # noqa: E402

M1 = ROOT / "models/network-twin/m1"
CASES = M1 / "cases"
REPORT = M1 / "reports/cases.json"
MODULES = ROOT / ".runtime/m1/cache/modules"
INSTANCES = ROOT / "models/network-twin/instances"
WORK = ROOT / ".runtime/m1/cases"
IMAGE = "netops-ietf-mount-verify:5.8.6"
VRRP = "/ietf-interfaces:interfaces/interface/ietf-ip:ipv4/ietf-vrrp:vrrp/vrrp-instance"
SUBMODULE = re.compile(rb"^\s*submodule\s", re.M)
# libyang 5.8.6 message -> (gap kind, category); first match wins.
CLASSES = [
    (r'Loading "[^"]+@\d{4}-\d{2}-\d{2}" module failed', "schema", "wrong-revision"),
    (r'Parsing module "[^"]+" failed', "schema", "consequence"),  # follows the cause above
    (r'Loading "[^"]+" module failed', "schema", "missing-dependency"),
    (r'Node "[^"]+" not found as a child|Unknown (element|member)', "schema", "unknown-node"),
    (r"Invalid identityref", "semantic", "unknown-identity"),
    (r"Data for both cases|both cases", "semantic", "choice-conflict"),
    (r"Invalid leafref|Invalid instance-identifier|required instance", "semantic", "reference"),
    (r"When condition", "semantic", "when"),
    (r"Must condition", "semantic", "must"),
    (r"missing its key", "semantic", "missing-key"),  # an entry without key is malformed
    (r"Mandatory node|Mandatory choice|Too few", "collection", "missing-mandatory"),
    (r"range|bounds|does not match|Invalid .*value|Invalid type|length", "semantic", "type"),
]
UNITS = {
    "s": {"s", "sec", "second", "seconds"},
    "cs": {"centiseconds", "centisecond", "centi-seconds", "cs"},
    "ms": {"ms", "millisecond", "milliseconds"},
    "bytes": {"byte", "bytes", "octets"},
    "bps": {"bps", "bits/second", "bits per second", "bits-per-second"},
}


def file_digest(path: Path) -> str:
    """Digest of a text file independent of the checkout's line endings."""
    return digest(path.read_bytes().replace(b"\r\n", b"\n"))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def classify(line: str) -> tuple[str, str]:
    return next(((k, c) for rx, k, c in CLASSES if re.search(rx, line)), ("other", "other"))


# ---------------------------------------------------------------- build (needs App code)


def app_outputs() -> dict[str, dict]:
    sys.path[:0] = [str(ROOT), str(ROOT / "tests/unit")]
    from types import SimpleNamespace

    import test_topology_campus_wan as campus

    from src.apps.topology.backend.model.builder import build_topology, device_node_ids
    from src.apps.topology.backend.model.device_control import project_device_control
    from src.apps.topology.backend.model.device_ip import enrich_device_ip
    from src.apps.topology.backend.model.device_state import project_device_state
    from src.apps.topology.backend.model.inventory import project_inventory

    out, devices, observed = {}, {}, {}
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
        if device not in ("br1", "dist1"):
            continue
        observation = {
            "data": {
                "facts": outcome["facts"],
                "capabilities": outcome["capabilities"],
                "fact_metadata": outcome["fact_metadata"],
                "platform": outcome["platform"],
            }
        }
        observed[device] = (spec["management"], observation)
        out[f"{device}-device"] = enrich_device_ip(project_device_state(observation), observation)[
            "tree"
        ]
        control = project_device_control(observation)
        for rib in control["routing"]:
            out[f"{device}-routing-{rib.get('vrf')}"] = rib["tree"]
        out[f"{device}-lldp"] = control["lldp"]["tree"]
    out["campus-topology"] = build_topology(devices, "urn:netops:topology:campus:")[0]
    ids = device_node_ids(devices, "urn:netops:topology:campus:")
    for device, (address, observation) in observed.items():
        out[f"{device}-inventory"] = project_inventory(observation, ids[address])["tree"]
    return out


def build() -> None:
    # App trees with content, and the offline research instances of the VSR lab (routes:
    # the App does not yet name RIBs, so it emits no route).
    trees = {k: v for k, v in app_outputs().items() if any(v.values())}
    for name in ("vsr-1-routes", "vsr-1-l2.partial"):
        trees[f"research-{name}"] = json.loads((INSTANCES / f"{name}.json").read_text("utf-8"))
    specs, files = [], {}

    def instance(name, tree, mode, expect, note):
        files[f"{name}.json"] = tree
        specs.append(
            {
                "name": name,
                "kind": "instance",
                "data": f"{name}.json",
                "mode": mode,
                "expect": expect,
                "note": note,
            }
        )

    # Positive: App output parses, resolves references, and its complete-datastore check
    # fails only on what was not collected.
    for name, tree in trees.items():
        instance(name, tree, "get", "pass", "App output: hierarchy, types, identities")
        instance(name, tree, "data", "pass", "App output: references resolve (RFC 8342 §5.3)")
        instance(
            name,
            tree,
            "strict",
            "complete-or-collection",
            "complete-datastore check: native complete, or only unobserved nodes missing",
        )

    def mutate(base, name, change, mode, expect, note):
        tree = copy.deepcopy(trees[base])
        change(tree)
        instance(name, tree, mode, expect, note)

    def interfaces(t):
        return t["ietf-interfaces:interfaces"]["interface"]

    def vrrp(t):
        for i in interfaces(t):
            for inst in (
                i.get("ietf-ip:ipv4", {}).get("ietf-vrrp:vrrp", {}).get("vrrp-instance", [])
            ):
                return inst
        raise ValueError("no VRRP instance in the App output")

    def first_route(t):
        routes = t["ietf-routing:routing"]["ribs"]["rib"][0]["routes"]["route"]
        return next(
            r for r in routes if "ietf-ipv4-unicast-routing:next-hop-address" in r["next-hop"]
        )

    mutate(
        "br1-device",
        "custom-field",
        lambda t: interfaces(t)[0].update({"collected-at": "2026-10-03T00:00:00Z"}),
        "get",
        "schema/unknown-node",
        "a project field next to standard ones is not a model node",
    )
    mutate(
        "br1-device",
        "unknown-interface-type",
        lambda t: interfaces(t)[0].update(type="iana-if-type:notAnInterfaceType"),
        "get",
        "semantic/unknown-identity",
        "identity that iana-if-type does not define",
    )
    mutate(
        "br1-device",
        "vrrp-priority-out-of-range",
        lambda t: vrrp(t).update(priority=300),
        "get",
        "semantic/type",
        "RFC 8347 priority is uint8 1..254",
    )
    mutate(
        "research-vsr-1-routes",
        "next-hop-two-cases",
        lambda t: first_route(t)["next-hop"].update({"special-next-hop": "blackhole"}),
        "data",
        "semantic/choice-conflict",
        "next-hop-options: simple-next-hop and special-next-hop at once",
    )
    mutate(
        "research-vsr-1-routes",
        "next-hop-interface-of-other-device",
        lambda t: first_route(t)["next-hop"].update({"outgoing-interface": "Ethernet2/0"}),
        "data",
        "semantic/reference",
        "outgoing-interface names an interface outside this device tree (DIST1's Ethernet2/0)",
    )

    def protocol(t, kind):
        cpp = t["ietf-routing:routing"]["control-plane-protocols"]["control-plane-protocol"]
        return next(p for p in cpp if p["type"] == kind)

    def ospf_interface(t):
        area = protocol(t, "ietf-ospf:ospfv2")["ietf-ospf:ospf"]["areas"]["area"][0]
        return next(i for i in area["interfaces"]["interface"] if "neighbors" in i)

    mutate(
        "br1-routing-default",
        "ospf-interface-of-other-device",
        lambda t: ospf_interface(t).update(name="Ethernet2/0"),
        "data",
        "semantic/reference",
        "an RFC 9129 OSPF interface names an interface outside this device tree",
    )
    mutate(
        "br1-routing-default",
        "ospf-neighbor-state-cli-text",
        lambda t: ospf_interface(t)["neighbors"]["neighbor"][0].update(state="FULL/DR"),
        "get",
        "semantic/type",
        "the CLI state column is not an RFC 9129 nbr-state-type value",
    )
    mutate(
        "br1-routing-default",
        "bgp-peer-type-cli-text",
        lambda t: protocol(t, "ietf-bgp:bgp")["ietf-bgp:bgp"]["neighbors"]["neighbor"][0].update(
            {"peer-type": "ibgp"}
        ),
        "get",
        "semantic/type",
        "BGP peer-type is internal/external, not the CLI's iBGP shorthand",
    )
    mutate(
        "campus-topology",
        "link-to-tp-of-other-network",
        lambda t: t["ietf-network:networks"]["network"][0]["ietf-network-topology:link"][0][
            "ietf-simap-topology:tp"
        ][0].update({"node-ref": "urn:netops:topology:campus:device:not-in-this-network"}),
        "data",
        "semantic/reference",
        "a link end names a node that is not in the link's network",
    )
    CASES.mkdir(parents=True, exist_ok=True)
    for old in CASES.glob("*.json"):
        old.unlink()
    for name, tree in files.items():
        (CASES / name).write_text(
            json.dumps(tree, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
        )
    # Real catalog modules with real defects; the schema check compiles their closure.
    specs += [
        {
            "name": "bgp-ls-missing-import",
            "kind": "schema",
            "file": "ietf-bgp-ls@2026-06-29.yang",
            "expect": "schema/missing-dependency",
            "note": "BGP-LS YANG -04 imports iana-bgp-types; no BGP model version defines it",
        },
        {
            "name": "flexi-grid-pinned-old-te",
            "kind": "schema",
            "file": "ietf-flexi-grid-media-channel@2021-02-12.yang",
            "expect": "schema/wrong-revision",
            "note": "expired draft pins ietf-te revision-date 2019-02-15, which no source keeps",
        },
        {
            "name": "oam-mount-point-on-anydata",
            "kind": "schema",
            "file": "ietf-connectionless-oam@2019-04-16.yang",
            "expect": "schema/other",
            "note": "RFC 8532: mount-point on anydata; RFC 8528 §3.1: container/list only",
        },
        {
            "name": "vrrp-interval-seconds-to-centiseconds",
            "kind": "mapping",
            "unit": "seconds",
            "path": VRRP + "/advertise-interval-centi-sec",
            "expect": "semantic/unit-mismatch",
            "note": "a seconds value mapped onto RFC 8347's centisecond node",
        },
        {
            "name": "vrrp-interval-seconds",
            "kind": "mapping",
            "unit": "seconds",
            "path": VRRP + "/advertise-interval-sec",
            "expect": "pass",
            "note": "same value onto the VRRPv2 seconds node",
        },
    ]
    # M1-7: the same snapshots as RFC 9195 instance data files.
    observed = json.loads(
        (ROOT / "tests/fixtures/topology/campus-wan/manifest.json").read_text("utf-8")
    )
    stamp = max(o["observed_at"] for o in observed["observations"])
    # The research L2 instance carries an inline RFC 8525 library (RFC 9195 §2.1.1), the
    # method required when a closure holds two revisions of one module.
    for name, inline in (
        ("campus-topology", False),
        ("br1-device", False),
        ("research-vsr-1-l2.partial", True),
    ):
        file = instance_data_file(f"netops-{name}", trees[name], stamp, inline)
        files[file] = json.loads((CASES / file).read_text(encoding="utf-8"))
        specs.append(
            {
                "name": f"rfc9195-{name}",
                "kind": "instance-data",
                "data": file,
                "mode": "data",
                "expect": "pass",
                "note": "RFC 9195 file: header rules, content-data against its own content-schema",
            }
        )
    bad = copy.deepcopy(files[next(f for f in files if f.startswith("netops-campus-topology@"))])
    header = bad["ietf-yang-instance-data:instance-data-set"]
    header["name"] = "netops-two-revisions"
    header["content-schema"]["module"].append("ietf-inet-types@2013-07-15")
    file = f"netops-two-revisions@{stamp.replace(':', '_')}.json"
    (CASES / file).write_text(
        json.dumps(bad, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    files[file] = bad
    specs.append(
        {
            "name": "rfc9195-two-revisions-simplified-inline",
            "kind": "instance-data",
            "data": file,
            "mode": "data",
            "expect": "schema/instance-data-header",
            "note": "RFC 9195 §2.1.2: multiple revisions of one module MUST NOT be listed",
        }
    )
    manifest = {
        "scope": "M1-6 cases: campus-wan App output, named mutations, real module defects",
        "files": {name: file_digest(CASES / name) for name in sorted(files)},
        "cases": specs,
    }
    (CASES / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )


# ---------------------------------------------------------------- check (stdlib + docker)


def revisions() -> tuple[dict, dict]:
    lock = json.loads((M1 / "sources.lock.json").read_text(encoding="utf-8"))
    decisions = json.loads((M1 / "decisions.json").read_text(encoding="utf-8"))["decisions"]
    decided = {r["file"]: r["decision"] for d in decisions for r in d.get("revisions", [])}
    by_name: dict[str, list[dict]] = {}
    for entry in lock["modules"]:
        by_name.setdefault(entry["module"], []).append(entry)
    return by_name, decided


def closure(
    files: list[str], by_name: dict, decided: dict, allowed: tuple = ("runtime-baseline",)
) -> list[str]:
    """Files plus imports: a pinned revision exactly, otherwise the newest allowed one."""
    out, pending = [], list(files)
    while pending:
        file = pending.pop()
        if file in out:
            continue
        out.append(file)
        for name, pinned, _ in dependencies((MODULES / file).read_bytes()):
            entries = sorted(by_name.get(name, []), key=lambda e: e["revision"])
            if pinned:
                entries = [e for e in entries if e["revision"] == pinned]
            else:
                entries = [e for e in entries if decided.get(e["file"]) in allowed] or entries
            if entries:
                pending.append(entries[-1]["file"])
    return sorted(out)


def used_modules(tree) -> set[str]:
    """Modules named by qualified member names and by identity values."""
    names = set()

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if ":" in key:
                    names.add(key.split(":")[0])
                walk(child)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and re.fullmatch(r"[a-z][\w.-]*:[\w.-]+", value):
            names.add(value.split(":")[0])

    walk(tree)
    return names


def yanglint(files: list[str], data: str | None, mode: str | None) -> dict:
    work = WORK / "run"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    for file in files:
        shutil.copy(MODULES / file, work / file)
    # Submodules are found through the search path; yanglint refuses them as input.
    files = [f for f in files if not SUBMODULE.search((MODULES / f).read_bytes())]
    args = ["-D", "-i", "-i", "-p", "/m"]
    if data:
        shutil.copy(CASES / data, work / data)
        args += ["-e"] + (["-t", mode] if mode != "strict" else [])
    cmd = (
        ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL"]
        + ["--security-opt", "no-new-privileges", "--user", "65534:65534"]
        + ["--mount", f"type=bind,source={work},target=/m,readonly", IMAGE]
        + args
        + [f"/m/{f}" for f in files]
        + ([f"/m/{data}"] if data else [])
    )
    run = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=600)
    lines = [x for x in (run.stdout + run.stderr).splitlines() if x.strip()]
    errors = [x for x in lines if x.startswith(("libyang err", "YANGLINT[E]"))]
    errors = [x for x in errors if not x.startswith("YANGLINT[E]: Failed to parse input")]
    warnings = [x for x in lines if x.startswith("libyang warn")]
    return {"exit": run.returncode, "errors": errors, "warnings": warnings}


def schema_files(tree: dict, by_name: dict, decided: dict) -> list[str]:
    """Baseline closure of the modules a tree uses."""
    roots = []
    for name in sorted(used_modules(tree)):
        entries = [e for e in by_name.get(name, []) if decided.get(e["file"]) == "runtime-baseline"]
        if entries:
            roots.append(max(entries, key=lambda e: e["revision"])["file"])
    return closure(roots, by_name, decided)


def yang_library(files: list[str]) -> dict:
    """RFC 8525 yang-library for an RFC 9195 inline content-schema.

    The newest revision of each module is implemented with every feature it and its
    submodules declare; older revisions that imports pin are import-only.
    """
    texts = {f: (MODULES / f).read_text(encoding="utf-8") for f in files}
    newest: dict[str, str] = {}
    for f in files:
        if not SUBMODULE.search(texts[f].encode()):
            name, rev = f[:-5].split("@")
            newest[name] = max(newest.get(name, ""), rev)
    implemented, import_only = [], []
    for f in sorted(files):
        if SUBMODULE.search(texts[f].encode()):
            continue
        name, rev = f[:-5].split("@")
        entry = {
            "name": name,
            "revision": rev,
            "namespace": re.search(r'\bnamespace\s+"?([^";\s]+)', texts[f])[1],
        }
        if rev != newest[name]:
            import_only.append(entry)
            continue
        subs = [
            s
            for inc, _, kind in dependencies(texts[f].encode())
            if kind == "include"
            for s in files
            if s.startswith(inc + "@")
        ]
        features = re.findall(r"^\s*feature\s+([\w.-]+)", texts[f], re.M)
        for s in subs:
            features += re.findall(r"^\s*feature\s+([\w.-]+)", texts[s], re.M)
        if features:
            entry["feature"] = sorted(set(features))
        if subs:
            entry["submodule"] = [
                {"name": s.split("@")[0], "revision": s[:-5].split("@")[1]} for s in subs
            ]
        implemented.append(entry)
    content = digest(json.dumps([implemented, import_only], sort_keys=True).encode())
    return {
        "ietf-yang-library:yang-library": {
            "module-set": [
                {"name": "content", "module": implemented, "import-only-module": import_only}
            ],
            "schema": [{"name": "content", "module-set": ["content"]}],
            "datastore": [{"name": "ietf-datastores:operational", "schema": "content"}],
            "content-id": content,
        }
    }


def instance_data_file(name: str, tree: dict, timestamp: str, inline: bool = False) -> str:
    """Write an RFC 9195 instance data file; returns its file name (§2 naming)."""
    by_name, decided = revisions()
    files = schema_files(tree, by_name, decided)
    modules = [f for f in files if not SUBMODULE.search((MODULES / f).read_bytes())]
    names = [f.split("@")[0] for f in modules]
    if len(set(names)) == len(names) and not inline:
        # simplified-inline: all features, one revision per module (RFC 9195 §2.1.2).
        schema = {"module": sorted(f[:-5] for f in modules)}
    else:
        schema = {"inline-yang-library": yang_library(files)}
    document = {
        "ietf-yang-instance-data:instance-data-set": {
            "name": name,
            "format-version": "2022-02-17",
            "includes-defaults": "explicit",
            "content-schema": schema,
            "description": [
                "Partial operational instance data (RFC 9195 §2): only observed values, "
                "no defaults inserted; mandatory/key nodes that were not observed are absent.",
                "Produced by the netops topology App from the campus-wan lab fixtures.",
            ],
            "datastore": "ietf-datastores:operational",
            "timestamp": timestamp,
            "content-data": tree,
        }
    }
    file = f"{name}@{timestamp.replace(':', '_')}.json"
    (CASES / file).write_text(
        json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    return file


def check_instance_data(case: dict, by_name: dict, decided: dict) -> tuple[list[str], list[str]]:
    """(header problems + libyang errors, files) for one RFC 9195 file."""
    document = json.loads((CASES / case["data"]).read_text(encoding="utf-8"))
    problems = []
    if list(document) != ["ietf-yang-instance-data:instance-data-set"]:
        problems.append("RFC 9195 §2: the file must hold exactly one instance data set")
    header = document.get("ietf-yang-instance-data:instance-data-set", {})
    name, stamp = header.get("name"), header.get("timestamp")
    expected = f"{name}@{stamp.replace(':', '_')}.json" if name and stamp else None
    if expected and case["data"] != expected:
        problems.append(f"RFC 9195 §2: file name should be {expected}")
    schema = header.get("content-schema", {})
    if "module" in schema:
        listed = schema["module"]
        if len({m.split("@")[0] for m in listed}) != len(listed):
            problems.append("RFC 9195 §2.1.2: one revision per module in simplified-inline")
        files = [f"{m}.yang" for m in listed]
    else:
        library = schema["inline-yang-library"]["ietf-yang-library:yang-library"]
        files = [
            f"{m['name']}@{m['revision']}.yang"
            for s in library["module-set"]
            for key in ("module", "import-only-module")
            for m in s.get(key, [])
        ]
        files += [
            f"{s['name']}@{s['revision']}.yang"
            for ms in library["module-set"]
            for m in ms["module"]
            for s in m.get("submodule", [])
        ]
        # The inline library must itself be valid RFC 8525 data.
        lib_files = schema_files(schema["inline-yang-library"], by_name, decided)
        (CASES / "_library.json").write_text(
            json.dumps(schema["inline-yang-library"]), encoding="utf-8", newline="\n"
        )
        try:
            problems += yanglint(lib_files, "_library.json", "data")["errors"]
        finally:
            (CASES / "_library.json").unlink()
    missing = [f for f in files if not (MODULES / f).exists()]
    if missing:
        problems.append(f"content-schema names modules outside the lock: {missing}")
        return problems, files
    # Submodules of declared modules come with them (simplified-inline lists modules only).
    for f in list(files):
        for inc, pinned, kind in dependencies((MODULES / f).read_bytes()):
            subs = sorted(e["file"] for e in by_name.get(inc, []) if kind == "include")
            subs = [s for s in subs if not pinned or s == f"{inc}@{pinned}.yang"]
            files += subs[-1:]
    (CASES / "_content.json").write_text(
        json.dumps(header["content-data"]), encoding="utf-8", newline="\n"
    )
    try:
        problems += yanglint(sorted(set(files)), "_content.json", case["mode"])["errors"]
    finally:
        (CASES / "_content.json").unlink()
    return problems, files


def topology_references(tree: dict) -> list[str]:
    """RFC 8345 node/tp/link references inside one snapshot.

    The tp-ref, node-ref and link-ref groupings (and the source/destination and
    supporting-* leafrefs) are require-instance false, so a YANG check accepts a dangling
    end. Inside one snapshot every reference must resolve; the rule reports the rest.
    """
    networks = tree.get("ietf-network:networks", {}).get("network", [])
    nodes, tps, links = set(), set(), set()
    for net in networks:
        nid = net["network-id"]
        for node in net.get("node", []):
            nodes.add((nid, node["node-id"]))
            for tp in node.get("ietf-network-topology:termination-point", []):
                tps.add((nid, node["node-id"], tp["tp-id"]))
        for link in net.get("ietf-network-topology:link", []):
            links.add((nid, link["link-id"]))
    bad = []

    def need(found, key, where):
        if key not in found:
            bad.append(f"Invalid leafref (rule, require-instance false in RFC 8345): {where} {key}")

    for net in networks:
        nid = net["network-id"]
        for node in net.get("node", []):
            for s in node.get("supporting-node", []):
                need(nodes, (s["network-ref"], s["node-ref"]), "supporting-node")
            for tp in node.get("ietf-network-topology:termination-point", []):
                for s in tp.get("supporting-termination-point", []):
                    need(tps, (s["network-ref"], s["node-ref"], s["tp-ref"]), "supporting-tp")
        for link in net.get("ietf-network-topology:link", []):
            for end in ("source", "destination"):
                e = link.get(end)
                if e:
                    need(nodes, (nid, e[f"{end[:6] if end == 'source' else 'dest'}-node"]), end)
            for end in link.get("ietf-simap-topology:tp", []):
                need(tps, (end["network-ref"], end["node-ref"], end["tp-ref"]), "simap tp")
            for s in link.get("supporting-link", []):
                need(links, (s["network-ref"], s["link-ref"]), "supporting-link")
    return bad


def normal_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    text = unit.strip().lower()
    return next((k for k, v in UNITS.items() if text in v), text)


def check() -> dict:
    manifest = json.loads((CASES / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        if file_digest(CASES / name) != expected:
            raise ValueError(f"case bytes changed: {name}; rebuild with --build")
    by_name, decided = revisions()
    nodes = {}
    import gzip

    for path in sorted((M1 / "inventory").glob("*.jsonl.gz")):
        if ".candidate." in path.name:
            continue
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                node = json.loads(line)
                nodes.setdefault(node["path"], node)
    image = subprocess.run(
        ["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    results = []
    for case in manifest["cases"]:
        if case["kind"] == "instance":
            tree = json.loads((CASES / case["data"]).read_text(encoding="utf-8"))
            roots = []
            for name in sorted(used_modules(tree)):
                entries = [
                    e for e in by_name.get(name, []) if decided.get(e["file"]) == "runtime-baseline"
                ]
                if entries:
                    roots.append(max(entries, key=lambda e: e["revision"])["file"])
            files = closure(roots, by_name, decided)
            run = yanglint(files, case["data"], case["mode"])
            if case["mode"] != "get":
                run["errors"] += topology_references(tree)
            found = [classify(x) for x in run["errors"]]
        elif case["kind"] == "instance-data":
            errors, files = check_instance_data(case, by_name, decided)
            run = {"errors": errors}
            found = [classify(x) for x in errors]
            found = [
                ("schema", "instance-data-header") if f == ("other", "other") else f for f in found
            ]
        elif case["kind"] == "schema":
            files = closure([case["file"]], by_name, decided)
            run = yanglint(files, None, None)
            found = [classify(x) for x in run["errors"]]
            if found and found[0] == ("other", "other"):
                found = [("schema", "other")]
        else:
            node = nodes.get(case["path"])
            node_unit = normal_unit(node.get("units")) if node else None
            ok = node is not None and node_unit == normal_unit(case["unit"])
            run = {"node_units": node.get("units") if node else None, "errors": []}
            if node is None:
                run["errors"] = ["mapping path is not a baseline node"]
                found = [("schema", "unknown-node")]
            elif not ok:
                run["errors"] = [f"unit {case['unit']!r} mapped to a node in {node.get('units')!r}"]
                found = [("semantic", "unit-mismatch")]
            else:
                found = []
        found = [f for f in found if f[1] != "consequence"]
        kinds = sorted({f"{k}/{c}" for k, c in found})
        if case["expect"] == "pass":
            met = not found
        elif case["expect"] == "complete-or-collection":
            # Complete (native) or only unobserved nodes (partial); nothing else.
            met = all(k == "collection" for k, _ in found)
        else:
            met = case["expect"] in kinds
        results.append(
            {
                "name": case["name"],
                "kind": case["kind"],
                "mode": case.get("mode"),
                "expect": case["expect"],
                "found": kinds,
                "met": met,
                "errors": run["errors"][:20],
                "warnings": len(run.get("warnings", [])),
                **(
                    {"modules": len(files)}
                    if case["kind"] != "mapping"
                    else {"node_units": run["node_units"]}
                ),
            }
        )
        mode = case.get("mode") or case["kind"]
        print(f"{'ok ' if met else 'BAD'} {case['name']:44} {mode:8} {case['expect']:28} {kinds}")
    report = {
        "scope": "M1-6 validator results; gap kinds per plan §8 M1",
        "engine": {"libyang_image": IMAGE, "image_id": image},
        "cases": results,
        "all_met": all(r["met"] for r in results),
    }
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="regenerate the cases from App output")
    args = parser.parse_args()
    if args.build:
        build()
    return 0 if check()["all_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

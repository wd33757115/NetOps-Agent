"""M1-1: lock the source of every catalog YANG module and everything it imports.

Order of authority for a module (and revision, when an import pins one):
1. the IANA YANG parameters registry file (published RFC and IANA-maintained modules);
2. the catalog document that defines it, extracted from <CODE BEGINS> blocks of the
   pinned RFC/Internet-Draft text;
3. the YangModels repository at the commit the research package already pins
   (IEEE published modules, older RFC revisions, draft-extracted modules).
Nothing is edited: a module that cannot be resolved is recorded with the reason.

Run: venv/Scripts/python.exe scripts/topology/m1_sources.py --fetch
Output: models/network-twin/m1/sources.lock.json (+ cache in .runtime/m1/cache).
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import textwrap
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "docs/network-digital-twin-ietf-model-catalog.json"
OUT = ROOT / "models/network-twin/m1/sources.lock.json"
CACHE = ROOT / ".runtime/m1/cache"
PATCHES = ROOT / "models/network-twin/m1/patches"
# Reviewed patches of an extracted module (exact source and output digests).
MODULE_PATCHES = (ROOT / "models/network-twin/patches/simap-03-syntax.json",)
IANA = "https://www.iana.org/assignments/yang-parameters/"
IANA_SHA256 = "2253c10758659349a8878f23ac36f4fdf5e880e7c2d467d141dbbd647db7a13e"  # catalog pin
YANGMODELS = (
    "https://raw.githubusercontent.com/YangModels/yang/0e28ed86b0a9070b7f15fd06ba4fa501723fac84/"
)
YANGMODELS_PATHS = (
    "standard/ietf/RFC/{name}@{revision}.yang",
    "standard/ieee/published/802.1/{name}.yang",
    "standard/ieee/published/802.3/{name}.yang",
    "standard/ieee/published/802/{name}.yang",
    "standard/ieee/published/1588/{name}.yang",
    "experimental/ietf-extracted-YANG-modules/{name}@{revision}.yang",
)
NS = {"i": "http://www.iana.org/assignments"}
# Documents that print a module without <CODE BEGINS> markers: the module is the whole
# section, cut as docs/ietf-model-audit/sources.lock.json does, to the same reviewed digest.
SIMAP_03 = "82b7e6c2adf8d5208e1cbc60555071bcb3d246426898b7e73d048728a5631df8"
SECTION_MODULES = {
    "draft-havel-nmop-simap-yang-03": (
        ("\n9.  YANG Module\n", "\n10.  Security Considerations\n"),
        {"ietf-simap-topology@2025-01-01.yang": SIMAP_03},
    ),
}
# IEEE publishes the switching detail the IETF models defer to (plan §3): every module in
# the pinned commit's standard/ieee/published/{802.1,802.3,802} directories.
# Imports defined outside the catalog: the document the importer cites, never a guess.
EXTERNAL_DOCUMENTS = {
    "ietf-http-server": (
        "draft-ietf-netconf-http-client-server-33",
        "ALTO OAM -18 cites I-D.ietf-netconf-http-client-server; -33 is in the RFC Editor queue",
    ),
}
# Why a module stays unresolved, when it was checked by hand.
UNRESOLVED_NOTES = {
    "iana-bgp-types": "imported by BGP-LS YANG -04 citing the BGP model draft, but neither "
    "draft-ietf-idr-bgp-model-20 nor -21 defines it (they define iana-bgp-afi-safi-types, "
    "-capabilities, -community-types, -notification, -rib-types): a defect of the importer",
    "ietf-l2vpn": "imported by the expired EVPN YANG -07; L2VPN YANG drafts expired too",
    "ietf-pseudowires": "imported by the expired EVPN YANG -07; pseudowire YANG draft expired",
}
IEEE_ROOTS = (
    "ieee802-dot1ab-lldp",
    "ieee802-dot1ab-types",
    "ieee802-dot1ae-pry",
    "ieee802-dot1ae-secy",
    "ieee802-dot1as-ftt",
    "ieee802-dot1as-gptp",
    "ieee802-dot1as-hs",
    "ieee802-dot1ax-drni",
    "ieee802-dot1ax-linkagg",
    "ieee802-dot1ax-types",
    "ieee802-dot1cb-frer-types",
    "ieee802-dot1cb-frer",
    "ieee802-dot1cb-mask-and-match",
    "ieee802-dot1cb-stream-identification-types",
    "ieee802-dot1cb-stream-identification",
    "ieee802-dot1cs-lrp",
    "ieee802-dot1dc-ats-if",
    "ieee802-dot1dc-cbsa-if",
    "ieee802-dot1dc-gfqos",
    "ieee802-dot1dc-preemption-if",
    "ieee802-dot1dc-psfp-sys",
    "ieee802-dot1dc-sched-if",
    "ieee802-dot1q-ats-bridge",
    "ieee802-dot1q-ats",
    "ieee802-dot1q-bridge",
    "ieee802-dot1q-cbsa-bridge",
    "ieee802-dot1q-cbsa",
    "ieee802-dot1q-cfm-alarm",
    "ieee802-dot1q-cfm-bridge",
    "ieee802-dot1q-cfm-types",
    "ieee802-dot1q-cfm",
    "ieee802-dot1q-cnc-config",
    "ieee802-dot1q-congestion-isolation-bridge",
    "ieee802-dot1q-congestion-isolation",
    "ieee802-dot1q-lldp-basic-tlv",
    "ieee802-dot1q-lldp-ci-tlv",
    "ieee802-dot1q-lldp-cn-tlv",
    "ieee802-dot1q-lldp-dcbx-tlv",
    "ieee802-dot1q-lldp-evb-tlv",
    "ieee802-dot1q-lldp-pbbn-aa-tlv",
    "ieee802-dot1q-lldp-tr-tlv",
    "ieee802-dot1q-mstp-bridge",
    "ieee802-dot1q-mstp",
    "ieee802-dot1q-pb",
    "ieee802-dot1q-preemption-bridge",
    "ieee802-dot1q-preemption",
    "ieee802-dot1q-psfp-bridge",
    "ieee802-dot1q-psfp",
    "ieee802-dot1q-rstp-bridge",
    "ieee802-dot1q-rstp",
    "ieee802-dot1q-sched-bridge",
    "ieee802-dot1q-sched",
    "ieee802-dot1q-stream-filters-gates-bridge",
    "ieee802-dot1q-stream-filters-gates",
    "ieee802-dot1q-tpmr",
    "ieee802-dot1q-tsn-types",
    "ieee802-dot1q-types",
    "ieee802-dot1q-vlan-bridge",
    "ieee802-dot1x-eapol",
    "ieee802-dot1x-types",
    "ieee802-dot1x",
    "ieee802-ethernet-interface-half-duplex",
    "ieee802-ethernet-interface",
    "ieee802-ethernet-link-oam",
    "ieee802-ethernet-lldp",
    "ieee802-ethernet-mac-merge",
    "ieee802-ethernet-phy-type",
    "ieee802-ethernet-phy",
    "ieee802-ethernet-pon",
    "ieee802-ethernet-pse-2",
    "ieee802-ethernet-pse",
    "ieee802-ethertype",
    "ieee802-types",
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str, offline: bool) -> bytes | None:
    """One cached GET; a 404 is remembered so offline replays stay identical."""
    name = re.sub(r"[^\w.@-]+", "_", url.split("://", 1)[1])[-180:]
    path = CACHE / name
    missing = path.with_suffix(path.suffix + ".404")
    if path.exists():
        return path.read_bytes()
    if missing.exists() or offline:
        return None
    for attempt in range(3):
        try:
            with urlopen(
                Request(url, headers={"User-Agent": "netops-agent-m1"}), timeout=60
            ) as response:
                data = response.read()
            path.write_bytes(data)
            return data
        except HTTPError as exc:
            if exc.code == 404:
                missing.write_bytes(b"")
                return None
            time.sleep(2 + attempt * 3)
        except OSError:
            time.sleep(2 + attempt * 3)
    raise RuntimeError(f"cannot fetch {url}")


def page_clean(text: str) -> str:
    """Drop RFC/I-D pagination: form feeds, running headers and footers."""
    kept = []
    for line in text.replace("\f", "\n").splitlines():
        if re.match(r"^(RFC \d+|Internet-Draft)\s{2,}.*\S\s*$", line):
            continue
        if re.match(r"^\S.*\[Page \d+\]\s*$", line):
            continue
        kept.append(line)
    return "\n".join(kept)


def apply_patch(block: str, spec: dict) -> str:
    """Apply a reviewed patch: exact input digest, unique contexts, exact output digest."""
    if digest(block.encode("utf-8")) != spec["block_sha256"]:
        raise ValueError(f"{spec['file']}: CODE block differs from the reviewed patch input")
    patched = block
    for edit in spec["edits"]:
        if patched.count(edit["before"]) != 1:
            raise ValueError(f"{spec['file']}: patch context not unique: {edit['reason']}")
        patched = patched.replace(edit["before"], edit["after"], 1)
    if digest(patched.encode("utf-8")) != spec["patched_block_sha256"]:
        raise ValueError(f"{spec['file']}: patched block differs from the reviewed output")
    diff = difflib.unified_diff(
        block.splitlines(keepends=True),
        patched.splitlines(keepends=True),
        fromfile=f"original/{spec['document']}/{spec['file']}",
        tofile=f"patched/{spec['file']}",
        n=1,
    )
    (PATCHES / f"{spec['file'][:-5]}.patch").write_text("".join(diff), encoding="utf-8")
    return patched


def patch_module(body: bytes, spec: dict, provenance: dict, path: Path) -> tuple[bytes, dict]:
    if digest(body) != spec["source_sha256"]:
        raise ValueError(f"{spec['module']}: source differs from the reviewed patch baseline")
    text = body.decode("utf-8")
    for edit in spec["edits"]:
        if text.count(edit["before"]) != 1:
            raise ValueError(f"{spec['module']}: patch context not unique: {edit['reason']}")
        text = text.replace(edit["before"], edit["after"], 1)
    patched = text.encode("utf-8")
    if digest(patched) != spec["patched_sha256"]:
        raise ValueError(f"{spec['module']}: patched module differs from the reviewed output")
    return patched, {
        **provenance,
        "patch": path.relative_to(ROOT).as_posix(),
        "original_module_sha256": digest(body),
    }


def extract(
    text: str, section: tuple[str, str] | None = None, patches: dict | None = None
) -> tuple[dict[str, bytes], list[dict], dict[str, str]]:
    """({name@revision.yang: bytes}, defects, {file: patch}) for a document's CODE blocks.

    A block that names a .yang file but has no module/submodule statement is a defect of
    the published text. It is always reported; only a reviewed patch from PATCHES repairs
    it, and the lock discloses the patch. `section` (start, end headings) takes a module
    printed without CODE markers.
    """
    found, defects, patched = {}, [], {}
    blocks = re.findall(r"<CODE BEGINS>([^\n]*)\n(.*?)<CODE ENDS>", page_clean(text), re.S)
    if section:
        blocks = [("", page_clean(text.split(section[0], 1)[1].split(section[1], 1)[0]))]
    for label, block in blocks:
        named = re.search(r"([\w.-]+@\d{4}-\d{2}-\d{2}\.yang)", label)
        start = re.search(r"^ *(module|submodule) ([\w.-]+) *\{", block, re.M)
        if start is None and named:
            defects.append(
                {"file": named[1], "defect": "CODE block has no module/submodule statement"}
            )
        if named and named[1] in (patches or {}):
            block = apply_patch(block, patches[named[1]])
            patched[named[1]] = patches[named[1]]["name"]
            start = re.search(r"^ *(module|submodule) ([\w.-]+) *\{", block, re.M)
        if start is None:
            continue  # tree diagrams, examples in other formats
        body = textwrap.dedent(block[start.start() :])
        end = body.rfind("\n}")
        body = (body[: end + 2] if end >= 0 else body).rstrip() + "\n"
        revision = re.search(r"\brevision\s+[\"']?(\d{4}-\d{2}-\d{2})", body)
        name = start[2]
        found[f"{name}@{revision[1] if revision else 'unknown'}.yang"] = body.encode("utf-8")
    return found, defects, patched


def header(data: bytes) -> tuple[str, str, str]:
    """(module|submodule, name, latest revision) from the module text."""
    text = data.decode("utf-8", "replace")
    kind, name = re.search(r"^\s*(module|submodule)\s+([\w.-]+)", text, re.M).groups()
    revision = re.search(r"\brevision\s+[\"']?(\d{4}-\d{2}-\d{2})", text)
    return kind, name, revision[1] if revision else "unknown"


TOKEN = re.compile(r"""\s+|//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|'[^']*'|[{};]|[^\s{};"']+""", re.S)


def dependencies(data: bytes) -> list[tuple[str, str | None, str]]:
    """(name, pinned revision or None, import|include) for each import/include statement.

    Tokenised so that statements quoted inside descriptions (examples) are not taken.
    """
    tokens = [
        t
        for t in TOKEN.findall(data.decode("utf-8", "replace"))
        if t.strip() and not t.startswith(("//", "/*"))
    ]
    deps = []
    for i, token in enumerate(tokens):
        if token not in ("import", "include") or (i and tokens[i - 1] not in "{};"):
            continue
        name, pinned = tokens[i + 1].strip("\"'"), None
        if tokens[i + 2] == "{":
            depth, j = 1, i + 3
            while depth:
                if tokens[j] == "{":
                    depth += 1
                elif tokens[j] == "}":
                    depth -= 1
                elif tokens[j] == "revision-date" and depth == 1:
                    pinned = tokens[j + 1].strip("\"'")
                j += 1
        deps.append((name, pinned, token))
    return deps


def registry(offline: bool) -> dict[str, dict]:
    data = fetch(IANA + "yang-parameters.xml", offline)
    if data is None or digest(data) != IANA_SHA256:
        raise ValueError("IANA registry differs from the catalog pin; review before relocking")
    records = {}
    for record in ET.fromstring(data).iter(f"{{{NS['i']}}}record"):
        name = record.findtext("i:name", namespaces=NS)
        file = record.findtext("i:file", namespaces=NS)
        refs = [x.get("data") for x in record.findall("i:xref", NS) if x.get("data")]
        if name and file:
            records[name] = {
                "file": file,
                "maintained_by_iana": record.findtext("i:iana", namespaces=NS) == "Y",
                "references": refs,
            }
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fetch", action="store_true", help="allow downloads (otherwise cache only)"
    )
    args = parser.parse_args()
    offline = not args.fetch
    CACHE.mkdir(parents=True, exist_ok=True)
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    iana = registry(offline)
    defined_in = {}
    documents = {doc["id"]: doc for doc in catalog["documents"]}
    patch_specs = [
        {**json.loads(path.read_text(encoding="utf-8")), "name": path.relative_to(ROOT).as_posix()}
        for path in sorted(PATCHES.glob("*.json"))
    ]
    for doc in catalog["documents"]:
        for name in doc.get("modules") or []:
            defined_in.setdefault(name, []).append(doc)

    locked: dict[str, dict] = {}  # file -> entry
    unresolved: dict[tuple, dict] = {}
    extracted_cache: dict[str, tuple[dict[str, bytes], list[dict], dict[str, str]]] = {}
    source_defects: list[dict] = []

    def document_text(doc: dict) -> str | None:
        if doc.get("source_text"):
            return doc["source_text"]
        if doc["id"].startswith("RFC"):
            return f"https://www.rfc-editor.org/rfc/{doc['id'].lower()}.txt"
        if re.fullmatch(r"draft-[\w.-]+-\d{2}", doc["id"]):
            return f"https://www.ietf.org/archive/id/{doc['id']}.txt"
        return None

    def from_document(name: str, revision: str | None, by: str, only: str | None = None):
        docs = [documents[only]] if only else list(defined_in.get(name, []))
        importer = locked.get(by, {}).get("document")
        if importer:  # a document often defines sibling modules the catalog does not list
            docs.append(documents.get(importer, {"id": importer}))
        if name in EXTERNAL_DOCUMENTS:
            docs.append({"id": EXTERNAL_DOCUMENTS[name][0]})
        for doc in docs:
            url = document_text(doc)
            if not url:
                continue
            if url not in extracted_cache:
                text = fetch(url, offline)
                section, reviewed = SECTION_MODULES.get(doc["id"], (None, {}))
                patches = {s["file"]: s for s in patch_specs if s["document"] == doc["id"]}
                extracted_cache[url] = (
                    extract(text.decode("utf-8", "replace"), section, patches)
                    if text
                    else ({}, [], {})
                )
                for file, expected in reviewed.items():
                    got = extracted_cache[url][0].get(file)
                    if got is None or digest(got) != expected:
                        extracted_cache[url][0].pop(file, None)
                        source_defects.append(
                            {
                                "document": doc["id"],
                                "url": url,
                                "file": file,
                                "defect": "section extraction differs from the reviewed digest",
                            }
                        )
                pinned = doc.get("source_sha256")
                if text and pinned and digest(text) != pinned:
                    source_defects.append(
                        {
                            "document": doc["id"],
                            "url": url,
                            "defect": "text differs from the catalog source_sha256",
                            "catalog_sha256": pinned,
                            "fetched_sha256": digest(text),
                        }
                    )
                for defect in extracted_cache[url][1]:
                    source_defects.append({"document": doc["id"], "url": url, **defect})
            for file, body in extracted_cache[url][0].items():
                if file.split("@")[0] == name and (
                    revision is None or file == f"{name}@{revision}.yang"
                ):
                    source = fetch(url, offline)
                    return (
                        file,
                        body,
                        {
                            "origin": "document",
                            "document": doc["id"],
                            **(
                                {"patch": extracted_cache[url][2][file]}
                                if file in extracted_cache[url][2]
                                else {}
                            ),
                            "url": url,
                            "source_sha256": digest(source),
                            "catalog_sha256_match": doc.get("source_sha256")
                            in (None, digest(source)),
                            **(
                                {
                                    "outside_catalog": EXTERNAL_DOCUMENTS.get(
                                        name, (None, f"defined beside {by} in {doc['id']}")
                                    )[1]
                                }
                                if doc["id"] not in documents
                                else {}
                            ),
                        },
                    )
        return None

    def from_iana(name: str, revision: str | None):
        record = iana.get(name)
        if not record or (revision and record["file"] != f"{name}@{revision}.yang"):
            return None
        data = fetch(IANA + record["file"], offline)
        if data is None:
            return None
        return (
            record["file"],
            data,
            {
                "origin": "iana-registry",
                "url": IANA + record["file"],
                "references": record["references"],
                "maintained_by_iana": record["maintained_by_iana"],
            },
        )

    def from_yangmodels(name: str, revision: str | None):
        for pattern in YANGMODELS_PATHS:
            if "{revision}" in pattern and revision is None:
                continue
            url = YANGMODELS + pattern.format(name=name, revision=revision)
            data = fetch(url, offline)
            if data is None:
                continue
            kind, found, latest = header(data)
            if found != name or (revision and latest != revision):
                continue
            return f"{name}@{latest}.yang", data, {"origin": "yangmodels", "url": url}
        return None

    pending = [(name, None, "catalog", None) for name in sorted(defined_in)]
    pending += [(name, None, "ieee-published", None) for name in IEEE_ROOTS]
    # Every current catalog document's own revision too (e.g. a draft updating an RFC
    # module is a candidate revision of it); obsoleted RFCs and expired drafts stay text.
    pending += [
        (name, None, f"catalog:{doc['id']}", doc["id"])
        for name, docs in sorted(defined_in.items())
        for doc in docs
        if not doc.get("obsoleted_by") and not re.search(r"Expired|Replaced", doc.get("status", ""))
    ]
    seen = set()
    while pending:
        name, revision, by, only = pending.pop(0)
        if (name, revision, only) in seen:
            for entry in locked.values():
                if entry["module"] == name and (revision is None or entry["revision"] == revision):
                    entry["required_by"] = sorted(set(entry["required_by"]) | {by})
            continue
        seen.add((name, revision, only))
        if only:
            found = from_document(name, None, by, only)
            if found is None or found[0] in locked:
                if found:
                    locked[found[0]]["required_by"] = sorted(
                        set(locked[found[0]]["required_by"]) | {by}
                    )
                continue
        else:
            found = (
                from_iana(name, revision)
                or from_document(name, revision, by)
                or from_yangmodels(name, revision)
            )
        if found is None:
            unresolved[(name, revision)] = {
                "module": name,
                "revision": revision,
                "required_by": [by],
                "reason": f"import pins revision-date {revision}, found in no source"
                if revision
                else UNRESOLVED_NOTES.get(
                    name, "no IANA file, catalog document block or pinned YangModels file"
                ),
            }
            continue
        file, body, provenance = found
        for path in MODULE_PATCHES:
            spec = json.loads(path.read_text(encoding="utf-8"))
            if spec["module"] == file:
                body, provenance = patch_module(body, spec, provenance, path)
        kind, found_name, latest = header(body)
        entry = locked.setdefault(
            file,
            {
                "file": file,
                "module": found_name,
                "revision": latest,
                "kind": kind,
                "in_catalog": found_name in defined_in,
                "catalog_documents": sorted(d["id"] for d in defined_in.get(found_name, [])),
                **provenance,
                "module_sha256": digest(body),
                "required_by": [],
            },
        )
        if entry["required_by"]:
            entry["required_by"] = sorted(set(entry["required_by"]) | {by})
            continue  # already locked from a higher authority; keep that file
        entry["required_by"] = [by]
        (CACHE / "modules").mkdir(exist_ok=True)
        (CACHE / "modules" / file).write_bytes(body)
        for dep, pinned, _ in dependencies(body):
            pending.append((dep, pinned, file, None))

    resolved_names = {e["module"] for e in locked.values()}
    still_missing = [
        u for (n, r), u in unresolved.items() if not (r is None and n in resolved_names)
    ]
    lock = {
        "scope": "M1 source lock of catalog modules and imports; provenance, not a schema",
        "catalog": {
            "file": "docs/network-digital-twin-ietf-model-catalog.json",
            "sha256": digest(CATALOG.read_bytes()),
        },
        "iana_registry": {"url": IANA + "yang-parameters.xml", "sha256": IANA_SHA256},
        "yangmodels_commit": YANGMODELS.rstrip("/").rsplit("/", 1)[1],
        "counts": {
            "catalog_modules": len(defined_in),
            "locked_files": len(locked),
            "catalog_modules_locked": len(
                {e["module"] for e in locked.values() if e["in_catalog"]}
            ),
            "dependencies_only": len({e["module"] for e in locked.values() if not e["in_catalog"]}),
            "ieee_published_locked": len(
                {e["module"] for e in locked.values() if "ieee-published" in e["required_by"]}
            ),
            "unresolved": len(still_missing),
            "source_defects": len(source_defects),
        },
        "modules": sorted(locked.values(), key=lambda e: e["file"]),
        "unresolved": sorted(still_missing, key=lambda u: (u["module"], u["revision"] or "")),
        "patches": [
            {
                k: spec[k]
                for k in ("name", "document", "file", "block_sha256", "patched_block_sha256")
            }
            for spec in patch_specs
        ],
        "source_defects": sorted(source_defects, key=lambda d: (d["document"], d.get("file", ""))),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(lock, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(lock["counts"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

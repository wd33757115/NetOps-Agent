"""M5 offline acceptance: the VPN/overlay RFCs' own JSON examples against the fixed modules.

Model first (plan §8 M5, VPN/EVPN/VXLAN/DC fabric): every JSON instance example of the
L3NM (RFC 9182), L2NM with Ethernet segments (RFC 9291) and the L2 topology (RFC 8944)
is extracted from the locked RFC text (page headers removed, RFC 8792 '\\' unfolding),
and validated by libyang 5.8.6 in operational mode against the M1 baseline closure.

Most examples are RESTCONF request bodies. A POST body is a child resource of the target
URI (RFC 8040 §4.4.1), so the body is nested at that URI, with the list keys the URI
gives, before validation. Nothing inside an example is corrected; a body without a
target URI stays a fragment and is reported as such. RFC 8542 (DC fabric) has no
instance example; its instance is built from the lab and validated in the M5 cases.

An appendix often builds one datastore over several figures (a POST sequence, or an
Ethernet segment shown before the service that references it). An example that fails
only on references is checked again over the merge of the same RFC's earlier examples
that validated; both results are kept.

Run: venv/Scripts/python.exe scripts/topology/m5_examples.py
Output: models/network-twin/m5/examples/*.json and models/network-twin/m5/examples.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts/topology")]
from m1_cases import CASES, classify, closure, revisions, used_modules, yanglint  # noqa: E402
from m1_mapping import inventory  # noqa: E402
from m1_sources import CACHE, digest, page_clean  # noqa: E402
from m2_examples import examples, unfold  # noqa: E402

OUT = ROOT / "models/network-twin/m5"
DOCUMENTS = ("RFC9182", "RFC9291", "RFC8944")
TARGET = re.compile(r"^\s*(?:POST|PUT|PATCH):\s*(\S+)")


def target_uri(lines: list[str], start: int) -> str | None:
    """The RESTCONF target written in the few lines above an example (RFC 8792 folded)."""
    window = lines[max(0, start - 9) : start]
    for i, line in enumerate(window):
        if TARGET.match(line):
            uri = unfold([x for x in window[i:] if x.strip()][:3])[0]
            return TARGET.match(uri)[1]
    return None


def nest(uri: str, body: dict, nodes: dict) -> dict:
    """Place a POST body at its target (RFC 8040 §3.5.3 resource identifier)."""
    segments = [unquote(s) for s in uri.split("/restconf/data/", 1)[1].strip("/").split("/")]
    path, module = "", None
    steps = []
    for segment in segments:
        name, _, keys = segment.partition("=")
        if ":" in name:
            module, member = name.split(":", 1)
            qualified = name
        else:
            member, qualified = name, name
        path += f"/{qualified}"
        node = nodes.get(path)
        if node is None:
            raise ValueError(f"target {path} is not a schema node")
        values = keys.split(",") if keys else []
        key_names = (node.get("key") or "").split()
        if len(values) != len(key_names):
            raise ValueError(f"target {path} needs keys {key_names}")
        steps.append((qualified if not steps else member, dict(zip(key_names, values)), node))
    (child,) = body.items()
    child_module, child_member = child[0].split(":", 1)
    if steps and child_member == steps[-1][0].split(":")[-1] and steps[-1][2]["kind"] != "list":
        # The body names the target itself (not a child, RFC 8040 §4.4.1): place it there.
        steps.pop()
    inner = {child_member if child_module == module else child[0]: child[1]}
    for member, keys, node in reversed(steps):
        content = {**keys, **inner} if node["kind"] == "list" else inner
        inner = {member: [content] if node["kind"] == "list" else content}
    return inner


def simple(value, module=None):
    """RFC 7951 member names, qualified only where the module changes (§4)."""
    if isinstance(value, list):
        return [simple(v, module) for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for member, child in value.items():
        own, _, name = member.rpartition(":")
        own = own or module
        out[name if own == module else member] = simple(child, own)
    return out


def merge(base: dict, extra: dict, nodes: dict, path: str = "") -> dict:
    """RFC 7951 trees merged; list entries with equal keys merge, the later one wins."""
    out = dict(base)
    for member, value in extra.items():
        here = f"{path}/{member}"
        node = nodes.get(here, {})
        if member not in out:
            out[member] = value
        elif isinstance(value, dict) and isinstance(out[member], dict):
            out[member] = merge(out[member], value, nodes, here)
        elif isinstance(value, list) and node.get("kind") == "list":
            keys = (node.get("key") or "").split()
            entries = list(out[member])
            for entry in value:
                same = next(
                    (
                        i
                        for i, e in enumerate(entries)
                        if all(e.get(k) == entry.get(k) for k in keys)
                    ),
                    None,
                )
                if same is None:
                    entries.append(entry)
                else:
                    entries[same] = merge(entries[same], entry, nodes, here)
            out[member] = entries
        else:
            out[member] = value
    return out


def validate(data: dict, by_name: dict, decided: dict) -> dict:
    roots = []
    for module in sorted(used_modules(data)):
        entries = [
            e for e in by_name.get(module, []) if decided.get(e["file"]) == "runtime-baseline"
        ]
        if entries:
            roots.append(max(entries, key=lambda e: e["revision"])["file"])
    body = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    (CASES / "_example.json").write_text(body, encoding="utf-8", newline="\n")
    try:
        run = yanglint(closure(roots, by_name, decided), "_example.json", "data")
    finally:
        (CASES / "_example.json").unlink()
    kinds = sorted({"/".join(classify(e)) for e in run["errors"]} - {"schema/consequence"})
    return {
        "result": "pass" if not run["errors"] else "fail",
        "found": kinds,
        "errors": run["errors"][:10],
        "warnings": len(run["warnings"]),
    }


def main() -> int:
    lock = json.loads((ROOT / "models/network-twin/m1/sources.lock.json").read_text("utf-8"))
    url_of = {e.get("document"): e["url"] for e in lock["modules"] if e.get("document")}
    by_name, decided = revisions()
    nodes = inventory()
    (OUT / "examples").mkdir(parents=True, exist_ok=True)
    report = {"scope": __doc__.split("\n\n")[0], "documents": {}}
    for doc in DOCUMENTS:
        url = url_of.get(doc, f"https://www.rfc-editor.org/rfc/{doc.lower()}.txt")
        path = CACHE / re.sub(r"[^\w.@-]+", "_", url.split("://", 1)[1])[-180:]
        text = path.read_bytes()
        lines = page_clean(text.decode("utf-8")).split("\n")
        rows, datastore = [], {}
        for n, example in enumerate(examples(text.decode("utf-8")), 1):
            row = {"example": n, "line": example["line"], "rfc8792_unfolded": example["wrapped"]}
            if "error" in example:
                row.update(result="not-json", errors=[example["error"]])
                rows.append(row)
                print(f"{doc:10} #{n} line {example['line']:5} not-json")
                continue
            data = example["data"]
            top = {m for m in data}
            is_root = all(nodes.get("/" + m, {}).get("kind") in ("container", "list") for m in top)
            uri = target_uri(lines, example["line"] - 1)
            if uri and "/restconf/data/" in uri and uri.rstrip("/") != "/restconf/data":
                try:
                    data = nest(uri, data, nodes)
                except ValueError as exc:
                    row.update(result="target-unresolved", target=uri, errors=[str(exc)])
                    rows.append(row)
                    print(f"{doc:10} #{n} line {example['line']:5} target-unresolved {exc}")
                    continue
                row["target"] = uri
            elif not is_root:
                row.update(result="fragment", root=sorted(top))
                rows.append(row)
                print(f"{doc:10} #{n} line {example['line']:5} fragment {sorted(top)}")
                continue
            name = f"{doc.lower()}-{n}.json"
            body = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
            (OUT / "examples" / name).write_text(body, encoding="utf-8", newline="\n")
            row.update(
                file=f"models/network-twin/m5/examples/{name}", **validate(data, by_name, decided)
            )
            if row["result"] == "fail" and row["found"] == ["semantic/reference"] and datastore:
                merged = merge(datastore, simple(data), nodes)
                row["with_earlier_examples"] = validate(merged, by_name, decided)
            if "pass" in (row["result"], row.get("with_earlier_examples", {}).get("result")):
                # Only examples that validate carry objects later figures may reference.
                datastore = merge(datastore, simple(data), nodes)
            rows.append(row)
            print(
                f"{doc:10} #{n} line {example['line']:5} {row['result']:5} {row['found']}",
                row.get("with_earlier_examples", {}).get("result", ""),
            )
        report["documents"][doc] = {"text_sha256": digest(text), "examples": rows}
    (OUT / "examples.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""M2 offline acceptance: the inventory drafts' own JSON examples against the fixed modules.

The lab has no physical, passive, entitlement, location or energy facts, so the plan
(§8 M2) accepts that scope offline with the standards' published examples. Every JSON
instance example of the fixed IVY drafts is extracted from the locked document text
(page headers removed, RFC 8792 '\\' line wrapping unfolded), then validated by libyang
5.8.6 in operational mode against the M1 baseline closure of the modules it uses. A
failure is reported with its gap kind; nothing in an example is corrected.

Run: venv/Scripts/python.exe scripts/topology/m2_examples.py
Output: models/network-twin/m2/examples/*.json and models/network-twin/m2/examples.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts/topology")]
from m1_cases import CASES, classify, closure, revisions, used_modules, yanglint  # noqa: E402
from m1_sources import CACHE, digest, page_clean  # noqa: E402

OUT = ROOT / "models/network-twin/m2"
DOCUMENTS = (
    "draft-ietf-ivy-network-inventory-yang-20",
    "draft-ietf-ivy-network-inventory-software-04",
    "draft-ietf-ivy-entitlement-inventory-05",
    "draft-ietf-ivy-network-inventory-location-06",
    "draft-ietf-ivy-passive-network-inventory-01",
    "draft-ietf-green-power-and-energy-yang-04",
)


def unfold(lines: list[str]) -> list[str]:
    """RFC 8792 §7.2 single-backslash unfolding: drop '\\' and the next line's indent."""
    out: list[str] = []
    carry = False
    for line in lines:
        text = line.lstrip() if carry else line
        carry = text.endswith("\\")
        if out and out[-1].endswith("\\"):
            out[-1] = out[-1][:-1] + text
        else:
            out.append(text)
    return out


def examples(text: str) -> list[dict]:
    """JSON objects that start with a lone '{' line and end at the same indentation."""
    lines = page_clean(text).split("\n")
    found, i = [], 0
    while i < len(lines):
        start = re.fullmatch(r"( *)\{\s*", lines[i])
        if not start or not any('":' in x for x in lines[i + 1 : i + 3]):
            i += 1
            continue
        indent = start[1]
        end = next(
            (j for j in range(i + 1, len(lines)) if re.fullmatch(indent + r"\}\s*", lines[j])),
            None,
        )
        if end is None:
            break
        wrapped = any("line wrapping per RFC 8792" in x for x in lines[max(0, i - 6) : i])
        # Page breaks leave blank lines, also between a wrapped line and its continuation.
        block = [x for x in lines[i : end + 1] if x.strip()]
        body = "\n".join(unfold(block) if wrapped else block)
        try:
            found.append({"line": i + 1, "wrapped": wrapped, "data": json.loads(body)})
        except json.JSONDecodeError as exc:
            found.append({"line": i + 1, "wrapped": wrapped, "error": f"not JSON: {exc}"})
        i = end + 1
    return found


def main() -> int:
    lock = json.loads((ROOT / "models/network-twin/m1/sources.lock.json").read_text("utf-8"))
    url_of = {e.get("document"): e["url"] for e in lock["modules"] if e.get("document")}
    by_name, decided = revisions()
    (OUT / "examples").mkdir(parents=True, exist_ok=True)
    report = {"scope": __doc__.split("\n\n")[0], "documents": {}}
    for doc in DOCUMENTS:
        url = url_of.get(doc, f"https://www.ietf.org/archive/id/{doc}.txt")
        path = CACHE / re.sub(r"[^\w.@-]+", "_", url.split("://", 1)[1])[-180:]
        text = path.read_bytes()
        rows = []
        for n, example in enumerate(examples(text.decode("utf-8")), 1):
            row = {"example": n, "line": example["line"], "rfc8792_unfolded": example["wrapped"]}
            if "error" in example:
                row.update(result="not-json", errors=[example["error"]])
                rows.append(row)
                print(f"{doc:48} #{n} line {example['line']:5} not-json {example['error']}")
                continue
            name = f"{doc}-{n}.json"
            data = json.dumps(example["data"], ensure_ascii=False, indent=1) + "\n"
            (OUT / "examples" / name).write_text(data, encoding="utf-8", newline="\n")
            roots = []
            for module in sorted(used_modules(example["data"])):
                entries = [
                    e
                    for e in by_name.get(module, [])
                    if decided.get(e["file"]) == "runtime-baseline"
                ]
                if entries:
                    roots.append(max(entries, key=lambda e: e["revision"])["file"])
            (CASES / "_example.json").write_text(data, encoding="utf-8", newline="\n")
            try:
                run = yanglint(closure(roots, by_name, decided), "_example.json", "data")
            finally:
                (CASES / "_example.json").unlink()
            kinds = sorted({"/".join(classify(e)) for e in run["errors"]} - {"schema/consequence"})
            # Examples built on the drafts' own illustrative example-* modules cannot load.
            illustrative = any('No module named "example-' in e for e in run["errors"])
            row.update(
                file=f"models/network-twin/m2/examples/{name}",
                result="pass"
                if not run["errors"]
                else "uses-example-modules"
                if illustrative
                else "fail",
                found=kinds,
                errors=run["errors"][:10],
                warnings=len(run["warnings"]),
            )
            rows.append(row)
            print(f"{doc:48} #{n} line {example['line']:5} {row['result']:5} {kinds}")
        report["documents"][doc] = {"text_sha256": digest(text), "examples": rows}
        if not rows:
            print(f"{doc:48} no JSON instance example in the document")
    (OUT / "examples.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

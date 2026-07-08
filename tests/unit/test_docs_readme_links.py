from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote


DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"


def test_docs_readme_local_links_exist():
    readme = DOCS_DIR / "README.md"
    content = readme.read_text(encoding="utf-8")

    missing: list[str] = []
    for match in re.finditer(r"\]\(([^)]+)\)", content):
        href = match.group(1).strip()
        if not href or href.startswith(("http://", "https://", "#")):
            continue
        target_text = href.split("#", 1)[0]
        if not target_text:
            continue
        target = (DOCS_DIR / unquote(target_text)).resolve()
        if not target.exists():
            missing.append(href)

    assert missing == []

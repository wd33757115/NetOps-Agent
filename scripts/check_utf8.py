#!/usr/bin/env python
"""Scan repository text files for UTF-8 decode failures and common mojibake."""

from __future__ import annotations

import argparse
from pathlib import Path


TEXT_EXTENSIONS = {
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".ps1",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}

SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".runtime",
    ".venv",
    "__pycache__",
    "dist",
    "node_modules",
    "reports",
}

MOJIBAKE_MARKERS = (
    "\ufffd",
    "锛",
    "銆",
    "鈫",
    "缁撴",
    "璇︾粏",
    "鎴愬姛",
    "鍙",
    "鍜",
    "鏌",
    "鐧",
    "娌",
    "鏈",
    "閿",
    "绠",
    "涓",
    "浠",
    "悊",
    "杞",
    "閺",
    "閸",
    "鐠",
    "婢",
    "濞",
    "闁",
)


def iter_text_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in TEXT_EXTENSIONS or path.name in {".editorconfig", ".gitattributes"}:
            yield path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="*", default=["."], help="Repository roots or subdirectories to scan.")
    parser.add_argument("--mojibake", action="store_true", help="Also fail on common Chinese mojibake markers.")
    args = parser.parse_args()

    repo_root = Path.cwd().resolve()
    failures: list[str] = []
    mojibake: list[str] = []
    for root_arg in args.roots:
        root = Path(root_arg).resolve()
        for path in iter_text_files(root):
            try:
                rel = path.relative_to(repo_root)
            except ValueError:
                rel = path
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                failures.append(f"{rel}: UTF-8 decode failed at byte {exc.start}")
                continue
            if args.mojibake and any(marker in text for marker in MOJIBAKE_MARKERS):
                mojibake.append(str(rel))

    for item in failures:
        print(item)
    for item in mojibake:
        print(f"{item}: possible mojibake")

    return 1 if failures or mojibake else 0


if __name__ == "__main__":
    raise SystemExit(main())

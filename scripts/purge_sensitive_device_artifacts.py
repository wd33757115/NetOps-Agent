"""Purge generated business artifacts and redact caller-supplied identifiers."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GENERATED_BUSINESS_PATHS = (
    Path(".playwright-cli"),
    Path(".runtime"),
    Path("src/skills/textfsm-generator/reports"),
    Path("TEXTFSMTEST"),
)
EXCLUDED_DIRECTORY_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
    "site-packages",
    "venv",
}


def _resolve_under_root(root: Path, relative_path: Path) -> Path:
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"refusing to operate outside project root: {relative_path}") from exc
    if target == root:
        raise ValueError("refusing to operate on project root")
    return target


def _is_within(path: Path, container: Path) -> bool:
    try:
        path.resolve().relative_to(container)
    except ValueError:
        return False
    return True


def _parse_replacements(values: Iterable[str]) -> list[tuple[str, str]]:
    replacements: list[tuple[str, str]] = []
    for value in values:
        source, separator, replacement = value.partition("=")
        if not separator or not source:
            raise ValueError("each --replace value must use SOURCE=REPLACEMENT")
        replacements.append((source, replacement))
    if not replacements:
        raise ValueError("at least one --replace value is required")
    return replacements


def _redact_project_files(
    *, root: Path, replacements: list[tuple[str, str]], excluded_paths: list[Path], execute: bool
) -> dict[str, Any]:
    patterns = [(re.compile(re.escape(source), re.IGNORECASE), replacement) for source, replacement in replacements]
    replacement_counts = {source: 0 for source, _ in replacements}
    updated_file_count = 0
    skipped_binary_file_count = 0
    skipped_decode_error_count = 0

    for current_root, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current_root)
        directories[:] = [
            directory
            for directory in directories
            if directory not in EXCLUDED_DIRECTORY_NAMES
            and not any(_is_within(current_path / directory, target) for target in excluded_paths)
        ]
        for filename in filenames:
            path = current_path / filename
            if any(_is_within(path, target) for target in excluded_paths):
                continue
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:8192]:
                skipped_binary_file_count += 1
                continue
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                skipped_decode_error_count += 1
                continue

            updated_content = content
            file_changed = False
            for (source, _), (pattern, replacement) in zip(replacements, patterns, strict=True):
                updated_content, count = pattern.subn(replacement, updated_content)
                if count:
                    replacement_counts[source] += count
                    file_changed = True
            if not file_changed:
                continue
            updated_file_count += 1
            if execute:
                path.write_text(updated_content, encoding="utf-8")

    return {
        "updated_file_count": updated_file_count,
        "replacement_counts": replacement_counts,
        "skipped_binary_file_count": skipped_binary_file_count,
        "skipped_decode_error_count": skipped_decode_error_count,
    }


def purge_sensitive_device_artifacts(
    *, root: Path = ROOT, replacements: list[tuple[str, str]], execute: bool
) -> dict[str, Any]:
    project_root = root.resolve()
    targets = [_resolve_under_root(project_root, path) for path in GENERATED_BUSINESS_PATHS]
    existing_targets = [target for target in targets if target.exists()]

    if execute:
        for target in existing_targets:
            shutil.rmtree(target)

    redaction = _redact_project_files(
        root=project_root,
        replacements=replacements,
        excluded_paths=targets,
        execute=execute,
    )
    return {
        "success": True,
        "dry_run": not execute,
        "project_root": str(project_root),
        "removed_directories": [
            str(target.relative_to(project_root)).replace("\\", "/")
            for target in existing_targets
        ],
        "redaction": redaction,
        "values_redacted": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove generated business data and redact caller-supplied identifiers."
    )
    parser.add_argument("--execute", action="store_true", help="perform deletion and redaction")
    parser.add_argument(
        "--replace",
        action="append",
        default=[],
        metavar="SOURCE=REPLACEMENT",
        help="case-insensitive replacement to apply outside generated business directories",
    )
    args = parser.parse_args()
    result = purge_sensitive_device_artifacts(
        replacements=_parse_replacements(args.replace), execute=args.execute
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

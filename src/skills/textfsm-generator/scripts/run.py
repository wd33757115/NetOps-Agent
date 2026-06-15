#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""TextFSM Generator Skill command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[4]
for import_path in (PROJECT_ROOT, SCRIPT_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from generate_templates import generate_templates  # noqa: E402


def _configure_stdio_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate validated TextFSM parser assets"
    )
    parser.add_argument("--params", help="platform params JSON path")
    parser.add_argument("--source-path", help="offline txt/log file or directory")
    parser.add_argument("--templates-dir")
    parser.add_argument("--vendor")
    parser.add_argument("--model")
    parser.add_argument("--command")
    parser.add_argument("--max-retries", type=int)
    parser.add_argument("--max-samples-per-prompt", type=int)
    parser.add_argument("--minimum-sample-pass-rate", type=float)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-generate", action="store_true")
    parser.add_argument("--no-publish", action="store_true")
    parser.add_argument("--no-recursive", action="store_true")
    args = parser.parse_args()

    params: dict = {}
    if args.params:
        with open(args.params, encoding="utf-8-sig") as handle:
            params = json.load(handle)
    for key in (
        "source_path",
        "templates_dir",
        "vendor",
        "model",
        "command",
        "max_retries",
        "max_samples_per_prompt",
        "minimum_sample_pass_rate",
    ):
        value = getattr(args, key)
        if value is not None:
            params[key] = value
    if args.dry_run:
        params["dry_run"] = True
    if args.force_generate:
        params["force_generate"] = True
    if args.no_publish:
        params["publish"] = False
    if args.no_recursive:
        params["recursive"] = False

    try:
        result = generate_templates(params)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("success") else 1
    except Exception as exc:
        print(
            json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    _configure_stdio_utf8()
    raise SystemExit(main())

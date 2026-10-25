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
PROJECT_ROOT = Path(__file__).resolve().parents[7]
for import_path in (PROJECT_ROOT, SCRIPT_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from src.apps.rule_governance.backend.generators.textfsm_generator.scripts.generate_templates import generate_templates  # noqa: E402
from src.apps.rule_governance.backend.generators.textfsm_generator.scripts.spec_suggester import suggest_parser_spec  # noqa: E402


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
        description="Generate a validated TextFSM candidate for a durable Parser job"
    )
    parser.add_argument("--params", help="platform params JSON path")
    parser.add_argument("--job-id", help="Parser control-plane job ID")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-publish", action="store_true")
    args = parser.parse_args()

    params: dict = {}
    if args.params:
        with open(args.params, encoding="utf-8-sig") as handle:
            params = json.load(handle)
    if args.job_id:
        params["job_id"] = args.job_id
    if args.dry_run:
        params["dry_run"] = True
    if args.no_publish:
        params["publish"] = False

    try:
        result = (
            suggest_parser_spec(params)
            if str(params.get("operation") or "generate") == "suggest_spec"
            else generate_templates(params)
        )
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
    from src.apps.rule_governance.backend.access import maintenance
    raise SystemExit(maintenance(main)())

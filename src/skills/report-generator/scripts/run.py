#!/usr/bin/env python
"""Skill entrypoint for report-generator."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.patrol.reporting import render_run_report  # noqa: E402

_FORMAT_EXTENSIONS = {
    "markdown": ".md",
    "html": ".html",
    "xlsx": ".xlsx",
}


def _platform_output_path(*, output_dir: str, run_id: str, output_format: str) -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    safe_run_id = re.sub(r"[^A-Za-z0-9._-]+", "-", run_id).strip("-.") or "patrol-run"
    return directory / f"patrol-report-{safe_run_id}{_FORMAT_EXTENSIONS[output_format]}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a patrol report")
    parser.add_argument("--params", required=True, help="params.json path")
    parser.add_argument(
        "--output-dir",
        help="platform-owned output directory; takes precedence over params.output_path",
    )
    args = parser.parse_args()
    with open(args.params, encoding="utf-8-sig") as f:
        params = json.load(f)
    run_id = str(params["run_id"])
    output_format = str(params.get("output_format") or "markdown").lower()
    if output_format not in _FORMAT_EXTENSIONS:
        parser.error(f"unsupported output_format: {output_format}")
    output_path = params.get("output_path")
    if args.output_dir:
        output_path = str(
            _platform_output_path(
                output_dir=args.output_dir,
                run_id=run_id,
                output_format=output_format,
            )
        )
    result = render_run_report(
        run_id=run_id,
        output_path=output_path,
        device_id=params.get("device_id"),
        output_format=output_format,
    )
    if output_path:
        result["output_filename"] = Path(output_path).name
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Print the timeline of one assistant run: what the model said, called and got back.

Usage:
  venv/Scripts/python.exe scripts/assistant/trace_timeline.py <run_id> [--reasoning]
  venv/Scripts/python.exe scripts/assistant/trace_timeline.py --file <trace.jsonl>

The run ID is shown on each answer's execution record (and in evaluation files).
Reads local trace files only; never calls a device or a model.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.platform.agent_runtime.timeline import find_trace, render, timeline  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_id", nargs="?")
    parser.add_argument("--file", help="a trace .jsonl path instead of a run ID")
    parser.add_argument("--reasoning", action="store_true", help="show thinking-mode reasoning")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    path = Path(args.file) if args.file else find_trace(args.run_id or "")
    if path is None or not path.exists():
        parser.error("no trace found for that run")
    print(render(timeline(path), reasoning=args.reasoning))


if __name__ == "__main__":
    main()

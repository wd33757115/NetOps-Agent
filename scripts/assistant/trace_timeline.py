"""Print the timeline of one assistant run: what the model said, called and got back.

Usage:
  venv/Scripts/python.exe scripts/assistant/trace_timeline.py <run_id> [--reasoning]
  venv/Scripts/python.exe scripts/assistant/trace_timeline.py --file <trace.jsonl>
  venv/Scripts/python.exe scripts/assistant/trace_timeline.py <run_id> --requests
  venv/Scripts/python.exe scripts/assistant/trace_timeline.py <run_id> --request-json 2

--requests shows what the model provider actually received each round (settings,
tool descriptions, the system prompt as sent, new messages); --request-json N prints
round N's request body verbatim. The run ID is shown on each answer's execution
record (and in evaluation files). Reads local files only; never calls a device or a model.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.platform.agent_runtime.timeline import (  # noqa: E402
    find_trace,
    render,
    render_requests,
    requests,
    timeline,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_id", nargs="?")
    parser.add_argument("--file", help="a trace .jsonl path instead of a run ID")
    parser.add_argument("--reasoning", action="store_true", help="show thinking-mode reasoning")
    parser.add_argument("--requests", action="store_true", help="what the model was sent")
    parser.add_argument("--request-json", type=int, metavar="N", help="round N's request body")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    path = Path(args.file) if args.file else find_trace(args.run_id or "")
    if path is None or not path.exists():
        parser.error("no trace found for that run")
    if args.request_json is not None:
        rows = [row for row in requests(path) if row["round"] == args.request_json]
        if not rows:
            parser.error(f"no recorded request for round {args.request_json}")
        print(json.dumps(rows[0]["request"], ensure_ascii=False, indent=2))
    elif args.requests:
        print(render_requests(requests(path)))
    else:
        print(render(timeline(path), reasoning=args.reasoning))


if __name__ == "__main__":
    main()

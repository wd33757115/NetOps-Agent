"""Bounded production-adapter probe. Real provider access requires --live and a small fragment."""

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.apps.materials.backend.documents import DocumentReader
from src.apps.assistant.backend.workspace import DocumentWorkspace
from src.platform.agent_runtime.base import RunContext
from src.platform.agent_runtime.dsh import DshRuntime


async def probe(fragment):
    if fragment.stat().st_size > 12000:
        raise ValueError("Fragment input too large")
    sample = json.loads(fragment.read_text(encoding="utf8"))["text"]
    if not isinstance(sample, str) or not 0 < len(sample) <= 1200:
        raise ValueError(
            "Only a previously selected fragment of at most 1200 characters is allowed"
        )
    with tempfile.TemporaryDirectory(prefix="netops-dsh-source-") as directory:
        path = Path(directory) / "fragment.md"
        path.write_text(sample, encoding="utf8")
        workspace = DocumentWorkspace(
            "local-probe", reader=DocumentReader(directory), check=lambda: None
        )
        workspace.definitions = lambda: []  # This isolated probe cannot query real business data.
        started = time.monotonic()
        events = [
            event
            async for event in DshRuntime().start(
                RunContext(
                    "probe",
                    "local-probe",
                    "probe",
                    timeout_seconds=60,
                    max_model_rounds=5,
                    max_tool_calls=6,
                ),
                [
                    {
                        "role": "user",
                        "content": (
                            "请根据本轮资料，简述 RR 与 BGP EVPN 的配置关系。"
                            "最多 150 字，引用实际读取的原文；未包含的信息明确说明。"
                        ),
                    }
                ],
                workspace,
            )
        ]
        report = {
            "executor": "deepseek-harness-sdk",
            "source_characters": len(sample),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "terminal": events[-1].kind,
            "error": events[-1].data.get("code"),
            "tools": [e.data for e in events if e.kind == "tool_finished"],
            "citations": sum(e.kind == "citation" for e in events),
            "answer": "".join(e.data["text"] for e in events if e.kind == "text_delta"),
        }
        destination = ROOT / ".runtime/assistant-dsh-probe.json"
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
        print(json.dumps({k: v for k, v in report.items() if k != "answer"}, ensure_ascii=False))
        if report["terminal"] != "completed" or not report["citations"]:
            raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--fragment", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error("Explicit --live is required; offline coverage is in test_dsh_runtime.py")
    asyncio.run(probe(args.fragment))

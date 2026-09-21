"""Reproducible bounded DSH evaluation. Checks are evidence gates, not expert acceptance."""

import argparse
import asyncio
import hashlib
import json
import math
import platform
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.apps.materials.backend.documents import DocumentReader
from src.apps.assistant.backend.workspace import DocumentWorkspace
from src.platform.agent_runtime.base import RunContext
from src.platform.agent_runtime.dsh import PROMPT, DshRuntime


def implementation_digest():
    digest = hashlib.sha256()
    for relative in (
        "src/platform/agent_runtime/dsh.py",
        "src/platform/agent_runtime/dsh_guard.cjs",
        "src/platform/agent_runtime/dsh_worker.py",
        "src/apps/assistant/backend/workspace.py",
        "scripts/assistant/evaluate.py",
    ):
        digest.update(relative.encode() + b"\0")
        digest.update((ROOT / relative).read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def load_dataset(path):
    if path.stat().st_size > 200000:
        raise ValueError("Dataset exceeds the bounded evaluation size")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or not 1 <= len(data["cases"]) <= 100:
        raise ValueError("Unsupported dataset")
    ids = [case["id"] for case in data["cases"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case identifiers")
    for case in data["cases"]:
        if case["split"] not in {"dev", "holdout"} or not case["question"].strip():
            raise ValueError("Every question needs an explicit split")
        if not case.get("answer_groups") or not case.get("rubric"):
            raise ValueError("A case needs expected answer groups and a review rubric")
        sources = case["documents"]
        if not sources or any(name not in data["documents"] for name in sources):
            raise ValueError("Unknown source")
        if sum(len(data["documents"][name]["text"]) for name in sources) > 12000:
            raise ValueError("Per-case source budget exceeded")
    return data


def grade(case, answer, citations, terminal):
    normalized = answer.casefold()
    excerpts = "\n".join(c.get("excerpt", "") for c in citations).casefold()
    checks = {
        "completed": terminal == "completed",
        "answer_points": all(
            any(term.casefold() in normalized for term in group) for group in case["answer_groups"]
        ),
        "required_evidence": all(
            term.casefold() in excerpts for term in case.get("evidence_terms", [])
        ),
        "no_forbidden_claims": not any(
            term.casefold() in normalized for term in case.get("forbidden", [])
        ),
        "citations_present": bool(citations) if case.get("evidence_terms") else True,
    }
    return {
        "checks": checks,
        "automatic_pass": all(checks.values()),
        "review_status": "requires_domain_review",
        "rubric": case["rubric"],
    }


async def trial(case, documents, number):
    # Each attempt gets an independent corpus, conversation and SDK home. Expected
    # answers and rubric are never included in the model's input.
    with tempfile.TemporaryDirectory(prefix="netops-eval-source-") as directory:
        for index, name in enumerate(case["documents"]):
            Path(directory, f"source-{index}.md").write_text(
                documents[name]["text"], encoding="utf-8"
            )
        workspace = DocumentWorkspace(
            "evaluation", reader=DocumentReader(directory), check=lambda: None
        )
        workspace.definitions = lambda: []
        rejected = {}
        validate = workspace.references

        def capture_validation(answer):
            try:
                return validate(answer)
            except ValueError as exc:
                rejected.update(candidate_answer=answer, reason=str(exc))
                raise

        workspace.references = capture_validation
        context = RunContext(
            uuid4().hex,
            "evaluation",
            uuid4().hex,
            timeout_seconds=90,
            max_tool_calls=10,
            max_model_rounds=7,
        )
        messages = [*case.get("history", []), {"role": "user", "content": case["question"]}]
        started = time.monotonic()
        events = [e async for e in DshRuntime().start(context, messages, workspace)]
        elapsed = round(time.monotonic() - started, 3)
        answer = "".join(e.data["text"] for e in events if e.kind == "text_delta")
        citations = [e.data for e in events if e.kind == "citation"]
        return {
            "id": case["id"],
            "split": case["split"],
            "category": case["category"],
            "trial": number,
            "elapsed_seconds": elapsed,
            "terminal": events[-1].kind,
            "error_code": events[-1].data.get("code"),
            "answer": answer,
            "citations": citations,
            "metrics": next((e.data for e in events if e.kind == "metrics"), {}),
            "rejected_answer": rejected or None,
            "tools": [e.data for e in events if e.kind == "tool_finished"],
            **grade(case, answer, citations, events[-1].kind),
        }


async def run(args, dataset):
    from src.common.config import get_settings

    args.output.mkdir(parents=True, exist_ok=True)
    selected = [c for c in dataset["cases"] if args.split == "all" or c["split"] == args.split]
    results = []
    semaphore = asyncio.Semaphore(2)

    async def bounded(case, number):
        async with semaphore:
            result = await trial(case, dataset["documents"], number)
            results.append(result)
            Path(args.output, f"{case['id']}-{number}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        k: result[k]
                        for k in ("id", "trial", "terminal", "automatic_pass", "elapsed_seconds")
                    }
                ),
                flush=True,
            )

    await asyncio.gather(*(bounded(c, n + 1) for c in selected for n in range(args.trials)))
    latencies = sorted(r["elapsed_seconds"] for r in results)
    summary = {
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "implementation_sha256": implementation_digest(),
        "sdk": version("deepseek-harness-sdk"),
        "model": get_settings().LLM_MODEL,
        "environment": platform.platform(),
        "split": args.split,
        "trials": args.trials,
        "attempts": len(results),
        "automatic_passes": sum(r["automatic_pass"] for r in results),
        "completed": sum(r["terminal"] == "completed" for r in results),
        "p95_seconds": latencies[math.ceil(len(latencies) * 0.95) - 1] if latencies else None,
        "domain_acceptance": "not_measured",
        "warning": "Keyword and evidence gates do not prove semantic correctness.",
        "results": [
            {k: r[k] for k in ("id", "trial", "automatic_pass", "checks")}
            for r in sorted(results, key=lambda r: (r["id"], r["trial"]))
        ],
    }
    Path(args.output, "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False))
    return 0 if all(r["automatic_pass"] for r in results) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/assistant-evaluation")
    parser.add_argument("--split", choices=["dev", "holdout", "all"], default="dev")
    parser.add_argument("--trials", type=int, choices=[1, 2, 3], default=1)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    dataset = load_dataset(args.dataset)
    if not args.live:
        print(json.dumps({"validated_cases": len(dataset["cases"]), "model_calls": 0}))
        return 0
    return asyncio.run(run(args, dataset))


if __name__ == "__main__":
    raise SystemExit(main())

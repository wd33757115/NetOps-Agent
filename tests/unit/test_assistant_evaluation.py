import json
from pathlib import Path

import pytest

from scripts.assistant.evaluate import grade, load_dataset
from src.platform.agent_runtime.usage import Usage


def test_grader_requires_both_answer_and_observed_evidence():
    case = {
        "answer_groups": [["100"]],
        "evidence_terms": ["AS 100"],
        "forbidden": ["INJECTION_SUCCEEDED"],
        "rubric": "correct AS",
    }
    assert not grade(case, "AS 100", [], "completed")["automatic_pass"]
    assert not grade(case, "AS 100 INJECTION_SUCCEEDED", [{"excerpt": "AS 100"}], "completed")[
        "automatic_pass"
    ]
    passing = grade(case, "AS 100", [{"excerpt": "AS 100"}], "completed")
    assert passing["automatic_pass"] and passing["review_status"] == "requires_domain_review"


def test_dataset_rejects_accidental_full_document_and_duplicate_cases(tmp_path):
    case = {
        "id": "one",
        "split": "holdout",
        "question": "q",
        "documents": ["doc"],
        "answer_groups": [["a"]],
        "rubric": "r",
    }
    data = {"schema": 1, "documents": {"doc": {"text": "x" * 12001}}, "cases": [case]}
    path = Path(tmp_path, "dataset.json")
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="source budget"):
        load_dataset(path)
    data["documents"]["doc"]["text"] = "small"
    data["cases"].append(case)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        load_dataset(path)


def test_usage_handles_fragmented_sse_without_storing_model_content():
    usage = Usage()
    wire = b'data: {"choices":[{"delta":{"reasoning_content":"PRIVATE"}}]}\r\n\r\n'
    wire += b'data: {"usage":{"prompt_tokens":20,"completion_tokens":3,"total_tokens":23}}\n\n'
    wire += b"data: [DONE]\n\n"
    for char in wire:
        usage.feed(bytes([char]))
    assert usage.tokens == {"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23}
    assert usage.buffer == b""
    usage.feed(b"x" * 200000)
    assert usage.buffer == b""

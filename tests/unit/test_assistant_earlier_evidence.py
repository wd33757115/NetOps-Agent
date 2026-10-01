"""Follow-up answers may cite evidence earlier answers showed; unknown brackets are named."""

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from src.apps.assistant.backend import answer_gate, evidence, turns
from src.apps.assistant.backend.workspace import DocumentWorkspace
from src.platform.clock import beijing, in_beijing

EARLIER = evidence.capture("device_access__run", {"host": "10.10.1.1", "commands": []})
NOW = evidence.capture("topology__locate", {"status": "confirmed"})


@pytest.fixture
def space(monkeypatch):
    checked = []
    monkeypatch.setattr(
        "src.apps.assistant.backend.workspace.authorize_worker",
        lambda actor, app: checked.append(app),
    )
    workspace = DocumentWorkspace("alice", reader=object(), check=lambda: None)
    workspace.checked = checked
    return workspace


def submit(workspace, answer):
    return answer_gate.submit(workspace, {"answer": answer, "status": "answered"})


def test_a_follow_up_may_cite_what_an_earlier_answer_showed(space):
    space.earlier_evidence = {EARLIER["id"]: EARLIER}
    result = submit(space, f"上一轮 VSR-1 的现场输出已说明这一点 {EARLIER['citation']}")
    assert result["accepted"] and space.prepared_answer[1] == [EARLIER]
    # Citing still rechecks the source App.
    assert space.checked == ["device-access"]
    assert EARLIER["citation"] in space.citation_guidance()


def test_evidence_fetched_this_turn_must_still_be_cited(space):
    space.earlier_evidence = {EARLIER["id"]: EARLIER}
    space.business_evidence = {NOW["id"]: NOW}
    assert not submit(space, f"只引用旧证据 {EARLIER['citation']}")["accepted"]
    assert submit(space, f"新旧都有 {EARLIER['citation']}{NOW['citation']}")["accepted"]


def test_a_bracket_that_names_no_evidence_is_named_back(space):
    space.business_evidence = {NOW["id"]: NOW}
    # The model once copied the prompt's placeholder into its own heading.
    result = submit(space, f"已证实（均有 [evidence-...] 支撑）：已定位 {NOW['citation']}")
    assert not result["accepted"] and result["code"] == "evidence_unknown"
    assert result["message"].endswith("[evidence-...]")
    assert NOW["citation"] not in result["message"]


def test_earlier_evidence_comes_from_this_conversations_answers_only():
    rows = [
        {"role": "user", "references": [EARLIER], "content": "问题"},
        {"role": "assistant", "references": [EARLIER, {"kind": "document", "id": "d"}]},
        {"role": "assistant", "references": [{**NOW, "excerpt": None}]},
    ]
    assert turns.earlier_evidence(rows) == {EARLIER["id"]: EARLIER}


def test_a_revoked_source_app_still_blocks_an_earlier_citation(space, monkeypatch):
    def revoked(actor, app):
        raise HTTPException(403, "revoked")

    monkeypatch.setattr("src.apps.assistant.backend.workspace.authorize_worker", revoked)
    space.earlier_evidence = {EARLIER["id"]: EARLIER}
    with pytest.raises(HTTPException):
        space.references(f"旧证据 {EARLIER['citation']}")


def test_times_the_model_reads_are_beijing_time():
    moment = datetime(2026, 9, 30, 9, 55, tzinfo=timezone.utc)
    assert beijing(moment) == "2026-09-30T17:55:00+08:00"
    assert beijing("2026-09-30T09:55:53.5+00:00") == "2026-09-30T17:55:53.500000+08:00"
    assert beijing("2026-09-30T09:55:00Z") == "2026-09-30T17:55:00+08:00"
    result = in_beijing(
        {
            "observed_at": "2026-09-30T09:55:00+00:00",
            "rows": [{"at": "2026-09-30T17:55:00+08:00"}],
            # Device output and free text are never rewritten.
            "output": "Last clock change 2026-09-30T09:55:00+00:00",
            "naive": "2026-09-30T09:55:00",
            "count": 3,
        }
    )
    assert result == {
        "observed_at": "2026-09-30T17:55:00+08:00",
        "rows": [{"at": "2026-09-30T17:55:00+08:00"}],
        "output": "Last clock change 2026-09-30T09:55:00+00:00",
        "naive": "2026-09-30T09:55:00",
        "count": 3,
    }
    assert evidence.capture("topology__locate", {})["retrieved_at"].endswith("+08:00")

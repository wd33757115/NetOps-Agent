"""A request sheet drafted from the resource ledger reads back like one a person filled in."""
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.apps.firewall_policy.backend import request_draft, workflow_actions

SCRIPTS = Path(__file__).resolve().parents[2] / "src/apps/firewall_policy/backend/engine/policy/scripts"
ROWS = [
    {"source_system": "", "src_ip": "10.248.255.11", "destination_system": "风险评估系统",
     "dst_ip": "10.1.0.11,10.1.0.12", "port": "443", "proto": "tcp"},
    {"source_system": "", "src_ip": "10.248.255.11", "destination_system": "风险评估系统",
     "dst_ip": "10.1.0.13", "port": "53", "proto": "udp"},
]


@pytest.fixture
def reader(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    for name in ("policy_input", "policy_sets", "policy_window"):
        sys.modules.pop(name, None)
    from policy_input import read_policy_workbook

    return read_policy_workbook


def test_drafted_sheet_reads_as_a_request(tmp_path, reader):
    path = tmp_path / "draft.xlsx"
    path.write_bytes(request_draft.workbook(ROWS, start_time="2026-11-01 20:00", end_time="2026-11-30",
                                            long_link="168", note="由资源台账生成"))
    frame = reader(str(path), now=datetime(2026, 10, 23))
    # The reader folds adjacent addresses into a range.
    assert list(frame["dst_ip"]) == ["10.1.0.11-10.1.0.12", "10.1.0.13"]
    assert list(frame["proto"]) == ["tcp", "udp"]
    # Long connections only apply to TCP, so the UDP row carries none.
    assert list(frame["long_link_hours"]) == ["168", ""]
    assert frame["window"].nunique() == 1 and frame["window"].iloc[0]
    assert list(frame["source_system"]) == ["", ""] or frame["source_system"].isna().all()
    assert list(frame["destination_system"]) == ["风险评估系统", "风险评估系统"]


def test_draft_action_saves_the_sheet_for_the_next_step(monkeypatch):
    user = SimpleNamespace(user_id="7")
    monkeypatch.setattr(request_draft, "plan", lambda u, body: {
        "rows": ROWS, "notes": ["10.248.255.11 不在台账的已规划网段里，按申请照常放行"]})
    saved = {}

    def save(u, filename, data, content_type, *, folder):
        saved.update(filename=filename, folder=folder, size=len(data))
        return {"file_id": "f1", "name": filename}

    monkeypatch.setattr(workflow_actions.files, "save", save)
    body = workflow_actions.DraftRequest(ticket_id="CHG-1", source="10.248.255.11", destination="风险评估系统",
                                         long_link=168, start_time="", end_time=None)
    assert body.long_link == "168" and body.start_time is None
    result = workflow_actions.draft_request(user, body, run_id="r1")
    assert result["policy_file_url"] == "netdisk:f1"
    assert saved["filename"] == "CHG-1-策略申请表.xlsx" and saved["folder"] == "防火墙申请表"
    assert list(result["files"]) == ["policy_request_draft"]
    assert result["message"] == "按资源台账生成了 2 条策略申请"


def test_draft_action_explains_an_empty_plan(monkeypatch):
    monkeypatch.setattr(request_draft, "plan", lambda u, body: {
        "rows": [], "notes": ["主机 a 没有登记服务端口，没有为它生成策略"]})
    body = workflow_actions.DraftRequest(ticket_id="CHG-1", source="1.1.1.1", destination="x")
    with pytest.raises(RuntimeError, match="没有可开通的访问关系：主机 a 没有登记服务端口"):
        workflow_actions.draft_request(SimpleNamespace(user_id="7"), body, run_id="r1")


def test_page_drafts_from_the_ledger_and_keeps_the_sheet_as_an_upload(monkeypatch):
    from fastapi import HTTPException

    from src.apps.firewall_policy.backend import api
    from src.apps.firewall_policy.backend.schemas import LedgerDraft, LedgerUpload

    user = SimpleNamespace(user_id="7")
    monkeypatch.setattr(request_draft, "plan", lambda u, body: {"rows": ROWS, "notes": []})
    saved = {}

    def save_upload(owner, filename, kind, data, request_id):
        saved.update(owner=owner, filename=filename, kind=kind, request_id=request_id)
        return {"id": "a" * 32, "filename": filename, "kind": kind, "sha256": "x", "size": len(data)}

    monkeypatch.setattr(api.service, "save_upload", save_upload)
    preview = api.ledger_preview(LedgerDraft(source="10.248.255.11", destination="风险评估系统", long_link=168), user)
    assert preview == {"rows": ROWS, "notes": []}
    upload = api.ledger_upload(LedgerUpload(source="10.248.255.11", destination="风险评估系统", ticket_id="CHG-9",
                                            request_id="r1"), user)
    assert upload["id"] == "a" * 32 and upload["rows"] == ROWS
    assert saved == {"owner": "7", "filename": "CHG-9-策略申请表.xlsx", "kind": "policy", "request_id": "r1"}
    with pytest.raises(ValueError, match="工单号"):
        LedgerUpload(source="a", destination="b", ticket_id="../x", request_id="r")

    def no_ledger(u, body):
        raise RuntimeError("按台账生成申请表需要先安装并启用“资源台账”应用")

    monkeypatch.setattr(request_draft, "plan", no_ledger)
    with pytest.raises(HTTPException) as refused:
        api.ledger_preview(LedgerDraft(source="a", destination="b"), user)
    assert refused.value.status_code == 422 and "资源台账" in refused.value.detail

"""The ITSM firewall workflows call the firewall-policy App, as the Workflow's user."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from src.apps.firewall_policy.backend import service, workflow_actions as wa
from src.platform import files

USER = SimpleNamespace(user_id="7")


class Runs:
    """The App's service, recording what the action asked of it."""

    def __init__(self, monkeypatch, *, statuses=("queued", "completed"), error=None):
        self.uploads, self.submitted, self.reads = [], [], 0
        self.statuses = list(statuses)
        monkeypatch.setattr(wa, "fetch", lambda url, kind, fallback, user: (
            ("申请表.xlsx" if kind == "policy" else "topo.json"), b"data:" + url.encode()))
        monkeypatch.setattr(service, "save_upload", self.save_upload)
        monkeypatch.setattr(service, "submit_policy", self.submit("policy"))
        monkeypatch.setattr(service, "submit_ticket",
                            lambda owner, parent, body: self.submit("ticket")(owner, body, parent))
        monkeypatch.setattr(service, "read", self.read)
        monkeypatch.setattr(service, "download", lambda owner, run_id, key: (b"bytes-" + key.encode(), f"{key}.bin", "application/octet-stream"))
        monkeypatch.setattr(wa, "POLL_SECONDS", 0)
        self.error = error

    def save_upload(self, owner, filename, kind, data, request_id):
        self.uploads.append((owner, filename, kind, request_id))
        return {"id": ("b" if kind == "policy" else "c") * 32}

    def submit(self, kind):
        def submit(owner, body, parent=None):
            self.submitted.append((kind, owner, body, parent))
            return {"id": "r" * 32, "status": "queued"}
        return submit

    def read(self, owner, run_id):
        self.reads += 1
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return {
            "id": run_id, "status": status, "error": self.error,
            "result": {"manifest": {"devices": [{"device_name": "FW-1"}, {"device_name": "FW-2"}],
                                    "risk_level": "中"}} if status == "completed" else None,
            "artifacts": {"config_zip": {}, "policy_preview": {}} if status == "completed" else {},
        }


def test_generate_policy_uploads_the_ticket_files_and_returns_the_runs_files(monkeypatch):
    runs = Runs(monkeypatch)
    body = wa.GeneratePolicy(ticket_id="REQ-1", policy_file_url="http://itsm/files/申请表.xlsx",
                             topology_file_url="http://itsm/files/topo.json", workflow_run_id="ignored")

    result = wa.generate_policy(USER, body, run_id="wf-1")

    assert [upload[2:] for upload in runs.uploads] == [
        ("policy", "wf-wf-1-policy-file"), ("topology", "wf-wf-1-topology-file")]
    kind, owner, request, _ = runs.submitted[0]
    assert (kind, owner, request.request_id, request.ticket_id) == ("policy", "7", "wf-wf-1-policy", "REQ-1")
    assert request.policy_upload_id == "b" * 32 and request.topology_upload_id == "c" * 32
    assert result["policy_run_id"] == "r" * 32
    assert result["devices"] == ["FW-1", "FW-2"] and result["message"] == "已为 2 台防火墙生成策略配置"
    assert set(result["files"]) == {"config_zip", "policy_preview"}
    assert result["files"]["config_zip"][2] == b"bytes-config_zip"


def test_a_failed_policy_run_fails_the_step_with_its_reason(monkeypatch):
    Runs(monkeypatch, statuses=("failed",), error="策略生成失败：第 3 行地址无效")
    body = wa.GeneratePolicy(ticket_id="REQ-1", policy_file_url="http://itsm/p.xlsx")
    with pytest.raises(RuntimeError, match="第 3 行地址无效"):
        wa.generate_policy(USER, body, run_id="wf-1")


def test_a_run_still_going_at_the_deadline_names_it(monkeypatch):
    Runs(monkeypatch, statuses=("running",))
    monkeypatch.setattr(wa, "POLICY_SECONDS", 0)
    body = wa.GeneratePolicy(ticket_id="REQ-1", policy_file_url="http://itsm/p.xlsx")
    with pytest.raises(RuntimeError, match="未在 0 秒内完成.*" + "r" * 32):
        wa.generate_policy(USER, body, run_id="wf-1")


def test_an_invalid_workbook_is_reported_as_such(monkeypatch):
    Runs(monkeypatch)

    def reject(*args):
        raise HTTPException(422, "文件格式无效或内容超出限制")

    monkeypatch.setattr(service, "save_upload", reject)
    body = wa.GeneratePolicy(ticket_id="REQ-1", policy_file_url="http://itsm/p.xlsx")
    with pytest.raises(ValueError, match="策略申请表无效：文件格式无效"):
        wa.generate_policy(USER, body, run_id="wf-1")


def test_the_change_ticket_uses_only_the_fields_given(monkeypatch):
    runs = Runs(monkeypatch)
    body = wa.WriteTicket(policy_run_id="a" * 32, ticket_title="开通 Web 访问", requester="张三",
                          assignee=None, due_date="")

    result = wa.write_change_ticket(USER, body, run_id="wf-2")

    kind, owner, request, parent = runs.submitted[0]
    assert (kind, parent, request.request_id) == ("ticket", "a" * 32, "wf-wf-2-ticket")
    assert request.fields.ticket_title == "开通 Web 访问" and request.fields.requester == "张三"
    assert request.fields.assignee == "" and request.fields.due_date == ""
    assert result["ticket_run_id"] == "r" * 32 and "config_zip" in result["files"]


def test_fetch_reads_netdisk_files_as_the_user_and_http_otherwise(monkeypatch):
    read = []
    monkeypatch.setattr(files, "read", lambda user, file_id: read.append((user.user_id, file_id)) or (
        "申请表.xlsx", "application/octet-stream", b"sheet"))
    assert wa.fetch("netdisk:file-0123456789abcdef", "policy", "f.xlsx", USER) == ("申请表.xlsx", b"sheet")
    assert read == [("7", "file-0123456789abcdef")]

    with pytest.raises(ValueError, match="只支持网盘文件或 http"):
        wa.fetch("file:///etc/passwd", "policy", "x.xlsx", USER)
    with pytest.raises(ValueError, match="只支持网盘文件或 http"):
        wa.fetch(r"C:\secret.xlsx", "policy", "x.xlsx", USER)

    def serve(request):
        return httpx.Response(200, content=b"x" * 10)

    client = httpx.Client(transport=httpx.MockTransport(serve))
    monkeypatch.setattr(files.httpx, "stream", lambda method, url, **kwargs: client.stream(method, url))
    assert wa.fetch("http://itsm/download?id=9", "policy", "REQ-1-策略申请表.xlsx", USER) == (
        "REQ-1-策略申请表.xlsx", b"x" * 10)
    assert wa.fetch("http://itsm/files/%E7%94%B3%E8%AF%B7.xlsx", "policy", "f.xlsx", USER)[0] == "申请.xlsx"
    monkeypatch.setattr(wa.storage, "MAX_UPLOAD", 5)
    with pytest.raises(ValueError, match="文件超过"):
        wa.fetch("http://itsm/p.xlsx", "policy", "f.xlsx", USER)

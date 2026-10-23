"""Files attached in chat: kept in the asker's netdisk, read by the model, used by Workflows."""

import asyncio
import json
import re
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.apps.assistant.backend import attachments, automation
from src.apps.assistant.backend.automation import Attachment, StartWorkflow
from src.apps.assistant.backend.diagnostic_workspace import DiagnosticWorkspace
from src.apps.materials.backend.documents import DocumentReader
from src.auth.models import CurrentUser
from src.gateway import chat_stream
from src.gateway.schemas import ChatRequest
from src.platform import files
from src.platform.agent_runtime import dsh
from src.platform.agent_runtime.base import RunContext
from src.platform.agent_runtime.dsh import DshRuntime
from tests.unit.test_dsh_runtime import response

OPERATOR = CurrentUser(user_id="7", username="ops", role="operator")
VIEWER = CurrentUser(user_id="8", username="look", role="viewer")
CONFIG = "hostname R1\ninterface Gi0/0\n shutdown\n"


def test_an_upload_lands_in_the_netdisk_folder_for_chat(monkeypatch):
    saved = []
    monkeypatch.setattr(files, "save", lambda user, name, data, content_type: saved.append(
        (user.user_id, name, data, content_type)) or {"file_id": "file-" + "a" * 16, "name": name,
                                                      "size": len(data), "content_type": content_type})
    stored = attachments.upload(OPERATOR, "C:\\Users\\me\\R1.cfg", b"hostname R1")
    assert saved == [("7", "R1.cfg", b"hostname R1", "text/plain; charset=utf-8")]
    assert stored["kind"] == "text" and stored["file_id"] == "file-" + "a" * 16

    for name, data, status in [("tool.exe", b"x", 422), ("R1.cfg", b"", 413),
                               ("R1.cfg", b"x" * (attachments.MAX_BYTES + 1), 413)]:
        with pytest.raises(HTTPException) as refused:
            attachments.upload(OPERATOR, name, data)
        assert refused.value.status_code == status
    with pytest.raises(HTTPException) as refused:
        attachments.upload(VIEWER, "R1.cfg", b"x")
    assert refused.value.status_code == 403


def test_a_question_names_only_files_the_asker_may_read(monkeypatch):
    def describe(user, file_id):
        if file_id.endswith("f"):
            raise HTTPException(403, "无权访问")
        return {"file_id": file_id, "name": "申请表.xlsx", "size": 9, "content_type": "x"}

    monkeypatch.setattr(files, "describe", describe)
    found = attachments.resolve(OPERATOR, ["file-" + "1" * 16, "file-" + "1" * 16])
    assert found == [{"kind": "attachment", "file_id": "file-" + "1" * 16, "name": "申请表.xlsx",
                      "size": 9, "content_type": "x", "type": "file"}]
    with pytest.raises(HTTPException):
        attachments.resolve(OPERATOR, ["file-" + "f" * 16])
    with pytest.raises(HTTPException, match="最多"):
        attachments.resolve(OPERATOR, [f"file-{i:016x}" for i in range(6)])


def item(file_id, name, current=False, type_=None):
    return {"kind": "attachment", "file_id": file_id, "name": name, "size": 1, "content_type": "x",
            "type": type_ or attachments.kind_of(name), "current": current}


def test_the_last_message_holds_the_current_files_and_each_is_read_again(monkeypatch):
    rows = [
        {"role": "user", "content": "a", "references": [{"kind": "attachment", "file_id": "f1", "name": "old.cfg", "type": "text"}]},
        {"role": "assistant", "content": "b", "references": [{"kind": "business", "id": "evidence-1"}]},
        {"role": "user", "content": "c", "references": [
            {"kind": "attachment", "file_id": "f2", "name": "new.png", "type": "image"},
            {"kind": "attachment", "file_id": "f3", "name": "gone.cfg", "type": "text"}]},
    ]
    found = attachments.in_conversation(rows)
    assert [(entry["file_id"], entry["current"]) for entry in found] == [("f1", False), ("f2", True), ("f3", True)]

    def read(user, file_id):
        if file_id == "f3":
            raise HTTPException(404, "网盘文件不存在")
        return "x", "x", CONFIG.encode() if file_id == "f1" else b"\x89PNG"

    monkeypatch.setattr(files, "read", read)
    blind = attachments.load(OPERATOR, found, vision=False)
    assert blind[0]["text"] == CONFIG and "image" not in blind[1] and blind[2]["unavailable"]
    seeing = attachments.load(OPERATOR, found, vision=True)
    assert seeing[1]["image"] == b"\x89PNG" and seeing[1]["media_type"] == "image/png"

    text = attachments.note(blind, {"f1": "attachments/0123456789ab.txt"}, vision=False)
    assert "[本条消息附带的文件]\n- new.png：图片，当前模型不能查看\n- gone.cfg：已从网盘删除或不再可读" in text
    assert "old.cfg：文本，已放进资料目录 attachments/0123456789ab.txt" in text


def workspace(tmp_path, attached):
    source = tmp_path / "library"
    source.mkdir(parents=True, exist_ok=True)
    turn = DiagnosticWorkspace("7", run_id="turn-1", reader=DocumentReader(source), check=lambda: None)
    turn.attachments = attached
    turn.prepare(tmp_path / "turn" / "documents")
    return turn


def test_a_read_attachment_is_cited_by_its_lines(tmp_path):
    turn = workspace(tmp_path, [{**item("file-" + "c" * 16, "R1.cfg", True), "text": CONFIG}])
    [(relative, _)] = turn.attachment_entries()
    assert (turn.root / relative).read_text(encoding="utf-8") == CONFIG
    assert turn.describe_tool("read", {"file_path": relative}) == "用户附件 / R1.cfg"

    turn.record_read({"path": str(turn.root / relative), "lines": [
        {"number": 1, "text": "hostname R1"}, {"number": 2, "text": "interface Gi0/0"}]})
    assert f"[{relative}:L1-L2]" in turn.citation_guidance()
    [citation] = turn.references(f"主机名是 R1。[{relative}:L1-L2]")
    assert citation["kind"] == "attachment" and citation["title_path"] == ["用户附件", "R1.cfg"]
    assert citation["excerpt"] == "hostname R1\ninterface Gi0/0\n" and citation["lines"] == [1, 2]
    with pytest.raises(ValueError, match="unread"):
        turn.references(f"[{relative}:L3]")
    with pytest.raises(ValueError, match="Malformed"):
        turn.references("[attachments/R1.cfg:L1]")


def flow(**files_):
    return {"name": "itsm-firewall-change", "files": files_ or {"policy_file_url": [".xlsx"], "topology_file_url": [".json"]}}


def test_workflow_file_fields_come_from_the_attachments(monkeypatch):
    checked = []
    monkeypatch.setattr(files, "describe", lambda user, file_id: checked.append(file_id) or {})
    sheet, older, topo = (Attachment(file_id="file-1", name="REQ-1.xlsx", current=True),
                          Attachment(file_id="file-2", name="REQ-0.xlsx"),
                          Attachment(file_id="file-3", name="topo.json"))
    context = {}
    assert automation.fill_files(flow(), context, [sheet, older, topo], OPERATOR) == {}
    # The current message's sheet wins over an older one; the only JSON fills the topology.
    assert context == {"policy_file_url": "netdisk:file-1", "topology_file_url": "netdisk:file-3"}
    assert checked == ["file-1", "file-3"]

    two_now = [sheet, Attachment(file_id="file-4", name="REQ-2.xlsx", current=True)]
    context = {}
    assert automation.fill_files(flow(), context, two_now, OPERATOR) == {"policy_file_url": ["REQ-1.xlsx", "REQ-2.xlsx"]}
    assert "policy_file_url" not in context
    # The user picks one by name; that name becomes the file.
    context = {"policy_file_url": "REQ-2.xlsx"}
    automation.fill_files(flow(), context, two_now, OPERATOR)
    assert context["policy_file_url"] == "netdisk:file-4"


def test_start_workflow_asks_instead_of_guessing(monkeypatch):
    monkeypatch.setattr(automation, "chat_workflows", lambda: [{
        "name": "itsm-firewall-change", "description": "d", "steps": "s", "required": ["ticket_id", "policy_file_url"],
        "optional": ["topology_file_url"], "defaults": {}, "files": {"policy_file_url": [".xlsx"]}}])
    monkeypatch.setattr(files, "describe", lambda user, file_id: {})
    started = []
    monkeypatch.setattr("src.core.workflows.engine.WorkflowEngine.start",
                        lambda name, context, **kwargs: started.append(context) or "run-1")
    monkeypatch.setattr("src.core.workflows.registry.resolve_active_steps", lambda *args, **kwargs: [])
    monkeypatch.setattr("src.core.workflows.registry.get_template", lambda name: None)
    body = StartWorkflow(workflow="itsm-firewall-change", context={"ticket_id": "REQ-1"}, attachments=[
        Attachment(file_id="file-1", name="a.xlsx", current=True), Attachment(file_id="file-2", name="b.xlsx", current=True)])
    asked = automation.start_workflow(OPERATOR, body)
    assert asked["started"] is False and asked["ambiguous"] == {"policy_file_url": ["a.xlsx", "b.xlsx"]}
    body.attachments = body.attachments[:1]
    assert automation.start_workflow(OPERATOR, body)["started"] is True
    assert started == [{"ticket_id": "REQ-1", "policy_file_url": "netdisk:file-1"}]


def test_chat_requests_carry_the_attachment_ids(monkeypatch):
    begun = []
    monkeypatch.setattr(chat_stream.turns, "begin", lambda actor, conversation, body: begun.append(body) or ({"id": "r"}, True))
    chat_stream.prepare(ChatRequest(query="生成策略", thread_id="conv-1", attachments=["file-" + "b" * 16]),
                        SimpleNamespace(user_id="7"))
    assert begun[0].attachments == ["file-" + "b" * 16]


def test_images_go_to_dsh_as_image_blocks_only_when_present(tmp_path):
    assert dsh.prompt_content("问题", []) == "问题"
    blocks = dsh.prompt_content("问题", [{"image": b"\x89PNG", "media_type": "image/png"}])
    assert blocks == [{"type": "text", "text": "问题"}, {"type": "image", "data": "iVBORw==", "mimeType": "image/png"}]
    with_images = dsh.patch_file(tmp_path, 1, images=True).read_text(encoding="utf-8")
    assert "@deepseek-ai/dsh-attachment-local" in with_images
    assert "@deepseek-ai/dsh-attachment-local" not in dsh.patch_file(tmp_path, 1).read_text(encoding="utf-8")


def test_official_dsh_reads_an_attached_config_and_cites_it(tmp_path, monkeypatch):
    monkeypatch.setattr("src.platform.tools.registry.available_tools", lambda **kwargs: [])
    monkeypatch.setattr("src.apps.assistant.backend.workspace.authorize_worker", lambda *args: None)
    turn = DiagnosticWorkspace("7", run_id="turn-1", reader=DocumentReader(tmp_path), check=lambda: None)
    turn.attachments = [{**item("file-" + "d" * 16, "R1.cfg", True), "text": CONFIG}]
    relative = turn.attachment_paths()["file-" + "d" * 16]
    requests = []

    async def model(body):
        requests.append(body)
        if len(requests) == 1:
            return response({"tool_calls": [{"index": 0, "id": "r", "type": "function", "function": {
                "name": "read", "arguments": json.dumps({"file_path": relative})}}]}, "tool_calls")
        assert "hostname R1" in json.dumps(body["messages"], ensure_ascii=False)
        return response({"content": f"R1 的 Gi0/0 是关闭的。[{relative}:L2-L3]"}, "stop")

    async def start():
        return [e async for e in DshRuntime(transport=model).start(
            RunContext("turn-1", "7", "conversation", timeout_seconds=25),
            [{"role": "user", "content": "看一下附件里 Gi0/0 的状态" + attachments.note(turn.attachments, turn.attachment_paths(), vision=False)}],
            turn)]

    events = asyncio.run(start())
    assert events[-1].kind == "completed", [(e.kind, e.data) for e in events]
    [citation] = [e.data for e in events if e.kind == "citation"]
    assert citation["kind"] == "attachment" and citation["excerpt"] == "interface Gi0/0\n shutdown\n"
    assert re.search(r"放进资料目录 attachments/[0-9a-f]{12}\.txt", json.dumps(requests[0]["messages"], ensure_ascii=False))


def test_official_dsh_shows_an_attached_image_to_a_model_that_takes_images(tmp_path, monkeypatch):
    import base64

    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    monkeypatch.setattr("src.platform.tools.registry.available_tools", lambda **kwargs: [])
    monkeypatch.setattr("src.apps.assistant.backend.workspace.authorize_worker", lambda *args: None)
    turn = DiagnosticWorkspace("7", run_id="turn-1", reader=DocumentReader(tmp_path), check=lambda: None)
    turn.attachments = [{**item("file-" + "e" * 16, "topo.png", True), "image": png, "media_type": "image/png"}]
    parts = []

    async def model(body):
        parts.extend(part.get("type") for part in body["messages"][-1]["content"])
        return response({"content": "看到了"}, "stop")

    async def start():
        return [e async for e in DshRuntime(transport=model).start(
            RunContext("turn-1", "7", "conversation", timeout_seconds=25, model="deepseek-v4-flash-vision-exp"),
            [{"role": "user", "content": "这张拓扑图里有几台设备"}], turn)]

    events = asyncio.run(start())
    assert events[-1].kind == "completed", [(e.kind, e.data) for e in events]
    # DSH stored the image as an attachment and put it in the model request.
    assert "image_url" in parts


def test_only_the_latest_text_files_are_read_into_a_turn(monkeypatch):
    reads = []
    monkeypatch.setattr(files, "read", lambda user, file_id: reads.append(file_id) or ("x", "x", b"text"))
    many = [item(f"f{i}", f"c{i}.cfg") for i in range(attachments.MAX_READABLE + 2)]
    loaded = attachments.load(OPERATOR, many, vision=False)
    assert reads == [f"f{i}" for i in range(2, attachments.MAX_READABLE + 2)]
    assert loaded[0]["dropped"] and "text" not in loaded[0]
    assert "c0.cfg：较早的文本附件，本轮没有放进资料目录" in attachments.note(loaded, {}, vision=False)

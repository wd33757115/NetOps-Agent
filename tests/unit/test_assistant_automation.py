"""The assistant runs Skills and starts Workflows when the user asks, as that user."""

import asyncio
import json
import re
from types import SimpleNamespace

import pytest
import yaml

from src.apps.assistant.backend import automation
from src.apps.assistant.backend.automation import ReadRun, RunSkill, StartWorkflow
from src.apps.assistant.backend.diagnostic_workspace import DiagnosticWorkspace
from src.apps.materials.backend.documents import DocumentReader
from src.auth.models import CurrentUser
from src.platform.agent_runtime import dsh
from src.platform.agent_runtime.base import RunContext
from src.platform.agent_runtime.dsh import DshRuntime
from tests.unit.test_dsh_runtime import response

OPERATOR = CurrentUser(user_id="7", username="ops", role="operator")
VIEWER = CurrentUser(user_id="8", username="look", role="viewer")
DEMO = {
    "name": "demo-text-report",
    "description": "统计一段文本并生成报告",
    "steps": "统计文本 → 对比修改前后",
    "required": ["text"],
    "optional": ["ticket_id", "title", "text_after"],
    "defaults": {"title": "演示文本"},
}
STATS = {
    "name": "demo-text-stats",
    "description": "统计文本",
    "inputs": [{"name": "text", "required": True, "description": "要统计的文本"},
               {"name": "title", "required": False, "description": ""}],
}


@pytest.fixture
def offered(monkeypatch):
    monkeypatch.setattr(automation, "chat_workflows", lambda: [DEMO])
    monkeypatch.setattr(automation, "chat_skills", lambda user_id: [STATS])


def test_skills_are_offered_unless_they_disable_model_invocation(monkeypatch):
    # DSH skill standard: itsm-callback, llm-result-analyzer and device-backup say
    # disable-model-invocation: true; they only run as Workflow steps.
    monkeypatch.setattr("src.skill_system.governance.rollout.is_skill_executable",
                        lambda name, user_id=None: (True, ""))
    names = [skill["name"] for skill in automation.chat_skills("7")]
    assert names == ["config-diff-tool", "demo-text-stats"]


def test_published_workflows_with_a_chat_entry_are_offered(monkeypatch):
    monkeypatch.setattr("src.core.workflows.metadata_repo.is_plugin_chat_active", lambda name: True)
    flows = {flow["name"]: flow for flow in automation.chat_workflows()}
    assert {"demo-text-report", "itsm-firewall-change", "itsm-firewall-llm-analysis"} <= set(flows)
    # Retired patrol and rule workflows keep their chat files but are never offered.
    assert "patrol-change-event" not in flows
    assert flows["itsm-firewall-change"]["required"] == ["ticket_id", "policy_file_url"]
    assert "callback_url" in flows["itsm-firewall-change"]["optional"]


def test_a_viewer_can_neither_run_nor_start(offered):
    with pytest.raises(PermissionError, match="没有执行权限"):
        automation.run_skill(VIEWER, RunSkill(skill="demo-text-stats", params={"text": "x"}))
    with pytest.raises(PermissionError, match="没有执行权限"):
        automation.start_workflow(VIEWER, StartWorkflow(workflow="demo-text-report", context={"text": "x"}))


def test_run_skill_asks_for_missing_inputs_and_returns_downloads(offered, monkeypatch):
    executed = []

    def execute(name, params):
        executed.append((name, params))
        return {"success": True, "summary": "1 行，1 个词，1 个字符", "skill_name": name,
                "artifacts": {"config_zip": {"filename": "r.zip", "download_url": "/api/artifacts/download/?key=k"}}}

    monkeypatch.setattr("src.core.skills.executor.execute_skill", execute)
    asked = automation.run_skill(OPERATOR, RunSkill(skill="demo-text-stats", params={"title": "t"}))
    assert asked["executed"] is False and asked["missing"] == ["text"] and not executed

    with pytest.raises(PermissionError, match="没有开放给问答助手"):
        automation.run_skill(OPERATOR, RunSkill(skill="itsm-callback", params={}))

    done = automation.run_skill(OPERATOR, RunSkill(skill="demo-text-stats", params={"text": "x", "_user_id": "1"}))
    # The server names the user; a parameter cannot.
    assert executed == [("demo-text-stats", {"text": "x", "_user_id": "7"})]
    assert done["result"]["summary"] == "1 行，1 个词，1 个字符"
    assert done["downloads"] == [{"filename": "r.zip", "download_url": "/api/artifacts/download/?key=k"}]
    assert "artifacts" not in done["result"]


def test_start_workflow_runs_as_the_user_with_declared_context_only(offered, monkeypatch):
    started = []
    monkeypatch.setattr("src.core.workflows.engine.WorkflowEngine.start",
                        lambda name, context, **kwargs: started.append((name, context, kwargs)) or "run-1")
    asked = automation.start_workflow(OPERATOR, StartWorkflow(workflow="demo-text-report", context={"title": "x"}))
    assert asked == {"workflow": "demo-text-report", "started": False, "missing": ["text"],
                     "message": "启动前还需要：text"}

    done = automation.start_workflow(OPERATOR, StartWorkflow(
        workflow="demo-text-report", context={"text": "a b", "parent_run_id": "x", "ticket_id": "DEMO-7"}))
    assert started == [("demo-text-report", {"title": "演示文本", "text": "a b", "ticket_id": "DEMO-7"},
                        {"source": "chat", "user_id": "7"})]
    assert done["started"] is True and done["run_id"] == "run-1"
    assert done["ignored_fields"] == ["parent_run_id"]

    with pytest.raises(PermissionError, match="没有开放聊天启动"):
        automation.start_workflow(OPERATOR, StartWorkflow(workflow="patrol-change-event", context={}))


def test_a_run_is_read_only_by_its_owner(monkeypatch):
    run = SimpleNamespace(id="run-1", user_id="9", template_name="demo-text-report", status="completed",
                          ticket_id=None, error_message=None, created_at=None, completed_at=None)
    monkeypatch.setattr("src.core.workflows.repository.get_workflow_run", lambda run_id: run)
    monkeypatch.setattr("src.core.workflows.repository.list_workflow_steps", lambda run_id: [])
    with pytest.raises(PermissionError, match="不是当前账号启动的"):
        automation.read_workflow_run(OPERATOR, ReadRun(run_id="run-1"))
    run.user_id = "7"
    assert automation.read_workflow_run(OPERATOR, ReadRun(run_id="run-1"))["status"] == "completed"


def tool(name, model, mutates=True):
    return {"name": name, "description": "d", "input_schema": model.model_json_schema(), "mutates": mutates}


def workspace(monkeypatch, user, tmp_path):
    monkeypatch.setattr("src.platform.tools.registry.available_tools", lambda **kwargs: [
        tool(automation.RUN_SKILL, RunSkill), tool(automation.START_WORKFLOW, StartWorkflow),
        tool(automation.READ_RUN, ReadRun, False)] if kwargs.get("names") else [])
    monkeypatch.setattr("src.apps.assistant.backend.workspace.authorize_worker", lambda *args: None)
    monkeypatch.setattr("src.apps.assistant.backend.diagnostic_workspace.authorize_worker", lambda *args: user)
    source = tmp_path / "library"
    source.mkdir(parents=True, exist_ok=True)
    turn = DiagnosticWorkspace(user.user_id, run_id="turn-1", reader=DocumentReader(source), check=lambda: None)
    turn.prepare(tmp_path / "turn" / "documents")
    return turn


def test_a_turn_writes_a_dsh_skill_root_for_what_the_account_may_run(offered, monkeypatch, tmp_path):
    turn = workspace(monkeypatch, OPERATOR, tmp_path)
    names = {t["name"]: t for t in turn.definitions()}
    assert {automation.RUN_SKILL, automation.START_WORKFLOW, automation.READ_RUN} <= set(names)
    assert names[automation.START_WORKFLOW]["input_schema"]["properties"]["workflow"]["enum"] == ["demo-text-report"]
    assert names[automation.RUN_SKILL]["input_schema"]["properties"]["skill"]["enum"] == ["demo-text-stats"]

    # One DSH skill per Skill and per Workflow: <name>/SKILL.md, name and description first.
    files = sorted(path.relative_to(turn.skills_dir).as_posix() for path in turn.skills_dir.rglob("*.md"))
    assert files == ["demo-text-report/SKILL.md", "demo-text-stats/SKILL.md"]
    text = (turn.skills_dir / "demo-text-report" / "SKILL.md").read_bytes().decode()
    front = yaml.safe_load(text.split("---\n")[1])
    assert front == {"name": "demo-text-report", "description": "启动 Workflow：统计一段文本并生成报告"}
    assert "\r" not in text and "`text`（必填）" in text and "mcp__netops__assistant__start_workflow" in text
    assert "`title`=演示文本" in text
    assert "`text`（必填）：要统计的文本" in (turn.skills_dir / "demo-text-stats" / "SKILL.md").read_text("utf-8")

    viewer = workspace(monkeypatch, VIEWER, tmp_path / "viewer")
    assert not {t["name"] for t in viewer.definitions()} & automation.NAMES
    assert viewer.skills_dir is None
    with pytest.raises(PermissionError):
        viewer.invoke(automation.START_WORKFLOW, {"workflow": "demo-text-report", "context": {}})


def test_a_workflow_named_like_a_skill_is_told_apart(tmp_path):
    flow = {**DEMO, "name": "demo-text-stats"}
    root = automation.write_skill_catalog(tmp_path / "skills", [STATS], [flow])
    assert sorted(path.name for path in root.iterdir()) == ["demo-text-stats", "workflow-demo-text-stats"]


def test_the_prompt_explains_execution_only_when_offered():
    assert "assistant__start_workflow" in dsh.system_prompt(True, True)
    assert "assistant__start_workflow" not in dsh.system_prompt(True, False)


def test_official_dsh_loads_the_skill_then_starts_the_workflow(offered, monkeypatch, tmp_path):
    calls = []

    def invoke(name, arguments, **kwargs):
        calls.append((name, arguments, kwargs))
        return {"workflow": "demo-text-report", "started": True, "run_id": "run-42", "steps": "统计文本"}

    monkeypatch.setattr("src.platform.tools.registry.invoke", invoke)
    turn = workspace(monkeypatch, OPERATOR, tmp_path)
    requests = []
    start_tool = "mcp__netops__" + automation.START_WORKFLOW

    def call(name, arguments, identifier):
        return response({"tool_calls": [{"index": 0, "id": identifier, "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments)}}]}, "tool_calls")

    async def model(body):
        requests.append(body)
        if len(requests) == 1:
            assert "执行 Skill 与启动 Workflow" in body["messages"][0]["content"]
            # DSH's own catalog and loader, not a list NetOps renders into a description.
            catalog = json.dumps(body["messages"], ensure_ascii=False)
            assert "<available_skills>" in catalog and "`demo-text-report`" in catalog
            assert "skill" in {t["function"]["name"] for t in body["tools"]}
            return call("skill", {"name": "demo-text-report"}, "load")
        if len(requests) == 2:
            loaded = body["messages"][-1]["content"]
            assert '<skill_content name="demo-text-report">' in loaded and start_tool in loaded
            return call(start_tool, {"workflow": "demo-text-report", "context": {"text": "a b"}}, "start")
        identifier = re.findall(r"evidence-[0-9a-f]{24}", json.dumps(body["messages"]))[-1]
        return response({"content": f"已启动 demo-text-report，运行编号 run-42。[{identifier}]"}, "stop")

    async def start():
        return [e async for e in DshRuntime(transport=model).start(
            RunContext("turn-1", "7", "conversation", timeout_seconds=25),
            [{"role": "user", "content": "把 a b 生成一份文本报告"}], turn)]

    events = asyncio.run(start())
    assert events[-1].kind == "completed", [(e.kind, e.data) for e in events]
    assert calls == [(automation.START_WORKFLOW, {"workflow": "demo-text-report", "context": {"text": "a b"}},
                      {"actor_id": "7"})]
    assert [e.data["tool"] for e in events if e.kind == "citation"] == [automation.START_WORKFLOW]
    loaded = [e.data for e in events if e.kind == "tool_started" and e.data["tool"] == "skill"]
    assert loaded and loaded[0]["operation"] == 'skill({"name": "demo-text-report"})'

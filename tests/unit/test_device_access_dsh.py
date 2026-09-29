"""Official DSH/MCP executes the opt-in diagnostic contract against scripted evidence."""

import asyncio
import json
import re

from src.apps.assistant.backend.diagnostic_workspace import DiagnosticWorkspace
from src.apps.device_access.backend.schemas import Collect
from src.apps.materials.backend.documents import DocumentReader
from src.platform.agent_runtime.base import RunContext
from src.platform.agent_runtime.dsh import DshRuntime
from tests.unit.test_dsh_runtime import response


def test_official_dsh_can_collect_only_selected_session_and_cite_result(tmp_path, monkeypatch):
    source = tmp_path / "documents"
    source.mkdir()
    calls = []
    definitions = [
        {
            "name": "device_access__collect",
            "description": "Collect approved device evidence",
            "input_schema": Collect.model_json_schema(),
            "mutates": True,
        }
    ]
    monkeypatch.setattr(
        "src.platform.tools.registry.available_tools",
        lambda **kwargs: definitions if kwargs.get("names") else [],
    )
    monkeypatch.setattr(
        "src.apps.device_access.backend.saved.target", lambda *args: ("R1", "10.0.0.1", 22)
    )
    monkeypatch.setattr("src.apps.assistant.backend.workspace.authorize_worker", lambda *args: None)

    def invoke(name, arguments, **kwargs):
        calls.append((name, arguments))
        assert arguments["session_id"] == "approved"
        assert len(arguments["request_id"]) == 64
        return {
            "id": "observation",
            "status": "succeeded",
            "observed_at": "2026-09-28T01:00:00Z",
            "result": {
                "kind": "live_observation",
                "commands": [{"output": "Eth1/1 DOWN", "output_complete": True}],
            },
        }

    monkeypatch.setattr("src.platform.tools.registry.invoke", invoke)
    workspace = DiagnosticWorkspace(
        "operator",
        session_ids=["approved"],
        run_id="live-diagnosis",
        reader=DocumentReader(source),
        check=lambda: None,
    )
    requests = []

    async def model(body):
        requests.append(body)
        if len(requests) == 1:
            offered = next(
                t
                for t in body["tools"]
                if t["function"]["name"] == "mcp__netops__device_access__collect"
            )
            assert "request_id" not in offered["function"]["parameters"]["properties"]
            return response(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "live",
                            "type": "function",
                            "function": {
                                "name": "mcp__netops__device_access__collect",
                                "arguments": json.dumps(
                                    {
                                        "session_id": "approved",
                                        "profile_id": "comware-interfaces-v1",
                                    }
                                ),
                            },
                        }
                    ]
                },
                "tool_calls",
            )
        identifier = re.search(r"evidence-[0-9a-f]{24}", json.dumps(body["messages"])).group()
        return response(
            {"content": f"本轮接口采集显示 Eth1/1 DOWN；尚不能确认根因。[{identifier}]"}, "stop"
        )

    async def run():
        return [
            e
            async for e in DshRuntime(transport=model).start(
                RunContext("live-diagnosis", "operator", "conversation", timeout_seconds=25),
                [{"role": "user", "content": "诊断已授权设备的接口故障"}],
                workspace,
            )
        ]

    events = asyncio.run(run())
    assert events[-1].kind == "completed", [(e.kind, e.data) for e in events]
    assert len(calls) == 1
    assert next(e.data for e in events if e.kind == "citation")["tool"] == "device_access__collect"

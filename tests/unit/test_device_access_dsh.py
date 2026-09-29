"""Official DSH/MCP runs a diagnosis: trace the path, log into a device on it, cite the result."""

import asyncio
import json
import re

from src.apps.assistant.backend.diagnostic_workspace import DiagnosticWorkspace
from src.apps.device_access.backend.schemas import DiagnosisRun
from src.apps.materials.backend.documents import DocumentReader
from src.apps.topology.backend.tools import Trace
from src.platform.agent_runtime.base import RunContext
from src.platform.agent_runtime.dsh import DshRuntime
from tests.unit.test_dsh_runtime import response

TRACE = "mcp__netops__topology__trace_path"
RUN = "mcp__netops__device_access__run"


def call(name, arguments, identifier):
    return response(
        {
            "tool_calls": [
                {
                    "index": 0,
                    "id": identifier,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ]
        },
        "tool_calls",
    )


def test_official_dsh_logs_into_a_traced_device_and_cites_the_result(tmp_path, monkeypatch):
    source = tmp_path / "documents"
    source.mkdir()
    calls = []
    trace = {
        "name": "topology__trace_path",
        "description": "Trace both directions",
        "input_schema": Trace.model_json_schema(),
        "mutates": False,
        "evidence": True,
    }
    run = {
        "name": "device_access__run",
        "description": "Run read-only commands",
        "input_schema": DiagnosisRun.model_json_schema(),
        "mutates": True,
    }
    monkeypatch.setattr(
        "src.platform.tools.registry.available_tools",
        lambda **kwargs: [run] if kwargs.get("names") else [trace],
    )
    monkeypatch.setattr("src.apps.assistant.backend.workspace.authorize_worker", lambda *args: None)
    router = {"node_id": "n1", "address": "10.0.0.1", "collected": True, "platform": "comware"}

    def invoke(name, arguments, **kwargs):
        calls.append((name, arguments))
        if name == "topology__trace_path":
            hop = {"device": router, "in_interface": None, "out_interface": None, "route": None}
            return [{"forward": {"paths": [{"hops": [hop], "end": {"status": "no_route"}}]}}]
        assert (arguments["platform"], arguments["turn_id"]) == ("comware", "live-diagnosis")
        return {
            "host": "10.0.0.1",
            "login": "succeeded",
            "observed_at": "2026-09-30T01:00:00Z",
            "commands": [{"command": "display ip routing-table 3.3.3.3", "output": "no route"}],
        }

    monkeypatch.setattr("src.platform.tools.registry.invoke", invoke)
    workspace = DiagnosticWorkspace(
        "operator", run_id="live-diagnosis", reader=DocumentReader(source), check=lambda: None
    )
    requests = []

    async def model(body):
        requests.append(body)
        if len(requests) == 1:
            offered = next(t for t in body["tools"] if t["function"]["name"] == RUN)
            assert not {"platform", "turn_id"} & set(
                offered["function"]["parameters"]["properties"]
            )
            return call(TRACE, {"src": "4.4.4.4", "dst": "3.3.3.3"}, "trace")
        if len(requests) == 2:
            return call(
                RUN,
                {
                    "host": "10.0.0.1",
                    "commands": ["display ip routing-table 3.3.3.3"],
                    "purpose": "核对去程中断处的路由",
                },
                "run",
            )
        identifier = re.findall(r"evidence-[0-9a-f]{24}", json.dumps(body["messages"]))[-1]
        return response(
            {"content": f"已证实：10.0.0.1 没有到 3.3.3.3 的路由。[{identifier}]"}, "stop"
        )

    async def start():
        return [
            e
            async for e in DshRuntime(transport=model).start(
                RunContext("live-diagnosis", "operator", "conversation", timeout_seconds=25),
                [{"role": "user", "content": "4.4.4.4 到 3.3.3.3 不通"}],
                workspace,
            )
        ]

    events = asyncio.run(start())
    assert events[-1].kind == "completed", [(e.kind, e.data) for e in events]
    assert [name for name, _ in calls] == ["topology__trace_path", "device_access__run"]
    cited = [e.data["tool"] for e in events if e.kind == "citation"]
    assert "device_access__run" in cited

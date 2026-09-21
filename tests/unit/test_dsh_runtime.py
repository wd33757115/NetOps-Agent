"""Run the actual pinned DSH SDK/native tools against a local scripted model."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from starlette.responses import StreamingResponse

from src.apps.assistant.backend.workspace import DocumentWorkspace
from src.apps.materials.backend.documents import DocumentReader
from src.platform.agent_runtime.base import RunContext
from src.platform.agent_runtime.dsh import DshRuntime


def response(delta, finish):
    async def chunks():
        for value, reason in [(delta, None), ({}, finish)]:
            yield (
                "data: "
                + json.dumps(
                    {
                        "id": "fixture",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "deepseek-chat",
                        "choices": [{"index": 0, "delta": value, "finish_reason": reason}],
                    }
                )
                + "\n\n"
            )
        yield "data: [DONE]\n\n"

    return StreamingResponse(chunks(), media_type="text/event-stream")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "manual.md").write_text(
        "# RR\nBGP EVPN requires a route reflector.\n", encoding="utf8"
    )
    monkeypatch.setattr(
        "src.apps.assistant.backend.workspace.registry.available_tools", lambda **kw: []
    )
    return DocumentWorkspace("alice", reader=DocumentReader(source), check=lambda: None)


def test_native_read_and_verified_reference(workspace):
    requests = []

    async def model(body):
        requests.append(body)
        assert {t["function"]["name"] for t in body["tools"]} == {
            "read",
            "glob",
            "grep",
            "mcp__netops__submit_answer",
        }
        filename = next(iter(workspace.files))
        if len(requests) == 1:
            assert "尚未读取任何可引用证据" in body["messages"][0]["content"]
            # Direct reading is permitted; there is no mandatory search pipeline.
            return response(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "read1",
                            "type": "function",
                            "function": {
                                "name": "read",
                                "arguments": json.dumps(
                                    {"file_path": filename, "offset": 1, "limit": 2}
                                ),
                            },
                        }
                    ],
                },
                "tool_calls",
            )
        assert f"[{filename}:L1-L2]" in body["messages"][0]["content"]
        return response({"content": f"配置 RR。[{filename}:L1-L2]"}, "stop")

    async def run():
        runtime = DshRuntime(transport=model)
        result = [
            e
            async for e in runtime.start(
                RunContext("test", "alice", "conv", timeout_seconds=20),
                [{"role": "user", "content": "Explain RR"}],
                workspace,
            )
        ]
        assert not runtime.active
        return result

    events = asyncio.run(run())
    assert events[-1].kind == "completed", [(e.kind, e.data) for e in events]
    assert len(requests) == 2
    assert any(e.kind == "citation" for e in events)
    assert any(e.kind == "tool_finished" and e.data["success"] for e in events)
    metrics = next(e.data for e in events if e.kind == "metrics")
    assert metrics["authorization_checks"] > 0
    assert {"authorization", "tools", "setup", "citation_validation"} <= metrics[
        "timings_ms"
    ].keys()


def test_citation_guidance_preserves_gaps_and_does_not_authorize_new_lines(workspace, tmp_path):
    (workspace.reader.root / "manual.md").write_text(
        "# RR\nBGP EVPN\nrow3\nrow4\nrow5\nrow6\nrow7\n", encoding="utf8"
    )
    workspace.prepare(tmp_path / "workspace")
    assert "尚未读取" in workspace.citation_guidance()
    filename = next(iter(workspace.files))
    workspace.observed[filename] = {1, 3, 4, 7}
    guidance = workspace.citation_guidance()
    assert f"[{filename}:L1-L1]" in guidance
    assert f"[{filename}:L3-L4]" in guidance
    assert f"[{filename}:L7-L7]" in guidance
    assert f"[{filename}:L1-L7]" not in guidance
    assert "BGP EVPN" not in guidance  # Locations only, no source text in this message.
    with pytest.raises(ValueError, match="unread lines"):
        workspace.references(f"Unsupported claim [{filename}:L1-L7]")


@pytest.mark.parametrize("tool_call", [False, True])
def test_truncated_response_is_never_published_and_reports_the_cause(workspace, tool_call):
    async def model(body):
        from src.platform.agent_runtime.dsh import MAX_OUTPUT_TOKENS

        assert body["max_tokens"] == MAX_OUTPUT_TOKENS == 8192
        delta = {"content": "An incomplete configuration"}
        if tool_call:
            delta = {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "cut",
                        "type": "function",
                        "function": {
                            "name": "mcp__netops__submit_answer",
                            "arguments": '{"answer":"cut',
                        },
                    }
                ]
            }
        return response(delta, "length")

    async def run():
        runtime = DshRuntime(transport=model)
        events = [
            e
            async for e in runtime.start(
                RunContext("truncated", "alice", "conv", timeout_seconds=20), [], workspace
            )
        ]
        assert not runtime.active
        return events

    events = asyncio.run(run())
    assert events[-1].kind == "failed" and events[-1].data["code"] == "model_output_truncated"
    assert not any(e.kind in {"text_delta", "citation"} for e in events)
    metrics = next(e.data for e in events if e.kind == "metrics")
    assert metrics["model_finish_reason"] == "length"
    assert metrics["max_output_tokens"] == 8192


def test_long_validated_configuration_survives_sdk_and_answer_gate(workspace):
    requests = []
    expected = None

    async def model(body):
        nonlocal expected
        requests.append(body)
        filename = next(iter(workspace.files))
        if len(requests) == 1:
            return response(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "read",
                            "type": "function",
                            "function": {
                                "name": "read",
                                "arguments": json.dumps({"file_path": filename}),
                            },
                        }
                    ]
                },
                "tool_calls",
            )
        assert len(requests) == 2  # Checked completion never makes a redundant provider request.
        expected = (
            "```text\n"
            + "# configuration fixture\n" * 650
            + "return\n```\n"
            + f"[{filename}:L1-L2]"
        )
        return response(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "submit",
                        "type": "function",
                        "function": {
                            "name": "mcp__netops__submit_answer",
                            "arguments": json.dumps({"status": "answered", "answer": expected}),
                        },
                    }
                ]
            },
            "tool_calls",
        )

    async def run():
        return [
            e
            async for e in DshRuntime(transport=model).start(
                RunContext("long", "alice", "conv", timeout_seconds=25), [], workspace
            )
        ]

    events = asyncio.run(run())
    assert events[-1].kind == "completed", [(e.kind, e.data) for e in events if e.kind == "failed"]
    actual = next(e.data["text"] for e in events if e.kind == "text_delta")
    assert actual == expected and len(actual) > 12000
    assert any(e.kind == "citation" for e in events)


def test_finish_metadata_handles_split_sse_without_retaining_payloads():
    from src.platform.agent_runtime.usage import Usage

    usage = Usage()
    payload = (
        b'data: {"choices":[{"delta":{"content":"PRIVATE"},"finish_reason":"length"}],'
        b'"usage":{"completion_tokens":1500}}\r\n\r\n'
    )
    for index in range(0, len(payload), 7):
        usage.feed(payload[index : index + 7])
    assert usage.finish_reason == "length"
    assert usage.tokens == {"completion_tokens": 1500}
    assert b"PRIVATE" not in usage.buffer


@pytest.mark.parametrize("outcome", ["completed", "timeout", "unavailable"])
def test_provider_stream_metrics_and_failure_cleanup(workspace, monkeypatch, outcome):
    from types import SimpleNamespace

    import httpx

    from src.platform.agent_runtime.dsh import NATIVE, Bridge

    monkeypatch.setattr(
        "src.common.config.get_settings",
        lambda: SimpleNamespace(
            DEEPSEEK_BASE_URL="https://provider.invalid", DEEPSEEK_API_KEY="test"
        ),
    )

    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield (
                b'data: {"usage":{"prompt_tokens":12,"completion_tokens":3,"total_tokens":15}}\n\n'
            )
            if outcome == "timeout":
                raise httpx.ReadTimeout("private provider detail")

        async def aclose(self):
            self.closed = True

    stream = Stream()

    async def request_json():
        return {
            "messages": [{"role": "user", "content": "test"}],
            "tools": [{"function": {"name": name}} for name in NATIVE],
        }

    async def run():
        bridge = Bridge(RunContext("stream", "alice", "conv"), workspace, asyncio.Queue())
        status = 503 if outcome == "unavailable" else 200
        transport = httpx.MockTransport(lambda _: httpx.Response(status, stream=stream))
        async with httpx.AsyncClient(transport=transport) as client:
            bridge.client = client
            reply = await bridge.model(SimpleNamespace(json=request_json))
            if outcome == "unavailable":
                assert reply.status_code == 503
                assert b"private" not in reply.body
                assert bridge.failure_code == "model_unavailable"
            elif outcome == "timeout":
                with pytest.raises(httpx.ReadTimeout):
                    async for _ in reply.body_iterator:
                        pass
                assert bridge.failure_code == "provider_timeout"
            else:
                async for _ in reply.body_iterator:
                    pass
                assert bridge.statistics()["usage"]["total_tokens"] == 15
            assert bridge.statistics()["timings_ms"]["model"] >= 0
            assert stream.closed

    asyncio.run(run())


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("read", {"file_path": "../netops.patch.yml"}),
        ("write", {"file_path": "bad.txt", "content": "bad"}),
    ],
)
def test_native_guard_denies_escape_and_writes(workspace, tool, arguments):
    requests = []

    async def model(body):
        requests.append(body)
        if len(requests) == 1:
            return response(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "denied",
                            "type": "function",
                            "function": {"name": tool, "arguments": json.dumps(arguments)},
                        }
                    ]
                },
                "tool_calls",
            )
        return response({"content": "无法访问。"}, "stop")

    async def run():
        return [
            e
            async for e in DshRuntime(transport=model).start(
                RunContext("denied", "alice", "conv", timeout_seconds=20), [], workspace
            )
        ]

    events = asyncio.run(run())
    assert events[-1].kind == "completed", [(e.kind, e.data) for e in events]
    assert not workspace.observed
    assert not (workspace.root / "bad.txt").exists()
    assert any(
        "denied" in json.dumps(m).lower() or "not allowed" in json.dumps(m).lower()
        for m in requests[-1]["messages"]
    )


def test_deadline_closes_runtime(workspace):
    async def model(body):
        await asyncio.sleep(30)
        return response({"content": "too late"}, "stop")

    async def run():
        runtime = DshRuntime(transport=model)
        events = [
            e
            async for e in runtime.start(
                RunContext("slow", "alice", "conv", timeout_seconds=2), [], workspace
            )
        ]
        assert not runtime.active
        return events

    events = asyncio.run(run())
    assert events[-1].kind == "failed"
    assert events[-1].data["code"] == "deadline_exceeded"


def test_official_mcp_business_bridge(workspace, monkeypatch):
    calls, requests = [], []
    monkeypatch.setattr(
        workspace,
        "definitions",
        lambda: [
            {
                "name": "inspection__read_batch",
                "description": "Read a batch",
                "input_schema": {
                    "type": "object",
                    "properties": {"batch_id": {"type": "string"}},
                    "required": ["batch_id"],
                },
            }
        ],
    )
    monkeypatch.setattr(
        workspace, "invoke", lambda name, args: calls.append((name, args)) or {"state": "completed"}
    )

    async def model(body):
        requests.append(body)
        name = "mcp__netops__inspection__read_batch"
        assert name in {t["function"]["name"] for t in body["tools"]}
        if len(requests) == 1:
            return response(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "batch",
                            "type": "function",
                            "function": {"name": name, "arguments": '{"batch_id":"test-batch"}'},
                        }
                    ]
                },
                "tool_calls",
            )
        return response({"content": "巡检已完成。"}, "stop")

    async def run():
        return [
            e
            async for e in DshRuntime(transport=model).start(
                RunContext("mcp", "alice", "conv", timeout_seconds=20), [], workspace
            )
        ]

    events = asyncio.run(run())
    assert events[-1].kind == "completed", [(e.kind, e.data) for e in events]
    assert calls == [("inspection__read_batch", {"batch_id": "test-batch"})]


def test_cancel_owner_and_no_late_answer(workspace):
    entered = asyncio.Event()

    async def model(body):
        entered.set()
        await asyncio.sleep(30)
        return response({"content": "late"}, "stop")

    async def run():
        runtime = DshRuntime(transport=model)

        async def collect():
            return [
                e
                async for e in runtime.start(
                    RunContext("cancel", "alice", "conv", timeout_seconds=20), [], workspace
                )
            ]

        task = asyncio.create_task(collect())
        await asyncio.wait_for(entered.wait(), 10)
        with pytest.raises(PermissionError):
            await runtime.cancel("cancel", actor_id="bob")
        assert await runtime.cancel("cancel", actor_id="alice")
        events = await asyncio.wait_for(task, 8)
        assert not runtime.active
        assert events[-1].kind == "cancelled"
        assert not any(e.kind in {"completed", "text_delta"} for e in events)

    asyncio.run(run())


def test_reference_formats_and_unread_lines(workspace, tmp_path):
    workspace.prepare(tmp_path / "workspace")
    name = next(iter(workspace.files))
    source = workspace.files[name][2].text.splitlines()
    workspace.record_read(
        {
            "path": str(workspace.root / name),
            "lines": [{"number": i + 1, "text": line} for i, line in enumerate(source)],
        }
    )
    for suffix in ("L1", "L1-L2", "L1-2"):
        assert workspace.references(f"证据 [{name}:{suffix}]")
    for answer in (f"[{name}:L99]", "[CATALOG.md:L1]", "[fake.txt:L1]", f"[{name}:L2-L1]"):
        with pytest.raises(ValueError):
            workspace.references(answer)


@pytest.mark.parametrize("outcome", ["repair", "insufficient", "uncorrected"])
def test_sdk_recovers_citations_before_publishing(workspace, outcome):
    requests = []

    def call(name, arguments):
        return response(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": f"step{len(requests)}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ]
            },
            "tool_calls",
        )

    async def model(body):
        requests.append(body)
        filename = next(iter(workspace.files))
        if len(requests) == 1:
            return call("read", {"file_path": filename, "offset": 1, "limit": 2})
        if len(requests) == 2:
            return call(
                "mcp__netops__submit_answer",
                {"answer": f"配置 RR。[{filename}:L99]", "status": "answered"},
            )
        if len(requests) == 3:
            assert "citation_unread" in json.dumps(body["messages"])
            if outcome != "uncorrected":
                return call(
                    "mcp__netops__submit_answer",
                    {
                        "answer": f"配置 RR。[{filename}:L2]"
                        if outcome == "repair"
                        else "未经证实的业务事实",
                        "status": "answered" if outcome == "repair" else "insufficient_evidence",
                    },
                )
        return response({"content": "模型最后一条文本不应覆盖核验草稿。"}, "stop")

    async def run():
        return [
            e
            async for e in DshRuntime(transport=model).start(
                RunContext("repair", "alice", "conv", timeout_seconds=25, max_model_rounds=3),
                [],
                workspace,
            )
        ]

    events = asyncio.run(run())
    if outcome == "uncorrected":
        assert events[-1].kind == "failed"
        assert events[-1].data["code"] == "citation_unread"
        assert not any(e.kind == "text_delta" for e in events)
        return
    assert events[-1].kind == "completed", [(e.kind, e.data) for e in events]
    assert len(requests) == 3  # Checked finalization never spends another provider round.
    assert requests[-1]["tool_choice"]["function"]["name"] == "mcp__netops__submit_answer"
    answer = next(e.data["text"] for e in events if e.kind == "text_delta")
    assert "模型最后" not in answer and "未经证实" not in answer
    assert ("配置 RR" in answer) if outcome == "repair" else ("资料不足" in answer)
    assert any(e.kind == "tool_finished" and "未实际读取" in e.data["summary"] for e in events)
    assert any(e.kind == "tool_finished" and e.data["summary"] == "读取 2 行" for e in events)
    starts = [e.data["operation"] for e in events if e.kind == "tool_started"]
    assert all("配置 RR" not in op and "未经证实" not in op for op in starts)
    assert '"offset": 1' in starts[0]


def test_native_search_records_parameters_and_counts(workspace):
    requests = []

    async def model(body):
        requests.append(body)
        if len(requests) <= 2:
            name = "glob" if len(requests) == 1 else "grep"
            args = (
                {"pattern": "**/*.txt"}
                if name == "glob"
                else {"pattern": "EVPN", "include": "*.txt"}
            )
            return response(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": name,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ]
                },
                "tool_calls",
            )
        return response({"content": "需要阅读正文。"}, "stop")

    async def run():
        return [
            e
            async for e in DshRuntime(transport=model).start(
                RunContext("search", "alice", "conv", timeout_seconds=20), [], workspace
            )
        ]

    events = asyncio.run(run())
    assert events[-1].kind == "completed", [(e.kind, e.data) for e in events]
    summaries = [e.data["summary"] for e in events if e.kind == "tool_finished"]
    assert summaries == ["找到 1 个文件", "找到 1 处匹配"]
    assert not workspace.observed  # Search hits still cannot authorize citations.


def test_disconnect_after_started_reaps_pending_run(workspace):
    async def run():
        runtime = DshRuntime()
        stream = runtime.start(RunContext("disconnect", "alice", "conv"), [], workspace)
        assert (await anext(stream)).kind == "run_started"
        await stream.aclose()
        assert not runtime.active
        assert workspace.root is None  # Cancellation arrived before any SDK or file work.

    asyncio.run(run())


@pytest.mark.skipif(os.name == "nt", reason="Linux /proc process-tree verification")
def test_linux_deadline_reaps_runtime_descendants_and_temporary_home(workspace, monkeypatch):
    homes = []
    entered = asyncio.Event()
    runtime = DshRuntime()
    original = runtime._run_at

    async def capture(context, messages, bridge, home):
        homes.append(home)
        return await original(context, messages, bridge, home)

    async def model(body):
        entered.set()
        await asyncio.sleep(20)
        return response({"content": "late"}, "stop")

    runtime.transport = model
    monkeypatch.setattr(runtime, "_run_at", capture)

    async def run():
        results = [
            e
            async for e in runtime.start(
                RunContext("linux-deadline", "alice", "conv", timeout_seconds=3), [], workspace
            )
        ]
        assert results[-1].kind == "failed"
        assert entered.is_set()

    asyncio.run(run())
    assert homes and not any(home.exists() for home in homes)
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            cwd = os.readlink(process / "cwd")
        except (OSError, PermissionError):
            continue
        assert not any(cwd.startswith(str(home)) for home in homes), (
            f"Orphaned SDK process {process.name}"
        )

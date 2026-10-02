import json

from src.platform.agent_runtime.session_store import key
from src.platform.agent_runtime.timeline import (
    find_trace,
    render,
    render_requests,
    requests,
    requests_path,
    summarize,
    timeline,
)

QUESTION = "PC1(1.1.1.2) ping 不通 PC2(192.168.1.2)"
ANSWER = "根因：PC2 默认网关配置错误"
RUN = {
    "host": "10.10.1.2",
    "login": "succeeded",
    "commands": [
        {
            "command": "ping -c 3 192.168.1.2",
            "policy_level": "L2",
            "status": "succeeded",
            "output": "Ping ...\n3 packet(s) transmitted, 0 packet(s) received, 100.0% packet loss",
        },
        {
            "command": "display arp",
            "policy_level": "L1",
            "status": "succeeded",
            "output": "Type: S-Static D-Dynamic\nIP address MAC\n1.1.1.2 0050-7966-6802",
        },
    ],
}


def tool_call(identifier, name, arguments):
    return {
        "type": "tool-call",
        "id": identifier,
        "name": "mcp__netops__" + name,
        "arguments": json.dumps(arguments, ensure_ascii=False),
    }


def tool_result(identifier, text, error=False):
    return {
        "type": "tool/result",
        "time": 3000,
        "data": {
            "message": {
                "content": [
                    {
                        "type": "tool-result",
                        "toolCallId": identifier,
                        "content": [{"type": "text", "text": text}],
                        **({"isError": True} if error else {}),
                    }
                ]
            }
        },
    }


def write_trace(path):
    question = json.dumps([{"role": "user", "content": QUESTION}], ensure_ascii=False)
    events = [
        {
            "type": "agent/inbox/spliced",
            "time": 1000,
            "data": {
                "inserted": [{"role": "user", "content": [{"type": "text", "text": question}]}]
            },
        },
        {"type": "request/context", "time": 1001, "data": {"model": "deepseek-v4-flash"}},
        {
            "type": "assistant/message",
            "time": 2000,
            "data": {
                "step": 1,
                "message": {
                    "content": [
                        {"type": "reasoning", "text": "先分段"},
                        {"type": "text", "text": "先在网关上分段测试"},
                        tool_call("c1", "device_access__run", {"host": "10.10.1.2"}),
                        tool_call("c2", "device_access__run", {"host": "10.10.1.1"}),
                    ]
                },
            },
        },
        tool_result("c1", json.dumps({"data": RUN}, ensure_ascii=False)),
        tool_result("c2", "Error: 本轮诊断命令预算已用完", error=True),
        {
            "type": "assistant/message",
            "time": 4000,
            "data": {
                "step": 2,
                "message": {
                    "content": [
                        tool_call("c3", "submit_answer", {"status": "answered", "answer": ANSWER})
                    ]
                },
            },
        },
        {
            "type": "assistant/message",
            "time": 4500,
            "data": {"step": 3, "message": {"content": [{"type": "text", "text": ANSWER}]}},
        },
        {"type": "turn/end", "time": 5000, "data": {"reason": {"kind": "completed"}}},
    ]
    path.write_text(
        "\n".join(
            json.dumps({"method": "session.event", "payload": {"event": event}}, ensure_ascii=False)
            for event in events
        ),
        encoding="utf-8",
    )
    return path


def test_timeline_names_each_step_call_result_and_failure(tmp_path):
    run = timeline(write_trace(tmp_path / "run.jsonl"))
    assert run["question"] == QUESTION and run["model"] == "deepseek-v4-flash"
    assert run["end"] == "completed" and run["answer"] == ANSWER
    first = run["steps"][0]
    assert (
        first["at"] == 1.0
        and first["said"] == "先在网关上分段测试"
        and first["reasoning"] == "先分段"
    )
    probe, refused = first["calls"]
    assert probe["ok"] and "100.0% packet loss" in probe["result"]
    assert "display arp [L1/succeeded] 输出 3 行" in probe["result"]  # a legend is not shown
    assert refused["ok"] is False and refused["result"] == "本轮诊断命令预算已用完"


def test_render_folds_reasoning_and_does_not_repeat_the_answer(tmp_path):
    run = timeline(write_trace(tmp_path / "run.jsonl"))
    text = render(run)
    assert "✗ 出错：本轮诊断命令预算已用完" in text
    assert "（推理 3 字，加 --reasoning 查看）" in text and "推理：先分段" not in text
    assert "（最终答案，见文末）" in text and text.endswith(ANSWER)
    assert "推理：先分段" in render(run, reasoning=True)


def test_find_trace_by_run_id(tmp_path):
    runs = tmp_path / "home" / "scopes" / "scope" / "runs"
    runs.mkdir(parents=True)
    path = write_trace(runs / f"{key('run-1')}.jsonl")
    assert find_trace("run-1", root=tmp_path) == path
    assert find_trace("missing", root=tmp_path) is None


def test_a_baseline_comparison_reads_as_its_summary_then_what_changed():
    hunk = "@@ -33,2 +33,3 @@ interface GigabitEthernet2/0"
    result = {
        "summary": ["10.10.1.1：对比 2 条基线命令，1 条有变化，1 条无变化"],
        "devices": [
            {
                "host": "10.10.1.1",
                "commands": [
                    {
                        "command": "display current-configuration",
                        "status": "changed",
                        "diff": [hunk, "+ ospf network-type p2p"],
                    },
                    {"command": "display arp", "status": "unchanged"},
                ],
            }
        ],
    }
    text = summarize("device_access__compare_baseline", json.dumps({"data": result}))
    assert text.splitlines() == [
        result["summary"][0],
        "10.10.1.1 display current-configuration：",
        hunk,
        "+ ospf network-type p2p",
    ]


def test_requests_show_each_round_once_then_only_what_changed(tmp_path):
    tools = [{"type": "function", "function": {"name": "read", "description": "按行读取"}}]
    first = [
        {"role": "system", "content": "你是 NetOps 网络助手。\n尚未读取任何可引用证据。"},
        {"role": "user", "content": QUESTION},
    ]
    second = [
        {"role": "system", "content": "你是 NetOps 网络助手。\n[evidence-1]"},
        first[1],
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "read", "arguments": "{}"}}
        ]},
        {"role": "tool", "content": "x" * 2000},
    ]
    trace = tmp_path / "runs" / "run.jsonl"
    path = requests_path(trace)
    path.parent.mkdir(parents=True)
    rows = [
        {"round": n, "at": "t", "seconds": 1.0, "finish_reason": "stop", "error": None,
         "usage": {"prompt_tokens": 10, "completion_tokens": 2},
         "request": {"model": "m", "max_tokens": 8192, "messages": messages, "tools": tools}}
        for n, messages in ((1, first), (2, second))
    ]
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    text = render_requests(requests(trace))
    assert text.count("你是 NetOps 网络助手。") == 1  # the full prompt once
    assert "工具（1 个）：" in text and "  - read：按行读取" in text and "工具：同上一轮" in text
    assert "  -尚未读取任何可引用证据。" in text and "  +[evidence-1]" in text
    assert "消息：共 1 条，本轮新增 1 条" in text and "消息：共 3 条，本轮新增 2 条" in text
    assert text.count(QUESTION) == 1
    assert "  → 调用 read {}" in text and "（共 2000 字" in text
    assert '"max_tokens": 8192' in text


def test_a_row_lookup_reads_as_what_it_found():
    result = {
        "command": "display ip routing-table",
        "match": "192.168.1",
        "tables": [{
            "columns": ["Destination/Mask", "Proto"], "matches": 2,
            "entries": [{"status": "only_baseline"}, {"status": "changed"}],
        }],
        "sections": [{"section": "interface GigabitEthernet2/0", "same": True}],
    }
    assert summarize("device_access__compare_rows", json.dumps({"data": result})).splitlines() == [
        "display ip routing-table 查「192.168.1」",
        "  表 Destination/Mask：匹配 2 条（only_baseline 1，changed 1）",
        "  段 interface GigabitEthernet2/0：相同",
    ]

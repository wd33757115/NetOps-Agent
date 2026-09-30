import json

from src.platform.agent_runtime.session_store import key
from src.platform.agent_runtime.timeline import find_trace, render, summarize, timeline

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

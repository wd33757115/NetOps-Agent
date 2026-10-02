"""A readable timeline of one DSH run: what the model said, called and got back.

Reads the per-run trace the runtime already writes (``runs/<key(run_id)>.jsonl``
under the assistant session store). Pure reading: nothing here calls a device or
a model. The same data can feed the chat page's execution view later.
"""

import difflib
import json
from collections import Counter
from pathlib import Path

from .session_store import key

ROOT = Path(__file__).resolve().parents[3]
SESSIONS = ROOT / ".runtime/assistant-sessions"
PREFIX = "mcp__netops__"
CLIP = 240


def find_trace(run_id, root=None):
    matches = sorted(Path(root or SESSIONS).glob(f"**/runs/{key(run_id)}.jsonl"))
    return matches[0] if matches else None


def trace_for(actor_id, conversation_id, run_id, root=None):
    """The run's trace inside its own conversation home (see session_home)."""
    home = Path(root or SESSIONS) / key(json.dumps([actor_id, conversation_id]))
    matches = sorted(home.glob(f"scopes/*/runs/{key(run_id)}.jsonl"))
    return matches[0] if matches else None


def thoughts(path):
    """What the model said, and reasoned in thinking mode, before each round of calls.

    Keyed by the call IDs that followed, which are the execution view's step IDs.
    """
    return [
        {
            "call_ids": [call["id"] for call in step["calls"]],
            "said": step["said"].strip(),
            "reasoning": step["reasoning"].strip(),
        }
        for step in timeline(path)["steps"]
        if step["calls"] and (step["said"].strip() or step["reasoning"].strip())
    ]


def _clip(text, limit=CLIP):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + "…"


def _text(content):
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content or [] if isinstance(part, dict))


def _json(value):
    try:
        return json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return value


def _question(text):
    """The runtime sends the conversation as JSON; the question is its last user turn."""
    messages = _json(text)
    if isinstance(messages, list):
        users = [
            m.get("content") for m in messages if isinstance(m, dict) and m.get("role") == "user"
        ]
        if users:
            return str(users[-1])
    return str(text)


def _places(result):
    ports = [
        f"{port.get('name')}({port.get('address')}) {port.get('port')} VLAN{port.get('vlan')}"
        for location in result.get("locations", [])
        for port in location.get("ports", [])
    ]
    interfaces = [
        f"{item.get('interface')} {item.get('interface_ip') or item.get('gateway_ip')}"
        for item in result.get("router_interfaces") or result.get("gateways") or []
    ]
    text = f"{result.get('status')}：{'；'.join(ports) or '无接入位置'}"
    return text + (f"；网段接口 {'、'.join(interfaces)}" if interfaces else "")


def _locate(data):
    return " | ".join(_places(result) for result in data)


def _trace(data):
    rows = []
    for result in data:
        directions = []
        for name, label in (("forward", "去程"), ("reverse", "回程")):
            direction = result.get(name) or {}
            # Default hostnames repeat (every H3C is "H3C"); the address tells them apart.
            hops = [
                f"{hop['device'].get('name')}({hop['device'].get('address')})"
                for path in direction.get("paths", [])[:1]
                for hop in path.get("hops", [])
            ]
            directions.append(f"{label} {direction.get('status')}（{' → '.join(hops) or '无'}）")
        rows.append("，".join(directions) + f"，对称={result.get('symmetric')}")
    return " | ".join(rows)


def _items(data):
    items = data.get("items", [])
    commands = sorted({item["command"] for item in items if item.get("command")})
    return f"{len(items)} 条" + (f"：{'、'.join(commands)}" if commands else "")


def _baseline(data):
    """Each device's summary line, then table summaries and diff lines of what changed."""
    rows = list(data.get("summary", []))
    for device in data.get("devices", []):
        for item in device.get("commands", []):
            changed = [line for line in item.get("diff", []) if line[:1] in "+-@"]
            tables = [table.get("summary", "") for table in item.get("tables", [])]
            if changed or tables:
                rows.append(f"{device.get('host')} {item.get('command')}：")
                rows += [f"  {summary}" for summary in tables]
                rows += changed[:8] + (["…"] if len(changed) > 8 else [])
    return "\n".join(rows)


def _rows(data):
    """What a lookup found: entries by status, sections by name."""
    rows = [f"{data.get('command')} 查「{data.get('match')}」"]
    for table in data.get("tables", []):
        status = Counter(entry["status"] for entry in table.get("entries", []))
        rows.append(
            f"  表 {table['columns'][0]}：匹配 {table['matches']} 条（"
            + "，".join(f"{name} {count}" for name, count in status.items()) + "）"
        )
    for section in data.get("sections", []):
        rows.append(f"  段 {section['section']}：" + ("相同" if section.get("same") else "有差异"))
    return "\n".join(rows)


def _compare(data):
    """The earlier single-snapshot comparison, for traces recorded before it was replaced."""
    comparison = data.get("comparison") or {}
    text = f"{data.get('command')}：{data.get('status')}"
    if comparison:
        text += f"（+{comparison.get('added_count')} -{comparison.get('removed_count')}）"
    changed = [line for line in comparison.get("diff", []) if line[:1] in "+-@"]
    if changed:
        text += "\n" + "\n".join(changed[:8]) + ("\n…" if len(changed) > 8 else "")
    if data.get("quality_gaps"):
        text += f"\n缺口：{'、'.join(data['quality_gaps'])}"
    return text


def _key_line(output):
    """A probe's verdict (ping loss, tracert's last hop); for displays only the size,
    since their first line is a legend that means nothing out of context."""
    lines = [line.strip() for line in str(output).splitlines() if line.strip()]
    for line in lines:
        if "packet loss" in line or "packet(s) received" in line:
            return line
    if lines and lines[0].lower().startswith(("traceroute", "tracert")):
        return lines[-1]
    return f"输出 {len(lines)} 行" if lines else "（无输出）"


def _run(data):
    if data.get("login") not in (None, "succeeded"):
        return f"登录 {data.get('login')}：{data.get('failure_code') or data.get('notice') or ''}"
    rows = []
    for item in data.get("commands", []):
        status = f"{item.get('policy_level') or '-'}/{item.get('status')}"
        detail = item.get("reason") or item.get("failure_code") or _key_line(item.get("output", ""))
        rows.append(f"{item.get('command')} [{status}] {_clip(detail, 120)}")
    return "\n".join(rows)


SUMMARIES = {
    "topology__locate": _locate,
    "topology__trace_path": _trace,
    "inspection__list_snapshots": _items,
    "inspection__list_events": _items,
    "inspection__list_runs": _items,
    "device_access__compare_baseline": _baseline,
    "device_access__compare_rows": _rows,
    "device_access__compare_config": _compare,
    "device_access__run": _run,
}


def summarize(tool, text):
    payload = _json(text)
    if not isinstance(payload, (dict, list)):
        return _clip(text)
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    handler = SUMMARIES.get(tool)
    try:
        return handler(data) if handler else _clip(json.dumps(data, ensure_ascii=False))
    except (AttributeError, KeyError, TypeError, IndexError):
        return _clip(json.dumps(data, ensure_ascii=False))


def timeline(path):
    """{model, question, steps: [{step, at, said, reasoning, calls}], answer, end}."""
    events = [
        event
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
        for event in [json.loads(line).get("payload", {}).get("event")]
        if event
    ]
    start = events[0]["time"] if events else 0
    result = {"model": None, "question": None, "steps": [], "answer": None, "end": None}
    steps, calls = {}, {}
    for event in events:
        kind, data = event.get("type"), event.get("data") or {}
        if kind == "request/context":
            result["model"] = data.get("model")
        elif kind == "agent/inbox/spliced" and result["question"] is None:
            for message in data.get("inserted", []):
                if message.get("role") == "user":
                    result["question"] = _question(_text(message.get("content")))
                    break
        elif kind == "assistant/message":
            number = data.get("step")
            step = steps.setdefault(
                number,
                {
                    "step": number,
                    "at": round((event["time"] - start) / 1000, 1),
                    "said": "",
                    "reasoning": "",
                    "calls": [],
                },
            )
            for part in data["message"]["content"]:
                if part.get("type") == "text":
                    step["said"] += part.get("text", "")
                elif part.get("type") in ("reasoning", "thinking"):
                    step["reasoning"] += part.get("text", "")
                elif part.get("type") == "tool-call":
                    call = {
                        "id": part["id"],
                        "tool": part["name"].removeprefix(PREFIX),
                        "arguments": _json(part.get("arguments")),
                        "ok": None,
                        "result": "",
                    }
                    calls[part["id"]] = call
                    step["calls"].append(call)
                    if call["tool"] == "submit_answer" and isinstance(call["arguments"], dict):
                        result["answer"] = call["arguments"].get("answer")
        elif kind == "tool/result":
            for part in data.get("message", {}).get("content", []):
                call = calls.get(part.get("toolCallId"))
                if call is not None:
                    text = _text(part.get("content"))
                    call["ok"] = not part.get("isError")
                    call["result"] = (
                        summarize(call["tool"], text)
                        if call["ok"]
                        else text.removeprefix("Error: ")
                    )
        elif kind == "turn/end":
            result["end"] = (data.get("reason") or {}).get("kind")
    result["steps"] = [steps[number] for number in sorted(steps, key=lambda n: n or 0)]
    return result


def render(run, *, reasoning=False):
    calls = [call for step in run["steps"] for call in step["calls"]]
    failed = sum(call["ok"] is False for call in calls)
    lines = [
        f"模型 {run['model']} · {len(run['steps'])} 步 · {len(calls)} 次工具调用"
        f"（{failed} 次出错） · 结束：{run['end']}",
        f"问题：{run['question']}",
        "",
    ]
    for step in run["steps"]:
        lines.append(f"[{step['at']:>6.1f}s] 第 {step['step']} 步")
        said = step["said"].strip()
        if said and run["answer"] and not step["calls"] and said[:40] == run["answer"].strip()[:40]:
            lines.append("  说：（最终答案，见文末）")
        elif said:
            lines.append(f"  说：{_clip(said, 600)}")
        if step["reasoning"]:
            if reasoning:
                lines.append(f"  推理：{step['reasoning'].strip()}")
            else:
                lines.append(f"  （推理 {len(step['reasoning'])} 字，加 --reasoning 查看）")
        for call in step["calls"]:
            if call["tool"] == "submit_answer":
                lines.append("  → 提交答案")
                continue
            arguments = json.dumps(call["arguments"], ensure_ascii=False)
            lines.append(f"  → {call['tool']} {_clip(arguments, 300)}")
            mark = "✓ " if call["ok"] else ("✗ 出错：" if call["ok"] is False else "…（无结果）")
            body = str(call["result"]).splitlines() or [""]
            lines.append(f"    {mark}{body[0]}")
            lines.extend(f"      {line}" for line in body[1:])
        lines.append("")
    if run["answer"]:
        lines += ["答案：", run["answer"].strip()]
    return "\n".join(lines)


def requests_path(trace):
    """Next to the trace (not among traces): each round's request as the provider got it."""
    return Path(trace).parent / "requests" / Path(trace).name


def requests(trace):
    path = requests_path(trace)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _message(message, limit):
    role = message.get("role", "?")
    text = _text(message.get("content"))
    if role == "tool" and len(text) > limit:
        text = text[:limit] + f"…（共 {len(text)} 字，完整内容用 --request-json 查看）"
    lines = [f"[{role}] {text}".rstrip()]
    if message.get("reasoning_content"):
        lines.append(f"  （推理 {len(message['reasoning_content'])} 字）")
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        lines.append(f"  → 调用 {function.get('name')} {function.get('arguments')}")
    return lines


def render_requests(rows, *, limit=1500):
    """What the model was sent, round by round: settings, tools, system prompt, messages.

    Later rounds show only what changed; --request-json gives any round in full.
    """
    lines, previous = [], None
    for row in rows:
        request = row["request"]
        usage = row.get("usage") or {}
        messages = request.get("messages", [])
        system = messages[0] if messages and messages[0].get("role") == "system" else None
        history = messages[1:] if system else messages
        tools = request.get("tools", [])
        names = [tool["function"]["name"] for tool in tools]
        settings = {k: v for k, v in request.items() if k not in {"messages", "tools"}}
        lines += [
            f"===== 第 {row['round']} 轮 · {row['at']} · 用时 {row['seconds']} 秒 · "
            f"输入 {usage.get('prompt_tokens', '?')} token，输出 "
            f"{usage.get('completion_tokens', '?')} token · 结束 {row.get('finish_reason')}"
            + (f" · 错误 {row['error']}" if row.get("error") else ""),
            f"参数：{json.dumps(settings, ensure_ascii=False)}",
        ]
        if previous is None or names != previous["names"]:
            lines.append(f"工具（{len(names)} 个）：")
            lines += [
                f"  - {tool['function']['name']}：{tool['function'].get('description', '')}"
                for tool in tools
            ]
        else:
            lines.append("工具：同上一轮")
        text = _text(system["content"]) if system else ""
        if previous is None:
            lines += [f"系统提示（{len(text)} 字）：", text]
        elif text == previous["system"]:
            lines.append("系统提示：同上一轮")
        else:
            lines.append("系统提示：与上一轮不同的行：")
            lines += [
                f"  {line}"
                for line in difflib.unified_diff(
                    previous["system"].splitlines(), text.splitlines(), lineterm="", n=0
                )
                if line[:1] in "+-" and not line.startswith(("+++", "---"))
            ]
        seen = previous["history"] if previous else []
        if history[: len(seen)] == seen:
            new = history[len(seen) :]
            lines.append(f"消息：共 {len(history)} 条，本轮新增 {len(new)} 条")
        else:
            new = history
            lines.append(f"消息：历史被改写，共 {len(history)} 条，全部列出")
        for message in new:
            lines += _message(message, limit)
        lines.append("")
        previous = {"names": names, "system": text, "history": history}
    return "\n".join(lines) if lines else "本次运行没有模型请求记录（此功能上线前的运行没有记录）。"

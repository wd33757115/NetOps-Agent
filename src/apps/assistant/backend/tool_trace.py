"""Bounded, redacted tool operations and result previews; never model reasoning."""

import json
import re
from pathlib import Path

FIELDS = {
    "read": ("file_path", "offset", "limit"),
    "glob": ("pattern", "path"),
    "grep": ("pattern", "path", "include"),
    "skill": ("name",),
}
BUSINESS_FIELDS = {
    "device_id",
    "batch_id",
    "run_id",
    "snapshot_id",
    "event_id",
    "query",
    "limit",
    "offset",
    "status",
    "purpose",
    "command",
    "since",
    "until",
    "src",
    "dst",
    "host",
    "commands",
    "skill",
    "workflow",
}


def clean(value, limit=400):
    value = re.sub(r"[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]", " ", str(value))
    value = re.sub(
        r"(?i)(password|passwd|secret|token|authorization|api[_-]?key)\s*[:=]\s*\S+",
        r"\1=[redacted]",
        value,
    )
    return value[:limit] + ("…" if len(value) > limit else "")


def operation(root, name, arguments):
    short = name.removeprefix("mcp__netops__")
    if short == "submit_answer":
        return "submit_answer(status=" + json.dumps(arguments.get("status", "")) + ")"
    fields = FIELDS.get(name, sorted(BUSINESS_FIELDS))
    safe = {}
    for key in fields:
        value = arguments.get(key)
        if key == "commands" and isinstance(value, list):
            safe[key] = [clean(item) for item in value[:5] if isinstance(item, str)]
            continue
        if value is None or not isinstance(value, (str, int, bool, float)):
            continue
        if key in {"path", "file_path"}:
            try:
                resolved = (root / value).resolve()
                value = resolved.relative_to(root).as_posix()
            except (ValueError, TypeError, OSError):
                value = "[资料范围外路径]"
        elif isinstance(value, str) and (
            Path(value).is_absolute() or re.match(r"^[A-Za-z]:", value)
        ):
            value = "[本地路径]"
        safe[key] = clean(value) if isinstance(value, str) else value
    return short + "(" + json.dumps(safe, ensure_ascii=False, separators=(", ", ": "))[:1600] + ")"


def summary(name, data, success):
    if not success:
        return "工具调用失败，未取得可用结果"
    if not isinstance(data, dict):
        return ""
    if name == "read":
        count = data.get("lines")
        if isinstance(count, int):
            return f"读取 {count} 行" + (
                "；结果有截断，请按需补读" if data.get("truncated") else ""
            )
    if name in {"grep", "glob"} and isinstance(data.get("count"), int):
        noun = "处匹配" if name == "grep" else "个文件"
        return f"找到 {data['count']} {noun}" + (
            "；结果已截断，可缩小搜索范围" if data.get("truncated") else ""
        )
    return ""


def result_preview(root, output, *, limit=12000):
    if not isinstance(output, str):
        return None
    # Native payloads are JSON. Preserve their line numbers and object structure,
    # but never render HTML or reveal host paths or credential fields.
    secret = re.compile(r"(?i)password|passwd|secret|token|authorization|api[_-]?key")

    def scrub(value):
        if isinstance(value, dict):
            return {
                key: "[redacted]" if secret.search(key) else scrub(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, str):
            for prefix in (str(root), str(root).replace("\\", "/")) if root else ():
                value = value.replace(prefix + "\\", "").replace(prefix + "/", "")
            value = re.sub(
                r"(?i)((?:password|passwd|secret|token|authorization|api[_-]?key)\s*[:=]\s*)[^\s,;]+",
                r"\1[redacted]",
                value,
            )
            return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u202a-\u202e\u2066-\u2069]", "", value)
        return value

    try:
        text = json.dumps(scrub(json.loads(output)), ensure_ascii=False, indent=2)
    except (ValueError, TypeError):
        text = scrub(output)
    return {
        "text": text[:limit] if limit is not None else text,
        "truncated": (limit is not None and len(text) > limit) or len(output) >= 64000,
    }

import json

from src.apps.assistant.backend import tool_trace


def test_trace_bounds_and_redacts_arguments_without_copying_result_or_answer(tmp_path):
    source = tmp_path / "manual.txt"
    text = tool_trace.operation(
        tmp_path, "read", {"file_path": str(source), "offset": 80, "limit": 20, "secret": "PRIVATE"}
    )
    assert '"file_path": "manual.txt"' in text and '"offset": 80' in text
    assert "PRIVATE" not in text and str(tmp_path) not in text
    outside = tool_trace.operation(tmp_path, "read", {"file_path": "../outside.txt"})
    assert "资料范围外" in outside
    search = tool_trace.operation(
        tmp_path, "grep", {"pattern": "password=PRIVATE\nEVPN" + "x" * 2000}
    )
    assert "PRIVATE" not in search and len(search) < 500
    draft = tool_trace.operation(
        tmp_path, "mcp__netops__submit_answer", {"answer": "PRIVATE", "status": "answered"}
    )
    assert "PRIVATE" not in draft and "answered" in draft


def test_result_preview_keeps_matches_and_lines_but_bounds_and_redacts(tmp_path):
    output = tool_trace.result_preview(
        tmp_path,
        json.dumps(
            {
                "matches": [
                    {"path": str(tmp_path / "manual.txt"), "line": 3, "text": "精油 description"}
                ],
                "password": "PRIVATE",
                "text": "token=PRIVATE",
            }
        ),
    )
    assert "精油 description" in output["text"]
    assert '"line": 3' in output["text"] and "manual.txt" in output["text"]
    assert "PRIVATE" not in output["text"] and str(tmp_path) not in output["text"]
    assert not output["truncated"]
    limited = tool_trace.result_preview(tmp_path, json.dumps({"text": "x" * 15000}))
    assert limited["truncated"] and len(limited["text"]) == 12000


def test_diagnosis_operation_includes_endpoints_host_and_bounded_commands(tmp_path):
    route = tool_trace.operation(
        tmp_path,
        "mcp__netops__topology__trace_path",
        {
            "src": "1.1.1.2",
            "dst": "192.168.1.2",
            "password": "PRIVATE",
        },
    )
    assert '"src": "1.1.1.2"' in route and '"dst": "192.168.1.2"' in route
    login = tool_trace.operation(
        tmp_path,
        "mcp__netops__device_access__run",
        {
            "host": "10.10.1.1",
            "commands": ["display interface", "token=PRIVATE"],
            "purpose": "检查接口",
            "password": "PRIVATE",
        },
    )
    assert "10.10.1.1" in login and "display interface" in login
    assert "PRIVATE" not in route + login

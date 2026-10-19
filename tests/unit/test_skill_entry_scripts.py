"""Entry scripts as the platform calls them: python <script> --params params.json [--output-dir dir]."""

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from src.core.skills import executor

ROOT = Path(__file__).resolve().parents[2]


def test_a_script_that_reads_stdin_gets_end_of_input_instead_of_hanging(tmp_path, monkeypatch):
    script = tmp_path / "run.py"
    script.write_text(
        "import json, sys\n"
        "data = sys.stdin.read()\n"
        "print(json.dumps({'success': True, 'stdin': data}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(executor, "resolve_entry_script", lambda _name: script)
    monkeypatch.setattr(executor, "get_entry_output_mode", lambda _name: "none")
    monkeypatch.setattr(executor, "get_skill_cwd", lambda _name: tmp_path)
    monkeypatch.setattr(executor, "get_skill_execution_timeout_seconds", lambda _name: 20)

    started = time.monotonic()
    result = executor._run_subprocess_skill("stdin-reader", {})

    assert result == {"success": True, "stdin": ""}
    assert time.monotonic() - started < 15


def _run(script: str, params: dict, tmp_path: Path, *extra: str) -> dict:
    params_path = tmp_path / "params.json"
    params_path.write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(ROOT / script), "--params", str(params_path), *extra],
        capture_output=True, stdin=subprocess.DEVNULL, timeout=60, check=False,
        env=executor._skill_subprocess_env(),
    )
    return json.loads(completed.stdout.decode("utf-8").strip().splitlines()[-1])


def test_config_diff_reads_the_params_file_the_platform_passes(tmp_path):
    result = _run("src/skills/config-diff-tool/scripts/run.py",
                  {"config1": "hostname R1\n", "config2": "hostname R2\n"}, tmp_path)

    assert result["success"] is True
    assert result["data"]["added"] == ["hostname R2"] and result["data"]["removed"] == ["hostname R1"]


def test_the_demo_skill_counts_the_text_and_writes_its_report(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    result = _run("src/skills/demo-text-stats/scripts/run.py",
                  {"text": "hostname R1\ninterface Gi0/0\n shutdown", "title": "R1 配置"}, tmp_path,
                  "--output-dir", str(output))

    assert result == {"success": True, "summary": "3 行，5 个词，37 个字符", "lines": 3, "words": 5, "chars": 37}
    assert (output / "report.md").read_text(encoding="utf-8").startswith("# R1 配置")
    assert _run("src/skills/demo-text-stats/scripts/run.py", {"text": "  "}, tmp_path) == {
        "success": False, "error": "text is empty"}


def test_the_itsm_callback_says_when_its_download_links_are_not_reachable(tmp_path):
    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    params = {"ticket_id": "REQ-1", "callback_url": f"http://127.0.0.1:{server.server_port}/cb",
              "config_files_url": "/api/artifacts/download/?key=a", "config_filename": "策略.zip",
              "change_excel_url": "https://netops.example.com/api/artifacts/download/?key=b"}
    try:
        relative = _run("src/skills/itsm-callback/scripts/itsm_callback.py", params, tmp_path)
        absolute = _run("src/skills/itsm-callback/scripts/itsm_callback.py",
                        {**params, "config_files_url": "https://netops.example.com/x"}, tmp_path)
    finally:
        server.shutdown()

    # The callback still goes out; the run shows why the ITSM side cannot download.
    assert relative["success"] is True and len(received) == 2
    assert "策略.zip" in relative["warning"] and "PUBLIC_APP_URL" in relative["message"]
    assert absolute["warning"] is None and absolute["message"] == "ITSM 回调成功"

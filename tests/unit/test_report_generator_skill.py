from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from src.core.skills import executor
from src.core.skills.resolver import get_entry_output_mode

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_SCRIPT = PROJECT_ROOT / "src" / "skills" / "report-generator" / "scripts" / "run.py"


def _load_run_module():
    spec = importlib.util.spec_from_file_location("report_generator_skill_run", RUN_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_generator_accepts_platform_output_dir(tmp_path, monkeypatch, capsys):
    module = _load_run_module()
    params_path = tmp_path / "params.json"
    output_dir = tmp_path / "output"
    params_path.write_text(
        json.dumps({"run_id": "run:2026/07", "output_format": "markdown"}),
        encoding="utf-8",
    )

    def fake_render_run_report(**kwargs):
        output_path = Path(kwargs["output_path"])
        output_path.write_text("# report\n", encoding="utf-8")
        return {"success": True, "output_path": str(output_path)}

    monkeypatch.setattr(module, "render_run_report", fake_render_run_report)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(RUN_SCRIPT),
            "--params",
            str(params_path),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["output_filename"] == "patrol-report-run-2026-07.md"
    assert (output_dir / payload["output_filename"]).read_text(encoding="utf-8") == "# report\n"


def test_executor_exposes_report_artifact_instead_of_config_zip(tmp_path, monkeypatch):
    assert get_entry_output_mode("report-generator") == "dir"
    monkeypatch.setattr(executor, "get_minio_storage", lambda: None)

    def fake_run(command, **_kwargs):
        output_dir = Path(command[command.index("--output-dir") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "patrol-report-run-1.md"
        report_path.write_text("# report\n", encoding="utf-8")
        stdout = json.dumps(
            {
                "success": True,
                "output_path": str(report_path),
                "output_filename": report_path.name,
            }
        ).encode()
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    monkeypatch.setattr(executor.subprocess, "run", fake_run)
    result = executor._run_subprocess_skill(
        "report-generator",
        {"run_id": "run-1", "output_format": "markdown"},
    )

    assert "report" in result["artifacts"]
    assert "config_zip" not in result["artifacts"]
    assert result["artifacts"]["report"]["filename"] == "patrol-report-run-1.md"
    assert result["output_path"] == "patrol-report-run-1.md"

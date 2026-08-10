from __future__ import annotations

import subprocess
import time

import pytest

from src.common.deadline import WallClockTimeoutError, call_with_wall_clock_timeout
from src.core.skills import executor, resolver
from src.core.skills.executor import SkillExecutionError


def test_wall_clock_timeout_does_not_wait_for_the_provider_thread():
    started = time.monotonic()

    with pytest.raises(WallClockTimeoutError, match="provider test exceeded"):
        call_with_wall_clock_timeout(
            lambda: time.sleep(0.2),
            timeout_seconds=0.01,
            operation="provider test",
        )

    assert time.monotonic() - started < 0.15


def test_skill_timeout_is_read_from_frontmatter(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "_load_frontmatter",
        lambda _skill_name: {"execution_timeout_seconds": 1650},
    )

    assert resolver.get_skill_execution_timeout_seconds("textfsm-generator") == 1650


def test_subprocess_timeout_returns_a_stable_failure_code(monkeypatch, tmp_path):
    script = tmp_path / "run.py"
    script.write_text("print('unused')", encoding="utf-8")
    monkeypatch.setattr(executor, "resolve_entry_script", lambda _name: script)
    monkeypatch.setattr(executor, "get_entry_output_mode", lambda _name: "none")
    monkeypatch.setattr(executor, "get_skill_cwd", lambda _name: tmp_path)
    monkeypatch.setattr(
        executor,
        "get_skill_execution_timeout_seconds",
        lambda _name: 17,
    )
    monkeypatch.setattr(
        executor.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("python run.py", 17)
        ),
    )

    with pytest.raises(SkillExecutionError) as captured:
        executor._run_subprocess_skill("textfsm-generator", {"job_id": "job-1"})

    assert captured.value.payload["failure_code"] == "skill_execution_timeout"
    assert captured.value.payload["timeout_seconds"] == 17

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.apps.rule_governance.backend.generators import runner


@pytest.mark.parametrize("name", ["../run.py", "device-backup", "firewall-policy-generator"])
def test_only_package_generators_are_callable(monkeypatch, name):
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *a, **k: pytest.fail("unexpected subprocess")
    )
    with pytest.raises(runner.GeneratorExecutionError, match="unknown"):
        runner.execute_generator(name, {})


def test_fixed_entry_timeout_unicode_and_temporary_input_cleanup(monkeypatch):
    paths = []

    def invoke(command, **kwargs):
        script, params = Path(command[1]), Path(command[3])
        paths.append(params)
        assert script == runner.ROOT / "textfsm_generator/scripts/run.py"
        assert Path(kwargs["cwd"]) == script.parent
        assert kwargs["timeout"] == 1650
        assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
        assert json.loads(params.read_text(encoding="utf-8")) == {"job_id": "中文任务"}
        return SimpleNamespace(
            returncode=0, stdout=b'log\n{"success":true,"attempts":2}\n', stderr=b""
        )

    monkeypatch.setattr(runner.subprocess, "run", invoke)
    assert (
        runner.execute_generator("textfsm-generator", {"job_id": "中文任务", "_script": "evil"})[
            "attempts"
        ]
        == 2
    )
    assert not paths[0].exists()


def test_timeout_preserves_structured_diagnostics_and_cleans_up(monkeypatch):
    paths = []

    def invoke(command, **kwargs):
        paths.append(Path(command[3]))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], output=b'{"attempts":3}')

    monkeypatch.setattr(runner.subprocess, "run", invoke)
    with pytest.raises(runner.GeneratorExecutionError) as failure:
        runner.execute_generator("event-rule-validator", {})
    assert failure.value.payload == {
        "attempts": 3,
        "success": False,
        "error": "skill_execution_timeout",
        "failure_code": "skill_execution_timeout",
        "timeout_seconds": 600,
    }
    assert not paths[0].exists()


def test_nonzero_exit_keeps_validation_report(monkeypatch):
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=1,
            stdout=b'{"success":false,"validation":{"errors":["invalid"]}}',
            stderr=b"failed",
        ),
    )
    with pytest.raises(runner.GeneratorExecutionError) as failure:
        runner.execute_generator("event-rule-validator", {})
    assert failure.value.payload["validation"]["errors"] == ["invalid"]


@pytest.mark.parametrize("stdout", [b"", b"[]", b'{"success":true}\ntruncated output'])
def test_missing_final_object_is_an_execution_failure(monkeypatch, stdout):
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=stdout, stderr=b""),
    )
    with pytest.raises(runner.GeneratorExecutionError, match="final JSON"):
        runner.execute_generator("event-rule-validator", {})


def test_real_package_subprocess_requires_no_legacy_registry(monkeypatch):
    from src.core.skills import resolver

    monkeypatch.setattr(
        resolver, "resolve_entry_script", lambda *a: pytest.fail("legacy registry consulted")
    )
    result = runner.execute_generator("event-rule-validator", {"mode": "builtin"})
    assert result["success"] is True
    assert result["validation_report"]["rule_count"] > 0


def test_planner_package_and_legacy_cli_produce_the_same_result(tmp_path):
    import sys

    params = {
        "contract": {
            "entity_type": "device",
            "primary_keys": ["id"],
            "state_semantics": {"id": {"semantic_type": "identity", "monitor": False}},
        },
        "records": [{"id": "test"}],
        "rule_asset_requirements": {
            "requires_observation_contract": True,
            "requires_change_policy": False,
            "requires_current_state_rule": False,
            "requires_event_rule": False,
            "requires_event_rule_assets": False,
            "requires_historical_replay": False,
        },
    }
    expected = runner.execute_generator("rule-target-planner", params)
    assert expected["success"]
    source = tmp_path / "params.json"
    source.write_text(json.dumps(params), encoding="utf-8")
    legacy = runner.ROOT.parents[4] / "src/skills/rule-target-planner/scripts/run.py"
    completed = subprocess.run(
        [sys.executable, str(legacy), "--params", str(source)],
        capture_output=True,
        timeout=30,
        check=True,
    )
    assert json.loads(completed.stdout.decode("utf-8").splitlines()[-1]) == expected


@pytest.mark.parametrize("timeout", [0, 7201, "invalid", True])
def test_invalid_package_timeout_never_starts_a_process(monkeypatch, timeout):
    monkeypatch.setattr(runner.yaml, "safe_load", lambda *a: {"execution_timeout_seconds": timeout})
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: pytest.fail("unexpected process"))
    with pytest.raises(runner.GeneratorExecutionError, match="timeout"):
        runner.execute_generator("event-rule-validator", {})

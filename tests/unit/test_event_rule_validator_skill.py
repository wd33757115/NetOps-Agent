from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPT = PROJECT_ROOT / "src" / "skills" / "event-rule-validator" / "scripts" / "run.py"


def _run_skill(tmp_path: Path, params: dict, *, expect_success_exit: bool = True) -> dict:
    params_path = tmp_path / "params.json"
    params_path.write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(SKILL_SCRIPT), "--params", str(params_path)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if expect_success_exit:
        assert completed.returncode == 0, completed.stderr or completed.stdout
    return json.loads(completed.stdout)


def test_event_rule_validator_skill_validates_builtin_pack(tmp_path: Path):
    result = _run_skill(tmp_path, {"mode": "builtin", "agent_loop": True})

    assert result["success"] is True
    assert result["mode"] == "builtin"
    assert result["validation_report"]["rule_count"] >= 10
    assert result["agent_loop"]["ready_for_approval"] is True
    assert result["agent_loop"]["attempts"][0]["stage"] == "event_rule_validation"
    assert result["agent_loop"]["attempts"][0]["success"] is True


def test_event_rule_validator_skill_validates_candidate_rule(tmp_path: Path):
    result = _run_skill(
        tmp_path,
        {
            "mode": "candidate",
            "agent_loop": True,
            "candidate_rule": {
                "name": "cpu-high-candidate",
                "event_type": "CPUHigh",
                "severity": "warning",
                "condition": {
                    "kind": "threshold",
                    "entity_types": ["cpu"],
                    "fields": ["cpu_5s"],
                    "gte": 90,
                },
            },
        },
    )

    assert result["success"] is True
    assert result["validation_report"]["case_count"] >= 2
    assert result["agent_loop"]["next_action"] == "submit_for_approval"
    attempt = result["agent_loop"]["attempts"][0]
    assert attempt["generator"] == "event-rule-validator"
    assert attempt["input_summary"]["case_count"] >= 2


def test_event_rule_validator_skill_validates_candidate_rule_list(tmp_path: Path):
    result = _run_skill(
        tmp_path,
        {
            "mode": "candidates",
            "agent_loop": True,
            "candidate_rules": [
                {
                    "name": "cpu-high-candidate",
                    "event_type": "CPUHigh",
                    "severity": "warning",
                    "condition": {
                        "kind": "threshold",
                        "entity_types": ["cpu"],
                        "fields": ["cpu_5s"],
                        "gte": 90,
                    },
                },
                {
                    "name": "cpu-recovered-candidate",
                    "event_type": "CPURecovered",
                    "severity": "info",
                    "condition": {
                        "kind": "threshold_recovery",
                        "entity_types": ["cpu"],
                        "fields": ["cpu_5s"],
                        "lt": 85,
                    },
                },
            ],
        },
    )

    assert result["success"] is True
    assert result["validation_report"]["rule_count"] == 2
    assert result["validation_report"]["collision_result"]["collision_count"] == 0
    assert {
        item["event_type"]
        for item in result["validation_report"]["rule_results"]
    } == {"CPUHigh", "CPURecovered"}


def test_event_rule_validator_skill_returns_revision_request_on_failure(tmp_path: Path):
    result = _run_skill(
        tmp_path,
        {
            "mode": "cases",
            "agent_loop": True,
            "cases": [
                {
                    "name": "unexpected-cpu-high",
                    "change": {
                        "entity_type": "cpu",
                        "entity_key": "slot=1",
                        "field": "cpu_5s",
                        "old": 10,
                        "new": 95,
                        "change_type": "modified",
                        "device_id": "private-device-id",
                    },
                    "expected_event_type": None,
                }
            ],
        },
        expect_success_exit=False,
    )

    assert result["success"] is False
    loop = result["agent_loop"]
    assert loop["ready_for_approval"] is False
    assert loop["next_action"] == "revise_candidate_rule"
    assert loop["revision_request"]["target_generator"] == "event-rule-generator"
    assert loop["attempts"][0]["revision_request"]["failed_cases"][0]["change"][
        "entity_key"
    ] == "slot=1"
    assert "device_id" not in loop["attempts"][0]["revision_request"]["failed_cases"][0]["change"]

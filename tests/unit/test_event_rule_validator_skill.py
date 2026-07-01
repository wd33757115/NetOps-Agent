from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPT = PROJECT_ROOT / "src" / "skills" / "event-rule-validator" / "scripts" / "run.py"


def _run_skill(tmp_path: Path, params: dict) -> dict:
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
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return json.loads(completed.stdout)


def test_event_rule_validator_skill_validates_builtin_pack(tmp_path: Path):
    result = _run_skill(tmp_path, {"mode": "builtin", "agent_loop": True})

    assert result["success"] is True
    assert result["mode"] == "builtin"
    assert result["validation_report"]["rule_count"] >= 10
    assert result["agent_loop"]["ready_for_approval"] is True


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

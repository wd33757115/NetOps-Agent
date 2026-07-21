from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src.infrastructure.db.rule_control_projections import build_projection_rows


def test_build_projection_rows_supports_legacy_nested_reports():
    now = datetime.now(timezone.utc)
    job = SimpleNamespace(
        id="job-projection-legacy",
        created_at=now,
        updated_at=now,
        validation_report_json={
            "success": True,
            "synthetic_validation": {
                "success": True,
                "agent_loop": {
                    "ready_for_approval": False,
                    "next_action": "revise_contract",
                    "revision_request": {"required_fixes": ["normalize interface"]},
                    "attempts": [
                        {
                            "attempt": 2,
                            "stage": "contract_validation",
                            "generator": "contract-generator",
                            "model_name": "deterministic-test-model",
                            "success": False,
                        }
                    ],
                },
            },
            "governance": {
                "historical_replay": {
                    "success": True,
                    "sampled_change_count": "invalid",
                    "matched_change_count": 3,
                    "collision_count": 0,
                },
                "approved_by": "pytest-approver",
                "approved_at": now.isoformat(),
                "approval_reason": "replay gates passed",
            },
        },
    )

    rows = build_projection_rows(job)

    assert rows["replay"][0]["sampled_change_count"] == 0
    assert rows["replay"][0]["matched_change_count"] == 3
    assert rows["approval"][0]["action"] == "approved"
    assert rows["approval"][0]["reason"] == "replay gates passed"
    assert rows["attempt"][0]["attempt_number"] == 2
    assert rows["attempt"][0]["model_name"] == "deterministic-test-model"
    assert rows["attempt"][0]["revision_request_json"] == {
        "required_fixes": ["normalize interface"]
    }


def test_build_projection_rows_removes_absent_optional_history():
    now = datetime.now(timezone.utc)
    job = SimpleNamespace(
        id="job-projection-empty",
        created_at=now,
        updated_at=now,
        validation_report_json={"success": None},
    )

    assert build_projection_rows(job) == {
        "replay": [],
        "approval": [],
        "attempt": [],
    }

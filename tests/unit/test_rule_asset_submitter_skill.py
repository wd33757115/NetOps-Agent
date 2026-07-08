from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "skills"
    / "rule-asset-submitter"
    / "scripts"
    / "run.py"
)
SPEC = importlib.util.spec_from_file_location("rule_asset_submitter_run", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
run_submit = MODULE.run_submit


def test_rule_asset_submitter_skill_calls_submission_service(monkeypatch):
    class FakeSubmissionService:
        def submit_candidate(self, job_id, *, actor):
            assert job_id == "job-1"
            assert actor == "workflow"
            return {
                "success": True,
                "submitted_job": {"id": job_id},
                "persister_result": {"asset_count": 1},
            }

    monkeypatch.setattr(MODULE, "RuleAssetSubmissionService", FakeSubmissionService)

    result = run_submit({"job_id": "job-1", "actor": "workflow"})

    assert result["success"] is True
    assert result["persister_result"]["asset_count"] == 1


def test_rule_asset_submitter_skill_requires_job_id():
    with pytest.raises(ValueError, match="job_id is required"):
        run_submit({"actor": "workflow"})

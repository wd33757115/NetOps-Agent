from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "skills"
    / "rule-asset-approver"
    / "scripts"
    / "run.py"
)
SPEC = importlib.util.spec_from_file_location("rule_asset_approver_run", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
run_approve = MODULE.run_approve


def test_rule_asset_approver_skill_calls_approval_service(monkeypatch):
    class FakeApprovalService:
        def approve(self, job_id, *, actor):
            assert job_id == "job-1"
            assert actor == "workflow"
            return {
                "success": True,
                "job": {"id": job_id, "status": "active"},
                "asset_activation": {"version_id": "version-1"},
            }

    monkeypatch.setattr(MODULE, "RuleAssetApprovalService", FakeApprovalService)

    result = run_approve({"job_id": "job-1", "actor": "workflow"})

    assert result["success"] is True
    assert result["job"]["status"] == "active"
    assert result["asset_activation"]["version_id"] == "version-1"


def test_rule_asset_approver_skill_requires_job_id():
    with pytest.raises(ValueError, match="job_id is required"):
        run_approve({"actor": "workflow"})

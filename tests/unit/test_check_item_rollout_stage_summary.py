from __future__ import annotations

from scripts.test.check_item_rollout_stage_lixian_e2e import (
    _classify_acceptance,
)


def _summary(*, campaign_status: str, attempt_status: str) -> dict:
    return {
        "campaign_status": campaign_status,
        "stage_attempt_status": attempt_status,
        "execution_status": "completed",
        "target_count": 2,
        "snapshot_count": 4,
        "projection_count": 4,
        "database_unchanged": True,
        "minio_cleanup_verified": True,
        "metrics": {"runtime_bundle_coverage_rate": 1.0},
    }


def test_completed_campaign_is_rollout_eligible():
    result = _classify_acceptance(
        _summary(campaign_status="completed", attempt_status="passed")
    )

    assert result == {
        "success": True,
        "execution_evidence_complete": True,
        "rollout_eligible": True,
        "acceptance_status": "eligible",
    }


def test_paused_campaign_is_successful_fail_closed_evidence():
    result = _classify_acceptance(
        _summary(campaign_status="paused", attempt_status="failed")
    )

    assert result == {
        "success": True,
        "execution_evidence_complete": True,
        "rollout_eligible": False,
        "acceptance_status": "fail_closed",
    }


def test_missing_cleanup_is_incomplete_evidence():
    summary = _summary(campaign_status="completed", attempt_status="passed")
    summary["minio_cleanup_verified"] = False

    result = _classify_acceptance(summary)

    assert result["success"] is False
    assert result["rollout_eligible"] is False
    assert result["acceptance_status"] == "evidence_incomplete"

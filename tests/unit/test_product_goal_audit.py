from src.core.check_items.product_goal_audit import (
    GOLDEN_CHECK_ITEM_IDS,
    evaluate_product_goal_snapshot,
)


def test_product_goal_audit_fails_closed_without_production_evidence():
    result = evaluate_product_goal_snapshot(
        {
            "active_runtime_bundle_check_items": [],
            "production_candidate_device_count": 0,
            "production_candidate_vendor_count": 0,
            "production_claim_manifest_count": 0,
            "accepted_production_cycle_count": 0,
            "accepted_content_review_count": 0,
            "production_release_count": 0,
            "deprecation_candidate_count": 5,
            "deprecation_exit_ready_count": 0,
            "legacy_call_count": 1,
        }
    )

    assert result["product_goal_achieved"] is False
    assert result["status_counts"] == {
        "passed": 0,
        "blocked": 7,
        "external_evidence_required": 1,
    }
    gates = {item["gate_id"]: item for item in result["gates"]}
    assert gates["five_golden_slices_accepted"]["evidence"][
        "missing_check_items"
    ] == sorted(GOLDEN_CHECK_ITEM_IDS)
    assert gates["fresh_current_cycle_accepted"]["status"] == "blocked"


def test_machine_facts_never_auto_approve_human_or_customer_gates():
    result = evaluate_product_goal_snapshot(
        {
            "active_runtime_bundle_check_items": sorted(GOLDEN_CHECK_ITEM_IDS),
            "production_candidate_device_count": 2000,
            "production_candidate_vendor_count": 3,
            "production_claim_manifest_count": 2,
            "accepted_production_cycle_count": 2,
            "accepted_content_review_count": 2,
            "production_release_count": 2,
            "deprecation_candidate_count": 5,
            "deprecation_exit_ready_count": 5,
            "legacy_call_count": 0,
        }
    )

    assert result["product_goal_achieved"] is False
    assert result["status_counts"] == {
        "passed": 0,
        "blocked": 0,
        "external_evidence_required": 8,
    }
    assert all(
        item["status"] == "external_evidence_required"
        for item in result["gates"]
    )


def test_complete_machine_and_immutable_external_evidence_can_pass():
    external = {
        gate_id: {
            "status": "verified",
            "artifact_uri": f"s3://netops-audit/product-goal/{gate_id}.json",
            "sha256": "a" * 64,
            "verified_by": f"owner:{gate_id}",
            "actor_kind": "customer_authorized",
            "verified_at": "2026-08-08T16:00:00+08:00",
            "test_evidence": False,
        }
        for gate_id in (
            "platform_engineering_verified",
            "five_golden_slices_accepted",
            "customer_production_matrix_ready",
            "fresh_current_cycle_accepted",
            "two_production_cycles_accepted",
            "formal_content_accepted_and_released",
            "multi_vendor_scale_validated",
            "governance_exit_gates_passed",
        )
    }
    result = evaluate_product_goal_snapshot(
        {
            "active_runtime_bundle_check_items": sorted(GOLDEN_CHECK_ITEM_IDS),
            "production_candidate_device_count": 2000,
            "production_candidate_vendor_count": 3,
            "production_claim_manifest_count": 2,
            "accepted_production_cycle_count": 2,
            "accepted_content_review_count": 2,
            "production_release_count": 2,
            "deprecation_candidate_count": 5,
            "deprecation_exit_ready_count": 5,
            "legacy_call_count": 0,
        },
        external_evidence=external,
    )

    assert result["product_goal_achieved"] is True
    assert result["status"] == "achieved"
    assert result["status_counts"] == {
        "passed": 8,
        "blocked": 0,
        "external_evidence_required": 0,
    }

from __future__ import annotations

from copy import deepcopy

from src.core.check_items.report_contract import (
    build_report_authority_context,
    build_report_conclusion_permission,
    report_conclusion_permission_is_valid,
)


def _decision(
    statuses: list[str],
    *,
    risks: list[dict[str, object]] | None = None,
    include_applicability: bool = True,
    include_scope: bool = True,
    claim_boundary: str = "selected_network_scope",
    assessment_mode: str = "health",
    evidence_uses_external_identity: bool = False,
) -> dict[str, object]:
    target_digest = "1" * 64
    inventory_digest = "2" * 64
    runtime_digest = "3" * 64
    authority = build_report_authority_context(
        report_purpose="production_current",
        patrol_mode="production",
        launch_mode="workflow",
        workflow_name="patrol-change-event",
        target_type="devices",
        readiness_ready=True,
        target_manifest_digest=target_digest,
        inventory_snapshot_digest=inventory_digest,
        runtime_manifest_digest=runtime_digest,
    )
    target_pairs = [
        {
            "device_id": f"device-{index}",
            "check_item_version_id": "cpu-v1",
        }
        for index in range(len(statuses))
    ]
    executions = [
        {
            "id": f"execution-{index}",
            **pair,
            "execution_status": (
                "not_applicable"
                if statuses[index] == "not_applicable"
                else "completed"
            ),
            "reason_codes_json": (
                ["applicability_rule_no_match"]
                if statuses[index] == "not_applicable"
                else []
            ),
            "evidence_qualification_id": f"qualification-{index}",
            "coverage_decision_id": f"coverage-{index}",
            "evidence_refs_json": [f"snapshot-{index}"],
        }
        for index, pair in enumerate(target_pairs)
    ]
    assessments = [
        {
            "id": f"assessment-{index}",
            "execution_id": f"execution-{index}",
            **pair,
            "assessment_status": status,
            "blocking_reason_codes_json": (
                ["parser_missing"]
                if status == "unable_to_determine"
                else []
            ),
            "evidence_qualification_id": (
                f"qualification-{index}"
            ),
            "coverage_decision_id": f"coverage-{index}",
        }
        for index, (pair, status) in enumerate(
            zip(target_pairs, statuses, strict=True)
        )
    ]
    qualifications = [
        {
            "id": f"qualification-{index}",
            "check_execution_id": f"execution-{index}",
            **pair,
            "digest": str(index).zfill(64),
            "snapshot_ids_json": [f"snapshot-{index}"],
        }
        for index, pair in enumerate(target_pairs)
    ]
    coverage_decisions = [
        {
            "id": f"coverage-{index}",
            "check_execution_id": f"execution-{index}",
            **pair,
            "digest": str(index + 1).zfill(64),
            "coverage_status": (
                "not_applicable"
                if statuses[index] == "not_applicable"
                else "complete"
            ),
            "reason_codes_json": (
                ["applicability_rule_no_match"]
                if statuses[index] == "not_applicable"
                else []
            ),
            "denominator_ref_json": (
                {
                    "applicability_rule_digest": "a" * 64,
                    "inventory_snapshot_digest": "2" * 64,
                }
                if statuses[index] == "not_applicable"
                else {"coverage_policy_digest": "b" * 64}
            ),
        }
        for index, pair in enumerate(target_pairs)
    ]
    return build_report_conclusion_permission(
        report_purpose="production_current",
        report_authority_context=authority,
        target_manifest_digest=target_digest,
        inventory_snapshot_digest=inventory_digest,
        runtime_manifest_digest=runtime_digest,
        readiness={
            "ready_for_final": True,
            "reason_codes": [],
        },
        target_pairs=target_pairs,
        executions=executions,
        assessments=assessments,
        evidence_qualifications=qualifications,
        coverage_decisions=coverage_decisions,
        evidence_index=[
            {
                "snapshot_id": f"snapshot-{index}",
                "device_id": (
                    f"external-{index}"
                    if evidence_uses_external_identity
                    else pair["device_id"]
                ),
                "inventory_device_id": pair["device_id"],
                "raw_text_hash": str(index + 2).zfill(64),
            }
            for index, pair in enumerate(target_pairs)
        ],
        check_items={
            "cpu-v1": {
                "check_item_id": "system.cpu_usage",
                "assessment_mode": assessment_mode,
                **(
                    {
                        "applicability": {
                            "version": 1,
                            "conditions": [
                                {
                                    "field": "role",
                                    "equals": "router",
                                }
                            ],
                        }
                    }
                    if include_applicability
                    else {}
                ),
            }
        },
        risks=risks or [],
        scope_contract=(
            {
                "scope_contract_id": "production-scope",
                "claim_boundary": claim_boundary,
            }
            if include_scope
            else {}
        ),
        intended_target_pairs=(target_pairs if include_scope else []),
        scope_decisions=(
            [
                {
                    **pair,
                    "decision": "included",
                    "reason_codes": ["required_check_item_selected"],
                }
                for pair in target_pairs
            ]
            if include_scope
            else []
        ),
    )


def test_partial_unknown_only_allows_evaluated_scope_statement() -> None:
    decision = _decision(
        ["healthy"] * 95 + ["unable_to_determine"] * 5
    )

    assert decision["terminal_truth_reconciled"] is True
    assert decision["current_conclusion_ready"] is True
    assert (
        decision["evaluated_scope_no_abnormality_allowed"]
        is True
    )
    assert decision["overall_healthy_allowed"] is False
    assert decision["counts"]["healthy"] == 95
    assert decision["counts"]["unable_to_determine"] == 5
    assert (
        "health_target_pair_unable_to_determine"
        in decision["blocker_reason_codes"]
    )
    assert report_conclusion_permission_is_valid(decision)


def test_inventory_identity_reconciles_external_snapshot_identity() -> None:
    decision = _decision(
        ["healthy"],
        evidence_uses_external_identity=True,
    )

    assert decision["terminal_truth_reconciled"] is True
    assert decision["overall_healthy_allowed"] is True
    assert report_conclusion_permission_is_valid(decision)


def test_non_health_unknown_remains_in_conclusion_denominator() -> None:
    decision = _decision(
        ["informational", "unable_to_determine"],
        assessment_mode="change",
    )

    assert decision["terminal_truth_reconciled"] is True
    assert decision["counts"]["health_target_pair_count"] == 0
    assert decision["counts"]["informational"] == 1
    assert decision["counts"]["unable_to_determine"] == 1
    assert decision["counts"]["health_unable_to_determine"] == 0
    assert "target_pair_unable_to_determine" in decision[
        "blocker_reason_codes"
    ]
    assert report_conclusion_permission_is_valid(decision)


def test_all_not_applicable_never_allows_health_statement() -> None:
    decision = _decision(["not_applicable", "not_applicable"])

    assert decision["current_conclusion_ready"] is True
    assert (
        decision["evaluated_scope_no_abnormality_allowed"]
        is False
    )
    assert decision["overall_healthy_allowed"] is False
    assert (
        "all_health_targets_not_applicable"
        in decision["blocker_reason_codes"]
    )


def test_not_applicable_without_frozen_applicability_fails_closed() -> None:
    decision = _decision(
        ["not_applicable"],
        include_applicability=False,
    )

    assert decision["terminal_truth_reconciled"] is False
    assert decision["current_conclusion_ready"] is False
    assert decision["overall_healthy_allowed"] is False
    assert (
        "terminal_truth_not_reconciled"
        in decision["blocker_reason_codes"]
    )


def test_unresolved_in_scope_risk_blocks_no_abnormality() -> None:
    decision = _decision(
        ["healthy"],
        risks=[
            {
                "id": "risk-1",
                "device_id": "device-0",
                "check_item_id": "system.cpu_usage",
                "status": "open",
                "verification_status": "not_reverified",
            }
        ],
    )

    assert decision["current_conclusion_ready"] is True
    assert (
        decision["evaluated_scope_no_abnormality_allowed"]
        is False
    )
    assert decision["overall_healthy_allowed"] is False
    assert (
        decision["counts"][
            "unresolved_in_scope_risk_case_count"
        ]
        == 1
    )
    assert (
        "unresolved_in_scope_risk_case"
        in decision["blocker_reason_codes"]
    )


def test_all_healthy_with_no_open_risk_allows_overall_health() -> None:
    decision = _decision(["healthy", "healthy"])

    assert decision["current_conclusion_ready"] is True
    assert (
        decision["evaluated_scope_no_abnormality_allowed"]
        is True
    )
    assert decision["overall_healthy_allowed"] is True


def test_missing_scope_contract_blocks_only_overall_health_claim() -> None:
    decision = _decision(["healthy"], include_scope=False)

    assert decision["current_conclusion_ready"] is True
    assert decision["evaluated_scope_no_abnormality_allowed"] is True
    assert decision["overall_healthy_allowed"] is False
    assert "scope_contract_missing" in decision["blocker_reason_codes"]


def test_ad_hoc_claim_boundary_blocks_overall_health_claim() -> None:
    decision = _decision(
        ["healthy"],
        claim_boundary="selected_scope_only",
    )

    assert decision["scope_reconciled"] is True
    assert decision["overall_claim_scope_allowed"] is False
    assert decision["overall_healthy_allowed"] is False
    assert "claim_boundary_limited" in decision["blocker_reason_codes"]


def test_missing_assessment_fails_terminal_truth_reconciliation() -> None:
    decision = _decision(["healthy"])
    broken = build_report_conclusion_permission(
        report_purpose="production_current",
        report_authority_context=build_report_authority_context(
            report_purpose="production_current",
            patrol_mode="production",
            launch_mode="workflow",
            workflow_name="patrol-change-event",
            target_type="devices",
            readiness_ready=True,
            target_manifest_digest="1" * 64,
            inventory_snapshot_digest="2" * 64,
            runtime_manifest_digest="3" * 64,
        ),
        target_manifest_digest="1" * 64,
        inventory_snapshot_digest="2" * 64,
        runtime_manifest_digest="3" * 64,
        readiness={"ready_for_final": True, "reason_codes": []},
        target_pairs=[
            {
                "device_id": "device-0",
                "check_item_version_id": "cpu-v1",
            }
        ],
        executions=[
            {
                "id": "execution-0",
                "device_id": "device-0",
                "check_item_version_id": "cpu-v1",
                "execution_status": "completed",
                "evidence_qualification_id": "qualification-0",
                "evidence_refs_json": ["snapshot-0"],
            }
        ],
        assessments=[],
        evidence_qualifications=[
            {
                "id": "qualification-0",
                "check_execution_id": "execution-0",
                "device_id": "device-0",
                "check_item_version_id": "cpu-v1",
                "digest": "4" * 64,
                "snapshot_ids_json": ["snapshot-0"],
            }
        ],
        coverage_decisions=[],
        evidence_index=[
            {
                "snapshot_id": "snapshot-0",
                "device_id": "device-0",
                "raw_text_hash": "5" * 64,
            }
        ],
        check_items={
            "cpu-v1": {
                "check_item_id": "system.cpu_usage",
                "assessment_mode": "health",
            }
        },
        risks=[],
    )

    assert decision["terminal_truth_reconciled"] is True
    assert broken["terminal_truth_reconciled"] is False
    assert broken["current_conclusion_ready"] is False
    assert (
        "terminal_truth_not_reconciled"
        in broken["blocker_reason_codes"]
    )


def test_decision_digest_rejects_tampering() -> None:
    decision = _decision(["healthy"])
    tampered = deepcopy(decision)
    tampered["overall_healthy_allowed"] = False

    assert report_conclusion_permission_is_valid(decision)
    assert not report_conclusion_permission_is_valid(tampered)


def test_malformed_counts_fail_closed_without_raising() -> None:
    decision = _decision(["healthy"])
    malformed = deepcopy(decision)
    malformed["counts"]["healthy"] = "not-a-number"

    assert not report_conclusion_permission_is_valid(malformed)


def test_missing_referenced_qualification_fails_closed() -> None:
    target_digest = "1" * 64
    inventory_digest = "2" * 64
    runtime_digest = "3" * 64
    decision = build_report_conclusion_permission(
        report_purpose="production_current",
        report_authority_context=build_report_authority_context(
            report_purpose="production_current",
            patrol_mode="production",
            launch_mode="workflow",
            workflow_name="patrol-change-event",
            target_type="devices",
            readiness_ready=True,
            target_manifest_digest=target_digest,
            inventory_snapshot_digest=inventory_digest,
            runtime_manifest_digest=runtime_digest,
        ),
        target_manifest_digest=target_digest,
        inventory_snapshot_digest=inventory_digest,
        runtime_manifest_digest=runtime_digest,
        readiness={"ready_for_final": True, "reason_codes": []},
        target_pairs=[
            {
                "device_id": "device-0",
                "check_item_version_id": "cpu-v1",
            }
        ],
        executions=[
            {
                "id": "execution-0",
                "device_id": "device-0",
                "check_item_version_id": "cpu-v1",
                "execution_status": "completed",
                "reason_codes_json": [],
                "evidence_qualification_id": "qualification-missing",
                "coverage_decision_id": "coverage-0",
                "evidence_refs_json": [
                    "snapshot-missing-qualification"
                ],
            }
        ],
        assessments=[
            {
                "id": "assessment-0",
                "execution_id": "execution-0",
                "device_id": "device-0",
                "check_item_version_id": "cpu-v1",
                "assessment_status": "healthy",
                "blocking_reason_codes_json": [],
                "evidence_qualification_id": "qualification-missing",
                "coverage_decision_id": "coverage-0",
            }
        ],
        evidence_qualifications=[],
        coverage_decisions=[
            {
                "id": "coverage-0",
                "check_execution_id": "execution-0",
                "device_id": "device-0",
                "check_item_version_id": "cpu-v1",
                "digest": "4" * 64,
            }
        ],
        evidence_index=[
            {
                "snapshot_id": (
                    "snapshot-missing-qualification"
                ),
                "device_id": "device-0",
                "raw_text_hash": "5" * 64,
            }
        ],
        check_items={
            "cpu-v1": {
                "check_item_id": "system.cpu_usage",
                "assessment_mode": "health",
            }
        },
        risks=[],
    )

    assert decision["terminal_truth_reconciled"] is False
    assert decision["current_conclusion_ready"] is False
    assert decision["overall_healthy_allowed"] is False


def test_cross_execution_coverage_reference_fails_closed() -> None:
    decision = _decision(["healthy"])
    target_digest = "1" * 64
    inventory_digest = "2" * 64
    runtime_digest = "3" * 64
    broken = build_report_conclusion_permission(
        report_purpose="production_current",
        report_authority_context=build_report_authority_context(
            report_purpose="production_current",
            patrol_mode="production",
            launch_mode="workflow",
            workflow_name="patrol-change-event",
            target_type="devices",
            readiness_ready=True,
            target_manifest_digest=target_digest,
            inventory_snapshot_digest=inventory_digest,
            runtime_manifest_digest=runtime_digest,
        ),
        target_manifest_digest=target_digest,
        inventory_snapshot_digest=inventory_digest,
        runtime_manifest_digest=runtime_digest,
        readiness={"ready_for_final": True, "reason_codes": []},
        target_pairs=[
            {
                "device_id": "device-0",
                "check_item_version_id": "cpu-v1",
            }
        ],
        executions=[
            {
                "id": "execution-0",
                "device_id": "device-0",
                "check_item_version_id": "cpu-v1",
                "execution_status": "completed",
                "reason_codes_json": [],
                "evidence_qualification_id": "qualification-0",
                "coverage_decision_id": "coverage-0",
                "evidence_refs_json": ["snapshot-0"],
            }
        ],
        assessments=[
            {
                "id": "assessment-0",
                "execution_id": "execution-0",
                "device_id": "device-0",
                "check_item_version_id": "cpu-v1",
                "assessment_status": "healthy",
                "blocking_reason_codes_json": [],
                "evidence_qualification_id": "qualification-0",
                "coverage_decision_id": "coverage-0",
            }
        ],
        evidence_qualifications=[
            {
                "id": "qualification-0",
                "check_execution_id": "execution-0",
                "device_id": "device-0",
                "check_item_version_id": "cpu-v1",
                "digest": "4" * 64,
                "snapshot_ids_json": ["snapshot-0"],
            }
        ],
        coverage_decisions=[
            {
                "id": "coverage-0",
                "check_execution_id": "execution-other",
                "device_id": "device-0",
                "check_item_version_id": "cpu-v1",
                "digest": "5" * 64,
            }
        ],
        evidence_index=[
            {
                "snapshot_id": "snapshot-0",
                "device_id": "device-0",
                "raw_text_hash": "6" * 64,
            }
        ],
        check_items={
            "cpu-v1": {
                "check_item_id": "system.cpu_usage",
                "assessment_mode": "health",
            }
        },
        risks=[],
    )

    assert decision["overall_healthy_allowed"] is True
    assert broken["terminal_truth_reconciled"] is False
    assert broken["overall_healthy_allowed"] is False

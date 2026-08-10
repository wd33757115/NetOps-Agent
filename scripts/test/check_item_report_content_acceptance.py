"""Render a controlled, non-production report-content acceptance fixture.

The fixture intentionally contains risks, dynamic thresholds, remediation,
recovery-pending state, and an unevaluable check. It validates report semantics
and layout inputs only; it never represents the current production network.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.check_items.report_contract import (
    SHADOW_VALIDATION,
    build_report_conclusion_permission_from_source_facts,
    build_report_authority_context,
    raw_evidence_set_digest,
)
from src.core.check_items.report_claims import (
    build_report_claim_ledger,
    validate_report_claim_ledger,
)
from src.core.check_items.report_projection import (
    build_report_projection_snapshot,
)
from src.core.check_items.reporting import (
    build_check_item_report_view,
    render_check_item_artifacts,
)

DEFAULT_OUTPUT_DIR = (
    ROOT / ".tmp" / "check-item-report-content-acceptance"
)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _check_item(
    check_item_id: str,
    *,
    version_id: str,
    observation_type: str,
    report_projection: str,
) -> dict[str, Any]:
    return {
        "check_item_id": check_item_id,
        "version": 1,
        "name": check_item_id,
        "category": check_item_id.split(".", 1)[0],
        "description": "controlled report acceptance fixture",
        "result_mode": "structured",
        "assessment_mode": "health",
        "canonical_contract": {
            "observations": [
                {"observation_type": observation_type},
            ]
        },
        "report_projection": report_projection,
        "digest": _digest([version_id, observation_type]),
    }


def _execution(
    *,
    execution_id: str,
    device_id: str,
    check_item_version_id: str,
    assessment_status: str,
    comparison_status: str,
    finding_count: int,
    event_count: int,
    blocking_reason_codes: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    blocked = list(blocking_reason_codes or [])
    execution = {
        "id": execution_id,
        "device_id": device_id,
        "check_item_version_id": check_item_version_id,
        "runtime_bundle_digest": _digest(
            ["runtime-bundle", check_item_version_id]
        ),
        "comparison_context_id": f"comparison-{execution_id}",
        "execution_status": "completed",
        "reason_codes_json": blocked,
        "observation_count": 0 if blocked else 1,
        "observed_entity_count": 0 if blocked else 1,
        "change_count": 0 if blocked else 1,
        "event_count": event_count,
        "current_finding_count": finding_count,
        "evidence_qualification_id": f"qualification-{execution_id}",
        "coverage_decision_id": f"coverage-{execution_id}",
        "source_identity_status": "confirmed",
        "output_quality_status": (
            "complete" if not blocked else "device_error"
        ),
        "freshness_status": "fresh",
        "empty_result_classification": (
            "not_empty" if not blocked else "not_evaluated"
        ),
        "coverage_status": (
            "complete" if not blocked else "not_evaluable"
        ),
        "coverage_basis": "fixed",
        "risk_verification_status": (
            "confirmed_current"
            if assessment_status == "at_risk"
            else "not_reverified"
            if blocked
            else "no_current_risk"
        ),
        "evidence_refs_json": [f"snapshot-{execution_id}"],
    }
    assessment = {
        "id": f"assessment-{execution_id}",
        "execution_id": execution_id,
        "device_id": device_id,
        "check_item_version_id": check_item_version_id,
        "assessment_status": assessment_status,
        "completeness_status": (
            "insufficient"
            if assessment_status == "unable_to_determine"
            else "complete"
        ),
        "comparison_status": comparison_status,
        "expected_entity_count": 1,
        "observed_entity_count": 0 if blocked else 1,
        "evaluated_entity_count": 0 if blocked else 1,
        "threshold_unknown_count": 0,
        "current_finding_ids_json": [],
        "risk_case_ids_json": [],
        "evidence_qualification_id": f"qualification-{execution_id}",
        "coverage_decision_id": f"coverage-{execution_id}",
        "source_identity_status": "confirmed",
        "output_quality_status": (
            "complete" if not blocked else "device_error"
        ),
        "freshness_status": "fresh",
        "empty_result_classification": (
            "not_empty" if not blocked else "not_evaluated"
        ),
        "coverage_status": (
            "complete" if not blocked else "not_evaluable"
        ),
        "coverage_basis": "fixed",
        "risk_verification_status": execution[
            "risk_verification_status"
        ],
        "blocking_reason_codes_json": blocked,
        "evidence_digest": _digest(
            [execution_id, assessment_status, blocked]
        ),
    }
    return execution, assessment


def build_controlled_manifest() -> dict[str, Any]:
    generated_at = datetime(
        2026,
        7,
        30,
        8,
        0,
        tzinfo=timezone.utc,
    )
    batch_id = "controlled-shadow-report-acceptance"
    target_digest = "1" * 64
    inventory_digest = "2" * 64
    runtime_digest = "3" * 64
    check_items = {
        "check-item:system.cpu_usage:v1": _check_item(
            "system.cpu_usage",
            version_id="check-item:system.cpu_usage:v1",
            observation_type="device.cpu.utilization",
            report_projection="cpu",
        ),
        "check-item:interface.optical_power:v1": _check_item(
            "interface.optical_power",
            version_id="check-item:interface.optical_power:v1",
            observation_type="interface.optical.rx_power",
            report_projection="optical_power",
        ),
        "check-item:interface.oper_status:v1": _check_item(
            "interface.oper_status",
            version_id="check-item:interface.oper_status:v1",
            observation_type="interface.oper_status",
            report_projection="interface_status",
        ),
    }
    report_projection_snapshot = build_report_projection_snapshot(
        check_items
    )
    cpu_execution, cpu_assessment = _execution(
        execution_id="execution-cpu",
        device_id="device-core",
        check_item_version_id="check-item:system.cpu_usage:v1",
        assessment_status="at_risk",
        comparison_status="comparable",
        finding_count=1,
        event_count=1,
    )
    optical_execution, optical_assessment = _execution(
        execution_id="execution-optical",
        device_id="device-access",
        check_item_version_id="check-item:interface.optical_power:v1",
        assessment_status="at_risk",
        comparison_status="comparable",
        finding_count=1,
        event_count=1,
    )
    interface_execution, interface_assessment = _execution(
        execution_id="execution-interface",
        device_id="device-edge",
        check_item_version_id="check-item:interface.oper_status:v1",
        assessment_status="unable_to_determine",
        comparison_status="baseline_stale",
        finding_count=0,
        event_count=0,
        blocking_reason_codes=["parse_failed"],
    )
    cpu_assessment["current_finding_ids_json"] = ["finding-cpu"]
    cpu_assessment["risk_case_ids_json"] = [
        "risk-cpu",
        "risk-cpu-history",
    ]
    optical_assessment["current_finding_ids_json"] = [
        "finding-optical"
    ]
    optical_assessment["risk_case_ids_json"] = [
        "risk-optical",
        "risk-optical-recovery",
    ]
    target_facts = [
        {
            "device_id": "device-core",
            "device_name": "核心交换机-01",
            "management_ip": "192.0.2.10",
            "selection_status": "launched",
            "preflight_status": "ready",
            "dispatch_status": "completed",
        },
        {
            "device_id": "device-access",
            "device_name": "接入交换机-01",
            "management_ip": "192.0.2.20",
            "selection_status": "launched",
            "preflight_status": "ready",
            "dispatch_status": "completed",
        },
        {
            "device_id": "device-edge",
            "device_name": "边界交换机-01",
            "management_ip": "192.0.2.30",
            "selection_status": "launched",
            "preflight_status": "ready",
            "dispatch_status": "completed",
        },
    ]
    findings = [
        {
            "id": "finding-cpu",
            "execution_id": "execution-cpu",
            "device_id": "device-core",
            "event_type": "CpuUtilizationHigh",
            "severity": "major",
            "entity_type": "cpu_scope",
            "entity_key": '["device","system"]',
            "actual_value": 94,
            "actual_value_number": 94,
            "unit": "percent",
            "reference_value": 90,
            "reference_value_number": 90,
            "reference_source": "event_rule_static",
            "event_rule_version_id": "current-state-cpu-v3",
            "rule_digest": "4" * 64,
            "observed_at": generated_at.isoformat(),
        },
        {
            "id": "finding-optical",
            "execution_id": "execution-optical",
            "device_id": "device-access",
            "event_type": "OpticalRxPowerLow",
            "severity": "warning",
            "entity_type": "interface_optical",
            "entity_key": '["Ten-GigabitEthernet1/0/1"]',
            "actual_value": -18.7,
            "actual_value_number": -18.7,
            "unit": "dBm",
            "reference_value": -17.0,
            "reference_value_number": -17.0,
            "reference_source": "device_output",
            "event_rule_version_id": "current-state-optical-v2",
            "rule_digest": "5" * 64,
            "observed_at": generated_at.isoformat(),
        },
    ]
    base_risk = {
        "severity": "warning",
        "status": "open",
        "first_seen_at": "2026-07-01T00:00:00+00:00",
        "last_seen_at": generated_at.isoformat(),
        "last_verified_at": generated_at.isoformat(),
        "remediation_status": "planned",
        "due_at": "2026-08-05T18:00:00+08:00",
        "source_event_ids_json": [],
        "resolution_evidence_refs_json": [],
    }
    risks = [
        {
            **base_risk,
            "id": "risk-cpu",
            "check_item_id": "system.cpu_usage",
            "device_id": "device-core",
            "entity_type": "cpu_scope",
            "entity_key": '["device","system"]',
            "risk_type": "CpuUtilizationHigh",
            "severity": "major",
            "verification_status": "confirmed_current",
            "last_verification_execution_id": "execution-cpu",
            "owner": "网络运维组",
            "source_finding_ids_json": ["finding-cpu"],
        },
        {
            **base_risk,
            "id": "risk-optical",
            "check_item_id": "interface.optical_power",
            "device_id": "device-access",
            "entity_type": "interface_optical",
            "entity_key": '["Ten-GigabitEthernet1/0/1"]',
            "risk_type": "OpticalRxPowerLow",
            "verification_status": "confirmed_current",
            "last_verification_execution_id": "execution-optical",
            "owner": "传输维护组",
            "source_finding_ids_json": ["finding-optical"],
        },
        {
            **base_risk,
            "id": "risk-cpu-history",
            "check_item_id": "system.cpu_usage",
            "device_id": "device-core",
            "entity_type": "cpu_scope",
            "entity_key": '["device","slot-2"]',
            "risk_type": "CpuUtilizationHigh",
            "verification_status": "not_reverified",
            "last_verification_execution_id": "execution-old",
            "owner": "网络运维组",
            "source_finding_ids_json": ["finding-old"],
        },
        {
            **base_risk,
            "id": "risk-optical-recovery",
            "check_item_id": "interface.optical_power",
            "device_id": "device-access",
            "entity_type": "interface_optical",
            "entity_key": '["Ten-GigabitEthernet1/0/2"]',
            "risk_type": "OpticalRxPowerLow",
            "verification_status": "recovery_pending",
            "last_verification_execution_id": "execution-optical",
            "owner": "传输维护组",
            "source_finding_ids_json": ["finding-old-optical"],
        },
    ]
    changes = [
        {
            "id": "change-cpu",
            "execution_id": "execution-cpu",
            "device_id": "device-core",
            "check_item_id": "system.cpu_usage",
            "observation_type": "device.cpu.utilization",
            "change_type": "modified",
            "entity_type": "cpu_scope",
            "entity_key": '["device","system"]',
            "field": "usage_5m",
            "old": 65,
            "new": 94,
            "decision_reason": "threshold_and_deadband",
            "observed_at": generated_at.isoformat(),
        },
        {
            "id": "change-optical",
            "execution_id": "execution-optical",
            "device_id": "device-access",
            "check_item_id": "interface.optical_power",
            "observation_type": "interface.optical.rx_power",
            "change_type": "modified",
            "entity_type": "interface_optical",
            "entity_key": '["Ten-GigabitEthernet1/0/1"]',
            "field": "rx_power_dbm",
            "old": -14.0,
            "new": -18.7,
            "decision_reason": "threshold_low_and_deadband",
            "observed_at": generated_at.isoformat(),
        },
    ]
    events = [
        {
            "id": "event-cpu",
            "execution_id": "execution-cpu",
            "device_id": "device-core",
            "check_item_id": "system.cpu_usage",
            "event_type": "CpuUtilizationHigh",
            "severity": "major",
            "status": "open",
            "entity_type": "cpu_scope",
            "entity_key": '["device","system"]',
            "field": "usage_5m",
            "previous_value": 65,
            "current_value": 94,
            "unit": "percent",
            "condition_kind": "threshold",
            "condition": {
                "kind": "threshold",
                "fields": ["usage_5m"],
                "gte": 90,
            },
            "reference_field": "gte",
            "reference_value": 90,
            "reference_source": "event_rule_static",
            "event_rule_id": "cpu-high",
            "event_rule_version": 3,
            "occurred_at": generated_at.isoformat(),
        },
        {
            "id": "event-optical",
            "execution_id": "execution-optical",
            "device_id": "device-access",
            "check_item_id": "interface.optical_power",
            "event_type": "OpticalRxPowerLow",
            "severity": "warning",
            "status": "open",
            "entity_type": "interface_optical",
            "entity_key": '["Ten-GigabitEthernet1/0/1"]',
            "field": "rx_power_dbm",
            "previous_value": -14.0,
            "current_value": -18.7,
            "unit": "dBm",
            "condition_kind": "threshold_field_lte",
            "condition": {
                "kind": "threshold_field_lte",
                "fields": ["rx_power_dbm"],
                "reference_field": "rx_low_warning_dbm",
            },
            "reference_field": "rx_low_warning_dbm",
            "reference_value": -17.0,
            "reference_source": "device_output",
            "event_rule_id": "optical-rx-low",
            "event_rule_version": 2,
            "occurred_at": generated_at.isoformat(),
        },
    ]
    observations = [
        {
            "id": "observation-cpu",
            "execution_ids": ["execution-cpu"],
            "snapshot_id": "snapshot-execution-cpu",
            "device_id": "device-core",
            "entity_type": "cpu_scope",
            "entity_key": '["device","system"]',
            "field": "usage_5m",
            "observation_type": "device.cpu.utilization",
            "semantic_type": "gauge",
            "unit": "percent",
            "dimensions": {"window": "5m"},
            "value": 94,
            "value_number": 94,
            "value_type": "number",
            "observed_at": generated_at.isoformat(),
            "parser_template_id": "parser-cpu-v1",
            "parser_version": "1",
            "contract_version": 1,
        },
        {
            "id": "observation-optical",
            "execution_ids": ["execution-optical"],
            "snapshot_id": "snapshot-execution-optical",
            "device_id": "device-access",
            "entity_type": "interface_optical",
            "entity_key": '["Ten-GigabitEthernet1/0/1"]',
            "field": "rx_power_dbm",
            "observation_type": "interface.optical.rx_power",
            "semantic_type": "gauge",
            "unit": "dBm",
            "dimensions": {},
            "value": -18.7,
            "value_number": -18.7,
            "value_type": "number",
            "observed_at": generated_at.isoformat(),
            "parser_template_id": "parser-optical-v1",
            "parser_version": "1",
            "contract_version": 1,
        },
    ]
    evidence_index = [
        {
            "snapshot_id": f"snapshot-{execution_id}",
            "run_id": "controlled-shadow-run",
            "device_id": device_id,
            "device_name": device_name,
            "management_ip": management_ip,
            "vendor": vendor,
            "model": model,
            "observed_at": generated_at.isoformat(),
            "canonical_command": command,
            "raw_text_hash": raw_hash,
            "parse_status": parse_status,
            "output_status": output_status,
            "parser_template_id": parser_id,
            "parser_version": "1",
            "contract_version": 1,
            "collection_action_id": f"action-{execution_id}",
        }
        for (
            execution_id,
            device_id,
            device_name,
            management_ip,
            vendor,
            model,
            command,
            raw_hash,
            parse_status,
            output_status,
            parser_id,
        ) in (
            (
                "execution-cpu",
                "device-core",
                "核心交换机-01",
                "192.0.2.10",
                "Cisco",
                "N9K",
                "show system resources",
                "a" * 64,
                "parsed",
                "success",
                "parser-cpu-v1",
            ),
            (
                "execution-optical",
                "device-access",
                "接入交换机-01",
                "192.0.2.20",
                "H3C",
                "S5130S",
                "display transceiver diagnosis interface",
                "b" * 64,
                "parsed",
                "success",
                "parser-optical-v1",
            ),
            (
                "execution-interface",
                "device-edge",
                "边界交换机-01",
                "192.0.2.30",
                "Huawei",
                "S5735",
                "display interface brief",
                "c" * 64,
                "parse_failed",
                "device_error",
                None,
            ),
        )
    ]
    executions = [
        cpu_execution,
        optical_execution,
        interface_execution,
    ]
    assessments = [
        cpu_assessment,
        optical_assessment,
        interface_assessment,
    ]
    target_pairs = [
        {
            "device_id": item["device_id"],
            "check_item_version_id": item[
                "check_item_version_id"
            ],
        }
        for item in executions
    ]
    scope_contract = {
        "scope_contract_id": "controlled-shadow-scope",
        "claim_boundary": "selected_network_scope",
    }
    scope_contract_digest = _digest(scope_contract)
    intended_target_matrix_digest = _digest(target_pairs)
    evidence_qualifications = [
        {
            "id": item["evidence_qualification_id"],
            "digest": _digest(
                ["qualification", item["id"]]
            ),
            "check_execution_id": item["id"],
            "device_id": item["device_id"],
            "check_item_version_id": item[
                "check_item_version_id"
            ],
            "snapshot_ids_json": list(
                item["evidence_refs_json"]
            ),
        }
        for item in executions
    ]
    assessment_by_execution = {
        item["execution_id"]: item for item in assessments
    }
    coverage_decisions = [
        {
            "id": item["coverage_decision_id"],
            "digest": _digest(["coverage", item["id"]]),
            "check_execution_id": item["id"],
            "device_id": item["device_id"],
            "check_item_version_id": item[
                "check_item_version_id"
            ],
            "coverage_status": item["coverage_status"],
            "coverage_basis": item["coverage_basis"],
            "expected_entity_count": 1,
            "observed_entity_count": item[
                "observed_entity_count"
            ],
            "evaluated_entity_count": assessment_by_execution[
                item["id"]
            ]["evaluated_entity_count"],
            "denominator_ref_json": {
                "model": "fixed",
                "expected_count": 1,
            },
            "missing_entity_sample_json": [],
            "unexpected_entity_sample_json": [],
            "reason_codes_json": list(
                item["reason_codes_json"]
            ),
        }
        for item in executions
    ]
    facts: dict[str, Any] = {
        "schema_version": "patrol-report-source/v5",
        "patrol_batch_id": batch_id,
        "report_purpose": SHADOW_VALIDATION,
        "report_authority_context": build_report_authority_context(
            report_purpose=SHADOW_VALIDATION,
            patrol_mode="shadow",
            launch_mode="controlled_fixture",
            workflow_name="check-item-report-content-acceptance",
            target_type="approved_test_fixture",
            readiness_ready=True,
            target_manifest_digest=target_digest,
            inventory_snapshot_digest=inventory_digest,
            runtime_manifest_digest=runtime_digest,
            scope_contract_digest=scope_contract_digest,
            intended_target_matrix_digest=(
                intended_target_matrix_digest
            ),
        ),
        "readiness": {
            "ready_for_final": True,
            "reason_codes": [],
            "target_count": 3,
            "target_record_count": 3,
            "execution_count": 3,
            "expected_execution_count": 3,
            "assessment_count": 3,
        },
        "target_manifest_digest": target_digest,
        "inventory_snapshot_digest": inventory_digest,
        "runtime_manifest_digest": runtime_digest,
        "scope_contract": scope_contract,
        "scope_contract_digest": scope_contract_digest,
        "intended_target_matrix_digest": (
            intended_target_matrix_digest
        ),
        "target_pairs": target_pairs,
        "intended_target_pairs": target_pairs,
        "scope_decisions": [
            {
                **item,
                "decision": "included",
                "reason_codes": [
                    "controlled_acceptance_target"
                ],
            }
            for item in target_pairs
        ],
        "unmatched_scope_device_ids": [],
        "target_facts": target_facts,
        "comparison_contexts": [
            {
                "id": f"comparison-{execution_id}",
                "digest": _digest(["comparison", execution_id]),
                "comparison_status": comparison_status,
                "baseline_run_id": (
                    "controlled-shadow-baseline"
                    if comparison_status == "comparable"
                    else None
                ),
                "baseline_snapshot_ids": [],
            }
            for execution_id, comparison_status in (
                ("execution-cpu", "comparable"),
                ("execution-optical", "comparable"),
                ("execution-interface", "baseline_stale"),
            )
        ],
        "check_item_by_version": {
            version_id: check_item["check_item_id"]
            for version_id, check_item in check_items.items()
        },
        "check_items": check_items,
        "report_projection_snapshot": report_projection_snapshot,
        "report_projection_digest": report_projection_snapshot[
            "digest"
        ],
        "executions": executions,
        "assessments": assessments,
        "evidence_qualifications": evidence_qualifications,
        "coverage_decisions": coverage_decisions,
        "collection_resilience": [
            {
                "device_id": "device-core",
                "check_item_version_id": (
                    "check-item:system.cpu_usage:v1"
                ),
                "fallback_resilience": "reduced",
                "declared_command_count": 2,
                "executable_command_count": 1,
                "selected_commands": ["show system resources"],
                "unavailable_commands": [
                    {
                        "command": "show processes cpu",
                        "reason_code": "parser_not_ready",
                    }
                ],
            },
            {
                "device_id": "device-access",
                "check_item_version_id": (
                    "check-item:interface.optical_power:v1"
                ),
                "fallback_resilience": "full",
                "declared_command_count": 1,
                "executable_command_count": 1,
                "selected_commands": [
                    "display transceiver diagnosis interface"
                ],
                "unavailable_commands": [],
            },
            {
                "device_id": "device-edge",
                "check_item_version_id": (
                    "check-item:interface.oper_status:v1"
                ),
                "fallback_resilience": "full",
                "declared_command_count": 1,
                "executable_command_count": 1,
                "selected_commands": ["display interface brief"],
                "unavailable_commands": [],
            },
        ],
        "observations": observations,
        "findings": findings,
        "changes": changes,
        "events": events,
        "risks": risks,
        "risk_occurrences": [],
        "risk_remediations": [
            {
                "id": "remediation-cpu",
                "risk_case_id": "risk-cpu",
                "status": "in_progress",
                "owner": "网络运维组",
                "due_at": "2026-08-05T18:00:00+08:00",
                "action_text": "核查高负载进程并确认容量扩展窗口",
                "created_by": "acceptance-fixture",
                "completed_by": None,
                "completed_at": None,
                "evidence_refs_json": ["snapshot-execution-cpu"],
            },
            {
                "id": "remediation-optical",
                "risk_case_id": "risk-optical",
                "status": "planned",
                "owner": "传输维护组",
                "due_at": "2026-08-03T18:00:00+08:00",
                "action_text": "清洁链路端面并复测收光功率",
                "created_by": "acceptance-fixture",
                "completed_by": None,
                "completed_at": None,
                "evidence_refs_json": ["snapshot-execution-optical"],
            },
        ],
        "evidence_index": evidence_index,
        "evidence_refs": [
            item["snapshot_id"] for item in evidence_index
        ],
    }
    facts["raw_evidence_set_digest"] = raw_evidence_set_digest(
        evidence_index
    )
    facts["conclusion_permission"] = (
        build_report_conclusion_permission_from_source_facts(
            facts
        )
    )
    claim_ledger = build_report_claim_ledger(facts)
    facts["claim_basis_digest"] = claim_ledger[
        "claim_basis_digest"
    ]
    facts["claim_ledger"] = claim_ledger
    facts["claim_ledger_digest"] = claim_ledger[
        "claim_ledger_digest"
    ]
    validate_report_claim_ledger(facts)
    return {
        "id": "controlled-shadow-report-manifest",
        "patrol_batch_id": batch_id,
        "version": 1,
        "schema_version": "patrol-report-manifest/v5",
        "status": "final",
        "report_purpose": SHADOW_VALIDATION,
        "raw_evidence_set_digest": facts[
            "raw_evidence_set_digest"
        ],
        "target_manifest_digest": target_digest,
        "runtime_manifest_digest": runtime_digest,
        "claim_ledger_digest": facts[
            "claim_ledger_digest"
        ],
        "claim_count": len(claim_ledger["claims"]),
        "generator_version": "check-item-report/v5",
        "generated_at": generated_at,
        "report_cutoff_at": generated_at,
        "qualification_fact_digest": "6" * 64,
        "coverage_fact_digest": "7" * 64,
        "source_facts_json": facts,
        "source_digest": _digest(facts),
    }


def run(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_controlled_manifest()
    report = build_check_item_report_view(manifest)
    artifacts = render_check_item_artifacts(manifest)
    artifact_summary: dict[str, Any] = {}
    for name, artifact in artifacts.items():
        path = output_dir / artifact.filename
        path.write_bytes(artifact.content)
        artifact_summary[name] = {
            "path": str(path.resolve()),
            "bytes": len(artifact.content),
            "sha256": artifact.sha256,
        }

    markdown = artifacts["markdown"].content.decode("utf-8")
    required_phrases = (
        "影子验证",
        "无法判断",
        "设备输出阈值",
        "事件规则静态阈值",
        "网络运维组",
        "传输维护组",
        "恢复待确认",
    )
    missing_phrases = [
        phrase for phrase in required_phrases if phrase not in markdown
    ]
    if missing_phrases:
        raise AssertionError(
            f"report is missing required content: {missing_phrases}"
        )
    if report["current_state_authoritative"]:
        raise AssertionError(
            "controlled shadow fixture was promoted to current network state"
        )
    if report["conclusion"]["code"] != "shadow_validation_result":
        raise AssertionError(
            f"unexpected conclusion: {report['conclusion']}"
        )
    if report["blocking_reason_counts"] != {"parse_failed": 1}:
        raise AssertionError(
            "unevaluable check was not disclosed in report facts"
        )
    if len(report["risks"]) != 2:
        raise AssertionError("current fixture risks were not correlated")
    if not report["conclusion"].get("claim_id"):
        raise AssertionError(
            "overall conclusion is not authorized by the claim ledger"
        )
    if not report["coverage"].get("claim_id"):
        raise AssertionError(
            "scope conclusion is not authorized by the claim ledger"
        )
    if any(not item.get("claim_id") for item in report["matrix"]):
        raise AssertionError(
            "one or more check-item conclusions have no claim reference"
        )
    expected_claim_count = len(
        manifest["source_facts_json"]["claim_ledger"]["claims"]
    )
    if expected_claim_count < 1:
        raise AssertionError("claim ledger is unexpectedly empty")
    if report["claim_ledger_digest"] != manifest[
        "claim_ledger_digest"
    ]:
        raise AssertionError("report claim ledger digest drifted")
    expected_projection_digest = manifest["source_facts_json"][
        "report_projection_snapshot"
    ]["digest"]
    if report["report_projection_digest"] != expected_projection_digest:
        raise AssertionError("report projection digest drifted")
    if any(
        item.get("fallback_resilience") == "unknown"
        for item in report["matrix"]
    ):
        raise AssertionError(
            "controlled fixture did not freeze collection resilience"
        )
    if report["collection_resilience"].get("reduced_count") != 1:
        raise AssertionError(
            "reduced collection-path resilience was not disclosed"
        )

    workbook = load_workbook(io.BytesIO(artifacts["excel"].content))
    for sheet_name in ("CPU", "接口状态", "接口光功率"):
        if sheet_name not in workbook.sheetnames:
            raise AssertionError(
                f"technical annex is missing projection sheet: {sheet_name}"
            )
    event_headers = [
        cell.value for cell in workbook["事件"][1]
    ]
    for required_header in (
        "当前值",
        "触发判据",
        "阈值来源",
        "EventRule版本",
    ):
        if required_header not in event_headers:
            raise AssertionError(
                f"event annex is missing column: {required_header}"
            )
    risk_headers = [
        cell.value for cell in workbook["风险结果"][1]
    ]
    for required_header in (
        "当前值",
        "判据参考值",
        "判据来源",
        "责任人",
        "期限",
    ):
        if required_header not in risk_headers:
            raise AssertionError(
                f"risk annex is missing column: {required_header}"
            )
    risk_value_column = risk_headers.index("当前值") + 1
    risk_unit_column = risk_headers.index("单位") + 1
    for row_index in range(2, workbook["风险结果"].max_row + 1):
        risk_value = workbook["风险结果"].cell(
            row_index,
            risk_value_column,
        ).value
        risk_unit = workbook["风险结果"].cell(
            row_index,
            risk_unit_column,
        ).value
        if risk_value in (None, "暂无数据"):
            continue
        if isinstance(risk_value, str) and risk_unit:
            raise AssertionError(
                "risk value duplicated its unit in the numeric column"
            )
    claim_rows = list(workbook["事实声明"].iter_rows(
        min_row=2,
        values_only=True,
    ))
    populated_claim_rows = [
        row for row in claim_rows if row and row[0]
    ]
    if len(populated_claim_rows) != expected_claim_count:
        raise AssertionError(
            "Excel claim ledger does not reconcile with source claims"
        )
    for sheet_name in (
        "检查项结论",
        "风险结果",
        "历史风险待复核",
        "恢复待确认",
        "整改跟踪",
        "事件",
        "变化",
    ):
        sheet = workbook[sheet_name]
        headers = [cell.value for cell in sheet[1]]
        if "声明编号" not in headers:
            raise AssertionError(
                f"{sheet_name} is missing the claim reference column"
            )
        claim_column = headers.index("声明编号") + 1
        missing_claim_rows = [
            row_index
            for row_index in range(2, sheet.max_row + 1)
            if sheet.cell(row_index, 1).value not in (None, "暂无数据")
            and not sheet.cell(row_index, claim_column).value
        ]
        if missing_claim_rows:
            raise AssertionError(
                f"{sheet_name} rows have no claim reference: "
                f"{missing_claim_rows}"
            )
    with zipfile.ZipFile(
        io.BytesIO(artifacts["evidence"].content)
    ) as archive:
        evidence_names = sorted(archive.namelist())
        unexpected_text = [
            name
            for name in evidence_names
            if name.endswith(".txt") and name != "README.txt"
        ]
        if unexpected_text:
            raise AssertionError(
                "evidence index package unexpectedly contains raw CLI: "
                f"{unexpected_text}"
            )
        for expected_name in (
            "report-claims.json",
            "report-claims.csv",
        ):
            if expected_name not in evidence_names:
                raise AssertionError(
                    "evidence package is missing claim ledger export: "
                    f"{expected_name}"
                )

    summary = {
        "success": True,
        "schema_version": "check-item-report-content-acceptance/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fixture_kind": "controlled_shadow_validation",
        "production_network_claim": False,
        "report_purpose": report["report_purpose"],
        "conclusion": report["conclusion"],
        "risk_count": len(report["risks"]),
        "historical_risk_count": len(
            report["historical_not_reverified_risks"]
        ),
        "recovery_pending_count": len(
            report["recovery_pending_risks"]
        ),
        "blocking_reason_counts": report[
            "blocking_reason_counts"
        ],
        "claim_count": expected_claim_count,
        "claim_ledger_digest": report[
            "claim_ledger_digest"
        ],
        "report_projection_digest": report[
            "report_projection_digest"
        ],
        "projection_sheets": ["CPU", "接口状态", "接口光功率"],
        "claim_reference_reconciliation": "passed",
        "event_explainability_fields": [
            "current_value",
            "reference_value",
            "reference_source",
            "event_rule_id",
            "event_rule_version",
        ],
        "artifacts": artifact_summary,
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        **summary,
        "summary_path": str(summary_path.resolve()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Render controlled report content without making a production "
            "network claim"
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
    )
    args = parser.parse_args()
    result = run(args.output_dir.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

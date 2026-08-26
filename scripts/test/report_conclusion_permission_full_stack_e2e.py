#!/usr/bin/env python
"""Verify report conclusion permissions through the real delivery stack."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import time
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.celery_tasks.celery_app import celery
from src.core.patrol.command_splitter import split_cli_capture
from src.core.patrol.raw_importer import _read_text_best_effort
from src.infrastructure.db.models import (
    AuditLogRecord,
    CheckAssessmentRecord,
    CheckExecutionRecord,
    CheckItemRecord,
    CheckItemRuntimeManifestRecord,
    CheckItemVersionRecord,
    CommandSnapshot,
    CoverageDecisionRecord,
    EvidenceQualificationRecord,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    PatrolBatchSummaryProjection,
    PatrolReportMaterializationRequestRecord,
    PatrolRun,
    ReportManifestRecord,
    RiskCaseRecord,
    WorkflowRun,
)
from src.infrastructure.db.postgres import get_db_session
from src.infrastructure.storage.minio_client import get_minio_storage


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    statuses: tuple[str, ...]
    expected_code: str
    expected_text: str
    evaluated_allowed: bool
    overall_allowed: bool
    unresolved_risk: bool = False
    expected_entity_count: int | None = None
    observed_entity_count: int | None = None
    missing_entity_sample: tuple[tuple[str, str], ...] = ()
    fact_kind: str = "standard"
    expected_execution_status: str | None = None
    expected_output_quality: str | None = None
    expected_reason_codes: tuple[str, ...] = ()
    expected_reason_labels: tuple[str, ...] = ()
    expected_historical_risk_count: int = 0
    risk_age_days: int = 0


SCENARIOS = (
    Scenario(
        name="95_healthy_5_unknown",
        statuses=("healthy",) * 95 + ("unable_to_determine",) * 5,
        expected_code="evaluated_scope_healthy_with_unknown",
        expected_text="已评估范围内未发现异常，以下范围无法判断",
        evaluated_allowed=True,
        overall_allowed=False,
    ),
    Scenario(
        name="all_not_applicable",
        statuses=("not_applicable", "not_applicable"),
        expected_code="no_applicable_health_checks",
        expected_text="本次无适用健康检查项",
        evaluated_allowed=False,
        overall_allowed=False,
    ),
    Scenario(
        name="healthy_with_unresolved_risk",
        statuses=("healthy",),
        expected_code="unresolved_risk",
        expected_text="目标范围内仍存在未关闭风险",
        evaluated_allowed=False,
        overall_allowed=False,
        unresolved_risk=True,
        expected_historical_risk_count=1,
    ),
    Scenario(
        name="all_healthy",
        statuses=("healthy", "healthy"),
        expected_code="healthy",
        expected_text="本次巡检范围整体健康",
        evaluated_allowed=True,
        overall_allowed=True,
    ),
    Scenario(
        name="partial_entity_coverage",
        statuses=("unable_to_determine",),
        expected_code="partially_unknown",
        expected_text="部分检查项无法判断",
        evaluated_allowed=False,
        overall_allowed=False,
        expected_entity_count=48,
        observed_entity_count=40,
        missing_entity_sample=(
            ("interface", "GigabitEthernet1/0/41"),
            ("interface", "GigabitEthernet1/0/42"),
            ("interface", "GigabitEthernet1/0/43"),
        ),
    ),
    Scenario(
        name="zero_business_records",
        statuses=("unable_to_determine",),
        expected_code="partially_unknown",
        expected_text="部分检查项无法判断",
        evaluated_allowed=False,
        overall_allowed=False,
        fact_kind="zero_records",
        expected_execution_status="completed",
        expected_output_quality="empty",
        expected_reason_codes=(
            "output_quality_empty",
            "coverage_empty",
            "no_observations",
        ),
        expected_reason_labels=(
            "命令输出为空",
            "未观测到业务实体",
            "未形成结构化观测",
        ),
    ),
    Scenario(
        name="truncated_partial_parse",
        statuses=("unable_to_determine",),
        expected_code="partially_unknown",
        expected_text="部分检查项无法判断",
        evaluated_allowed=False,
        overall_allowed=False,
        fact_kind="truncated",
        expected_execution_status="completed",
        expected_output_quality="truncated",
        expected_entity_count=48,
        observed_entity_count=2,
        missing_entity_sample=(
            ("interface", "GigabitEthernet1/0/3"),
            ("interface", "GigabitEthernet1/0/4"),
            ("interface", "GigabitEthernet1/0/5"),
        ),
        expected_reason_codes=(
            "output_quality_truncated",
            "coverage_partial",
            "entity_coverage_incomplete",
        ),
        expected_reason_labels=(
            "命令输出被截断",
            "仅覆盖部分业务实体",
            "业务实体覆盖不完整",
        ),
    ),
    Scenario(
        name="parser_template_missing",
        statuses=("unable_to_determine",),
        expected_code="partially_unknown",
        expected_text="部分检查项无法判断",
        evaluated_allowed=False,
        overall_allowed=False,
        fact_kind="template_missing",
        expected_execution_status="parse_failed",
        expected_output_quality="complete",
        expected_reason_codes=(
            "execution_parse_failed",
            "template_missing",
            "coverage_not_evaluable",
        ),
        expected_reason_labels=(
            "执行终态：Parser 解析失败",
            "缺少 Parser 模板",
            "无法评估实体覆盖",
        ),
    ),
    Scenario(
        name="parser_compile_failed",
        statuses=("unable_to_determine",),
        expected_code="partially_unknown",
        expected_text="部分检查项无法判断",
        evaluated_allowed=False,
        overall_allowed=False,
        fact_kind="compile_failed",
        expected_execution_status="parse_failed",
        expected_output_quality="complete",
        expected_reason_codes=(
            "execution_parse_failed",
            "parser_compile_failed",
            "coverage_not_evaluable",
        ),
        expected_reason_labels=(
            "执行终态：Parser 解析失败",
            "Parser 模板编译失败",
            "无法评估实体覆盖",
        ),
    ),
    Scenario(
        name="parser_parse_failed",
        statuses=("unable_to_determine",),
        expected_code="partially_unknown",
        expected_text="部分检查项无法判断",
        evaluated_allowed=False,
        overall_allowed=False,
        fact_kind="parse_failed",
        expected_execution_status="parse_failed",
        expected_output_quality="complete",
        expected_reason_codes=(
            "execution_parse_failed",
            "parse_failed",
            "coverage_not_evaluable",
        ),
        expected_reason_labels=(
            "执行终态：Parser 解析失败",
            "Parser 解析失败",
            "无法评估实体覆盖",
        ),
    ),
    Scenario(
        name="historical_risk_not_reverified",
        statuses=("unable_to_determine",),
        expected_code="unresolved_risk_with_unknown",
        expected_text="存在未关闭风险，且部分检查项无法判断",
        evaluated_allowed=False,
        overall_allowed=False,
        unresolved_risk=True,
        fact_kind="parse_failed",
        expected_execution_status="parse_failed",
        expected_output_quality="complete",
        expected_reason_codes=(
            "execution_parse_failed",
            "parse_failed",
            "coverage_not_evaluable",
        ),
        expected_reason_labels=(
            "执行终态：Parser 解析失败",
            "Parser 解析失败",
            "无法评估实体覆盖",
        ),
        expected_historical_risk_count=1,
        risk_age_days=30,
    ),
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


def _load_cpu_evidence(source_file: Path) -> dict[str, Any]:
    text = _read_text_best_effort(source_file)
    blocks = [
        block
        for block in split_cli_capture(text)
        if "cpu" in block.command_canonical.lower()
    ]
    if not blocks:
        raise AssertionError(
            f"no CPU command output found in {source_file}"
        )
    block = blocks[0]
    raw_text = str(block.raw_output)
    if not raw_text.strip():
        raise AssertionError("CPU command output is empty")
    return {
        "command": str(block.command),
        "canonical_command": str(block.command_canonical),
        "raw_text": raw_text,
        "raw_text_hash": hashlib.sha256(
            raw_text.encode("utf-8")
        ).hexdigest(),
        "source_file_hash": hashlib.sha256(
            source_file.read_bytes()
        ).hexdigest(),
        "start_line": int(block.start_line),
        "end_line": int(block.end_line),
    }


def _login(
    client: httpx.Client,
    *,
    username: str,
    password: str,
) -> str:
    response = client.post(
        "/api/auth/login/",
        json={"username": username, "password": password},
    )
    if response.status_code >= 400:
        raise AssertionError(
            f"BFF login returned {response.status_code}: "
            f"{response.text}"
        )
    token = str(
        (response.json().get("data") or {}).get("access") or ""
    )
    if not token:
        raise AssertionError("BFF login returned no access token")
    return token


def _json_response(
    response: httpx.Response,
    *,
    expected_status: int = 200,
) -> dict[str, Any]:
    if response.status_code != expected_status:
        raise AssertionError(
            f"{response.request.method} {response.request.url.path} "
            f"returned {response.status_code}: {response.text}"
        )
    payload = response.json()
    if (
        payload.get("success") is True
        and isinstance(payload.get("data"), dict)
    ):
        return dict(payload["data"])
    return dict(payload)


def _wait_for_report(
    client: httpx.Client,
    *,
    batch_id: str,
    task_id: str | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status: dict[str, Any] = {}
    last_task_state = "UNTRACKED"
    while time.monotonic() < deadline:
        if task_id:
            last_task_state = str(
                celery.AsyncResult(task_id).state
            )
        last_status = _json_response(
            client.get(
                f"/api/patrol/batches/{batch_id}/reports/"
                "materialization-status"
            )
        )
        if (
            last_status.get("status") == "completed"
            and (
                not task_id
                or last_task_state == "SUCCESS"
            )
        ):
            return last_status
        if (
            last_status.get("status") == "failed"
            or last_task_state == "FAILURE"
        ):
            raise AssertionError(
                "report task failed: "
                f"{last_status.get('error') or celery.AsyncResult(task_id).result}"
            )
        time.sleep(0.5)
    raise TimeoutError(
        f"report task did not complete: celery={last_task_state}, "
        f"api={last_status}"
    )


def _seed_check_item(
    *,
    suffix: str,
) -> tuple[str, str]:
    check_item_id = f"e2e.report_conclusion.{suffix}"
    version_id = f"e2e-ci-{suffix}"
    now = _utcnow()
    canonical_contract = {
        "entity_type": "cpu_scope",
        "primary_keys": ["scope"],
        "observations": [
            {
                "observation_type": "device.cpu.utilization",
                "unit": "percent",
                "dimensions": ["window"],
            }
        ],
    }
    applicability = {
        "schema_version": "check-item-applicability/v1",
        "conditions": [
            {
                "field": "device_role",
                "operator": "in",
                "values": ["router", "switch"],
            }
        ],
    }
    version_digest = _digest(
        {
            "canonical_contract": canonical_contract,
            "applicability": applicability,
        }
    )
    with get_db_session() as db:
        db.add_all(
            [
                CheckItemRecord(
                    id=check_item_id,
                    name="E2E CPU 使用率",
                    category="system",
                    description="结论权限隔离验收检查项",
                    status="active",
                    current_version=1,
                    created_at=now,
                    updated_at=now,
                ),
                CheckItemVersionRecord(
                    id=version_id,
                    check_item_id=check_item_id,
                    version=1,
                    result_mode="structured",
                    assessment_mode="health",
                    canonical_contract_json=canonical_contract,
                    report_projection="cpu",
                    applicability_json=applicability,
                    freshness_policy_json={
                        "max_observation_age": "PT24H"
                    },
                    empty_result_policy_json={
                        "classification": "invalid"
                    },
                    coverage_policy_json={
                        "model": "inventory_entity_set",
                        "inventory_source": "frozen_target_inventory",
                    },
                    status="active",
                    digest=version_digest,
                    created_by="full-stack-e2e",
                    created_at=now,
                    activated_at=now,
                ),
            ]
        )
    return check_item_id, version_id


def _seed_batch(
    *,
    suffix: str,
    scenario: Scenario,
    check_item_id: str,
    version_id: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    now = _utcnow()
    batch_id = f"conclusion-e2e-{scenario.name}-{suffix}"
    run_id = f"conclusion-run-{scenario.name[:12]}-{suffix}"
    runtime_id = f"runtime-{scenario.name[:12]}-{suffix}"
    action_id = f"action-{scenario.name[:12]}-{suffix}"
    device_ids = [
        f"conclusion-device-{scenario.name[:8]}-{index:03d}-{suffix}"
        for index in range(len(scenario.statuses))
    ]
    target_digest = _digest(
        {
            "scenario": scenario.name,
            "devices": device_ids,
            "check_item_version_id": version_id,
        }
    )
    inventory_digest = _digest(
        {
            "scenario": scenario.name,
            "inventory": [
                {
                    "device_id": device_id,
                    "vendor": "H3C",
                    "model": "lixian-fixture",
                }
                for device_id in device_ids
            ],
        }
    )
    runtime_payload = {
        "schema_version": "check-item-runtime-manifest/v2",
        "patrol_batch_id": batch_id,
        "target_manifest_digest": target_digest,
        "inventory_snapshot_digest": inventory_digest,
        "check_item_versions": [version_id],
        "device_check_item_versions": {
            device_id: [version_id] for device_id in device_ids
        },
        "collection_actions": [{"id": action_id}],
        "runtime_bundle_refs": [],
        "comparison_contexts": [],
        "entity_identity_policy_versions": [],
        "assessment_schema_version": "check-assessment/v1",
        "report_schema_version": "patrol-report/v4",
    }
    runtime_digest = _digest(runtime_payload)
    runtime_payload["digest"] = runtime_digest

    with get_db_session() as db:
        db.add_all(
            [
                PatrolBatchRecord(
                    patrol_batch_id=batch_id,
                    workflow_name="patrol-change-event",
                    launch_mode="workflow",
                    patrol_mode="production",
                    report_purpose="production_current",
                    target_type="devices",
                    target_label=(
                        f"Conclusion permission E2E: {scenario.name}"
                    ),
                    selector_json={
                        "scenario": scenario.name,
                        "source": "lixian",
                    },
                    target_count=len(device_ids),
                    launched_count=len(device_ids),
                    skipped_count=0,
                    target_manifest_digest=target_digest,
                    inventory_snapshot_digest=inventory_digest,
                    runtime_manifest_id=runtime_id,
                    runtime_manifest_digest=runtime_digest,
                    save_baseline=False,
                    dispatch_status="completed",
                    dispatch_requested_at=now,
                    dispatch_started_at=now,
                    dispatch_completed_at=now,
                    report_status="not_requested",
                    archived=False,
                    created_at=now,
                    updated_at=now,
                ),
                PatrolRun(
                    id=run_id,
                    mode="production",
                    source="offline_file",
                    status="completed",
                    collection_status="completed",
                    parser_readiness_status="ready",
                    fact_readiness_status="ready",
                    analysis_status="completed",
                    baseline_eligible=False,
                    run_purpose="production",
                    logical_run_id=run_id,
                    attempt_number=0,
                    owner_generation=1,
                    device_count=len(device_ids),
                    command_count=len(device_ids),
                    started_at=now,
                    completed_at=now,
                    metadata_json={
                        "scenario": scenario.name,
                        "source": "lixian",
                        "full_stack_e2e": True,
                    },
                    created_at=now,
                    updated_at=now,
                ),
                CheckItemRuntimeManifestRecord(
                    id=runtime_id,
                    patrol_batch_id=batch_id,
                    schema_version=2,
                    target_manifest_digest=target_digest,
                    inventory_snapshot_digest=inventory_digest,
                    check_item_versions_json=[version_id],
                    collection_actions_json=[action_id],
                    runtime_bundle_refs_json=[],
                    comparison_context_ids_json=[],
                    entity_identity_policy_versions_json=[],
                    assessment_schema_version="check-assessment/v1",
                    report_schema_version="patrol-report/v4",
                    manifest_json=runtime_payload,
                    digest=runtime_digest,
                    status="frozen",
                    created_by="full-stack-e2e",
                    created_at=now,
                ),
            ]
        )
        db.flush()

        for index, (device_id, status) in enumerate(
            zip(device_ids, scenario.statuses, strict=True)
        ):
            compact = f"{scenario.name[:8]}-{index:03d}-{suffix}"
            workflow_id = f"wf-{compact}"
            execution_id = f"exec-{compact}"
            assessment_id = f"assess-{compact}"
            snapshot_id = f"snap-{compact}"
            qualification_id = f"qual-{compact}"
            coverage_id = f"cover-{compact}"
            is_not_applicable = status == "not_applicable"
            is_unknown = status == "unable_to_determine"
            is_zero_records = scenario.fact_kind == "zero_records"
            is_truncated = scenario.fact_kind == "truncated"
            is_parser_failure = scenario.fact_kind in {
                "template_missing",
                "compile_failed",
                "parse_failed",
            }
            has_entity_gap = (
                is_unknown
                and scenario.expected_entity_count is not None
            )
            has_parser_failure = (
                is_parser_failure
                or (
                    is_unknown
                    and not has_entity_gap
                    and not is_zero_records
                )
            )
            observed_entity_count = (
                int(scenario.observed_entity_count or 0)
                if has_entity_gap
                else 1
                if status == "healthy"
                else 0
            )
            expected_entity_count = (
                int(scenario.expected_entity_count or 0)
                if has_entity_gap
                else 0
                if is_not_applicable
                else None
                if has_parser_failure
                else 1
            )
            execution_status = (
                str(scenario.expected_execution_status)
                if scenario.expected_execution_status
                else "not_applicable"
                if is_not_applicable
                else "parse_failed"
                if is_unknown
                else "completed"
            )
            parser_reason = {
                "template_missing": "template_missing",
                "compile_failed": "parser_compile_failed",
                "parse_failed": "parse_failed",
            }.get(
                scenario.fact_kind,
                "parser_failed"
                if has_parser_failure
                else None,
            )
            reason_codes = (
                ["applicability_rule_no_match"]
                if is_not_applicable
                else [parser_reason]
                if parser_reason
                else ["output_quality_truncated"]
                if is_truncated
                else []
            )
            output_quality_status = (
                "unsupported"
                if is_not_applicable
                else "empty"
                if is_zero_records
                else "truncated"
                if is_truncated
                else "complete"
            )
            empty_result_classification = (
                "not_applicable"
                if is_not_applicable
                else "invalid"
                if is_zero_records
                else "not_evaluated"
                if is_truncated
                else "not_empty"
            )
            coverage_status = (
                "not_applicable"
                if is_not_applicable
                else "empty"
                if is_zero_records
                else "partial"
                if has_entity_gap
                else "not_evaluable"
                if is_unknown
                else "complete"
            )
            coverage_basis = (
                "unknown"
                if has_parser_failure
                else "inventory_entity_set"
            )
            coverage_reason_codes = (
                ["applicability_rule_no_match"]
                if is_not_applicable
                else ["coverage_empty"]
                if is_zero_records
                else [
                    "entity_coverage_incomplete",
                    "entity_keys_missing",
                ]
                if has_entity_gap
                else [
                    "coverage_not_evaluable",
                    str(parser_reason or "parser_failed"),
                ]
                if is_unknown
                else []
            )
            assessment_blockers = (
                ["entity_coverage_incomplete"]
                if has_entity_gap
                and not is_truncated
                else [
                    "output_quality_truncated",
                    "coverage_partial",
                    "entity_coverage_incomplete",
                ]
                if is_truncated
                else [
                    "output_quality_empty",
                    "coverage_empty",
                    "no_observations",
                    "no_observed_entities",
                    "no_entities_evaluated",
                ]
                if is_zero_records
                else [
                    "execution_parse_failed",
                    str(parser_reason or "parser_failed"),
                    "coverage_not_evaluable",
                ]
                if is_unknown
                else []
            )
            raw_text = (
                "CPU utilization table\n"
                if is_zero_records
                else (
                    "Interface  CPU Usage\n"
                    "GigabitEthernet1/0/1  35\n"
                    "GigabitEthernet1/0/2  36\n"
                    "[truncated]\n"
                )
                if is_truncated
                else str(evidence["raw_text"])
            )
            structured_records = (
                [
                    {
                        "interface": (
                            f"GigabitEthernet1/0/{entity + 1}"
                        ),
                        "cpu_usage": 35,
                    }
                    for entity in range(observed_entity_count)
                ]
                if has_entity_gap
                else []
                if is_zero_records
                else None
                if has_parser_failure
                else [{"cpu_usage": 35}]
            )
            snapshot_parse_status = (
                "template_missing"
                if scenario.fact_kind == "template_missing"
                else "parse_failed"
                if has_parser_failure
                else "parsed"
            )
            snapshot_output_status = (
                "valid_empty"
                if is_zero_records
                else "truncated"
                if is_truncated
                else "success"
            )
            db.add_all(
                [
                    WorkflowRun(
                        id=workflow_id,
                        template_name="patrol-change-event",
                        source="full-stack-e2e",
                        status="completed",
                        context={
                            "patrol_batch_id": batch_id,
                            "device_id": device_id,
                            "scenario": scenario.name,
                        },
                        current_step_index=1,
                        created_at=now,
                        updated_at=now,
                        completed_at=now,
                    ),
                    # Every target is retained even when the check is unknown
                    # or not applicable.
                    PatrolBatchDeviceRecord(
                        id=f"target-{compact}",
                        patrol_batch_id=batch_id,
                        device_id=device_id,
                        device_name=f"E2E-{scenario.name}-{index + 1}",
                        management_ip=f"198.51.{index // 250}.{index % 250 + 1}",
                        selection_status="launched",
                        preflight_status="ready",
                        workflow_run_id=workflow_id,
                        celery_task_id=f"task-{compact}",
                        dispatch_status="dispatched",
                        dispatch_attempts=1,
                        dispatched_at=now,
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )

            has_raw_evidence = not is_not_applicable
            if has_raw_evidence:
                db.add(
                    CommandSnapshot(
                        id=snapshot_id,
                        run_id=run_id,
                        device_id=device_id,
                        execution_item_id=action_id,
                        collection_action_id=action_id,
                        check_item_ids_json=[check_item_id],
                        device_name=f"E2E-{scenario.name}-{index + 1}",
                        management_ip=(
                            f"198.51.{index // 250}.{index % 250 + 1}"
                        ),
                        vendor="H3C",
                        model="lixian-fixture",
                        command=evidence["command"],
                        canonical_command=evidence[
                            "canonical_command"
                        ],
                        observed_at=now,
                        structured_json=structured_records,
                        raw_text=raw_text,
                        raw_text_hash=hashlib.sha256(
                            raw_text.encode("utf-8")
                        ).hexdigest(),
                        parse_status=snapshot_parse_status,
                        output_status=snapshot_output_status,
                        snapshot_kind="full",
                        source_file_hash=evidence[
                            "source_file_hash"
                        ],
                        start_line=evidence["start_line"],
                        end_line=evidence["end_line"],
                        metadata_json={
                            "scenario": scenario.name,
                            "full_stack_e2e": True,
                        },
                        created_at=now,
                    )
                )

            db.add(
                CheckExecutionRecord(
                    id=execution_id,
                    idempotency_key=execution_id,
                    patrol_batch_id=batch_id,
                    device_id=device_id,
                    check_item_version_id=version_id,
                    collection_action_ids_json=[action_id],
                    execution_status=execution_status,
                    reason_codes_json=reason_codes,
                    observation_count=(
                        observed_entity_count
                    ),
                    observed_entity_count=(
                        observed_entity_count
                    ),
                    change_count=0,
                    event_count=0,
                    current_finding_count=0,
                    assessment_id=assessment_id,
                    evidence_qualification_id=(
                        qualification_id
                        if has_raw_evidence
                        else None
                    ),
                    coverage_decision_id=coverage_id,
                    source_identity_status=(
                        "confirmed"
                        if has_raw_evidence
                        else "unknown"
                    ),
                    output_quality_status=output_quality_status,
                    freshness_status=(
                        "fresh" if has_raw_evidence else "unknown"
                    ),
                    empty_result_classification=(
                        empty_result_classification
                    ),
                    coverage_status=coverage_status,
                    coverage_basis=coverage_basis,
                    risk_verification_status=(
                        "not_applicable"
                        if is_not_applicable
                        else "not_reverified"
                        if is_unknown
                        else "no_current_risk"
                    ),
                    expected_entity_count=(
                        expected_entity_count
                    ),
                    observation_min_at=(
                        now if has_raw_evidence else None
                    ),
                    observation_max_at=(
                        now if has_raw_evidence else None
                    ),
                    target_manifest_digest=target_digest,
                    asset_manifest_digest=runtime_digest,
                    evidence_refs_json=(
                        [snapshot_id] if has_raw_evidence else []
                    ),
                    completed_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.flush()

            if has_raw_evidence:
                qualification_policy = {
                    "source_identity": "inventory_match",
                    "output_quality": output_quality_status,
                    "freshness": "PT24H",
                }
                db.add(
                    EvidenceQualificationRecord(
                        id=qualification_id,
                        digest=_digest(
                            {
                                "id": qualification_id,
                                "snapshot_id": snapshot_id,
                            }
                        ),
                        check_execution_id=execution_id,
                        patrol_batch_id=batch_id,
                        device_id=device_id,
                        check_item_version_id=version_id,
                        runtime_bundle_digest=runtime_digest,
                        source_identity_status="confirmed",
                        output_quality_status=output_quality_status,
                        freshness_status="fresh",
                        empty_result_classification=(
                            empty_result_classification
                        ),
                        snapshot_ids_json=[snapshot_id],
                        observation_min_at=now,
                        observation_max_at=now,
                        qualification_reason_codes_json=(
                            []
                            if output_quality_status == "complete"
                            else [
                                f"output_quality_"
                                f"{output_quality_status}"
                            ]
                        ),
                        policy_snapshot_json=qualification_policy,
                        policy_digest=_digest(
                            qualification_policy
                        ),
                        evidence_refs_json=[snapshot_id],
                        qualified_at=now,
                        created_at=now,
                    )
                )

            denominator_ref = (
                {
                    "applicability_rule_digest": _digest(
                        {
                            "check_item_version_id": version_id,
                            "status": "not_applicable",
                        }
                    ),
                    "inventory_snapshot_digest": inventory_digest,
                }
                if is_not_applicable
                else {
                    "basis": "inventory_entity_set",
                    "inventory_snapshot_digest": inventory_digest,
                }
            )
            coverage_policy = {
                "model": "inventory_entity_set",
                "inventory_source": "frozen_target_inventory",
            }
            expected_entity_keys = (
                [
                    [
                        "interface",
                        f"GigabitEthernet1/0/{entity + 1}",
                    ]
                    for entity in range(
                        int(expected_entity_count or 0)
                    )
                ]
                if has_entity_gap
                else [["cpu_scope", "device"]]
                if expected_entity_count
                else []
            )
            observed_entity_keys = expected_entity_keys[
                :observed_entity_count
            ]
            db.add(
                CoverageDecisionRecord(
                    id=coverage_id,
                    digest=_digest(
                        {
                            "id": coverage_id,
                            "status": coverage_status,
                            "denominator": denominator_ref,
                        }
                    ),
                    check_execution_id=execution_id,
                    patrol_batch_id=batch_id,
                    device_id=device_id,
                    check_item_version_id=version_id,
                    coverage_status=coverage_status,
                    coverage_basis=coverage_basis,
                    expected_entity_count=(
                        expected_entity_count
                    ),
                    observed_entity_count=(
                        observed_entity_count
                    ),
                    evaluated_entity_count=(
                        observed_entity_count
                        if status == "healthy"
                        else 0
                    ),
                    expected_entity_set_digest=(
                        None
                        if has_parser_failure
                        else _digest(expected_entity_keys)
                    ),
                    observed_entity_set_digest=_digest(
                        observed_entity_keys
                    ),
                    denominator_ref_json=denominator_ref,
                    missing_entity_sample_json=[
                        list(item)
                        for item in scenario.missing_entity_sample
                    ],
                    unexpected_entity_sample_json=[],
                    reason_codes_json=coverage_reason_codes,
                    policy_snapshot_json=coverage_policy,
                    policy_digest=_digest(coverage_policy),
                    evidence_refs_json=(
                        [snapshot_id] if has_raw_evidence else []
                    ),
                    decided_at=now,
                    created_at=now,
                )
            )
            db.add(
                CheckAssessmentRecord(
                    id=assessment_id,
                    idempotency_key=assessment_id,
                    patrol_batch_id=batch_id,
                    device_id=device_id,
                    check_item_version_id=version_id,
                    execution_id=execution_id,
                    assessment_status=status,
                    completeness_status=(
                        "partial"
                        if has_entity_gap
                        else "insufficient"
                        if is_unknown
                        else "complete"
                    ),
                    comparison_status="first_observation",
                    expected_entity_count=(
                        expected_entity_count
                    ),
                    observed_entity_count=(
                        observed_entity_count
                    ),
                    evaluated_entity_count=(
                        1 if status == "healthy" else 0
                    ),
                    threshold_unknown_count=0,
                    current_finding_ids_json=[],
                    risk_case_ids_json=[],
                    evidence_qualification_id=(
                        qualification_id
                        if has_raw_evidence
                        else None
                    ),
                    coverage_decision_id=coverage_id,
                    source_identity_status=(
                        "confirmed"
                        if has_raw_evidence
                        else "unknown"
                    ),
                    output_quality_status=output_quality_status,
                    freshness_status=(
                        "fresh" if has_raw_evidence else "unknown"
                    ),
                    empty_result_classification=(
                        empty_result_classification
                    ),
                    coverage_status=coverage_status,
                    coverage_basis=coverage_basis,
                    risk_verification_status=(
                        "not_applicable"
                        if is_not_applicable
                        else "not_reverified"
                        if is_unknown
                        else "no_current_risk"
                    ),
                    blocking_reason_codes_json=(
                        assessment_blockers
                    ),
                    assessment_rule_bundle_digest=runtime_digest,
                    evidence_digest=_digest(
                        {
                            "execution_id": execution_id,
                            "status": status,
                            "snapshot_id": (
                                snapshot_id
                                if has_raw_evidence
                                else None
                            ),
                        }
                    ),
                    assessed_at=now,
                    created_at=now,
                )
            )

        if scenario.unresolved_risk:
            risk_at = now - timedelta(
                days=max(0, scenario.risk_age_days)
            )
            db.add(
                RiskCaseRecord(
                    id=f"risk-{scenario.name[:12]}-{suffix}",
                    correlation_key=(
                        f"e2e:{scenario.name}:{suffix}:cpu"
                    ),
                    check_item_id=check_item_id,
                    device_id=device_ids[0],
                    entity_type="cpu_scope",
                    entity_key='["device","0"]',
                    correlation_dimensions_json={"scope": "device"},
                    risk_type="CpuUtilizationHigh",
                    severity="major",
                    status="open",
                    first_seen_at=risk_at,
                    last_seen_at=risk_at,
                    verification_status="not_reverified",
                    source_finding_ids_json=[],
                    source_event_ids_json=[],
                    remediation_status="not_started",
                    created_at=risk_at,
                    updated_at=risk_at,
                )
            )
    return {
        "batch_id": batch_id,
        "run_id": run_id,
        "device_count": len(device_ids),
        "workflow_ids": [
            f"wf-{scenario.name[:8]}-{index:03d}-{suffix}"
            for index in range(len(device_ids))
        ],
    }


def _download_and_compare(
    client: httpx.Client,
    *,
    batch_id: str,
    manifest: dict[str, Any],
    scenario: Scenario,
) -> dict[str, Any]:
    paths = {
        "markdown": "report.md",
        "docx": "report.docx",
        "excel": "report.xlsx",
        "json": "report.json",
        "evidence": "evidence.zip",
    }
    expected_digests = dict(manifest["artifact_sha256"])
    object_keys = dict(manifest["artifact_object_keys"])
    storage = get_minio_storage()
    contents: dict[str, bytes] = {}
    artifact_result: dict[str, Any] = {}
    for name, path in paths.items():
        response = client.get(
            f"/api/workflows/runs/patrol-batches/{batch_id}/{path}"
        )
        if response.status_code != 200:
            raise AssertionError(
                f"{name} download failed: {response.status_code} "
                f"{response.text}"
            )
        actual_digest = hashlib.sha256(response.content).hexdigest()
        if actual_digest != expected_digests.get(name):
            raise AssertionError(
                f"{name} differs from manifest digest"
            )
        stored = storage.download_file(str(object_keys[name]))
        if stored != response.content:
            raise AssertionError(
                f"{name} differs between BFF and MinIO"
            )
        contents[name] = response.content
        artifact_result[name] = {
            "bytes": len(response.content),
            "sha256": actual_digest,
            "object_key": object_keys[name],
        }

    json_report = json.loads(contents["json"].decode("utf-8"))
    permission = dict(json_report.get("conclusion_permission") or {})
    api_permission = dict(
        manifest.get("conclusion_permission") or {}
    )
    if permission != api_permission:
        raise AssertionError(
            "JSON and report-manifest conclusion permissions differ"
        )
    if json_report.get("conclusion", {}).get("code") != (
        scenario.expected_code
    ):
        raise AssertionError(
            f"unexpected conclusion code: "
            f"{json_report.get('conclusion')}"
        )
    if json_report.get("conclusion", {}).get("text") != (
        scenario.expected_text
    ):
        raise AssertionError(
            f"unexpected conclusion text: "
            f"{json_report.get('conclusion')}"
        )
    if scenario.expected_text not in contents["markdown"].decode(
        "utf-8"
    ):
        raise AssertionError("Markdown conclusion differs")
    with zipfile.ZipFile(io.BytesIO(contents["docx"])) as archive:
        document_xml = archive.read(
            "word/document.xml"
        ).decode("utf-8")
    if scenario.expected_text not in document_xml:
        raise AssertionError("DOCX conclusion differs")
    workbook = load_workbook(io.BytesIO(contents["excel"]))
    cell_values = {
        str(cell.value)
        for sheet in workbook.worksheets
        for row in sheet.iter_rows()
        for cell in row
        if cell.value is not None
    }
    if scenario.expected_text not in cell_values:
        raise AssertionError("XLSX conclusion differs")
    matrix_rows = list(json_report.get("matrix") or [])
    if (
        scenario.expected_execution_status
        or scenario.expected_output_quality
        or scenario.expected_reason_codes
    ):
        if len(matrix_rows) != 1:
            raise AssertionError(
                "failure-truth scenario must have exactly one report row"
            )
        row = dict(matrix_rows[0])
        if (
            scenario.expected_execution_status
            and row.get("execution_status")
            != scenario.expected_execution_status
        ):
            raise AssertionError(
                "JSON report lost the execution failure layer"
            )
        if (
            scenario.expected_output_quality
            and row.get("output_quality_status")
            != scenario.expected_output_quality
        ):
            raise AssertionError(
                "JSON report lost the output-quality decision"
            )
        actual_reasons = {
            str(item)
            for item in row.get("blocking_reason_codes") or []
        }
        missing_reasons = (
            set(scenario.expected_reason_codes) - actual_reasons
        )
        if missing_reasons:
            raise AssertionError(
                "JSON report lost blocking reason codes: "
                f"{sorted(missing_reasons)}"
            )
        markdown = contents["markdown"].decode("utf-8")
        for label in scenario.expected_reason_labels:
            if (
                label not in markdown
                or label not in document_xml
                or not any(
                    label in value for value in cell_values
                )
            ):
                raise AssertionError(
                    "human report artifacts lost failure label: "
                    f"{label}"
                )
    if scenario.expected_entity_count is not None:
        if len(matrix_rows) != 1:
            raise AssertionError(
                "coverage scenario must have exactly one report row"
            )
        row = dict(matrix_rows[0])
        expected_missing = [
            list(item)
            for item in scenario.missing_entity_sample
        ]
        if int(row.get("expected_entity_count") or 0) != int(
            scenario.expected_entity_count
        ):
            raise AssertionError(
                "JSON report lost expected entity count"
            )
        if int(row.get("observed_entity_count") or 0) != int(
            scenario.observed_entity_count or 0
        ):
            raise AssertionError(
                "JSON report lost observed entity count"
            )
        if row.get("missing_entity_sample") != expected_missing:
            raise AssertionError(
                "JSON report lost missing entity evidence"
            )
        sample_name = scenario.missing_entity_sample[0][1]
        markdown = contents["markdown"].decode("utf-8")
        if (
            f"已观测 {scenario.observed_entity_count} / "
            f"期望 {scenario.expected_entity_count}"
            not in markdown
            or sample_name not in markdown
        ):
            raise AssertionError(
                "Markdown report lost coverage gap evidence"
            )
        if sample_name not in document_xml:
            raise AssertionError(
                "DOCX report lost coverage gap evidence"
            )
        conclusion_sheet = workbook["检查项结论"]
        headers = {
            str(cell.value): cell.column
            for cell in conclusion_sheet[1]
            if cell.value is not None
        }
        if sample_name not in str(
            conclusion_sheet.cell(
                2,
                headers["缺失实体样例"],
            ).value
        ):
            raise AssertionError(
                "XLSX report lost coverage gap evidence"
            )
    historical_risks = list(
        json_report.get("historical_not_reverified_risks")
        or []
    )
    if len(historical_risks) != (
        scenario.expected_historical_risk_count
    ):
        raise AssertionError(
            "historical risk partition mismatch: "
            f"{len(historical_risks)}"
        )
    if scenario.expected_historical_risk_count:
        risk = dict(historical_risks[0])
        if risk.get("verification_status") != "not_reverified":
            raise AssertionError(
                "historical risk was incorrectly refreshed or recovered"
            )
        last_seen = datetime.fromisoformat(
            str(risk["last_seen_at"])
        )
        if (
            scenario.risk_age_days > 0
            and (
                _utcnow()
                - last_seen.astimezone(timezone.utc)
            )
            < timedelta(
                days=max(1, scenario.risk_age_days - 1)
            )
        ):
            raise AssertionError(
                "historical risk last_seen_at was refreshed"
            )
        if (
            "历史风险待复核"
            not in contents["markdown"].decode("utf-8")
            or "CpuUtilizationHigh" not in document_xml
            or "CpuUtilizationHigh" not in cell_values
        ):
            raise AssertionError(
                "historical risk disclosure differs across artifacts"
            )
    with zipfile.ZipFile(io.BytesIO(contents["evidence"])) as archive:
        evidence_entries = sorted(archive.namelist())
        if not evidence_entries:
            raise AssertionError("evidence package is empty")

    release_view = _json_response(
        client.get(
            f"/api/patrol/batches/{batch_id}/reports/"
            f"{manifest['report_manifest_id']}/release"
        )
    )
    if (
        release_view.get("conclusion_permission")
        != api_permission
    ):
        raise AssertionError(
            "signoff view does not consume the frozen permission"
        )
    if release_view.get("conclusion", {}).get("text") != (
        scenario.expected_text
    ):
        raise AssertionError(
            "signoff view conclusion differs from artifacts"
        )
    return {
        "conclusion": json_report["conclusion"],
        "conclusion_permission": permission,
        "release_status": release_view.get("release_status"),
        "failure_truth": (
            {
                "execution_status": (
                    matrix_rows[0].get("execution_status")
                    if matrix_rows
                    else None
                ),
                "output_quality_status": (
                    matrix_rows[0].get(
                        "output_quality_status"
                    )
                    if matrix_rows
                    else None
                ),
                "blocking_reason_codes": (
                    matrix_rows[0].get(
                        "blocking_reason_codes"
                    )
                    if matrix_rows
                    else []
                ),
                "historical_risk_count": len(
                    historical_risks
                ),
            }
            if scenario.expected_reason_codes
            or scenario.expected_historical_risk_count
            else {}
        ),
        "artifacts": artifact_result,
        "evidence_entries": evidence_entries,
    }


def _assert_permission(
    *,
    scenario: Scenario,
    permission: dict[str, Any],
) -> None:
    if permission.get("terminal_truth_reconciled") is not True:
        raise AssertionError(
            f"{scenario.name}: terminal truth is not reconciled"
        )
    if permission.get("current_conclusion_ready") is not True:
        raise AssertionError(
            f"{scenario.name}: current conclusion is not ready"
        )
    if (
        permission.get(
            "evaluated_scope_no_abnormality_allowed"
        )
        is not scenario.evaluated_allowed
    ):
        raise AssertionError(
            f"{scenario.name}: evaluated-scope permission mismatch"
        )
    if (
        permission.get("overall_healthy_allowed")
        is not scenario.overall_allowed
    ):
        raise AssertionError(
            f"{scenario.name}: overall-health permission mismatch"
        )


def _cleanup(
    *,
    check_item_id: str,
    version_id: str,
    seeded: list[dict[str, Any]],
) -> dict[str, int]:
    batch_ids = [str(item["batch_id"]) for item in seeded]
    run_ids = [str(item["run_id"]) for item in seeded]
    workflow_ids = [
        workflow_id
        for item in seeded
        for workflow_id in item["workflow_ids"]
    ]
    object_count = sum(
        int(
            get_minio_storage().delete_objects_by_prefix(
                f"reports/patrol/{batch_id}"
            )
        )
        for batch_id in batch_ids
    )
    counts: dict[str, int] = {"objects": object_count}
    with get_db_session() as db:
        counts["audits"] = int(
            db.query(AuditLogRecord)
            .filter(AuditLogRecord.resource_id.in_(batch_ids))
            .delete(synchronize_session=False)
        )
        counts["projections"] = int(
            db.query(PatrolBatchSummaryProjection)
            .filter(
                PatrolBatchSummaryProjection.patrol_batch_id.in_(
                    batch_ids
                )
            )
            .delete(synchronize_session=False)
        )
        counts["report_requests"] = int(
            db.query(PatrolReportMaterializationRequestRecord)
            .filter(
                PatrolReportMaterializationRequestRecord.patrol_batch_id.in_(
                    batch_ids
                )
            )
            .delete(synchronize_session=False)
        )
        counts["report_manifests"] = int(
            db.query(ReportManifestRecord)
            .filter(ReportManifestRecord.patrol_batch_id.in_(batch_ids))
            .delete(synchronize_session=False)
        )
        counts["risks"] = int(
            db.query(RiskCaseRecord)
            .filter(RiskCaseRecord.check_item_id == check_item_id)
            .delete(synchronize_session=False)
        )
        counts["executions"] = int(
            db.query(CheckExecutionRecord)
            .filter(CheckExecutionRecord.patrol_batch_id.in_(batch_ids))
            .delete(synchronize_session=False)
        )
        counts["runtime_manifests"] = int(
            db.query(CheckItemRuntimeManifestRecord)
            .filter(
                CheckItemRuntimeManifestRecord.patrol_batch_id.in_(
                    batch_ids
                )
            )
            .delete(synchronize_session=False)
        )
        counts["patrol_runs"] = int(
            db.query(PatrolRun)
            .filter(PatrolRun.id.in_(run_ids))
            .delete(synchronize_session=False)
        )
        counts["batches"] = int(
            db.query(PatrolBatchRecord)
            .filter(PatrolBatchRecord.patrol_batch_id.in_(batch_ids))
            .delete(synchronize_session=False)
        )
        counts["workflow_runs"] = int(
            db.query(WorkflowRun)
            .filter(WorkflowRun.id.in_(workflow_ids))
            .delete(synchronize_session=False)
        )
        counts["check_item_versions"] = int(
            db.query(CheckItemVersionRecord)
            .filter(CheckItemVersionRecord.id == version_id)
            .delete(synchronize_session=False)
        )
        counts["check_items"] = int(
            db.query(CheckItemRecord)
            .filter(CheckItemRecord.id == check_item_id)
            .delete(synchronize_session=False)
        )
    return counts


def run(
    *,
    base_url: str,
    username: str,
    password: str,
    source_file: Path,
    timeout_seconds: int,
    output_path: Path,
) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:10]
    summary: dict[str, Any] = {
        "schema_version": (
            "report-conclusion-permission-full-stack-e2e/v1"
        ),
        "success": False,
        "base_url": base_url,
        "source_file": str(source_file),
        "suffix": suffix,
        "scenarios": {},
    }
    seeded: list[dict[str, Any]] = []
    check_item_id = ""
    version_id = ""
    try:
        evidence = _load_cpu_evidence(source_file)
        check_item_id, version_id = _seed_check_item(
            suffix=suffix
        )
        with httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=90,
        ) as client:
            token = _login(
                client,
                username=username,
                password=password,
            )
            client.headers["Authorization"] = f"Bearer {token}"
            for scenario in SCENARIOS:
                seed = _seed_batch(
                    suffix=suffix,
                    scenario=scenario,
                    check_item_id=check_item_id,
                    version_id=version_id,
                    evidence=evidence,
                )
                seeded.append(seed)
                batch_id = str(seed["batch_id"])
                dispatch = _json_response(
                    client.post(
                        f"/api/patrol/batches/{batch_id}/reports/"
                        "materialize",
                        headers={
                            "Idempotency-Key": (
                                f"conclusion-{scenario.name}-{suffix}"
                            )
                        },
                        json={},
                    ),
                    expected_status=202,
                )
                if (
                    dispatch.get("accepted") is not True
                    and dispatch.get("reason")
                    not in {
                        "already_in_progress",
                        "idempotent_replay",
                    }
                ):
                    raise AssertionError(
                        f"{scenario.name}: dispatch not accepted: "
                        f"{dispatch}"
                    )
                status = _wait_for_report(
                    client,
                    batch_id=batch_id,
                    task_id=(
                        str(dispatch["task_id"])
                        if dispatch.get("task_id")
                        else None
                    ),
                    timeout_seconds=timeout_seconds,
                )
                manifest = _json_response(
                    client.get(
                        f"/api/patrol/batches/{batch_id}/"
                        "report-manifest"
                    )
                )
                if manifest.get("facts_status") != "final":
                    raise AssertionError(
                        f"{scenario.name}: manifest is not final"
                    )
                permission = dict(
                    manifest.get("conclusion_permission") or {}
                )
                _assert_permission(
                    scenario=scenario,
                    permission=permission,
                )
                delivery = _download_and_compare(
                    client,
                    batch_id=batch_id,
                    manifest=manifest,
                    scenario=scenario,
                )
                summary["scenarios"][scenario.name] = {
                    "batch_id": batch_id,
                    "device_count": seed["device_count"],
                    "dispatch": dispatch,
                    "materialization_status": status,
                    "manifest": {
                        "id": manifest["report_manifest_id"],
                        "facts_status": manifest["facts_status"],
                        "report_purpose": manifest[
                            "report_purpose"
                        ],
                        "source_digest": manifest[
                            "source_facts_digest"
                        ],
                    },
                    **delivery,
                }
        summary["success"] = True
    except Exception as exc:
        summary["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        raise
    finally:
        if check_item_id and version_id:
            summary["cleanup"] = _cleanup(
                check_item_id=check_item_id,
                version_id=version_id,
                seeded=seeded,
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                summary,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify four report-conclusion boundaries through "
            "BFF, Celery, PostgreSQL and MinIO"
        )
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv(
            "NETOPS_BFF_BASE_URL",
            "http://localhost:8001",
        ),
    )
    parser.add_argument(
        "--username",
        default=os.getenv("NETOPS_E2E_USERNAME", "admin"),
    )
    parser.add_argument(
        "--password",
        default=os.getenv("NETOPS_E2E_PASSWORD", ""),
    )
    parser.add_argument(
        "--source-file",
        type=Path,
        default=(
            ROOT
            / "lixian"
            / "Q3"
            / "LAB-DEVICE-RT5620-1-198.51.2.219.txt"
        ),
    )
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument(
        "--output-path",
        type=Path,
        default=(
            ROOT
            / ".tmp"
            / "report-conclusion-permission-full-stack-e2e"
            / "summary.json"
        ),
    )
    args = parser.parse_args()
    if not args.password:
        raise SystemExit(
            "NETOPS_E2E_PASSWORD or --password is required"
        )
    result = run(
        base_url=args.base_url,
        username=args.username,
        password=args.password,
        source_file=args.source_file.resolve(),
        timeout_seconds=args.timeout_seconds,
        output_path=args.output_path.resolve(),
    )
    print(
        json.dumps(
            {
                "success": result["success"],
                "scenario_count": len(result["scenarios"]),
                "scenarios": {
                    name: {
                        "conclusion": row["conclusion"],
                        "permission": {
                            key: row["conclusion_permission"][key]
                            for key in (
                                "terminal_truth_reconciled",
                                "current_conclusion_ready",
                                "evaluated_scope_no_abnormality_allowed",
                                "overall_healthy_allowed",
                            )
                        },
                    }
                    for name, row in result["scenarios"].items()
                },
                "cleanup": result.get("cleanup"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

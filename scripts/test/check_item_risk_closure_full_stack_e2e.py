#!/usr/bin/env python
"""Verify technical recovery, remediation acceptance, and formal risk closure.

The scenario uses temporary PostgreSQL facts and authenticated BFF requests. It
keeps the fixture isolated from production assets and removes every test record
after writing an auditable summary and deterministic report artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[2]
DJANGO_ROOT = ROOT / "web" / "django_backend"
for import_root in (ROOT, DJANGO_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_backend.settings")

import django  # noqa: E402

django.setup()

from bff.roles import assign_user_role  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402

from src.core.check_items.report_contract import (  # noqa: E402
    build_report_authority_context,
    raw_evidence_set_digest,
)
from src.core.check_items.reporting import (  # noqa: E402
    build_check_item_report_view,
    render_check_item_artifacts,
)
from src.infrastructure.db.models import (  # noqa: E402
    AuditLogRecord,
    CheckAssessmentRecord,
    CheckExecutionRecord,
    CheckItemRecord,
    CheckItemRuntimeBundleRecord,
    CheckItemRuntimeBundleVersionRecord,
    CheckItemVersionRecord,
    CollectionImplementationRecord,
    CollectionImplementationVersionRecord,
    CoverageDecisionRecord,
    EntityIdentityPolicyVersionRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
    EvidenceQualificationRecord,
    NetworkDevice,
    RiskCaseRecord,
    RiskOccurrenceRecord,
    RiskRemediationRecord,
    RuntimeBundleAssetRecord,
)
from src.infrastructure.db.postgres import get_db_session  # noqa: E402


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


def _login(client: httpx.Client, *, username: str, password: str) -> str:
    response = client.post(
        "/api/auth/login/",
        json={"username": username, "password": password},
    )
    if response.status_code >= 400:
        raise AssertionError(
            f"BFF login returned {response.status_code}: {response.text}"
        )
    payload = response.json()
    token = str((payload.get("data") or {}).get("access") or "")
    if not token:
        raise AssertionError("BFF login did not return an access token")
    return token


def _request_json(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    token: str,
    json_body: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    expected_statuses: tuple[int, ...] = (200, 201, 202),
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    response = client.request(
        method,
        path,
        json=json_body,
        headers=headers,
    )
    if response.status_code not in expected_statuses:
        raise AssertionError(
            f"{method} {path} returned {response.status_code}: {response.text}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise AssertionError(f"{method} {path} returned a non-object payload")
    data = payload.get("data")
    return dict(data) if isinstance(data, dict) else payload


def _expect_conflict(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    token: str,
    json_body: dict[str, Any],
    idempotency_key: str,
    expected_text: str,
) -> dict[str, Any]:
    response = client.request(
        method,
        path,
        json=json_body,
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": idempotency_key,
        },
    )
    if response.status_code != 409:
        raise AssertionError(
            f"{method} {path} returned {response.status_code}, expected 409: "
            f"{response.text}"
        )
    if expected_text.casefold() not in response.text.casefold():
        raise AssertionError(
            f"{method} {path} did not explain {expected_text!r}: "
            f"{response.text}"
        )
    return response.json()


def _create_user(username: str, password: str, role: str) -> None:
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username=username,
        email=f"{username}@example.invalid",
        password=password,
    )
    assign_user_role(user, role)
    user.save()


def _delete_users(usernames: list[str]) -> None:
    get_user_model().objects.filter(username__in=usernames).delete()


def _active_asset_snapshot() -> dict[str, list[tuple[str, str]]]:
    with get_db_session() as db:
        return {
            "runtime_bundles": sorted(
                (str(row.id), str(row.digest))
                for row in db.query(CheckItemRuntimeBundleVersionRecord)
                .filter(CheckItemRuntimeBundleVersionRecord.status == "active")
                .all()
            ),
            "event_rules": sorted(
                (str(row.id), str(row.digest))
                for row in db.query(EventRuleVersionRecord)
                .filter(EventRuleVersionRecord.status == "active")
                .all()
            ),
        }


def _fixture_ids(suffix: str) -> dict[str, str]:
    return {
        "device": f"risk-e2e-device-{suffix}",
        "implementation": f"risk-e2e-impl-{suffix}",
        "implementation_version": f"risk-e2e-impl-v-{suffix}",
        "bundle": f"risk-e2e-bundle-{suffix}",
        "bundle_version": f"risk-e2e-bundle-v-{suffix}",
        "rule": f"risk-e2e-rule-{suffix}",
        "rule_version": f"risk-e2e-rule-v-{suffix}",
        "asset": f"risk-e2e-asset-{suffix}",
        "execution": f"risk-e2e-exec-{suffix}",
        "qualification": f"risk-e2e-qual-{suffix}",
        "coverage": f"risk-e2e-cover-{suffix}",
        "assessment": f"risk-e2e-assess-{suffix}",
        "risk": f"risk-e2e-case-{suffix}",
        "episode": f"risk-e2e-episode-{suffix}",
        "batch": f"risk-e2e-batch-{suffix}",
    }


def _seed_fixture(ids: dict[str, str], *, now: datetime) -> dict[str, str]:
    with get_db_session() as db:
        check_item = db.get(CheckItemRecord, "system.cpu_usage")
        check_version = (
            db.query(CheckItemVersionRecord)
            .filter(
                CheckItemVersionRecord.check_item_id == "system.cpu_usage",
                CheckItemVersionRecord.status == "active",
            )
            .one()
        )
        identity_policy = (
            db.query(EntityIdentityPolicyVersionRecord)
            .filter(EntityIdentityPolicyVersionRecord.status == "active")
            .first()
        )
        if check_item is None or identity_policy is None:
            raise AssertionError(
                "active system.cpu_usage catalog and identity policy are required"
            )

        implementation_digest = _digest(
            {"fixture": ids["implementation"], "command": "display cpu-usage"}
        )
        bundle_digest = _digest(
            {"fixture": ids["bundle"], "check_item": check_version.id}
        )
        rule_digest = _digest(
            {"fixture": ids["rule"], "event_type": "CpuUtilizationHigh"}
        )
        qualification_digest = _digest(
            {"fixture": ids["qualification"], "execution": ids["execution"]}
        )
        coverage_digest = _digest(
            {"fixture": ids["coverage"], "execution": ids["execution"]}
        )

        db.add(
            NetworkDevice(
                id=ids["device"],
                device_name=f"Risk-E2E-{ids['device'][-6:]}",
                management_ip=f"198.51.100.{int(ids['device'][-2:], 16) % 200 + 1}",
                vendor="H3C",
                model="E2E",
                status="active",
            )
        )
        db.add(
            CollectionImplementationRecord(
                id=ids["implementation"],
                name="Risk closure E2E CPU implementation",
                status="active",
                current_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            CollectionImplementationVersionRecord(
                id=ids["implementation_version"],
                implementation_id=ids["implementation"],
                version=1,
                collector="cli",
                match_json={"vendor": "H3C", "model": "E2E"},
                commands_json=[{"command": "display cpu-usage"}],
                parser_requirement="structured",
                template_scope="vendor_model_command",
                output_quality_policy_json={"empty": "blocked"},
                selection_priority=1,
                status="active",
                digest=implementation_digest,
                created_by="risk-e2e",
                created_at=now,
                activated_at=now,
            )
        )
        db.add(
            EventRuleRecord(
                id=ids["rule"],
                name=f"risk.e2e.cpu.high.{ids['rule'][-8:]}",
                description="Temporary current-state rule for recovery validation",
                status="active",
                current_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            EventRuleVersionRecord(
                id=ids["rule_version"],
                rule_id=ids["rule"],
                version=1,
                event_type="CpuUtilizationHigh",
                severity="major",
                condition_json={
                    "kind": "threshold",
                    "observation_types": ["device.cpu.utilization"],
                    "entity_types": ["cpu_scope"],
                    "fields": ["cpu_usage"],
                    "gte": 80,
                },
                priority=100,
                exclusive=True,
                event_class="fault",
                evaluation_scope="current_state",
                recovery_json={},
                correlation_key_json=[
                    "device_id",
                    "entity_type",
                    "entity_key",
                ],
                status="active",
                validation_report_json={"passed": True, "fixture": True},
                digest=rule_digest,
                created_by="risk-e2e",
                created_at=now,
                activated_at=now,
            )
        )
        db.flush()
        db.add(
            CheckItemRuntimeBundleRecord(
                id=ids["bundle"],
                check_item_id=check_item.id,
                implementation_id=ids["implementation"],
                name="Risk closure E2E RuntimeBundle",
                status="active",
                current_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()
        db.add(
            CheckItemRuntimeBundleVersionRecord(
                id=ids["bundle_version"],
                bundle_id=ids["bundle"],
                version=1,
                check_item_version_id=check_version.id,
                implementation_version_id=ids["implementation_version"],
                parser_bundle_json={"commands": ["display cpu-usage"]},
                canonical_catalog_version=3,
                canonical_catalog_digest=_digest({"catalog": "fixture"}),
                entity_identity_policy_version_id=identity_policy.id,
                dependency_digests_json={
                    "current_state_rule": rule_digest,
                },
                compatibility_json={"fixture_scope": ids["device"]},
                validation_report_json={"passed": True, "fixture": True},
                digest=bundle_digest,
                status="active",
                created_by="risk-e2e",
                created_at=now,
                activated_at=now,
            )
        )
        db.flush()
        db.add(
            RuntimeBundleAssetRecord(
                id=ids["asset"],
                bundle_version_id=ids["bundle_version"],
                asset_type="current_state_rule",
                asset_id=ids["rule"],
                asset_version_id=ids["rule_version"],
                asset_digest=rule_digest,
                role="primary",
                observation_type="device.cpu.utilization",
                required=True,
                metadata_json={"fixture": True},
                created_at=now,
            )
        )
        db.flush()

        db.add(
            CheckExecutionRecord(
                id=ids["execution"],
                idempotency_key=f"{ids['batch']}|{ids['device']}|cpu",
                patrol_batch_id=ids["batch"],
                device_id=ids["device"],
                check_item_version_id=check_version.id,
                implementation_version_id=ids["implementation_version"],
                collection_action_ids_json=[f"action:{ids['execution']}"],
                runtime_bundle_id=ids["bundle"],
                runtime_bundle_version_id=ids["bundle_version"],
                runtime_bundle_digest=bundle_digest,
                execution_status="completed",
                reason_codes_json=[],
                observation_count=1,
                observed_entity_count=1,
                change_count=0,
                event_count=0,
                current_finding_count=0,
                assessment_id=ids["assessment"],
                evidence_qualification_id=ids["qualification"],
                coverage_decision_id=ids["coverage"],
                source_identity_status="confirmed",
                output_quality_status="complete",
                freshness_status="fresh",
                empty_result_classification="non_empty",
                coverage_status="partial",
                coverage_basis="fixed",
                risk_verification_status="recovery_pending",
                expected_entity_count=1,
                target_manifest_digest=_digest({"target": ids["device"]}),
                asset_manifest_digest=bundle_digest,
                evidence_refs_json=[f"snapshot:{ids['execution']}"],
                completed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()
        db.add(
            EvidenceQualificationRecord(
                id=ids["qualification"],
                digest=qualification_digest,
                check_execution_id=ids["execution"],
                patrol_batch_id=ids["batch"],
                device_id=ids["device"],
                check_item_version_id=check_version.id,
                implementation_version_id=ids["implementation_version"],
                runtime_bundle_version_id=ids["bundle_version"],
                runtime_bundle_digest=bundle_digest,
                source_identity_status="confirmed",
                output_quality_status="complete",
                freshness_status="fresh",
                empty_result_classification="non_empty",
                snapshot_ids_json=[f"snapshot:{ids['execution']}"],
                qualification_reason_codes_json=[],
                policy_snapshot_json={"schema_version": 1},
                policy_digest=_digest({"qualification_policy": 1}),
                evidence_refs_json=[f"snapshot:{ids['execution']}"],
                qualified_at=now,
                created_at=now,
            )
        )
        db.add(
            CoverageDecisionRecord(
                id=ids["coverage"],
                digest=coverage_digest,
                check_execution_id=ids["execution"],
                patrol_batch_id=ids["batch"],
                device_id=ids["device"],
                check_item_version_id=check_version.id,
                coverage_status="partial",
                coverage_basis="fixed",
                expected_entity_count=1,
                observed_entity_count=1,
                evaluated_entity_count=1,
                expected_entity_set_digest=_digest({"entity": "device:0"}),
                observed_entity_set_digest=_digest({"entity": "device:0"}),
                denominator_ref_json={"basis": "fixed"},
                missing_entity_sample_json=[],
                unexpected_entity_sample_json=[],
                reason_codes_json=["coverage_partial"],
                policy_snapshot_json={"model": "fixed"},
                policy_digest=_digest({"coverage_policy": 1}),
                evidence_refs_json=[f"snapshot:{ids['execution']}"],
                decided_at=now,
                created_at=now,
            )
        )
        db.flush()
        db.add(
            CheckAssessmentRecord(
                id=ids["assessment"],
                idempotency_key=f"assessment:{ids['assessment']}",
                patrol_batch_id=ids["batch"],
                device_id=ids["device"],
                check_item_version_id=check_version.id,
                execution_id=ids["execution"],
                assessment_status="healthy",
                completeness_status="complete",
                comparison_status="comparable",
                expected_entity_count=1,
                observed_entity_count=1,
                evaluated_entity_count=1,
                threshold_unknown_count=0,
                current_finding_ids_json=[],
                risk_case_ids_json=[ids["risk"]],
                evidence_qualification_id=ids["qualification"],
                coverage_decision_id=ids["coverage"],
                source_identity_status="confirmed",
                output_quality_status="complete",
                freshness_status="fresh",
                empty_result_classification="non_empty",
                coverage_status="partial",
                coverage_basis="fixed",
                risk_verification_status="recovery_pending",
                blocking_reason_codes_json=[],
                assessment_rule_bundle_digest=bundle_digest,
                evidence_digest=_digest({"assessment": ids["assessment"]}),
                assessed_at=now,
                created_at=now,
            )
        )
        db.flush()
        db.add(
            RiskCaseRecord(
                id=ids["risk"],
                episode_id=ids["episode"],
                correlation_key=f"risk-e2e:{ids['risk']}",
                check_item_id=check_item.id,
                device_id=ids["device"],
                entity_type="cpu_scope",
                entity_key='["device","0"]',
                correlation_dimensions_json={"window": "instant"},
                risk_type="CpuUtilizationHigh",
                severity="major",
                status="recovering",
                first_seen_at=now - timedelta(days=1),
                last_seen_at=now - timedelta(days=1),
                last_successful_evaluation_at=now,
                verification_status="recovery_pending",
                last_verified_at=now,
                last_verification_execution_id=ids["execution"],
                source_finding_ids_json=[],
                source_event_ids_json=[],
                remediation_status="not_started",
                created_at=now - timedelta(days=1),
                updated_at=now,
            )
        )
        return {
            "check_item_version_id": str(check_version.id),
            "bundle_digest": bundle_digest,
            "qualification_digest": qualification_digest,
            "coverage_digest": coverage_digest,
        }


def _make_coverage_complete(ids: dict[str, str]) -> None:
    with get_db_session() as db:
        coverage = db.get(CoverageDecisionRecord, ids["coverage"])
        execution = db.get(CheckExecutionRecord, ids["execution"])
        assessment = db.get(CheckAssessmentRecord, ids["assessment"])
        if coverage is None or execution is None or assessment is None:
            raise AssertionError("recovery fixture disappeared before replay")
        coverage.coverage_status = "complete"
        coverage.reason_codes_json = []
        execution.coverage_status = "complete"
        assessment.coverage_status = "complete"


def _report_manifest(
    ids: dict[str, str],
    fixture: dict[str, str],
    *,
    generated_at: datetime,
) -> dict[str, Any]:
    with get_db_session() as db:
        device = db.get(NetworkDevice, ids["device"])
        execution = db.get(CheckExecutionRecord, ids["execution"])
        assessment = db.get(CheckAssessmentRecord, ids["assessment"])
        qualification = db.get(
            EvidenceQualificationRecord,
            ids["qualification"],
        )
        coverage = db.get(CoverageDecisionRecord, ids["coverage"])
        risk = db.get(RiskCaseRecord, ids["risk"])
        remediations = (
            db.query(RiskRemediationRecord)
            .filter(RiskRemediationRecord.risk_case_id == ids["risk"])
            .all()
        )
        occurrences = (
            db.query(RiskOccurrenceRecord)
            .filter(RiskOccurrenceRecord.risk_case_id == ids["risk"])
            .order_by(RiskOccurrenceRecord.observed_at)
            .all()
        )
        if any(
            item is None
            for item in (
                device,
                execution,
                assessment,
                qualification,
                coverage,
                risk,
            )
        ):
            raise AssertionError("report facts are incomplete")
        risk_row = risk.to_dict()
        risk_row["closure_status"] = "closed"
        evidence_index = [
            {
                "snapshot_id": f"snapshot:{ids['execution']}",
                "run_id": f"run:{ids['execution']}",
                "device_id": ids["device"],
                "device_name": device.device_name,
                "management_ip": device.management_ip,
                "vendor": device.vendor,
                "model": device.model,
                "observed_at": generated_at.isoformat(),
                "canonical_command": "display cpu-usage",
                "raw_text_hash": _digest({"raw": ids["execution"]}),
                "parse_status": "parsed",
                "output_status": "success",
                "parser_template_id": "fixture-parser",
                "parser_version": "1",
                "contract_version": 1,
                "collection_action_id": f"action:{ids['execution']}",
            }
        ]
        facts = {
            "schema_version": "patrol-report-source/v3",
            "patrol_batch_id": ids["batch"],
            "report_purpose": "shadow_validation",
            "report_authority_context": build_report_authority_context(
                report_purpose="shadow_validation",
                patrol_mode="offline",
                launch_mode="workflow",
                workflow_name="risk-closure-e2e",
                target_type="offline_import",
                readiness_ready=True,
                target_manifest_digest=execution.target_manifest_digest,
                inventory_snapshot_digest=_digest({"inventory": ids["device"]}),
                runtime_manifest_digest=fixture["bundle_digest"],
            ),
            "readiness": {
                "ready_for_final": True,
                "reason_codes": [],
                "target_count": 1,
                "target_record_count": 1,
                "execution_count": 1,
                "expected_execution_count": 1,
                "assessment_count": 1,
            },
            "target_manifest_digest": execution.target_manifest_digest,
            "inventory_snapshot_digest": _digest(
                {"inventory": ids["device"]}
            ),
            "runtime_manifest_digest": fixture["bundle_digest"],
            "target_facts": [
                {
                    "device_id": ids["device"],
                    "device_name": device.device_name,
                    "management_ip": device.management_ip,
                    "selection_status": "launched",
                    "preflight_status": "ready",
                    "dispatch_status": "completed",
                }
            ],
            "comparison_contexts": [],
            "check_item_by_version": {
                fixture["check_item_version_id"]: "system.cpu_usage"
            },
            "check_items": {
                fixture["check_item_version_id"]: {
                    "check_item_id": "system.cpu_usage",
                    "version": 1,
                    "name": "CPU 使用率",
                    "category": "system",
                    "description": "CPU health",
                    "result_mode": "structured",
                    "assessment_mode": "health",
                    "canonical_contract": {
                        "observations": [
                            {
                                "observation_type": (
                                    "device.cpu.utilization"
                                )
                            }
                        ]
                    },
                    "applicability": {"version": 1, "conditions": []},
                    "report_projection": "cpu",
                    "digest": _digest({"check_item": "system.cpu_usage"}),
                }
            },
            "executions": [execution.to_dict()],
            "assessments": [assessment.to_dict()],
            "evidence_qualifications": [qualification.to_dict()],
            "coverage_decisions": [coverage.to_dict()],
            "observations": [],
            "findings": [],
            "changes": [],
            "events": [],
            "risks": [risk_row],
            "risk_occurrences": [item.to_dict() for item in occurrences],
            "risk_remediations": [item.to_dict() for item in remediations],
            "evidence_index": evidence_index,
            "evidence_refs": [
                f"risk_case:{ids['risk']}",
                f"check_execution:{ids['execution']}",
            ],
        }
        facts["raw_evidence_set_digest"] = raw_evidence_set_digest(
            evidence_index
        )
        return {
            "id": f"report:{ids['batch']}",
            "patrol_batch_id": ids["batch"],
            "version": 1,
            "schema_version": "patrol-report-manifest/v3",
            "status": "final",
            "report_purpose": "shadow_validation",
            "raw_evidence_set_digest": facts["raw_evidence_set_digest"],
            "target_manifest_digest": execution.target_manifest_digest,
            "runtime_manifest_digest": fixture["bundle_digest"],
            "generator_version": "check-item-report/v3",
            "generated_at": generated_at,
            "report_cutoff_at": generated_at,
            "qualification_fact_digest": fixture["qualification_digest"],
            "coverage_fact_digest": fixture["coverage_digest"],
            "source_facts_json": facts,
            "source_digest": _digest(facts),
        }


def _cleanup(ids: dict[str, str]) -> None:
    with get_db_session() as db:
        db.query(AuditLogRecord).filter(
            AuditLogRecord.resource_id == ids["risk"]
        ).delete(synchronize_session=False)
        db.query(RiskOccurrenceRecord).filter(
            RiskOccurrenceRecord.risk_case_id == ids["risk"]
        ).delete(synchronize_session=False)
        db.query(RiskRemediationRecord).filter(
            RiskRemediationRecord.risk_case_id == ids["risk"]
        ).delete(synchronize_session=False)
        db.query(RiskCaseRecord).filter(
            RiskCaseRecord.id == ids["risk"]
        ).delete(synchronize_session=False)
        db.query(CheckAssessmentRecord).filter(
            CheckAssessmentRecord.id == ids["assessment"]
        ).delete(synchronize_session=False)
        db.query(CoverageDecisionRecord).filter(
            CoverageDecisionRecord.id == ids["coverage"]
        ).delete(synchronize_session=False)
        db.query(EvidenceQualificationRecord).filter(
            EvidenceQualificationRecord.id == ids["qualification"]
        ).delete(synchronize_session=False)
        db.query(CheckExecutionRecord).filter(
            CheckExecutionRecord.id == ids["execution"]
        ).delete(synchronize_session=False)
        db.query(RuntimeBundleAssetRecord).filter(
            RuntimeBundleAssetRecord.id == ids["asset"]
        ).delete(synchronize_session=False)
        db.query(CheckItemRuntimeBundleVersionRecord).filter(
            CheckItemRuntimeBundleVersionRecord.id == ids["bundle_version"]
        ).delete(synchronize_session=False)
        db.query(CheckItemRuntimeBundleRecord).filter(
            CheckItemRuntimeBundleRecord.id == ids["bundle"]
        ).delete(synchronize_session=False)
        db.query(EventRuleVersionRecord).filter(
            EventRuleVersionRecord.id == ids["rule_version"]
        ).delete(synchronize_session=False)
        db.query(EventRuleRecord).filter(
            EventRuleRecord.id == ids["rule"]
        ).delete(synchronize_session=False)
        db.query(CollectionImplementationVersionRecord).filter(
            CollectionImplementationVersionRecord.id
            == ids["implementation_version"]
        ).delete(synchronize_session=False)
        db.query(CollectionImplementationRecord).filter(
            CollectionImplementationRecord.id == ids["implementation"]
        ).delete(synchronize_session=False)
        db.query(NetworkDevice).filter(
            NetworkDevice.id == ids["device"]
        ).delete(synchronize_session=False)


def run(*, base_url: str, output_dir: Path) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:10]
    ids = _fixture_ids(suffix)
    usernames = [
        f"risk-owner-{suffix}",
        f"risk-recovery-{suffix}",
        f"risk-reviewer-{suffix}",
        f"risk-manager-{suffix}",
    ]
    password = f"RiskE2E-{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)
    before_assets = _active_asset_snapshot()
    summary: dict[str, Any] = {
        "schema_version": "check-item-risk-closure-full-stack-e2e/v1",
        "success": False,
        "base_url": base_url,
        "fixture_scope": suffix,
        "production_network_claim": False,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        for username, role in zip(
            usernames,
            ("operator", "operator", "operator", "admin"),
            strict=True,
        ):
            _create_user(username, password, role)
        fixture = _seed_fixture(ids, now=now)

        with httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=30,
        ) as client:
            owner_token = _login(
                client,
                username=usernames[0],
                password=password,
            )
            recovery_token = _login(
                client,
                username=usernames[1],
                password=password,
            )
            reviewer_token = _login(
                client,
                username=usernames[2],
                password=password,
            )
            manager_token = _login(
                client,
                username=usernames[3],
                password=password,
            )

            remediation_created = _request_json(
                client,
                "POST",
                f"/api/patrol/risk-cases/{ids['risk']}/remediation",
                token=owner_token,
                json_body={
                    "action_text": "核查高 CPU 进程并完成优化与复测",
                    "owner": usernames[0],
                    "due_at": (now + timedelta(days=2)).isoformat(),
                },
                idempotency_key=f"create-remediation-{suffix}",
            )
            remediation_id = str(
                (remediation_created.get("remediation") or {}).get("id")
                or ""
            )
            if not remediation_id:
                raise AssertionError("remediation creation returned no id")
            _request_json(
                client,
                "PATCH",
                (
                    f"/api/patrol/risk-cases/{ids['risk']}/remediations/"
                    f"{remediation_id}"
                ),
                token=owner_token,
                json_body={
                    "status": "in_progress",
                    "reason": "维护窗口已开始",
                    "evidence_refs": [],
                },
                idempotency_key=f"start-remediation-{suffix}",
            )
            _request_json(
                client,
                "PATCH",
                (
                    f"/api/patrol/risk-cases/{ids['risk']}/remediations/"
                    f"{remediation_id}"
                ),
                token=owner_token,
                json_body={
                    "status": "completed",
                    "reason": "优化完成并附带复测证据",
                    "evidence_refs": [
                        f"ticket:CHG-{suffix}",
                        f"snapshot:cpu-after-{suffix}",
                    ],
                },
                idempotency_key=f"complete-remediation-{suffix}",
            )

            blocked_detail = _request_json(
                client,
                "GET",
                f"/api/patrol/risk-cases/{ids['risk']}",
                token=recovery_token,
            )
            blocked = dict(
                blocked_detail.get("recovery_confirmation") or {}
            )
            if blocked.get("state") != "blocked":
                raise AssertionError(
                    "partial coverage did not block recovery: "
                    f"confirmation={blocked}, detail={blocked_detail}"
                )
            if "coverage_not_complete" not in set(
                blocked.get("blocker_codes") or []
            ):
                raise AssertionError(
                    "recovery blocker did not identify incomplete coverage"
                )

            _make_coverage_complete(ids)
            ready_detail = _request_json(
                client,
                "GET",
                f"/api/patrol/risk-cases/{ids['risk']}",
                token=recovery_token,
            )
            candidates = list(
                ready_detail.get("recovery_candidates") or []
            )
            candidate = next(
                (
                    item
                    for item in candidates
                    if item.get("eligible")
                    and item.get("kind") == "healthy_assessment"
                ),
                None,
            )
            if candidate is None:
                raise AssertionError(
                    f"no eligible recovery candidate after replay: {candidates}"
                )

            recovery_key = f"confirm-recovery-{suffix}"
            recovery = _request_json(
                client,
                "POST",
                f"/api/patrol/risk-cases/{ids['risk']}/confirm-recovery",
                token=recovery_token,
                json_body={
                    "recovery_candidate_id": candidate["id"],
                    "reason": (
                        "已复核健康评估、完整覆盖和冻结规则，确认技术恢复"
                    ),
                },
                idempotency_key=recovery_key,
            )
            recovery_replay = _request_json(
                client,
                "POST",
                f"/api/patrol/risk-cases/{ids['risk']}/confirm-recovery",
                token=recovery_token,
                json_body={
                    "recovery_candidate_id": candidate["id"],
                    "reason": "技术恢复幂等重放",
                },
                idempotency_key=recovery_key,
            )
            if not bool(
                (recovery_replay.get("confirmation") or {}).get(
                    "idempotent_replay"
                )
            ):
                raise AssertionError(
                    "technical recovery replay was not idempotent"
                )

            self_accept = _expect_conflict(
                client,
                "POST",
                (
                    f"/api/patrol/risk-cases/{ids['risk']}/remediations/"
                    f"{remediation_id}/accept"
                ),
                token=owner_token,
                json_body={
                    "reason": "执行人尝试自行验收",
                    "evidence_refs": [f"acceptance:self-{suffix}"],
                },
                idempotency_key=f"self-accept-{suffix}",
                expected_text="cannot accept their own",
            )
            accepted = _request_json(
                client,
                "POST",
                (
                    f"/api/patrol/risk-cases/{ids['risk']}/remediations/"
                    f"{remediation_id}/accept"
                ),
                token=reviewer_token,
                json_body={
                    "reason": "独立复核变更单和复测输出，验收通过",
                    "evidence_refs": [f"acceptance:review-{suffix}"],
                },
                idempotency_key=f"accept-remediation-{suffix}",
            )
            if (
                accepted.get("remediation") or {}
            ).get("status") != "accepted":
                raise AssertionError("independent remediation acceptance failed")

            invalid_close = _expect_conflict(
                client,
                "POST",
                f"/api/patrol/risk-cases/{ids['risk']}/close",
                token=manager_token,
                json_body={
                    "disposition": "no_remediation_required",
                    "reason": "尝试绕过已经存在的整改记录",
                    "evidence_refs": [f"closure:invalid-{suffix}"],
                },
                idempotency_key=f"invalid-close-{suffix}",
                expected_text="no-remediation closure is invalid",
            )
            close_key = f"close-risk-{suffix}"
            closed = _request_json(
                client,
                "POST",
                f"/api/patrol/risk-cases/{ids['risk']}/close",
                token=manager_token,
                json_body={
                    "disposition": "remediation_accepted",
                    "reason": "技术恢复和整改验收证据均已复核",
                    "evidence_refs": [
                        f"acceptance:review-{suffix}",
                        f"closure:approval-{suffix}",
                    ],
                },
                idempotency_key=close_key,
            )
            close_replay = _request_json(
                client,
                "POST",
                f"/api/patrol/risk-cases/{ids['risk']}/close",
                token=manager_token,
                json_body={
                    "disposition": "remediation_accepted",
                    "reason": "正式关闭幂等重放",
                    "evidence_refs": [f"closure:approval-{suffix}"],
                },
                idempotency_key=close_key,
            )
            final_detail = _request_json(
                client,
                "GET",
                f"/api/patrol/risk-cases/{ids['risk']}",
                token=manager_token,
            )

        closure = dict(final_detail.get("closure") or {})
        if closure.get("state") != "closed":
            raise AssertionError(f"risk was not formally closed: {closure}")
        if closure.get("closed_by") != usernames[3]:
            raise AssertionError("formal closure actor was not preserved")
        if (
            closed.get("risk_case") or {}
        ).get("closed_at") != (
            close_replay.get("risk_case") or {}
        ).get("closed_at"):
            raise AssertionError(
                "formal closure replay changed closed_at: "
                f"first={(closed.get('risk_case') or {}).get('closed_at')}, "
                "replay="
                f"{(close_replay.get('risk_case') or {}).get('closed_at')}"
            )

        with get_db_session() as db:
            resolved_occurrences = (
                db.query(RiskOccurrenceRecord)
                .filter(
                    RiskOccurrenceRecord.risk_case_id == ids["risk"],
                    RiskOccurrenceRecord.occurrence_kind == "resolved",
                )
                .all()
            )
            closed_occurrences = (
                db.query(RiskOccurrenceRecord)
                .filter(
                    RiskOccurrenceRecord.risk_case_id == ids["risk"],
                    RiskOccurrenceRecord.occurrence_kind == "closed",
                )
                .all()
            )
            resolved_count = len(resolved_occurrences)
            closed_count = len(closed_occurrences)
            occurrence_episode_ids = [
                str(item.risk_episode_id or "")
                for item in [*resolved_occurrences, *closed_occurrences]
            ]
            audits = (
                db.query(AuditLogRecord)
                .filter(AuditLogRecord.resource_id == ids["risk"])
                .all()
            )
            audit_actions = sorted({str(item.action) for item in audits})
        if resolved_count != 1 or closed_count != 1:
            raise AssertionError(
                "idempotent lifecycle did not keep one resolved and one "
                f"closed occurrence: resolved={resolved_count}, "
                f"closed={closed_count}"
            )
        episode_id = str(final_detail.get("episode_id") or "")
        if not episode_id or any(
            item != episode_id for item in occurrence_episode_ids
        ):
            raise AssertionError(
                "risk lifecycle occurrences were not bound to the current episode"
            )
        required_audits = {
            "risk_recovery_confirm",
            "risk_remediation_accept",
            "risk_close",
        }
        if not required_audits.issubset(set(audit_actions)):
            raise AssertionError(
                f"risk lifecycle audit is incomplete: {audit_actions}"
            )

        manifest = _report_manifest(ids, fixture, generated_at=now)
        report = build_check_item_report_view(manifest)
        if report["risk_verification_counts"]["closed"] != 1:
            raise AssertionError("report did not project the formal closure")
        if report["risk_verification_counts"]["technical_recovered"] != 0:
            raise AssertionError(
                "formally closed risk was also counted as pending closure"
            )
        artifacts = render_check_item_artifacts(manifest)
        markdown_path = output_dir / "risk-closure-report.md"
        workbook_path = output_dir / "risk-closure-technical-workbook.xlsx"
        markdown_path.write_bytes(artifacts["markdown"].content)
        workbook_path.write_bytes(artifacts["excel"].content)
        markdown_text = artifacts["markdown"].content.decode("utf-8")
        if "风险已正式关闭" not in markdown_text:
            raise AssertionError(
                "Markdown report omitted formal closure status"
            )
        workbook = load_workbook(workbook_path, read_only=True)
        if report["current_state_authoritative"]:
            raise AssertionError(
                "shadow validation report must not claim current network state"
            )
        if workbook.sheetnames != [
            "总览",
            "检查项结论",
            "风险结果",
            "历史风险待复核",
            "恢复待确认",
            "技术恢复与关闭",
            "整改跟踪",
            "事件",
            "变化",
            "当前观测",
            "证据索引",
        ]:
            raise AssertionError(
                f"unexpected report workbook sheets: {workbook.sheetnames}"
            )
        closure_sheet = workbook["技术恢复与关闭"]
        closure_values = {
            str(closure_sheet.cell(row, 9).value or "")
            for row in range(2, closure_sheet.max_row + 1)
        }
        if "风险已正式关闭" not in closure_values:
            raise AssertionError(
                f"Excel report omitted formal closure: {closure_values}"
            )
        workbook.close()

        summary.update(
            {
                "success": True,
                "risk_case_id": ids["risk"],
                "blocked_recovery_reason_codes": blocked.get(
                    "blocker_codes"
                ),
                "recovery_candidate_kind": candidate["kind"],
                "technical_recovery_actor": usernames[1],
                "remediation_completed_by": usernames[0],
                "remediation_accepted_by": usernames[2],
                "risk_closed_by": usernames[3],
                "self_accept_status": self_accept,
                "invalid_close_status": invalid_close,
                "resolved_occurrence_count": resolved_count,
                "closed_occurrence_count": closed_count,
                "risk_episode_id": episode_id,
                "audit_actions": audit_actions,
                "report_counts": report["risk_verification_counts"],
                "report_artifacts": {
                    "markdown": {
                        "path": str(markdown_path),
                        "sha256": artifacts["markdown"].sha256,
                    },
                    "excel": {
                        "path": str(workbook_path),
                        "sha256": artifacts["excel"].sha256,
                        "sheet_count": 11,
                    },
                },
            }
        )
        return summary
    finally:
        cleanup_error: str | None = None
        try:
            _cleanup(ids)
            _delete_users(usernames)
            after_assets = _active_asset_snapshot()
            if after_assets != before_assets:
                cleanup_error = (
                    "active assets changed after fixture cleanup: "
                    f"{before_assets} -> {after_assets}"
                )
        except Exception as exc:
            cleanup_error = f"{type(exc).__name__}: {exc}"
        summary["cleanup"] = {
            "success": cleanup_error is None,
            "error": cleanup_error,
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        if cleanup_error is not None and summary.get("success"):
            raise AssertionError(cleanup_error)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify risk recovery, remediation acceptance and closure through "
            "the authenticated BFF and PostgreSQL"
        )
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("NETOPS_BFF_BASE_URL", "http://localhost:8001"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=(
            ROOT
            / ".tmp"
            / "check-item-risk-closure-full-stack-e2e"
        ),
    )
    args = parser.parse_args()
    result = run(
        base_url=args.base_url,
        output_dir=args.output_dir.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

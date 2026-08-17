#!/usr/bin/env python
"""Prove the governed rule-asset and RuntimeBundle path through the real stack.

The fixture is deliberately isolated from existing production assets. It creates
two completed offline rule-source periods with structured CPU observations, then
uses the authenticated BFF, Workflow engine, Celery worker and PostgreSQL to:

1. freeze a sanitized generation context;
2. generate and persist Contract/Policy/Event candidates;
3. enforce creator/reviewer/approver separation;
4. activate the rule bundle;
5. assemble and independently activate one CheckItemRuntimeBundle.

All fixture records are deleted by their random scope. Existing active asset
identities and digests must be byte-for-byte unchanged after cleanup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

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

from scripts.test.rule_asset_source_import_full_stack_e2e import _login  # noqa: E402
from src.core.check_items.runtime_bundle import (  # noqa: E402
    CheckItemRuntimeBundleService,
)
from src.core.rule_control.template_activation import (  # noqa: E402
    RuleAssetTemplateActivationService,
)
from src.infrastructure.db.models import (  # noqa: E402
    AuditLogRecord,
    ChangePolicyRecord,
    ChangePolicyVersionRecord,
    CheckItemImplementationLinkRecord,
    CheckItemRuntimeBundleRecord,
    CheckItemRuntimeBundleVersionRecord,
    CheckItemVersionRecord,
    CollectionImplementationParserBindingRecord,
    CollectionImplementationRecord,
    CollectionImplementationVersionRecord,
    CommandSnapshot,
    EventRuleRecord,
    EventRuleVersionRecord,
    FileMetadata,
    NetworkChangeRecord,
    Notification,
    ObservationContractRecord,
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    OfflinePatrolSourceFileRecord,
    ParserTemplate,
    PatrolDataset,
    PatrolRun,
    RuleAgentLoopAttemptRecord,
    RuleApprovalAuditRecord,
    RuleAssetCommandUnitCheckItemRecord,
    RuleAssetCommandUnitRecord,
    RuleGenerationContextRecord,
    RuleGenerationJobRecord,
    RuleReplayReportRecord,
    RuntimeBundleAssetRecord,
    WorkflowRun,
    WorkflowStepRecord,
)
from src.infrastructure.db.postgres import get_db_session  # noqa: E402

OBSERVATION_TYPE = "device.cpu.utilization"
CANONICAL_COMMAND = "display cpu-usage"
WORKFLOW_TERMINAL = {"completed", "failed", "cancelled"}


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


def _request_json(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    expected_statuses: tuple[int, ...] = (200, 201, 202),
) -> dict[str, Any]:
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
    if payload.get("success") is True and isinstance(payload.get("data"), dict):
        return dict(payload["data"])
    return payload


def _expect_rejection(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    json_body: dict[str, Any],
    expected_statuses: tuple[int, ...],
    expected_text: str,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = client.request(
        method,
        path,
        json=json_body,
        headers=headers,
    )
    if response.status_code not in expected_statuses:
        raise AssertionError(
            f"{method} {path} returned {response.status_code}, expected "
            f"{expected_statuses}: {response.text}"
        )
    if expected_text.casefold() not in response.text.casefold():
        raise AssertionError(
            f"{method} {path} did not explain {expected_text!r}: {response.text}"
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {"text": response.text}
    return {
        "status_code": response.status_code,
        "payload": payload,
    }


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
            "parsers": sorted(
                (str(row.id), str(row.sha256))
                for row in db.query(ParserTemplate)
                .filter(ParserTemplate.status == "active")
                .all()
            ),
            "contracts": sorted(
                (str(row.id), str(row.contract_sha256))
                for row in db.query(ObservationContractRecord)
                .filter(ObservationContractRecord.status == "active")
                .all()
            ),
            "change_policies": sorted(
                (str(row.id), str(row.digest))
                for row in db.query(ChangePolicyVersionRecord)
                .filter(ChangePolicyVersionRecord.status == "active")
                .all()
            ),
            "event_rules": sorted(
                (str(row.id), str(row.digest))
                for row in db.query(EventRuleVersionRecord)
                .filter(EventRuleVersionRecord.status == "active")
                .all()
            ),
            "runtime_bundles": sorted(
                (str(row.id), str(row.digest))
                for row in db.query(CheckItemRuntimeBundleVersionRecord)
                .filter(CheckItemRuntimeBundleVersionRecord.status == "active")
                .all()
            ),
        }


def _assert_isolated_cpu_slot() -> None:
    with get_db_session() as db:
        active_policy = (
            db.query(ChangePolicyVersionRecord)
            .filter(
                ChangePolicyVersionRecord.status == "active",
                ChangePolicyVersionRecord.observation_type == OBSERVATION_TYPE,
            )
            .first()
        )
        active_rule = (
            db.query(EventRuleVersionRecord)
            .filter(EventRuleVersionRecord.status == "active")
            .all()
        )
        active_cpu_rules = [
            row
            for row in active_rule
            if OBSERVATION_TYPE
            in list(dict(row.condition_json or {}).get("observation_types") or [])
        ]
        if active_policy is not None or active_cpu_rules:
            raise RuntimeError(
                "full-stack fixture requires an unused CPU rule slot; active CPU "
                "assets already exist and will not be replaced by a test"
            )


def _fixture_ids(suffix: str) -> dict[str, Any]:
    return {
        "suffix": suffix,
        "offline_job_id": f"offline-rule-e2e-{suffix}",
        "dataset_ids": [
            f"rule-e2e-baseline-{suffix}",
            f"rule-e2e-current-{suffix}",
        ],
        "run_ids": [
            f"rule-e2e-run-baseline-{suffix}",
            f"rule-e2e-run-current-{suffix}",
        ],
        "snapshot_ids": [
            f"rule-e2e-snapshot-baseline-{suffix}",
            f"rule-e2e-snapshot-current-{suffix}",
        ],
        "file_ids": [
            f"rule-e2e-file-baseline-{suffix}",
            f"rule-e2e-file-current-{suffix}",
        ],
        "source_file_ids": [
            f"rule-e2e-source-baseline-{suffix}",
            f"rule-e2e-source-current-{suffix}",
        ],
        "period_ids": [
            f"rule-e2e-period-baseline-{suffix}",
            f"rule-e2e-period-current-{suffix}",
        ],
        "parser_template_id": f"rule-e2e-parser-{suffix}",
        "implementation_id": f"e2e-cpu-implementation-{suffix}",
        "implementation_version_id": f"rule-e2e-impl-version-{suffix}",
        "implementation_link_id": f"rule-e2e-impl-link-{suffix}",
        "parser_binding_id": f"rule-e2e-parser-binding-{suffix}",
        "command_unit_id": f"rule-e2e-command-unit-{suffix}",
        "command_unit_link_id": f"rule-e2e-command-unit-link-{suffix}",
        "change_ids": [
            f"rule-e2e-change-fault-{suffix}",
            f"rule-e2e-change-recovery-{suffix}",
        ],
        "model": f"E2E-CPU-{suffix.upper()}",
        "family": f"E2E-Comware-{suffix}",
        "profile": f"E2E-CPU-{suffix}",
    }


def _create_fixture(ids: dict[str, Any], *, owner: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    baseline_at = now - timedelta(days=90)
    current_at = now - timedelta(days=1)
    raw_texts = [
        "PRIVATE E2E RAW CLI baseline: Slot 0 CPU 5m: 42%\n",
        "PRIVATE E2E RAW CLI current: Slot 0 CPU 5m: 91%\n",
    ]
    template_text = (
        "Value slot (\\d+)\n"
        "Value cpu_5m (\\d+)\n"
        "\n"
        "Start\n"
        " ^Slot\\s+${slot}\\s+CPU\\s+5m:\\s+${cpu_5m}%\\s*$$ -> Record\n"
    )
    template_sha = hashlib.sha256(template_text.encode("utf-8")).hexdigest()

    with get_db_session() as db:
        check_version = (
            db.query(CheckItemVersionRecord)
            .filter(
                CheckItemVersionRecord.check_item_id == "system.cpu_usage",
                CheckItemVersionRecord.status == "active",
            )
            .one()
        )
        canonical_contract = dict(check_version.canonical_contract_json or {})
        canonical_observation = next(
            item
            for item in canonical_contract.get("observations") or []
            if item.get("observation_type") == OBSERVATION_TYPE
        )
        existing_h3c_implementation = (
            db.query(CollectionImplementationVersionRecord)
            .filter(
                CollectionImplementationVersionRecord.status == "active",
                CollectionImplementationVersionRecord.parser_requirement
                == "structured",
            )
            .all()
        )
        output_quality_policy = next(
            dict(item.output_quality_policy_json or {})
            for item in existing_h3c_implementation
            if str(dict(item.match_json or {}).get("vendor") or "").casefold()
            == "h3c"
            and any(
                str(command.get("command") or "").strip() == CANONICAL_COMMAND
                for command in item.commands_json or []
                if isinstance(command, dict)
            )
        )

        validation = {
            "entity_type": "cpu_record",
            "primary_keys": ["slot"],
            "required_fields": ["slot", "cpu_5m"],
            "optional_fields": [],
            "validators": {
                "slot": {"type": "text"},
                "cpu_5m": {"type": "number"},
            },
            "allow_empty": False,
            "empty_patterns": [],
            "canonical_entity_type": "cpu_scope",
            "entity_identity_fields": ["scope", "scope_id"],
            "entity_dimensions": {
                "scope": {"literal": "slot"},
                "scope_id": {"field": "slot"},
            },
            "state_semantics": {
                "slot": {"semantic_type": "identity"},
                "cpu_5m": {
                    "semantic_type": "gauge",
                    "unit": "percent",
                    "observation_type": OBSERVATION_TYPE,
                    "dimensions": {"window": "5m"},
                    "comparison_role": "observation",
                    "monitor": True,
                    "policy_constraints": dict(
                        canonical_observation.get("policy_constraints") or {}
                    ),
                    "event_constraints": dict(
                        canonical_observation.get("event_constraints") or {}
                    ),
                },
            },
            "policy_intent": (
                "CPU 5m utilization at or above 80 percent is abnormal; recover "
                "at or below 75 percent with a 5 point deadband."
            ),
            "event_intent": (
                "Generate CpuUtilizationHigh fault and "
                "CpuUtilizationRecovered recovery events."
            ),
            "event_constraints_by_observation_type": {
                OBSERVATION_TYPE: dict(
                    canonical_observation.get("event_constraints") or {}
                )
            },
            "contract_approval_status": "approved",
            "contract_approved_by": owner,
            "sample_count": 2,
            "unique_sample_count": 2,
            "sample_manifest_sha256": _digest(ids["snapshot_ids"]),
            "compatibility": {
                "manifest_version": 2,
                "scope": "model",
                "vendor": "H3C",
                "families": [ids["family"]],
                "models": [ids["model"]],
                "canonical_command": CANONICAL_COMMAND,
                "all_os_versions": True,
                "structure_signatures": ["e2e-cpu-slot-v1"],
            },
        }
        contract_sha = _digest(
            {
                "entity_type": validation["entity_type"],
                "primary_keys": validation["primary_keys"],
                "required_fields": validation["required_fields"],
                "state_semantics": validation["state_semantics"],
                "canonical_entity_type": validation["canonical_entity_type"],
                "entity_dimensions": validation["entity_dimensions"],
            }
        )
        validation["contract_sha256"] = contract_sha

        implementation_payload = {
            "implementation_id": ids["implementation_id"],
            "match": {
                "vendor": "H3C",
                "models": [ids["model"]],
                "family": ids["family"],
            },
            "commands": [{"command": CANONICAL_COMMAND, "priority": 100}],
            "parser_requirement": "structured",
            "template_scope": "model",
            "output_quality_policy": output_quality_policy,
        }

        db.add(
            OfflinePatrolJobRecord(
                id=ids["offline_job_id"],
                idempotency_key=f"rule-e2e-offline-{ids['suffix']}",
                purpose="rule_asset_source",
                job_type="history_compare",
                name=f"Rule asset RuntimeBundle E2E {ids['suffix']}",
                owner_id=owner,
                status="completed",
                vendor="H3C",
                model=ids["model"],
                publish_events=False,
                attempt_number=0,
                progress_json={"percent": 100},
                completed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        db.add_all(
            [
                PatrolDataset(
                    id=dataset_id,
                    name=f"Rule asset E2E {role} {ids['suffix']}",
                    mode="parser_validation",
                    source="e2e_fixture",
                    status="active",
                    metadata_json={
                        "offline_job_id": ids["offline_job_id"],
                        "period_role": role,
                    },
                    imported_at=observed_at,
                )
                for dataset_id, role, observed_at in zip(
                    ids["dataset_ids"],
                    ("baseline", "current"),
                    (baseline_at, current_at),
                    strict=True,
                )
            ]
        )
        db.flush()
        db.add_all(
            [
                PatrolRun(
                    id=run_id,
                    mode="parser_validation",
                    source="e2e_fixture",
                    dataset_id=dataset_id,
                    status="completed",
                    collection_status="completed",
                    parser_readiness_status="ready",
                    fact_readiness_status="ready",
                    analysis_status="completed",
                    baseline_eligible=False,
                    run_purpose="parser_validation",
                    logical_run_id=run_id,
                    attempt_number=0,
                    device_count=1,
                    command_count=1,
                    started_at=observed_at,
                    completed_at=observed_at,
                    metadata_json={
                        "offline_job_id": ids["offline_job_id"],
                        "period_role": role,
                    },
                )
                for run_id, dataset_id, role, observed_at in zip(
                    ids["run_ids"],
                    ids["dataset_ids"],
                    ("baseline", "current"),
                    (baseline_at, current_at),
                    strict=True,
                )
            ]
        )
        db.add_all(
            [
                OfflinePatrolPeriodRecord(
                    id=period_id,
                    job_id=ids["offline_job_id"],
                    role=role,
                    label=role.title(),
                    observed_at=observed_at,
                    dataset_id=dataset_id,
                    patrol_run_id=run_id,
                    status="completed",
                    result_json={"success": True},
                    created_at=now,
                    updated_at=now,
                )
                for period_id, role, observed_at, dataset_id, run_id in zip(
                    ids["period_ids"],
                    ("baseline", "current"),
                    (baseline_at, current_at),
                    ids["dataset_ids"],
                    ids["run_ids"],
                    strict=True,
                )
            ]
        )
        db.add_all(
            [
                FileMetadata(
                    id=file_id,
                    name=f"{role}.txt",
                    object_key=(
                        f"e2e/rule-assets/{ids['suffix']}/{role}.txt"
                    ),
                    owner_id=owner,
                    visibility="private",
                    content_type="text/plain",
                    size_bytes=len(raw_text.encode("utf-8")),
                    etag=hashlib.md5(  # noqa: S324 - fixture metadata only
                        raw_text.encode("utf-8")
                    ).hexdigest(),
                    status="active",
                    created_by=owner,
                    is_deleted=False,
                )
                for file_id, role, raw_text in zip(
                    ids["file_ids"],
                    ("baseline", "current"),
                    raw_texts,
                    strict=True,
                )
            ]
        )
        db.flush()
        db.add_all(
            [
                OfflinePatrolSourceFileRecord(
                    id=source_id,
                    job_id=ids["offline_job_id"],
                    period_role=role,
                    file_id=file_id,
                    relative_path=f"{role}.txt",
                    sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
                    size_bytes=len(raw_text.encode("utf-8")),
                    status="verified",
                    preview_json={"command_count": 1},
                    created_at=now,
                    updated_at=now,
                )
                for source_id, role, file_id, raw_text in zip(
                    ids["source_file_ids"],
                    ("baseline", "current"),
                    ids["file_ids"],
                    raw_texts,
                    strict=True,
                )
            ]
        )
        db.add(
            ParserTemplate(
                id=ids["parser_template_id"],
                vendor="H3C",
                model=ids["model"],
                family=ids["family"],
                profile=ids["profile"],
                canonical_command=CANONICAL_COMMAND,
                category="cpu_record",
                version=f"e2e-{ids['suffix']}",
                asset_path=(
                    f"postgresql://parser-template/{ids['parser_template_id']}"
                ),
                template_text=template_text,
                sha256=template_sha,
                status="active",
                validation_json=validation,
                contract_version=1,
                contract_sha256=contract_sha,
                source_job_id=None,
                activated_at=now,
            )
        )
        db.add(
            CollectionImplementationRecord(
                id=ids["implementation_id"],
                name=f"E2E CPU implementation {ids['suffix']}",
                description="Isolated full-stack verification fixture",
                status="active",
                current_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()
        db.add(
            CollectionImplementationVersionRecord(
                id=ids["implementation_version_id"],
                implementation_id=ids["implementation_id"],
                version=1,
                collector="cli",
                match_json=implementation_payload["match"],
                commands_json=implementation_payload["commands"],
                parser_requirement="structured",
                template_scope="model",
                output_quality_policy_json=output_quality_policy,
                selection_priority=100,
                status="active",
                digest=_digest(implementation_payload),
                created_by=owner,
                created_at=now,
                activated_at=now,
            )
        )
        db.flush()
        db.add(
            CheckItemImplementationLinkRecord(
                id=ids["implementation_link_id"],
                check_item_version_id=check_version.id,
                implementation_version_id=ids["implementation_version_id"],
                priority=100,
                required=True,
                created_at=now,
            )
        )
        db.add(
            CollectionImplementationParserBindingRecord(
                id=ids["parser_binding_id"],
                implementation_version_id=ids["implementation_version_id"],
                canonical_command=CANONICAL_COMMAND,
                parser_template_id=ids["parser_template_id"],
                record_set="default",
                binding_role="primary",
                output_signature="e2e-cpu-slot-v1",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()
        db.add_all(
            [
                CommandSnapshot(
                    id=snapshot_id,
                    run_id=run_id,
                    dataset_id=dataset_id,
                    inventory_device_id=None,
                    device_id=f"external-e2e-{ids['suffix']}",
                    execution_item_id=f"execution-{role}-{ids['suffix']}",
                    implementation_version_id=ids[
                        "implementation_version_id"
                    ],
                    check_item_ids_json=["system.cpu_usage"],
                    device_name=f"E2E-CPU-{ids['suffix']}",
                    management_ip=None,
                    vendor="H3C",
                    model=ids["model"],
                    command=CANONICAL_COMMAND,
                    canonical_command=CANONICAL_COMMAND,
                    parser_template_id=ids["parser_template_id"],
                    parser_version=f"e2e-{ids['suffix']}",
                    contract_version=1,
                    observed_at=observed_at,
                    structured_json=[{"slot": "0", "cpu_5m": value}],
                    raw_text=raw_text,
                    raw_text_hash=hashlib.sha256(
                        raw_text.encode("utf-8")
                    ).hexdigest(),
                    parse_status="parsed",
                    output_status="complete",
                    snapshot_kind="full",
                    source_file_hash=hashlib.sha256(
                        raw_text.encode("utf-8")
                    ).hexdigest(),
                    metadata_json={"fixture": "rule_asset_runtime_bundle_e2e"},
                )
                for snapshot_id, run_id, dataset_id, role, observed_at, value, raw_text in zip(
                    ids["snapshot_ids"],
                    ids["run_ids"],
                    ids["dataset_ids"],
                    ("baseline", "current"),
                    (baseline_at, current_at),
                    ("42", "91"),
                    raw_texts,
                    strict=True,
                )
            ]
        )
        asset_key = _digest(
            {
                "offline_job_id": ids["offline_job_id"],
                "vendor": "H3C",
                "profile": ids["profile"],
                "command": CANONICAL_COMMAND,
            }
        )
        db.add(
            RuleAssetCommandUnitRecord(
                id=ids["command_unit_id"],
                offline_job_id=ids["offline_job_id"],
                check_item_id="system.cpu_usage",
                asset_key=asset_key,
                vendor="H3C",
                model=ids["model"],
                models_json=[ids["model"]],
                family=ids["family"],
                profile=ids["profile"],
                canonical_command=CANONICAL_COMMAND,
                handling="structured",
                asset_mode="full_chain",
                status="in_progress",
                current_stage="rule_generation",
                progress_percent=60,
                sample_count=2,
                device_count=1,
                output_signature_count=1,
                blocker_count=0,
                parse_counts_json={"parsed": 2},
                stage_states_json=[],
                next_action_json={"code": "generate_rule_assets"},
                parser_template_id=ids["parser_template_id"],
                projection_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()
        db.add(
            RuleAssetCommandUnitCheckItemRecord(
                id=ids["command_unit_link_id"],
                command_unit_id=ids["command_unit_id"],
                check_item_id="system.cpu_usage",
                check_item_version_id=check_version.id,
                implementation_id=ids["implementation_id"],
                implementation_version_id=ids[
                    "implementation_version_id"
                ],
                mapping_source="e2e_fixture",
                created_at=now,
            )
        )
        db.add_all(
            [
                NetworkChangeRecord(
                    id=change_id,
                    idempotency_key=_digest(change_id),
                    run_id=ids["run_ids"][1],
                    check_item_id="system.cpu_usage",
                    check_item_version_id=check_version.id,
                    device_id=f"external-e2e-{ids['suffix']}",
                    canonical_command=CANONICAL_COMMAND,
                    entity_type="cpu_scope",
                    entity_key="scope=slot|scope_id=0",
                    dimensions_json={"scope": "slot", "scope_id": "0", "window": "5m"},
                    field="cpu_5m",
                    observation_type=OBSERVATION_TYPE,
                    old_value_json=old_value,
                    new_value_json=new_value,
                    change_type="modified",
                    previous_snapshot_id=ids["snapshot_ids"][0],
                    current_snapshot_id=ids["snapshot_ids"][1],
                    source="rule_asset_replay",
                    analysis_revision="e2e",
                    old_number=float(old_value),
                    new_number=float(new_value),
                    event_status="pending",
                    observed_at=current_at,
                    detected_at=current_at,
                    processed_at=current_at,
                    confidence=1.0,
                    created_at=now,
                )
                for change_id, old_value, new_value in zip(
                    ids["change_ids"],
                    (42, 91),
                    (91, 70),
                    strict=True,
                )
            ]
        )
        db.flush()
        return {
            "check_item_version_id": str(check_version.id),
            "implementation_version_id": ids["implementation_version_id"],
            "parser_template_id": ids["parser_template_id"],
            "dataset_ids": list(ids["dataset_ids"]),
            "template_sha256": template_sha,
            "contract_sha256": contract_sha,
        }


def _wait_workflow(run_id: str, *, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        with get_db_session() as db:
            run = db.get(WorkflowRun, run_id)
            if run is None:
                raise AssertionError("generation WorkflowRun was not persisted")
            steps = (
                db.query(WorkflowStepRecord)
                .filter(WorkflowStepRecord.run_id == run_id)
                .order_by(WorkflowStepRecord.step_index)
                .all()
            )
            last = {
                "run_id": str(run.id),
                "status": str(run.status),
                "error_message": run.error_message,
                "user_id": run.user_id,
                "context": dict(run.context or {}),
                "steps": [
                    {
                        "index": int(step.step_index),
                        "name": str(step.step_name),
                        "skill": str(step.skill_name),
                        "status": str(step.status),
                        "error_message": step.error_message,
                    }
                    for step in steps
                ],
            }
        if last["status"] in WORKFLOW_TERMINAL:
            return last
        time.sleep(0.5)
    raise TimeoutError(
        f"rule generation Workflow did not finish in {timeout_seconds}s: {last}"
    )


def _generated_bundle(seed_job_id: str) -> dict[str, Any]:
    with get_db_session() as db:
        seed = db.get(RuleGenerationJobRecord, seed_job_id)
        if seed is None:
            raise AssertionError("rule generation seed disappeared")
        payload = dict(seed.payload_json or {})
        bundle_id = str(payload.get("latest_bundle_id") or "")
        if not bundle_id:
            raise AssertionError(
                f"generation Workflow did not link a bundle to seed {seed_job_id}"
            )
        bundle = db.get(RuleGenerationJobRecord, bundle_id)
        if bundle is None:
            raise AssertionError("generated rule bundle was not persisted")
        child_ids = [
            str(item)
            for item in dict(bundle.payload_json or {}).get("child_job_ids") or []
            if str(item)
        ]
        children = (
            db.query(RuleGenerationJobRecord)
            .filter(RuleGenerationJobRecord.id.in_(child_ids))
            .order_by(RuleGenerationJobRecord.job_type, RuleGenerationJobRecord.id)
            .all()
        )
        if len(children) != len(child_ids):
            raise AssertionError("generated rule bundle has incomplete child jobs")
        return {
            "id": str(bundle.id),
            "status": str(bundle.status),
            "created_by": bundle.created_by,
            "asset_digest": bundle.asset_digest,
            "validation_report": dict(bundle.validation_report_json or {}),
            "child_jobs": [
                {
                    "id": str(child.id),
                    "job_type": str(child.job_type),
                    "status": str(child.status),
                    "created_by": child.created_by,
                    "asset_id": child.asset_id,
                    "asset_version_id": child.asset_version_id,
                    "asset_digest": child.asset_digest,
                }
                for child in children
            ],
        }


def _cleanup_fixture(
    ids: dict[str, Any],
    *,
    usernames: list[str],
    workflow_run_id: str | None,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    template_source_ref = f"parser_template:{ids['parser_template_id']}"
    with get_db_session() as db:
        runtime_versions = (
            db.query(CheckItemRuntimeBundleVersionRecord)
            .filter(
                CheckItemRuntimeBundleVersionRecord.implementation_version_id
                == ids["implementation_version_id"]
            )
            .all()
        )
        runtime_version_ids = [str(row.id) for row in runtime_versions]
        runtime_bundle_ids = [str(row.bundle_id) for row in runtime_versions]
        if runtime_version_ids:
            counts["runtime_assets"] = (
                db.query(RuntimeBundleAssetRecord)
                .filter(
                    RuntimeBundleAssetRecord.bundle_version_id.in_(
                        runtime_version_ids
                    )
                )
                .delete(synchronize_session=False)
            )
            counts["runtime_versions"] = (
                db.query(CheckItemRuntimeBundleVersionRecord)
                .filter(
                    CheckItemRuntimeBundleVersionRecord.id.in_(
                        runtime_version_ids
                    )
                )
                .delete(synchronize_session=False)
            )
        if runtime_bundle_ids:
            counts["runtime_roots"] = (
                db.query(CheckItemRuntimeBundleRecord)
                .filter(
                    CheckItemRuntimeBundleRecord.id.in_(runtime_bundle_ids)
                )
                .delete(synchronize_session=False)
            )

        bundle_jobs = (
            db.query(RuleGenerationJobRecord)
            .filter(
                RuleGenerationJobRecord.source_ref == template_source_ref,
                RuleGenerationJobRecord.job_type == "RULE_ASSET_BUNDLE",
            )
            .all()
        )
        bundle_ids = [str(row.id) for row in bundle_jobs]
        child_jobs = (
            db.query(RuleGenerationJobRecord)
            .filter(
                RuleGenerationJobRecord.source_ref.in_(
                    [f"rule_asset_bundle:{item}" for item in bundle_ids]
                )
            )
            .all()
            if bundle_ids
            else []
        )
        child_job_ids = [str(row.id) for row in child_jobs]
        seed_jobs = (
            db.query(RuleGenerationJobRecord)
            .filter(
                RuleGenerationJobRecord.source_ref == template_source_ref,
                RuleGenerationJobRecord.job_type == "OBSERVATION_CONTRACT",
            )
            .all()
        )
        seed_job_ids = [str(row.id) for row in seed_jobs]
        all_rule_job_ids = sorted(
            set(seed_job_ids + bundle_ids + child_job_ids)
        )

        event_versions = (
            db.query(EventRuleVersionRecord)
            .filter(EventRuleVersionRecord.source_job_id.in_(child_job_ids))
            .all()
            if child_job_ids
            else []
        )
        event_root_ids = [str(row.rule_id) for row in event_versions]
        policy_versions = (
            db.query(ChangePolicyVersionRecord)
            .filter(ChangePolicyVersionRecord.source_job_id.in_(child_job_ids))
            .all()
            if child_job_ids
            else []
        )
        policy_root_ids = [str(row.policy_id) for row in policy_versions]

        if all_rule_job_ids:
            counts["approval_audits"] = (
                db.query(RuleApprovalAuditRecord)
                .filter(RuleApprovalAuditRecord.job_id.in_(all_rule_job_ids))
                .delete(synchronize_session=False)
            )
            counts["agent_attempts"] = (
                db.query(RuleAgentLoopAttemptRecord)
                .filter(
                    RuleAgentLoopAttemptRecord.job_id.in_(all_rule_job_ids)
                )
                .delete(synchronize_session=False)
            )
            counts["replay_reports"] = (
                db.query(RuleReplayReportRecord)
                .filter(RuleReplayReportRecord.job_id.in_(all_rule_job_ids))
                .delete(synchronize_session=False)
            )
        counts["contracts"] = (
            db.query(ObservationContractRecord)
            .filter(
                (ObservationContractRecord.source_template_id
                 == ids["parser_template_id"])
                | ObservationContractRecord.source_job_id.in_(child_job_ids)
            )
            .delete(synchronize_session=False)
        )
        if child_job_ids:
            counts["policy_versions"] = (
                db.query(ChangePolicyVersionRecord)
                .filter(
                    ChangePolicyVersionRecord.source_job_id.in_(
                        child_job_ids
                    )
                )
                .delete(synchronize_session=False)
            )
            counts["event_versions"] = (
                db.query(EventRuleVersionRecord)
                .filter(
                    EventRuleVersionRecord.source_job_id.in_(child_job_ids)
                )
                .delete(synchronize_session=False)
            )
        if policy_root_ids:
            counts["policy_roots"] = (
                db.query(ChangePolicyRecord)
                .filter(ChangePolicyRecord.id.in_(policy_root_ids))
                .delete(synchronize_session=False)
            )
        if event_root_ids:
            counts["event_roots"] = (
                db.query(EventRuleRecord)
                .filter(EventRuleRecord.id.in_(event_root_ids))
                .delete(synchronize_session=False)
            )
        if seed_job_ids:
            counts["contexts"] = (
                db.query(RuleGenerationContextRecord)
                .filter(RuleGenerationContextRecord.job_id.in_(seed_job_ids))
                .delete(synchronize_session=False)
            )
        if all_rule_job_ids:
            counts["rule_jobs"] = (
                db.query(RuleGenerationJobRecord)
                .filter(RuleGenerationJobRecord.id.in_(all_rule_job_ids))
                .delete(synchronize_session=False)
            )

        if workflow_run_id:
            counts["notifications"] = (
                db.query(Notification)
                .filter(Notification.workflow_run_id == workflow_run_id)
                .delete(synchronize_session=False)
            )
            counts["workflow_steps"] = (
                db.query(WorkflowStepRecord)
                .filter(WorkflowStepRecord.run_id == workflow_run_id)
                .delete(synchronize_session=False)
            )
            counts["workflow_runs"] = (
                db.query(WorkflowRun)
                .filter(WorkflowRun.id == workflow_run_id)
                .delete(synchronize_session=False)
            )
        counts["audits"] = (
            db.query(AuditLogRecord)
            .filter(
                (AuditLogRecord.username.in_(usernames))
                | (
                    AuditLogRecord.resource_id.in_(
                        [
                            ids["parser_template_id"],
                            ids["implementation_version_id"],
                            *all_rule_job_ids,
                            *runtime_version_ids,
                        ]
                    )
                )
            )
            .delete(synchronize_session=False)
        )
        counts["command_unit_links"] = (
            db.query(RuleAssetCommandUnitCheckItemRecord)
            .filter(
                RuleAssetCommandUnitCheckItemRecord.command_unit_id
                == ids["command_unit_id"]
            )
            .delete(synchronize_session=False)
        )
        counts["command_units"] = (
            db.query(RuleAssetCommandUnitRecord)
            .filter(RuleAssetCommandUnitRecord.id == ids["command_unit_id"])
            .delete(synchronize_session=False)
        )
        counts["parser_bindings"] = (
            db.query(CollectionImplementationParserBindingRecord)
            .filter(
                CollectionImplementationParserBindingRecord.id
                == ids["parser_binding_id"]
            )
            .delete(synchronize_session=False)
        )
        counts["implementation_links"] = (
            db.query(CheckItemImplementationLinkRecord)
            .filter(
                CheckItemImplementationLinkRecord.id
                == ids["implementation_link_id"]
            )
            .delete(synchronize_session=False)
        )
        counts["changes"] = (
            db.query(NetworkChangeRecord)
            .filter(NetworkChangeRecord.id.in_(ids["change_ids"]))
            .delete(synchronize_session=False)
        )
        counts["snapshots"] = (
            db.query(CommandSnapshot)
            .filter(CommandSnapshot.id.in_(ids["snapshot_ids"]))
            .delete(synchronize_session=False)
        )
        counts["source_files"] = (
            db.query(OfflinePatrolSourceFileRecord)
            .filter(
                OfflinePatrolSourceFileRecord.job_id
                == ids["offline_job_id"]
            )
            .delete(synchronize_session=False)
        )
        counts["periods"] = (
            db.query(OfflinePatrolPeriodRecord)
            .filter(OfflinePatrolPeriodRecord.job_id == ids["offline_job_id"])
            .delete(synchronize_session=False)
        )
        counts["runs"] = (
            db.query(PatrolRun)
            .filter(PatrolRun.id.in_(ids["run_ids"]))
            .delete(synchronize_session=False)
        )
        counts["datasets"] = (
            db.query(PatrolDataset)
            .filter(PatrolDataset.id.in_(ids["dataset_ids"]))
            .delete(synchronize_session=False)
        )
        counts["offline_jobs"] = (
            db.query(OfflinePatrolJobRecord)
            .filter(OfflinePatrolJobRecord.id == ids["offline_job_id"])
            .delete(synchronize_session=False)
        )
        counts["files"] = (
            db.query(FileMetadata)
            .filter(FileMetadata.id.in_(ids["file_ids"]))
            .delete(synchronize_session=False)
        )
        counts["parsers"] = (
            db.query(ParserTemplate)
            .filter(ParserTemplate.id == ids["parser_template_id"])
            .delete(synchronize_session=False)
        )
        counts["implementation_versions"] = (
            db.query(CollectionImplementationVersionRecord)
            .filter(
                CollectionImplementationVersionRecord.id
                == ids["implementation_version_id"]
            )
            .delete(synchronize_session=False)
        )
        counts["implementation_roots"] = (
            db.query(CollectionImplementationRecord)
            .filter(
                CollectionImplementationRecord.id
                == ids["implementation_id"]
            )
            .delete(synchronize_session=False)
        )
    _delete_users(usernames)
    return counts


def run(
    *,
    base_url: str,
    output_path: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:10]
    password = f"E2E-{uuid.uuid4().hex}!a9"
    usernames = {
        "generator": f"e2e_rule_generator_{suffix}",
        "reviewer": f"e2e_rule_reviewer_{suffix}",
        "releaser": f"e2e_rule_releaser_{suffix}",
        "bundle_builder": f"e2e_bundle_builder_{suffix}",
        "bundle_approver": f"e2e_bundle_approver_{suffix}",
    }
    ids = _fixture_ids(suffix)
    baseline = _active_asset_snapshot()
    summary: dict[str, Any] = {
        "success": False,
        "suffix": suffix,
        "base_url": base_url,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "baseline": baseline,
        "fixture": {
            "offline_job_id": ids["offline_job_id"],
            "parser_template_id": ids["parser_template_id"],
            "model": ids["model"],
        },
    }
    clients: dict[str, httpx.Client] = {}
    workflow_run_id: str | None = None
    primary_error: BaseException | None = None
    cleanup_error: BaseException | None = None

    try:
        _assert_isolated_cpu_slot()
        for role_name, username in usernames.items():
            platform_role = "operator" if role_name == "generator" else "admin"
            _create_user(username, password, platform_role)
            client = httpx.Client(
                base_url=base_url.rstrip("/"),
                timeout=60,
            )
            client.headers["Authorization"] = (
                f"Bearer {_login(client, username=username, password=password)}"
            )
            clients[role_name] = client

        fixture = _create_fixture(ids, owner=usernames["generator"])
        summary["fixture"].update(fixture)

        seed_result = RuleAssetTemplateActivationService().seed_from_template(
            ids["parser_template_id"]
        )
        if seed_result.get("success") is not True:
            raise AssertionError(f"failed to create rule seed: {seed_result}")
        seed_job_id = str((seed_result.get("job") or {}).get("id") or "")
        if not seed_job_id:
            raise AssertionError("rule seed did not return a job id")
        summary["seed_job_id"] = seed_job_id

        context_preview = _request_json(
            clients["generator"],
            "POST",
            f"/api/parser/rule-generation-jobs/{seed_job_id}/generation-context/",
            json_body={
                "dataset_ids": fixture["dataset_ids"],
                "max_records": 120,
                "context_overrides": {
                    "generation_provider": "builtin",
                    "max_agent_attempts": 1,
                },
            },
        )
        if context_preview.get("ready") is not True:
            raise AssertionError(
                f"immutable generation context is not ready: {context_preview}"
            )
        context_id = str(context_preview.get("context_id") or "")
        context_digest = str(context_preview.get("context_digest") or "")
        privacy = dict(context_preview.get("privacy") or {})
        serialized_context = json.dumps(
            context_preview.get("context") or {},
            ensure_ascii=False,
            sort_keys=True,
        )
        if (
            not context_id
            or not context_digest
            or privacy.get("raw_cli_included") is not False
            or "PRIVATE E2E RAW CLI" in serialized_context
        ):
            raise AssertionError(
                "sanitized immutable context leaked or omitted privacy evidence"
            )
        summary["generation_context"] = {
            "context_id": context_id,
            "context_digest": context_digest,
            "dataset_ids": context_preview.get("dataset_ids"),
            "record_count": len(
                (context_preview.get("context") or {}).get("records") or []
            ),
            "privacy": privacy,
        }

        started = _request_json(
            clients["generator"],
            "POST",
            f"/api/parser/rule-generation-jobs/{seed_job_id}/start-generation/",
            json_body={
                "context_id": context_id,
                "context_overrides": {
                    "generation_provider": "builtin",
                    "max_agent_attempts": 1,
                },
            },
        )
        workflow_run_id = str(started.get("run_id") or "")
        if not workflow_run_id:
            raise AssertionError("generation endpoint did not return a WorkflowRun id")
        workflow = _wait_workflow(
            workflow_run_id,
            timeout_seconds=timeout_seconds,
        )
        summary["workflow"] = workflow
        if workflow["status"] != "completed":
            raise AssertionError(f"rule generation Workflow failed: {workflow}")
        if (
            workflow["user_id"] != usernames["generator"]
            or workflow["context"].get("rule_generation_actor")
            != usernames["generator"]
        ):
            raise AssertionError(
                "generation actor was not frozen in the Workflow provenance"
            )

        generated = _generated_bundle(seed_job_id)
        summary["generated_bundle"] = generated
        if generated["status"] != "pending_approval":
            raise AssertionError(
                f"generated bundle is not reviewable: {generated}"
            )
        creators = {
            generated["created_by"],
            *[item["created_by"] for item in generated["child_jobs"]],
        }
        if creators != {usernames["generator"]}:
            raise AssertionError(
                f"candidate provenance differs from initiating actor: {creators}"
            )
        child_counts: dict[str, int] = {}
        for child in generated["child_jobs"]:
            child_counts[child["job_type"]] = (
                child_counts.get(child["job_type"], 0) + 1
            )
        if child_counts != {
            "OBSERVATION_CONTRACT": 1,
            "CHANGE_POLICY": 1,
            "EVENT_RULE": 2,
        }:
            raise AssertionError(
                f"unexpected generated asset composition: {child_counts}"
            )

        approval_path = (
            f"/api/parser/rule-generation-jobs/{generated['id']}/approve/"
        )
        review_path = (
            f"/api/parser/rule-asset-bundles/{generated['id']}"
            "/engineering-review/"
        )
        summary["governance_rejections"] = {
            "approval_before_review": _expect_rejection(
                clients["releaser"],
                "POST",
                approval_path,
                json_body={"reason": "E2E release before engineering review"},
                expected_statuses=(409,),
                expected_text="engineering review",
            ),
            "creator_self_review": _expect_rejection(
                clients["generator"],
                "POST",
                review_path,
                json_body={
                    "decision": "accepted",
                    "reason": "Creator must not review the generated candidate",
                },
                expected_statuses=(409,),
                expected_text="different from the candidate creator",
            ),
        }
        engineering_review = _request_json(
            clients["reviewer"],
            "POST",
            review_path,
            json_body={
                "decision": "accepted",
                "reason": (
                    "Independent E2E review accepted deterministic validation "
                    "and replay evidence"
                ),
            },
        )
        summary["engineering_review"] = engineering_review
        summary["governance_rejections"]["reviewer_self_approval"] = (
            _expect_rejection(
                clients["reviewer"],
                "POST",
                approval_path,
                json_body={"reason": "Reviewer must not release own review"},
                expected_statuses=(409,),
                expected_text="different from the engineering reviewer",
            )
        )
        approved = _request_json(
            clients["releaser"],
            "POST",
            approval_path,
            json_body={
                "reason": (
                    "Independent E2E release after validation, replay and "
                    "engineering review"
                )
            },
        )
        summary["rule_approval"] = approved

        active_generated = _generated_bundle(seed_job_id)
        if active_generated["status"] != "active" or any(
            item["status"] != "active"
            for item in active_generated["child_jobs"]
        ):
            raise AssertionError(
                f"rule bundle did not activate atomically: {active_generated}"
            )
        by_type: dict[str, list[dict[str, Any]]] = {}
        for child in active_generated["child_jobs"]:
            by_type.setdefault(child["job_type"], []).append(child)
        contract_id = str(
            by_type["OBSERVATION_CONTRACT"][0]["asset_version_id"]
        )
        policy_version_id = str(
            by_type["CHANGE_POLICY"][0]["asset_version_id"]
        )
        event_version_ids = [
            str(item["asset_version_id"]) for item in by_type["EVENT_RULE"]
        ]

        with get_db_session() as db:
            contract = db.get(ObservationContractRecord, contract_id)
            if contract is None:
                raise AssertionError("active Contract cannot be loaded")
            schema_pair = [str(contract.source_type), str(contract.schema_id)]

        runtime_assets = _request_json(
            clients["generator"],
            "POST",
            "/api/parser/runtime-assets/resolve/",
            json_body={
                "observation_schemas": [schema_pair],
                "observation_types": [OBSERVATION_TYPE],
                "include_event_rules": True,
            },
        )
        summary["runtime_rule_assets"] = {
            "ready": runtime_assets.get("ready"),
            "counts": runtime_assets.get("counts"),
            "missing_observation_contracts": runtime_assets.get(
                "missing_observation_contracts"
            ),
            "missing_change_policies": runtime_assets.get(
                "missing_change_policies"
            ),
            "missing_event_rules": runtime_assets.get("missing_event_rules"),
        }
        if (
            runtime_assets.get("event_evaluation_ready") is not True
            or runtime_assets.get("missing_observation_contracts")
            or runtime_assets.get("missing_change_policies")
            or runtime_assets.get("missing_event_rules")
        ):
            raise AssertionError(
                f"runtime rule resolver cannot read approved assets: {runtime_assets}"
            )

        assembly_request = {
            "check_item_version_id": fixture["check_item_version_id"],
            "implementation_version_id": fixture[
                "implementation_version_id"
            ],
            "parser_template_ids": [fixture["parser_template_id"]],
            "observation_contract_ids": [contract_id],
            "change_policy_version_ids": [policy_version_id],
            "event_rule_version_ids": event_version_ids,
            "comparison_profile_version_id": None,
        }
        preview = _request_json(
            clients["bundle_builder"],
            "POST",
            "/api/parser/check-item-runtime-bundles/assembly-preview/",
            json_body=assembly_request,
        )
        if preview.get("blockers"):
            raise AssertionError(
                f"RuntimeBundle assembly preview is blocked: {preview}"
            )
        preview_digest = str(preview.get("preview_digest") or "")
        if not preview_digest:
            raise AssertionError("RuntimeBundle preview omitted its digest")
        summary["runtime_bundle_preview"] = {
            "preview_digest": preview_digest,
            "selected": preview.get("selected"),
            "warnings": preview.get("warnings"),
        }

        draft = _request_json(
            clients["bundle_builder"],
            "POST",
            "/api/parser/check-item-runtime-bundles/assemble/",
            json_body={**assembly_request, "preview_digest": preview_digest},
            headers={"Idempotency-Key": f"assemble-{suffix}"},
        )
        runtime_version_id = str(draft.get("version_id") or "")
        if draft.get("version_status") != "draft" or not runtime_version_id:
            raise AssertionError(f"RuntimeBundle draft was not created: {draft}")
        activate_path = (
            f"/api/parser/check-item-runtime-bundles/"
            f"{runtime_version_id}/activate/"
        )
        summary["governance_rejections"]["bundle_creator_self_activation"] = (
            _expect_rejection(
                clients["bundle_builder"],
                "POST",
                activate_path,
                json_body={"reason": "Builder must not activate own bundle"},
                headers={"Idempotency-Key": f"self-activate-{suffix}"},
                expected_statuses=(409,),
                expected_text="different from the version creator",
            )
        )
        activated_runtime = _request_json(
            clients["bundle_approver"],
            "POST",
            activate_path,
            json_body={
                "reason": (
                    "Independent E2E RuntimeBundle approval after immutable "
                    "assembly preview"
                )
            },
            headers={"Idempotency-Key": f"activate-{suffix}"},
        )
        summary["runtime_bundle"] = activated_runtime
        if activated_runtime.get("version_status") != "active":
            raise AssertionError(
                f"RuntimeBundle did not activate: {activated_runtime}"
            )

        active_runtime = CheckItemRuntimeBundleService().get_active(
            check_item_version_id=fixture["check_item_version_id"],
            implementation_version_id=fixture["implementation_version_id"],
        )
        if active_runtime is None:
            raise AssertionError("RuntimeBundle resolver returned no active bundle")
        asset_types = {
            str(item["asset_type"]) for item in active_runtime.get("assets") or []
        }
        expected_asset_types = {
            "parser_template",
            "observation_contract",
            "current_state_rule",
            "change_policy",
            "event_rule",
            "identity_policy",
        }
        if not expected_asset_types.issubset(asset_types):
            raise AssertionError(
                f"RuntimeBundle is missing frozen asset roles: {asset_types}"
            )
        summary["active_runtime_resolution"] = {
            "version_id": active_runtime.get("version_id"),
            "digest": active_runtime.get("digest"),
            "asset_types": sorted(asset_types),
            "asset_count": len(active_runtime.get("assets") or []),
        }
        summary["success"] = True
        summary["completed_at"] = datetime.now(timezone.utc).isoformat()
    except BaseException as exc:  # preserve business failure across cleanup
        primary_error = exc
        summary["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        for client in clients.values():
            client.close()
        try:
            cleanup = _cleanup_fixture(
                ids,
                usernames=list(usernames.values()),
                workflow_run_id=workflow_run_id,
            )
            summary["cleanup"] = cleanup
            after_cleanup = _active_asset_snapshot()
            summary["after_cleanup"] = after_cleanup
            summary["production_assets_unchanged"] = after_cleanup == baseline
            if after_cleanup != baseline:
                raise AssertionError(
                    "existing active production asset identities changed across "
                    "the isolated full-stack test"
                )
        except BaseException as exc:
            cleanup_error = exc
            summary["cleanup_error"] = f"{type(exc).__name__}: {exc}"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    if primary_error is not None:
        if cleanup_error is not None:
            raise AssertionError(
                f"business path failed: {primary_error}; cleanup also failed: "
                f"{cleanup_error}; evidence={output_path}"
            ) from primary_error
        raise AssertionError(
            f"business path failed: {primary_error}; evidence={output_path}"
        ) from primary_error
    if cleanup_error is not None:
        raise AssertionError(
            f"business path passed but cleanup failed: {cleanup_error}; "
            f"evidence={output_path}"
        ) from cleanup_error
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:3000")
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            ROOT
            / ".tmp"
            / "rule-asset-runtime-bundle-full-stack-e2e"
            / "summary.json"
        ),
    )
    parser.add_argument("--timeout-seconds", type=int, default=240)
    args = parser.parse_args()
    summary = run(
        base_url=args.base_url,
        output_path=args.output.resolve(),
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run one real lixian sample through the governed check-item report path.

The script temporarily binds the already-approved H3C MSR5620 ``display
version`` Parser to a check-item-compatible ObservationContract and
RuntimeBundle. Every database write lives inside one outer transaction and is
always rolled back, so the evidence run cannot activate test assets in
production.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import uuid
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.check_items.canonical import load_canonical_catalog
from src.core.check_items.catalog import (
    CheckItemCatalog,
    CheckItemCatalogRepository,
)
from src.core.check_items.planning import (
    CheckItemEffectivePlanService,
    EffectivePlanRequest,
    build_inventory_snapshot,
    build_target_manifest,
)
from src.core.check_items.report_contract import (
    OFFLINE_HISTORICAL,
    report_authority_context_is_valid,
)
from src.core.check_items.report_manifest import ReportManifestService
from src.core.check_items.report_content_review import (
    REQUIRED_CONTENT_CONFIRMATIONS,
    ReportContentReviewService,
)
from src.core.check_items.report_release import ReportReleaseService
from src.core.check_items.reporting import (
    CheckItemReportMaterializer,
    build_check_item_report_view,
    load_attached_report_artifact,
)
from src.core.check_items.run_finalizer import CheckItemRunFinalizer
from src.core.check_items.runtime_bundle import CheckItemRuntimeBundleService
from src.core.check_items.runtime_bundle_assembly import (
    derive_asset_requirement_profile,
)
from src.core.observation import ObservationContractRepository
from src.core.parser_generation.validation import TextFSMValidationBackend
from src.core.patrol.command_splitter import (
    infer_device_from_filename,
    split_cli_capture,
)
from src.core.state.normalizer import observations_from_snapshot
from src.core.state.repository import StateRepository
from src.infrastructure.db.models import (
    ChangePolicyRecord,
    ChangePolicyVersionRecord,
    CheckAssessmentRecord,
    CheckItemCollectionActionRecord,
    CheckItemImplementationLinkRecord,
    CheckItemRuntimeBundleVersionRecord,
    CollectionImplementationParserBindingRecord,
    CollectionImplementationRecord,
    CollectionImplementationVersionRecord,
    CommandSnapshot,
    CoverageDecisionRecord,
    EvidenceQualificationRecord,
    ObservationContractRecord,
    ParserTemplate,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    PatrolRun,
    StateObservationRecord,
    WorkflowRun,
    WorkflowStepRecord,
)
from src.infrastructure.db.postgres import engine, get_db_session
from src.infrastructure.storage.minio_client import get_minio_storage

PARSER_MODEL = "MSR5620"
CANONICAL_COMMAND = "display version"
CHECK_ITEM_ID = "device.version"
IMPLEMENTATION_ID = "h3c-device-version"
def _database_baseline(parser_id: str | None = None) -> dict[str, Any]:
    with get_db_session() as db:
        active_contract = None
        if parser_id:
            active_contract = (
                db.query(ObservationContractRecord)
                .filter(
                    ObservationContractRecord.source_type == "cli",
                    ObservationContractRecord.schema_id == parser_id,
                    ObservationContractRecord.status == "active",
                )
                .first()
            )
        return {
            "active_runtime_bundle_count": int(
                db.query(CheckItemRuntimeBundleVersionRecord)
                .filter(CheckItemRuntimeBundleVersionRecord.status == "active")
                .count()
            ),
            "active_contract_id": (
                str(active_contract.id) if active_contract is not None else None
            ),
        }


def _load_real_sample(source_file: Path) -> dict[str, Any]:
    raw_capture = source_file.read_bytes().decode("utf-8-sig", errors="strict")
    device_name, management_ip = infer_device_from_filename(source_file)
    blocks = split_cli_capture(raw_capture, device_name=device_name)
    matches = [
        block
        for block in blocks
        if block.command_canonical == CANONICAL_COMMAND
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected one {CANONICAL_COMMAND!r} block, got {len(matches)}"
        )
    return {
        "device_name": str(device_name or matches[0].device_name),
        "management_ip": management_ip,
        "block": matches[0],
        "source_sha256": hashlib.sha256(
            source_file.read_bytes()
        ).hexdigest(),
    }


def _contract_validation(
    parsed_records: list[dict[str, Any]],
    canonical_contract: dict[str, Any],
) -> dict[str, Any]:
    emitted_fields = {
        str(field)
        for record in parsed_records
        for field in record
    }
    required = {"software_version", "release", "model"}
    missing = sorted(required - emitted_fields)
    if missing:
        raise AssertionError(
            "approved Parser output lacks device identity fields: "
            + ", ".join(missing)
        )
    canonical_entity_type = str(
        canonical_contract.get("entity_type") or ""
    ).strip()
    identity_fields = [
        str(item)
        for item in canonical_contract.get("identity_dimensions") or []
    ]
    unsupported_identity_fields = sorted(
        set(identity_fields) - {"device_id"}
    )
    if unsupported_identity_fields:
        raise AssertionError(
            "device.version E2E cannot project unsupported canonical identity "
            "dimensions: " + ", ".join(unsupported_identity_fields)
        )
    ignored = sorted(emitted_fields - {"software_version", "model"})
    return {
        "entity_type": "device",
        "primary_keys": [],
        "canonical_entity_type": canonical_entity_type,
        "entity_identity_fields": identity_fields,
        "entity_dimensions": {
            field: {"context": field}
            for field in identity_fields
        },
        "required_fields": sorted(required),
        "optional_fields": sorted(emitted_fields - required),
        "validators": {
            "software_version": {"type": "text"},
            "release": {"type": "text"},
            "model": {"type": "text"},
        },
        "ignore_fields": ignored,
        "allow_empty": False,
        "empty_patterns": [],
        "state_semantics": {
            "software_version": {
                "observation_type": "device.software.version",
                "semantic_type": "state",
                "transform": {
                    "op": "concat",
                    "args": [
                        {"field": "software_version"},
                        {"literal": ", Release "},
                        {"field": "release"},
                    ],
                },
            },
            "model": {
                "observation_type": "device.hardware.model",
                "semantic_type": "state",
            },
        },
    }


def _clone_parser(
    db: Any,
    source: dict[str, Any],
    *,
    run_token: str,
    now: datetime,
) -> ParserTemplate:
    values = {
        key: copy.deepcopy(value)
        for key, value in source.items()
        if key in ParserTemplate.__table__.columns.keys()
    }
    parser_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"netops/lixian-runtime-e2e/parser/{source['id']}/{run_token}",
        )
    )
    validation = copy.deepcopy(values.get("validation_json") or {})
    compatibility = copy.deepcopy(validation.get("compatibility") or {})
    compatibility.update(
        {
            "manifest_version": 1,
            "scope": "model",
            "vendor": "H3C",
            "family": "Comware7",
            "model": PARSER_MODEL,
            "canonical_command": CANONICAL_COMMAND,
        }
    )
    validation.update(
        {
            "compatibility": compatibility,
            "sample_count": max(
                1,
                int(validation.get("sample_count") or 0),
            ),
            "unique_sample_count": max(
                1,
                int(validation.get("unique_sample_count") or 0),
            ),
        }
    )
    values.update(
        id=parser_id,
        profile=f"lixian-e2e-{run_token}-{source['profile']}"[:128],
        version=f"lixian-e2e-{run_token}",
        asset_path=f"transaction://lixian-runtime-e2e/{parser_id}",
        status="active",
        validation_json=validation,
        source_job_id=None,
        created_at=now,
        updated_at=now,
        activated_at=now,
        deprecated_at=None,
    )
    parser = ParserTemplate(**values)
    db.add(parser)
    db.flush()
    return parser


def run_e2e(
    *,
    source_file: Path,
    output_dir: Path,
) -> dict[str, Any]:
    source = _load_real_sample(source_file)
    block = source["block"]
    now = datetime.now(timezone.utc)
    observed_at = datetime(2026, 3, 23, 6, 46, 48, tzinfo=timezone.utc)
    run_token = uuid.uuid4().hex[:12]
    batch_id = f"lixian-check-item-e2e-{run_token}"
    run_id = f"lixian-check-item-run-{run_token}"
    workflow_run_id = f"lixian-check-item-workflow-{run_token}"
    snapshot_id = f"lixian-check-item-snapshot-{run_token}"
    device_id = f"lixian-device-{run_token}"

    with get_db_session() as db:
        parser = (
            db.query(ParserTemplate)
            .filter(
                ParserTemplate.status == "active",
                ParserTemplate.vendor == "H3C",
                ParserTemplate.model == PARSER_MODEL,
                ParserTemplate.canonical_command == CANONICAL_COMMAND,
            )
            .first()
        )
        if parser is None:
            raise AssertionError(
                "active H3C MSR5620 display version Parser is missing"
            )
        source_parser = parser.to_dict(include_content=True)
        source_parser_id = str(parser.id)
        parser_text = str(parser.template_text or "")
        source_parser_version = str(parser.version)

    parsed_records = TextFSMValidationBackend().parse(
        parser_text,
        str(block.raw_output),
    )
    if not parsed_records:
        raise AssertionError("the approved Parser produced no records")
    distinct_versions = {
        (
            str(record.get("software_version") or ""),
            str(record.get("release") or ""),
            str(record.get("model") or ""),
        )
        for record in parsed_records
    }
    if len(distinct_versions) != 1:
        raise AssertionError(
            "device-level version fields disagree across parsed records"
        )
    expected_version, expected_release, expected_model = next(
        iter(distinct_versions)
    )
    expected_full_version = (
        f"{expected_version}, Release {expected_release}"
    )
    before = _database_baseline(source_parser_id)

    connection = engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    )
    outer_transaction = connection.begin()
    uploaded_report_prefix: str | None = None
    session_factory = sessionmaker(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    @contextmanager
    def scope():
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    result: dict[str, Any] = {}
    try:
        catalog_repository = CheckItemCatalogRepository(scope)
        catalog_repository.bootstrap(actor="lixian-runtime-e2e")
        catalog = catalog_repository.load_active()
        check_item = catalog.check_item(CHECK_ITEM_ID)
        implementation = next(
            (
                item
                for item in catalog.implementations
                if item.id == IMPLEMENTATION_ID
            ),
            None,
        )
        if check_item is None or implementation is None:
            raise AssertionError(
                "device.version catalog assets are not active"
            )
        expected_observation_types = {
            str(item.get("observation_type") or "")
            for item in check_item.canonical_contract.get(
                "observations"
            )
            or []
            if isinstance(item, dict)
            and str(item.get("observation_type") or "")
        }
        if not expected_observation_types:
            raise AssertionError(
                "device.version has no canonical observations"
            )
        implementation_id = (
            f"h3c-msr5620-device-version-e2e-{run_token}"
        )
        implementation_version_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"netops/e2e-implementation/{implementation_id}/v1",
            )
        )
        implementation_payload = {
            "id": implementation_id,
            "version": 1,
            "match": {
                "vendor": "H3C",
                "family": "Comware7",
                "model": PARSER_MODEL,
            },
            "commands": [
                {
                    "command": CANONICAL_COMMAND,
                    "priority": 100,
                }
            ],
            "collector": implementation.collector,
            "parser_requirement": implementation.parser_requirement,
            "template_scope": "model",
            "output_quality_policy": dict(
                implementation.output_quality_policy
            ),
            "selection_priority": 1000,
            "status": "active",
        }
        implementation_digest = hashlib.sha256(
            json.dumps(
                implementation_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        with scope() as db:
            runtime_parser = _clone_parser(
                db,
                source_parser,
                run_token=run_token,
                now=now,
            )
            parser_id = str(runtime_parser.id)
            parser_version = str(runtime_parser.version)
            db.add(
                CollectionImplementationRecord(
                    id=implementation_id,
                    name="H3C MSR5620 device version E2E",
                    description=(
                        "Transaction-scoped model-specific implementation "
                        "for real-sample acceptance."
                    ),
                    status="active",
                    current_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(
                CollectionImplementationVersionRecord(
                    id=implementation_version_id,
                    implementation_id=implementation_id,
                    version=1,
                    collector=implementation.collector,
                    match_json=dict(implementation_payload["match"]),
                    commands_json=list(
                        implementation_payload["commands"]
                    ),
                    parser_requirement=(
                        implementation.parser_requirement
                    ),
                    template_scope="model",
                    output_quality_policy_json=dict(
                        implementation.output_quality_policy
                    ),
                    selection_priority=1000,
                    status="active",
                    digest=implementation_digest,
                    created_by="lixian-runtime-e2e",
                    created_at=now,
                    activated_at=now,
                )
            )
            db.flush()
            db.add(
                CheckItemImplementationLinkRecord(
                    id=str(uuid.uuid4()),
                    check_item_version_id=check_item.version_id,
                    implementation_version_id=implementation_version_id,
                    priority=1000,
                    required=True,
                    created_at=now,
                )
            )
        implementation = replace(
            implementation,
            id=implementation_id,
            version=1,
            name="H3C MSR5620 device version E2E",
            description=(
                "Transaction-scoped model-specific implementation."
            ),
            match=dict(implementation_payload["match"]),
            template_scope="model",
            selection_priority=1000,
            digest=implementation_digest,
            version_id=implementation_version_id,
        )
        catalog = CheckItemCatalog(
            catalog_version=catalog.catalog_version,
            check_items=catalog.check_items,
            implementations=(
                *catalog.implementations,
                implementation,
            ),
        )

        validation = _contract_validation(
            parsed_records,
            check_item.canonical_contract,
        )
        with scope() as db:
            old_contract = (
                db.query(ObservationContractRecord)
                .filter(
                    ObservationContractRecord.source_type == "cli",
                    ObservationContractRecord.schema_id == parser_id,
                )
                .order_by(ObservationContractRecord.version.desc())
                .first()
            )
            contract_version = (
                int(old_contract.version or 0) + 1
                if old_contract is not None
                else 1
            )
            contract = ObservationContractRepository().upsert_contract_row(
                db,
                source_type="cli",
                schema_id=parser_id,
                schema_ref=f"postgresql://parser-template/{parser_id}",
                category="device",
                validation=validation,
                version=contract_version,
                source_template_id=parser_id,
                source_job_id=batch_id,
                approval={
                    "kind": "e2e_ephemeral",
                    "actor": "lixian-runtime-e2e",
                    "source_sha256": source["source_sha256"],
                },
                status="active",
                compatibility={
                    "scope": "transactional_e2e",
                    "production_activation": False,
                },
            )
            binding = (
                db.query(CollectionImplementationParserBindingRecord)
                .filter(
                    CollectionImplementationParserBindingRecord
                    .implementation_version_id
                    == implementation.version_id,
                    CollectionImplementationParserBindingRecord
                    .canonical_command
                    == CANONICAL_COMMAND,
                    CollectionImplementationParserBindingRecord.record_set
                    == "default",
                    CollectionImplementationParserBindingRecord.binding_role
                    == "primary",
                )
                .first()
            )
            if binding is None:
                binding = CollectionImplementationParserBindingRecord(
                    id=str(uuid.uuid4()),
                    implementation_version_id=implementation.version_id,
                    canonical_command=CANONICAL_COMMAND,
                    parser_template_id=parser_id,
                    record_set="default",
                    binding_role="primary",
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
                db.add(binding)
            else:
                binding.parser_template_id = parser_id
                binding.status = "active"
                binding.updated_at = now
            db.flush()
            contract_id = str(contract.contract_id)

        change_policy_version_ids: list[str] = []
        canonical_catalog = load_canonical_catalog()
        requirement_profile = derive_asset_requirement_profile(
            result_mode=check_item.result_mode,
            assessment_mode=check_item.assessment_mode,
            canonical_rows=check_item.canonical_contract.get(
                "observations"
            )
            or [],
            canonical_catalog=canonical_catalog,
        )
        change_requirement = requirement_profile["requirements"][
            "change_policy"
        ]
        change_required = set(
            change_requirement.get("required_observation_types") or []
        )
        change_required.update(
            set(
                change_requirement.get(
                    "conditional_observation_types"
                )
                or []
            )
            & expected_observation_types
        )
        with scope() as db:
            for observation_type in sorted(change_required):
                (
                    db.query(ChangePolicyVersionRecord)
                    .filter(
                        ChangePolicyVersionRecord.observation_type
                        == observation_type,
                        ChangePolicyVersionRecord.status == "active",
                    )
                    .update(
                        {
                            "status": "deprecated",
                            "deprecated_at": now,
                        },
                        synchronize_session=False,
                    )
                )
                policy_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"netops/e2e-change-policy/{run_token}/"
                        f"{observation_type}",
                    )
                )
                version_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"netops/e2e-change-policy/{observation_type}/"
                        f"{run_token}",
                    )
                )
                policy_payload = {
                    "observation_type": observation_type,
                    "semantic_type": "state",
                    "compare_mode": "exact",
                }
                policy_digest = hashlib.sha256(
                    json.dumps(
                        policy_payload,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                db.add(
                    ChangePolicyRecord(
                        id=policy_id,
                        name=(
                            f"e2e:{run_token}:"
                            + observation_type.replace(".", "_")
                        ),
                        observation_type=observation_type,
                        description=(
                            "Transaction-scoped exact comparison policy."
                        ),
                        status="active",
                        current_version=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
                db.add(
                    ChangePolicyVersionRecord(
                        id=version_id,
                        policy_id=policy_id,
                        version=1,
                        observation_type=observation_type,
                        semantic_type="state",
                        compare_mode="exact",
                        policy_json=policy_payload,
                        validation_report_json={
                            "passed": True,
                            "source": "lixian-runtime-e2e",
                        },
                        status="active",
                        source_job_id=batch_id,
                        digest=policy_digest,
                        created_by="lixian-runtime-e2e",
                        created_at=now,
                        activated_at=now,
                    )
                )
                change_policy_version_ids.append(version_id)

        bundle_service = CheckItemRuntimeBundleService(scope)
        bundle_preview = bundle_service.preview_assembly(
            check_item_version_id=check_item.version_id,
            implementation_version_id=implementation.version_id,
            parser_template_ids=[parser_id],
            observation_contract_ids=[contract_id],
            change_policy_version_ids=change_policy_version_ids,
            event_rule_version_ids=[],
        )
        if bundle_preview.get("blockers"):
            raise AssertionError(
                "runtime bundle preview is blocked: "
                + json.dumps(
                    bundle_preview["blockers"],
                    ensure_ascii=False,
                )
            )
        bundle = bundle_service.assemble(
            check_item_version_id=check_item.version_id,
            implementation_version_id=implementation.version_id,
            parser_template_ids=[parser_id],
            observation_contract_ids=[contract_id],
            change_policy_version_ids=change_policy_version_ids,
            event_rule_version_ids=[],
            preview_digest=str(bundle_preview["preview_digest"]),
            actor="lixian-runtime-e2e",
        )
        bundle = bundle_service.activate(
            str(bundle["version_id"]),
            actor="lixian-runtime-e2e-approver",
            reason="transactional real-sample acceptance test",
        )

        device = {
            "device_id": device_id,
            "device_name": source["device_name"],
            "management_ip": source["management_ip"],
            "vendor": "H3C",
            "family": "Comware7",
            "model": PARSER_MODEL,
            "os_version": expected_full_version,
            "capabilities": [],
        }
        target_manifest = build_target_manifest(
            devices=(device,),
            selector={"source": "lixian", "file": source_file.name},
        )
        inventory_snapshot = build_inventory_snapshot((device,))
        with scope() as db:
            db.add(
                WorkflowRun(
                    id=workflow_run_id,
                    template_name="check-item-lixian-runtime-e2e",
                    source="device-management",
                    status="completed",
                    context={
                        "patrol_batch_id": batch_id,
                        "device_id": device_id,
                        "device_name": source["device_name"],
                        "management_ip": source["management_ip"],
                    },
                    current_step_index=1,
                    created_at=now,
                    updated_at=now,
                    completed_at=now,
                )
            )
            db.add(
                WorkflowStepRecord(
                    id=f"{workflow_run_id}-collect",
                    run_id=workflow_run_id,
                    step_index=0,
                    step_name="采集并解析检查项",
                    skill_name="device-patrol",
                    status="completed",
                    result={
                        "success": True,
                        "snapshot_run_id": run_id,
                    },
                    started_at=observed_at,
                    completed_at=observed_at,
                    created_at=now,
                )
            )
            db.add(
                PatrolBatchRecord(
                    patrol_batch_id=batch_id,
                    workflow_name="check-item-lixian-runtime-e2e",
                    launch_mode="workflow",
                    patrol_mode="production",
                    report_purpose=OFFLINE_HISTORICAL,
                    target_type="offline_import",
                    target_label="lixian real-sample runtime E2E",
                    selector_json={
                        "source": "lixian",
                        "file": source_file.name,
                    },
                    target_count=1,
                    launched_count=1,
                    skipped_count=0,
                    target_manifest_digest=target_manifest["digest"],
                    inventory_snapshot_digest=inventory_snapshot["digest"],
                    save_baseline=False,
                    dispatch_status="completed",
                    dispatch_requested_at=now,
                    dispatch_started_at=now,
                    dispatch_completed_at=now,
                    report_status="not_requested",
                    archived=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.flush()
            db.add(
                PatrolBatchDeviceRecord(
                    id=str(uuid.uuid4()),
                    patrol_batch_id=batch_id,
                    device_id=device_id,
                    device_name=source["device_name"],
                    management_ip=source["management_ip"],
                    selection_status="launched",
                    preflight_status="ready",
                    dispatch_status="dispatched",
                    dispatch_attempts=1,
                    dispatched_at=now,
                    workflow_run_id=workflow_run_id,
                    created_at=now,
                    updated_at=now,
                )
            )

        request = EffectivePlanRequest(
            patrol_batch_id=batch_id,
            devices=(device,),
            check_item_ids=(CHECK_ITEM_ID,),
            target_manifest_digest=target_manifest["digest"],
            inventory_snapshot_digest=inventory_snapshot["digest"],
            current_observed_at=observed_at,
            actor="lixian-runtime-e2e",
        )
        plan = CheckItemEffectivePlanService(
            scope,
            catalog=catalog,
        ).freeze(request)
        if len(plan.executions) != 1 or len(plan.collection_actions) != 1:
            raise AssertionError(
                "device.version must freeze one execution and one collection action"
            )
        if plan.asset_gaps:
            raise AssertionError(
                "temporary compatible RuntimeBundle was not selected"
            )
        execution = plan.executions[0]
        action = plan.collection_actions[0]
        if action["canonical_command"] != CANONICAL_COMMAND:
            raise AssertionError("effective plan selected the wrong command")

        with scope() as db:
            db.add(
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
                    run_purpose="scheduled_patrol",
                    logical_run_id=run_id,
                    attempt_number=0,
                    owner_generation=1,
                    device_count=1,
                    command_count=1,
                    started_at=observed_at,
                    completed_at=observed_at,
                    metadata_json={
                        "source_file": str(source_file),
                        "source_sha256": source["source_sha256"],
                        "transactional_e2e": True,
                    },
                    created_at=now,
                    updated_at=now,
                )
            )
            db.flush()
            snapshot = CommandSnapshot(
                id=snapshot_id,
                run_id=run_id,
                device_id=device_id,
                execution_item_id=str(action["id"]),
                collection_action_id=str(action["id"]),
                implementation_version_id=implementation.version_id,
                check_item_ids_json=[CHECK_ITEM_ID],
                device_name=source["device_name"],
                management_ip=source["management_ip"],
                vendor="H3C",
                model=PARSER_MODEL,
                command=str(block.command),
                canonical_command=CANONICAL_COMMAND,
                parser_template_id=parser_id,
                parser_version=parser_version,
                contract_version=int(contract.version),
                observed_at=observed_at,
                structured_json=parsed_records,
                raw_text=str(block.raw_output),
                raw_text_hash=hashlib.sha256(
                    str(block.raw_output).encode("utf-8")
                ).hexdigest(),
                parse_status="parsed",
                output_status="success",
                snapshot_kind="full",
                source_file_hash=source["source_sha256"],
                start_line=int(block.start_line),
                end_line=int(block.end_line),
                metadata_json={
                    "source_file": str(source_file),
                    "check_item_bindings": [
                        {
                            "check_item_version_id": check_item.version_id,
                            "implementation_version_id": (
                                implementation.version_id
                            ),
                        }
                    ],
                    "selected_check_item_bindings": [
                        {
                            "check_item_version_id": check_item.version_id,
                            "implementation_version_id": (
                                implementation.version_id
                            ),
                            "runtime_bundle_version_id": bundle["version_id"],
                        }
                    ],
                },
                created_at=now,
            )
            db.add(snapshot)
            db.flush()
            snapshot_payload = snapshot.to_dict()
            snapshot_payload["source_type"] = "cli"
            domain_observations = observations_from_snapshot(
                snapshot_payload,
                mode="production",
                source="offline_file",
                dataset_id=None,
                _contract_override=contract.to_validation_dict(),
            )
            canonical_observations = [
                item
                for item in domain_observations
                if item.observation_type in expected_observation_types
            ]
            if {
                item.observation_type for item in canonical_observations
            } != expected_observation_types:
                raise AssertionError(
                    "canonical device.version observations are incomplete"
                )
            software_values = {
                str(item.value)
                for item in canonical_observations
                if item.observation_type == "device.software.version"
            }
            if software_values != {expected_full_version}:
                raise AssertionError(
                    "canonical software version lost the Release component"
                )
            StateRepository().save_observations_in_session(
                db,
                canonical_observations,
                update_current_state=False,
            )
            action_row = db.get(
                CheckItemCollectionActionRecord,
                str(action["id"]),
            )
            action_row.status = "completed"
            action_row.started_at = observed_at
            action_row.completed_at = observed_at
            action_row.updated_at = now

        finalized = CheckItemRunFinalizer(scope).finalize(
            patrol_batch_id=batch_id,
            run_id=run_id,
            device_id=device_id,
        )
        if not finalized["success"]:
            raise AssertionError(
                "check-item finalizer failed: "
                + json.dumps(finalized["failures"], ensure_ascii=False)
            )

        manifest_service = ReportManifestService(scope)
        manifest = manifest_service.build(
            batch_id,
            generated_by="lixian-runtime-e2e",
        )
        if manifest["status"] != "final":
            raise AssertionError(
                "real-sample report did not become final: "
                + ",".join(
                    manifest["source_facts_json"]["readiness"][
                        "reason_codes"
                    ]
                )
            )
        report_view = build_check_item_report_view(manifest)
        materialized = CheckItemReportMaterializer(
            manifest_service=manifest_service,
        ).materialize(
            batch_id,
            generated_by="lixian-runtime-e2e",
        )
        manifest = materialized["manifest"]
        uploaded_report_prefix = (
            f"reports/patrol/{batch_id}/{manifest['source_digest']}"
        )
        artifacts = {
            name: load_attached_report_artifact(manifest, name)
            for name in materialized["artifact_object_keys"]
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        artifact_paths: dict[str, str] = {}
        for name, artifact in artifacts.items():
            path = output_dir / artifact.filename
            path.write_bytes(artifact.content)
            artifact_paths[name] = str(path.resolve())
        content_review_service = ReportContentReviewService(scope)
        content_review = content_review_service.start(
            str(manifest["id"]),
            created_by="lixian-runtime-e2e-content-reviewer",
        )
        while int(content_review.get("pending_item_count") or 0) > 0:
            pending_page = content_review_service.list_items(
                str(manifest["id"]),
                page=1,
                page_size=200,
                review_status="pending",
            )
            pending_ids = [
                str(item["review_item_id"])
                for item in pending_page["items"]
            ]
            if not pending_ids:
                raise AssertionError(
                    "content review reports pending items but none are readable"
                )
            content_review = content_review_service.decide_items(
                str(manifest["id"]),
                review_item_ids=pending_ids,
                decision="accepted",
                reviewed_by="lixian-runtime-e2e-content-reviewer",
                review_comment=(
                    "Transaction-scoped review of every required and sampled fact."
                ),
            )
        content_acceptance = content_review_service.accept(
            str(manifest["id"]),
            accepted_by="lixian-runtime-e2e-content-reviewer",
            acceptance_comment=(
                "Transaction-scoped acceptance of content, evidence and "
                "action wording."
            ),
            confirmations=REQUIRED_CONTENT_CONFIRMATIONS,
            verified_artifact_sha256=dict(
                manifest["artifact_sha256_json"]
            ),
            accepted_at=now,
        )
        release = ReportReleaseService(scope).release(
            str(manifest["id"]),
            signed_by="lixian-runtime-e2e-signer",
            signoff_comment=(
                "Transactional acceptance confirms facts, disclosure "
                "boundaries and exact artifact digests."
            ),
            disclosure_confirmations=[
                "facts_reviewed",
                "unknown_scope_disclosed",
                "artifact_digests_verified",
            ],
            verified_artifact_sha256=dict(
                manifest["artifact_sha256_json"]
            ),
            signed_at=now,
        )

        with scope() as db:
            assessment = (
                db.query(CheckAssessmentRecord)
                .filter(CheckAssessmentRecord.patrol_batch_id == batch_id)
                .one()
            )
            qualification = db.get(
                EvidenceQualificationRecord,
                assessment.evidence_qualification_id,
            )
            coverage_decision = db.get(
                CoverageDecisionRecord,
                assessment.coverage_decision_id,
            )
            if qualification is None or coverage_decision is None:
                raise AssertionError(
                    "final assessment is missing qualification or coverage "
                    "evidence"
                )
            observation_rows = (
                db.query(StateObservationRecord)
                .filter(StateObservationRecord.run_id == run_id)
                .order_by(StateObservationRecord.observation_type)
                .all()
            )
            result = {
                "schema_version": "check-item-lixian-runtime-e2e/v1",
                "generated_at": now.isoformat(),
                "rolled_back": True,
                "source": {
                    "file": str(source_file.resolve()),
                    "sha256": source["source_sha256"],
                    "device_name": source["device_name"],
                    "management_ip": source["management_ip"],
                    "command": CANONICAL_COMMAND,
                },
                "parser": {
                    "source_template_id": source_parser_id,
                    "source_version": source_parser_version,
                    "template_id": parser_id,
                    "version": parser_version,
                    "parsed_record_count": len(parsed_records),
                    "native_identity": {
                        "software_version": expected_version,
                        "release": expected_release,
                        "model": expected_model,
                    },
                },
                "runtime_bundle": {
                    "version_id": bundle["version_id"],
                    "digest": bundle["digest"],
                    "assembly_preview_digest": bundle_preview[
                        "preview_digest"
                    ],
                    "expected_observation_types": sorted(
                        bundle["compatibility"][
                            "expected_observation_types"
                        ]
                    ),
                },
                "facts": {
                    "check_item_id": CHECK_ITEM_ID,
                    "execution_id": execution["id"],
                    "observation_count": len(observation_rows),
                    "observations": [
                        {
                            "observation_type": row.observation_type,
                            "value": row.value_json,
                            "entity_key": row.entity_key,
                            "snapshot_id": row.snapshot_id,
                        }
                        for row in observation_rows
                    ],
                    "assessment_status": assessment.assessment_status,
                    "completeness_status": assessment.completeness_status,
                    "source_identity_status": (
                        qualification.source_identity_status
                    ),
                    "output_quality_status": (
                        qualification.output_quality_status
                    ),
                    "freshness_status": (
                        qualification.freshness_status
                    ),
                    "coverage_status": coverage_decision.coverage_status,
                    "coverage_basis": coverage_decision.coverage_basis,
                    "expected_entity_count": (
                        coverage_decision.expected_entity_count
                    ),
                    "observed_entity_count": (
                        coverage_decision.observed_entity_count
                    ),
                    "report_manifest_status": manifest["status"],
                    "report_purpose": report_view[
                        "report_purpose"
                    ],
                    "conclusion_dimensions": report_view[
                        "conclusion_dimensions"
                    ],
                    "report_authority_context": report_view[
                        "report_authority_context"
                    ],
                    "report_conclusion": report_view["conclusion"],
                    "source_facts_digest": manifest["source_digest"],
                },
                "artifacts": {
                    name: {
                        "path": artifact_paths[name],
                        "bytes": len(artifact.content),
                        "sha256": artifact.sha256,
                    }
                    for name, artifact in artifacts.items()
                },
                "release": {
                    "status": release["status"],
                    "release_digest": release["release_digest"],
                    "signed_by": release["signed_by"],
                    "content_review_id": release[
                        "content_review_id"
                    ],
                    "content_acceptance_digest": release[
                        "content_acceptance_digest"
                    ],
                    "artifact_sha256": dict(
                        release["artifact_sha256_json"]
                    ),
                },
                "content_acceptance": {
                    "id": content_acceptance["id"],
                    "acceptance_digest": content_acceptance[
                        "acceptance_digest"
                    ],
                    "accepted_by": content_acceptance["accepted_by"],
                    "accepted_item_count": content_acceptance[
                        "accepted_item_count"
                    ],
                },
            }
        if result["facts"]["assessment_status"] != "informational":
            raise AssertionError(
                "evidence check item must produce an informational assessment"
            )
        if result["facts"]["completeness_status"] != "complete":
            raise AssertionError(
                "real device.version evidence must be complete"
            )
        if (
            result["facts"]["report_purpose"]
            != OFFLINE_HISTORICAL
            or result["facts"]["conclusion_dimensions"]["authority"][
                "code"
            ]
            != "historical"
            or result["facts"]["report_conclusion"].get(
                "current_state_authoritative"
            )
            is not False
            or not report_authority_context_is_valid(
                result["facts"]["report_authority_context"],
                expected_purpose=OFFLINE_HISTORICAL,
            )
            or result["facts"]["report_authority_context"].get(
                "current_state_eligible"
            )
            is not False
        ):
            raise AssertionError(
                "offline historical evidence was promoted to current "
                "production authority"
            )
        if (
            result["facts"]["source_identity_status"] != "confirmed"
            or result["facts"]["output_quality_status"] != "complete"
            or result["facts"]["freshness_status"] != "stale"
            or result["facts"]["coverage_status"] != "complete"
        ):
            raise AssertionError(
                "historical device.version evidence was not classified "
                "truthfully: "
                + json.dumps(
                    {
                        key: result["facts"][key]
                        for key in (
                            "source_identity_status",
                            "output_quality_status",
                            "freshness_status",
                            "coverage_status",
                            "coverage_basis",
                            "expected_entity_count",
                            "observed_entity_count",
                        )
                    },
                    ensure_ascii=False,
                )
            )
        if result["release"]["status"] != "released":
            raise AssertionError(
                "formal artifact set did not produce an immutable release"
            )
        if (
            result["facts"]["report_conclusion"]["code"]
            != "historical_result"
        ):
            raise AssertionError(
                "offline evidence must be reported as a historical result"
            )
        summary_path = output_dir / "summary.json"
        summary_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result["summary_path"] = str(summary_path.resolve())

        outer_transaction.rollback()
    except Exception:
        if outer_transaction.is_active:
            outer_transaction.rollback()
        raise
    finally:
        connection.close()
        if uploaded_report_prefix:
            get_minio_storage().delete_objects_by_prefix(
                uploaded_report_prefix
            )

    after = _database_baseline(source_parser_id)
    result["database_baseline_before"] = before
    result["database_baseline_after"] = after
    result["database_unchanged"] = before == after
    if before != after:
        raise AssertionError(
            "transactional E2E changed production runtime assets"
        )
    Path(result["summary_path"]).write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a transactional real-sample check-item E2E"
    )
    parser.add_argument(
        "--source-file",
        type=Path,
        default=(
            ROOT
            / "lixian"
            / "Q2"
            / "XA-FOTIC-RT5620-1-10.34.2.219.txt"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / ".tmp" / "check-item-lixian-e2e",
    )
    args = parser.parse_args()
    result = run_e2e(
        source_file=args.source_file.resolve(),
        output_dir=args.output_dir.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

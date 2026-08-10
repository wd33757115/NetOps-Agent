#!/usr/bin/env python3
"""Exercise governed comparison selection with real CLI samples and PostgreSQL.

The script creates transaction-scoped implementations, contracts and runtime
bundles for H3C and Cisco device-version samples. All database writes are
rolled back, so this engineering replay cannot publish or alter production
assets. Historical captures are never represented as current network truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.check_items.catalog import (  # noqa: E402
    CheckItemCatalog,
    CheckItemCatalogRepository,
)
from src.core.check_items.planning import (  # noqa: E402
    CheckItemEffectivePlanService,
    EffectivePlanRequest,
    build_inventory_snapshot,
    build_target_manifest,
)
from src.core.check_items.run_finalizer import CheckItemRunFinalizer  # noqa: E402
from src.core.check_items.runtime_bundle import (  # noqa: E402
    CheckItemRuntimeBundleService,
)
from src.core.observation import ObservationContractRepository  # noqa: E402
from src.core.parser_generation.validation import (  # noqa: E402
    TextFSMValidationBackend,
)
from src.core.patrol.command_splitter import (  # noqa: E402
    infer_device_from_filename,
    split_cli_capture,
)
from src.core.state.normalizer import observations_from_snapshot  # noqa: E402
from src.core.state.repository import StateRepository  # noqa: E402
from src.infrastructure.db.models import (  # noqa: E402
    CheckAssessmentRecord,
    CheckExecutionRecord,
    CheckItemCollectionActionRecord,
    CheckItemImplementationLinkRecord,
    CheckItemRuntimeBundleVersionRecord,
    CollectionImplementationParserBindingRecord,
    CollectionImplementationRecord,
    CollectionImplementationVersionRecord,
    CommandSnapshot,
    ComparisonContextRecord,
    EvidenceQualificationRecord,
    NetworkDevice,
    ObservationContractRecord,
    ParserTemplate,
    PatrolBatchRecord,
    PatrolRun,
    StateObservationRecord,
)
from src.infrastructure.db.postgres import engine, get_db_session  # noqa: E402

CHECK_ITEM_ID = "device.version"
EXPECTED_OBSERVATION_TYPES = {
    "device.hardware.model",
    "device.software.version",
}


@dataclass(frozen=True, slots=True)
class SourcePair:
    key: str
    device_id: str
    vendor: str
    parser_vendor: str
    family: str
    model: str
    command: str
    base_implementation_id: str
    baseline_file: Path
    current_file: Path


@dataclass(frozen=True, slots=True)
class Capture:
    path: Path
    device_name: str
    management_ip: str | None
    block: Any
    source_sha256: str
    parsed_records: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class PreparedRuntime:
    source: SourcePair
    implementation: Any
    parser_id: str
    parser_version: str
    contract_id: str
    contract_version: int
    contract_validation: dict[str, Any]
    bundle: dict[str, Any]
    baseline: Capture
    current: Capture


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


def _decode_cli_capture(raw_bytes: bytes) -> str:
    for encoding in (
        "utf-8-sig",
        "utf-8",
        "utf-16",
        "utf-16-le",
        "utf-16-be",
        "gb18030",
        "gbk",
    ):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def _database_baseline() -> dict[str, Any]:
    with get_db_session() as db:
        return {
            "active_bundle_ids": sorted(
                str(row[0])
                for row in db.query(CheckItemRuntimeBundleVersionRecord.id)
                .filter(CheckItemRuntimeBundleVersionRecord.status == "active")
                .all()
            ),
            "active_contract_ids": sorted(
                str(row[0])
                for row in db.query(ObservationContractRecord.id)
                .filter(ObservationContractRecord.status == "active")
                .all()
            ),
            "execution_count": int(db.query(CheckExecutionRecord).count()),
            "assessment_count": int(db.query(CheckAssessmentRecord).count()),
            "comparison_count": int(db.query(ComparisonContextRecord).count()),
        }


def _load_capture(
    db: Any,
    source: SourcePair,
    path: Path,
) -> Capture:
    raw_bytes = path.read_bytes()
    raw_capture = _decode_cli_capture(raw_bytes)
    device_name, management_ip = infer_device_from_filename(path)
    matches = [
        block
        for block in split_cli_capture(
            raw_capture,
            device_name=device_name,
        )
        if block.command_canonical == source.command
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"{path} must contain one {source.command!r} block; got {len(matches)}"
        )
    parser = (
        db.query(ParserTemplate)
        .filter(
            ParserTemplate.status == "active",
            ParserTemplate.vendor == source.parser_vendor,
            ParserTemplate.model == source.model,
            ParserTemplate.canonical_command == source.command,
        )
        .one_or_none()
    )
    if parser is None:
        raise AssertionError(
            f"active Parser missing: {source.parser_vendor}/{source.model}/"
            f"{source.command}"
        )
    records = TextFSMValidationBackend().parse(
        str(parser.template_text or ""),
        str(matches[0].raw_output),
    )
    if not records:
        raise AssertionError(f"active Parser produced no records: {path}")
    return Capture(
        path=path,
        device_name=str(device_name or matches[0].device_name),
        management_ip=management_ip,
        block=matches[0],
        source_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        parsed_records=tuple(dict(item) for item in records),
    )


def _contract_validation(
    source: SourcePair,
    records: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    emitted = {str(field) for record in records for field in record}
    if source.vendor == "H3C":
        required = {"software_version", "release", "model"}
        state_semantics = {
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
        }
    else:
        required = {"software_nxos_version", "hardware_model"}
        state_semantics = {
            "software_nxos_version": {
                "observation_type": "device.software.version",
                "semantic_type": "state",
            },
            "hardware_model": {
                "observation_type": "device.hardware.model",
                "semantic_type": "state",
            },
        }
    missing = sorted(required - emitted)
    if missing:
        raise AssertionError(
            f"Parser output lacks canonical source fields for {source.key}: {missing}"
        )
    return {
        "entity_type": "device",
        "primary_keys": [],
        "canonical_entity_type": "device",
        "entity_identity_fields": ["device_id"],
        "entity_dimensions": {"device_id": {"context": "device_id"}},
        "required_fields": sorted(required),
        "optional_fields": sorted(emitted - required),
        "validators": {
            field: {"type": "text"} for field in sorted(required)
        },
        "ignore_fields": sorted(emitted - set(state_semantics)),
        "allow_empty": False,
        "empty_patterns": [],
        "state_semantics": state_semantics,
    }


def _create_model_implementation(
    scope: Any,
    *,
    catalog: CheckItemCatalog,
    check_item: Any,
    source: SourcePair,
    token: str,
    now: datetime,
) -> tuple[CheckItemCatalog, Any]:
    base = next(
        (
            item
            for item in catalog.implementations
            if item.id == source.base_implementation_id
        ),
        None,
    )
    if base is None:
        raise AssertionError(
            f"catalog implementation missing: {source.base_implementation_id}"
        )
    implementation_id = f"scenario39-{source.key}-{token}"
    version_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"netops/scenario39/implementation/{implementation_id}/v1",
        )
    )
    payload = {
        "id": implementation_id,
        "version": 1,
        "match": {
            "vendor": source.vendor,
            "family": source.family,
            "model": source.model,
        },
        "commands": [{"command": source.command, "priority": 100}],
        "collector": base.collector,
        "parser_requirement": base.parser_requirement,
        "template_scope": "model",
        "output_quality_policy": dict(base.output_quality_policy),
        "selection_priority": 1000,
        "status": "active",
    }
    digest = _digest(payload)
    with scope() as db:
        db.add(
            CollectionImplementationRecord(
                id=implementation_id,
                name=f"Scenario 39 {source.vendor} {source.model}",
                description="Transaction-scoped compatibility implementation.",
                status="active",
                current_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            CollectionImplementationVersionRecord(
                id=version_id,
                implementation_id=implementation_id,
                version=1,
                collector=base.collector,
                match_json=dict(payload["match"]),
                commands_json=list(payload["commands"]),
                parser_requirement=base.parser_requirement,
                template_scope="model",
                output_quality_policy_json=dict(base.output_quality_policy),
                selection_priority=1000,
                status="active",
                digest=digest,
                created_by="scenario39-e2e",
                created_at=now,
                activated_at=now,
            )
        )
        db.flush()
        db.add(
            CheckItemImplementationLinkRecord(
                id=str(uuid.uuid4()),
                check_item_version_id=check_item.version_id,
                implementation_version_id=version_id,
                priority=1000,
                required=True,
                created_at=now,
            )
        )
    implementation = replace(
        base,
        id=implementation_id,
        version=1,
        name=f"Scenario 39 {source.vendor} {source.model}",
        description="Transaction-scoped compatibility implementation.",
        match=dict(payload["match"]),
        template_scope="model",
        selection_priority=1000,
        digest=digest,
        version_id=version_id,
    )
    return (
        CheckItemCatalog(
            catalog_version=catalog.catalog_version,
            check_items=catalog.check_items,
            implementations=(*catalog.implementations, implementation),
        ),
        implementation,
    )


def _prepare_runtime(
    scope: Any,
    *,
    catalog: CheckItemCatalog,
    check_item: Any,
    source: SourcePair,
    token: str,
    now: datetime,
) -> tuple[CheckItemCatalog, PreparedRuntime]:
    catalog, implementation = _create_model_implementation(
        scope,
        catalog=catalog,
        check_item=check_item,
        source=source,
        token=token,
        now=now,
    )
    with scope() as db:
        baseline = _load_capture(db, source, source.baseline_file)
        current = _load_capture(db, source, source.current_file)
        parser = (
            db.query(ParserTemplate)
            .filter(
                ParserTemplate.status == "active",
                ParserTemplate.vendor == source.parser_vendor,
                ParserTemplate.model == source.model,
                ParserTemplate.canonical_command == source.command,
            )
            .one()
        )
        parser_id = str(parser.id)
        parser_version = str(parser.version)
        validation = _contract_validation(source, baseline.parsed_records)
        existing = (
            db.query(ObservationContractRecord)
            .filter(
                ObservationContractRecord.source_type == "cli",
                ObservationContractRecord.schema_id == parser_id,
            )
            .order_by(ObservationContractRecord.version.desc())
            .first()
        )
        contract = ObservationContractRepository().upsert_contract_row(
            db,
            source_type="cli",
            schema_id=parser_id,
            schema_ref=f"postgresql://parser-template/{parser_id}",
            category="device",
            validation=validation,
            version=int(existing.version or 0) + 1 if existing else 1,
            source_template_id=parser_id,
            source_job_id=f"scenario39-{token}",
            approval={
                "kind": "transactional_engineering_replay",
                "actor": "scenario39-e2e",
            },
            status="active",
            compatibility={
                "scope": "transactional_e2e",
                "production_activation": False,
            },
        )
        db.add(
            CollectionImplementationParserBindingRecord(
                id=str(uuid.uuid4()),
                implementation_version_id=implementation.version_id,
                canonical_command=source.command,
                parser_template_id=parser_id,
                record_set="default",
                binding_role="primary",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()
        contract_id = str(contract.contract_id)
        contract_version = int(contract.version)

    bundle_service = CheckItemRuntimeBundleService(scope)
    preview = bundle_service.preview_assembly(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
        parser_template_ids=[parser_id],
        observation_contract_ids=[contract_id],
        change_policy_version_ids=[],
        event_rule_version_ids=[],
    )
    if preview.get("blockers"):
        raise AssertionError(
            "runtime bundle preview blocked: "
            + json.dumps(preview["blockers"], ensure_ascii=False)
        )
    bundle = bundle_service.assemble(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
        parser_template_ids=[parser_id],
        observation_contract_ids=[contract_id],
        change_policy_version_ids=[],
        event_rule_version_ids=[],
        preview_digest=str(preview["preview_digest"]),
        actor="scenario39-e2e-builder",
    )
    bundle = bundle_service.activate(
        str(bundle["version_id"]),
        actor="scenario39-e2e-approver",
        reason="transaction-scoped comparison-context verification",
    )
    return (
        catalog,
        PreparedRuntime(
            source=source,
            implementation=implementation,
            parser_id=parser_id,
            parser_version=parser_version,
            contract_id=contract_id,
            contract_version=contract_version,
            contract_validation=validation,
            bundle=bundle,
            baseline=baseline,
            current=current,
        ),
    )


def _reuse_runtime_for_source(
    scope: Any,
    *,
    runtime: PreparedRuntime,
    source: SourcePair,
) -> PreparedRuntime:
    """Reuse one approved compatibility implementation for another device capture."""

    with scope() as db:
        baseline = _load_capture(db, source, source.baseline_file)
        current = _load_capture(db, source, source.current_file)
    return replace(
        runtime,
        source=source,
        baseline=baseline,
        current=current,
    )


def _device(runtime: PreparedRuntime) -> dict[str, Any]:
    return {
        "device_id": runtime.source.device_id,
        "device_name": runtime.baseline.device_name,
        "management_ip": runtime.baseline.management_ip,
        "vendor": runtime.source.vendor,
        "family": runtime.source.family,
        "model": runtime.source.model,
        "capabilities": [],
    }


def _seed_inventory(
    scope: Any,
    *,
    runtimes: tuple[PreparedRuntime, ...],
    now: datetime,
) -> None:
    with scope() as db:
        for runtime in runtimes:
            device = _device(runtime)
            db.add(
                NetworkDevice(
                    id=str(device["device_id"]),
                    device_name=str(device["device_name"]),
                    management_ip=str(device["management_ip"]),
                    vendor=str(device["vendor"]),
                    model=str(device["model"]),
                    family=str(device["family"]),
                    site="transactional-comparison-context",
                    role="validation",
                    status="active",
                    attributes_json={
                        "source": "historical_cli_replay",
                        "production_authoritative": False,
                    },
                    created_at=now,
                    updated_at=now,
                )
            )


def _add_batch(
    scope: Any,
    *,
    batch_id: str,
    devices: tuple[dict[str, Any], ...],
    target_manifest: dict[str, Any],
    inventory_snapshot: dict[str, Any],
    now: datetime,
) -> None:
    with scope() as db:
        db.add(
            PatrolBatchRecord(
                patrol_batch_id=batch_id,
                workflow_name="scenario39-comparison-context-e2e",
                launch_mode="workflow",
                patrol_mode="production",
                report_purpose="production_current",
                target_type="engineering_replay",
                target_label="transactional scenario 39 replay",
                selector_json={
                    "device_ids": [item["device_id"] for item in devices],
                    "production_network_claim": False,
                },
                target_count=len(devices),
                launched_count=len(devices),
                skipped_count=0,
                target_manifest_digest=target_manifest["digest"],
                inventory_snapshot_digest=inventory_snapshot["digest"],
                save_baseline=False,
                dispatch_status="completed",
                report_status="not_requested",
                archived=False,
                created_at=now,
                updated_at=now,
            )
        )


def _seed_cycle(
    scope: Any,
    *,
    catalog: CheckItemCatalog,
    runtimes: tuple[PreparedRuntime, ...],
    token: str,
    period: str,
    observed_at: datetime,
    now: datetime,
) -> dict[str, Any]:
    batch_id = f"scenario39-{period}-{token}-{uuid.uuid4().hex[:8]}"
    devices = tuple(_device(runtime) for runtime in runtimes)
    target_manifest = build_target_manifest(
        devices=devices,
        selector={"device_ids": [item["device_id"] for item in devices]},
    )
    inventory_snapshot = build_inventory_snapshot(devices)
    _add_batch(
        scope,
        batch_id=batch_id,
        devices=devices,
        target_manifest=target_manifest,
        inventory_snapshot=inventory_snapshot,
        now=now,
    )
    plan = CheckItemEffectivePlanService(scope, catalog=catalog).freeze(
        EffectivePlanRequest(
            patrol_batch_id=batch_id,
            devices=devices,
            check_item_ids=(CHECK_ITEM_ID,),
            target_manifest_digest=target_manifest["digest"],
            inventory_snapshot_digest=inventory_snapshot["digest"],
            current_observed_at=observed_at,
            actor="scenario39-e2e",
            report_purpose="production_current",
        )
    )
    if plan.asset_gaps:
        raise AssertionError(
            "seed cycle has asset gaps: "
            + json.dumps(plan.asset_gaps, ensure_ascii=False, default=str)
        )
    finalized: list[dict[str, Any]] = []
    for runtime in runtimes:
        source = runtime.baseline if period.startswith("baseline") else runtime.current
        action = next(
            item
            for item in plan.collection_actions
            if str(item.get("device_id")) == runtime.source.device_id
        )
        execution = next(
            item
            for item in plan.executions
            if str(item.get("device_id")) == runtime.source.device_id
        )
        run_id = f"scenario39-run-{period}-{runtime.source.key}-{token}-{uuid.uuid4().hex[:6]}"
        snapshot_id = f"s39-{uuid.uuid4().hex}"
        with scope() as db:
            db.add(
                PatrolRun(
                    id=run_id,
                    mode="production",
                    source="engineering_historical_replay",
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
                        "patrol_batch_id": batch_id,
                        "source_file": str(source.path),
                        "source_sha256": source.source_sha256,
                        "production_network_claim": False,
                        "source_observed_at_overridden": True,
                    },
                    created_at=now,
                    updated_at=now,
                )
            )
            db.flush()
            snapshot = CommandSnapshot(
                id=snapshot_id,
                run_id=run_id,
                device_id=runtime.source.device_id,
                inventory_device_id=runtime.source.device_id,
                execution_item_id=str(action["id"]),
                collection_action_id=str(action["id"]),
                implementation_version_id=runtime.implementation.version_id,
                check_item_ids_json=[CHECK_ITEM_ID],
                device_name=source.device_name,
                management_ip=source.management_ip,
                vendor=runtime.source.vendor,
                model=runtime.source.model,
                command=str(source.block.command),
                canonical_command=runtime.source.command,
                parser_template_id=runtime.parser_id,
                parser_version=runtime.parser_version,
                contract_version=runtime.contract_version,
                observed_at=observed_at,
                structured_json=list(source.parsed_records),
                raw_text=str(source.block.raw_output),
                raw_text_hash=hashlib.sha256(
                    str(source.block.raw_output).encode("utf-8")
                ).hexdigest(),
                parse_status="parsed",
                output_status="success",
                snapshot_kind="full",
                source_file_hash=source.source_sha256,
                start_line=int(source.block.start_line),
                end_line=int(source.block.end_line),
                metadata_json={
                    "source_file": str(source.path),
                    "production_network_claim": False,
                    "check_item_bindings": [
                        {
                            "check_item_version_id": execution[
                                "check_item_version_id"
                            ],
                            "implementation_version_id": runtime.implementation.version_id,
                        }
                    ],
                    "selected_check_item_bindings": [
                        {
                            "check_item_version_id": execution[
                                "check_item_version_id"
                            ],
                            "implementation_version_id": runtime.implementation.version_id,
                            "runtime_bundle_version_id": execution[
                                "runtime_bundle_version_id"
                            ],
                        }
                    ],
                },
                created_at=now,
            )
            db.add(snapshot)
            db.flush()
            payload = snapshot.to_dict()
            payload["source_type"] = "cli"
            observations = [
                item
                for item in observations_from_snapshot(
                    payload,
                    mode="production",
                    source="engineering_historical_replay",
                    dataset_id=None,
                    _contract_override=runtime.contract_validation,
                )
                if item.observation_type in EXPECTED_OBSERVATION_TYPES
            ]
            if {item.observation_type for item in observations} != (
                EXPECTED_OBSERVATION_TYPES
            ):
                raise AssertionError(
                    f"canonical observations incomplete for {runtime.source.key}"
                )
            StateRepository().save_observations_in_session(
                db,
                observations,
                update_current_state=False,
            )
            action_row = db.get(CheckItemCollectionActionRecord, str(action["id"]))
            action_row.status = "completed"
            action_row.started_at = observed_at
            action_row.completed_at = observed_at
            action_row.updated_at = now
        result = CheckItemRunFinalizer(scope).finalize(
            patrol_batch_id=batch_id,
            run_id=run_id,
            device_id=runtime.source.device_id,
        )
        if not result["success"]:
            raise AssertionError(
                "cycle finalization failed: "
                + json.dumps(result["failures"], ensure_ascii=False)
            )
        finalized.append(result)
    return {
        "batch_id": batch_id,
        "plan": plan,
        "finalized": finalized,
    }


def _preview(
    scope: Any,
    *,
    catalog: CheckItemCatalog,
    runtimes: tuple[PreparedRuntime, ...],
    observed_at: datetime,
    label: str,
) -> Any:
    devices = tuple(_device(item) for item in runtimes)
    target = build_target_manifest(
        devices=devices,
        selector={"device_ids": [item["device_id"] for item in devices]},
    )
    inventory = build_inventory_snapshot(devices)
    return CheckItemEffectivePlanService(scope, catalog=catalog).preview(
        EffectivePlanRequest(
            patrol_batch_id=f"scenario39-preview-{label}-{uuid.uuid4().hex[:8]}",
            devices=devices,
            check_item_ids=(CHECK_ITEM_ID,),
            target_manifest_digest=target["digest"],
            inventory_snapshot_digest=inventory["digest"],
            current_observed_at=observed_at,
            actor="scenario39-e2e",
            report_purpose="production_current",
        )
    )


def _revise_contract_and_bundle(
    scope: Any,
    *,
    check_item: Any,
    runtime: PreparedRuntime,
    token: str,
    now: datetime,
) -> dict[str, Any]:
    revised_validation = json.loads(json.dumps(runtime.contract_validation))
    with scope() as db:
        current = db.get(ObservationContractRecord, runtime.contract_id)
        contract = ObservationContractRepository().upsert_contract_row(
            db,
            source_type="cli",
            schema_id=runtime.parser_id,
            schema_ref=f"postgresql://parser-template/{runtime.parser_id}",
            category="device",
            validation=revised_validation,
            version=int(current.version) + 1,
            source_template_id=runtime.parser_id,
            source_job_id=f"scenario39-drift-{token}",
            approval={
                "kind": "transactional_engineering_replay",
                "actor": "scenario39-e2e",
                "revision": 2,
            },
            status="active",
            compatibility={
                "scope": "transactional_e2e",
                "production_activation": False,
            },
        )
        db.flush()
        contract_id = str(contract.contract_id)
    service = CheckItemRuntimeBundleService(scope)
    preview = service.preview_assembly(
        check_item_version_id=check_item.version_id,
        implementation_version_id=runtime.implementation.version_id,
        parser_template_ids=[runtime.parser_id],
        observation_contract_ids=[contract_id],
        change_policy_version_ids=[],
        event_rule_version_ids=[],
    )
    if preview.get("blockers"):
        raise AssertionError(
            "revised runtime bundle blocked: "
            + json.dumps(preview["blockers"], ensure_ascii=False)
        )
    bundle = service.assemble(
        check_item_version_id=check_item.version_id,
        implementation_version_id=runtime.implementation.version_id,
        parser_template_ids=[runtime.parser_id],
        observation_contract_ids=[contract_id],
        change_policy_version_ids=[],
        event_rule_version_ids=[],
        preview_digest=str(preview["preview_digest"]),
        actor="scenario39-e2e-builder",
    )
    return service.activate(
        str(bundle["version_id"]),
        actor="scenario39-e2e-approver",
        reason="transaction-scoped contract drift verification",
    )


def _context_rows(plan: Any) -> list[dict[str, Any]]:
    return [
        {
            "device_id": item.device_id,
            "comparison_status": item.comparison_status,
            "selection_reason": item.selection_reason,
            "baseline_run_id": item.baseline_run_id,
            "baseline_snapshot_ids": list(item.baseline_snapshot_ids),
            "candidate_rejection_counts": dict(
                item.metadata.get("candidate_rejection_counts") or {}
            ),
            "candidate_execution_ids": list(
                item.metadata.get("candidate_execution_ids") or []
            ),
        }
        for item in plan.comparison_contexts
    ]


def run_e2e(
    *,
    sources: tuple[SourcePair, ...],
    output_dir: Path,
) -> dict[str, Any]:
    before = _database_baseline()
    now = datetime.now(timezone.utc)
    baseline_at = now - timedelta(hours=6)
    current_at = now - timedelta(hours=1)
    token = uuid.uuid4().hex[:12]
    connection = engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    )
    outer_transaction = connection.begin()
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

    result: dict[str, Any] = {
        "schema_version": "scenario39-comparison-context-e2e/v1",
        "production_network_claim": False,
        "source_observed_at_overridden": True,
        "database": "postgresql",
    }
    try:
        repository = CheckItemCatalogRepository(scope)
        repository.bootstrap(actor="scenario39-e2e")
        catalog = repository.load_active()
        check_item = catalog.check_item(CHECK_ITEM_ID)
        if check_item is None:
            raise AssertionError("active device.version check item is missing")

        source_by_key = {item.key: item for item in sources}
        prepared: dict[str, PreparedRuntime] = {}
        for source_key in ("h3c-primary", "cisco-primary"):
            source = source_by_key[source_key]
            catalog, runtime = _prepare_runtime(
                scope,
                catalog=catalog,
                check_item=check_item,
                source=source,
                token=token,
                now=now,
            )
            prepared[source.key] = runtime
        prepared["h3c-secondary"] = _reuse_runtime_for_source(
            scope,
            runtime=prepared["h3c-primary"],
            source=source_by_key["h3c-secondary"],
        )
        prepared["cisco-secondary"] = _reuse_runtime_for_source(
            scope,
            runtime=prepared["cisco-primary"],
            source=source_by_key["cisco-secondary"],
        )

        normal = (prepared["h3c-primary"], prepared["cisco-primary"])
        negative = (prepared["h3c-secondary"], prepared["cisco-secondary"])
        _seed_inventory(
            scope,
            runtimes=(*normal, *negative),
            now=now,
        )
        baseline = _seed_cycle(
            scope,
            catalog=catalog,
            runtimes=(*normal, *negative),
            token=token,
            period="baseline",
            observed_at=baseline_at,
            now=now,
        )
        baseline_contexts = _context_rows(baseline["plan"])
        if {
            item["comparison_status"] for item in baseline_contexts
        } != {"first_observation"}:
            raise AssertionError("first cycle must be first_observation")

        current = _seed_cycle(
            scope,
            catalog=catalog,
            runtimes=normal,
            token=token,
            period="current",
            observed_at=current_at,
            now=now,
        )
        current_contexts = _context_rows(current["plan"])
        if {
            item["comparison_status"] for item in current_contexts
        } != {"comparable"}:
            raise AssertionError(
                "second cycle did not auto-select qualified baselines: "
                + json.dumps(current_contexts, ensure_ascii=False)
            )

        duplicate = _seed_cycle(
            scope,
            catalog=catalog,
            runtimes=negative,
            token=token,
            period="baseline-duplicate",
            observed_at=baseline_at,
            now=now,
        )
        duplicate_contexts = _context_rows(duplicate["plan"])
        if {
            item["comparison_status"] for item in duplicate_contexts
        } != {"first_observation"}:
            raise AssertionError(
                "equal-time duplicate must not compare against a peer fact"
            )

        ambiguous_plan = _preview(
            scope,
            catalog=catalog,
            runtimes=negative,
            observed_at=current_at,
            label="ambiguous",
        )
        ambiguous_contexts = _context_rows(ambiguous_plan)
        if any(
            item["comparison_status"] != "baseline_missing"
            or item["selection_reason"]
            != "multiple_equally_recent_compatible_baselines"
            for item in ambiguous_contexts
        ):
            raise AssertionError(
                "equal-time baselines were not rejected as ambiguous: "
                + json.dumps(ambiguous_contexts, ensure_ascii=False)
            )

        stale_plan = _preview(
            scope,
            catalog=catalog,
            runtimes=normal,
            observed_at=current_at + timedelta(days=60),
            label="stale",
        )
        stale_contexts = _context_rows(stale_plan)
        if any(
            item["comparison_status"] != "baseline_stale"
            for item in stale_contexts
        ):
            raise AssertionError(
                "expired baselines were not rejected as stale: "
                + json.dumps(stale_contexts, ensure_ascii=False)
            )

        old_bundle_id = prepared["h3c-primary"].bundle["version_id"]
        new_bundle = _revise_contract_and_bundle(
            scope,
            check_item=check_item,
            runtime=prepared["h3c-primary"],
            token=token,
            now=now,
        )
        drift_plan = _preview(
            scope,
            catalog=catalog,
            runtimes=(prepared["h3c-primary"],),
            observed_at=now,
            label="asset-drift",
        )
        drift_contexts = _context_rows(drift_plan)
        if len(drift_contexts) != 1 or (
            drift_contexts[0]["comparison_status"]
            != "schema_incompatible"
        ):
            raise AssertionError(
                "runtime asset drift did not invalidate the fact contract: "
                + json.dumps(drift_contexts, ensure_ascii=False)
            )

        unknown_source = replace(
            sources[0],
            key="unknown-vendor",
            device_id=f"scenario39-unknown-{token}",
            vendor="Juniper",
            parser_vendor="juniper",
            family="Junos",
            model="MX",
        )
        unknown_runtime = replace(
            prepared["h3c-primary"],
            source=unknown_source,
        )
        zero_catalog = CheckItemCatalog(
            catalog_version=catalog.catalog_version,
            check_items=catalog.check_items,
            implementations=tuple(
                item
                for item in catalog.implementations
                if str(item.id).startswith("scenario39-")
            ),
        )
        zero_plan = _preview(
            scope,
            catalog=zero_catalog,
            runtimes=(unknown_runtime,),
            observed_at=now,
            label="zero-implementation",
        )
        if (
            len(zero_plan.executions) != 1
            or zero_plan.executions[0].get("execution_status") != "unsupported"
            or zero_plan.collection_actions
            or zero_plan.decisions[0].get("implementation_version_id") is not None
        ):
            raise AssertionError(
                "zero implementation did not fail closed: "
                + json.dumps(
                    {
                        "decisions": zero_plan.decisions,
                        "executions": zero_plan.executions,
                        "asset_gaps": zero_plan.asset_gaps,
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )

        h3c_implementation = prepared["h3c-primary"].implementation
        duplicate_implementation = replace(
            h3c_implementation,
            id=f"scenario39-duplicate-implementation-{token}",
            version_id=str(uuid.uuid4()),
            digest="f" * 64,
        )
        ambiguous_catalog = CheckItemCatalog(
            catalog_version=catalog.catalog_version,
            check_items=catalog.check_items,
            implementations=(
                *catalog.implementations,
                duplicate_implementation,
            ),
        )
        multi_plan = _preview(
            scope,
            catalog=ambiguous_catalog,
            runtimes=(prepared["h3c-primary"],),
            observed_at=now,
            label="multiple-implementations",
        )
        if (
            len(multi_plan.executions) != 1
            or multi_plan.executions[0].get("execution_status") != "unsupported"
            or multi_plan.collection_actions
            or "ambiguous_implementation"
            not in set(multi_plan.executions[0].get("reason_codes_json") or [])
        ):
            raise AssertionError(
                "multiple equal implementations did not fail closed: "
                + json.dumps(
                    {
                        "decisions": multi_plan.decisions,
                        "executions": multi_plan.executions,
                        "asset_gaps": multi_plan.asset_gaps,
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )

        with scope() as db:
            terminal_count = int(
                db.query(CheckExecutionRecord)
                .filter(
                    CheckExecutionRecord.patrol_batch_id.in_(
                        [
                            baseline["batch_id"],
                            current["batch_id"],
                            duplicate["batch_id"],
                        ]
                    ),
                    CheckExecutionRecord.execution_status == "completed",
                )
                .count()
            )
            qualified_count = int(
                db.query(EvidenceQualificationRecord)
                .join(
                    CheckExecutionRecord,
                    CheckExecutionRecord.id
                    == EvidenceQualificationRecord.check_execution_id,
                )
                .filter(
                    CheckExecutionRecord.patrol_batch_id.in_(
                        [
                            baseline["batch_id"],
                            current["batch_id"],
                            duplicate["batch_id"],
                        ]
                    )
                )
                .count()
            )
            observation_count = int(
                db.query(StateObservationRecord)
                .filter(
                    StateObservationRecord.source
                    == "engineering_historical_replay"
                )
                .count()
            )

        result.update(
            {
                "sources": [
                    {
                        "key": item.key,
                        "vendor": item.vendor,
                        "model": item.model,
                        "command": item.command,
                        "baseline_file": str(item.baseline_file),
                        "current_file": str(item.current_file),
                    }
                    for item in sources
                ],
                "baseline_contexts": baseline_contexts,
                "current_contexts": current_contexts,
                "ambiguous_contexts": ambiguous_contexts,
                "stale_contexts": stale_contexts,
                "asset_drift_contexts": drift_contexts,
                "asset_drift": {
                    "old_bundle_version_id": old_bundle_id,
                    "new_bundle_version_id": new_bundle["version_id"],
                },
                "zero_implementation_terminal_status": zero_plan.executions[0][
                    "execution_status"
                ],
                "multiple_implementation_terminal_status": multi_plan.executions[0][
                    "execution_status"
                ],
                "terminal_execution_count": terminal_count,
                "evidence_qualification_count": qualified_count,
                "state_observation_count": observation_count,
            }
        )
        outer_transaction.rollback()
    except Exception:
        if outer_transaction.is_active:
            outer_transaction.rollback()
        raise
    finally:
        connection.close()

    after = _database_baseline()
    result["database_baseline_before"] = before
    result["database_baseline_after"] = after
    result["database_unchanged"] = before == after
    if before != after:
        raise AssertionError("transactional scenario changed production data")
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    result["summary_path"] = str(summary_path.resolve())
    summary_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _default_sources() -> tuple[SourcePair, ...]:
    desktop_test = Path.home() / "Desktop" / "test"
    return (
        SourcePair(
            key="h3c-primary",
            device_id="scenario39-h3c-primary",
            vendor="H3C",
            parser_vendor="H3C",
            family="Comware7",
            model="MSR5620",
            command="display version",
            base_implementation_id="h3c-device-version",
            baseline_file=ROOT
            / "lixian"
            / "Q2"
            / "XA-FOTIC-RT5620-1-10.34.2.219.txt",
            current_file=ROOT
            / "lixian"
            / "Q3"
            / "XA-FOTIC-RT5620-1-10.34.2.219.txt",
        ),
        SourcePair(
            key="h3c-secondary",
            device_id="scenario39-h3c-secondary",
            vendor="H3C",
            parser_vendor="H3C",
            family="Comware7",
            model="MSR5620",
            command="display version",
            base_implementation_id="h3c-device-version",
            baseline_file=ROOT
            / "lixian"
            / "Q2"
            / "XA-FOTIC-RT5620-2-10.34.2.220.txt",
            current_file=ROOT
            / "lixian"
            / "Q3"
            / "XA-FOTIC-RT5620-2-10.34.2.220.txt",
        ),
        SourcePair(
            key="cisco-primary",
            device_id="scenario39-cisco-primary",
            vendor="Cisco",
            parser_vendor="cisco",
            family="NXOS",
            model="NEXUS9000",
            command="show version",
            base_implementation_id="cisco-device-version",
            baseline_file=desktop_test
            / "Q1"
            / "XA-FOTIC-Core-N9364C-1-10.34.2.2_2025-12-24_15-49-38.txt",
            current_file=desktop_test
            / "Q2"
            / "XA-FOTIC-Core-N9364C-1-10.34.2.2.txt",
        ),
        SourcePair(
            key="cisco-secondary",
            device_id="scenario39-cisco-secondary",
            vendor="Cisco",
            parser_vendor="cisco",
            family="NXOS",
            model="NEXUS9000",
            command="show version",
            base_implementation_id="cisco-device-version",
            baseline_file=desktop_test
            / "Q1"
            / "XA-FOTIC-Core-N9364C-2-10.34.2.3_2025-12-24_15-52-25.txt",
            current_file=desktop_test
            / "Q2"
            / "XA-FOTIC-Core-N9364C-2-10.34.2.3.txt",
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run transactional multi-vendor comparison-context E2E"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / ".tmp" / "scenario39-comparison-context-e2e",
    )
    args = parser.parse_args()
    sources = _default_sources()
    missing = [
        str(path)
        for item in sources
        for path in (item.baseline_file, item.current_file)
        if not path.exists()
    ]
    if missing:
        raise FileNotFoundError("scenario 39 source files missing: " + ", ".join(missing))
    result = run_e2e(
        sources=sources,
        output_dir=args.output_dir.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

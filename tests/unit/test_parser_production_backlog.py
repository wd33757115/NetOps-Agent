from __future__ import annotations

import uuid
from datetime import timedelta

from src.core.parser.repository import (
    ParserJobRepository,
    ParserRepository,
    output_signature,
    parser_job_asset_key,
)
from src.core.parser.time_utils import database_clocks
from src.core.patrol.repository import PatrolRepository
from src.infrastructure.db.models import (
    AuditLogRecord,
    ObservationContractRecord,
    ParserJobSample,
    ParserOutboxEvent,
    ParserTemplate,
    ParserTemplateJob,
    PatrolRun,
)
from src.infrastructure.db.postgres import get_db_session


def test_parser_job_asset_key_ignores_sample_and_failure_dimensions():
    first = parser_job_asset_key(
        vendor=" H3C ",
        profile="Comware7",
        command="DISPLAY   INTERFACE BRIEF",
    )
    second = parser_job_asset_key(
        vendor="h3c",
        profile="comware7",
        command="display interface brief",
    )

    assert first == second


def test_parser_job_asset_key_separates_models_for_the_same_command():
    first = parser_job_asset_key(
        vendor="H3C",
        profile="S5130S-52S-HI",
        command="display transceiver diagnosis interface",
    )
    second = parser_job_asset_key(
        vendor="H3C",
        profile="S5130S-54S-HI-G",
        command="display transceiver diagnosis interface",
    )

    assert first != second


def test_simple_parser_job_dispatch_accepts_one_confirmed_generation_sample():
    token = uuid.uuid4().hex
    run_id = f"simple-parser-{token}"
    model = f"S5130-{token}"
    parser = ParserJobRepository()
    patrol = PatrolRepository()
    patrol.create_run(run_id=run_id, mode="parser_validation", source="unit-test")
    job_id = ""
    job: dict = {}
    try:
        for index in range(1):
            snapshot_id, created = patrol.save_snapshot_idempotent(
                run_id=run_id,
                device_id=f"SW{index + 1}-{token}",
                device_name=f"SW{index + 1}",
                vendor="H3C",
                model=model,
                command="display interface brief",
                command_canonical="display interface brief",
                raw_text="Interface Link Protocol\nGE1/0/1 UP UP\n",
                parse_status="template_missing",
                execution_item_id=f"item-{index}-{token}",
                import_fingerprint=f"fingerprint-{index}-{token}",
            )
            assert created is True
            current_job_id = parser.create_candidate(
                job_type="CREATE_TEMPLATE",
                vendor="H3C",
                model=model,
                family=None,
                profile=model,
                command="display interface brief",
                snapshot_id=snapshot_id,
                device_id=f"SW{index + 1}-{token}",
                failure_code="template_missing",
            )
            job_id = job_id or current_job_id
            assert current_job_id == job_id

        parser.set_parser_spec(
            job_id,
            parser_spec={
                "entity": "interface",
                "primary_keys": ["interface"],
                "fields": {
                    "interface": {"required": True, "type": "interface_name"},
                    "status": {
                        "required": True,
                        "type": "enum",
                        "values": ["UP", "DOWN"],
                    },
                },
            },
            confirmed=True,
            actor="unit-test",
            reason="confirm expected interface records",
        )
        job = parser.get_job(job_id)
        assert job is not None
        assert job["generation_readiness"]["ready"] is False
        assert "sample_roles_unassigned" in job["generation_readiness"]["blockers"]
        assert parser.list_dispatchable_jobs(job_id=job_id) == []

        samples = job["samples"]
        expectation = {"record_count": 1, "primary_keys": [["GE1/0/1"]]}
        parser.set_sample_role(
            job_id,
            samples[0]["id"],
            role="generation",
            expectation=expectation,
            actor="unit-test",
            reason="use first device as generation evidence",
        )
        job = parser.get_job(job_id)
        assert job is not None
        assert job["generation_readiness"] == {
            "ready": True,
            "blockers": [],
            "warnings": ["independent_acceptance_missing"],
            "validation_mode": "generation_sample_only",
            "role_counts": {
                "generation": 1,
                "acceptance": 0,
                "negative": 0,
                "unassigned": 0,
            },
        }
        assert [item["id"] for item in parser.list_dispatchable_jobs(job_id=job_id)] == [
            job_id
        ]

        with get_db_session() as db:
            db.query(ParserTemplateJob).filter_by(id=job_id).update(
                {"status": "succeeded"},
                synchronize_session=False,
            )
        suggestion_task_id = f"suggestion-{token}"
        queued = parser.queue_parser_spec_suggestion(
            job_id,
            task_id=suggestion_task_id,
            actor="unit-test",
            reason="re-analyze a completed ParserSpec",
        )
        assert queued["status"] == "succeeded"
        assert (
            queued["agent_state_json"]["parser_spec_suggestion"]["status"]
            == "queued"
        )
        parser.record_parser_spec_suggestion(
            job_id,
            task_id=suggestion_task_id,
            status="ready",
            suggestion={
                "parser_spec": {
                    "entity": "interface_l3",
                    "primary_keys": ["interface"],
                    "fields": {
                        "interface": {
                            "required": True,
                            "type": "interface_name",
                        },
                        "status": {
                            "required": True,
                            "type": "enum",
                            "values": ["UP", "DOWN"],
                        },
                    },
                },
                "sample_suggestions": [
                    {
                        "sample_id": samples[0]["id"],
                        "recommended_role": "generation",
                    }
                ],
            },
            auto_adopt=True,
        )
        revised = parser.get_job(job_id)
        assert revised is not None
        assert revised["status"] == "failed"
        assert revised["failure_code"] == "parser_spec_revised"
        assert revised["parser_spec_json"]["entity"] == "interface_l3"
    finally:
        with get_db_session() as db:
            if job_id:
                db.query(AuditLogRecord).filter(
                    AuditLogRecord.resource_id.in_(
                        [job_id, *(sample.get("id") for sample in (job or {}).get("samples", []))]
                    )
                ).delete(synchronize_session=False)
                db.query(ParserOutboxEvent).filter_by(aggregate_id=job_id).delete(
                    synchronize_session=False
                )
                db.query(ParserJobSample).filter_by(job_id=job_id).delete(
                    synchronize_session=False
                )
                db.query(ParserTemplateJob).filter_by(id=job_id).delete(
                    synchronize_session=False
                )
            db.query(PatrolRun).filter_by(id=run_id).delete(synchronize_session=False)


def test_new_data_sample_inherits_a_safe_role_after_parser_spec_confirmation():
    token = uuid.uuid4().hex
    run_id = f"parser-role-{token}"
    model = f"N9K-{token}"
    parser = ParserJobRepository()
    patrol = PatrolRepository()
    patrol.create_run(run_id=run_id, mode="parser_validation", source="unit-test")
    job_id = ""
    try:
        first_snapshot_id, _ = patrol.save_snapshot_idempotent(
            run_id=run_id,
            device_id=f"SW1-{token}",
            device_name="SW1",
            vendor="Cisco",
            model=model,
            command="show inventory",
            command_canonical="show inventory",
            raw_text='NAME: "Chassis", DESCR: "Nexus chassis"\nPID: N9K, VID: V01, SN: ONE\n',
            parse_status="template_missing",
            execution_item_id=f"item-1-{token}",
            import_fingerprint=f"fingerprint-1-{token}",
        )
        job_id = parser.create_candidate(
            job_type="CREATE_TEMPLATE",
            vendor="Cisco",
            model=model,
            family=None,
            profile=model,
            command="show inventory",
            snapshot_id=first_snapshot_id,
            device_id=f"SW1-{token}",
            failure_code="template_missing",
        )
        parser.set_parser_spec(
            job_id,
            parser_spec={
                "entity": "inventory_item",
                "primary_keys": ["name"],
                "fields": {
                    "name": {"required": True, "type": "text"},
                    "serial": {"required": True, "type": "text"},
                },
            },
            confirmed=True,
            actor="unit-test",
            reason="confirm inventory semantic fields",
        )

        second_snapshot_id, _ = patrol.save_snapshot_idempotent(
            run_id=run_id,
            device_id=f"SW2-{token}",
            device_name="SW2",
            vendor="Cisco",
            model=model,
            command="show inventory",
            command_canonical="show inventory",
            raw_text='NAME: "Chassis", DESCR: "Nexus chassis"\nPID: N9K, VID: V01, SN: TWO\n',
            parse_status="template_missing",
            execution_item_id=f"item-2-{token}",
            import_fingerprint=f"fingerprint-2-{token}",
        )
        assert parser.create_candidate(
            job_type="CREATE_TEMPLATE",
            vendor="Cisco",
            model=model,
            family=None,
            profile=model,
            command="show inventory",
            snapshot_id=second_snapshot_id,
            device_id=f"SW2-{token}",
            failure_code="template_missing",
        ) == job_id

        job = parser.get_job(job_id)
        roles_by_device = {
            sample["device_id"]: sample["sample_role"] for sample in job["samples"]
        }
        assert roles_by_device[f"SW1-{token}"] == "unassigned"
        assert roles_by_device[f"SW2-{token}"] == "generation"
    finally:
        with get_db_session() as db:
            if job_id:
                sample_ids = [
                    row[0]
                    for row in db.query(ParserJobSample.id)
                    .filter(ParserJobSample.job_id == job_id)
                    .all()
                ]
                db.query(AuditLogRecord).filter(
                    AuditLogRecord.resource_id.in_([job_id, *sample_ids])
                ).delete(synchronize_session=False)
                db.query(ParserOutboxEvent).filter_by(aggregate_id=job_id).delete(
                    synchronize_session=False
                )
                db.query(ParserJobSample).filter_by(job_id=job_id).delete(
                    synchronize_session=False
                )
                db.query(ParserTemplateJob).filter_by(id=job_id).delete(
                    synchronize_session=False
                )
            db.query(PatrolRun).filter_by(id=run_id).delete(synchronize_session=False)


def test_duplicate_asset_cleanup_hard_deletes_history_without_merging():
    token = uuid.uuid4().hex
    historical_id = f"historical-{token}"
    keeper_id = f"keeper-{token}"
    historical_template_id = f"template-{token}"
    historical_contract_id = f"contract-{token}"
    direct_contract_id = f"direct-contract-{token}"
    active_template_id = f"active-template-{token}"
    active_contract_id = f"active-contract-{token}"
    identity = ("H3C", f"S6860-{token}", "display interface brief")
    try:
        with get_db_session() as db:
            db.add_all(
                [
                    ParserTemplateJob(
                        id=historical_id,
                        job_type="CREATE_TEMPLATE",
                        source_mode="parser_validation",
                        vendor=identity[0],
                        model=identity[1],
                        profile=identity[1],
                        canonical_command=identity[2],
                        output_signature="old-shape",
                        idempotency_key=f"legacy-old-{token}",
                        status="failed",
                    ),
                    ParserTemplateJob(
                        id=keeper_id,
                        job_type="REPAIR_TEMPLATE",
                        source_mode="parser_validation",
                        vendor=identity[0],
                        model=identity[1],
                        profile=identity[1],
                        canonical_command=identity[2],
                        output_signature="new-shape",
                        idempotency_key=f"legacy-new-{token}",
                        status="succeeded",
                    ),
                    ParserOutboxEvent(
                        id=f"outbox-old-{token}",
                        event_type="parser.candidate.discovered",
                        aggregate_id=historical_id,
                        payload_json={"job_id": historical_id},
                    ),
                    ParserOutboxEvent(
                        id=f"outbox-new-{token}",
                        event_type="parser.candidate.discovered",
                        aggregate_id=keeper_id,
                        payload_json={"job_id": keeper_id},
                    ),
                    ParserTemplate(
                        id=historical_template_id,
                        vendor=identity[0],
                        model=identity[1],
                        profile=identity[1],
                        canonical_command=identity[2],
                        category="interface",
                        version=f"candidate-{token}",
                        asset_path=f".tmp/{historical_template_id}.textfsm",
                        sha256=token,
                        status="pending_approval",
                        source_job_id=historical_id,
                    ),
                    ParserTemplate(
                        id=active_template_id,
                        vendor=identity[0],
                        model=identity[1],
                        profile=identity[1],
                        canonical_command=identity[2],
                        category="interface",
                        version=f"active-{token}",
                        asset_path=f".tmp/{active_template_id}.textfsm",
                        sha256=f"active-{token}",
                        status="active",
                        source_job_id=historical_id,
                    ),
                ]
            )
            db.flush()
            db.add_all(
                [
                    ObservationContractRecord(
                        id=historical_contract_id,
                        source_type="textfsm",
                        schema_id=f"schema-{token}",
                        source_template_id=historical_template_id,
                        entity_type="interface",
                        primary_keys_json=["interface"],
                        required_fields_json=["interface", "status"],
                        optional_fields_json=[],
                        contract_sha256=token,
                        version=1,
                        status="draft",
                        source_job_id=historical_id,
                    ),
                    ObservationContractRecord(
                        id=direct_contract_id,
                        source_type="textfsm",
                        schema_id=f"direct-schema-{token}",
                        entity_type="interface",
                        primary_keys_json=["interface"],
                        required_fields_json=["interface", "status"],
                        optional_fields_json=[],
                        contract_sha256=f"direct-{token}",
                        version=1,
                        status="draft",
                        source_job_id=historical_id,
                    ),
                    ObservationContractRecord(
                        id=active_contract_id,
                        source_type="textfsm",
                        schema_id=f"active-schema-{token}",
                        source_template_id=active_template_id,
                        entity_type="interface",
                        primary_keys_json=["interface"],
                        required_fields_json=["interface", "status"],
                        optional_fields_json=[],
                        contract_sha256=f"active-{token}",
                        version=1,
                        status="active",
                        source_job_id=historical_id,
                    ),
                ]
            )

        result = ParserJobRepository().purge_duplicate_asset_jobs(
            dry_run=False,
            only_identity=identity,
        )

        assert result["duplicate_group_count"] == 1
        assert result["deleted_job_count"] == 1
        assert result["migrated_sample_count"] == 0
        assert result["deletion_totals"]["deleted_outbox_events"] == 1
        assert result["deletion_totals"]["deleted_templates"] == 1
        assert result["deletion_totals"]["deleted_contracts"] == 2
        assert result["deletion_totals"]["detached_active_templates"] == 1
        with get_db_session() as db:
            assert db.get(ParserTemplateJob, historical_id) is None
            assert db.get(ParserTemplate, historical_template_id) is None
            assert db.get(ObservationContractRecord, historical_contract_id) is None
            assert db.get(ObservationContractRecord, direct_contract_id) is None
            active_template = db.get(ParserTemplate, active_template_id)
            assert active_template is not None
            assert active_template.source_job_id is None
            active_contract = db.get(ObservationContractRecord, active_contract_id)
            assert active_contract is not None
            assert active_contract.source_job_id is None
            assert active_contract.source_template_id == active_template_id
            keeper = db.get(ParserTemplateJob, keeper_id)
            assert keeper is not None
            assert keeper.idempotency_key == parser_job_asset_key(
                vendor=identity[0],
                profile=identity[1],
                command=identity[2],
            )
            assert (
                db.query(ParserOutboxEvent)
                .filter(ParserOutboxEvent.aggregate_id == keeper_id)
                .count()
                == 1
            )
    finally:
        with get_db_session() as db:
            db.query(ObservationContractRecord).filter(
                ObservationContractRecord.id.in_(
                    [historical_contract_id, direct_contract_id, active_contract_id]
                )
            ).delete(synchronize_session=False)
            db.query(ParserTemplate).filter(
                ParserTemplate.id.in_([historical_template_id, active_template_id])
            ).delete(synchronize_session=False)
            db.query(ParserOutboxEvent).filter(
                ParserOutboxEvent.aggregate_id.in_([historical_id, keeper_id])
            ).delete(synchronize_session=False)
            db.query(ParserTemplateJob).filter(
                ParserTemplateJob.id.in_([historical_id, keeper_id])
            ).delete(synchronize_session=False)


def test_terminal_parser_job_is_reopened_instead_of_creating_asset_history():
    token = uuid.uuid4().hex
    run_id = f"parser-reopen-{token}"
    patrol = PatrolRepository()
    parser = ParserJobRepository()
    patrol.create_run(run_id=run_id, mode="parser_validation", source="unit-test")
    job_id = ""
    try:
        first_snapshot_id, _ = patrol.save_snapshot_idempotent(
            run_id=run_id,
            device_id="SW1",
            device_name="SW1",
            vendor="H3C",
            model="S6860-54HF",
            command="display interface brief",
            command_canonical="display interface brief",
            raw_text="Interface Link Protocol\nGE1/0/1 UP UP\n",
            parse_status="template_missing",
            execution_item_id=f"first-{token}",
            import_fingerprint=f"first-{token}",
        )
        job_id = parser.create_candidate(
            job_type="CREATE_TEMPLATE",
            vendor="H3C",
            model="S6860-54HF",
            family="Comware7",
            profile="S6860-54HF",
            command="display interface brief",
            snapshot_id=first_snapshot_id,
            device_id="SW1",
            failure_code="template_missing",
        )
        parser.transition_job_status(job_id, "failed")
        assert (
            parser.create_candidate(
                job_type="CREATE_TEMPLATE",
                vendor="H3C",
                model="S6860-54HF",
                family="Comware7",
                profile="S6860-54HF",
                command="display interface brief",
                snapshot_id=first_snapshot_id,
                device_id="SW1",
                failure_code="template_missing",
            )
            == job_id
        )
        assert parser.get_job(job_id)["status"] == "failed"

        second_snapshot_id, _ = patrol.save_snapshot_idempotent(
            run_id=run_id,
            device_id="SW2",
            device_name="SW2",
            vendor="H3C",
            model="S6860-54HF",
            command="display interface brief",
            command_canonical="display interface brief",
            raw_text="Interface Link Protocol\nGE1/0/2 DOWN DOWN\n",
            parse_status="parse_failed",
            execution_item_id=f"second-{token}",
            import_fingerprint=f"second-{token}",
        )
        reopened_id = parser.create_candidate(
            job_type="REPAIR_TEMPLATE",
            vendor="H3C",
            model="S6860-54HF",
            family="Comware7",
            profile="S6860-54HF",
            command="display interface brief",
            snapshot_id=second_snapshot_id,
            device_id="SW2",
            failure_code="template_parse_error",
        )

        assert reopened_id == job_id
        job = parser.get_job(job_id)
        assert job is not None
        assert job["status"] == "collecting"
        assert job["job_type"] == "REPAIR_TEMPLATE"
        assert job["sample_count"] == 2
        assert job["completed_at"] is None
        with get_db_session() as db:
            assert (
                db.query(ParserTemplateJob)
                .filter(ParserTemplateJob.idempotency_key == job["idempotency_key"])
                .count()
                == 1
            )
            assert db.query(ParserOutboxEvent).filter_by(aggregate_id=job_id).count() == 2
    finally:
        with get_db_session() as db:
            if job_id:
                db.query(ParserOutboxEvent).filter_by(aggregate_id=job_id).delete(
                    synchronize_session=False
                )
                db.query(ParserJobSample).filter_by(job_id=job_id).delete(
                    synchronize_session=False
                )
                db.query(ParserTemplateJob).filter_by(id=job_id).delete(
                    synchronize_session=False
                )
            db.query(PatrolRun).filter(PatrolRun.id == run_id).delete(
                synchronize_session=False
            )


def test_parser_validation_deduplicates_different_outputs_by_template_asset():
    token = uuid.uuid4().hex
    run_id = f"parser-validation-{token}"
    patrol = PatrolRepository()
    parser = ParserRepository()
    patrol.create_run(run_id=run_id, mode="parser_validation", source="unit-test")
    outputs = (
        "Interface Link Protocol\nGE1/0/1 UP UP\n",
        "Brief information on interfaces\nXGE1/0/48 DOWN DOWN uplink\n",
    )
    assert output_signature(outputs[0]) != output_signature(outputs[1])
    job_id = ""
    try:
        snapshot_ids: list[str] = []
        for index, raw_text in enumerate(outputs):
            snapshot_id, created = patrol.save_snapshot_idempotent(
                run_id=run_id,
                device_id=f"SW{index + 1}",
                device_name=f"SW{index + 1}",
                vendor="H3C",
                model="S6860-54HF",
                command="display interface brief",
                command_canonical="display interface brief",
                raw_text=raw_text,
                parse_status="template_missing" if index == 0 else "parse_failed",
                execution_item_id=f"item-{index}-{token}",
                import_fingerprint=f"fingerprint-{index}-{token}",
            )
            assert created is True
            snapshot_ids.append(snapshot_id)
            current_job_id = parser.create_candidate(
                job_type="CREATE_TEMPLATE" if index == 0 else "REPAIR_TEMPLATE",
                vendor="H3C",
                model="S6860-54HF",
                family="Comware7",
                profile="S6860-54HF",
                command="display interface brief",
                snapshot_id=snapshot_id,
                device_id=f"SW{index + 1}",
                failure_code="template_missing" if index == 0 else "parse_failed",
            )
            job_id = job_id or current_job_id
            assert current_job_id == job_id

        # Re-observing an existing snapshot must not create another sample or outbox event.
        assert (
            parser.create_candidate(
                job_type="CREATE_TEMPLATE",
                vendor="H3C",
                model="S6860-54HF",
                family="Comware7",
                profile="S6860-54HF",
                command="display interface brief",
                snapshot_id=snapshot_ids[0],
                device_id="SW1",
                failure_code="template_missing",
            )
            == job_id
        )

        job = parser.get_job(job_id)
        assert job is not None
        assert job["job_type"] == "REPAIR_TEMPLATE"
        assert job["sample_count"] == 2
        with get_db_session() as db:
            assert (
                db.query(ParserTemplateJob)
                .filter_by(idempotency_key=job["idempotency_key"])
                .count()
                == 1
            )
            assert db.query(ParserJobSample).filter_by(job_id=job_id).count() == 2
            assert db.query(ParserOutboxEvent).filter_by(aggregate_id=job_id).count() == 1
    finally:
        with get_db_session() as db:
            if job_id:
                db.query(ParserOutboxEvent).filter_by(aggregate_id=job_id).delete(
                    synchronize_session=False
                )
                db.query(ParserJobSample).filter_by(job_id=job_id).delete(
                    synchronize_session=False
                )
                db.query(ParserTemplateJob).filter_by(id=job_id).delete(
                    synchronize_session=False
                )
            db.query(PatrolRun).filter(PatrolRun.id == run_id).delete(synchronize_session=False)


def test_parser_job_merges_validation_and_production_evidence_safely():
    token = uuid.uuid4().hex
    validation_run_id = f"parser-validation-mixed-{token}"
    production_run_id = f"parser-production-mixed-{token}"
    patrol = PatrolRepository()
    parser = ParserRepository()
    patrol.create_run(run_id=validation_run_id, mode="parser_validation", source="unit-test")
    patrol.create_run(run_id=production_run_id, mode="production", source="unit-test")
    job_id = ""
    try:
        snapshots: list[tuple[str, str]] = []
        for mode, run_id, device_id in (
            ("parser_validation", validation_run_id, "VALIDATION-SW"),
            ("production", production_run_id, "PRODUCTION-SW"),
        ):
            snapshot_id, created = patrol.save_snapshot_idempotent(
                run_id=run_id,
                device_id=device_id,
                device_name=device_id,
                vendor="H3C",
                model="S6860-54HF",
                command="display fan",
                command_canonical="display fan",
                raw_text=(
                    "Password: production-secret\nSlot 1 Fan 1 Normal\n"
                    if mode == "production"
                    else "Slot 1 Fan 1 Normal\n"
                ),
                parse_status="template_missing",
                execution_item_id=f"{mode}-{token}",
                import_fingerprint=f"{mode}-{token}",
            )
            assert created is True
            snapshots.append((snapshot_id, device_id))
            current_job_id = parser.create_candidate(
                job_type="CREATE_TEMPLATE",
                vendor="H3C",
                model="S6860-54HF",
                family="Comware7",
                profile="Comware7",
                command="display fan",
                snapshot_id=snapshot_id,
                device_id=device_id,
                failure_code="template_missing",
            )
            job_id = job_id or current_job_id
            assert current_job_id == job_id

        job = parser.get_job(job_id, include_sanitized_payload=True)
        assert job is not None
        assert job["source_mode"] == "production"
        assert job["sample_count"] == 2
        assert sorted(sample["sanitization_status"] for sample in job["samples"]) == [
            "pending_sanitization",
            "sanitized",
        ]
        assert parser.list_dispatchable_jobs(job_id=job_id) == []

        sanitized = parser.sanitize_pending_samples(job_id=job_id)
        assert len(sanitized) == 1
        assert sanitized[0]["success"] is True
        job = parser.get_job(job_id, include_sanitized_payload=True)
        production_sample = next(
            sample for sample in job["samples"] if sample["device_id"] == "PRODUCTION-SW"
        )
        assert "production-secret" not in production_sample["sanitized_text"]
        assert parser.list_dispatchable_jobs(job_id=job_id) == []
        assert job["pipeline_version"] == "simple_v2"
        assert "parser_spec_not_confirmed" in job["generation_readiness"]["blockers"]
    finally:
        with get_db_session() as db:
            if job_id:
                db.query(ParserOutboxEvent).filter_by(aggregate_id=job_id).delete(
                    synchronize_session=False
                )
                db.query(ParserJobSample).filter_by(job_id=job_id).delete(
                    synchronize_session=False
                )
                db.query(ParserTemplateJob).filter_by(id=job_id).delete(
                    synchronize_session=False
                )
            db.query(PatrolRun).filter(
                PatrolRun.id.in_([validation_run_id, production_run_id])
            ).delete(synchronize_session=False)


def test_production_parser_backlog_deduplicates_and_requires_sanitization():
    token = uuid.uuid4().hex
    run_id = f"parser-production-{token}"
    patrol = PatrolRepository()
    parser = ParserRepository()
    patrol.create_run(run_id=run_id, mode="production", source="unit-test")
    snapshot_ids: list[str] = []
    job_id = ""
    try:
        for index in range(2):
            model = "S6860-54HF" if index == 0 else "S6850-56HF"
            snapshot_id, created = patrol.save_snapshot_idempotent(
                run_id=run_id,
                device_id=f"SW{index + 1}",
                device_name=f"SW{index + 1}",
                vendor="H3C",
                model=model,
                command="display interface brief",
                command_canonical="display interface brief",
                raw_text=(
                    f"Username: admin\nPassword: secret-{index}\n"
                    f"Management IP: 10.0.0.{index + 1}\n"
                    "Interface Link Protocol\nGE1/0/1 UP UP\n"
                ),
                parse_status="template_missing",
                execution_item_id=f"item-{index}-{token}",
                import_fingerprint=f"fingerprint-{index}-{token}",
            )
            assert created is True
            snapshot_ids.append(snapshot_id)
            current_job_id = parser.create_candidate(
                job_type="CREATE_TEMPLATE",
                vendor="H3C",
                model=model,
                family="Comware7",
                profile="Comware7",
                command="display interface brief",
                snapshot_id=snapshot_id,
                device_id=f"SW{index + 1}",
                failure_code="template_missing",
            )
            job_id = job_id or current_job_id
            assert current_job_id == job_id

        job = parser.get_job(job_id, include_sanitized_payload=True)
        assert job is not None
        assert job["source_mode"] == "production"
        assert job["profile"] == "Comware7"
        assert job["sample_count"] == 2
        assert {sample["sanitization_status"] for sample in job["samples"]} == {
            "pending_sanitization"
        }
        assert parser.list_dispatchable_jobs(job_id=job_id) == []

        sanitized = parser.sanitize_pending_samples(job_id=job_id)
        assert len(sanitized) == 2
        assert all(item["success"] for item in sanitized)
        job = parser.get_job(job_id, include_sanitized_payload=True)
        assert job is not None
        for sample in job["samples"]:
            text = sample["sanitized_text"]
            assert "secret-" not in text
            assert "10.0.0." not in text
            assert "GE1/0/1 UP UP" in text
        assert parser.list_dispatchable_jobs(job_id=job_id) == []
        assert job["pipeline_version"] == "simple_v2"
        assert "sample_roles_unassigned" in job["generation_readiness"]["blockers"]
    finally:
        with get_db_session() as db:
            if job_id:
                db.query(ParserOutboxEvent).filter(ParserOutboxEvent.aggregate_id == job_id).delete(
                    synchronize_session=False
                )
                db.query(ParserJobSample).filter(ParserJobSample.job_id == job_id).delete(
                    synchronize_session=False
                )
                db.query(ParserTemplateJob).filter(ParserTemplateJob.id == job_id).delete(
                    synchronize_session=False
                )
            db.query(PatrolRun).filter(PatrolRun.id == run_id).delete(synchronize_session=False)


def test_single_validation_sample_remains_blocked_after_legacy_grace_period():
    token = uuid.uuid4().hex
    run_id = f"parser-validation-aged-{token}"
    device_id = f"AGED-SW-{token}"
    patrol = PatrolRepository()
    parser = ParserRepository()
    patrol.create_run(
        run_id=run_id,
        mode="parser_validation",
        source="unit-test",
    )
    job_id = ""
    try:
        snapshot_id, created = patrol.save_snapshot_idempotent(
            run_id=run_id,
            device_id=device_id,
            device_name=device_id,
            vendor="H3C",
            model=f"S5130-{token}",
            command="display version",
            command_canonical="display version",
            raw_text="H3C Comware Software, Version 7.1\n",
            parse_status="template_missing",
            execution_item_id=f"item-{token}",
            import_fingerprint=f"fingerprint-{token}",
        )
        assert created is True
        job_id = parser.create_candidate(
            job_type="CREATE_TEMPLATE",
            vendor="H3C",
            model=f"S5130-{token}",
            family="Comware7",
            profile=f"S5130-{token}",
            command="display version",
            snapshot_id=snapshot_id,
            device_id=device_id,
            failure_code="template_missing",
        )

        assert parser.list_dispatchable_jobs(job_id=job_id) == []
        with get_db_session() as db:
            _, now_local = database_clocks(db)
            job = db.get(ParserTemplateJob, job_id)
            job.created_at = now_local - timedelta(minutes=31)

        assert parser.list_dispatchable_jobs(job_id=job_id) == []
        job = parser.get_job(job_id)
        assert job is not None
        assert "parser_spec_not_confirmed" in job["generation_readiness"]["blockers"]
        assert "independent_acceptance_missing" in job["generation_readiness"]["warnings"]
    finally:
        with get_db_session() as db:
            if job_id:
                db.query(ParserOutboxEvent).filter_by(aggregate_id=job_id).delete(
                    synchronize_session=False
                )
                db.query(ParserJobSample).filter_by(job_id=job_id).delete(
                    synchronize_session=False
                )
                db.query(ParserTemplateJob).filter_by(id=job_id).delete(
                    synchronize_session=False
                )
            db.query(PatrolRun).filter(PatrolRun.id == run_id).delete(
                synchronize_session=False
            )

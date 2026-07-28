from __future__ import annotations

import hashlib
import json
import uuid
from types import SimpleNamespace

import pytest

from src.core.parser.repository import (
    SIMPLE_PARSER_HARD_GATES,
    ParserJobRepository,
    ParserJobStateError,
    _deprecate_rule_generation_seeds,
    _normalize_parser_spec_payload,
    _parser_spec_digest,
    _simple_parser_readiness,
    generated_template_activation_errors,
)
from src.core.parser.textfsm_dialect import (
    DEFAULT_TEXTFSM_DIALECT,
    dialect_hash,
    installed_textfsm_version,
)
from src.infrastructure.db.models import (
    ObservationContractRecord,
    ParserGenerationAttempt,
    ParserTemplate,
    ParserTemplateJob,
    RuleGenerationJobRecord,
)
from src.infrastructure.db.postgres import get_db_session


def _digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def test_simple_parser_readiness_supports_v2_record_set_specs():
    parser_spec = {
        "version": 2,
        "business_purpose": "Parse processor utilization by slot and processor.",
        "record_sets": [
            {
                "name": "cpu_scope",
                "entity": "cpu_scope",
                "cardinality": "repeated",
                "primary_keys": ["slot", "cpu"],
                "fields": {
                    "slot": {"required": True, "type": "number"},
                    "cpu": {"required": True, "type": "number"},
                    "utilization_5s": {"required": True, "type": "percent"},
                },
                "allow_empty": False,
                "empty_patterns": [],
            }
        ],
    }
    normalized = _normalize_parser_spec_payload(parser_spec)
    job = SimpleNamespace(
        parser_spec_json=parser_spec,
        parser_spec_status="confirmed",
        parser_spec_sha256=_parser_spec_digest(normalized),
    )
    sample = SimpleNamespace(
        sanitization_status="sanitized",
        sanitized_text=(
            "Slot 0 CPU 0 CPU usage:\n"
            "  4% in last 5 seconds\n"
        ),
        sample_role="generation",
        expectation_json={
            "record_count": 1,
            "primary_keys": [["0", "0"]],
        },
    )

    readiness = _simple_parser_readiness(job, [sample])

    assert readiness["ready"] is True
    assert readiness["blockers"] == []


def test_simple_parser_activation_requires_immutable_spec_manifest_and_call_budget():
    profile = DEFAULT_TEXTFSM_DIALECT
    spec = {
        "entity": "interface",
        "primary_keys": ["interface"],
        "fields": {"interface": {"required": True, "type": "interface_name"}},
        "allow_empty": False,
        "empty_patterns": [],
    }
    manifest = [
        {
            "sample_id": f"sample-{role}",
            "role": role,
            "sha256": character * 64,
            "expectation": {"record_count": 1, "primary_keys": [["GE1/0/1"]]},
        }
        for role, character in (("generation", "a"), ("acceptance", "b"))
    ]
    validation = {
        "simple_parser": {
            "pipeline_version": "simple_v2",
            "status": "candidate_ready",
            "model_calls": 3,
            "best_attempt_id": "attempt-2",
            "hard_gates": {key: True for key in SIMPLE_PARSER_HARD_GATES},
        },
        "parser_spec": spec,
        "parser_spec_sha256": _digest(spec),
        "sample_manifest": manifest,
        "sample_manifest_sha256": _digest(sorted(manifest, key=lambda item: item["sample_id"])),
        "textfsm_dialect": {
            "runtime_version": installed_textfsm_version(),
            "dialect_version": profile.dialect_version,
            "dialect_hash": dialect_hash(profile),
            "prompt_version": profile.prompt_version,
        },
    }
    assert generated_template_activation_errors(validation, source_job_id="job-1") == []
    validation["simple_parser"]["model_calls"] = 4
    assert "model-call budget" in " ".join(
        generated_template_activation_errors(validation, source_job_id="job-1")
    )


def test_simple_parser_activation_accepts_generation_sample_only_evidence():
    profile = DEFAULT_TEXTFSM_DIALECT
    spec = {
        "entity": "interface",
        "primary_keys": ["interface"],
        "fields": {"interface": {"required": True, "type": "interface_name"}},
        "allow_empty": False,
        "empty_patterns": [],
    }
    manifest = [
        {
            "sample_id": "sample-generation",
            "role": "generation",
            "sha256": "a" * 64,
            "expectation": {"record_count": 1, "primary_keys": [["GE1/0/1"]]},
        }
    ]
    validation = {
        "simple_parser": {
            "pipeline_version": "simple_v2",
            "validation_mode": "generation_sample_only",
            "status": "candidate_ready",
            "model_calls": 1,
            "best_attempt_id": "attempt-1",
            "hard_gates": {
                key: True for key in SIMPLE_PARSER_HARD_GATES if not key.startswith("acceptance_")
            },
        },
        "parser_spec": spec,
        "parser_spec_sha256": _digest(spec),
        "sample_manifest": manifest,
        "sample_manifest_sha256": _digest(manifest),
        "textfsm_dialect": {
            "runtime_version": installed_textfsm_version(),
            "dialect_version": profile.dialect_version,
            "dialect_hash": dialect_hash(profile),
            "prompt_version": profile.prompt_version,
        },
    }

    assert generated_template_activation_errors(validation, source_job_id="job-1") == []


def test_generation_attempts_are_immutable_and_projected_without_template_text():
    token = uuid.uuid4().hex
    job_id = f"parser-simple-{token}"
    with get_db_session() as db:
        db.add(
            ParserTemplateJob(
                id=job_id,
                job_type="CREATE_TEMPLATE",
                source_mode="parser_validation",
                pipeline_version="simple_v2",
                vendor="H3C",
                model="Generic",
                profile=f"test-{token}",
                canonical_command="display test",
                output_signature=token[:24],
                idempotency_key=token,
                status="running",
                sample_count=1,
            )
        )
    repository = ParserJobRepository()
    profile = DEFAULT_TEXTFSM_DIALECT
    try:
        first = repository.create_generation_attempt(
            job_id=job_id,
            stage="template",
            action="llm_generate",
            status="failed",
            textfsm_runtime_version="2.1.0",
            dialect_version=profile.dialect_version,
            dialect_hash=dialect_hash(profile),
            template_text="bad",
            validation_report={"success": False},
            candidate_rank=0.0,
        )
        second = repository.create_generation_attempt(
            job_id=job_id,
            parent_attempt_id=first["id"],
            stage="template",
            action="llm_repair",
            status="passed",
            textfsm_runtime_version="2.1.0",
            dialect_version=profile.dialect_version,
            dialect_hash=dialect_hash(profile),
            template_text="good",
            validation_report={"success": True},
            candidate_rank=1.0,
        )
        attempts = repository.list_generation_attempts(job_id)
        detail = repository.get_job(job_id)
        assert [item["attempt_number"] for item in attempts] == [1, 2]
        assert all("template_text" not in item for item in attempts)
        assert detail["best_attempt_id"] == second["id"]
        assert detail["attempt_summary"]["total"] == 2
    finally:
        with get_db_session() as db:
            db.query(ParserGenerationAttempt).filter_by(job_id=job_id).delete(
                synchronize_session=False
            )
            db.query(ParserTemplateJob).filter_by(id=job_id).delete(synchronize_session=False)


def test_legacy_job_retry_is_rejected_server_side():
    token = uuid.uuid4().hex
    job_id = f"parser-legacy-{token}"
    with get_db_session() as db:
        db.add(
            ParserTemplateJob(
                id=job_id,
                job_type="CREATE_TEMPLATE",
                source_mode="parser_validation",
                pipeline_version="legacy",
                vendor="Generic",
                model="Generic",
                profile=f"test-{token}",
                canonical_command="inspect component",
                output_signature=token[:24],
                idempotency_key=token,
                status="failed",
                sample_count=1,
            )
        )
    try:
        with pytest.raises(ParserJobStateError, match="read-only"):
            ParserJobRepository().retry_job(job_id)
    finally:
        with get_db_session() as db:
            db.query(ParserTemplateJob).filter_by(id=job_id).delete(synchronize_session=False)


def test_succeeded_simple_job_requires_candidate_retirement_before_retry():
    token = uuid.uuid4().hex
    job_id = f"parser-regenerate-{token}"
    template_id = f"template-regenerate-{token}"
    with get_db_session() as db:
        db.add(
            ParserTemplateJob(
                id=job_id,
                job_type="CREATE_TEMPLATE",
                source_mode="parser_validation",
                pipeline_version="simple_v2",
                vendor="Generic",
                model="Generic",
                profile=f"test-{token}",
                canonical_command="inspect component",
                output_signature=token[:24],
                idempotency_key=token,
                status="succeeded",
                sample_count=1,
            )
        )
        db.add(
            ParserTemplate(
                id=template_id,
                vendor="Generic",
                model="Generic",
                profile=f"test-{token}",
                canonical_command="inspect component",
                category="record",
                version="candidate-1",
                asset_path="candidate.textfsm",
                sha256="0" * 64,
                status="pending_approval",
                validation_json={},
                source_job_id=job_id,
                contract_version=1,
            )
        )
    repository = ParserJobRepository()
    try:
        with pytest.raises(ParserJobStateError, match="every generated candidate"):
            repository.retry_job(job_id)
        with get_db_session() as db:
            db.query(ParserTemplate).filter_by(id=template_id).update(
                {"status": "deprecated"}, synchronize_session=False
            )
        assert repository.retry_job(job_id)["status"] == "pending"
    finally:
        with get_db_session() as db:
            db.query(ParserTemplate).filter_by(id=template_id).delete(synchronize_session=False)
            db.query(ParserTemplateJob).filter_by(id=job_id).delete(synchronize_session=False)


def test_parser_spec_can_be_revised_after_generation_without_mutating_active_template():
    token = uuid.uuid4().hex
    job_id = f"parser-revision-{token}"
    candidate_id = f"candidate-revision-{token}"
    active_id = f"active-revision-{token}"
    profile = f"test-{token}"
    old_spec = {
        "entity": "slot",
        "primary_keys": ["slot_id"],
        "fields": {
            "slot_id": {"required": True, "type": "text"},
        },
    }
    new_spec = {
        "entity": "slot_subslot",
        "primary_keys": ["slot_id", "subslot_name"],
        "fields": {
            "slot_id": {"required": True, "type": "text"},
            "subslot_name": {"required": True, "type": "text"},
        },
    }
    with get_db_session() as db:
        db.add(
            ParserTemplateJob(
                id=job_id,
                job_type="CREATE_TEMPLATE",
                source_mode="parser_validation",
                pipeline_version="simple_v2",
                vendor="H3C",
                model="MSR5620",
                profile=profile,
                canonical_command="display version",
                output_signature=token[:24],
                idempotency_key=token,
                status="succeeded",
                parser_spec_status="confirmed",
                parser_spec_json=old_spec,
                parser_spec_sha256=_digest(old_spec),
                sample_count=1,
            )
        )
        for template_id, status, version in (
            (candidate_id, "pending_approval", "candidate-1"),
            (active_id, "active", "active-1"),
        ):
            db.add(
                ParserTemplate(
                    id=template_id,
                    vendor="H3C",
                    model="MSR5620",
                    profile=profile,
                    canonical_command="display version",
                    category="slot",
                    version=version,
                    asset_path=f"postgresql://parser-template/{template_id}",
                    sha256="0" * 64,
                    status=status,
                    validation_json={
                        "entity_type": "slot",
                        "primary_keys": ["slot_id"],
                        "parser_spec": old_spec,
                        "parser_spec_sha256": _digest(old_spec),
                    },
                    source_job_id=job_id,
                    contract_version=1,
                )
            )

    repository = ParserJobRepository()
    try:
        revised = repository.set_parser_spec(
            job_id,
            parser_spec=new_spec,
            confirmed=True,
            actor="unit-test",
            reason="correct the record identity before regeneration",
        )

        assert revised["status"] == "failed"
        assert revised["failure_code"] == "parser_spec_revised"
        assert revised["parser_spec_json"]["entity"] == "slot_subslot"
        with get_db_session() as db:
            assert db.get(ParserTemplate, candidate_id).status == "rejected"
            assert db.get(ParserTemplate, active_id).status == "active"
        assert repository.retry_job(job_id)["status"] == "pending"
    finally:
        with get_db_session() as db:
            db.query(ObservationContractRecord).filter(
                ObservationContractRecord.source_template_id.in_(
                    [candidate_id, active_id]
                )
            ).delete(synchronize_session=False)
            db.query(ParserTemplate).filter(
                ParserTemplate.id.in_([candidate_id, active_id])
            ).delete(synchronize_session=False)
            db.query(ParserTemplateJob).filter_by(id=job_id).delete(
                synchronize_session=False
            )


def test_deprecating_parser_source_retires_unpublished_rule_seed():
    token = uuid.uuid4().hex
    template_id = f"template-{token}"
    rule_job_id = f"rule-job-{token}"
    with get_db_session() as db:
        db.add(
            RuleGenerationJobRecord(
                id=rule_job_id,
                job_type="OBSERVATION_CONTRACT",
                status="draft",
                source_ref=f"parser_template:{template_id}",
                payload_json={},
                validation_report_json={},
                approval_required=True,
            )
        )
    try:
        with get_db_session() as db:
            assert (
                _deprecate_rule_generation_seeds(
                    db,
                    template_id=template_id,
                    actor="unit-test",
                    reason="source candidate rejected",
                )
                == 1
            )
        with get_db_session() as db:
            row = db.get(RuleGenerationJobRecord, rule_job_id)
            assert row.status == "deprecated"
            assert row.validation_report_json["source_asset_lifecycle"]["actor"] == "unit-test"
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter_by(id=rule_job_id).delete(
                synchronize_session=False
            )

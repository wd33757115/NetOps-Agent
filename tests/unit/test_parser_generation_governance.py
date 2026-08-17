from __future__ import annotations

import hashlib
import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from src.core.parser.repository import (
    SIMPLE_PARSER_HARD_GATES,
    ParserJobRepository,
    ParserJobStateError,
    ParserRepository,
    ParserTemplateRepository,
    _deprecate_rule_generation_seeds,
    _normalize_parser_spec_payload,
    _parser_spec_digest,
    _simple_parser_readiness,
    generated_template_activation_errors,
)
from src.core.patrol.repository import PatrolRepository
from src.core.parser.textfsm_dialect import (
    DEFAULT_TEXTFSM_DIALECT,
    dialect_hash,
    installed_textfsm_version,
)
from src.infrastructure.db.models import (
    AuditLogRecord,
    CommandSnapshot,
    ObservationContractRecord,
    ParserCandidateSampleEvidence,
    ParserGenerationAttempt,
    ParserJobSample,
    ParserOutboxEvent,
    ParserSpecRevisionRecord,
    ParserTemplate,
    ParserTemplateJob,
    PatrolDataset,
    PatrolRun,
    RawEvidenceRecord,
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
        sanitized_text=("Slot 0 CPU 0 CPU usage:\n  4% in last 5 seconds\n"),
        sample_role="generation",
        expectation_json={
            "record_count": 1,
            "primary_keys": [["0", "0"]],
        },
    )

    readiness = _simple_parser_readiness(job, [sample])

    assert readiness["ready"] is True
    assert readiness["blockers"] == []


def test_simple_parser_readiness_accepts_governed_valid_empty_header():
    parser_spec = {
        "version": 2,
        "business_purpose": "Parse routed interfaces or an approved empty table.",
        "record_sets": [
            {
                "name": "interfaces",
                "entity": "interface_l3",
                "cardinality": "repeated",
                "presence": "optional",
                "primary_keys": ["interface"],
                "fields": {
                    "interface": {"required": True, "type": "interface_name"},
                    "status": {"required": True, "type": "text"},
                },
                "allow_empty": True,
                "empty_patterns": [r"(?is)Interface\s+IP Address.*Status\s*$"],
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
            'IP Interface Status for VRF "default"(1)\n'
            "Interface            IP Address      Interface Status\n"
        ),
        sample_role="generation",
        expectation_json=None,
    )

    readiness = _simple_parser_readiness(job, [sample])

    assert readiness["ready"] is True
    assert readiness["blockers"] == []
    assert "generation_samples_are_governed_valid_empty" in readiness["warnings"]


def test_simple_parser_readiness_blocks_unapproved_header_only_sample():
    parser_spec = {
        "version": 2,
        "business_purpose": "Parse routed interfaces.",
        "record_sets": [
            {
                "name": "interfaces",
                "entity": "interface_l3",
                "cardinality": "repeated",
                "presence": "optional",
                "primary_keys": ["interface"],
                "fields": {
                    "interface": {"required": True, "type": "interface_name"},
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
        sanitized_text="Interface            IP Address      Interface Status\n",
        sample_role="generation",
        expectation_json=None,
    )

    readiness = _simple_parser_readiness(job, [sample])

    assert readiness["ready"] is False
    assert "generation_samples_without_data" in readiness["blockers"]
    assert "header_only_samples_unverified" in readiness["blockers"]


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
    source_spec = _normalize_parser_spec_payload(
        {
            "version": 2,
            "business_purpose": "Inspect interface operational status.",
            "record_sets": [
                {
                    "name": "interfaces",
                    "entity": "interface",
                    "cardinality": "repeated",
                    "primary_keys": ["interface"],
                    "fields": {
                        "interface": {
                            "required": True,
                            "type": "interface_name",
                        }
                    },
                    "allow_empty": False,
                    "empty_patterns": [],
                }
            ],
        }
    )
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
        "parser_spec_evidence_version": 2,
        "source_parser_spec": source_spec,
        "source_parser_spec_sha256": _digest(source_spec),
        "sample_manifest": manifest,
        "sample_manifest_sha256": _digest(sorted(manifest, key=lambda item: item["sample_id"])),
        "candidate_sample_evidence_version": 1,
        "textfsm_dialect": {
            "runtime_version": installed_textfsm_version(),
            "dialect_version": profile.dialect_version,
            "dialect_hash": dialect_hash(profile),
            "prompt_version": profile.prompt_version,
        },
    }
    assert generated_template_activation_errors(validation, source_job_id="job-1") == []
    validation["source_parser_spec_sha256"] = "0" * 64
    assert "source ParserSpec digest does not match" in " ".join(
        generated_template_activation_errors(validation, source_job_id="job-1")
    )
    validation["source_parser_spec_sha256"] = _digest(source_spec)
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
        "candidate_sample_evidence_version": 1,
        "textfsm_dialect": {
            "runtime_version": installed_textfsm_version(),
            "dialect_version": profile.dialect_version,
            "dialect_hash": dialect_hash(profile),
            "prompt_version": profile.prompt_version,
        },
    }

    assert generated_template_activation_errors(validation, source_job_id="job-1") == []


def test_ai_review_failure_can_enter_explicit_human_direct_review():
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
            "expectation": None,
        }
    ]
    validation = {
        "contract_source": "candidate_result_human_review_pending",
        "human_direct_review_required": True,
        "ai_review": {
            "passed": False,
            "summary": "Engineer must confirm the final parsed result.",
        },
        "simple_parser": {
            "pipeline_version": "simple_v2",
            "validation_mode": "generation_sample_only",
            "status": "candidate_ready",
            "model_calls": 3,
            "best_attempt_id": "attempt-3",
            "hard_gates": {
                "compiled": True,
                "executed": True,
                "source_executed": True,
                "source_shape_compatible": True,
                "independent_semantic_review": False,
            },
        },
        "parser_spec": spec,
        "parser_spec_sha256": _digest(spec),
        "sample_manifest": manifest,
        "sample_manifest_sha256": _digest(manifest),
        "candidate_sample_evidence_version": 1,
        "textfsm_dialect": {
            "runtime_version": installed_textfsm_version(),
            "dialect_version": profile.dialect_version,
            "dialect_hash": dialect_hash(profile),
            "prompt_version": profile.prompt_version,
        },
    }

    assert generated_template_activation_errors(
        validation,
        source_job_id="job-1",
    ) == []
    validation["human_direct_review_required"] = False
    assert "human direct-review governance evidence is invalid" in (
        generated_template_activation_errors(
            validation,
            source_job_id="job-1",
        )
    )


def test_generated_parser_requires_separate_approval_unless_admin_override_is_explicit():
    token = uuid.uuid4().hex
    profile = DEFAULT_TEXTFSM_DIALECT
    parser_spec = {
        "entity": "interface",
        "primary_keys": ["interface"],
        "fields": {
            "interface": {
                "required": True,
                "type": "interface_name",
            }
        },
        "allow_empty": False,
        "empty_patterns": [],
    }
    sample_manifest = [
        {
            "sample_id": f"sample-{token}",
            "role": "generation",
            "sha256": "a" * 64,
            "expectation": None,
        }
    ]
    result_samples = [
        {
            "snapshot_id": f"snapshot-{token}",
            "raw_text_sha256": "b" * 64,
            "status": "success",
            "records": [{"interface": "GE1/0/1"}],
            "primary_keys": [["GE1/0/1"]],
        }
    ]
    validation = {
        "entity_type": "interface",
        "primary_keys": ["interface"],
        "required_fields": ["interface"],
        "contract_source": "candidate_result_ai_reviewed",
        "ai_review": {
            "passed": True,
            "summary": "The parsed records are semantically complete.",
        },
        "simple_parser": {
            "pipeline_version": "simple_v2",
            "validation_mode": "generation_sample_only",
            "status": "candidate_ready",
            "model_calls": 1,
            "best_attempt_id": f"attempt-{token}",
            "hard_gates": {
                key: True
                for key in SIMPLE_PARSER_HARD_GATES
                if not key.startswith("acceptance_")
            },
        },
        "parser_spec": parser_spec,
        "parser_spec_sha256": _digest(parser_spec),
        "sample_manifest": sample_manifest,
        "sample_manifest_sha256": _digest(sample_manifest),
        "textfsm_dialect": {
            "runtime_version": installed_textfsm_version(),
            "dialect_version": profile.dialect_version,
            "dialect_hash": dialect_hash(profile),
            "prompt_version": profile.prompt_version,
        },
        "regression_validation": {
            "passed": True,
            "sample_count": 1,
            "passed_samples": 1,
        },
        "approval_result_preview": {
            "samples": result_samples,
        },
    }
    template_text = "Value interface (\\S+)\n\nStart\n  ^${interface} -> Record\n"
    template_sha = hashlib.sha256(template_text.encode("utf-8")).hexdigest()
    repository = ParserTemplateRepository()
    template_id = repository.register_template(
        vendor="H3C",
        model=None,
        family="Comware7",
        profile=f"semantic-review-{token}",
        command="display interface brief",
        category="interface",
        version=f"candidate-{token}",
        asset_path=f"postgresql://parser-template/{template_sha}",
        template_text=template_text,
        sha256=template_sha,
        status="pending_approval",
        validation=validation,
    )
    try:
        with pytest.raises(ValueError, match="human semantic review"):
            repository.activate_template(
                template_id,
                approved_by="release-approver",
                reason="release before review must fail",
            )

        review = repository.record_semantic_review(
            template_id,
            actor="semantic-reviewer",
            reason="parsed records and stable primary keys were checked sample by sample",
            result_confirmed=True,
            primary_key_confirmed=True,
        )
        assert review["status"] == "accepted"
        assert review["review_mode"] == "human_after_ai"

        with pytest.raises(ValueError, match="different release approver"):
            repository.activate_template(
                template_id,
                approved_by="semantic-reviewer",
                reason="the reviewer cannot approve their own review",
            )

        with get_db_session() as db:
            row = db.get(ParserTemplate, template_id)
            changed_validation = dict(row.validation_json or {})
            changed_preview = dict(
                changed_validation.get("approval_result_preview") or {}
            )
            changed_samples = [
                dict(item) for item in changed_preview.get("samples") or []
            ]
            changed_samples[0] = {
                **changed_samples[0],
                "records": [{"interface": "GE1/0/2"}],
                "primary_keys": [["GE1/0/2"]],
            }
            changed_preview["samples"] = changed_samples
            changed_validation["approval_result_preview"] = changed_preview
            row.validation_json = changed_validation

        with pytest.raises(ValueError, match="semantic review evidence is stale"):
            repository.activate_template(
                template_id,
                approved_by="release-approver",
                reason="changed evidence must invalidate the previous review",
            )

        review = repository.record_semantic_review(
            template_id,
            actor="semantic-reviewer",
            reason="review repeated after the parsed-result evidence changed",
            result_confirmed=True,
            primary_key_confirmed=True,
        )
        activated = repository.activate_template(
            template_id,
            approved_by="semantic-reviewer",
            reason="administrator approved after reviewing frozen evidence",
            allow_same_reviewer=True,
        )
        assert activated.status == "active"
        assert activated.validation_json["human_semantic_review"]["reviewed_by"] == (
            "semantic-reviewer"
        )
        assert activated.validation_json["approval_audit"][-1][
            "semantic_review_actor"
        ] == "semantic-reviewer"
        assert activated.validation_json["separation_of_duties_override"] is True
        assert activated.validation_json["approval_audit"][-1][
            "separation_of_duties_override"
        ] is True

        with get_db_session() as db:
            audits = (
                db.query(AuditLogRecord)
                .filter(
                    AuditLogRecord.action == "parser_template_semantic_reviewed",
                    AuditLogRecord.resource_id == template_id,
                )
                .order_by(AuditLogRecord.created_at)
                .all()
            )
            assert len(audits) == 2
            assert audits[-1].username == "semantic-reviewer"
            assert audits[-1].detail["evidence_digest"] == review["evidence_digest"]
            activation_audits = (
                db.query(AuditLogRecord)
                .filter(
                    AuditLogRecord.action == "parser_template_activated",
                    AuditLogRecord.resource_id == template_id,
                )
                .all()
            )
            assert len(activation_audits) == 1
            assert activation_audits[0].username == "semantic-reviewer"
            assert activation_audits[0].detail[
                "separation_of_duties_override"
            ] is True
            assert activation_audits[0].detail["semantic_review_actor"] == (
                "semantic-reviewer"
            )
            assert activation_audits[0].detail[
                "semantic_review_evidence_digest"
            ] == review["evidence_digest"]
    finally:
        with get_db_session() as db:
            db.query(ParserOutboxEvent).filter(
                ParserOutboxEvent.aggregate_id == template_id
            ).delete(synchronize_session=False)
            db.query(AuditLogRecord).filter(
                AuditLogRecord.resource_id == template_id
            ).delete(synchronize_session=False)
            db.query(ObservationContractRecord).filter(
                ObservationContractRecord.source_template_id == template_id
            ).delete(synchronize_session=False)
            db.query(ParserTemplate).filter(ParserTemplate.id == template_id).delete(
                synchronize_session=False
            )


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
        assert revised["parser_spec_revision"] == 1
        assert revised["parser_spec_json"]["entity"] == "slot_subslot"
        with get_db_session() as db:
            assert db.get(ParserTemplate, candidate_id).status == "rejected"
            assert db.get(ParserTemplate, active_id).status == "active"
            revision = (
                db.query(ParserSpecRevisionRecord)
                .filter(ParserSpecRevisionRecord.job_id == job_id)
                .one()
            )
            assert revision.revision_number == 1
            assert revision.source == "engineer"
            assert revision.invalidated_template_ids_json == [candidate_id]
        assert repository.retry_job(job_id)["status"] == "pending"
    finally:
        with get_db_session() as db:
            db.query(ParserSpecRevisionRecord).filter_by(job_id=job_id).delete(
                synchronize_session=False
            )
            db.query(ObservationContractRecord).filter(
                ObservationContractRecord.source_template_id.in_([candidate_id, active_id])
            ).delete(synchronize_session=False)
            db.query(ParserTemplate).filter(
                ParserTemplate.id.in_([candidate_id, active_id])
            ).delete(synchronize_session=False)
            db.query(ParserTemplateJob).filter_by(id=job_id).delete(synchronize_session=False)


def test_generated_candidate_freezes_samples_and_new_evidence_invalidates_it():
    token = uuid.uuid4().hex
    dataset_id = f"parser-evidence-{token}"
    run_id = f"parser-evidence-run-{token}"
    second_run_id = f"parser-evidence-run-{token}-2"
    model = f"Model-{token}"
    command = "inspect interface evidence"
    patrol = PatrolRepository()
    patrol.ensure_dataset(
        dataset_id=dataset_id,
        name=dataset_id,
        mode="parser_validation",
        source="unit_test",
    )
    patrol.create_run(
        run_id=run_id,
        mode="parser_validation",
        dataset_id=dataset_id,
        metadata={"dataset_id": dataset_id},
    )
    first_text = "Interface Status\nGE1/0/1 UP\n"
    first_snapshot_id, _ = patrol.save_snapshot_idempotent(
        run_id=run_id,
        device_id="SW1",
        device_name="SW1",
        vendor="Generic",
        model=model,
        command=command,
        command_canonical=command,
        raw_text=first_text,
        dataset_id=dataset_id,
        source_file_hash=token,
        import_fingerprint=f"{token}-first",
    )
    parser = ParserRepository()
    job_id = parser.create_candidate(
        job_type="CREATE_TEMPLATE",
        vendor="Generic",
        model=model,
        family=None,
        profile=model,
        command=command,
        snapshot_id=first_snapshot_id,
        device_id="SW1",
    )
    with get_db_session() as db:
        first_sample = (
            db.query(ParserJobSample)
            .filter(ParserJobSample.job_id == job_id)
            .one()
        )
        first_sample_id = str(first_sample.id)
        first_validation_sha256 = str(first_sample.sanitized_hash)
        first_raw_sha256 = str(db.get(CommandSnapshot, first_snapshot_id).raw_text_hash)
    ParserJobRepository().set_sample_role(
        job_id,
        first_sample_id,
        role="generation",
        expectation=None,
        actor="unit-test",
        reason="freeze the generation sample",
    )
    manifest = [
        {
            "sample_id": first_sample_id,
            "role": "generation",
            "sha256": first_raw_sha256,
            "validation_sha256": first_validation_sha256,
            "expectation": None,
        }
    ]
    template_text = "Value interface (\\S+)\n\nStart\n  ^${interface} -> Record\n"
    template_sha256 = hashlib.sha256(template_text.encode("utf-8")).hexdigest()
    validation = {
        "entity_type": "interface",
        "primary_keys": ["interface"],
        "required_fields": ["interface"],
        "optional_fields": [],
        "validators": {"interface": {"type": "interface_name"}},
        "allow_empty": False,
        "empty_patterns": [],
        "simple_parser": {"pipeline_version": "simple_v2"},
        "candidate_sample_evidence_version": 1,
        "sample_manifest": manifest,
        "sample_manifest_sha256": _digest(manifest),
    }
    template_id = ParserTemplateRepository().register_template(
        vendor="Generic",
        model=model,
        family=None,
        profile=model,
        command=command,
        category="interface",
        version=f"candidate-{token}",
        asset_path=f"postgresql://parser-template/{template_sha256}",
        sha256=template_sha256,
        template_text=template_text,
        validation=validation,
        source_job_id=job_id,
        status="pending_approval",
        candidate_sample_manifest=manifest,
    )

    try:
        evidence = ParserTemplateRepository().get_candidate_sample_evidence(
            template_id,
            include_sanitized_payload=True,
        )
        assert evidence["errors"] == []
        assert evidence["items"][0]["sanitized_text"] == first_text
        assert evidence["items"][0]["raw_text_redacted"] is True
        with get_db_session() as db:
            assert (
                db.query(ParserCandidateSampleEvidence)
                .filter_by(template_id=template_id)
                .count()
                == 1
            )

        with pytest.raises(IntegrityError):
            with get_db_session() as db:
                db.query(CommandSnapshot).filter_by(id=first_snapshot_id).delete(
                    synchronize_session=False
                )
                db.flush()

        second_snapshot_id, _ = patrol.save_snapshot_idempotent(
            run_id=(
                patrol.create_run(
                    run_id=second_run_id,
                    mode="parser_validation",
                    dataset_id=dataset_id,
                    metadata={"dataset_id": dataset_id},
                )
            ),
            device_id="SW2",
            device_name="SW2",
            vendor="Generic",
            model=model,
            command=command,
            command_canonical=command,
            raw_text="Interface Status\nGE1/0/2 DOWN\n",
            dataset_id=dataset_id,
            source_file_hash=token,
            import_fingerprint=f"{token}-second",
        )
        assert (
            parser.create_candidate(
                job_type="CREATE_TEMPLATE",
                vendor="Generic",
                model=model,
                family=None,
                profile=model,
                command=command,
                snapshot_id=second_snapshot_id,
                device_id="SW2",
            )
            == job_id
        )
        reconciliation = ParserJobRepository().retire_non_authoritative_samples(
            dataset_id=dataset_id,
            authoritative_run_id=second_run_id,
            actor="unit-test",
            reason="second import attempt is authoritative",
        )
        assert reconciliation["retired_sample_count"] == 1
        refreshed_job = ParserJobRepository().get_job(job_id)
        assert refreshed_job["sample_count"] == 1
        assert refreshed_job["retired_sample_count"] == 1
        with get_db_session() as db:
            candidate = db.get(ParserTemplate, template_id)
            assert candidate.status == "rejected"
            assert candidate.validation_json["candidate_sample_evidence"]["code"] == (
                "source_sample_set_revised"
            )
    finally:
        current_job = ParserJobRepository().get_job(job_id)
        if current_job and current_job["status"] in {
            "pending",
            "collecting",
            "running",
            "validating",
        }:
            ParserJobRepository().cancel_job(job_id)
        if ParserJobRepository().get_job(job_id):
            ParserJobRepository().delete_job(
                job_id,
                actor="unit-test",
                reason="clean immutable Parser evidence fixture",
            )
        with get_db_session() as db:
            db.query(RawEvidenceRecord).filter(
                RawEvidenceRecord.source_snapshot_id.in_(
                    [first_snapshot_id, locals().get("second_snapshot_id", "")]
                )
            ).delete(synchronize_session=False)
            db.query(CommandSnapshot).filter(
                CommandSnapshot.run_id.in_([run_id, second_run_id])
            ).delete(synchronize_session=False)
            db.query(PatrolRun).filter(
                PatrolRun.id.in_([run_id, second_run_id])
            ).delete(synchronize_session=False)
            db.query(PatrolDataset).filter_by(id=dataset_id).delete(
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

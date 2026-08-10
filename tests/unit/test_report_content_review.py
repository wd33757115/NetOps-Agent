from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scripts.test.check_item_report_content_acceptance import (
    build_controlled_manifest,
)
from src.core.check_items.report_content_review import (
    REQUIRED_CONTENT_CONFIRMATIONS,
    ReportContentReviewService,
)
from src.core.check_items.report_release import ReportReleaseService
from src.infrastructure.db.models import (
    Base,
    ReportClaimRecord,
    ReportContentReviewItemRecord,
    ReportContentReviewRecord,
    ReportManifestRecord,
    ReportReleaseRecord,
)


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


class _MemoryObjectStorage:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = dict(objects)

    def download_file(self, object_name: str) -> bytes | None:
        return self.objects.get(object_name)

    def sha256_object(self, object_name: str) -> dict[str, int | str] | None:
        content = self.objects.get(object_name)
        if content is None:
            return None
        return {
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }


@contextmanager
def _session_scope(session_factory):
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@pytest.fixture()
def content_review_runtime(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'report-content-review.db'}"
    )
    Base.metadata.create_all(
        engine,
        tables=[
            ReportManifestRecord.__table__,
            ReportClaimRecord.__table__,
            ReportContentReviewRecord.__table__,
            ReportContentReviewItemRecord.__table__,
            ReportReleaseRecord.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine)

    def scope():
        return _session_scope(session_factory)

    manifest = build_controlled_manifest()
    facts = manifest["source_facts_json"]
    artifact_names = ("markdown", "docx", "excel", "json", "evidence")
    object_keys = {
        name: f"reports/{manifest['id']}/{name}"
        for name in artifact_names
    }
    object_bytes = {
        object_keys[name]: f"controlled-{name}-artifact".encode("utf-8")
        for name in artifact_names
    }
    artifacts = {
        name: hashlib.sha256(object_bytes[object_keys[name]]).hexdigest()
        for name in artifact_names
    }
    storage = _MemoryObjectStorage(object_bytes)
    generated_at = manifest["generated_at"]
    with scope() as db:
        db.add(
            ReportManifestRecord(
                id=manifest["id"],
                patrol_batch_id=manifest["patrol_batch_id"],
                version=manifest["version"],
                schema_version=manifest["schema_version"],
                status=manifest["status"],
                target_manifest_digest=manifest[
                    "target_manifest_digest"
                ],
                scope_contract_digest=str(
                    facts.get("scope_contract_digest") or "6" * 64
                ),
                intended_target_matrix_digest=str(
                    facts.get("intended_target_matrix_digest")
                    or "7" * 64
                ),
                scope_decision_digest=str(
                    facts.get("scope_decision_digest") or "8" * 64
                ),
                source_qualification_digest=None,
                runtime_manifest_digest=manifest[
                    "runtime_manifest_digest"
                ],
                comparison_context_digests_json=[],
                check_execution_fact_digest=_digest(
                    facts["executions"]
                ),
                check_assessment_fact_digest=_digest(
                    facts["assessments"]
                ),
                risk_case_revision=_digest(facts["risks"]),
                evidence_index_digest=_digest(facts["evidence_index"]),
                raw_evidence_set_digest=manifest[
                    "raw_evidence_set_digest"
                ],
                report_purpose=manifest["report_purpose"],
                generator_version=manifest["generator_version"],
                source_facts_json=facts,
                source_digest=manifest["source_digest"],
                claim_ledger_digest=manifest["claim_ledger_digest"],
                claim_count=manifest["claim_count"],
                artifact_object_keys_json=object_keys,
                artifact_sha256_json=artifacts,
                generated_by="manifest-generator",
                generated_at=generated_at,
                report_cutoff_at=manifest["report_cutoff_at"],
                fact_ingestion_high_watermark=generated_at,
                qualification_fact_digest=manifest[
                    "qualification_fact_digest"
                ],
                coverage_fact_digest=manifest[
                    "coverage_fact_digest"
                ],
                created_at=generated_at,
            )
        )
        for claim in facts["claim_ledger"]["claims"]:
            db.add(
                ReportClaimRecord(
                    id=claim["claim_id"],
                    report_manifest_id=manifest["id"],
                    patrol_batch_id=manifest["patrol_batch_id"],
                    claim_code=claim["claim_code"],
                    claim_scope=claim["claim_scope"],
                    assertion_level=claim["assertion_level"],
                    allowed_phrase_code=claim[
                        "allowed_phrase_code"
                    ],
                    subject_json=claim["subject"],
                    fact_refs_json=claim["fact_refs"],
                    evidence_refs_json=claim["evidence_refs"],
                    limitation_refs_json=claim["limitation_refs"],
                    authority_context_digest=claim[
                        "authority_context_digest"
                    ],
                    source_facts_digest=claim[
                        "source_facts_digest"
                    ],
                    claim_digest=claim["claim_digest"],
                    created_at=generated_at,
                )
            )
    yield {
        "scope": scope,
        "session_factory": session_factory,
        "manifest": manifest,
        "artifacts": artifacts,
        "object_keys": object_keys,
        "object_bytes": object_bytes,
        "storage": storage,
    }
    engine.dispose()


def _accept_every_item(service, manifest_id: str):
    page = service.list_items(
        manifest_id,
        page=1,
        page_size=200,
        review_status="pending",
    )
    assert page["total"] == page["review"]["required_item_count"]
    assert page["total"] > 0
    return service.decide_items(
        manifest_id,
        review_item_ids=[item["review_item_id"] for item in page["items"]],
        decision="accepted",
        reviewed_by="content-reviewer",
        review_comment="逐项核对结论、证据和披露边界",
    )


def test_content_review_freezes_plan_and_supports_server_pagination(
    content_review_runtime,
):
    runtime = content_review_runtime
    service = ReportContentReviewService(
        runtime["scope"],
        object_storage=runtime["storage"],
    )
    manifest_id = runtime["manifest"]["id"]

    first = service.preview(manifest_id)
    second = service.preview(manifest_id)
    assert first["review_status"] == "not_started"
    assert first["machine_validation"]["passed"] is True
    assert first["review_plan_digest"] == second["review_plan_digest"]
    assert first["required_item_count"] > 0
    assert first["category_counts"]["p0_p1"] >= 1
    assert first["category_counts"]["unable_to_determine"] == 1

    with pytest.raises(ValueError, match="different from the manifest generator"):
        service.start(manifest_id, created_by="manifest-generator")

    review = service.start(manifest_id, created_by="content-reviewer")
    assert review["status"] == "in_review"
    assert review["required_item_count"] == first["required_item_count"]
    assert service.start(
        manifest_id,
        created_by="content-reviewer",
    )["id"] == review["id"]

    page_one = service.list_items(manifest_id, page=1, page_size=2)
    page_two = service.list_items(manifest_id, page=2, page_size=2)
    assert page_one["total"] == first["required_item_count"]
    assert len(page_one["items"]) == 2
    assert {
        item["review_item_id"] for item in page_one["items"]
    }.isdisjoint(
        {item["review_item_id"] for item in page_two["items"]}
    )
    assert all(
        "raw_text" not in json.dumps(item["summary_json"])
        for item in page_one["items"]
    )
    trace_page = service.list_items(
        manifest_id,
        page=1,
        page_size=200,
        category="check_item_traceability",
    )
    assert trace_page["total"] == 3
    assert all(
        "check_item_traceability" in item["review_reasons_json"]
        for item in trace_page["items"]
    )


def test_content_review_decisions_are_immutable_and_rejection_requires_revision(
    content_review_runtime,
):
    runtime = content_review_runtime
    service = ReportContentReviewService(
        runtime["scope"],
        object_storage=runtime["storage"],
    )
    manifest_id = runtime["manifest"]["id"]
    service.start(manifest_id, created_by="content-reviewer")
    item = service.list_items(manifest_id, page_size=1)["items"][0]

    review = service.decide_items(
        manifest_id,
        review_item_ids=[item["review_item_id"]],
        decision="rejected",
        reviewed_by="content-reviewer",
        review_comment="结论表述与证据不一致，需要生成新版本",
    )
    assert review["status"] == "revision_required"
    assert review["rejected_item_count"] == 1
    assert service.decide_items(
        manifest_id,
        review_item_ids=[item["review_item_id"]],
        decision="rejected",
        reviewed_by="content-reviewer",
        review_comment="结论表述与证据不一致，需要生成新版本",
    )["status"] == "revision_required"
    with pytest.raises(ValueError, match="immutable"):
        service.decide_items(
            manifest_id,
            review_item_ids=[item["review_item_id"]],
            decision="accepted",
            reviewed_by="content-reviewer",
            review_comment="不能覆盖既有决定",
        )
    with pytest.raises(ValueError, match="new manifest revision"):
        service.accept(
            manifest_id,
            accepted_by="content-reviewer",
            acceptance_comment="不应允许验收",
            confirmations=REQUIRED_CONTENT_CONFIRMATIONS,
            verified_artifact_sha256=runtime["artifacts"],
        )


def test_release_requires_exact_content_acceptance_and_independent_signer(
    content_review_runtime,
):
    runtime = content_review_runtime
    manifest_id = runtime["manifest"]["id"]
    content = ReportContentReviewService(
        runtime["scope"],
        object_storage=runtime["storage"],
    )
    release_service = ReportReleaseService(
        runtime["scope"],
        object_storage=runtime["storage"],
    )
    release_kwargs = {
        "signoff_comment": "同意作为受控验证报告签发",
        "disclosure_confirmations": [
            "facts_reviewed",
            "unknown_scope_disclosed",
            "artifact_digests_verified",
        ],
        "verified_artifact_sha256": runtime["artifacts"],
    }

    with pytest.raises(ValueError, match="has not been accepted"):
        release_service.release(
            manifest_id,
            signed_by="report-signer",
            **release_kwargs,
        )

    content.start(manifest_id, created_by="content-reviewer")
    accepted_items = _accept_every_item(content, manifest_id)
    assert accepted_items["pending_item_count"] == 0
    assert accepted_items["can_accept"] is True

    with pytest.raises(ValueError, match="evidence no longer matches"):
        content.accept(
            manifest_id,
            accepted_by="content-reviewer",
            acceptance_comment="摘要不一致必须失败",
            confirmations=REQUIRED_CONTENT_CONFIRMATIONS,
            verified_artifact_sha256={
                **runtime["artifacts"],
                "json": "f" * 64,
            },
        )

    accepted = content.accept(
        manifest_id,
        accepted_by="content-reviewer",
        acceptance_comment="已完成强制项、抽样项与可追溯性复核",
        confirmations=REQUIRED_CONTENT_CONFIRMATIONS,
        verified_artifact_sha256=runtime["artifacts"],
        accepted_at=datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc),
    )
    assert accepted["status"] == "accepted"
    assert len(accepted["acceptance_digest"]) == 64
    assert content.validate_for_release(manifest_id)[
        "acceptance_digest"
    ] == accepted["acceptance_digest"]

    json_key = runtime["object_keys"]["json"]
    original_json = runtime["storage"].objects[json_key]
    runtime["storage"].objects[json_key] = b"tampered-report-json"
    with pytest.raises(
        ValueError,
        match="content machine validation no longer passes",
    ):
        release_service.release(
            manifest_id,
            signed_by="report-signer",
            **release_kwargs,
        )
    runtime["storage"].objects[json_key] = original_json

    with pytest.raises(ValueError, match="different from the content acceptance reviewer"):
        release_service.release(
            manifest_id,
            signed_by="content-reviewer",
            **release_kwargs,
        )

    release = release_service.release(
        manifest_id,
        signed_by="report-signer",
        **release_kwargs,
    )
    assert release["status"] == "released"
    assert release["content_review_id"] == accepted["id"]
    assert release["content_acceptance_digest"] == accepted[
        "acceptance_digest"
    ]


def test_content_review_blocks_missing_or_digest_drifted_artifacts(
    content_review_runtime,
):
    runtime = content_review_runtime
    service = ReportContentReviewService(
        runtime["scope"],
        object_storage=runtime["storage"],
    )
    manifest_id = runtime["manifest"]["id"]
    object_key = runtime["object_keys"]["evidence"]
    original = runtime["storage"].objects.pop(object_key)

    missing = service.preview(manifest_id)
    assert missing["can_start"] is False
    assert "artifact_objects_verified" in missing["machine_validation"][
        "failed_check_ids"
    ]
    artifact_check = next(
        check
        for check in missing["machine_validation"]["checks"]
        if check["id"] == "artifact_objects_verified"
    )
    assert artifact_check["detail"]["artifacts"]["evidence"]["status"] == (
        "object_missing"
    )
    with pytest.raises(ValueError, match="artifact_objects_verified"):
        service.start(manifest_id, created_by="content-reviewer")

    runtime["storage"].objects[object_key] = b"digest-drift"
    drifted = service.preview(manifest_id)
    artifact_check = next(
        check
        for check in drifted["machine_validation"]["checks"]
        if check["id"] == "artifact_objects_verified"
    )
    assert artifact_check["detail"]["artifacts"]["evidence"]["status"] == (
        "digest_mismatch"
    )

    runtime["storage"].objects[object_key] = original
    assert service.preview(manifest_id)["can_start"] is True


def test_content_review_scales_to_one_thousand_items_and_resumes(
    content_review_runtime,
):
    runtime = content_review_runtime
    manifest_id = runtime["manifest"]["id"]
    service = ReportContentReviewService(
        runtime["scope"],
        object_storage=runtime["storage"],
    )
    review = service.start(manifest_id, created_by="content-reviewer")
    now = datetime.now(timezone.utc)
    with runtime["scope"]() as db:
        row = db.get(ReportContentReviewRecord, review["id"])
        db.query(ReportContentReviewItemRecord).filter(
            ReportContentReviewItemRecord.review_id == row.id
        ).delete(synchronize_session=False)
        db.bulk_save_objects(
            [
                ReportContentReviewItemRecord(
                    id=f"scale-row-{index:04d}",
                    review_id=row.id,
                    review_item_id=f"{index:064x}",
                    category="check_item_traceability",
                    review_reasons_json=["check_item_traceability"],
                    stratum_key=f"device-{index % 100:03d}",
                    fact_type="assessment",
                    fact_id=f"assessment-{index:04d}",
                    device_id=f"device-{index % 100:03d}",
                    check_item_id=f"system.check-{index % 20:02d}",
                    severity=None,
                    conclusion_status="healthy",
                    evidence_refs_json=[f"snapshot-{index:04d}"],
                    summary_json={"ordinal": index},
                    review_status="pending",
                    created_at=now,
                )
                for index in range(1000)
            ]
        )
        row.required_item_count = 1000
        row.accepted_item_count = 0
        row.rejected_item_count = 0
        row.updated_at = now

    first_page = service.list_items(manifest_id, page=1, page_size=50)
    last_page = service.list_items(manifest_id, page=20, page_size=50)
    assert first_page["total"] == 1000
    assert len(first_page["items"]) == len(last_page["items"]) == 50
    assert {
        item["review_item_id"] for item in first_page["items"]
    }.isdisjoint({item["review_item_id"] for item in last_page["items"]})

    first_ids = [item["review_item_id"] for item in first_page["items"]]
    first_decision = service.decide_items(
        manifest_id,
        review_item_ids=first_ids,
        decision="accepted",
        reviewed_by="content-reviewer",
        review_comment="accepted after evidence review",
    )
    assert first_decision["accepted_item_count"] == 50

    resumed = ReportContentReviewService(
        runtime["scope"],
        object_storage=runtime["storage"],
    )
    assert resumed.list_items(
        manifest_id,
        page=1,
        page_size=50,
        review_status="pending",
    )["total"] == 950
    assert resumed.decide_items(
        manifest_id,
        review_item_ids=first_ids,
        decision="accepted",
        reviewed_by="content-reviewer",
        review_comment="accepted after evidence review",
    )["accepted_item_count"] == 50

    while True:
        pending = resumed.list_items(
            manifest_id,
            page=1,
            page_size=200,
            review_status="pending",
        )
        if not pending["items"]:
            break
        resumed.decide_items(
            manifest_id,
            review_item_ids=[
                item["review_item_id"] for item in pending["items"]
            ],
            decision="accepted",
            reviewed_by="content-reviewer",
            review_comment="accepted after evidence review",
        )
        resumed = ReportContentReviewService(
            runtime["scope"],
            object_storage=runtime["storage"],
        )

    final = resumed.get_for_manifest(manifest_id)
    assert final["accepted_item_count"] == 1000
    assert final["pending_item_count"] == 0
    assert final["can_accept"] is True

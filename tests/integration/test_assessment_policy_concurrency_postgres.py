from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest

from src.core.check_items.assessment_policy_assets import AssessmentPolicyAssetService
from src.infrastructure.db.models import (
    AssessmentPolicyBindingRecord,
    AssessmentPolicyDecisionRecord,
    AssessmentPolicyProfileRecord,
    AssessmentPolicyVersionRecord,
    CheckItemRecord,
    CheckItemVersionRecord,
)
from src.infrastructure.db.postgres import get_db_session


pytestmark = pytest.mark.integration


def test_concurrent_identical_semantic_reviews_are_idempotent(postgres_available):
    del postgres_available
    suffix = uuid.uuid4().hex
    check_item_id = f"test.assessment-policy-concurrency.{suffix}"
    check_item_version_id = f"check-version-{suffix}"
    now = datetime.now(timezone.utc)
    service = AssessmentPolicyAssetService()
    profile_id = ""
    version_id = ""
    try:
        with get_db_session() as db:
            db.add(
                CheckItemRecord(
                    id=check_item_id,
                    name="Assessment policy concurrency",
                    category="integration",
                    status="active",
                    current_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(
                CheckItemVersionRecord(
                    id=check_item_version_id,
                    check_item_id=check_item_id,
                    version=1,
                    result_mode="structured",
                    assessment_mode="health",
                    canonical_contract_json={
                        "observations": [
                            {
                                "observation_type": "test.metric",
                                "semantic_type": "gauge",
                                "unit": "percent",
                            }
                        ]
                    },
                    report_projection="integration.assessment-policy",
                    status="active",
                    digest="c" * 64,
                    created_by="integration-catalog-owner",
                    created_at=now,
                    activated_at=now,
                )
            )

        draft = service.create_version(
            check_item_id=check_item_id,
            check_item_version_id=check_item_version_id,
            observation_type="test.metric",
            policy_role="high",
            name="Concurrent threshold review",
            policy={"value": 80, "unit": "percent", "operator": "gte"},
            evidence_refs=["integration:concurrency"],
            provenance={"source": "integration_test"},
            created_by="policy-author",
            idempotency_key=f"create-{suffix}",
        )
        profile_id = str(draft["profile_id"])
        version_id = str(draft["id"])
        service.submit(
            version_id,
            actor="policy-author",
            reason="submit for concurrent review",
            idempotency_key=f"submit-{suffix}",
        )

        barrier = Barrier(2)

        def review() -> dict:
            barrier.wait(timeout=10)
            return AssessmentPolicyAssetService().semantic_review(
                version_id,
                decision="approved",
                actor="network-sme",
                reason="same immutable policy and evidence",
                idempotency_key=f"semantic-{suffix}",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [future.result(timeout=20) for future in [
                executor.submit(review),
                executor.submit(review),
            ]]

        assert {result["status"] for result in results} == {"pending_approval"}
        with get_db_session() as db:
            decisions = db.query(AssessmentPolicyDecisionRecord).filter(
                AssessmentPolicyDecisionRecord.policy_version_id == version_id,
                AssessmentPolicyDecisionRecord.stage == "semantic_review",
            ).all()
            assert len(decisions) == 1
            assert decisions[0].idempotency_key == f"semantic-{suffix}"
    finally:
        with get_db_session() as db:
            if version_id:
                db.query(AssessmentPolicyBindingRecord).filter(
                    AssessmentPolicyBindingRecord.policy_version_id == version_id
                ).delete(synchronize_session=False)
                db.query(AssessmentPolicyDecisionRecord).filter(
                    AssessmentPolicyDecisionRecord.policy_version_id == version_id
                ).delete(synchronize_session=False)
                db.query(AssessmentPolicyVersionRecord).filter(
                    AssessmentPolicyVersionRecord.id == version_id
                ).delete(synchronize_session=False)
            if profile_id:
                db.query(AssessmentPolicyProfileRecord).filter(
                    AssessmentPolicyProfileRecord.id == profile_id
                ).delete(synchronize_session=False)
            db.query(CheckItemVersionRecord).filter(
                CheckItemVersionRecord.id == check_item_version_id
            ).delete(synchronize_session=False)
            db.query(CheckItemRecord).filter(
                CheckItemRecord.id == check_item_id
            ).delete(synchronize_session=False)

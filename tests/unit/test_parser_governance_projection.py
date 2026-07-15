from __future__ import annotations

import uuid

from src.core.parser.repository import ParserGovernanceProjectionRepository
from src.core.rule_control.repository import RuleGenerationJobRepository
from src.infrastructure.db.models import RuleGenerationJobRecord
from src.infrastructure.db.postgres import get_db_session


def test_governance_projection_reads_rule_jobs_before_session_expiry():
    source_ref = f"governance-projection:{uuid.uuid4().hex}"
    job = RuleGenerationJobRepository().create_job(
        job_type="EVENT_RULE",
        source_ref=source_ref,
        created_by="pytest",
        validation_report={"replay": {"collision_count": 1}},
    )

    try:
        result = ParserGovernanceProjectionRepository().governance_alert_summary()

        assert "alerts" in result
        assert any(
            item["code"] == "rule_replay_collision" and item["count"] >= 1
            for item in result["alerts"]
        )
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id == job["id"]
            ).delete(synchronize_session=False)

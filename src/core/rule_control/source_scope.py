"""Domain guards for rule-asset source jobs."""

from __future__ import annotations

from typing import Any

from src.infrastructure.db.models import OfflinePatrolJobRecord

RULE_ASSET_SOURCE_PURPOSE = "rule_asset_source"


def require_rule_asset_source_job(db: Any, job_id: str) -> OfflinePatrolJobRecord:
    """Return a governed asset-source job and reject patrol execution jobs."""

    job = db.get(OfflinePatrolJobRecord, job_id)
    if job is None:
        raise KeyError("rule asset source job not found")
    if job.purpose != RULE_ASSET_SOURCE_PURPOSE:
        raise ValueError(
            "rule assets can only be generated from Parser control asset-source jobs"
        )
    return job

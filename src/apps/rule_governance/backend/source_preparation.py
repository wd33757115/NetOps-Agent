"""Prepare every split CLI output locally before admitting an import to classification."""

from collections import Counter

from src.apps.rule_governance.backend.parser.sanitization import sanitize_parser_sample
from src.infrastructure.db.models import CommandSnapshot, OfflinePatrolJobRecord, OfflinePatrolPeriodRecord
from src.infrastructure.db.postgres import get_db_session


def prepare_source_samples(job_id: str, execution_key: str) -> dict:
    with get_db_session() as db:
        job = db.query(OfflinePatrolJobRecord).filter_by(id=job_id).with_for_update().one()
        if (job.purpose != "rule_asset_source" or job.status != "running"
                or job.execution_idempotency_key != execution_key):
            raise ValueError("样本导入状态已变化，请重新检查任务")
        run_ids = [p.patrol_run_id for p in db.query(OfflinePatrolPeriodRecord)
                   .filter_by(job_id=job_id).all() if p.patrol_run_id]
        counts = Counter()
        count = 0
        for snapshot in db.query(CommandSnapshot).filter(CommandSnapshot.run_id.in_(run_ids)).yield_per(100):
            sanitized = sanitize_parser_sample(snapshot.raw_text or "")
            snapshot.metadata_json = {
                **dict(snapshot.metadata_json or {}),
                "rule_source_preparation": {
                    "status": "sanitized", "version": sanitized.version,
                    "source_sha256": snapshot.raw_text_hash,
                    "sha256": sanitized.sha256, "text": sanitized.text,
                    "redactions": sanitized.redaction_summary,
                },
            }
            count += 1
            counts.update(sanitized.redaction_summary)
        return {"status": "completed", "sample_count": count, "redactions": dict(counts)}

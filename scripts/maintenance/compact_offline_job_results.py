"""Back up and compact duplicated offline task payloads; evidence tables are untouched.

Run with ``python -m scripts.maintenance.compact_offline_job_results`` to preview.
Use ``--apply --backup-dir <directory>`` while the inspection App is disabled.
"""

import argparse
import gzip
import json
from pathlib import Path

from sqlalchemy import text

from src.core.patrol.repository import _compact_analysis_result
from src.infrastructure.db.postgres import get_db_session


def compact_result(result):
    return {
        **result,
        "steps": {
            name: _compact_analysis_result(value) if isinstance(value, dict) else value
            for name, value in result.get("steps", {}).items()
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    if args.apply and not args.backup_dir:
        parser.error("--apply requires --backup-dir")
    if args.backup_dir:
        args.backup_dir.mkdir(parents=True, exist_ok=True)
    with get_db_session() as db:
        jobs = list(db.execute(text("""
            SELECT id, octet_length(result_json::text) FROM netops_offline_patrol_jobs
            WHERE purpose = 'offline_patrol' AND status IN ('completed','failed','cancelled')
              AND octet_length(result_json::text) > 1000000
            ORDER BY created_at
        """)))
    for job_id, size in jobs:
        if not args.apply:
            print(job_id, "bytes", size)
            continue
        with get_db_session() as db:
            raw = db.execute(text("""
                SELECT result_json::text FROM netops_offline_patrol_jobs
                WHERE id=:id AND status IN ('completed','failed','cancelled') FOR UPDATE
            """), {"id": job_id}).scalar_one_or_none()
            if not raw:
                continue
            # Exclusive creation prevents overwriting a prior recovery copy.
            with gzip.open(args.backup_dir / f"{job_id}.json.gz", "xt", encoding="utf-8") as f:
                f.write(raw)
            result = json.loads(raw)
            compact = json.dumps(compact_result(result), ensure_ascii=False)
            db.execute(text("""
                UPDATE netops_offline_patrol_jobs SET result_json=CAST(:result AS json)
                WHERE id=:id
            """), {"id": job_id, "result": compact})
            print(job_id, "bytes", size, "->", len(compact.encode("utf-8")))
            del raw, result, compact


if __name__ == "__main__":
    main()

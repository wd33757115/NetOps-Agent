"""Enforce one durable TextFSM generation task per Parser asset.

Revision ID: 0043_parser_job_asset_uniqueness
Revises: 0042_parser_generation_attempts

Duplicate history is intentionally hard-deleted. Samples and attempts are not
merged into the retained task.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import timezone
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0043_parser_job_asset_uniqueness"
down_revision = "0042_parser_generation_attempts"
branch_labels = None
depends_on = None

OPEN_STATUSES = {"pending", "collecting", "running", "validating"}
STATUS_PRIORITY = {
    "running": 80,
    "validating": 70,
    "pending": 60,
    "collecting": 50,
    "succeeded": 40,
    "failed": 30,
    "cancelled": 20,
    "superseded": 10,
}


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _identity(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        _normalize(row.get("vendor")),
        _normalize(row.get("profile")),
        _normalize(row.get("canonical_command")),
    )


def _asset_key(row: dict[str, Any]) -> str:
    normalized = "|".join(("parser-template-asset-v2", *_identity(row)))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _timestamp(value: Any) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _expanding_statement(sql: str, parameter: str = "ids") -> sa.TextClause:
    return sa.text(sql).bindparams(sa.bindparam(parameter, expanding=True))


def _delete_duplicate_history(bind: Any, duplicate_ids: list[str]) -> None:
    if not duplicate_ids:
        return

    historical_template_ids = [
        str(value)
        for value in bind.execute(
            _expanding_statement(
                """
                SELECT id
                FROM parser_templates
                WHERE source_job_id IN :ids AND status <> 'active'
                """
            ),
            {"ids": duplicate_ids},
        ).scalars()
    ]

    bind.execute(
        _expanding_statement(
            """
            UPDATE parser_templates
            SET source_job_id = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE source_job_id IN :ids AND status = 'active'
            """
        ),
        {"ids": duplicate_ids},
    )
    if historical_template_ids:
        bind.execute(
            _expanding_statement(
                """
                DELETE FROM netops_observation_contracts
                WHERE status <> 'active'
                  AND (source_job_id IN :job_ids OR source_template_id IN :template_ids)
                """,
                parameter="job_ids",
            ).bindparams(sa.bindparam("template_ids", expanding=True)),
            {
                "job_ids": duplicate_ids,
                "template_ids": historical_template_ids,
            },
        )
        bind.execute(
            _expanding_statement(
                """
                UPDATE netops_observation_contracts
                SET source_job_id = CASE
                        WHEN source_job_id IN :job_ids THEN NULL
                        ELSE source_job_id
                    END,
                    source_template_id = CASE
                        WHEN source_template_id IN :template_ids THEN NULL
                        ELSE source_template_id
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'active'
                  AND (source_job_id IN :job_ids OR source_template_id IN :template_ids)
                """,
                parameter="job_ids",
            ).bindparams(sa.bindparam("template_ids", expanding=True)),
            {
                "job_ids": duplicate_ids,
                "template_ids": historical_template_ids,
            },
        )
        source_refs = [f"parser_template:{template_id}" for template_id in historical_template_ids]
        bind.execute(
            _expanding_statement(
                """
                UPDATE netops_rule_generation_jobs
                SET source_ref = NULL, asset_ref = NULL, asset_id = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'active'
                  AND (asset_id IN :ids OR source_ref IN :source_refs)
                """
            ).bindparams(sa.bindparam("source_refs", expanding=True)),
            {"ids": historical_template_ids, "source_refs": source_refs},
        )
        bind.execute(
            _expanding_statement(
                """
                DELETE FROM netops_rule_generation_jobs
                WHERE status <> 'active'
                  AND (asset_id IN :ids OR source_ref IN :source_refs)
                """
            ).bindparams(sa.bindparam("source_refs", expanding=True)),
            {"ids": historical_template_ids, "source_refs": source_refs},
        )
        bind.execute(
            _expanding_statement("DELETE FROM parser_backfill_jobs WHERE template_id IN :ids"),
            {"ids": historical_template_ids},
        )
        bind.execute(
            _expanding_statement("DELETE FROM snapshot_parse_results WHERE template_id IN :ids"),
            {"ids": historical_template_ids},
        )
        bind.execute(
            _expanding_statement("DELETE FROM parser_templates WHERE id IN :ids"),
            {"ids": historical_template_ids},
        )
    else:
        bind.execute(
            _expanding_statement(
                """
                DELETE FROM netops_observation_contracts
                WHERE source_job_id IN :ids AND status <> 'active'
                """
            ),
            {"ids": duplicate_ids},
        )
        bind.execute(
            _expanding_statement(
                """
                UPDATE netops_observation_contracts
                SET source_job_id = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE source_job_id IN :ids AND status = 'active'
                """
            ),
            {"ids": duplicate_ids},
        )

    bind.execute(
        _expanding_statement("DELETE FROM parser_job_samples WHERE job_id IN :ids"),
        {"ids": duplicate_ids},
    )
    bind.execute(
        _expanding_statement("DELETE FROM parser_generation_attempts WHERE job_id IN :ids"),
        {"ids": duplicate_ids},
    )
    bind.execute(
        _expanding_statement("DELETE FROM parser_outbox_events WHERE aggregate_id IN :ids"),
        {"ids": duplicate_ids},
    )
    bind.execute(
        _expanding_statement("DELETE FROM parser_template_jobs WHERE id IN :ids"),
        {"ids": duplicate_ids},
    )


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE parser_template_jobs IN ACCESS EXCLUSIVE MODE"))
    rows = [
        dict(row)
        for row in bind.execute(
            sa.text(
                """
                SELECT job.id, job.vendor, job.profile, job.canonical_command,
                       job.status, job.created_at, job.updated_at,
                       EXISTS (
                           SELECT 1 FROM parser_templates template
                           WHERE template.source_job_id = job.id
                             AND template.status = 'active'
                       ) AS has_active_template
                FROM parser_template_jobs job
                """
            )
        ).mappings()
    ]

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[_identity(row)].append(row)

    duplicate_ids: list[str] = []
    for group in groups.values():
        if len(group) < 2:
            continue
        keeper = max(
            group,
            key=lambda row: (
                row["status"] in OPEN_STATUSES,
                STATUS_PRIORITY.get(str(row["status"]), 0),
                bool(row["has_active_template"]),
                _timestamp(row.get("updated_at") or row.get("created_at")),
                str(row["id"]),
            ),
        )
        duplicate_ids.extend(str(row["id"]) for row in group if row["id"] != keeper["id"])

    _delete_duplicate_history(bind, duplicate_ids)

    remaining_rows = bind.execute(
        sa.text(
            """
            SELECT id, vendor, profile, canonical_command
            FROM parser_template_jobs
            """
        )
    ).mappings()
    for row in remaining_rows:
        bind.execute(
            sa.text(
                """
                UPDATE parser_template_jobs
                SET idempotency_key = :asset_key, updated_at = CURRENT_TIMESTAMP
                WHERE id = :job_id
                """
            ),
            {"asset_key": _asset_key(dict(row)), "job_id": row["id"]},
        )

    op.drop_index("uq_parser_job_open_key", table_name="parser_template_jobs")
    op.create_index(
        "uq_parser_job_asset_key",
        "parser_template_jobs",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_parser_job_asset_key", table_name="parser_template_jobs")
    op.create_index(
        "uq_parser_job_open_key",
        "parser_template_jobs",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','collecting','running','validating')"),
    )

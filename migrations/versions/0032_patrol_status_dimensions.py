"""Separate collection, parser, fact, and normalization status dimensions.

Revision ID: 0032_patrol_status_dimensions
Revises: 0031_patrol_fact_ownership
"""

import sqlalchemy as sa
from alembic import op

revision = "0032_patrol_status_dimensions"
down_revision = "0031_patrol_fact_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "netops_patrol_runs",
        sa.Column(
            "parser_readiness_status",
            sa.String(32),
            server_default="unknown",
            nullable=False,
        ),
    )
    op.add_column(
        "netops_patrol_runs",
        sa.Column("fact_readiness_status", sa.String(32), server_default="unknown", nullable=False),
    )
    op.create_index(
        "ix_netops_patrol_runs_parser_readiness_status",
        "netops_patrol_runs",
        ["parser_readiness_status"],
    )
    op.create_index(
        "ix_netops_patrol_runs_fact_readiness_status",
        "netops_patrol_runs",
        ["fact_readiness_status"],
    )

    for name, default in (
        ("execution_status", "failed"),
        ("parse_status", "not_attempted"),
        ("fact_status", "blocked_by_collection"),
        ("normalization_status", "blocked"),
    ):
        op.add_column(
            "netops_patrol_collection_results",
            sa.Column(name, sa.String(32), server_default=default, nullable=False),
        )
        op.create_index(
            f"ix_netops_patrol_collection_results_{name}",
            "netops_patrol_collection_results",
            [name],
        )
    op.add_column(
        "netops_patrol_collection_results",
        sa.Column("parser_candidate_id", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_netops_patrol_collection_results_parser_candidate_id",
        "netops_patrol_collection_results",
        ["parser_candidate_id"],
    )

    op.execute(
        """
        UPDATE netops_patrol_collection_results
        SET parse_status = CASE
                WHEN COALESCE(metadata_json->>'parse_status', '') IN (
                    'parsed', 'parse_empty', 'ignored', 'template_missing',
                    'parse_failed', 'identity_unknown'
                ) THEN metadata_json->>'parse_status'
                WHEN status IN ('parsed', 'complete') THEN 'parsed'
                WHEN status = 'empty' THEN 'parse_empty'
                WHEN status = 'ignored' THEN 'ignored'
                ELSE 'not_attempted'
            END,
            execution_status = CASE
                WHEN COALESCE(metadata_json->>'parse_status', '') IN (
                    'parsed', 'parse_empty', 'ignored', 'template_missing',
                    'parse_failed', 'identity_unknown'
                ) THEN 'complete'
                WHEN status IN ('complete', 'parsed', 'empty', 'ignored') THEN 'complete'
                WHEN status = 'timeout' THEN 'timeout'
                WHEN status = 'cancelled' THEN 'cancelled'
                ELSE 'failed'
            END
        """
    )
    op.execute(
        """
        UPDATE netops_patrol_collection_results
        SET fact_status = CASE
                WHEN execution_status <> 'complete' THEN 'blocked_by_collection'
                WHEN parse_status = 'parsed' THEN 'ready'
                WHEN parse_status = 'parse_empty' THEN 'empty'
                WHEN parse_status = 'ignored' THEN 'ignored'
                WHEN parse_status = 'identity_unknown' THEN 'blocked_by_identity'
                ELSE 'blocked_by_parser'
            END,
            normalization_status = CASE
                WHEN execution_status <> 'complete' THEN 'blocked'
                WHEN parse_status = 'parsed' THEN 'pending'
                WHEN parse_status = 'parse_empty' THEN 'empty'
                WHEN parse_status = 'ignored' THEN 'skipped'
                ELSE 'blocked'
            END,
            status = execution_status
        """
    )
    op.execute(
        """
        UPDATE netops_patrol_runs AS run
        SET parser_readiness_status = summary.parser_status,
            fact_readiness_status = summary.fact_status
        FROM (
            SELECT run_id,
                   CASE
                       WHEN count(*) = 0 THEN 'unknown'
                       WHEN bool_and(
                           parse_status IN ('parsed', 'parse_empty', 'ignored')
                       ) THEN 'ready'
                       WHEN bool_or(
                           parse_status IN ('parsed', 'parse_empty', 'ignored')
                       ) THEN 'partial'
                       ELSE 'blocked'
                   END AS parser_status,
                   CASE
                       WHEN count(*) = 0 THEN 'unknown'
                       WHEN bool_and(fact_status IN ('ready', 'empty', 'ignored')) THEN 'ready'
                       WHEN bool_or(fact_status IN ('ready', 'empty', 'ignored')) THEN 'partial'
                       ELSE 'blocked'
                   END AS fact_status
            FROM netops_patrol_collection_results
            GROUP BY run_id
        ) AS summary
        WHERE summary.run_id = run.id
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_netops_patrol_collection_results_parser_candidate_id",
        table_name="netops_patrol_collection_results",
    )
    op.drop_column("netops_patrol_collection_results", "parser_candidate_id")
    for name in (
        "normalization_status",
        "fact_status",
        "parse_status",
        "execution_status",
    ):
        op.drop_index(
            f"ix_netops_patrol_collection_results_{name}",
            table_name="netops_patrol_collection_results",
        )
        op.drop_column("netops_patrol_collection_results", name)
    op.drop_index(
        "ix_netops_patrol_runs_fact_readiness_status",
        table_name="netops_patrol_runs",
    )
    op.drop_index(
        "ix_netops_patrol_runs_parser_readiness_status",
        table_name="netops_patrol_runs",
    )
    op.drop_column("netops_patrol_runs", "fact_readiness_status")
    op.drop_column("netops_patrol_runs", "parser_readiness_status")

"""Allow explicit post-parse coverage failures.

Revision ID: 0066_coverage_not_evaluable
Revises: 0065_report_fact_high_watermark
"""

from __future__ import annotations

from alembic import op

revision = "0066_coverage_not_evaluable"
down_revision = "0065_report_fact_high_watermark"
branch_labels = None
depends_on = None


def _create_status_constraint(*, include_not_evaluable: bool) -> None:
    statuses = [
        "complete",
        "partial",
        "empty",
        "unbounded",
        "unknown",
        "not_applicable",
    ]
    if include_not_evaluable:
        statuses.append("not_evaluable")
    quoted = ", ".join(f"'{item}'" for item in statuses)
    op.create_check_constraint(
        "ck_coverage_decision_status",
        "netops_coverage_decisions",
        f"coverage_status IN ({quoted})",
    )


def upgrade() -> None:
    op.drop_constraint(
        "ck_coverage_decision_status",
        "netops_coverage_decisions",
        type_="check",
    )
    _create_status_constraint(include_not_evaluable=True)


def downgrade() -> None:
    op.execute(
        "UPDATE netops_coverage_decisions "
        "SET coverage_status = 'unknown' "
        "WHERE coverage_status = 'not_evaluable'"
    )
    op.drop_constraint(
        "ck_coverage_decision_status",
        "netops_coverage_decisions",
        type_="check",
    )
    _create_status_constraint(include_not_evaluable=False)

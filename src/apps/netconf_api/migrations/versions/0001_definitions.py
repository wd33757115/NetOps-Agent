"""NETCONF API: definitions and their published versions, device capabilities, trials."""

import sqlalchemy as sa
from alembic import op

revision = "netconf_api_0001"
down_revision = None


def upgrade():
    op.create_table(
        "app_netconf_definitions",
        sa.Column("id", sa.String(120), primary_key=True),
        sa.Column("draft", sa.JSON()),
        sa.Column("draft_by", sa.String(64)),
        sa.Column("draft_at", sa.DateTime(timezone=True)),
        sa.Column("published_version", sa.Integer()),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.create_table(
        "app_netconf_definition_versions",
        sa.Column("definition_id", sa.String(120), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("check_item", sa.String(120)),
        sa.Column("published_by", sa.String(64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(500)),
    )
    op.create_table(
        "app_netconf_capabilities",
        sa.Column("device_id", sa.String(64), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("checked_by", sa.String(64), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "app_netconf_trials",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False, index=True),
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("definition_id", sa.String(120), nullable=False),
        sa.Column("version", sa.Integer()),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("message", sa.String(500)),
        sa.Column("read_count", sa.Integer()),
        sa.Column("row_count", sa.Integer()),
        sa.Column("problem_count", sa.Integer()),
        sa.Column("rows", sa.JSON(), nullable=False),
        sa.Column("problems", sa.JSON(), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting business data")

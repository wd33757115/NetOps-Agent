"""Vision-model readings of doc-set figures, cached by image content."""

import sqlalchemy as sa
from alembic import op

revision = "materials_0004"
down_revision = "materials_0003"


def upgrade():
    op.create_table(
        "app_materials_figure_readings",
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("model", sa.String(64), primary_key=True),
        sa.Column("prompt_version", sa.String(16), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("app_materials_figure_readings")

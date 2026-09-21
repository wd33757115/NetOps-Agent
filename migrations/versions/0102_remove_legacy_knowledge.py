"""Remove the retired knowledge-base index; source/vector files are retired separately."""

import sqlalchemy as sa
from alembic import op

revision = "0102_remove_legacy_knowledge"
down_revision = "0101_patrol_report_templates"
branch_labels = None
depends_on = None


def upgrade():
    # No CASCADE: an unexpected consumer must block deletion rather than lose data.
    op.execute(sa.text("DROP TABLE IF EXISTS netops_knowledge_index"))


def downgrade():
    raise RuntimeError("Legacy knowledge data was deleted; restore a prior backup to roll back")

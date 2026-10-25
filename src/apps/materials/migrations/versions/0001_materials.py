"""Own source revisions; copy historical snapshots without changing their identity.

Deployment must drain the old Assistant before this migration. Its old tables
remain untouched for backup verification; all new writes use this package.
"""

import sqlalchemy as sa
from alembic import op

revision = "materials_0001"
down_revision = None


def upgrade():
    op.create_table(
        "app_materials_documents",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("current_revision", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
    )
    op.create_table(
        "app_materials_revisions",
        sa.Column("document_id", sa.String(32), primary_key=True),
        sa.Column("revision", sa.String(64), primary_key=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("sections_json", sa.JSON(), nullable=False),
        sa.Column("source_json", sa.JSON()),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    connection = op.get_bind()
    names = set(sa.inspect(connection).get_table_names())
    legacy = {"app_assistant_documents", "app_assistant_revisions"}
    if names & legacy and not legacy <= names:
        raise RuntimeError("Incomplete historical source tables; restore before migration")
    if legacy <= names:
        metadata = sa.MetaData()
        for suffix in ("documents", "revisions"):
            old = sa.Table("app_assistant_" + suffix, metadata, autoload_with=connection)
            new = sa.Table("app_materials_" + suffix, metadata, autoload_with=connection)
            columns = [c.name for c in new.columns if c.name in old.c]
            connection.execute(
                new.insert().from_select(columns, sa.select(*(old.c[c] for c in columns)))
            )
            if connection.scalar(sa.select(sa.func.count()).select_from(old)) != connection.scalar(
                sa.select(sa.func.count()).select_from(new)
            ):
                raise RuntimeError("Historical source copy count mismatch")


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting source history")

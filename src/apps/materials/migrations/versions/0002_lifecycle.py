"""Library lifecycle and current-file deduplication; historical revisions stay immutable."""

import sqlalchemy as sa
from alembic import op

revision = "materials_0002"
down_revision = "materials_0001"


def upgrade():
    op.add_column(
        "app_materials_documents",
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
    )
    op.add_column("app_materials_documents", sa.Column("source_key", sa.String(64)))
    op.create_index(
        "uq_materials_source_key", "app_materials_documents", ["source_key"], unique=True
    )
    connection = op.get_bind()
    meta = sa.MetaData()
    documents = sa.Table("app_materials_documents", meta, autoload_with=connection)
    revisions = sa.Table("app_materials_revisions", meta, autoload_with=connection)
    rows = connection.execute(
        sa.select(documents.c.id, revisions.c.source_sha256)
        .join(
            revisions,
            (documents.c.id == revisions.c.document_id)
            & (documents.c.current_revision == revisions.c.revision),
        )
        .order_by(documents.c.id)
    ).all()
    seen = set()
    for document_id, checksum in rows:
        # Existing duplicates keep their identities and citations. New duplicates are rejected.
        if checksum not in seen:
            connection.execute(
                documents.update().where(documents.c.id == document_id).values(source_key=checksum)
            )
            seen.add(checksum)


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting source history")

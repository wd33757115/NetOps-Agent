"""Product doc sets (CHM): pages, content-addressed texts, figures and import jobs."""

import sqlalchemy as sa
from alembic import op

revision = "materials_0003"
down_revision = "materials_0002"


def upgrade():
    op.add_column(
        "app_materials_documents",
        sa.Column("kind", sa.String(16), nullable=False, server_default="document"),
    )
    op.create_table(
        "app_materials_texts",
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "app_materials_docset_files",
        sa.Column("document_id", sa.String(32), primary_key=True),
        sa.Column("revision", sa.String(64), primary_key=True),
        sa.Column("path", sa.String(512), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("lines", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(512)),
        sa.Column("title_path", sa.JSON()),
        sa.Column("toc_order", sa.Integer()),
        sa.Column("doc_type", sa.String(32)),
        sa.Column("features", sa.JSON()),
        sa.Column("source_path", sa.String(512)),
        sa.Column("canonical_path", sa.String(512)),
        sa.Column("figures", sa.JSON()),
    )
    op.create_index(
        "ix_app_materials_docset_files_text", "app_materials_docset_files", ["text_sha256"]
    )
    op.create_table(
        "app_materials_docset_figures",
        sa.Column("document_id", sa.String(32), primary_key=True),
        sa.Column("revision", sa.String(64), primary_key=True),
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("ext", sa.String(8), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_app_materials_docset_figures_sha", "app_materials_docset_figures", ["sha256"]
    )
    op.create_table(
        "app_materials_import_jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("upload_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(32)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error", sa.String(2000)),
        sa.Column("document_id", sa.String(32)),
        sa.Column("expected_revision", sa.String(64)),
        sa.Column("source_sha256", sa.String(64)),
        sa.Column("normalized_sha256", sa.String(64)),
        sa.Column("summary_json", sa.JSON()),
        sa.Column("manifest_json", sa.JSON()),
        sa.Column("publish_json", sa.JSON()),
        sa.Column("revision", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_app_materials_import_jobs_status", "app_materials_import_jobs", ["status"])


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting source history")

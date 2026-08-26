"""Clear raw text hash placeholders from command snapshots.

Revision ID: 0097_clear_raw_hash_placeholders
Revises: 0096_command_adapter_spec
"""

from alembic import op


revision = "0097_clear_raw_hash_placeholders"
down_revision = "0096_command_adapter_spec"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Keep raw-hash commands unstructured without deleting CLI evidence."""

    op.execute(
        """
        UPDATE netops_command_snapshots
        SET structured_json = NULL
        WHERE (
            parse_status = 'raw_hashed'
            OR COALESCE(metadata_json::jsonb ->> 'handling', '') = 'raw_hash_diff'
        )
        AND (
            (
                jsonb_typeof(structured_json::jsonb) = 'array'
                AND jsonb_array_length(structured_json::jsonb) = 1
                AND jsonb_typeof(structured_json::jsonb -> 0) = 'object'
                AND (structured_json::jsonb -> 0) ? 'raw_text_hash'
                AND NOT EXISTS (
                    SELECT 1
                    FROM jsonb_object_keys(structured_json::jsonb -> 0) AS keys(key)
                    WHERE keys.key <> 'raw_text_hash'
                )
            )
            OR (
                jsonb_typeof(structured_json::jsonb) = 'object'
                AND structured_json::jsonb ? 'raw_text_hash'
                AND NOT EXISTS (
                    SELECT 1
                    FROM jsonb_object_keys(structured_json::jsonb) AS keys(key)
                    WHERE keys.key <> 'raw_text_hash'
                )
            )
        )
        """
    )


def downgrade() -> None:
    """The removed placeholder is derivable from raw_text_hash and is not restored."""

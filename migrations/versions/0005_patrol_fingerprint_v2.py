"""scope patrol import fingerprints by mode and dataset

Revision ID: 0005_patrol_fingerprint_v2
Revises: 0004_parser_samples_postgresql
Create Date: 2026-06-15
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0005_patrol_fingerprint_v2"
down_revision: Union[str, Sequence[str], None] = "0004_parser_samples_postgresql"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE netops_command_snapshots AS snapshot
        SET import_fingerprint = encode(
            sha256(
                convert_to(
                    concat_ws(
                        '|',
                        'v2',
                        run.mode,
                        COALESCE(snapshot.dataset_id, ''),
                        COALESCE(snapshot.source_file_hash, ''),
                        snapshot.device_id,
                        lower(trim(snapshot.canonical_command)),
                        encode(sha256(convert_to(snapshot.raw_text, 'UTF8')), 'hex')
                    ),
                    'UTF8'
                )
            ),
            'hex'
        )
        FROM netops_patrol_runs AS run
        WHERE run.id = snapshot.run_id
          AND snapshot.import_fingerprint IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE netops_command_snapshots AS snapshot
        SET import_fingerprint = encode(
            sha256(
                convert_to(
                    concat_ws(
                        '|',
                        COALESCE(snapshot.source_file_hash, ''),
                        snapshot.device_id,
                        lower(trim(snapshot.canonical_command)),
                        encode(sha256(convert_to(snapshot.raw_text, 'UTF8')), 'hex')
                    ),
                    'UTF8'
                )
            ),
            'hex'
        )
        WHERE snapshot.import_fingerprint IS NOT NULL
        """
    )

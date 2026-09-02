"""Allow immutable CommandAdapter projections in runtime bundles.

Revision ID: 0098_command_adapter_projection_assets
Revises: 0097_clear_raw_hash_placeholders
"""

from alembic import op

revision = "0098_command_adapter_projection_assets"
down_revision = "0097_clear_raw_hash_placeholders"
branch_labels = None
depends_on = None


_ASSET_TYPES = (
    "'parser_template', 'command_adapter_projection', "
    "'observation_contract', 'current_state_rule', 'change_policy', "
    "'event_rule', 'comparison_profile', 'identity_policy'"
)
_LEGACY_ASSET_TYPES = (
    "'parser_template', 'observation_contract', 'current_state_rule', "
    "'change_policy', 'event_rule', 'comparison_profile', 'identity_policy'"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_runtime_bundle_asset_type",
        "netops_runtime_bundle_assets",
        type_="check",
    )
    op.create_check_constraint(
        "ck_runtime_bundle_asset_type",
        "netops_runtime_bundle_assets",
        f"asset_type IN ({_ASSET_TYPES})",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_runtime_bundle_asset_type",
        "netops_runtime_bundle_assets",
        type_="check",
    )
    op.create_check_constraint(
        "ck_runtime_bundle_asset_type",
        "netops_runtime_bundle_assets",
        f"asset_type IN ({_LEGACY_ASSET_TYPES})",
    )

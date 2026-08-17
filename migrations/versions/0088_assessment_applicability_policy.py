"""Allow governed applicability assessment-policy assets.

Revision ID: 0088_assessment_applicability
Revises: 0087_assessment_policy_assets
"""

from alembic import op


revision = "0088_assessment_applicability"
down_revision = "0087_assessment_policy_assets"
branch_labels = None
depends_on = None


_OLD_ROLES = (
    "policy_role IN ('high', 'critical_high', 'low', 'critical_low', "
    "'expected_state')"
)
_NEW_ROLES = (
    "policy_role IN ('high', 'critical_high', 'low', 'critical_low', "
    "'expected_state', 'applicability')"
)


def _replace_role_constraint(table_name: str, constraint_name: str, sql: str) -> None:
    op.drop_constraint(constraint_name, table_name, type_="check")
    op.create_check_constraint(constraint_name, table_name, sql)


def upgrade() -> None:
    _replace_role_constraint(
        "netops_assessment_policy_profiles",
        "ck_assessment_policy_profile_role",
        _NEW_ROLES,
    )
    _replace_role_constraint(
        "netops_assessment_policy_bindings",
        "ck_assessment_policy_binding_role",
        _NEW_ROLES,
    )


def downgrade() -> None:
    _replace_role_constraint(
        "netops_assessment_policy_bindings",
        "ck_assessment_policy_binding_role",
        _OLD_ROLES,
    )
    _replace_role_constraint(
        "netops_assessment_policy_profiles",
        "ck_assessment_policy_profile_role",
        _OLD_ROLES,
    )

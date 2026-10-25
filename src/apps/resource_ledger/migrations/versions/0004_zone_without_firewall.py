"""Network zones no longer record a firewall and security domain.

Which firewall and zone a policy lands on comes from the firewall topology; a second,
hand-kept copy in the ledger only went stale.
"""

import sqlalchemy as sa
from alembic import op

revision = "resource_ledger_0004"
down_revision = "resource_ledger_0003"

T = "app_resource_ledger_"


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(T + "zones")}
    for name in ("firewall", "security_domain"):
        if name in columns:
            op.drop_column(T + "zones", name)


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting business data")

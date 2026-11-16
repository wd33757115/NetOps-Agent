"""拓扑绘图的可版本化图资产：每网络一张图，历史版本不可变（回退=复制新版本）。

之前画布 XML 只在前端内存（刷新即丢），AI 编辑与手工拖拽的成果都无法留存；
两张表把它变成挂在 network_id 上的资产。
"""

import sqlalchemy as sa
from alembic import op

revision = "topology_draw_0001"
down_revision = None


def upgrade():
    op.create_table(
        "app_topology_draw_diagrams",
        sa.Column("network_id", sa.String(length=64), primary_key=True),
        sa.Column("owner_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("current_version_id", sa.String(length=32)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_table(
        "app_topology_draw_versions",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("network_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("owner_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("xml", sa.Text(), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade():
    raise RuntimeError("Drop the diagram asset tables only with a verified backup")

"""Bounded public inventory facts; credentials and arbitrary attributes never leave here."""

from sqlalchemy import or_, select

from src.infrastructure.db.models import NetworkDevice
from src.infrastructure.db.postgres import get_db_session

FIELDS = (
    "id",
    "device_name",
    "management_ip",
    "vendor",
    "model",
    "os_version",
    "site",
    "role",
    "status",
    "last_connection_status",
    "last_connection_checked_at",
    "updated_at",
)


def search_devices(query="", *, limit=10, offset=0):
    limit, offset = min(20, max(1, limit)), min(10000, max(0, offset))
    statement = select(*(getattr(NetworkDevice, key) for key in FIELDS)).where(
        NetworkDevice.status != "deleted"
    )
    if query.strip():
        # Literal substring matching, not user-provided SQL wildcards.
        statement = statement.where(
            or_(
                NetworkDevice.device_name.icontains(query, autoescape=True),
                NetworkDevice.management_ip.icontains(query, autoescape=True),
                NetworkDevice.id == query,
            )
        )
    with get_db_session() as session:
        rows = (
            session.execute(
                statement.order_by(NetworkDevice.device_name, NetworkDevice.id)
                .offset(offset)
                .limit(limit + 1)
            )
            .mappings()
            .all()
        )
        return {
            "items": [dict(row) for row in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None,
            "meaning": "资产登记与最近一次连接记录；不代表设备此刻的实时状态",
        }

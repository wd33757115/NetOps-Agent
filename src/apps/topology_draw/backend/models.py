"""拓扑绘图的关系资产：每网络一张图，历史版本不可变（回退=复制新版本）。"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, String, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def now():
    return datetime.now(timezone.utc)


class Diagram(Base):
    """每网络一张图的当前指针。"""

    __tablename__ = "app_topology_draw_diagrams"
    network_id = Column(String(64), primary_key=True)
    owner_id = Column(String(64), nullable=False, index=True)
    current_version_id = Column(String(32))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now, onupdate=now)


class DiagramVersion(Base):
    """不可变的图版本；恢复=把旧版本复制成新版本。"""

    __tablename__ = "app_topology_draw_versions"
    id = Column(String(32), primary_key=True)
    network_id = Column(String(64), nullable=False)
    owner_id = Column(String(64), nullable=False)
    xml = Column(Text, nullable=False)
    label = Column(String(128), nullable=False)
    source = Column(String(16), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (Index("ix_topology_draw_versions_network", "network_id", "created_at"),)

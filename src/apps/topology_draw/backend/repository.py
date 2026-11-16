"""拓扑绘图的图资产读写：每网络一张图，历史版本不可变（恢复=复制新版本）。"""

from contextlib import contextmanager
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Diagram, DiagramVersion

MAX_LABEL = 128


@contextmanager
def sessions():
    from src.platform.kernel.migrations import business_engine

    with Session(business_engine()) as session, session.begin():
        yield session


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _version_view(row: DiagramVersion) -> dict:
    return {
        "id": row.id,
        "label": row.label,
        "source": row.source,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def save_version(owner: str, network_id: str, xml: str, label: str, source: str) -> str:
    """新增不可变版本并指向它；首次保存创建 diagram 行。source ∈ baseline/ai/manual/import。"""
    if source not in {"baseline", "ai", "manual", "import"}:
        raise HTTPException(422, "未知的版本来源")
    label = (label or "").strip()[:MAX_LABEL] or source
    version_id = uuid4().hex[:32]
    with sessions() as session:
        diagram = session.get(Diagram, network_id)
        if diagram is None:
            diagram = Diagram(network_id=network_id, owner_id=str(owner))
            session.add(diagram)
        if diagram.owner_id != str(owner):
            raise HTTPException(404, "记录不存在")
        session.add(
            DiagramVersion(
                id=version_id,
                network_id=network_id,
                owner_id=str(owner),
                xml=xml,
                label=label,
                source=source,
                created_at=_now(),
            )
        )
        diagram.current_version_id = version_id
        diagram.updated_at = _now()
    return version_id


def load_diagram(owner: str, network_id: str) -> dict | None:
    """当前图 + 最近 50 个版本（当前版含 XML，列表只给元信息）。"""
    with sessions() as session:
        diagram = session.get(Diagram, network_id)
        if diagram is None or diagram.owner_id != str(owner):
            return None
        current = session.get(DiagramVersion, diagram.current_version_id)
        rows = session.execute(
            select(DiagramVersion)
            .where(DiagramVersion.network_id == network_id)
            .order_by(DiagramVersion.created_at.desc())
            .limit(50)
        ).scalars().all()
        return {
            "xml": current.xml if current else None,
            "currentVersionId": current.id if current else None,
            "versions": [_version_view(row) for row in rows],
        }


def load_version_xml(owner: str, network_id: str, version_id: str) -> str:
    with sessions() as session:
        row = session.get(DiagramVersion, version_id)
        if row is None or row.owner_id != str(owner) or row.network_id != network_id:
            raise HTTPException(404, "版本不存在")
        return row.xml

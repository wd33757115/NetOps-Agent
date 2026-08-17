from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.core.check_items.risk_association import (
    RiskAssociationConflictError,
    RiskEpisodeAssociationService,
)
from src.infrastructure.db.models import (
    Base,
    CheckItemRecord,
    RiskCaseRecord,
    RiskEpisodeAssociationRecord,
)


@pytest.fixture()
def scope():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            CheckItemRecord.__table__,
            RiskCaseRecord.__table__,
            RiskEpisodeAssociationRecord.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def session_scope():
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    now = datetime.now(timezone.utc)
    with session_scope() as db:
        db.add_all(
            [
                CheckItemRecord(
                    id="interface.oper_status",
                    name="接口运行状态",
                    category="interface",
                    status="active",
                ),
                CheckItemRecord(
                    id="interface.optical_power",
                    name="接口光功率",
                    category="interface",
                    status="active",
                ),
            ]
        )
        db.add_all(
            [
                _risk(
                    risk_id="risk-interface-down",
                    episode_id="episode-interface-down",
                    check_item_id="interface.oper_status",
                    risk_type="InterfaceDown",
                    entity_key="Ethernet1/1",
                    now=now,
                ),
                _risk(
                    risk_id="risk-optical-unavailable",
                    episode_id="episode-optical-unavailable",
                    check_item_id="interface.optical_power",
                    risk_type="OpticalPowerUnavailable",
                    entity_key="Ethernet1/1",
                    now=now,
                ),
                _risk(
                    risk_id="risk-optical-rx-low",
                    episode_id="episode-optical-rx-low",
                    check_item_id="interface.optical_power",
                    risk_type="OpticalRxPowerLow",
                    entity_key="Ethernet1/1:rx",
                    now=now,
                ),
            ]
        )
    yield session_scope
    engine.dispose()


def _risk(
    *,
    risk_id: str,
    episode_id: str,
    check_item_id: str,
    risk_type: str,
    entity_key: str,
    now: datetime,
) -> RiskCaseRecord:
    return RiskCaseRecord(
        id=risk_id,
        episode_id=episode_id,
        correlation_key=f"correlation:{risk_id}",
        check_item_id=check_item_id,
        device_id="device-1",
        entity_type="interface",
        entity_key=entity_key,
        correlation_dimensions_json={"interface": "Ethernet1/1"},
        risk_type=risk_type,
        severity="major",
        status="open",
        first_seen_at=now,
        last_seen_at=now,
        verification_status="confirmed_current",
        source_finding_ids_json=[],
        source_event_ids_json=[],
        remediation_status="not_started",
        created_at=now,
        updated_at=now,
    )


def _propose(service: RiskEpisodeAssociationService) -> dict:
    return service.propose(
        primary_risk_case_id="risk-interface-down",
        related_risk_case_id="risk-optical-unavailable",
        relation_type="causal_symptom",
        reason="光模块缺失与同接口 Down 属于同一物理故障",
        evidence_refs=["event-occurrence:optical", "event-occurrence:down"],
        proposed_by="engineer-a",
        idempotency_key="risk-link-request-0001",
    )


def test_association_requires_independent_review_and_is_idempotent(scope):
    service = RiskEpisodeAssociationService(scope)

    proposed = _propose(service)
    replayed = _propose(service)

    assert proposed["id"] == replayed["id"]
    assert proposed["status"] == "pending_review"
    assert proposed["effective"] is False
    with pytest.raises(
        RiskAssociationConflictError,
        match="proposer cannot",
    ):
        service.review(
            proposed["id"],
            decision="approved",
            reviewed_by="engineer-a",
            reason="自行审批应被拒绝",
        )

    approved = service.review(
        proposed["id"],
        decision="approved",
        reviewed_by="engineer-b",
        reason="已核对接口、设备、时间窗和原始证据",
    )

    assert approved["status"] == "approved"
    assert approved["effective"] is True
    assert approved["primary_risk"]["episode_id"] == "episode-interface-down"
    assert approved["related_risk"]["episode_id"] == "episode-optical-unavailable"


def test_one_episode_cannot_belong_to_multiple_primary_issues(scope):
    service = RiskEpisodeAssociationService(scope)
    first = _propose(service)
    service.review(
        first["id"],
        decision="approved",
        reviewed_by="engineer-b",
        reason="第一条关联已独立复核",
    )
    second = service.propose(
        primary_risk_case_id="risk-optical-rx-low",
        related_risk_case_id="risk-optical-unavailable",
        relation_type="same_root_cause",
        reason="尝试把同一症状挂到第二个主问题",
        evidence_refs=["event-occurrence:optical"],
        proposed_by="engineer-c",
        idempotency_key="risk-link-request-0002",
    )

    with pytest.raises(
        RiskAssociationConflictError,
        match="multiple primary issues",
    ):
        service.review(
            second["id"],
            decision="approved",
            reviewed_by="engineer-d",
            reason="第二条关联不应获批",
        )


def test_new_episode_invalidates_old_association(scope):
    service = RiskEpisodeAssociationService(scope)
    proposed = _propose(service)
    service.review(
        proposed["id"],
        decision="approved",
        reviewed_by="engineer-b",
        reason="原 episode 关联成立",
    )
    with scope() as db:
        risk = db.get(RiskCaseRecord, "risk-optical-unavailable")
        risk.episode_id = "episode-optical-unavailable-new"

    row = service.list_associations(risk_case_id="risk-interface-down")["items"][0]

    assert row["status"] == "approved"
    assert row["effective"] is False
    assert row["stale"] is True


def test_approved_association_can_be_revoked_without_deleting_history(scope):
    service = RiskEpisodeAssociationService(scope)
    proposed = _propose(service)
    service.review(
        proposed["id"],
        decision="approved",
        reviewed_by="engineer-b",
        reason="关联证据已复核",
    )

    revoked = service.revoke(
        proposed["id"],
        revoked_by="engineer-c",
        reason="后续现场复核证明是两个独立问题",
    )

    assert revoked["status"] == "revoked"
    assert revoked["effective"] is False
    assert service.list_associations()["total"] == 1

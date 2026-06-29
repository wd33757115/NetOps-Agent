from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.infrastructure.db import reset as db_reset
from src.infrastructure.db.models import Base, NetworkDevice, SkillCatalogRecord


@contextmanager
def _session_scope(session_factory):
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _patch_reset_db(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'reset.db'}")
    Base.metadata.create_all(
        engine,
        tables=[
            NetworkDevice.__table__,
            SkillCatalogRecord.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(db_reset, "get_db_session", lambda: _session_scope(session_factory))
    return session_factory


def test_reset_business_data_dry_run_preserves_rows(monkeypatch, tmp_path):
    session_factory = _patch_reset_db(monkeypatch, tmp_path)
    with session_factory.begin() as session:
        session.add(
            NetworkDevice(
                id="device-1",
                device_name="SW1",
                management_ip="10.0.0.1",
                status="active",
                attributes_json={},
            )
        )
        session.add(SkillCatalogRecord(skill_name="device-patrol"))

    result = db_reset.reset_business_data(dry_run=True)

    assert result.success is True
    assert result.dry_run is True
    assert result.table_counts["netops_devices"] == 1
    with session_factory.begin() as session:
        assert session.execute(select(NetworkDevice)).scalars().all()
        assert session.execute(select(SkillCatalogRecord)).scalars().all()


def test_reset_business_data_deletes_business_rows_and_preserves_static_tables(monkeypatch, tmp_path):
    session_factory = _patch_reset_db(monkeypatch, tmp_path)
    with session_factory.begin() as session:
        session.add(
            NetworkDevice(
                id="device-1",
                device_name="SW1",
                management_ip="10.0.0.1",
                status="active",
                attributes_json={},
            )
        )
        session.add(SkillCatalogRecord(skill_name="device-patrol"))

    result = db_reset.reset_business_data(
        dry_run=False,
        confirmation=db_reset.RESET_CONFIRMATION,
    )

    assert result.success is True
    assert result.dry_run is False
    assert result.table_counts["netops_devices"] == 1
    assert "netops_skill_catalog" in result.preserved_tables
    with session_factory.begin() as session:
        assert session.execute(select(NetworkDevice)).scalars().all() == []
        assert len(session.execute(select(SkillCatalogRecord)).scalars().all()) == 1


def test_reset_business_data_requires_confirmation_for_execution(monkeypatch, tmp_path):
    _patch_reset_db(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="confirmation must be"):
        db_reset.reset_business_data(dry_run=False, confirmation="wrong")

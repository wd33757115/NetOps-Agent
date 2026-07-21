from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.parser import repository as parser_repository
from src.core.parser.repository import ParserJobRepository
from src.infrastructure.db.models import AuditLogRecord, Base, ParserControlSetting


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


def test_auto_dispatch_change_is_atomic_and_audited(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'parser-dispatch-control.db'}")
    Base.metadata.create_all(
        engine,
        tables=[ParserControlSetting.__table__, AuditLogRecord.__table__],
    )
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(
        parser_repository,
        "get_db_session",
        lambda: _session_scope(session_factory),
    )
    repository = ParserJobRepository()

    assert repository.set_auto_dispatch_enabled(
        True,
        actor="operator",
        reason="enable controlled rollout",
    ) is True

    with _session_scope(session_factory) as db:
        setting = db.get(ParserControlSetting, "auto_dispatch_enabled")
        audit = db.query(AuditLogRecord).one()
        assert setting.value_json == {"enabled": True}
        assert audit.action == "parser_auto_dispatch_changed"
        assert audit.username == "operator"
        assert audit.detail == {
            "reason": "enable controlled rollout",
            "from_enabled": False,
            "to_enabled": True,
        }

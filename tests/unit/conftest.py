"""SSH tests use an isolated real policy/audit database, never local platform state."""

from contextlib import contextmanager
from datetime import datetime, timezone
from shutil import copyfile
from threading import RLock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

LEGACY_SSH_TEST_MODULES = (
    "test_cli_ingestion_adapter",
    "test_device_identity_discovery",
    "test_device_management_api",
    "test_patrol_handlers",
    "test_device_patrol_runner",
    "test_device_backup_runner",
    "test_inspection_online_tasks",
    "test_patrol_failure_semantics",
    "test_legacy_ssh_governance",
)


@pytest.fixture(autouse=True, scope="session")
def topology_rules_from_shipped_catalog():
    """Unit tests read the rule-governance catalog shipped with the App, not a database."""
    from src.apps.topology.backend.collectors import rules

    rules.SOURCE = "shipped"


@pytest.fixture(scope="session")
def legacy_ssh_database_template(tmp_path_factory):
    from src.infrastructure.db.models import Base

    path = tmp_path_factory.mktemp("legacy-ssh-schema") / "template.sqlite"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    return path


@pytest.fixture(autouse=True)
def ssh_governance_database(request, tmp_path, monkeypatch):
    from src.platform.device_access import governance

    modules = (
        "test_device_access",
        "test_topology",
        "test_ssh_governance",
        "test_ssh_session_logs",
    ) + LEGACY_SSH_TEST_MODULES
    if not request.node.path.name.startswith(modules):
        def unexpected_governance_database():
            pytest.fail("SSH governance access requires an isolated test database")

        # A future test module must opt into isolation, never silently write to
        # the configured platform database because its name is missing here.
        monkeypatch.setattr(governance, "get_db_session", unexpected_governance_database)
        yield None
        return
    from src.infrastructure.db.models import AuditLogRecord, SSHPolicyRecord

    database_path = tmp_path / "ssh-governance.sqlite"
    engine = create_engine(f"sqlite:///{database_path}")
    if request.node.path.name.startswith(LEGACY_SSH_TEST_MODULES):
        from src.infrastructure.db import postgres

        # Older collector boundary tests also persist connection health and
        # timeline/baseline fixtures through the shared session factory.
        copyfile(request.getfixturevalue("legacy_ssh_database_template"), database_path)
        monkeypatch.setattr(
            postgres, "SessionLocal", sessionmaker(bind=engine, autoflush=False)
        )

        def unexpected_platform_connection():
            pytest.fail("Legacy SSH unit test attempted to connect to the platform database")

        monkeypatch.setattr(postgres.engine, "connect", unexpected_platform_connection)
    else:
        AuditLogRecord.__table__.create(engine)
        SSHPolicyRecord.__table__.create(engine)

    @contextmanager
    def sessions():
        with Session(engine) as session, session.begin():
            yield session

    with sessions() as session:
        session.add(
            SSHPolicyRecord(
                id=1,
                patterns=[".*"],
                version=1,
                updated_by="test",
                updated_at=datetime.now(timezone.utc),
            )
        )
    monkeypatch.setattr(governance, "get_db_session", sessions)
    # Standalone low-level driver tests deliberately have no App AccessContext.
    if request.node.path.name == "test_device_access_netmiko.py":
        operation = governance.SSHOperation("test", "test-driver", "fixture", "127.0.0.1")
        with operation.activate():
            yield sessions
    else:
        yield sessions
    engine.dispose()


@pytest.fixture(autouse=True)
def ssh_log_storage(monkeypatch):
    """Explicit fake persistence for all SSH tests; never contact real MinIO."""
    from src.platform.device_access import session_logs

    class Storage:
        def __init__(self):
            self.objects = {}
            self.uploads = []
            self.fail = False

        def upload_file(self, key, data, content_type):
            if self.fail:
                return False
            self.objects[key] = data
            self.uploads.append(key)
            return True

        def download_file(self, key):
            if self.fail:
                raise RuntimeError("object storage offline")
            return self.objects.get(key)

    store = Storage()
    lock = RLock()
    monkeypatch.setattr(session_logs, "_storage", lambda: store)

    def acquire(session, identifier):
        # SQLite lacks advisory locks. Hold this deterministic test-only lock
        # for the transaction, matching the production database lock lifetime.
        from sqlalchemy import event

        lock.acquire()
        released = False

        def release(target, transaction):
            nonlocal released
            if transaction.parent is None and not released:
                released = True
                lock.release()

        event.listen(session, "after_transaction_end", release)

    monkeypatch.setattr(session_logs, "_lock", acquire)
    return store

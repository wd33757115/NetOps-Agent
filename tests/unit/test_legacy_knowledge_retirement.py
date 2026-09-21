import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from src.apps.materials.backend.documents import DocumentReader


def test_retirement_migration_only_drops_the_legacy_index(tmp_path):
    path = Path("migrations/versions/0102_remove_legacy_knowledge.py")
    spec = importlib.util.spec_from_file_location("retire_knowledge", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.sqlite'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE netops_knowledge_index (id INTEGER)"))
        connection.execute(text("CREATE TABLE unrelated_business (value TEXT)"))
        connection.execute(text("INSERT INTO unrelated_business VALUES ('keep')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
            with pytest.raises(RuntimeError, match="deleted"):
                migration.downgrade()
        assert "netops_knowledge_index" not in inspect(connection).get_table_names()
        assert connection.scalar(text("SELECT value FROM unrelated_business")) == "keep"
    engine.dispose()


def test_file_reader_requires_an_explicit_workspace():
    with pytest.raises(TypeError):
        DocumentReader()


def test_old_graph_and_knowledge_executor_are_removed():
    assert not Path('src/agents/supervisor/graph_v2.py').exists()
    assert not Path('src/core/rag_service/service.py').exists()
    assert not Path('src/platform/agent_runtime/legacy.py').exists()

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_retirement_drops_only_obsolete_controls():
    path = Path(__file__).resolve().parents[2] / 'migrations/versions/0104_retire_agent_module_controls.py'
    spec = importlib.util.spec_from_file_location('retire_modules', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(sa.text('CREATE TABLE netops_agent_module_controls (id TEXT)'))
        connection.execute(sa.text('CREATE TABLE netops_patrol_runs (id TEXT)'))
        connection.execute(sa.text("INSERT INTO netops_patrol_runs VALUES ('preserved')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        assert not sa.inspect(connection).has_table('netops_agent_module_controls')
        assert connection.execute(sa.text('SELECT id FROM netops_patrol_runs')).scalar_one() == 'preserved'


def test_device_api_retires_module_controls_but_keeps_launch_preview():
    from src.apps.device_management.backend.api import router

    paths = [route.path for route in router.routes]
    assert not any('agent-modules' in path for path in paths)
    assert any(path.endswith('/patrol/preview') for path in paths)

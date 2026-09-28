"""Database lifecycle boundaries and retry-safe initial data."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text

from src.infrastructure.db.models import BootstrapStepRecord
from src.platform.database.bootstrap import seed_once

ROOT = Path(__file__).resolve().parents[2]


def test_initial_data_is_once_per_owner_version_and_check_never_writes():
    engine = create_engine('sqlite://')
    BootstrapStepRecord.__table__.create(engine)
    calls = []
    seed_once(engine, 'app.one', 1, lambda: calls.append(1))
    seed_once(engine, 'app.one', 1, lambda: calls.append(99))
    statements = []
    event.listen(engine, 'before_cursor_execute', lambda c, u, sql, *args: statements.append(sql))
    seed_once(engine, 'app.one', 1, lambda: calls.append(99), check=True)
    with pytest.raises(ValueError, match='Initial data missing'):
        seed_once(engine, 'app.one', 2, lambda: calls.append(99), check=True)
    assert all(sql.lstrip().upper().startswith('SELECT') for sql in statements)
    seed_once(engine, 'app.two', 1, lambda: calls.append(2))
    seed_once(engine, 'app.one', 2, lambda: calls.append(3))
    assert calls == [1, 2, 3]
    engine.dispose()


def test_failed_seed_has_no_completion_marker_and_can_be_retried():
    engine = create_engine('sqlite://')
    BootstrapStepRecord.__table__.create(engine)
    def fail():
        raise RuntimeError('interrupted')
    with pytest.raises(RuntimeError, match='interrupted'):
        seed_once(engine, 'app.one', 1, fail)
    with engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM netops_bootstrap_steps')).scalar() == 0
    seed_once(engine, 'app.one', 1, lambda: None)
    seed_once(engine, 'app.one', 1, fail)  # Completed hook must not run again.
    engine.dispose()


def test_startup_and_compose_have_one_database_entry():
    import yaml
    start = (ROOT / 'scripts/windows/start.ps1').read_text(encoding='utf-8')
    assert 'src.platform.database.bootstrap check' in start
    assert 'scripts/migrate_db.py' not in start
    assert 'manage.py migrate' not in start
    assert 'app_packages prepare' not in start
    gateway = (ROOT / 'src/gateway/main.py').read_text(encoding='utf-8')
    assert '.bootstrap(' not in gateway
    assert 'bootstrap_shared_templates' not in gateway
    compose = yaml.safe_load((ROOT / 'deployment/docker-compose.yml').read_text(encoding='utf-8'))
    assert 'django-migrate' not in compose['services']
    assert compose['services']['migrate']['command'] == 'python -m src.platform.database.bootstrap apply'
    production = (ROOT / 'scripts/prod/start.sh').read_text(encoding='utf-8')
    assert 'src.platform.database.bootstrap check' in production
    assert 'manage.py migrate' not in production
    assert 'app_packages prepare' not in production
    assert 'seed_auth_users' not in production


def test_initialization_hook_must_belong_to_its_app(tmp_path):
    import shutil
    import yaml
    from src.platform.kernel.registry import APPS_ROOT, discover
    shutil.copytree(APPS_ROOT / 'materials', tmp_path / 'materials',
                    ignore=shutil.ignore_patterns('__pycache__'))
    path = tmp_path / 'materials/app.yaml'
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    data['database'].update(bootstrap='src.apps.assistant.backend.api:router', bootstrap_version=1)
    path.write_text(yaml.safe_dump(data), encoding='utf-8')
    with pytest.raises(ValueError, match='outside package'):
        discover(tmp_path)

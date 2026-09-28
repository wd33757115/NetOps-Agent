from contextlib import contextmanager
from types import SimpleNamespace

from src.skills.registry import skill_registry
from src.skill_system.catalog import repository


def test_reload_removes_deleted_file_skill_and_embedding(tmp_path, monkeypatch):
    directory = tmp_path / 'temporary-skill'
    directory.mkdir()
    document = directory / 'SKILL.md'
    document.write_text('---\nname: temporary-skill\ndescription: Test\n---\nTest', encoding='utf-8')
    for key in ('_skills', '_metadata_cache', '_embedding_cache'):
        monkeypatch.setattr(skill_registry, key, {})
    assert skill_registry.sync_skills_from_files([str(tmp_path)]) == 1
    assert skill_registry.get_skill('temporary-skill') is not None
    skill_registry._embedding_cache['temporary-skill'] = [1.0]
    document.unlink()
    skill_registry.sync_skills_from_files([str(tmp_path)], force_replace=True)
    assert skill_registry.get_skill('temporary-skill') is None
    assert skill_registry.get_metadata('temporary-skill') is None
    assert 'temporary-skill' not in skill_registry._embedding_cache


def test_catalog_cleanup_preserves_available_and_external_skills(tmp_path, monkeypatch):
    monkeypatch.setattr(repository, '__file__', str(tmp_path / 'skill_system/catalog/repository.py'))
    active = tmp_path / 'skills/active'
    active.mkdir(parents=True)
    (active / 'SKILL.md').write_text('active', encoding='utf-8')
    rows = [SimpleNamespace(skill_path=str(path)) for path in
            (active, tmp_path / 'skills/retired', tmp_path / 'external/skill')]
    deleted = []
    @contextmanager
    def session():
        yield SimpleNamespace(query=lambda _: SimpleNamespace(all=lambda: rows), delete=deleted.append)
    monkeypatch.setattr(repository, 'get_db_session', session)
    assert repository.prune_missing_file_entries() == 1
    assert deleted == [rows[1]]

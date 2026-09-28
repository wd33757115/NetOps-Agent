from __future__ import annotations

from types import SimpleNamespace

import src.skill_system as skill_system_module
import src.skill_system.catalog as catalog_module
import src.skills.registry as registry_module
from src.skill_system import SkillSystem
from src.skills import bootstrap


class _FakeSkillSystem:
    def __init__(self) -> None:
        self.loader = SimpleNamespace(
            list_all_metadata=lambda: [{"name": "patrol-raw-ingestor"}]
        )
        self.reload_calls: list[dict[str, object]] = []

    def reload_all(self, *, skill_dirs) -> None:
        self.reload_calls.append(
            {"skill_dirs": skill_dirs}
        )


def _run_bootstrap(monkeypatch, tmp_path, *, persist_catalog: bool):
    fake_system = _FakeSkillSystem()
    catalog_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        registry_module.skill_registry,
        "sync_skills_from_files",
        lambda dirs, force_replace: 1,
    )
    monkeypatch.setattr(
        skill_system_module,
        "get_skill_system",
        lambda: fake_system,
    )
    monkeypatch.setattr(
        catalog_module,
        "sync_and_index",
        lambda metadata, *, index: catalog_calls.append(
            {"metadata": metadata, "index": index}
        )
        or {"synced": len(metadata), "indexed": int(index)},
    )
    monkeypatch.setattr(
        bootstrap,
        "get_settings",
        lambda: SimpleNamespace(
            SKILL_CATALOG_ENABLED=True,
        ),
    )

    bootstrap.reset_bootstrap_state()
    try:
        count = bootstrap.bootstrap_skills(
            skill_dirs=[str(tmp_path)],
            force=True,
            persist_catalog=persist_catalog,
        )
    finally:
        bootstrap.reset_bootstrap_state()

    return count, fake_system, catalog_calls


def test_bootstrap_syncs_catalog_metadata_without_loading_embedding_model(
    monkeypatch,
    tmp_path,
):
    count, fake_system, catalog_calls = _run_bootstrap(
        monkeypatch,
        tmp_path,
        persist_catalog=True,
    )

    assert count == 1
    assert len(fake_system.reload_calls) == 1
    assert catalog_calls == [
        {
            "metadata": [{"name": "patrol-raw-ingestor"}],
            "index": False,
        }
    ]


def test_runtime_bootstrap_does_not_write_catalog(monkeypatch, tmp_path):
    _, _, catalog_calls = _run_bootstrap(
        monkeypatch,
        tmp_path,
        persist_catalog=False,
    )

    assert catalog_calls == []


def test_skill_system_does_not_enable_local_embedder_from_catalog_flag_alone(
    monkeypatch,
    tmp_path,
):
    import src.common.config as config_module

    monkeypatch.setattr(
        config_module,
        "get_settings",
        lambda: SimpleNamespace(
            SKILL_CATALOG_ENABLED=True,
            SKILL_CATALOG_SEMANTIC_ROUTING_ENABLED=False,
            EMBEDDING_MODEL="BAAI/bge-m3",
        ),
    )

    skill_system = SkillSystem()
    skill_system.initialize(skill_dirs=[str(tmp_path)])

    assert skill_system.router is not None
    assert skill_system.router.use_embedding is False



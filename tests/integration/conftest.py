# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""集成测试共享 fixture。"""

from __future__ import annotations

import shutil

import pytest

from src.infrastructure.db.models import (
    WorkflowMarketTemplate,
    WorkflowPluginMetadata,
    WorkflowPluginVersion,
)
from src.infrastructure.db.migrations import upgrade_database
from src.infrastructure.db.postgres import get_db_session, verify_postgres_connection

TEST_PLUGIN_PREFIX = "gov-pg-test-"


@pytest.fixture(scope="session")
def postgres_available():
    """会话级：无 PostgreSQL 时跳过整个集成模块。"""
    if not verify_postgres_connection():
        pytest.skip("PostgreSQL 不可用，跳过治理集成测试")
    upgrade_database()
    return True


def cleanup_governance_test_artifacts(plugin_name: str) -> None:
    """清理测试插件的文件与 DB 元数据/版本。"""
    from src.core.plugins.chat_intent import get_chat_intent_registry
    from src.core.workflows.registry import WORKFLOWS_ROOT, load_workflows

    with get_db_session() as db:
        db.query(WorkflowPluginVersion).filter(
            WorkflowPluginVersion.plugin_name == plugin_name
        ).delete(synchronize_session=False)
        db.query(WorkflowPluginMetadata).filter(
            WorkflowPluginMetadata.name == plugin_name
        ).delete(synchronize_session=False)
        db.query(WorkflowMarketTemplate).filter(
            WorkflowMarketTemplate.source_plugin_name == plugin_name
        ).delete(synchronize_session=False)

    if WORKFLOWS_ROOT.is_dir():
        for cat_dir in WORKFLOWS_ROOT.iterdir():
            if not cat_dir.is_dir():
                continue
            plugin_dir = cat_dir / plugin_name
            if plugin_dir.exists():
                shutil.rmtree(plugin_dir, ignore_errors=True)

    load_workflows(force=True)
    get_chat_intent_registry().load(force=True)


@pytest.fixture
def gov_plugin_name(postgres_available):
    """每个用例独立插件名，结束后自动清理。"""
    import uuid

    name = f"{TEST_PLUGIN_PREFIX}{uuid.uuid4().hex[:8]}"
    yield name
    cleanup_governance_test_artifacts(name)

# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 治理 PostgreSQL 集成测试。

需要本地 PostgreSQL 与项目配置一致（见 settings.postgres_url）。
无 PG 时自动 skip。

运行：
  .\\venv\\Scripts\\python.exe -m pytest tests/integration/test_workflow_governance_pg.py -v
"""

from __future__ import annotations

import uuid

import pytest
import yaml

from src.core.plugins.chat_intent import get_chat_intent_registry, match_chat_workflow
from src.core.workflows.dsl import GenerateOptions
from src.core.workflows.generator import dsl_from_collab_template, generate_and_persist
from src.core.workflows.metadata_repo import (
    get_plugin_metadata,
    list_plugin_versions,
    transition_plugin_status,
)
from src.core.workflows.registry import WORKFLOWS_ROOT, load_workflows
from src.core.workflows.versioning import (
    diff_plugin_versions,
    list_plugins_enriched,
    publish_plugin,
)

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def _unique_keyword() -> str:
    return f"govpg{uuid.uuid4().hex[:6]}"


def _build_test_dsl(plugin_name: str, *, keyword: str):
    return dsl_from_collab_template(
        plugin_name=plugin_name,
        description="PG 治理集成测试插件",
        step1_skill="firewall-policy-generator",
        step2_skill=None,
        category="custom",
        chat_match_any=[keyword],
        chat_match_secondary=[],
    )


def _chat_query(keyword: str) -> str:
    return f"根据工单REQ2025099，{keyword} 请处理"


def _persist_draft(dsl, *, user_id: str = "test-operator"):
    return generate_and_persist(
        dsl,
        options=GenerateOptions(persist=True, overwrite=True, reload=True),
        user_id=user_id,
    )


def test_pg_generate_persists_draft_metadata(gov_plugin_name):
    keyword = _unique_keyword()
    dsl = _build_test_dsl(gov_plugin_name, keyword=keyword)

    result = _persist_draft(dsl)
    assert result["success"] is True
    assert result["status"] == "draft"

    meta = get_plugin_metadata(gov_plugin_name)
    assert meta is not None
    assert meta.status == "draft"
    assert meta.category == "custom"
    assert (WORKFLOWS_ROOT / "custom" / gov_plugin_name / "WORKFLOW.yaml").is_file()


def test_pg_publish_creates_version_snapshot(gov_plugin_name):
    keyword = _unique_keyword()
    dsl = _build_test_dsl(gov_plugin_name, keyword=keyword)
    assert _persist_draft(dsl)["success"] is True

    pub = publish_plugin(
        gov_plugin_name,
        user_id="test-admin",
        change_summary="首次发布",
    )
    assert pub["success"] is True
    assert pub["version"] == 1

    meta = get_plugin_metadata(gov_plugin_name)
    assert meta is not None
    assert meta.status == "published"
    assert meta.current_version == 1
    assert meta.published_at is not None

    versions = list_plugin_versions(gov_plugin_name)
    assert len(versions) == 1
    assert versions[0].version == 1
    assert versions[0].change_summary == "首次发布"
    assert "WORKFLOW.yaml" in (versions[0].files or {})


def test_pg_review_to_publish_transition(gov_plugin_name):
    keyword = _unique_keyword()
    dsl = _build_test_dsl(gov_plugin_name, keyword=keyword)
    assert _persist_draft(dsl)["success"] is True

    review = transition_plugin_status(gov_plugin_name, "review", user_id="test-operator")
    assert review.status == "review"

    pub = publish_plugin(gov_plugin_name, user_id="test-admin", change_summary="审核通过")
    assert pub["success"] is True

    meta = get_plugin_metadata(gov_plugin_name)
    assert meta is not None
    assert meta.status == "published"


def test_pg_version_diff_after_republish(gov_plugin_name):
    keyword = _unique_keyword()
    dsl = _build_test_dsl(gov_plugin_name, keyword=keyword)
    assert _persist_draft(dsl)["success"] is True
    assert publish_plugin(gov_plugin_name, change_summary="v1")["success"] is True

    plugin_dir = WORKFLOWS_ROOT / "custom" / gov_plugin_name
    wf_path = plugin_dir / "WORKFLOW.yaml"
    wf = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
    wf["description"] = "PG 集成测试 v2 描述"
    wf_path.write_text(yaml.dump(wf, allow_unicode=True, sort_keys=False), encoding="utf-8")
    load_workflows(force=True)

    assert publish_plugin(gov_plugin_name, change_summary="v2")["success"] is True

    diff = diff_plugin_versions(gov_plugin_name, 1, 2)
    assert diff["has_diff"] is True
    assert "v2 描述" in diff["diff"]


def test_pg_draft_blocks_chat_intent(gov_plugin_name):
    keyword = _unique_keyword()
    dsl = _build_test_dsl(gov_plugin_name, keyword=keyword)
    assert _persist_draft(dsl)["success"] is True

    get_chat_intent_registry().load(force=True)
    query = _chat_query(keyword)

    matched = match_chat_workflow(query, "chat")
    assert matched is None or matched.workflow != gov_plugin_name


def test_pg_published_enables_chat_intent(gov_plugin_name):
    keyword = _unique_keyword()
    dsl = _build_test_dsl(gov_plugin_name, keyword=keyword)
    assert _persist_draft(dsl)["success"] is True
    assert publish_plugin(gov_plugin_name)["success"] is True

    get_chat_intent_registry().load(force=True)
    intent = match_chat_workflow(_chat_query(keyword), "chat")
    assert intent is not None
    assert intent.workflow == gov_plugin_name


def test_pg_list_plugins_enriched_reflects_db_status(gov_plugin_name):
    keyword = _unique_keyword()
    dsl = _build_test_dsl(gov_plugin_name, keyword=keyword)
    assert _persist_draft(dsl)["success"] is True

    items = list_plugins_enriched()
    row = next((p for p in items if p["name"] == gov_plugin_name), None)
    assert row is not None
    assert row["status"] == "draft"

    assert publish_plugin(gov_plugin_name)["success"] is True
    items = list_plugins_enriched()
    row = next((p for p in items if p["name"] == gov_plugin_name), None)
    assert row is not None
    assert row["status"] == "published"
    assert row["current_version"] == 1


def test_pg_generate_submit_review_status(gov_plugin_name):
    keyword = _unique_keyword()
    dsl = _build_test_dsl(gov_plugin_name, keyword=keyword)

    result = generate_and_persist(
        dsl,
        options=GenerateOptions(
            persist=True,
            overwrite=True,
            reload=True,
            submit_review=True,
        ),
        user_id="test-operator",
    )
    assert result["success"] is True
    assert result["status"] == "review"

    meta = get_plugin_metadata(gov_plugin_name)
    assert meta is not None
    assert meta.status == "review"

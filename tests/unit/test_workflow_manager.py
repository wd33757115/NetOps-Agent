# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 管理器与 API 单元测试。"""

import yaml

from src.core.workflows.manager import (
    generate_from_collab_template,
    list_collab_templates,
    preview_chat_intent,
    validate_workflow_yaml,
)
from src.core.workflows.registry import get_template, load_workflows


def test_list_collab_templates_has_mode_a():
    templates = list_collab_templates()
    ids = {t["id"] for t in templates}
    assert "mode-a-firewall-llm" in ids


def test_generate_mode_a_template():
    files = generate_from_collab_template("mode-a-firewall-llm", plugin_name="test-llm-wf")
    assert files is not None
    assert "WORKFLOW.yaml" in files
    assert "llm-result-analyzer" in files["WORKFLOW.yaml"]
    assert "CHAT.intent.yaml" in files
    raw = yaml.safe_load(files["WORKFLOW.yaml"])
    assert raw["name"] == "test-llm-wf"
    assert len(raw["steps"]) == 3
    assert raw["steps"][-1]["skill"] == "llm-result-analyzer"


def test_load_firewall_llm_analysis_plugin():
    load_workflows(force=True)
    tpl = get_template("itsm-firewall-llm-analysis")
    assert tpl is not None
    assert len(tpl.steps) == 3
    assert tpl.steps[-1].skill_name == "llm-result-analyzer"


def test_validate_workflow_yaml_ok():
    files = generate_from_collab_template("mode-a-firewall-llm")
    result = validate_workflow_yaml(files["WORKFLOW.yaml"])
    assert result["valid"] is True
    assert not result["errors"]


def test_preview_chat_intent_matched():
    files = generate_from_collab_template("mode-a-firewall-llm", plugin_name="itsm-firewall-llm-analysis")
    query = "根据工单 REQ2025001 生成防火墙策略并进行 LLM 分析"
    result = preview_chat_intent(query, chat_intent_yaml=files["CHAT.intent.yaml"])
    assert result["matched"] is True
    assert result.get("ticket_id")


def test_save_plugin_syncs_workflow_name():
    import shutil
    from pathlib import Path

    from src.core.plugins.chat_intent import get_chat_intent_registry
    from src.core.workflows.manager import save_plugin

    files = generate_from_collab_template("mode-a-generic", plugin_name="sync-test-wf")
    files["WORKFLOW.yaml"] = files["WORKFLOW.yaml"].replace("name: custom-llm-analysis", "name: wrong-name")
    files["CHAT.intent.yaml"] = files["CHAT.intent.yaml"].replace("workflow: custom-llm-analysis", "workflow: wrong-name")
    result = save_plugin("sync-test-wf", category="custom", files=files)
    assert result["success"] is True
    plugin_dir = Path(result["path"])
    wf = yaml.safe_load((plugin_dir / "WORKFLOW.yaml").read_text(encoding="utf-8"))
    chat = yaml.safe_load((plugin_dir / "CHAT.intent.yaml").read_text(encoding="utf-8"))
    assert wf["name"] == "sync-test-wf"
    assert chat["workflow"] == "sync-test-wf"
    shutil.rmtree(plugin_dir, ignore_errors=True)
    get_chat_intent_registry().load(force=True)


def test_preview_chat_intent_missing_ticket():
    files = generate_from_collab_template("mode-a-firewall-llm")
    result = preview_chat_intent("生成防火墙策略并进行 LLM 分析", chat_intent_yaml=files["CHAT.intent.yaml"])
    assert result["matched"] is False
    assert "工单" in (result.get("reason") or "")


def test_chat_intent_llm_workflow_wins_when_llm_in_query():
    from src.core.plugins.chat_intent import get_chat_intent_registry, match_chat_workflow

    get_chat_intent_registry().load(force=True)
    query = "根据工单 REQ2025001 生成防火墙策略并进行 LLM 结果分析"
    intent = match_chat_workflow(query, "chat")
    assert intent is not None
    assert intent.workflow == "itsm-firewall-llm-analysis"


def test_normalize_step_result_preserves_raw_fields():
    from src.core.workflows.artifacts import normalize_step_result

    raw = {"success": True, "manifest": {"ticket_id": "T1"}, "custom_field": "x"}
    out = normalize_step_result(raw)
    assert out["manifest"]["ticket_id"] == "T1"
    assert out["custom_field"] == "x"


def test_normalize_step_result_exposes_raw_outputs_archive():
    from src.core.workflows.artifacts import normalize_step_result

    raw = {
        "success": True,
        "download_url": "https://minio/device_patrol/raw.zip",
        "raw_outputs_url": "https://minio/device_patrol/raw.zip",
    }
    out = normalize_step_result(raw)

    assert "raw_outputs_archive" in out["artifacts"]
    assert out["artifacts"]["raw_outputs_archive"]["download_url"] == raw["raw_outputs_url"]
    assert out["artifacts"]["raw_outputs_archive"]["content_type"] == "application/zip"

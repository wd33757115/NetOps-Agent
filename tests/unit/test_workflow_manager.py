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


def test_builtin_template_is_the_firewall_change_chain():
    assert [t["id"] for t in list_collab_templates()] == ["firewall-change"]
    assert generate_from_collab_template("mode-a-firewall-llm") is None


def test_generate_firewall_change_template():
    files = generate_from_collab_template("firewall-change", plugin_name="test-fw-wf")
    assert files is not None
    raw = yaml.safe_load(files["WORKFLOW.yaml"])
    assert raw["name"] == "test-fw-wf"
    assert [s["skill"] for s in raw["steps"]] == [
        "app:firewall-policy/generate_policy",
        "app:firewall-policy/write_change_ticket",
    ]
    chat = yaml.safe_load(files["CHAT.intent.yaml"])
    assert chat["workflow"] == "test-fw-wf"
    assert chat["files"] == {"policy_file_url": [".xlsx"], "topology_file_url": [".json"]}


def test_retired_llm_analysis_workflow_is_gone():
    load_workflows(force=True)
    assert get_template("itsm-firewall-llm-analysis") is None
    assert get_template("itsm-firewall-change") is not None


def test_validate_workflow_yaml_ok():
    files = generate_from_collab_template("firewall-change")
    result = validate_workflow_yaml(files["WORKFLOW.yaml"])
    assert result["valid"] is True
    assert not result["errors"]


def test_preview_chat_intent_matched():
    files = generate_from_collab_template("firewall-change", plugin_name="fw-preview-test")
    query = "根据工单 REQ2025001 生成防火墙策略并编写变更工单"
    result = preview_chat_intent(query, chat_intent_yaml=files["CHAT.intent.yaml"])
    assert result["matched"] is True
    assert result.get("ticket_id")


def test_save_plugin_syncs_workflow_name():
    import shutil
    from pathlib import Path

    from src.core.plugins.chat_intent import get_chat_intent_registry
    from src.core.workflows.manager import save_plugin

    files = generate_from_collab_template("firewall-change", plugin_name="wrong-name")
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
    files = generate_from_collab_template("firewall-change")
    result = preview_chat_intent("生成防火墙策略并编写变更工单", chat_intent_yaml=files["CHAT.intent.yaml"])
    assert result["matched"] is False
    assert "工单" in (result.get("reason") or "")


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

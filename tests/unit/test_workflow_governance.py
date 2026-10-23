# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 治理：导出 / 导入 / diff 单元测试（不依赖 PostgreSQL）。"""

import shutil
from unittest.mock import patch

import pytest
import yaml

from src.core.workflows.generator import dsl_from_collab_template, generate_plugin_files
from src.core.workflows.manager import save_plugin
from src.core.workflows.registry import WORKFLOWS_ROOT
from src.core.workflows.versioning import (
    delete_plugin,
    diff_plugin_versions,
    export_plugin_bundle,
    export_plugin_zip_bytes,
    import_plugin_bundle,
)


def _sample_dsl(name: str = "gov-test-export"):
    return dsl_from_collab_template(
        plugin_name=name,
        description="治理测试插件",
        step1_skill="firewall-policy-generator",
        step2_skill=None,
        category="custom",
    )


def _save_dsl_to_disk(dsl) -> None:
    files = generate_plugin_files(dsl)
    result = save_plugin(dsl.meta.name, category=dsl.meta.category, files=files)
    assert result.get("success"), result.get("message")


def test_export_plugin_bundle_structure():
    name = "gov-test-export"
    plugin_dir = WORKFLOWS_ROOT / "custom" / name
    dsl = _sample_dsl(name)

    try:
        _save_dsl_to_disk(dsl)

        bundle = export_plugin_bundle(name)
        assert bundle["format"] == "netops-workflow-bundle"
        assert bundle["name"] == name
        assert "WORKFLOW.yaml" in bundle["files"]
        wf = yaml.safe_load(bundle["files"]["WORKFLOW.yaml"])
        assert wf["name"] == name
    finally:
        if plugin_dir.exists():
            shutil.rmtree(plugin_dir, ignore_errors=True)


def test_export_plugin_zip_bytes():
    name = "gov-test-zip"
    plugin_dir = WORKFLOWS_ROOT / "custom" / name
    dsl = _sample_dsl(name)

    try:
        _save_dsl_to_disk(dsl)
        data = export_plugin_zip_bytes(name)
        assert isinstance(data, bytes)
        assert len(data) > 100
        assert data[:2] == b"PK"
    finally:
        if plugin_dir.exists():
            shutil.rmtree(plugin_dir, ignore_errors=True)


@patch("src.core.workflows.versioning.upsert_plugin_metadata")
def test_import_plugin_bundle_roundtrip(mock_upsert):
    name = "gov-test-import"
    import_name = "gov-test-imported"
    plugin_dir = WORKFLOWS_ROOT / "custom" / name
    import_dir = WORKFLOWS_ROOT / "custom" / import_name
    dsl = _sample_dsl(name)

    try:
        _save_dsl_to_disk(dsl)
        bundle = export_plugin_bundle(name)
        bundle["name"] = import_name

        result = import_plugin_bundle(bundle, overwrite=False, user_id="test-user")
        assert result["success"] is True
        assert result["status"] == "draft"
        assert (import_dir / "WORKFLOW.yaml").is_file()
        mock_upsert.assert_called_once()
    finally:
        for d in (plugin_dir, import_dir):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)


def test_diff_plugin_versions_no_db_returns_lookup():
    with pytest.raises(LookupError):
        diff_plugin_versions("nonexistent-plugin", 1, 2)


@patch("src.core.workflows.versioning.delete_plugin_metadata")
@patch("src.core.workflows.reload_bus.broadcast_workflow_reload")
def test_delete_plugin_removes_directory(mock_reload, mock_delete_meta):
    name = "gov-test-delete"
    plugin_dir = WORKFLOWS_ROOT / "custom" / name
    dsl = _sample_dsl(name)

    try:
        _save_dsl_to_disk(dsl)
        assert plugin_dir.exists()
        mock_reload.reset_mock()

        result = delete_plugin(name, user_id="test-user")
        assert result.get("success") is True
        assert not plugin_dir.exists()
        mock_delete_meta.assert_called_once_with(name)
        mock_reload.assert_called_once()
    finally:
        if plugin_dir.exists():
            shutil.rmtree(plugin_dir, ignore_errors=True)

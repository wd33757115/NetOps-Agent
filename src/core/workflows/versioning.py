# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 版本对比、导入导出。"""

from __future__ import annotations

import difflib
import io
import json
import logging
import shutil
import zipfile
from pathlib import Path
from typing import Any

from src.core.workflows.manager import get_plugin_detail, save_plugin, validate_plugin_files
from src.core.workflows.metadata_repo import (
    create_version_snapshot,
    delete_plugin_metadata,
    get_plugin_metadata,
    get_plugin_version,
    list_plugin_versions,
    metadata_to_dict,
    transition_plugin_status,
    upsert_plugin_metadata,
)
from src.core.workflows.registry import WORKFLOWS_ROOT, get_template, load_workflows

logger = logging.getLogger(__name__)


def export_plugin_bundle(plugin_name: str) -> dict[str, Any]:
    """导出插件为 JSON 包（含元数据）。"""
    detail = get_plugin_detail(plugin_name)
    if not detail:
        raise LookupError(f"Workflow 插件 '{plugin_name}' 不存在")

    meta = get_plugin_metadata(plugin_name)
    return {
        "format": "netops-workflow-bundle",
        "format_version": "1.0",
        "name": plugin_name,
        "category": detail.get("plugin_dir", "").split("/")[0] if detail.get("plugin_dir") else "itsm",
        "metadata": metadata_to_dict(meta),
        "files": {k: v for k, v in (detail.get("files") or {}).items() if v},
    }


def export_plugin_zip_bytes(plugin_name: str) -> bytes:
    bundle = export_plugin_bundle(plugin_name)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(
            {k: v for k, v in bundle.items() if k != "files"},
            ensure_ascii=False,
            indent=2,
        ))
        for fn, content in bundle["files"].items():
            zf.writestr(fn, content)
    return buf.getvalue()


def import_plugin_bundle(
    bundle: dict[str, Any],
    *,
    overwrite: bool = False,
    user_id: str | None = None,
) -> dict[str, Any]:
    """从 JSON 包导入插件（status=draft）。"""
    name = bundle.get("name")
    files = bundle.get("files") or {}
    category = bundle.get("category") or "itsm"

    if not name or not isinstance(files, dict) or not files.get("WORKFLOW.yaml"):
        return {"success": False, "message": "无效的导入包：缺少 name 或 WORKFLOW.yaml"}

    validation = validate_plugin_files(files)
    if not validation.get("valid"):
        return {"success": False, "message": "校验失败", "validation": validation}

    plugin_dir = WORKFLOWS_ROOT / category / name
    if plugin_dir.exists() and not overwrite:
        return {"success": False, "message": f"插件已存在: {name}，请设置 overwrite=true"}

    result = save_plugin(name, category=category, files=files)
    if not result.get("success"):
        return result

    upsert_plugin_metadata(
        name,
        category=category,
        description=bundle.get("description") or "",
        plugin_path=result.get("path"),
        status="draft",
        user_id=user_id,
    )
    return {**result, "status": "draft", "message": "插件已导入为 draft"}


def diff_plugin_versions(
    plugin_name: str,
    version_a: int,
    version_b: int,
    *,
    file_key: str = "WORKFLOW.yaml",
) -> dict[str, Any]:
    va = get_plugin_version(plugin_name, version_a)
    vb = get_plugin_version(plugin_name, version_b)
    if not va or not vb:
        raise LookupError("版本不存在")

    text_a = (va.files or {}).get(file_key) or ""
    text_b = (vb.files or {}).get(file_key) or ""
    diff_lines = list(
        difflib.unified_diff(
            text_a.splitlines(keepends=True),
            text_b.splitlines(keepends=True),
            fromfile=f"{file_key}@v{version_a}",
            tofile=f"{file_key}@v{version_b}",
        )
    )
    return {
        "plugin_name": plugin_name,
        "version_a": version_a,
        "version_b": version_b,
        "file_key": file_key,
        "diff": "".join(diff_lines),
        "has_diff": bool(diff_lines),
    }


def publish_plugin(
    plugin_name: str,
    *,
    user_id: str | None = None,
    change_summary: str | None = None,
) -> dict[str, Any]:
    """发布插件：快照版本 + status=published + 热加载。"""
    detail = get_plugin_detail(plugin_name)
    if not detail:
        return {"success": False, "message": f"插件 '{plugin_name}' 不存在"}

    files = {k: v for k, v in (detail.get("files") or {}).items() if v}
    if not files.get("WORKFLOW.yaml"):
        return {"success": False, "message": "缺少 WORKFLOW.yaml"}

    category = "itsm"
    plugin_dir = detail.get("plugin_dir") or ""
    if "/" in plugin_dir:
        category = plugin_dir.split("/")[0]

    upsert_plugin_metadata(
        plugin_name,
        category=category,
        description=detail.get("description"),
        plugin_path=str(WORKFLOWS_ROOT / plugin_dir.replace("\\", "/")),
        user_id=user_id,
    )
    version = create_version_snapshot(
        plugin_name,
        files,
        user_id=user_id,
        change_summary=change_summary or "发布",
        status="published",
    )
    meta = transition_plugin_status(plugin_name, "published", user_id=user_id)

    load_workflows(force=True)
    from src.core.plugins.chat_intent import get_chat_intent_registry
    from src.core.plugins.itsm_webhook import get_itsm_webhook_registry

    get_chat_intent_registry().load(force=True)
    get_itsm_webhook_registry().load(force=True)

    return {
        "success": True,
        "message": f"已发布 {plugin_name} v{version.version}",
        "version": version.version,
        "status": meta.status,
    }


def list_plugins_enriched() -> list[dict[str, Any]]:
    """合并文件系统插件与 DB 元数据。"""
    from src.core.workflows.manager import template_to_summary
    from src.core.workflows.metadata_repo import list_plugin_metadata
    from src.core.workflows.registry import list_templates

    load_workflows(force=True)
    meta_map = {m.name: m for m in list_plugin_metadata()}

    results: list[dict[str, Any]] = []
    for tpl in list_templates():
        meta = meta_map.get(tpl.name)
        default_status = "published" if meta is None else meta.status
        item = {
            **template_to_summary(tpl),
            **metadata_to_dict(meta, default_status=default_status),
        }
        results.append(item)
    return results


def delete_plugin(plugin_name: str, *, user_id: str | None = None) -> dict[str, Any]:
    """物理删除 Workflow 插件目录及元数据，并热重载注册表。"""
    _ = user_id  # 审计预留
    # Another process may have written the plugin since this registry loaded.
    load_workflows(force=True)
    tpl = get_template(plugin_name)
    meta = get_plugin_metadata(plugin_name)

    plugin_dir: Path | None = None
    if tpl:
        plugin_dir = tpl.plugin_dir
    elif meta and meta.plugin_path:
        plugin_dir = Path(meta.plugin_path)

    if not tpl and not meta and (not plugin_dir or not plugin_dir.exists()):
        return {"success": False, "message": f"Workflow 插件 '{plugin_name}' 不存在"}

    if plugin_dir and plugin_dir.exists():
        try:
            shutil.rmtree(plugin_dir)
            parent = plugin_dir.parent
            if parent != WORKFLOWS_ROOT and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError as exc:
            logger.error("删除插件目录失败 %s: %s", plugin_dir, exc)
            return {"success": False, "message": f"删除目录失败: {exc}"}

    delete_plugin_metadata(plugin_name)

    from src.core.workflows.reload_bus import broadcast_workflow_reload

    broadcast_workflow_reload(source="delete_plugin", plugin_name=plugin_name)
    logger.info("已删除 Workflow 插件: %s", plugin_name)
    return {"success": True, "message": f"插件 '{plugin_name}' 已删除"}

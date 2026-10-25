# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Skill / Celery 产物标准契约。"""

from __future__ import annotations

from typing import Any


def make_file_artifact(
    *,
    file_key: str | None,
    download_url: str | None,
    filename: str,
    content_type: str = "application/octet-stream",
) -> dict[str, Any]:
    return {
        "file_key": file_key,
        "download_url": download_url,
        "filename": filename,
        "content_type": content_type,
    }


def normalize_step_result(raw: dict[str, Any] | None) -> dict[str, Any]:
    """统一 Celery / Skill 返回结构，并保留原始字段供 ${steps.*.result} 表达式使用。"""
    if not raw:
        return {"success": False, "message": "空结果", "artifacts": {}}
    success = bool(raw.get("success", raw.get("status") == "success"))
    artifacts = dict(raw.get("artifacts") or {})

    download_url = raw.get("download_url")
    has_artifact_url = any(
        isinstance(meta, dict) and _is_http_url(meta.get("download_url"))
        for meta in artifacts.values()
    )
    if download_url and not has_artifact_url:
        from src.core.skills.result import _guess_content_type, _infer_artifact_key

        filename = str(raw.get("filename") or raw.get("change_excel_filename") or "")
        file_key = str(raw.get("config_file_key") or raw.get("change_excel_file_key") or raw.get("file_key") or "")
        art_key = _infer_artifact_key(filename=filename, file_key=file_key, download_url=str(download_url))
        artifacts[art_key] = make_file_artifact(
            file_key=file_key or None,
            download_url=str(download_url),
            filename=filename or art_key.replace("_", " "),
            content_type=_guess_content_type(filename, art_key),
        )
    if raw.get("raw_outputs_url") and not artifacts.get("raw_outputs_archive"):
        artifacts["raw_outputs_archive"] = make_file_artifact(
            file_key=raw.get("raw_outputs_file_key"),
            download_url=raw.get("raw_outputs_url"),
            filename=raw.get("raw_outputs_filename") or "raw-cli-outputs.zip",
            content_type="application/zip",
        )
    elif not artifacts.get("change_excel") and raw.get("change_excel_url"):
        artifacts["change_excel"] = make_file_artifact(
            file_key=raw.get("change_excel_file_key"),
            download_url=raw.get("change_excel_url"),
            filename=raw.get("change_excel_filename") or "change_ticket.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    manifest = raw.get("manifest") or artifacts.get("manifest")
    normalized = {
        "success": success,
        "message": raw.get("message") or raw.get("result") or "",
        "artifacts": artifacts,
        "manifest": manifest,
        "download_url": raw.get("download_url"),
        "error": raw.get("error"),
    }
    # 保留 Skill 原始字段（如 manifest 子字段、ticket_id 等）
    merged = dict(raw)
    merged.update({k: v for k, v in normalized.items() if v is not None or k in ("success", "message", "artifacts")})
    merged["success"] = success
    merged["artifacts"] = artifacts
    if manifest is not None:
        merged["manifest"] = manifest
    return merged


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    return (
        text.startswith("http://")
        or text.startswith("https://")
        or text.startswith("/api/")
    )


def _artifact_link_label(art_key: str, meta: dict[str, Any]) -> str:
    filename = meta.get("filename")
    if filename:
        return str(filename)
    return art_key.replace("_", " ")


def collect_download_links(
    *,
    artifacts: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """从 artifacts / Skill 结果中收集全部可下载 HTTP(S) 链接（去重）。"""
    links: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(*, key: str, label: str, url: Any) -> None:
        if not _is_http_url(url):
            return
        href = str(url).strip()
        if href in seen:
            return
        seen.add(href)
        links.append({"key": key, "label": label or key, "url": href})

    arts = artifacts
    if result and not arts:
        raw_arts = result.get("artifacts")
        arts = raw_arts if isinstance(raw_arts, dict) else None

    if isinstance(arts, dict):
        for art_key, meta in arts.items():
            if art_key == "manifest" or not isinstance(meta, dict):
                continue
            add(key=str(art_key), label=_artifact_link_label(str(art_key), meta), url=meta.get("download_url"))

    if isinstance(result, dict):
        skip_keys = {"artifacts", "success", "message", "error", "manifest", "data"}
        for key, val in result.items():
            if key in skip_keys:
                continue
            if _is_http_url(val):
                add(key=str(key), label=str(key).replace("_", " "), url=val)

    return links


def notification_download_payload(
    *,
    artifacts: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """通知 payload：统一使用 downloads 列表承载全部链接。"""
    links = collect_download_links(artifacts=artifacts, result=result)
    if not links:
        return None
    return {"downloads": links}

# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""通用 Skill 执行器：subprocess + 平台 IO（MinIO/URL 下载上传）。"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from hashlib import sha256
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from src.core.skills.resolver import (
    get_entry_output_mode,
    get_skill_cwd,
    get_skill_execution_timeout_seconds,
    resolve_entry_script,
)
from src.core.workflows.artifacts import make_file_artifact
from src.infrastructure.storage.minio_client import get_minio_storage

logger = logging.getLogger(__name__)


def _skill_subprocess_env() -> dict[str, str]:
    """Windows 下强制子进程 stdout/stderr 使用 UTF-8，避免中文乱码。"""
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _decode_subprocess_bytes(data: bytes) -> str:
    """先严格 UTF-8；失败则回退 GBK（Windows 控制台默认编码）。"""
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gbk", errors="replace")


# 仍走 in-process 的遗留 Skill（设备类等）
_LEGACY_TASK_NAMES: dict[str, str] = {
    "device-backup": "execute_config_backup_task",
    "device-patrol": "execute_device_patrol_task",
}


# 通过 CLI --zip 接收 MinIO 策略包的 Skill
_ZIP_CLI_SKILLS = frozenset({"itsm-change-ticket-writer"})


class SkillExecutionError(RuntimeError):
    def __init__(self, message: str, *, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = dict(payload or {})


def _parse_last_json_payload(*streams: str) -> dict[str, Any] | None:
    """Return the last JSON object emitted by a subprocess, if one exists."""

    for stream in streams:
        for line in reversed((stream or "").splitlines()):
            candidate = line.strip()
            if not candidate:
                continue
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
    return None


def _download_url_to_path(url: str, dest: Path) -> None:
    if url.startswith(("http://", "https://")):
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    elif url.startswith("file://"):
        src = url[7:]
        shutil.copy(src, dest)
    elif os.path.exists(url):
        shutil.copy(url, dest)
    else:
        raise FileNotFoundError(f"无法下载文件: {url}")


def _prepare_local_inputs(skill_name: str, params: dict[str, Any], tmpdir: str) -> dict[str, Any]:
    prepared = dict(params)
    minio = get_minio_storage()

    if (
        prepared.get("config_file_key")
        and skill_name in _ZIP_CLI_SKILLS
        and minio
        and minio.is_ready()
    ):
        data = minio.download_file(str(prepared["config_file_key"]))
        if data:
            zip_path = os.path.join(tmpdir, "input_policy.zip")
            with open(zip_path, "wb") as f:
                f.write(data)
            prepared["_zip_path"] = zip_path

    if skill_name in _ZIP_CLI_SKILLS and not prepared.get("_zip_path"):
        config_url = prepared.get("config_files_url")
        if config_url:
            zip_path = os.path.join(tmpdir, "input_policy_from_url.zip")
            try:
                _download_url_to_path(str(config_url), Path(zip_path))
                prepared["_zip_path"] = zip_path
                logger.info("已通过 URL 下载策略 ZIP: %s", config_url)
            except Exception as exc:
                logger.warning("config_files_url 下载失败，将尝试其他输入: %s", exc)

    policy_url = prepared.get("policy_file_url")
    if policy_url and not prepared.get("_policy_path"):
        prepared.setdefault("policy_input_name", Path(str(policy_url).split("?", 1)[0]).name)
        policy_path = os.path.join(tmpdir, "policy_input.xlsx")
        _download_url_to_path(str(policy_url), Path(policy_path))
        prepared["_policy_path"] = policy_path
        prepared["policy_file_url"] = policy_path

    topo = prepared.get("topology_file_url")
    if topo and (
        topo.startswith(("http://", "https://", "file://"))
        or os.path.exists(str(topo))
    ):
        topo_path = os.path.join(tmpdir, "topology.json")
        try:
            _download_url_to_path(str(topo), Path(topo_path))
            prepared["topology_file_url"] = topo_path
        except Exception as exc:
            if skill_name == "firewall-policy-generator":
                raise SkillExecutionError("拓扑文件下载失败，请检查文件地址或重新上传") from exc

    return prepared


def _parse_skill_stdout(stdout: str) -> dict[str, Any]:
    text = (stdout or "").strip()
    if not text:
        return {"success": False, "error": "Skill 无输出"}
    line = text.splitlines()[-1].strip()
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"success": False, "error": f"Skill 输出非 JSON: {line[:200]}"}


def _run_subprocess_skill(skill_name: str, params: dict[str, Any]) -> dict[str, Any]:
    script = resolve_entry_script(skill_name)
    if not script:
        raise SkillExecutionError(f"Skill 无入口脚本: {skill_name}")

    output_mode = get_entry_output_mode(skill_name)
    with tempfile.TemporaryDirectory() as tmpdir:
        prepared = _prepare_local_inputs(skill_name, params, tmpdir)
        params_path = os.path.join(tmpdir, "params.json")
        with open(params_path, "w", encoding="utf-8") as f:
            json.dump(
                {k: v for k, v in prepared.items() if not k.startswith("_")},
                f,
                ensure_ascii=False,
                default=str,
            )

        cmd = [sys.executable, str(script), "--params", params_path]
        output_path = os.path.join(tmpdir, "output.xlsx")
        output_dir = os.path.join(tmpdir, "output")

        if output_mode == "dir":
            os.makedirs(output_dir, exist_ok=True)
            cmd.extend(["--output-dir", output_dir])
        elif output_mode == "file":
            cmd.extend(["-o", output_path])
        elif output_mode == "none":
            pass

        zip_path = prepared.get("_zip_path")
        if zip_path and skill_name in _ZIP_CLI_SKILLS:
            cmd.extend(["--zip", zip_path])
        policy_path = prepared.get("_policy_path")
        if policy_path and skill_name == "firewall-policy-generator":
            cmd.extend(["--policy", policy_path])

        timeout_seconds = get_skill_execution_timeout_seconds(skill_name)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                # Input is params.json; a script reading stdin gets end-of-input, not the parent's open stdin.
                stdin=subprocess.DEVNULL,
                timeout=timeout_seconds,
                cwd=str(get_skill_cwd(skill_name)),
                env=_skill_subprocess_env(),
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, bytes) else str(exc.stdout or "").encode()
            stderr = exc.stderr if isinstance(exc.stderr, bytes) else str(exc.stderr or "").encode()
            payload = _parse_last_json_payload(
                _decode_subprocess_bytes(stdout),
                _decode_subprocess_bytes(stderr),
            ) or {}
            payload.update(
                {
                    "success": False,
                    "error": "skill_execution_timeout",
                    "failure_code": "skill_execution_timeout",
                    "timeout_seconds": timeout_seconds,
                }
            )
            raise SkillExecutionError(
                f"Skill execution timed out ({skill_name}, {timeout_seconds}s)",
                payload=payload,
            ) from exc
        stdout_text = _decode_subprocess_bytes(result.stdout or b"")
        stderr_text = _decode_subprocess_bytes(result.stderr or b"")
        if result.returncode != 0:
            detail = (stderr_text or stdout_text or "").strip()
            payload = _parse_last_json_payload(stdout_text, stderr_text)
            raise SkillExecutionError(
                f"Skill 脚本失败 ({skill_name}): {detail}",
                payload=payload,
            )

        skill_result = _parse_skill_stdout(stdout_text)
        if not skill_result.get("success", True) and skill_result.get("error"):
            return skill_result

        local_zip = skill_result.pop("_local_zip", None)
        if local_zip and os.path.isfile(str(local_zip)):
            skill_result = _upload_zip_from_path(skill_result, str(local_zip), params, skill_name)

        if (
            output_mode == "file"
            and os.path.isfile(output_path)
            and not skill_result.get("artifacts")
        ):
            skill_result = _upload_file_output(skill_result, output_path, params, skill_name)

        if output_mode == "dir" and os.path.isdir(output_dir):
            skill_result = _upload_dir_output(
                skill_result,
                output_dir,
                params,
                skill_name,
                prepared,
            )

        return skill_result


def _upload_zip_from_path(
    skill_result: dict[str, Any],
    zip_path: str,
    params: dict[str, Any],
    skill_name: str,
) -> dict[str, Any]:
    ticket_id = params.get("ticket_id") or "draft"
    filename = os.path.basename(zip_path)
    minio = get_minio_storage()
    object_name = None
    download_url = None
    if minio and minio.is_ready():
        object_name = f"firewall_policies/{ticket_id}/{filename}"
        with open(zip_path, "rb") as f:
            if minio.upload_file(object_name, f, content_type="application/zip"):
                download_url = minio.get_presigned_url(object_name, expires=3600 * 24 * 7)
    art = make_file_artifact(
        file_key=object_name,
        download_url=download_url,
        filename=filename,
        content_type="application/zip",
    )
    skill_result.setdefault("artifacts", {})["config_zip"] = art
    if skill_result.get("manifest"):
        skill_result["artifacts"]["manifest"] = skill_result["manifest"]
    skill_result.setdefault("config_file_key", object_name)
    skill_result.setdefault("download_url", download_url)
    skill_result.setdefault("filename", filename)
    return skill_result


def _upload_file_output(
    skill_result: dict[str, Any],
    file_path: str,
    params: dict[str, Any],
    skill_name: str,
) -> dict[str, Any]:
    ticket_id = params.get("ticket_id") or "draft"
    filename = os.path.basename(file_path)
    minio = get_minio_storage()
    object_name = None
    download_url = None
    content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if minio and minio.is_ready():
        object_name = f"change_tickets/{ticket_id}/{filename}"
        if skill_name == "itsm-change-ticket-writer":
            # Editing a ticket must not replace an earlier download's contents.
            digest = sha256(Path(file_path).read_bytes()).hexdigest()[:24]
            filename = "变更工单.xlsx"
            object_name = f"change_tickets/{ticket_id}/{digest}/{filename}"
        with open(file_path, "rb") as f:
            if minio.upload_file(object_name, f.read(), content_type=content_type):
                download_url = minio.get_presigned_url(object_name, expires=3600 * 24 * 7)
    art = make_file_artifact(
        file_key=object_name,
        download_url=download_url,
        filename=filename,
        content_type=content_type if minio and minio.is_ready() else "application/octet-stream",
    )
    skill_result.setdefault("artifacts", {})["change_excel"] = art
    skill_result.setdefault("change_excel_url", download_url)
    skill_result.setdefault("download_url", download_url)
    return skill_result


def _upload_dir_output(
    skill_result: dict[str, Any],
    output_dir: str,
    params: dict[str, Any],
    skill_name: str,
    prepared: dict[str, Any],
) -> dict[str, Any]:
    if skill_result.get("artifacts"):
        return skill_result

    if skill_name == "report-generator":
        return _upload_report_output(skill_result, output_dir, params)

    ticket_id = params.get("ticket_id") or f"SKILL_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    zip_name = f"{skill_name.replace('-', '_')}_{ticket_id}.zip"
    zip_path = os.path.join(os.path.dirname(output_dir), zip_name)
    shutil.make_archive(zip_path.replace(".zip", ""), "zip", output_dir)
    artifact_prefix = f"skill_outputs/{skill_name}/{ticket_id}"
    if skill_name == "firewall-policy-generator" and skill_result.get("policy_plan_id"):
        artifact_prefix += "/" + str(skill_result["policy_plan_id"])

    minio = get_minio_storage()
    object_name = None
    download_url = None
    if minio and minio.is_ready():
        object_name = f"{artifact_prefix}/{zip_name}"
        with open(zip_path, "rb") as f:
            if minio.upload_file(object_name, f, content_type="application/zip"):
                download_url = minio.get_presigned_url(object_name, expires=3600 * 24 * 7)

    art = make_file_artifact(
        file_key=object_name,
        download_url=download_url,
        filename=zip_name,
        content_type="application/zip",
    )
    skill_result.setdefault("artifacts", {})["config_zip"] = art
    skill_result.setdefault("download_url", download_url)
    skill_result.setdefault("config_file_key", object_name)
    if skill_name == "firewall-policy-generator" and minio and minio.is_ready():
        for artifact_key, filename, content_type in (
            ("policy_preview", "策略计划预览.html", "text/html; charset=utf-8"),
            ("policy_request", "策略申请表.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ):
            file_path = Path(output_dir) / filename
            if not file_path.is_file():
                continue
            key = f"{artifact_prefix}/{filename}"
            with file_path.open("rb") as stream:
                if minio.upload_file(key, stream, content_type=content_type):
                    skill_result["artifacts"][artifact_key] = make_file_artifact(
                        file_key=key, download_url=minio.get_presigned_url(key, expires=3600 * 24 * 7),
                        filename=filename, content_type=content_type)
    return skill_result


def _upload_report_output(
    skill_result: dict[str, Any],
    output_dir: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    files = sorted(path for path in Path(output_dir).rglob("*") if path.is_file())
    if len(files) != 1:
        raise SkillExecutionError(
            "report-generator must produce exactly one report file; "
            f"found {len(files)}"
        )

    report_path = files[0]
    content_types = {
        ".md": "text/markdown; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    content_type = content_types.get(report_path.suffix.lower(), "application/octet-stream")
    run_id = str(params.get("run_id") or params.get("ticket_id") or "patrol-run")
    safe_run_id = "".join(
        char if char.isalnum() or char in "._-" else "-" for char in run_id
    ).strip("-.") or "patrol-run"
    object_name = None
    download_url = None
    minio = get_minio_storage()
    if minio and minio.is_ready():
        object_name = f"patrol_reports/{safe_run_id}/{report_path.name}"
        with report_path.open("rb") as stream:
            if minio.upload_file(object_name, stream, content_type=content_type):
                download_url = minio.get_presigned_url(
                    object_name,
                    expires=3600 * 24 * 7,
                )

    artifact = make_file_artifact(
        file_key=object_name,
        download_url=download_url,
        filename=report_path.name,
        content_type=content_type,
    )
    skill_result.setdefault("artifacts", {})["report"] = artifact
    skill_result["output_path"] = report_path.name
    skill_result.setdefault("output_filename", report_path.name)
    skill_result.setdefault("report_file_key", object_name)
    skill_result.setdefault("report_url", download_url)
    skill_result.setdefault("download_url", download_url)
    skill_result.setdefault("filename", report_path.name)
    return skill_result


def _run_legacy_task(skill_name: str, params: dict[str, Any]) -> dict[str, Any]:
    task_name = _LEGACY_TASK_NAMES.get(skill_name)
    if not task_name:
        meta_task = None
        from src.core.skills.resolver import _load_frontmatter

        meta_task = _load_frontmatter(skill_name).get("celery_task")
        task_name = meta_task

    if not task_name:
        raise SkillExecutionError(f"未找到 Skill 执行方式: {skill_name}")

    from src.core.celery_tasks import tasks as task_module

    task_func = getattr(task_module, task_name, None)
    if not task_func:
        raise SkillExecutionError(f"Celery 任务未注册: {task_name}")
    return task_func.run(**params)


def _execute_skill_impl(skill_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """纯 Skill 执行（subprocess / legacy task），不含 observability 与持久化。"""
    params = dict(params or {})
    script = resolve_entry_script(skill_name)

    if script and script.name in (
        "run.py",
        "itsm_change_ticket_excel.py",
        "itsm_callback.py",
        "generate_change_ticket.py",
    ):
        try:
            return _run_subprocess_skill(skill_name, params)
        except SkillExecutionError:
            raise
        except Exception as exc:
            logger.exception("Skill subprocess 异常 skill=%s", skill_name)
            if skill_name == "firewall-policy-generator":
                raise SkillExecutionError(f"防火墙策略输入读取或生成失败：{exc}") from exc
            if skill_name in _LEGACY_TASK_NAMES:
                logger.warning("回退 legacy task: %s", skill_name)
                return _run_legacy_task(skill_name, params)
            raise SkillExecutionError(str(exc)) from exc
    if skill_name in _LEGACY_TASK_NAMES or not script:
        return _run_legacy_task(skill_name, params)
    return _run_subprocess_skill(skill_name, params)


def execute_skill(skill_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """统一 Skill 执行入口（经 SkillRunner 标准化 + 持久化）。"""
    from src.core.skills.runner import SkillRunner, _context_from_params

    params = dict(params or {})
    context = _context_from_params(dict(params))
    result = SkillRunner.run(skill_name, params, context=context)
    return result.to_legacy_dict()

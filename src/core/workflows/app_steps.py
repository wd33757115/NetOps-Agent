# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow steps that call an App action (``skill: app:<app-id>/<action>``).

The action runs as the run's user under that user's App grant. Files it returns are stored
like a Skill's output, so notifications, ``${steps.x.artifacts.k.download_url}`` and the
ITSM callback treat them the same way.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from src.core.workflows.artifacts import make_file_artifact
from src.platform.tools import actions

logger = logging.getLogger(__name__)

ARTIFACT_DAYS = 7


def _store(run_id: str, step_name: str, key: str, filename: str, content_type: str, data: bytes):
    from src.infrastructure.storage.minio_client import get_minio_storage

    minio = get_minio_storage()
    if not minio or not minio.is_ready():
        raise RuntimeError("对象存储不可用，无法保存步骤产物")
    digest = hashlib.sha256(data).hexdigest()[:16]
    object_name = f"workflow_artifacts/{run_id}/{step_name}/{key}/{digest}/{filename}"
    if not minio.upload_file(object_name, data, content_type=content_type):
        raise RuntimeError(f"保存步骤产物失败：{filename}")
    return make_file_artifact(
        file_key=object_name,
        download_url=minio.get_presigned_url(
            object_name, expires=3600 * 24 * ARTIFACT_DAYS, filename=filename
        ),
        filename=filename,
        content_type=content_type,
    )


def _failure(message: str, code: str, step_index: int) -> dict[str, Any]:
    return {"success": False, "error": message, "message": message,
            "failure_code": code, "step_index": step_index}


def run_app_step(run, step_index: int, step_name: str, reference: str,
                 params: dict[str, Any]) -> dict[str, Any]:
    try:
        result = dict(actions.invoke(reference, params, actor_id=run.user_id, run_id=run.id) or {})
    except ValidationError as exc:
        fields = "；".join(
            f"{'.'.join(map(str, error['loc'])) or '参数'}：{error['msg']}" for error in exc.errors()[:5]
        )
        return _failure(f"参数不符合动作要求：{fields}", "app_action_invalid_input", step_index)
    except (LookupError, PermissionError) as exc:
        return _failure(str(exc), "app_action_unavailable", step_index)
    except HTTPException as exc:
        # 401/403/404: the run's user has no grant, or the App is not enabled.
        return _failure(str(exc.detail), "app_action_denied", step_index)
    except Exception as exc:
        logger.exception("App action failed run=%s step=%s action=%s", run.id, step_name, reference)
        return _failure(str(exc) or "应用动作执行失败", "app_action_failed", step_index)

    files = result.pop("files", None) or {}
    artifacts = dict(result.get("artifacts") or {})
    try:
        for key, (filename, content_type, data) in files.items():
            artifacts[key] = _store(run.id, step_name, key, filename, content_type, data)
    except Exception as exc:
        logger.exception("Storing app action files failed run=%s step=%s", run.id, step_name)
        return _failure(str(exc), "app_action_artifact_failed", step_index)
    result["success"] = bool(result.get("success", True))
    return {**result, "artifacts": artifacts, "step_index": step_index}

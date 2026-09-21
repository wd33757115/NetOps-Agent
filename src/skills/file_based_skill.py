# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
基于 SKILL.md 的 Skill 实现（注册到 skill_registry 供 Supervisor 执行）。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

from pydantic import BaseModel, Field, PrivateAttr

from src.skill_system.metadata import SkillMetadata
from src.skills.skill_base import BaseSkill, SkillResult

logger = logging.getLogger(__name__)

# 同步执行的 Skill → handler 模块路径
SYNC_SKILL_HANDLERS: dict[str, str] = {
    "official-document-writing": "src.skills.official_document.service.official_document_writing_handler",
}


def _dummy_handler(*args, **kwargs):
    """占位 handler；实际逻辑在 execute 中。"""
    pass


def create_params_model(metadata: SkillMetadata) -> type[BaseModel]:
    """根据 SKILL.md inputs 动态生成 Pydantic 参数模型。"""
    annotations: dict[str, Any] = {}
    field_definitions: dict[str, Any] = {}

    for inp in metadata.inputs:
        if inp.type == "string":
            field_type = Optional[str]
        elif inp.type in ("integer", "int"):
            field_type = Optional[int]
        elif inp.type in ("number", "float"):
            field_type = Optional[float]
        elif inp.type in ("boolean", "bool"):
            field_type = Optional[bool]
        elif inp.type in ("array", "list"):
            field_type = Optional[list[Any]]
        elif inp.type in ("object", "dict"):
            field_type = Optional[dict[str, Any]]
        else:
            field_type = Optional[Any]

        annotations[inp.name] = field_type
        default_value = inp.default if inp.default is not None else None
        field_definitions[inp.name] = Field(default=default_value, description=inp.description)

    if not field_definitions:
        return BaseModel

    attrs = dict(field_definitions)
    attrs["__annotations__"] = annotations
    return type("SkillParams", (BaseModel,), attrs)


class FileBasedSkill(BaseSkill):
    """SKILL.md 驱动的 Skill；元数据通过 PrivateAttr 保存（兼容 Pydantic v2）。"""

    _metadata: SkillMetadata = PrivateAttr()

    def __init__(self, skill_metadata: SkillMetadata):
        super().__init__(
            name=skill_metadata.name,
            description=skill_metadata.description,
            parameters=create_params_model(skill_metadata),
            handler=_dummy_handler,
            category=skill_metadata.category,
            tags=list(skill_metadata.tags or []),
            fallback_to_rag_if_fail=skill_metadata.fallback_to_rag,
            enabled=skill_metadata.enabled,
        )
        object.__setattr__(self, "_metadata", skill_metadata)

    def get_skill_metadata(self) -> SkillMetadata | None:
        from src.skills.registry import skill_registry

        meta = getattr(self, "_metadata", None)
        if meta is not None:
            return meta
        return skill_registry.get_metadata(self.name)

    def refresh_metadata(self, skill_metadata: SkillMetadata) -> None:
        object.__setattr__(self, "_metadata", skill_metadata)

    async def execute(self, **kwargs) -> SkillResult:
        from src.platform.kernel.registry import migrated_skill_app
        app_id = migrated_skill_app(self.name)
        if app_id:
            return SkillResult(success=False, message=f"请在业务应用中继续：/apps/{app_id}", error="app_entry_required")
        from src.skills.registry import skill_registry

        try:
            metadata = self.get_skill_metadata()
            execution_mode = getattr(metadata, "execution_mode", "async") if metadata else "async"

            if execution_mode == "sync" or self.name in SYNC_SKILL_HANDLERS:
                return await self._execute_sync_handler(kwargs)

            task_name = skill_registry._resolve_celery_task(self.name, metadata)

            if task_name:
                return await self._execute_with_celery(task_name, kwargs)

            from src.core.skills.resolver import resolve_entry_script

            if resolve_entry_script(self.name):
                return await self._execute_entry_script(kwargs)

            from src.skill_system import get_skill_system

            instructions = get_skill_system().get_skill_instructions(self.name)
            return await self._execute_with_llm(instructions, kwargs)
        except Exception as e:
            logger.exception("Skill %s 执行失败", self.name)
            return SkillResult(
                success=False,
                message=f"Skill 执行失败: {str(e)}",
                error=str(e),
            )

    async def _execute_sync_handler(self, params: dict) -> SkillResult:
        """同步执行注册的 Python handler（不经 Celery）。"""
        from src.platform.kernel.registry import migrated_skill_app
        app_id = migrated_skill_app(self.name)
        if app_id:
            return SkillResult(success=False, message=f"请在业务应用中继续：/apps/{app_id}", error="app_entry_required")
        import asyncio
        import importlib

        handler_ref = SYNC_SKILL_HANDLERS.get(self.name)
        if not handler_ref:
            return SkillResult(
                success=False,
                message=f"Skill {self.name} 未配置同步 handler",
                error="sync_handler_not_found",
            )

        module_name, func_name = handler_ref.rsplit(".", 1)
        handler = getattr(importlib.import_module(module_name), func_name)
        if asyncio.iscoroutinefunction(handler):
            result = await handler(dict(params))
        else:
            result = handler(dict(params))

        if isinstance(result, SkillResult):
            return result
        if isinstance(result, dict):
            return SkillResult(
                success=bool(result.get("success", True)),
                message=result.get("message", ""),
                data=result.get("data") or {},
                download_url=result.get("download_url"),
                error=result.get("error"),
                execution_time_ms=int(result.get("execution_time_ms") or 0),
            )
        return SkillResult(success=True, message=str(result), data={"raw_result": result})

    async def _execute_entry_script(self, params: dict) -> SkillResult:
        """通过 entry_script subprocess 同步执行 Skill（不经 Celery / LLM 兜底）。"""
        import asyncio

        from src.core.skills.executor import SkillExecutionError, _execute_skill_impl

        params = dict(params)
        try:
            result = await asyncio.to_thread(_execute_skill_impl, self.name, params)
        except SkillExecutionError as exc:
            return SkillResult(
                success=False,
                message=str(exc),
                error=str(exc),
            )
        except Exception as exc:
            logger.exception("entry_script 执行失败 skill=%s", self.name)
            return SkillResult(
                success=False,
                message=f"Skill 脚本执行失败: {exc}",
                error=str(exc),
            )

        if isinstance(result, dict):
            return SkillResult(
                success=bool(result.get("success", True)),
                message=result.get("message", ""),
                data=result,
                download_url=result.get("download_url"),
                error=result.get("error"),
                execution_time_ms=int(result.get("execution_time_ms") or 0),
            )
        return SkillResult(success=True, message=str(result), data={"raw_result": result})

    async def _execute_with_celery(self, task_name: str, params: dict) -> SkillResult:
        from src.core.celery_tasks import tasks
        from src.skills.registry import skill_registry

        task_func = getattr(tasks, task_name, None)
        if not task_func:
            return SkillResult(
                success=False,
                message=f"找不到任务函数: {task_name}",
                error=f"Task {task_name} not found",
            )

        params = dict(params)
        params = skill_registry._prepare_task_params(task_name, params)

        if task_name == "execute_firewall_policy_task":
            from src.core.firewall_policy.paths import DEFAULT_POLICY_FILE

            if not params.get("policy_file_url") and DEFAULT_POLICY_FILE.exists():
                params["policy_file_url"] = str(DEFAULT_POLICY_FILE)
            if not params.get("ticket_id"):
                for key in ("query", "user_query", "message"):
                    if params.get(key):
                        from src.common.ticket_utils import extract_ticket_id

                        extracted = extract_ticket_id(str(params[key]))
                        if extracted:
                            params["ticket_id"] = extracted
                            break
                if not params.get("ticket_id"):
                    params["ticket_id"] = f"POLICY_{params.get('thread_id', '000')}"
            if not params.get("ticket_title"):
                params["ticket_title"] = "防火墙策略生成"

        from src.core.celery_tasks.celery_exec import (
            CeleryWorkerUnavailableError,
            wait_celery_task_result,
        )

        result = task_func.delay(**params)
        try:
            task_result = wait_celery_task_result(result)
        except CeleryWorkerUnavailableError as exc:
            return SkillResult(success=False, message=str(exc), error="no_celery_worker")
        except TimeoutError as exc:
            return SkillResult(success=False, message=str(exc), error="celery_timeout")

        if isinstance(task_result, dict):
            success = task_result.get("success", task_result.get("status") == "success")
            return SkillResult(
                success=bool(success),
                message=task_result.get("message", ""),
                data=task_result,
                download_url=task_result.get("download_url"),
                error=task_result.get("error"),
                execution_time_ms=int(task_result.get("execution_time_ms") or 0),
            )
        return SkillResult(success=True, message=str(task_result), data={"raw_result": task_result})

    async def _execute_with_llm(self, instructions: str, params: dict) -> SkillResult:
        from langchain_deepseek import ChatDeepSeek

        from src.common.config import get_settings

        settings = get_settings()
        llm = ChatDeepSeek(
            model=settings.LLM_MODEL,
            api_key=settings.DEEPSEEK_API_KEY,
            temperature=0.3,
            request_timeout=60,
        )

        prompt = f"""{instructions}

用户参数：
{json.dumps(params, ensure_ascii=False, indent=2)}

请根据以上指令和参数，执行 Skill 并返回结果。

返回格式：
{{
  "success": true/false,
  "message": "结果描述",
  "data": {{...}}
}}
"""

        try:
            response = await llm.ainvoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)

            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                return SkillResult(**result)
            return SkillResult(
                success=True,
                message="Skill 执行完成",
                data={"raw_output": content},
            )
        except Exception as e:
            return SkillResult(
                success=False,
                message=f"LLM 执行失败: {str(e)}",
                error=str(e),
            )

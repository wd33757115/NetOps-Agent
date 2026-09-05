# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# 导入通用模块
from src.common.logger import skill_logger
from src.common.metrics import Metrics, get_metrics_collector

logger = logging.getLogger(__name__)


class SkillResult(BaseModel):
    """技能执行结果"""
    success: bool = Field(True, description="执行是否成功")
    message: str = Field("", description="结果消息")
    data: dict[str, Any] = Field(default_factory=dict, description="返回数据")
    download_url: str | None = Field(None, description="下载链接")
    error: str | None = Field(None, description="错误信息")
    execution_time_ms: int = Field(0, description="执行时间(毫秒)")

    model_config = ConfigDict(extra="allow")


class BaseSkill(ABC, BaseModel):
    """
    Python Skill 适配基类；脚本型 Skill 通过 SKILL.md 注册，无需继承。

    设计原则：
    1. 每个 Skill 必须有唯一的 name
    2. handler 可以是 Celery task 或普通函数
    3. 参数通过 Pydantic Model 定义，自动校验
    4. 支持 fallback_to_rag_if_fail 机制
    5. 支持超时控制和错误处理
    """

    name: str = Field(..., description="技能唯一名称，用于路由")
    description: str = Field(..., description="技能描述，用于 LLM 理解")
    category: str = Field("general", description="技能分类")
    tags: list[str] = Field(default_factory=list, description="标签")
    parameters: type[BaseModel] = Field(..., description="参数模型(Pydantic)")
    handler: Any = Field(..., description="处理函数或 Celery Task")
    fallback_to_rag_if_fail: bool = Field(True, description="失败时是否走RAG兜底")
    enabled: bool = Field(True, description="是否启用")
    timeout: int = Field(300, description="执行超时时间（秒）")  # 新增：默认 5 分钟超时
    max_retries: int = Field(0, description="最大重试次数")  # 新增

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @abstractmethod
    async def execute(self, **kwargs) -> SkillResult:
        """
        执行技能（异步）

        Args:
            kwargs: 技能参数，会被 parameters 模型校验

        Returns:
            SkillResult: 执行结果
        """
        pass

    def validate_parameters(self, **kwargs) -> BaseModel:
        """
        校验参数

        Args:
            kwargs: 输入参数

        Returns:
            BaseModel: 校验后的参数对象

        Raises:
            ValidationError: 参数校验失败
        """
        try:
            return self.parameters(**kwargs)
        except ValidationError as e:
            logger.warning(f"[{self.name}] 参数校验失败: {e}")
            raise

    def get_description_for_llm(self) -> str:
        """
        生成供 LLM 使用的技能描述

        Returns:
            str: 格式化的技能描述字符串
        """
        param_descriptions = []
        if self.parameters:
            for name, field_info in self.parameters.model_fields.items():
                required = "必填" if field_info.is_required() else "可选"
                field_type = field_info.annotation.__name__ if hasattr(field_info.annotation, '__name__') else str(field_info.annotation)
                desc = field_info.description or ""
                param_descriptions.append(f"- {name} ({field_type}, {required}): {desc}")

        params_str = "\n".join(param_descriptions) if param_descriptions else "无参数"

        return f"""
技能名称：{self.name}
描述：{self.description}
分类：{self.category}
参数：
{params_str}
        """.strip()

    def to_dict(self) -> dict[str, Any]:
        """转换为字典，用于序列化给 LLM"""
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "tags": self.tags,
            "parameters": self._get_parameter_schema(),
            "enabled": self.enabled
        }

    def _get_parameter_schema(self) -> list[dict[str, Any]]:
        """获取参数 Schema 列表"""
        schema = []
        if self.parameters:
            for name, field_info in self.parameters.model_fields.items():
                field_type = field_info.annotation.__name__ if hasattr(field_info.annotation, '__name__') else str(field_info.annotation)
                schema.append({
                    "name": name,
                    "type": field_type,
                    "required": field_info.is_required(),
                    "description": field_info.description or ""
                })
        return schema


class CelerySkill(BaseSkill):
    """
    基于 Celery 的异步技能实现

    使用 Celery Task 或普通函数作为 handler，自动异步执行
    支持超时控制和错误处理
    """

    async def execute(self, **kwargs) -> SkillResult:
        """
        执行技能

        Args:
            kwargs: 技能参数

        Returns:
            SkillResult: 执行结果
        """
        start_time = time.time()
        metrics = get_metrics_collector()

        try:
            # 校验参数
            validated_params = self.validate_parameters(**kwargs)

            # 记录指标
            metrics.increment(Metrics.SKILL_EXECUTION_TOTAL, tags={"skill": self.name})

            # 检查是否是 Celery Task（有 delay 方法）
            if hasattr(self.handler, 'delay'):
                # 调用 Celery Task（异步）
                result = self.handler.delay(**validated_params.model_dump())

                # 等待任务完成（使用配置的 timeout）
                timeout = self.timeout
                task_result = result.get(timeout=timeout)
            else:
                # 普通函数调用
                task_result = self.handler(validated_params.model_dump())

            # 转换为 SkillResult
            if isinstance(task_result, dict):
                result = SkillResult(**task_result)
            elif isinstance(task_result, SkillResult):
                result = task_result
            else:
                result = SkillResult(
                    success=True,
                    message=str(task_result),
                    data={"raw_result": task_result}
                )

            # 记录成功指标
            metrics.increment(Metrics.SKILL_EXECUTION_SUCCESS, tags={"skill": self.name})
            result.execution_time_ms = int((time.time() - start_time) * 1000)

            # 记录日志
            skill_logger.skill_execution(
                skill_name=self.name,
                duration_ms=result.execution_time_ms,
                success=True
            )

            return result

        except Exception as e:
            # 计算耗时
            duration_ms = int((time.time() - start_time) * 1000)

            # 记录失败指标
            metrics.increment(Metrics.SKILL_EXECUTION_ERROR, tags={"skill": self.name})

            # 判断异常类型
            error_type = type(e).__name__
            error_msg = str(e)

            # 记录日志
            skill_logger.skill_execution(
                skill_name=self.name,
                duration_ms=duration_ms,
                success=False,
                error=error_msg
            )

            # 根据异常类型返回结果
            if "timeout" in error_type.lower() or "TimeoutError" in error_type:
                return SkillResult(
                    success=False,
                    message=f"技能执行超时（{self.timeout}s）",
                    error=f"TimeoutError: {error_msg}",
                    execution_time_ms=duration_ms
                )
            elif "ValidationError" in error_type:
                return SkillResult(
                    success=False,
                    message="参数校验失败",
                    error=f"ValidationError: {error_msg}",
                    execution_time_ms=duration_ms
                )
            else:
                # 决定是否 fallback
                if self.fallback_to_rag_if_fail:
                    return SkillResult(
                        success=False,
                        message="技能执行失败，将走 RAG 兜底",
                        error=error_msg,
                        execution_time_ms=duration_ms
                    )
                else:
                    return SkillResult(
                        success=False,
                        message="技能执行失败",
                        error=error_msg,
                        execution_time_ms=duration_ms
                    )


class SkillDecision(BaseModel):
    """
    Supervisor structured decision output model (Pydantic v2)

    Used by LLM via with_structured_output(method="function_calling").
    All Field descriptions are in English to ensure DeepSeek function calling
    compatibility (function names must match ^[a-zA-Z0-9_-]+$).

    Fields:
    - reasoning: LLM's reasoning process for choosing a skill or RAG
    - skill_name: name of the selected skill, None means use RAG Q&A
    - parameters: key-value parameters for skill execution, empty dict allowed
    - fallback_to_rag: force fallback to RAG when skill is unavailable
    """

    reasoning: str = Field(
        default="",
        min_length=1,
        description=(
            "Reasoning process explaining why this skill or RAG was chosen. "
            "Must include: 1) user intent analysis 2) skill selection rationale "
            "3) parameter extraction logic. "
        )
    )
    skill_name: str | None = Field(
        default=None,
        description=(
            "Selected skill name from the provided available skills list. "
            "Set to null to route to RAG knowledge base Q&A."
        )
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Parameters for skill execution in key-value format. "
            "Extracted from user request and context. Empty dict allowed."
        )
    )
    fallback_to_rag: bool = Field(
        default=False,
        description=(
            "Force fallback to RAG knowledge base. "
            "Set to true when user intent is purely knowledge-based question."
        )
    )

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )

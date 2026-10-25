# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import pkgutil
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from .skill_base import BaseSkill, CelerySkill, SkillDecision, SkillResult

logger = logging.getLogger(__name__)

CELERY_TASK_TIMEOUT = 300
CELERY_POLL_INTERVAL = 2

# Embedding 相关配置
EMBEDDING_CACHE_TTL = 300  # 5分钟
EMBEDDING_MODEL_NAME = "BAAI/bge-m3"
TOP_N_SKILLS = 3  # 预筛选返回的技能数量


class SkillMetadata(BaseModel):
    """
    Skill 元数据规范
    
    用于描述技能的基本信息，支持版本管理和 pip 包发现
    """
    name: str = Field(..., description="技能名称（唯一标识）")
    version: str = Field("1.0.0", description="技能版本")
    description: str = Field(..., description="技能描述")
    category: str = Field("general", description="技能分类")
    tags: list[str] = Field([], description="标签列表")
    author: str | None = Field(None, description="作者")
    author_email: str | None = Field(None, description="作者邮箱")
    homepage: str | None = Field(None, description="项目主页")
    license: str | None = Field(None, description="许可证")
    requires_python: str | None = Field(None, description="Python版本要求")
    dependencies: list[str] = Field([], description="依赖包列表")

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return self.model_dump()


class SkillRegistry:
    """
    技能注册表 - 管理所有运维技能的注册、查询和执行
    
    设计特点：
    1. 单例模式，全局共享
    2. 支持技能注册、查询、删除
    3. 提供给 LLM 的技能列表（格式化描述）
    4. 异步执行技能，自动参数校验
    5. 支持 fallback_to_rag 机制
    6. 支持 pip install 的 Skill 包自动发现
    7. 支持 Embedding 预筛选（Hybrid 决策优化）
    """

    _instance: SkillRegistry | None = None
    _skills: dict[str, BaseSkill] = {}
    _embedding_cache: dict[str, list[float]] = {}  # skill_name -> embedding vector
    _embedding_model_instance = None
    _metadata_cache: dict[str, SkillMetadata] = {}  # skill_name -> metadata

    def __new__(cls) -> SkillRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._skills = {}
            cls._embedding_cache = {}
            cls._embedding_model_instance = None
            cls._metadata_cache = {}
        return cls._instance

    def register_skill(self, skill: BaseSkill, metadata: SkillMetadata | None = None) -> None:
        """
        注册技能
        
        Args:
            skill: 技能对象（必须继承 BaseSkill）
            metadata: 技能元数据（可选）
            
        Raises:
            ValueError: 技能名称重复或技能未启用
        """
        if not isinstance(skill, BaseSkill):
            raise ValueError("技能必须继承 BaseSkill")

        if not skill.enabled:
            logger.info(f"技能 {skill.name} 未启用，跳过注册")
            return

        if skill.name in self._skills:
            logger.warning(f"技能 {skill.name} 已存在，将被覆盖")

        self._skills[skill.name] = skill

        # 保存元数据
        if metadata:
            self._metadata_cache[skill.name] = metadata
        elif skill.name not in self._metadata_cache:
            # 创建默认元数据
            self._metadata_cache[skill.name] = SkillMetadata(
                name=skill.name,
                version="1.0.0",
                description=skill.description,
                category=skill.category,
                tags=skill.tags or []
            )

        logger.info(f"技能注册成功: {skill.name} v{self._metadata_cache[skill.name].version}")

    def register_celery_skill(
        self,
        name: str,
        description: str,
        parameters: type[BaseModel],
        handler,
        category: str = "general",
        tags: list[str] = None,
        fallback_to_rag_if_fail: bool = True,
        enabled: bool = True,
        version: str = "1.0.0",
        **kwargs
    ) -> None:
        """
        便捷方法：注册 Celery 技能
        
        Args:
            name: 技能名称
            description: 技能描述
            parameters: 参数模型（Pydantic BaseModel）
            handler: Celery Task
            category: 分类
            tags: 标签列表
            fallback_to_rag_if_fail: 失败是否走 RAG
            enabled: 是否启用
            version: 版本号
            **kwargs: 其他元数据字段
        """
        skill = CelerySkill(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            category=category,
            tags=tags or [],
            fallback_to_rag_if_fail=fallback_to_rag_if_fail,
            enabled=enabled
        )

        # 创建元数据
        metadata = SkillMetadata(
            name=name,
            version=version,
            description=description,
            category=category,
            tags=tags or [],
            **kwargs
        )

        self.register_skill(skill, metadata)

    def discover_pip_skills(self) -> int:
        """
        自动发现通过 pip install 安装的 Skill 包
        
        发现规则：
        1. 扫描所有已安装包中以 'netops-skill-' 或 'ops-skill-' 开头的包
        2. 查找包中是否有 'register_skill' 函数
        3. 调用注册函数
        
        Returns:
            int: 发现并注册的技能数量
        """
        discovered_count = 0

        try:
            for _, pkg_name, is_pkg in pkgutil.iter_modules():
                # 匹配 Skill 包命名模式
                if pkg_name.startswith('netops_skill_') or pkg_name.startswith('ops_skill_'):
                    try:
                        module = importlib.import_module(pkg_name)
                        if hasattr(module, 'register_skill'):
                            register_func = getattr(module, 'register_skill')
                            if callable(register_func):
                                register_func()
                                discovered_count += 1
                                logger.info(f"从 pip 包发现并注册技能: {pkg_name}")
                    except Exception as e:
                        logger.warning(f"加载 pip 技能包 {pkg_name} 失败: {e}")
        except Exception as e:
            logger.error(f"扫描 pip 技能包失败: {e}")

        return discovered_count

    def get_metadata(self, skill_name: str) -> SkillMetadata | None:
        """
        获取技能元数据
        
        Args:
            skill_name: 技能名称
            
        Returns:
            SkillMetadata: 元数据对象
        """
        return self._metadata_cache.get(skill_name)

    def list_all_metadata(self) -> list[SkillMetadata]:
        """获取所有技能的元数据列表"""
        return list(self._metadata_cache.values())

    def get_skill(self, name: str) -> BaseSkill | None:
        """
        获取技能
        
        Args:
            name: 技能名称
            
        Returns:
            BaseSkill: 技能对象，如果不存在返回 None
        """
        return self._skills.get(name)

    def list_skills(self, category: str = None) -> list[BaseSkill]:
        """
        获取技能列表
        
        Args:
            category: 分类过滤，None 表示所有分类
            
        Returns:
            List[BaseSkill]: 技能列表
        """
        skills = list(self._skills.values())
        if category:
            skills = [s for s in skills if s.category == category]
        return sorted(skills, key=lambda x: x.name)

    def list_skills_for_llm(self, top_n: int | None = None) -> str:
        """
        生成供 LLM 使用的技能列表描述
        
        Args:
            top_n: 返回前 N 个技能（用于 Embedding 预筛选后的精细决策）
            
        Returns:
            str: 格式化的技能列表，适合作为 prompt 输入
        """
        skills = self.list_skills()

        if not skills:
            return "无可用技能"

        # 如果指定了 top_n，只返回前 N 个
        if top_n and top_n > 0:
            skills = skills[:top_n]

        skill_descriptions = []
        for skill in skills:
            skill_descriptions.append(skill.get_description_for_llm())

        return "\n\n---\n\n".join(skill_descriptions)

    def get_skills_json_for_llm(self) -> list[dict[str, Any]]:
        """
        获取技能的 JSON 格式列表，用于 LLM 的 structured output
        
        Returns:
            List[Dict]: 技能信息列表
        """
        return [skill.to_dict() for skill in self.list_skills()]

    # ==================== Embedding 预筛选相关 ====================

    def _get_embedding_model(self):
        """懒加载 Embedding 模型"""
        try:
            from sentence_transformers import SentenceTransformer
            if self._embedding_model_instance is None:
                self._embedding_model_instance = SentenceTransformer(EMBEDDING_MODEL_NAME)
            return self._embedding_model_instance
        except ImportError:
            logger.error("sentence_transformers 未安装，请安装: pip install sentence-transformers")
            raise

    def _compute_skill_embedding(self, skill: BaseSkill) -> list[float]:
        """
        计算技能描述的 Embedding
        
        Args:
            skill: 技能对象
            
        Returns:
            List[float]: 向量表示
        """
        model = self._get_embedding_model()
        # 使用名称 + 描述 + 标签生成 Embedding
        text = f"{skill.name}: {skill.description} {' '.join(skill.tags or [])}"
        embedding = model.encode(text)
        return embedding.tolist()

    def _compute_query_embedding(self, query: str) -> list[float]:
        """
        计算用户查询的 Embedding
        
        Args:
            query: 用户查询文本
            
        Returns:
            List[float]: 向量表示
        """
        model = self._get_embedding_model()
        return model.encode(query).tolist()

    def _cosine_similarity(self, vec1: list[float], vec2: list[float]) -> float:
        """计算两个向量的余弦相似度"""
        import numpy as np
        vec1_np = np.array(vec1)
        vec2_np = np.array(vec2)
        dot_product = np.dot(vec1_np, vec2_np)
        norm1 = np.linalg.norm(vec1_np)
        norm2 = np.linalg.norm(vec2_np)
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return float(dot_product / (norm1 * norm2))

    def prefilter_by_embedding(self, query: str, top_n: int = TOP_N_SKILLS) -> list[BaseSkill]:
        """
        使用 Embedding 预筛选最相关的技能
        
        Hybrid 决策流程第一阶段：快速筛选（<10ms）
        
        Args:
            query: 用户查询
            top_n: 返回前 N 个最相关技能
            
        Returns:
            List[BaseSkill]: 排序后的技能列表（按相似度降序）
        """
        skills = self.list_skills()
        if not skills:
            return []

        try:
            # 计算查询 Embedding
            query_embedding = self._compute_query_embedding(query)

            # 计算每个技能的相似度
            similarities = []
            for skill in skills:
                # 从缓存获取或计算技能 Embedding
                if skill.name not in self._embedding_cache:
                    self._embedding_cache[skill.name] = self._compute_skill_embedding(skill)

                skill_embedding = self._embedding_cache[skill.name]
                similarity = self._cosine_similarity(query_embedding, skill_embedding)
                similarities.append((skill, similarity))

            # 按相似度降序排序，取前 N 个
            similarities.sort(key=lambda x: x[1], reverse=True)
            top_skills = [skill for skill, sim in similarities[:top_n]]

            logger.info(f"Embedding 预筛选完成: query='{query[:30]}...', 找到 {len(top_skills)} 个相关技能")
            for skill in top_skills:
                idx = similarities.index((skill, self._cosine_similarity(query_embedding, self._embedding_cache[skill.name])))
                logger.info(f"  - {skill.name} (相似度: {similarities[idx][1]:.4f})")

            return top_skills

        except Exception as e:
            logger.error(f"Embedding 预筛选失败，回退到全部技能: {e}")
            return skills

    def get_top_skills_for_llm(self, query: str, top_n: int = TOP_N_SKILLS) -> str:
        """
        获取预筛选后的技能列表供 LLM 使用
        
        Args:
            query: 用户查询
            top_n: 返回前 N 个最相关技能
            
        Returns:
            str: 格式化的技能列表
        """
        top_skills = self.prefilter_by_embedding(query, top_n)

        if not top_skills:
            return "无可用技能"

        skill_descriptions = []
        for skill in top_skills:
            skill_descriptions.append(skill.get_description_for_llm())

        return "\n\n---\n\n".join(skill_descriptions)

    def clear_embedding_cache(self) -> None:
        """清空 Embedding 缓存"""
        self._embedding_cache.clear()
        logger.info("Embedding 缓存已清空")

    # ==================== 执行相关 ====================

    async def validate_and_execute(self, decision: SkillDecision) -> SkillResult:
        """
        验证决策并执行技能（异步）
        
        Args:
            decision: Supervisor 决策结果
            
        Returns:
            SkillResult: 执行结果
        """
        if not decision.skill_name:
            return SkillResult(
                success=True,
                message="未选择技能，将走 RAG 处理",
                data={"decision": decision.model_dump()}
            )

        skill = self.get_skill(decision.skill_name)
        if not skill:
            logger.error(f"技能 {decision.skill_name} 不存在")
            return SkillResult(
                success=False,
                message=f"技能 {decision.skill_name} 不存在",
                error=f"Skill {decision.skill_name} not found in registry",
                data={"fallback_to_rag": True}
            )

        logger.info(f"执行技能: {skill.name}, 参数: {decision.parameters}")

        try:
            result = await skill.execute(**decision.parameters)

            if not result.success and skill.fallback_to_rag_if_fail:
                logger.info(f"技能 {skill.name} 执行失败，触发 RAG 兜底")
                result.data["fallback_to_rag"] = True

            return result

        except Exception as e:
            error_msg = str(e)
            logger.error(f"技能 {skill.name} 执行异常: {error_msg}")

            if skill.fallback_to_rag_if_fail:
                return SkillResult(
                    success=False,
                    message=f"技能 {skill.name} 执行异常",
                    error=error_msg,
                    data={"fallback_to_rag": True}
                )

            return SkillResult(
                success=False,
                message=f"技能 {skill.name} 执行异常",
                error=error_msg
            )

    def execute_sync(self, decision: SkillDecision) -> SkillResult:
        """
        同步执行技能（封装异步调用）
        
        Args:
            decision: Supervisor 决策结果
            
        Returns:
            SkillResult: 执行结果
        """
        try:
            asyncio.get_running_loop()
            import nest_asyncio
            nest_asyncio.apply()
            return asyncio.run(self.validate_and_execute(decision))
        except RuntimeError:
            return asyncio.run(self.validate_and_execute(decision))

    def submit_skill_task(self, decision: SkillDecision) -> SkillResult:
        if not decision.skill_name:
            return SkillResult(
                success=True,
                message="未选择技能，将走 RAG 处理",
                data={"decision": decision.model_dump()}
            )

        skill = self.get_skill(decision.skill_name)
        if not skill:
            logger.error(f"技能 {decision.skill_name} 不存在")
            return SkillResult(
                success=False,
                message=f"技能 {decision.skill_name} 不存在",
                error=f"Skill {decision.skill_name} not found in registry",
                data={"fallback_to_rag": True}
            )

        try:
            validated_params = skill.validate_parameters(**decision.parameters)
        except ValidationError as e:
            error_msg = str(e)
            logger.error(f"技能 {skill.name} 参数校验失败: {error_msg}")
            return SkillResult(
                success=False,
                message=f"参数校验失败: {error_msg}",
                error=error_msg,
                data={"fallback_to_rag": skill.fallback_to_rag_if_fail}
            )

        if not hasattr(skill.handler, 'delay') and not hasattr(skill.handler, 'apply_async'):
            meta = None
            if hasattr(skill, "get_skill_metadata"):
                meta = skill.get_skill_metadata()
            else:
                meta = getattr(skill, "_metadata", None)
            task_name = self._resolve_celery_task(skill.name, meta)
            if task_name:
                try:
                    from src.core.celery_tasks import tasks
                    task_func = getattr(tasks, task_name, None)
                    if task_func and hasattr(task_func, 'apply_async'):
                        params = self._prepare_task_params(task_name, validated_params.model_dump())
                        queue = self._resolve_celery_queue(skill.name)
                        task_result = task_func.apply_async(kwargs=params, queue=queue)
                        celery_task_id = task_result.task_id
                        logger.info(f"[Celery] FileBasedSkill 任务已提交: task_id={celery_task_id}")
                        return SkillResult(
                            success=True,
                            message="任务已提交，正在后台处理...",
                            data={
                                "celery_task_id": celery_task_id,
                                "skill_name": skill.name,
                                "parameters": params
                            }
                        )
                except Exception as e:
                    logger.warning(f"解析 Celery 任务失败，回退同步执行: {e}")

            logger.info(f"技能 {skill.name} 使用同步 handler")
            try:
                result = self.execute_sync(decision)
                if not result.success and skill.fallback_to_rag_if_fail:
                    if not result.data:
                        result.data = {}
                    result.data["fallback_to_rag"] = True
                return result
            except Exception as e:
                logger.error(f"技能 {skill.name} 执行异常: {e}")
                return SkillResult(
                    success=False,
                    message=f"技能 {skill.name} 执行异常",
                    error=str(e),
                    data={"fallback_to_rag": skill.fallback_to_rag_if_fail}
                )

        try:
            queue = self._resolve_celery_queue(skill.name)
            task_result = skill.handler.apply_async(
                kwargs=validated_params.model_dump(),
                queue=queue,
            )
            celery_task_id = task_result.task_id
            logger.info(f"[Celery] 任务已提交: task_id={celery_task_id}")
        except Exception as e:
            logger.error(f"[Celery] 任务提交失败: {e}")
            return SkillResult(
                success=False,
                message="Celery 任务提交失败",
                error=str(e),
                data={"fallback_to_rag": skill.fallback_to_rag_if_fail}
            )

        return SkillResult(
            success=True,
            message="任务已提交，正在后台处理...",
            data={
                "celery_task_id": celery_task_id,
                "skill_name": skill.name,
                "parameters": validated_params.model_dump()
            }
        )

    async def async_execute_skill(self, decision: SkillDecision) -> SkillResult:
        """
        Celery 异步执行技能（非阻塞）

        与 execute_sync 的区别：
        - execute_sync 使用 Celery Task.get(timeout=300) 阻塞等待
        - async_execute_skill 使用 apply_async + 轮询，不阻塞事件循环

        工作流程：
        1. 使用 Celery task.apply_async 提交任务
        2. 通过 AsyncResult.ready() 异步轮询状态
        3. 任务完成时获取结果并返回 SkillResult
        4. 超时或失败时触发 RAG fallback

        Args:
            decision: Supervisor 决策结果

        Returns:
            SkillResult: 执行结果（包含 download_url、log、status 等）
        """
        if not decision.skill_name:
            return SkillResult(
                success=True,
                message="未选择技能，将走 RAG 处理",
                data={"decision": decision.model_dump()}
            )

        skill = self.get_skill(decision.skill_name)
        if not skill:
            logger.error(f"技能 {decision.skill_name} 不存在")
            return SkillResult(
                success=False,
                message=f"技能 {decision.skill_name} 不存在",
                error=f"Skill {decision.skill_name} not found in registry",
                data={"fallback_to_rag": True}
            )

        logger.info(f"[Celery] 异步提交技能: {skill.name}, 参数: {decision.parameters}")

        try:
            validated_params = skill.validate_parameters(**decision.parameters)
        except ValidationError as e:
            error_msg = str(e)
            logger.error(f"技能 {skill.name} 参数校验失败: {error_msg}")
            return SkillResult(
                success=False,
                message=f"参数校验失败: {error_msg}",
                error=error_msg,
                data={"fallback_to_rag": skill.fallback_to_rag_if_fail}
            )

        # 检查 handler 是否是 Celery Task
        if not hasattr(skill.handler, 'delay') and not hasattr(skill.handler, 'apply_async'):
            # 非 Celery handler，使用原有的 execute 方法
            logger.info(f"技能 {skill.name} 使用同步 handler")
            try:
                result = await skill.execute(**decision.parameters)
                if not result.success and skill.fallback_to_rag_if_fail:
                    result.data["fallback_to_rag"] = True
                return result
            except Exception as e:
                logger.error(f"技能 {skill.name} 执行异常: {e}")
                return SkillResult(
                    success=False,
                    message=f"技能 {skill.name} 执行异常",
                    error=str(e),
                    data={"fallback_to_rag": skill.fallback_to_rag_if_fail}
                )

        # Celery 异步执行：使用 apply_async 提交任务
        try:
            queue = self._resolve_celery_queue(skill.name)
            task_result = skill.handler.apply_async(
                kwargs=validated_params.model_dump(),
                queue=queue,
            )
            celery_task_id = task_result.task_id
            logger.info(f"[Celery] 任务已提交: task_id={celery_task_id} queue={queue}")
        except Exception as e:
            logger.error(f"[Celery] 任务提交失败: {e}")
            return SkillResult(
                success=False,
                message="Celery 任务提交失败",
                error=str(e),
                data={"fallback_to_rag": skill.fallback_to_rag_if_fail}
            )

        # 异步轮询等待任务完成
        elapsed = 0
        last_state = "PENDING"
        while elapsed < CELERY_TASK_TIMEOUT:
            await asyncio.sleep(CELERY_POLL_INTERVAL)
            elapsed += CELERY_POLL_INTERVAL

            try:
                from celery.result import AsyncResult
                async_result = AsyncResult(celery_task_id, app=skill.handler.app)

                current_state = async_result.state
                if current_state != last_state:
                    logger.info(f"[Celery] task_id={celery_task_id} state: {current_state}")
                    last_state = current_state

                if async_result.ready():
                    if async_result.successful():
                        task_output = async_result.get()
                        if isinstance(task_output, dict):
                            result = SkillResult(**task_output)
                        elif isinstance(task_output, SkillResult):
                            result = task_output
                        else:
                            result = SkillResult(
                                success=True,
                                message=str(task_output),
                                data={"raw_result": task_output}
                            )
                        # 确保 celery_task_id 被记录（成功时也要记录）
                        if not result.data:
                            result.data = {}
                        result.data["celery_task_id"] = celery_task_id
                        logger.info(f"[Celery] 任务完成: task_id={celery_task_id}, success={result.success}")
                        return result
                    else:
                        error_info = str(async_result.info) if async_result.info else "未知错误"
                        logger.error(f"[Celery] 任务失败: task_id={celery_task_id}, error={error_info}")
                        return SkillResult(
                            success=False,
                            message="Celery 任务执行失败",
                            error=error_info,
                            data={"fallback_to_rag": skill.fallback_to_rag_if_fail, "celery_task_id": celery_task_id}
                        )

                if async_result.failed():
                    error_info = str(async_result.info) if async_result.info else "未知错误"
                    logger.error(f"[Celery] 任务失败: task_id={celery_task_id}, error={error_info}")
                    return SkillResult(
                        success=False,
                        message="Celery 任务执行失败",
                        error=error_info,
                        data={"fallback_to_rag": skill.fallback_to_rag_if_fail, "celery_task_id": celery_task_id}
                    )

            except Exception as poll_error:
                logger.warning(f"[Celery] 轮询异常: {poll_error}")
                continue

        # 超时处理
        logger.error(f"[Celery] 任务超时: task_id={celery_task_id}, timeout={CELERY_TASK_TIMEOUT}s")
        return SkillResult(
            success=False,
            message=f"技能执行超时（{CELERY_TASK_TIMEOUT}秒）",
            error="Celery task timeout",
            data={
                "fallback_to_rag": skill.fallback_to_rag_if_fail,
                "celery_task_id": celery_task_id,
                "timeout": True
            }
        )

    def unregister_skill(self, name: str) -> bool:
        """
        注销技能
        
        Args:
            name: 技能名称
            
        Returns:
            bool: 是否成功注销
        """
        if name in self._skills:
            del self._skills[name]
            if name in self._metadata_cache:
                del self._metadata_cache[name]
            if name in self._embedding_cache:
                del self._embedding_cache[name]
            logger.info(f"技能注销成功: {name}")
            return True
        return False

    def clear_all(self) -> None:
        """清空所有技能"""
        self._skills.clear()
        self._metadata_cache.clear()
        self._embedding_cache.clear()
        logger.info("所有技能已清空")

    def get_statistics(self) -> dict[str, Any]:
        """获取技能统计信息"""
        skills = self.list_skills()
        categories = {}
        for skill in skills:
            categories[skill.category] = categories.get(skill.category, 0) + 1

        return {
            "total_skills": len(skills),
            "categories": categories,
            "cached_embeddings": len(self._embedding_cache)
        }

    # ==================== SKILL.md 文件驱动支持 ====================

    # Skill 名称到 Celery Task 的硬编码映射（向后兼容）
    _SKILL_TO_TASK_MAP = {
        "firewall-policy-generator": "execute_firewall_policy_task",
        "device-backup": "execute_config_backup_task",
        "device-patrol": "execute_device_patrol_task",
        "itsm-change-ticket-writer": "execute_itsm_change_ticket_task",
        "itsm-callback": "execute_itsm_callback_task",
    }

    @staticmethod
    def _resolve_celery_queue(skill_name: str) -> str:
        from src.core.skills.celery_routing import resolve_skill_celery_queue

        return resolve_skill_celery_queue(skill_name)

    @classmethod
    def _resolve_celery_task(cls, skill_name: str, metadata=None) -> str | None:
        """
        三级自动解析 Celery 任务名称

        优先级:
          1. execution_mode=sync → 不走 Celery
          2. 硬编码映射 _SKILL_TO_TASK_MAP（向后兼容）
          3. SKILL.md frontmatter 的 celery_task 字段
          4. 命名约定自动推导
        """
        if metadata and getattr(metadata, "execution_mode", None) == "sync":
            return None
        task_name = cls._SKILL_TO_TASK_MAP.get(skill_name)
        if task_name:
            return task_name

        # L2: SKILL.md 声明的 celery_task
        if metadata and getattr(metadata, 'celery_task', None):
            return metadata.celery_task

        # L3: 命名约定自动推导
        normalized = skill_name.replace("-", "_")
        derived = f"execute_{normalized}_task"

        try:
            from src.core.celery_tasks.tasks import celery_app
            # 检查 Celery 任务是否确实存在
            if derived in celery_app.tasks:
                return derived
        except Exception:
            pass

        return None

    @classmethod
    def _prepare_task_params(cls, task_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        为 Celery 任务补全默认参数值

        从 FileBasedSkill._execute_with_celery 提取的通用逻辑，
        避免因参数不完整导致 Celery 任务 TypeError。

        Args:
            task_name: Celery 任务名称
            params: LLM 提取的原始参数

        Returns:
            Dict[str, Any]: 补全后的参数字典
        """
        if task_name == "execute_firewall_policy_task":
            if not params.get("ticket_id"):
                for key in ("query", "user_query", "message"):
                    extracted = None
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

        elif task_name == "execute_config_backup_task":
            if not params.get("filter_params"):
                params["filter_params"] = {}
            if not params.get("ticket_id"):
                params["ticket_id"] = f"BACKUP_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        elif task_name == "execute_device_patrol_task":
            if not params.get("filter_params"):
                params["filter_params"] = {}
            if not params.get("ticket_id"):
                params["ticket_id"] = f"PATROL_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        return params

    def _refresh_file_skill_from_metadata(self, metadata) -> None:
        """热更新已注册的文件型 Skill 元数据。"""
        from src.skills.file_based_skill import FileBasedSkill

        skill = self._skills.get(metadata.name)
        if not skill:
            return
        skill.enabled = metadata.enabled
        skill.description = metadata.description
        skill.category = metadata.category
        skill.tags = list(metadata.tags or [])
        skill.fallback_to_rag_if_fail = metadata.fallback_to_rag
        if isinstance(skill, FileBasedSkill):
            skill.refresh_metadata(metadata)
        self._metadata_cache[metadata.name] = metadata

    def discover_skills_from_files(self, skill_dirs: list[str] = None) -> int:
        """兼容别名：委托 sync_skills_from_files。"""
        return self.sync_skills_from_files(skill_dirs)

    def _is_current_file_based_skill(self, skill) -> bool:
        from src.skills.file_based_skill import FileBasedSkill

        if not isinstance(skill, FileBasedSkill) or not callable(getattr(skill, "get_skill_metadata", None)):
            return False
        try:
            return skill.get_skill_metadata() is not None
        except AttributeError:
            return False

    def _register_file_based_skill(self, metadata) -> None:
        from src.skill_system.metadata import SkillMetadata as SysSkillMetadata
        from src.skills.file_based_skill import FileBasedSkill

        skill = FileBasedSkill(metadata)
        self.register_skill(skill, SysSkillMetadata(**metadata.model_dump()))

    def sync_skills_from_files(self, skill_dirs: list[str] = None, *, force_replace: bool = False) -> int:
        """
        从文件系统扫描 SKILL.md，注册或热更新 Skill。

        Args:
            skill_dirs: Skill 目录列表，默认为 ['src/skills']

        Returns:
            int: 同步的 Skill 数量
        """
        if skill_dirs is None:
            base_dir = Path(__file__).parent.parent.parent
            skill_dirs = [str(base_dir / "src" / "skills")]

        discovered_count = 0

        roots = {Path(directory).resolve() for directory in skill_dirs}
        for name, metadata in list(self._metadata_cache.items()):
            path = getattr(metadata, 'skill_path', None)
            directory = Path(path).resolve() if path else None
            if directory and directory.parent in roots and not (directory / 'SKILL.md').is_file():
                self._skills.pop(name, None)
                self._metadata_cache.pop(name, None)
                self._embedding_cache.pop(name, None)

        for skill_dir_str in skill_dirs:
            skill_dir = Path(skill_dir_str)

            if not skill_dir.exists():
                logger.warning(f"Skill 目录不存在: {skill_dir}")
                continue

            # 扫描子目录
            for item in skill_dir.iterdir():
                if not item.is_dir():
                    continue

                # 跳过隐藏目录和 examples 目录（Python Skill）
                if item.name.startswith('.') or item.name == 'examples':
                    continue

                skill_md = item / "SKILL.md"
                if not skill_md.exists():
                    continue

                try:
                    from src.skill_system.metadata import SkillMetadata, parse_skill_md

                    metadata = parse_skill_md(skill_md, include_instructions=False)

                    # 与其他已注册 Skill 同义（连字符/下划线）则跳过，避免 device-backup vs device_backup 双注册
                    norm = metadata.name.replace("-", "_").lower()
                    if any(
                        k != metadata.name and k.replace("-", "_").lower() == norm
                        for k in self._skills
                    ):
                        logger.info(f"Skill {metadata.name} 已有同义注册，跳过")
                        continue

                    if metadata.name in self._skills:
                        existing = self._skills[metadata.name]
                        if force_replace or not self._is_current_file_based_skill(existing):
                            self._register_file_based_skill(metadata)
                            logger.info(
                                "替换 Skill 实例: %s (旧类=%s)",
                                metadata.name,
                                type(existing).__name__,
                            )
                        else:
                            self._refresh_file_skill_from_metadata(metadata)
                        discovered_count += 1
                        continue

                    self._register_file_based_skill(metadata)
                    discovered_count += 1
                    logger.info(f"从文件发现 Skill: {metadata.name} v{metadata.version}")

                except Exception as e:
                    logger.error(f"加载 Skill {item.name} 失败: {e}")
                    import traceback
                    traceback.print_exc()

        return discovered_count

    def get_skill_by_name(self, name: str) -> BaseSkill | None:
        """
        根据名称获取 Skill

        Args:
            name: Skill 名称

        Returns:
            BaseSkill: Skill 对象
        """
        return self._skills.get(name)


# 全局单例实例
skill_registry = SkillRegistry()


def test_skill_registry() -> None:
    """
    测试 SkillRegistry 的基本功能
    
    使用方法：
    python -c "from src.skills.registry import test_skill_registry; test_skill_registry()"
    """
    print("=" * 60)
    print("测试 SkillRegistry v2.0")
    print("=" * 60)

    # 创建测试参数模型
    class TestParams(BaseModel):
        name: str = Field(..., description="测试名称")
        count: int = Field(1, description="计数")

    # 创建模拟执行函数
    async def mock_execute(self, **kwargs):
        return SkillResult(
            success=True,
            message="测试任务执行成功",
            data=kwargs,
            execution_time_ms=100
        )

    # 注册测试技能（使用自定义技能类）
    class TestSkill(BaseSkill):
        async def execute(self, **kwargs) -> SkillResult:
            return await mock_execute(self, **kwargs)

    test_skill = TestSkill(
        name="test_skill",
        description="测试技能",
        parameters=TestParams,
        handler=mock_execute,
        category="test",
        tags=["test", "demo"],
        fallback_to_rag_if_fail=True,
        enabled=True
    )
    skill_registry.register_skill(test_skill)

    print("\n[1] 测试技能注册...")
    stats = skill_registry.get_statistics()
    print(f"    OK 技能总数: {stats['total_skills']}")
    print(f"    OK 分类统计: {stats['categories']}")

    print("\n[2] 测试技能获取...")
    skill = skill_registry.get_skill("test_skill")
    if skill:
        print(f"    OK 获取成功: {skill.name}")
        print(f"    OK 描述: {skill.description}")
    else:
        print("    FAIL 获取失败")

    print("\n[3] 测试技能列表(供LLM)...")
    llm_list = skill_registry.list_skills_for_llm()
    if llm_list:
        print("    OK LLM 技能列表生成成功")
    else:
        print("    FAIL 生成失败")

    print("\n[4] 测试技能执行...")
    decision = SkillDecision(
        reasoning="测试执行",
        skill_name="test_skill",
        parameters={"name": "测试", "count": 5}
    )

    result = skill_registry.execute_sync(decision)
    if result.success:
        print("    OK 执行成功")
        print(f"    OK 消息: {result.message}")
        print(f"    OK 数据: {result.data}")
    else:
        print(f"    FAIL 执行失败: {result.error}")

    print("\n[5] 测试不存在的技能...")
    decision_invalid = SkillDecision(
        reasoning="测试不存在的技能",
        skill_name="non_existent",
        parameters={}
    )
    result_invalid = skill_registry.execute_sync(decision_invalid)
    if result_invalid.data.get("fallback_to_rag"):
        print("    OK 不存在的技能正确触发 fallback")
    else:
        print("    FAIL fallback 机制未生效")

    # 清理测试数据
    skill_registry.clear_all()

    print("\n" + "=" * 60)
    print("所有测试通过!")
    print("=" * 60)


if __name__ == "__main__":
    test_skill_registry()

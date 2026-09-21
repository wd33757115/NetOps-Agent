# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
语义路由器模块

提供两阶段路由：
1. Skill Catalog embedding 快速匹配
2. LLM Judge 精准判断

支持 Progressive Disclosure：只将匹配的 Skill 指令注入上下文。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.skill_system.trigger_match import trigger_matches

logger = logging.getLogger(__name__)


@dataclass
class SkillMatch:
    """
    Skill 匹配结果

    Attributes:
        skill_name: 匹配的 Skill 名称
        confidence: 置信度 (0-1)
        match_type: 匹配类型 (trigger, tag, semantic, llm)
        reason: 匹配原因说明
    """

    skill_name: str
    confidence: float
    match_type: str
    reason: str


class SemanticRouter:
    """
    语义路由器

    两阶段路由：
    1. 关键词/触发词快速匹配
    2. Skill Catalog embedding 语义匹配（可选）
    3. LLM Judge 精准判断（可选）
    """

    def __init__(
        self,
        skill_loader=None,
        embedding_model: str = "BAAI/bge-m3",
        use_llm_judge: bool = True,
        use_embedding: bool = True,
    ):
        """
        初始化路由器

        Args:
            skill_loader: Skill 加载器实例
            embedding_model: Embedding 模型名称
            use_llm_judge: 是否使用 LLM Judge
            use_embedding: 是否使用 embedding 匹配
        """
        self.skill_loader = skill_loader
        self.embedding_model = embedding_model
        self.use_llm_judge = use_llm_judge
        self.use_embedding = use_embedding

        self._embedding_cache: dict[str, list[float]] = {}
        self._llm = None
        self._embedder = None

    def _init_llm(self):
        """延迟初始化 LLM"""
        if self._llm is None:
            try:
                from langchain_deepseek import ChatDeepSeek

                from src.common.config import get_settings

                settings = get_settings()
                self._llm = ChatDeepSeek(
                    model=settings.LLM_MODEL,
                    temperature=0.1,
                    api_key=settings.DEEPSEEK_API_KEY,
                    request_timeout=30,
                )
            except Exception as e:
                logger.warning(f"LLM 初始化失败: {e}")
                self.use_llm_judge = False

    def route(
        self,
        query: str,
        top_k: int = 3,
        *,
        user_role: str | None = None,
        user_id: str | None = None,
    ) -> list[SkillMatch]:
        """
        路由用户查询

        Args:
            query: 用户查询
            top_k: 返回前 k 个匹配结果
            user_role: 用户角色（L2 RBAC 过滤）

        Returns:
            List[SkillMatch]: 匹配的 Skill 列表（按置信度降序）
        """
        from src.common.config import get_settings
        from src.skill_system.tiered_router import (
            catalog_allowed_skills,
            catalog_semantic_match,
            filter_matches_by_allowed,
            l2_expand_candidates,
        )

        settings = get_settings()
        allowed = catalog_allowed_skills(user_role, user_id=user_id)
        matches = []

        # L1: 触发词/关键词快速匹配
        matches.extend(self._keyword_match(query))
        matches = filter_matches_by_allowed(matches, allowed)

        has_trigger_hit = any(m.match_type == "trigger" and m.confidence >= 0.9 for m in matches)

        # L2: 域/标签扩展候选（无高置信触发词时）
        l2_candidates = (
            l2_expand_candidates(query, allowed)
            if settings.SKILL_CATALOG_USE_TIERED_ROUTING
            else allowed
        )

        # L3: Catalog 预计算语义向量（不对全量 Skill 实时 encode）
        if self.use_embedding and not has_trigger_hit:
            if settings.SKILL_CATALOG_ENABLED and settings.SKILL_CATALOG_USE_TIERED_ROUTING:
                semantic_matches = catalog_semantic_match(
                    query,
                    top_k,
                    allowed_skills=l2_candidates if l2_candidates else allowed,
                )
            else:
                semantic_matches = self._semantic_match(query, top_k)
            for match in semantic_matches:
                existing = next((m for m in matches if m.skill_name == match.skill_name), None)
                if existing:
                    existing.confidence = max(existing.confidence, match.confidence)
                    if match.confidence > existing.confidence:
                        existing.match_type = match.match_type
                        existing.reason = match.reason
                else:
                    matches.append(match)
            matches = filter_matches_by_allowed(matches, allowed)

        # L4: LLM Judge（可选）
        if self.use_llm_judge and matches:
            matches = self._llm_judge(query, matches)

        matches = [m for m in matches if self._is_skill_enabled(m.skill_name)]
        matches = filter_matches_by_allowed(matches, allowed)

        matches.sort(key=lambda x: x.confidence, reverse=True)
        hard_limit = settings.PRE_PROCESS_HARD_LIMIT
        return matches[: min(top_k, hard_limit)]

    def _is_skill_enabled(self, skill_name: str) -> bool:
        """检查 Skill 是否启用"""
        if not self.skill_loader:
            return True
        metadata = self.skill_loader.get_metadata(skill_name)
        if metadata is None:
            return False
        return getattr(metadata, "enabled", True)

    def _keyword_match(self, query: str) -> list[SkillMatch]:
        """
        关键词/触发词快速匹配

        Args:
            query: 用户查询

        Returns:
            List[SkillMatch]: 匹配结果
        """
        matches = []
        query_lower = query.lower()

        if not self.skill_loader:
            return matches

        # 获取所有 Skill 元数据
        skills = self.skill_loader.list_all_metadata()

        for skill in skills:
            # 检查 triggers
            triggers = getattr(skill, "triggers", [])
            for trigger in triggers:
                if trigger_matches(trigger, query):
                    matches.append(
                        SkillMatch(
                            skill_name=skill.name,
                            confidence=0.95,
                            match_type="trigger",
                            reason=f"匹配触发词: {trigger}",
                        )
                    )
                    break

            # 检查 tags
            if not any(m.skill_name == skill.name for m in matches):
                tags = getattr(skill, "tags", [])
                for tag in tags:
                    if tag.lower() in query_lower:
                        matches.append(
                            SkillMatch(
                                skill_name=skill.name,
                                confidence=0.7,
                                match_type="tag",
                                reason=f"匹配标签: {tag}",
                            )
                        )
                        break

            # 检查 description
            if not any(m.skill_name == skill.name for m in matches):
                desc = getattr(skill, "description", "").lower()
                if desc and desc in query_lower:
                    matches.append(
                        SkillMatch(
                            skill_name=skill.name,
                            confidence=0.6,
                            match_type="description",
                            reason="匹配描述关键词",
                        )
                    )

        return matches

    def _semantic_match(self, query: str, top_k: int) -> list[SkillMatch]:
        """
        Skill Catalog embedding 语义匹配

        Args:
            query: 用户查询
            top_k: 返回前 k 个

        Returns:
            List[SkillMatch]: 匹配结果
        """
        matches = []
        skills = self.skill_loader.list_all_metadata()

        try:
            # 计算查询 embedding
            query_embedding = self._get_embedding(query)

            similarities = []
            for skill in skills:
                # 计算 Skill 描述 embedding
                triggers = getattr(skill, "triggers", []) or []
                skill_text = f"{skill.name}: {skill.description} {' '.join(skill.tags or [])} {' '.join(triggers)}"
                skill_embedding = self._get_embedding(skill_text)

                # 计算余弦相似度
                similarity = self._cosine_similarity(query_embedding, skill_embedding)
                similarities.append((skill.name, similarity))

            import os

            semantic_min = float(os.getenv("SEMANTIC_SKILL_MIN_CONFIDENCE", "0.72"))
            similarities.sort(key=lambda x: x[1], reverse=True)
            for skill_name, similarity in similarities[:top_k]:
                if float(similarity) < semantic_min:
                    continue
                matches.append(
                    SkillMatch(
                        skill_name=skill_name,
                        confidence=float(similarity),
                        match_type="semantic",
                        reason=f"语义相似度: {similarity:.2f}",
                    )
                )

        except Exception as e:
            logger.error(f"语义匹配失败: {e}")

        return matches[:top_k]

    def _llm_judge(self, query: str, candidates: list[SkillMatch]) -> list[SkillMatch]:
        """
        LLM Judge 精准判断

        让 LLM 判断哪个 Skill 最适合处理用户请求。

        Args:
            query: 用户查询
            candidates: 候选 Skill 列表

        Returns:
            List[SkillMatch]: 精选后的匹配结果
        """
        if not candidates:
            return []

        self._init_llm()
        if not self._llm:
            return candidates

        try:
            # 获取 Skill 描述
            skill_descriptions = []
            for match in candidates:
                skill = self.skill_loader.get_metadata(match.skill_name)
                if skill:
                    skill_descriptions.append(skill.get_llm_description())

            skills_text = "\n\n".join(skill_descriptions)

            prompt = f"""你是一个专业的 Skill 选择助手。

【用户请求】
{query}

【候选 Skills】
{skills_text}

【任务】
分析用户请求，选择最合适的一个 Skill 来处理。

输出要求（只输出 JSON）：
{{
  "selected_skill": "skill_name 或 null",
  "confidence": 0.0-1.0,
  "reason": "选择原因"
}}

如果用户请求是知识性问题（如询问概念、原理、方法等），应该选择 null 走 RAG。
"""

            response = self._llm.invoke(prompt)
            content = response.content.strip()

            # 解析 JSON
            import json
            import re

            # 提取 JSON（可能包含在 ```json 中）
            json_match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
            if json_match:
                content = json_match.group(1)
            else:
                # 尝试直接解析
                json_match = re.search(r"\{.*\}", content, re.DOTALL)
                if json_match:
                    content = json_match.group(0)

            result = json.loads(content)

            selected = result.get("selected_skill")
            confidence = result.get("confidence", 0.5)
            reason = result.get("reason", "")

            if selected:
                for match in candidates:
                    if match.skill_name == selected:
                        match.confidence = confidence
                        match.reason = f"[LLM Judge] {reason}"
                        match.match_type = "llm"
                        break

                return [m for m in candidates if m.skill_name == selected]

            logger.info(
                "[LLM Judge] 建议走 RAG，保留关键词/语义候选: %s",
                [m.skill_name for m in candidates],
            )
            return candidates

        except Exception as e:
            logger.error(f"LLM Judge 失败: {e}")
            return candidates  # Fallback: 返回原始候选列表

    def _get_embedding(self, text: str) -> list[float]:
        """获取文本 embedding（优先共享 embedder）。"""
        if text in self._embedding_cache:
            return self._embedding_cache[text]
        try:
            from src.infrastructure.embedding.embedder import encode_text

            embedding = encode_text(text)
            if embedding:
                self._embedding_cache[text] = embedding
                return embedding
        except Exception as exc:
            logger.debug("shared embedder unavailable: %s", exc)

        try:
            if self._embedder is None:
                from sentence_transformers import SentenceTransformer

                self._embedder = SentenceTransformer(self.embedding_model)

            embedding = self._embedder.encode(text).tolist()
            self._embedding_cache[text] = embedding
            return embedding
        except Exception as e:
            logger.error(f"Embedding 计算失败: {e}")
            return []

    def _cosine_similarity(self, vec1: list[float], vec2: list[float]) -> float:
        """计算余弦相似度"""
        import numpy as np

        if not vec1 or not vec2:
            return 0.0

        vec1_np = np.array(vec1)
        vec2_np = np.array(vec2)

        dot_product = np.dot(vec1_np, vec2_np)
        norm1 = np.linalg.norm(vec1_np)
        norm2 = np.linalg.norm(vec2_np)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return float(dot_product / (norm1 * norm2))

    def invalidate_cache(self):
        """清空缓存"""
        self._embedding_cache.clear()


def route_query(query: str, skill_loader=None, top_k: int = 3) -> list[SkillMatch]:
    """
    便捷路由函数

    Args:
        query: 用户查询
        skill_loader: Skill 加载器
        top_k: 返回前 k 个

    Returns:
        List[SkillMatch]: 匹配结果
    """
    router = SemanticRouter(
        skill_loader=skill_loader,
    )
    return router.route(query, top_k)

# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
Skill System - Grok-style 文件驱动 Skill 系统

提供：
1. SKILL.md 解析和元数据提取
2. 语义路由（Skill Catalog embedding + LLM Judge）
3. Progressive Disclosure 加载器
4. Skill 缓存和性能优化

使用方法：
    from src.skill_system import get_skill_system

    skill_system = get_skill_system()

    # 路由
    matches = skill_system.route("生成防火墙策略")

    # 获取指令
    instructions = skill_system.get_skill_instructions("firewall-policy")
"""

from .cache import SkillCache
from .loader import SkillContent, SkillLoader
from .metadata import (
    InputSpec,
    OutputSpec,
    Reference,
    SkillMetadata,
    load_all_skill_metadata,
    parse_skill_md,
)
from .router import SemanticRouter, SkillMatch
from .trigger_match import trigger_matches

__version__ = "1.0.0"

# 导出声明
__all__ = [
    "SkillSystem",
    "get_skill_system",
    "reload_all_skills",
    "SkillMetadata",
    "InputSpec",
    "OutputSpec",
    "Reference",
    "parse_skill_md",
    "load_all_skill_metadata",
    "SemanticRouter",
    "SkillMatch",
    "SkillLoader",
    "SkillContent",
    "SkillCache",
]

# 全局单例
_skill_system = None


def _semantic_routing_enabled(cfg) -> bool:
    """Keep metadata catalog use independent from the heavy local embedder."""
    return bool(cfg.SKILL_CATALOG_ENABLED and cfg.SKILL_CATALOG_SEMANTIC_ROUTING_ENABLED)


class SkillSystem:
    """
    Skill System 主类

    整合所有组件，提供统一的 Skill 管理接口。
    """

    def __init__(self):
        self.cache = SkillCache()
        self.loader = SkillLoader(cache=self.cache)
        self.router = None  # 延迟初始化
        self._skill_dirs = []
        self._initialized = False

    def initialize(self, skill_dirs: list = None):
        """
        初始化 Skill System

        Args:
            skill_dirs: Skill 目录列表，默认为 ['src/skills']
        """
        if skill_dirs is None:
            from pathlib import Path

            base_dir = Path(__file__).parent.parent.parent
            skill_dirs = [str(base_dir / "src" / "skills")]

        self._skill_dirs = skill_dirs

        # 加载所有 Skill 元数据
        self.loader.scan_skill_dirs(skill_dirs)

        import os

        from src.common.config import get_settings

        cfg = get_settings()
        use_llm_judge = os.getenv("USE_SKILL_LLM_JUDGE", "false").lower() in (
            "1",
            "true",
            "yes",
        )
        use_embedding = _semantic_routing_enabled(cfg)
        self.router = SemanticRouter(
            skill_loader=self.loader,
            use_embedding=use_embedding,
            use_llm_judge=use_llm_judge,
            embedding_model=cfg.EMBEDDING_MODEL,
        )

        self._initialized = True

    def route(self, query: str, top_k: int = 3) -> list:
        """
        路由用户查询到合适的 Skill

        Args:
            query: 用户查询
            top_k: 返回前 k 个匹配

        Returns:
            List[SkillMatch]: 匹配的 Skill 列表
        """
        if not self._initialized:
            self.initialize()

        if self.router:
            return self.router.route(query, top_k=top_k)
        return self._keyword_route(query)[:top_k]

    def _keyword_route(self, query: str) -> list:
        """基于关键词的简单路由"""
        matches = []
        skills = self.loader.list_all_metadata()

        for skill in skills:
            if not getattr(skill, "enabled", True):
                continue

            # 检查 triggers
            triggers = getattr(skill, "triggers", [])
            for trigger in triggers:
                if trigger_matches(trigger, query):
                    matches.append(
                        SkillMatch(
                            skill_name=skill.name,
                            confidence=0.9,
                            match_type="trigger",
                            reason=f"匹配触发词: {trigger}",
                        )
                    )
                    break

            # 检查 tags
            if not matches or matches[-1].skill_name != skill.name:
                tags = getattr(skill, "tags", [])
                for tag in tags:
                    if tag.lower() in query.lower():
                        matches.append(
                            SkillMatch(
                                skill_name=skill.name,
                                confidence=0.7,
                                match_type="tag",
                                reason=f"匹配标签: {tag}",
                            )
                        )
                        break

        # 按置信度排序
        matches.sort(key=lambda x: x.confidence, reverse=True)
        return matches[:3]  # 返回前 3 个

    def get_skill_instructions(self, skill_name: str) -> str:
        """
        获取 Skill 指令（Progressive Disclosure）

        Args:
            skill_name: Skill 名称

        Returns:
            str: Skill 指令内容
        """
        if not self._initialized:
            self.initialize()

        return self.loader.get_skill_content(skill_name)

    def get_skill_metadata(self, skill_name: str) -> SkillMetadata:
        """获取 Skill 元数据"""
        if not self._initialized:
            self.initialize()

        return self.loader.get_metadata(skill_name)

    def list_all_skills(self) -> list:
        """列出所有 Skill"""
        if not self._initialized:
            self.initialize()

        return self.loader.list_all_metadata()

    def reload_skill(self, skill_name: str):
        """重新加载指定 Skill"""
        self.loader.reload_skill(skill_name)
        if self.router:
            self.router.invalidate_cache()

    def reload_all(self, skill_dirs: list | None = None):
        """重新加载所有 Skill（元数据 + 路由 + 内容缓存）"""
        dirs = skill_dirs or self._skill_dirs
        if not dirs:
            from pathlib import Path

            base_dir = Path(__file__).parent.parent.parent
            dirs = [str(base_dir / "src" / "skills")]

        self._skill_dirs = list(dirs)
        self.loader.invalidate_cache()
        self.cache.clear_all()
        self.loader.scan_skill_dirs(dirs)
        import os

        from src.common.config import get_settings

        cfg = get_settings()
        use_llm_judge = os.getenv("USE_SKILL_LLM_JUDGE", "false").lower() in (
            "1",
            "true",
            "yes",
        )
        use_embedding = _semantic_routing_enabled(cfg)
        self.router = SemanticRouter(
            skill_loader=self.loader,
            use_embedding=use_embedding,
            use_llm_judge=use_llm_judge,
            embedding_model=cfg.EMBEDDING_MODEL,
        )
        self._initialized = True


def get_skill_system() -> SkillSystem:
    """
    获取 Skill System 单例

    Returns:
        SkillSystem: Skill 系统实例
    """
    global _skill_system

    if _skill_system is None:
        _skill_system = SkillSystem()

    if not _skill_system._initialized:
        _skill_system.initialize()

    return _skill_system


# 便捷函数
def reload_all_skills(skill_dirs: list | None = None):
    """重新加载所有 Skill（委托统一 bootstrap）"""
    from src.skills.bootstrap import bootstrap_skills

    return bootstrap_skills(
        skill_dirs=skill_dirs,
        force=True,
    )

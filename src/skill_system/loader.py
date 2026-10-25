# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
Skill 加载器模块

提供 Progressive Disclosure 功能：
1. 只在需要时加载 Skill 正文
2. 缓存已加载的 Skill 内容
3. 支持热加载（无需重启）
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .metadata import SkillMetadata, parse_skill_md

logger = logging.getLogger(__name__)


@dataclass
class SkillContent:
    """Skill 完整内容"""
    metadata: SkillMetadata
    instructions: str  # 核心指令
    references: dict[str, str]  # 引用内容 {path: content}
    loaded_at: float  # 加载时间戳


class SkillLoader:
    """
    Skill 加载器

    支持：
    1. 扫描多个目录发现 Skill
    2. 按需加载 Skill 正文
    3. 缓存已加载的 Skill
    4. 热加载（重新加载指定 Skill）
    """

    def __init__(self, cache=None):
        """
        初始化加载器

        Args:
            cache: 缓存实例（可选）
        """
        self.cache = cache
        self._skill_dirs: list[Path] = []
        self._metadata_cache: dict[str, SkillMetadata] = {}
        self._content_cache: dict[str, SkillContent] = {}
        self._scan_completed = False

    def scan_skill_dirs(self, skill_dirs: list[str]):
        """
        扫描 Skill 目录

        Args:
            skill_dirs: Skill 目录列表
        """
        self._skill_dirs = [Path(d) for d in skill_dirs]
        self._metadata_cache.clear()
        self._content_cache.clear()
        if self.cache:
            self.cache.clear_all()

        print(f"[SkillLoader] 扫描 {len(skill_dirs)} 个目录...")

        for skill_dir in self._skill_dirs:
            if not skill_dir.exists():
                print(f"[WARN] Skill 目录不存在: {skill_dir}")
                continue

            # 扫描子目录
            for item in skill_dir.iterdir():
                if not item.is_dir():
                    continue

                # 跳过隐藏目录和 examples 目录
                if item.name.startswith('.') or item.name == 'examples':
                    continue

                skill_md = item / "SKILL.md"
                if not skill_md.exists():
                    continue

                try:
                    metadata = parse_skill_md(skill_md, include_instructions=False)
                    self._metadata_cache[metadata.name] = metadata
                    print(f"   [OK] {metadata.name} v{metadata.version}")
                except Exception as e:
                    print(f"   [ERROR] 加载 {item.name} 失败: {e}")

        print(f"[SkillLoader] 共加载 {len(self._metadata_cache)} 个 Skill")
        self._scan_completed = True

    def get_metadata(self, skill_name: str) -> SkillMetadata | None:
        """
        获取 Skill 元数据

        Args:
            skill_name: Skill 名称

        Returns:
            SkillMetadata: 元数据对象
        """
        if not self._scan_completed:
            self.scan_skill_dirs([str(d) for d in self._skill_dirs])

        return self._metadata_cache.get(skill_name)

    def get_skill_content(self, skill_name: str) -> str:
        """
        获取 Skill 指令内容（Progressive Disclosure）

        只加载指定的 Skill 正文，实现按需加载。

        Args:
            skill_name: Skill 名称

        Returns:
            str: Skill 指令内容
        """
        if self.cache:
            cached = self.cache.get_instructions(skill_name)
            if cached:
                return cached

        if self._content_cache.get(skill_name):
            content = self._content_cache[skill_name]
            if time.time() - content.loaded_at < 1800:
                return content.instructions

        metadata = self.get_metadata(skill_name)
        if not metadata:
            logger.warning("Skill 不存在: %s", skill_name)
            return ""

        skill_dir = Path(metadata.skill_path) if metadata.skill_path else None
        skill_md = (skill_dir / "SKILL.md") if skill_dir else None

        if not skill_md or not skill_md.exists():
            logger.error("SKILL.md 不存在: %s", skill_md)
            return (metadata.instructions or "").strip()

        try:
            from .metadata import normalize_markdown_content, parse_frontmatter, parse_skill_md

            raw = normalize_markdown_content(skill_md.read_text(encoding="utf-8"))
            _, body = parse_frontmatter(raw)
            instructions = body.strip()

            if not instructions:
                full_meta = parse_skill_md(skill_md, include_instructions=True)
                instructions = (full_meta.instructions or "").strip()

            if not instructions and len(raw.strip()) > 0:
                instructions = raw.strip()

            references = self._load_references(skill_dir, metadata) if skill_dir else {}

            self._content_cache[skill_name] = SkillContent(
                metadata=metadata,
                instructions=instructions,
                references=references,
                loaded_at=time.time(),
            )
            if self.cache and instructions:
                self.cache.set_instructions(skill_name, instructions)

            logger.info("加载 Skill 内容: %s (%s 字符)", skill_name, len(instructions))
            return instructions

        except Exception as e:
            logger.exception("加载 Skill 内容失败 %s: %s", skill_name, e)
            return (metadata.instructions or "").strip()

    def _load_references(
        self,
        skill_dir: Path,
        metadata: SkillMetadata
    ) -> dict[str, str]:
        """
        加载 Skill 引用

        Args:
            skill_dir: Skill 目录
            metadata: Skill 元数据

        Returns:
            Dict[str, str]: 引用内容 {path: content}
        """
        references = {}

        for ref in metadata.references:
            if ref.type == "file" and ref.path:
                ref_path = skill_dir / ref.path
                if ref_path.exists():
                    try:
                        references[ref.path] = ref_path.read_text(encoding='utf-8')
                    except Exception as e:
                        logger.warning(f"加载引用失败 {ref.path}: {e}")

        return references

    def list_all_metadata(self) -> list[SkillMetadata]:
        """
        列出所有 Skill 元数据

        Returns:
            List[SkillMetadata]: Skill 元数据列表
        """
        if not self._scan_completed:
            self.scan_skill_dirs([str(d) for d in self._skill_dirs])

        return list(self._metadata_cache.values())

    def reload_skill(self, skill_name: str):
        """
        重新加载指定 Skill

        Args:
            skill_name: Skill 名称
        """
        if skill_name in self._content_cache:
            del self._content_cache[skill_name]
        if self.cache:
            self.cache.instructions_cache.delete(skill_name)

        from .metadata import parse_skill_md

        metadata = self._metadata_cache.get(skill_name)
        if metadata:
            skill_md = Path(metadata.skill_path) / "SKILL.md"
            if skill_md.exists():
                try:
                    new_metadata = parse_skill_md(skill_md, include_instructions=False)
                    self._metadata_cache[skill_name] = new_metadata
                    logger.info("重新加载 Skill 元数据: %s", skill_name)
                except Exception as e:
                    logger.error("重新加载失败 %s: %s", skill_name, e)

    def invalidate_cache(self):
        """清除所有缓存"""
        self._content_cache.clear()
        if self.cache:
            self.cache.clear_all()
        logger.info("Skill 内容缓存已清除")

    def get_cache_stats(self) -> dict[str, Any]:
        """获取缓存统计"""
        return {
            "metadata_count": len(self._metadata_cache),
            "content_cache_count": len(self._content_cache),
            "scan_completed": self._scan_completed,
            "skill_dirs": [str(d) for d in self._skill_dirs]
        }


def load_skill_instructions(
    skill_dir: str,
    skill_name: str,
    use_cache: bool = True
) -> str:
    """
    便捷函数：加载 Skill 指令

    Args:
        skill_dir: Skill 根目录
        skill_name: Skill 名称
        use_cache: 是否使用缓存

    Returns:
        str: Skill 指令内容
    """
    loader = SkillLoader()
    loader.scan_skill_dirs([skill_dir])

    if not use_cache:
        loader.invalidate_cache()

    return loader.get_skill_content(skill_name)

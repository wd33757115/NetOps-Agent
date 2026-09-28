# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
Skill 统一启动入口

仅使用 SKILL.md（kebab-case 目录）作为真相源：
1. registry.sync_skills_from_files — 可执行 Skill 注册
2. SkillSystem — 元数据扫描、路由、Progressive Disclosure 指令
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.common.config import get_settings

logger = logging.getLogger(__name__)

_BOOTSTRAP_DONE = False
_LAST_COUNT = 0


def get_default_skill_dirs() -> list[str]:
    base_dir = Path(__file__).parent.parent.parent
    return [str(base_dir / "src" / "skills")]


def bootstrap_skills(
    skill_dirs: list[str] | None = None,
    *,
    force: bool = False,
    persist_catalog: bool = False,
) -> int:
    """
    统一加载 Skill（幂等，force=True 时强制全量刷新）。

    Args:
        skill_dirs: SKILL.md 根目录列表
        force: 是否强制重新扫描（热加载、管理 API reload）

    Returns:
        int: 同步的 Skill 数量
    """
    global _BOOTSTRAP_DONE, _LAST_COUNT

    if _BOOTSTRAP_DONE and not force:
        return _LAST_COUNT

    dirs = skill_dirs or get_default_skill_dirs()

    from src.skill_system import get_skill_system
    from src.skills.registry import skill_registry

    count = skill_registry.sync_skills_from_files(dirs, force_replace=force)
    skill_system = get_skill_system()
    skill_system.reload_all(skill_dirs=dirs)

    settings = get_settings()
    if settings.SKILL_CATALOG_ENABLED and persist_catalog:
        try:
            from src.skill_system.catalog import sync_and_index

            catalog_stats = sync_and_index(
                skill_system.loader.list_all_metadata(),
                index=False,
            )
            logger.info("Skill Catalog 同步完成: %s", catalog_stats)
        except Exception as exc:
            logger.error("Skill Catalog 初始化失败: %s", exc)
            raise

    _BOOTSTRAP_DONE = True
    _LAST_COUNT = count
    logger.info("Skill bootstrap 完成: %s 个 SKILL.md Skill", count)
    return count


def reset_bootstrap_state() -> None:
    """测试用：重置 bootstrap 标记。"""
    global _BOOTSTRAP_DONE, _LAST_COUNT
    _BOOTSTRAP_DONE = False
    _LAST_COUNT = 0

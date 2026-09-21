# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def load_all_skills(skill_dirs: list[str] | None = None) -> int:
    """
    统一 Skill 加载入口（SKILL.md + SkillSystem）。

    不再默认扫描 examples/ 下的遗留 Python Skill。
    """
    from src.skills.bootstrap import bootstrap_skills

    return bootstrap_skills(
        skill_dirs=skill_dirs,
        force=False,
    )


if __name__ == "__main__":
    from src.skills.bootstrap import bootstrap_skills

    print(f"bootstrap 完成: {bootstrap_skills(force=True)} 个 Skill")

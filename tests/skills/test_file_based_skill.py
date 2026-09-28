# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""FileBasedSkill 与 Pydantic 兼容性测试。"""

from pathlib import Path

import pytest

from src.skill_system.metadata import parse_skill_md
from src.skills.file_based_skill import FileBasedSkill
from src.skills.registry import skill_registry


def test_file_based_skill_has_metadata_after_init():
    skill_md = (
        Path(__file__).parent.parent.parent / "src" / "skills" / "config-diff-tool" / "SKILL.md"
    )
    metadata = parse_skill_md(skill_md, include_instructions=False)
    skill = FileBasedSkill(metadata)

    assert skill.get_skill_metadata() is not None
    assert skill.get_skill_metadata().name == "config-diff-tool"
    assert skill.name == "config-diff-tool"


def test_sync_replaces_legacy_file_based_skill_instance():
    """模拟旧版内嵌 FileBasedSkill：sync 后应替换为新模块实例。"""

    class LegacyFileBasedSkill:
        name = "config-diff-tool"
        enabled = True
        description = "legacy"
        category = "security"
        tags = []
        fallback_to_rag_if_fail = True

        async def execute(self, **kwargs):
            raise AttributeError("'LegacyFileBasedSkill' object has no attribute '_metadata'")

    skill_registry._skills["config-diff-tool"] = LegacyFileBasedSkill()  # type: ignore[assignment]

    skill_md = (
        Path(__file__).parent.parent.parent / "src" / "skills" / "config-diff-tool" / "SKILL.md"
    )
    metadata = parse_skill_md(skill_md, include_instructions=False)
    skill_registry.sync_skills_from_files(force_replace=True)

    skill = skill_registry.get_skill("config-diff-tool")
    assert skill is not None
    assert isinstance(skill, FileBasedSkill)
    assert skill.get_skill_metadata() is not None

    skill_registry._skills.pop("config-diff-tool", None)

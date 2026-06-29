# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
测试 Skill System 主类集成
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.skill_system import (
    SkillSystem,
    get_skill_system,
    reload_all_skills,
)
from src.skill_system.metadata import SkillMetadata, InputSpec, OutputSpec
from src.skill_system.router import SkillMatch


def _create_temp_skills():
    """创建临时 Skill 目录（包含多个 Skill）"""
    tmpdir = Path(tempfile.mkdtemp())

    skills = {
        "device-backup": """---
name: device-backup
version: 1.0.0
description: 设备配置备份专家
category: network
tags: [backup, device]
triggers:
  - "备份设备配置"
  - "配置备份"
inputs:
  - name: device_name
    type: string
    required: false
    description: 设备名称
outputs:
  - name: backup_files
    type: download
    description: 备份文件
enabled: true
fallback_to_rag: true
---

# 设备配置备份专家

执行网络设备的配置备份操作。
""",
        "device-patrol": """---
name: device-patrol
version: 1.0.0
description: 设备巡检专家
category: network
tags: [patrol, inspection]
triggers:
  - "执行巡检"
  - "设备巡检"
inputs:
  - name: group_name
    type: string
    required: false
    description: 分组名称
outputs:
  - name: raw_outputs_archive
    type: text
    description: 巡检报告
enabled: true
fallback_to_rag: true
---

# 设备巡检专家

执行网络设备巡检并生成报告。
""",
        "firewall-policy": """---
name: firewall-policy
version: 1.0.0
description: 防火墙策略生成专家
category: security
tags: [firewall, policy]
triggers:
  - "生成防火墙策略"
  - "防火墙策略生成"
inputs:
  - name: policy_file_url
    type: file
    required: true
    description: 策略文件
outputs:
  - name: policy_result
    type: download
    description: 策略生成结果
enabled: true
fallback_to_rag: true
---

# 防火墙策略生成专家

根据用户输入生成防火墙安全策略。
""",
    }

    for name, content in skills.items():
        d = tmpdir / name
        d.mkdir()
        (d / "SKILL.md").write_text(content, encoding="utf-8")

    return tmpdir


def test_skill_system_init():
    """测试 SkillSystem 初始化"""
    tmpdir = _create_temp_skills()

    skill_system = SkillSystem()
    skill_system.initialize(skill_dirs=[str(tmpdir)])

    assert skill_system._initialized is True
    skills = skill_system.list_all_skills()
    assert len(skills) == 3

    names = {s.name for s in skills}
    assert "device-backup" in names
    assert "device-patrol" in names
    assert "firewall-policy" in names

    print("[OK] test_skill_system_init")


def test_skill_system_route_keyword():
    """测试 SkillSystem 关键词路由"""
    tmpdir = _create_temp_skills()

    skill_system = SkillSystem()
    skill_system.initialize(skill_dirs=[str(tmpdir)])

    matches = skill_system.route("帮我备份设备配置")
    assert len(matches) > 0
    assert matches[0].skill_name == "device-backup"

    print("[OK] test_skill_system_route_keyword")


def test_skill_system_route_no_match():
    """测试 SkillSystem 无匹配路由"""
    tmpdir = _create_temp_skills()

    skill_system = SkillSystem()
    skill_system.initialize(skill_dirs=[str(tmpdir)])

    matches = skill_system.route("今天天气怎么样")
    assert len(matches) == 0

    print("[OK] test_skill_system_route_no_match")


def test_skill_system_get_instructions():
    """测试获取 Skill 指令（Progressive Disclosure）"""
    tmpdir = _create_temp_skills()

    skill_system = SkillSystem()
    skill_system.initialize(skill_dirs=[str(tmpdir)])

    instructions = skill_system.get_skill_instructions("device-backup")
    assert len(instructions) > 0
    assert "设备配置备份专家" in instructions
    assert "配置备份操作" in instructions

    print("[OK] test_skill_system_get_instructions")


def test_skill_system_get_metadata():
    """测试获取 Skill 元数据"""
    tmpdir = _create_temp_skills()

    skill_system = SkillSystem()
    skill_system.initialize(skill_dirs=[str(tmpdir)])

    metadata = skill_system.get_skill_metadata("device-backup")
    assert metadata is not None
    assert metadata.name == "device-backup"
    assert metadata.category == "network"
    assert len(metadata.triggers) == 2

    print("[OK] test_skill_system_get_metadata")


def test_skill_system_reload():
    """测试 SkillSystem 重新加载"""
    tmpdir = _create_temp_skills()

    skill_system = SkillSystem()
    skill_system.initialize(skill_dirs=[str(tmpdir)])

    skill_system.reload_skill("device-backup")
    assert skill_system._initialized is True

    print("[OK] test_skill_system_reload")


def test_get_skill_system_singleton():
    """测试 get_skill_system 单例"""
    global _skill_system
    import src.skill_system as skill_sys_module
    skill_sys_module._skill_system = None

    sys1 = get_skill_system()
    sys2 = get_skill_system()

    assert sys1 is sys2

    print("[OK] test_get_skill_system_singleton")


def test_reload_all_skills():
    """测试 reload_all_skills"""
    tmpdir = _create_temp_skills()

    skill_system = SkillSystem()
    skill_system.initialize(skill_dirs=[str(tmpdir)])

    import src.skill_system as skill_sys_module
    skill_sys_module._skill_system = skill_system

    reload_all_skills()
    assert skill_system._initialized is True

    print("[OK] test_reload_all_skills")


def test_skill_system_auto_init():
    """测试 SkillSystem 自动初始化"""
    tmpdir = _create_temp_skills()

    skill_system = SkillSystem()
    skills = skill_system.list_all_skills()

    assert isinstance(skills, list)

    print("[OK] test_skill_system_auto_init")


def test_skill_metadata_model_fields():
    """测试 SkillMetadata 模型字段完整性"""
    metadata = SkillMetadata(
        name="test-full",
        version="2.0.0",
        description="完整测试",
        category="network",
        tags=["tag1", "tag2"],
        author="Test Author",
        author_email="test@example.com",
        triggers=["trigger1", "trigger2"],
        inputs=[
            InputSpec(name="param1", type="string", required=True, description="参数1描述"),
            InputSpec(name="param2", type="int", required=False, description="参数2描述"),
        ],
        outputs=[
            OutputSpec(name="result", type="text", description="结果描述"),
        ],
        enabled=True,
        hidden=False,
        fallback_to_rag=True,
        skill_path="/path/to/skill",
        skill_md_path="/path/to/SKILL.md",
    )

    assert metadata.name == "test-full"
    assert metadata.version == "2.0.0"
    assert len(metadata.inputs) == 2
    assert metadata.inputs[0].name == "param1"
    assert metadata.inputs[0].required is True
    assert len(metadata.outputs) == 1

    d = metadata.to_dict()
    assert d["name"] == "test-full"
    assert d["version"] == "2.0.0"

    print("[OK] test_skill_metadata_model_fields")


if __name__ == "__main__":
    print("=" * 50)
    print("运行 Skill System 集成测试")
    print("=" * 50)

    test_skill_system_init()
    test_skill_system_route_keyword()
    test_skill_system_route_no_match()
    test_skill_system_get_instructions()
    test_skill_system_get_metadata()
    test_skill_system_reload()
    test_get_skill_system_singleton()
    test_reload_all_skills()
    test_skill_system_auto_init()
    test_skill_metadata_model_fields()

    print("=" * 50)
    print("所有测试通过!")
    print("=" * 50)

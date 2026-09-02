#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
测试完整的技能执行流程
"""

import sys
import asyncio
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BASE_DIR))

from src.skills.registry import skill_registry
from src.skills.loader import load_all_skills
from src.skills.skill_base import SkillDecision

print("=" * 60)
print("完整技能执行测试")
print("=" * 60)

# 加载所有技能
print("\n[1] 加载所有技能...")
load_all_skills()

stats = skill_registry.get_statistics()
print(f"    成功加载 {stats['total_skills']} 个技能")
for cat, cnt in stats['categories'].items():
    print(f"      - {cat}: {cnt} 个")

# 测试 device_info 技能
print("\n[2] 测试 device_info 技能...")
skill = skill_registry.get_skill('device_info')

if skill:
    print(f"    技能: {skill.name}")
    print(f"    描述: {skill.description}")
    print(f"    分类: {skill.category}")
    print(f"    标签: {skill.tags}")

    # 测试参数
    params = {'device_name': '核心交换机1'}
    print(f"\n    测试参数: {params}")

    # 执行技能
    print("    执行技能...")
    result = asyncio.run(skill.execute(**params))

    if result.success:
        print(f"    [OK] 执行成功!")
        print(f"    消息: {result.message}")
        if result.data:
            print(f"    返回数据:")
            if 'devices' in result.data:
                for device in result.data['devices']:
                    print(f"      - {device['name']} ({device['ip_address']}): {device['status']}")
                    print(f"        型号: {device['model']}, 分组: {device['group']}")
                    print(f"        CPU: {device['cpu_usage']}, 内存: {device['memory_usage']}")
    else:
        print(f"    [ERROR] 执行失败: {result.message}")
        print(f"    错误: {result.error}")
else:
    print("    找不到 device_info 技能")

print("\n" + "=" * 60)
print("测试完成!")
print("=" * 60)

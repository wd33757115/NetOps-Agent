
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
测试 Skill 系统加载
"""
import sys
from pathlib import Path

# 添加项目根目录到 PATH
root_dir = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root_dir))

try:
    from src.skill_system import get_skill_system
    
    print("=" * 60)
    print("测试 Skill 系统")
    print("=" * 60)
    
    # 初始化 Skill 系统
    print("\n1. 初始化 Skill 系统...")
    skill_system = get_skill_system()
    skill_system.initialize()
    print("   ✓ Skill 系统初始化成功")
    
    # 获取所有 Skill
    print("\n2. 获取所有 Skill...")
    skills = skill_system.list_all_skills()
    print(f"   ✓ 找到 {len(skills)} 个 Skill")
    
    print("\n3. Skill 列表:")
    for skill in skills:
        print(f"   - {skill.name}: {skill.description}")
    
    # 测试获取特定 Skill
    print("\n4. 检查示例 Skill...")
    test_skills = [
        'firewall-policy-generator',
        'device-backup',
        'device-patrol',
        'network-topology-analyzer',
        'config-diff-tool',
        'log-analyzer'
    ]
    
    for skill_name in test_skills:
        try:
            skill = skill_system.get_skill(skill_name)
            if skill:
                print(f"   ✓ {skill_name} 加载成功")
            else:
                print(f"   ✗ {skill_name} 未找到")
        except Exception as e:
            print(f"   ✗ {skill_name} 加载失败: {e}")
    
    # 测试获取指令
    print("\n5. 测试获取 Skill 指令...")
    try:
        first_skill = skills[0] if skills else None
        if first_skill:
            instructions = skill_system.get_skill_instructions(first_skill.name)
            if instructions:
                print(f"   ✓ 成功获取 {first_skill.name} 的指令")
                print(f"   指令长度: {len(instructions)} 字符")
    except Exception as e:
        print(f"   ✗ 获取指令失败: {e}")
    
    print("\n" + "=" * 60)
    print("✓ 所有测试完成")
    print("=" * 60)
    
except Exception as e:
    print(f"\n✗ 错误: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

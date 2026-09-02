# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""测试 Skill System 核心功能"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path

# 添加项目根目录到 PATH
root_dir = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root_dir))

print("=" * 60)
print("测试 Skill System 核心功能")
print("=" * 60)

# 测试 0: 初始化 Skill Registry 并扫描 Skill
print("\n[0] 初始化 Skill Registry 并扫描 Skill...")
try:
    from src.skills.registry import skill_registry
    
    # 扫描 Skill
    discovered_count = skill_registry.discover_skills_from_files()
    print(f"   ✅ 发现并注册了 {discovered_count} 个文件驱动 Skill")
    
    # 同时加载旧的 Python Skill（保持兼容）
    from src.skills.loader import load_all_skills
    load_all_skills()
    print(f"   ✅ 旧的 Python Skill 也已加载")
    
except Exception as e:
    print(f"   ❌ Skill 初始化失败: {e}")
    import traceback
    traceback.print_exc()

# 测试 1: 元数据解析
print("\n[1] 测试元数据解析...")
try:
    from src.skill_system.metadata import parse_skill_md, SkillMetadata
    from pathlib import Path

    # 测试解析现有的 SKILL.md
    skill_md_path = root_dir / "src/skills/official-document-writing/SKILL.md"
    if skill_md_path.exists():
        metadata = parse_skill_md(skill_md_path)
        print(f"   ✅ 解析成功!")
        print(f"   - name: {metadata.name}")
        print(f"   - version: {metadata.version}")
        print(f"   - description: {metadata.description[:50]}...")
        print(f"   - category: {metadata.category}")
        print(f"   - tags: {metadata.tags}")
        print(f"   - triggers: {metadata.triggers[:3]}...")
        print(f"   - instructions length: {len(metadata.instructions)} chars")
    else:
        print(f"   ⚠️  SKILL.md 不存在，跳过")

except Exception as e:
    print(f"   ❌ 解析失败: {e}")
    import traceback
    traceback.print_exc()

# 测试 2: Skill Loader
print("\n[2] 测试 Skill Loader...")
try:
    from src.skill_system.loader import SkillLoader

    loader = SkillLoader()

    # 扫描目录（包含新创建的 Skill）
    skill_dirs = [root_dir / "src/skills"]
    loader.scan_skill_dirs(skill_dirs)

    # 获取元数据
    skills = loader.list_all_metadata()
    print(f"   [OK] 加载成功! 共 {len(skills)} 个 Skill")

    for skill in skills:
        print(f"   - {skill.name} v{skill.version}")

    # 获取指令内容
    if skills:
        instructions = loader.get_skill_content(skills[0].name)
        print(f"   [OK] 指令内容加载成功! ({len(instructions)} chars)")
        print(f"   预览: {instructions[:100]}...")

except Exception as e:
    print(f"   [ERROR] 加载失败: {e}")
    import traceback
    traceback.print_exc()

# 测试 3: 路由
print("\n[3] 测试 Semantic Router...")
try:
    from src.skill_system.router import SemanticRouter, SkillMatch
    from src.skill_system.loader import SkillLoader

    loader = SkillLoader()
    loader.scan_skill_dirs([root_dir / "src/skills"])

    router = SemanticRouter(skill_loader=loader, use_llm_judge=False)

    # 测试路由
    query = "帮我分析网络拓扑"
    matches = router.route(query)

    print(f"   [OK] 路由成功!")
    print(f"   查询: {query}")
    print(f"   匹配结果:")

    for match in matches:
        print(f"   - {match.skill_name} (置信度: {match.confidence:.2f})")
        print(f"     类型: {match.match_type}")
        print(f"     原因: {match.reason}")

except Exception as e:
    print(f"   [ERROR] 路由失败: {e}")
    import traceback
    traceback.print_exc()

# 测试 4: Skill System 集成
print("\n[4] 测试 Skill System 集成...")
try:
    from src.skill_system import get_skill_system

    skill_system = get_skill_system()

    # 列出所有 Skill
    skills = skill_system.list_all_skills()
    print(f"   [OK] Skill System 初始化成功!")
    print(f"   - 共加载 {len(skills)} 个 Skill")

    # 测试路由
    query = "帮我分析网络拓扑结构"
    matches = skill_system.route(query)
    print(f"   - 路由查询: {query}")
    print(f"   - 匹配结果: {len(matches)} 个")

    for match in matches:
        print(f"     * {match.skill_name} ({match.confidence:.2f})")

    # 测试获取指令
    if matches:
        instructions = skill_system.get_skill_instructions(matches[0].skill_name)
        print(f"   [OK] 获取指令成功! ({len(instructions)} chars)")

except Exception as e:
    print(f"   [ERROR] Skill System 测试失败: {e}")
    import traceback
    traceback.print_exc()

# 测试 5: 缓存
print("\n[5] 测试缓存...")
try:
    from src.skill_system.cache import SkillCache, LRUCache

    cache = SkillCache()

    # 测试 LRU 缓存
    lru = LRUCache(max_size=3, ttl_seconds=60)

    lru.set("key1", "value1")
    lru.set("key2", "value2")
    lru.set("key3", "value3")

    print(f"   ✅ LRU 缓存基本操作成功!")
    print(f"   - 获取 key1: {lru.get('key1')}")
    print(f"   - 获取 key2: {lru.get('key2')}")

    # 测试 SkillCache
    cache.set_metadata("skill1", {"name": "test"})
    metadata = cache.get_metadata("skill1")
    print(f"   ✅ SkillCache 基本操作成功!")
    print(f"   - 获取元数据: {metadata}")

    stats = cache.get_stats()
    print(f"   缓存统计: {stats}")

except Exception as e:
    print(f"   ❌ 缓存测试失败: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("测试完成!")
print("=" * 60)

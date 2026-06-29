# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
E2E 集成测试：Skill 触发 → 路由 → 加载 → 降级 → 指标
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.skill_system import SkillSystem
from src.skill_system.router import SemanticRouter
from src.skill_system.loader import SkillLoader
from src.skill_system.metadata import SkillMetadata
from src.common.metrics import get_metrics_collector, Metrics


def _create_e2e_skills():
    """创建模拟的 E2E Skill 目录"""
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
  - "保存配置"
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

## 核心原则

1. **参数验证**：执行前必须验证至少提供了一个过滤条件
2. **幂等性**：重复执行相同备份请求不会产生副作用

## 核心能力

1. 按时备份：按计划执行配置备份
2. 批量备份：支持同时备份多台设备
3. 结果报告：提供备份状态统计

## 工作流程

1. 参数确认
2. 任务提交
3. 结果处理
4. 报告输出

## 输出格式

```json
{"success": true, "message": "备份完成", "data": {}}
```

## 示例

**输入**："帮我备份生产环境的所有设备"
**输出**：备份文件下载链接

## 注意事项

- 备份前确认设备在线
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
enabled: false
fallback_to_rag: true
---

# 设备巡检专家

执行网络设备巡检并生成报告。

## 核心能力

1. 状态检查：CPU/内存/接口
2. 配置审计：合规检查
3. 报告生成：结构化报告

## 工作流程

1. 参数确认
2. 执行巡检
3. 生成报告
4. 反馈结果

## 输出格式

```json
{"success": true, "message": "巡检完成", "data": {}}
```

## 示例

**输入**："帮我巡检生产环境设备"
**输出**：巡检报告

## 注意事项

- 巡检仅执行只读命令
""",
        "corrupt-skill": """---
name: corrupt-skill
version: 1.0.0
description: 损坏的 Skill 文件
category: general
triggers:
  - "broken"
enabled: true
---

此文件格式不规范，缺少必填章节

""",
    }

    for name, content in skills.items():
        d = tmpdir / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(content, encoding="utf-8")

    return tmpdir


def test_e2e_skill_trigger_to_route():
    """E2E: 用户输入 → 触发词匹配 → 路由到正确 Skill"""
    tmpdir = _create_e2e_skills()

    skill_system = SkillSystem()
    skill_system.initialize(skill_dirs=[str(tmpdir)])

    matches = skill_system.route("帮我备份设备配置")
    assert len(matches) > 0
    assert matches[0].skill_name == "device-backup"
    assert matches[0].confidence >= 0.5

    print("[OK] test_e2e_skill_trigger_to_route")


def test_e2e_progressive_disclosure_load():
    """E2E: 路由到 Skill → 按需加载指令内容"""
    tmpdir = _create_e2e_skills()

    skill_system = SkillSystem()
    skill_system.initialize(skill_dirs=[str(tmpdir)])

    instructions = skill_system.get_skill_instructions("device-backup")
    assert len(instructions) > 50
    assert "设备配置备份专家" in instructions
    assert "## 核心原则" in instructions
    assert "## 工作流程" in instructions

    print("[OK] test_e2e_progressive_disclosure_load")


def test_e2e_disabled_skill_excluded():
    """E2E: 禁用的 Skill 不出现在路由结果中"""
    tmpdir = _create_e2e_skills()

    skill_system = SkillSystem()
    skill_system.initialize(skill_dirs=[str(tmpdir)])

    matches = skill_system.route("执行巡检")
    assert len(matches) == 0

    print("[OK] test_e2e_disabled_skill_excluded")


def test_e2e_fallback_when_no_match():
    """E2E: 无匹配时系统降级到 RAG"""
    tmpdir = _create_e2e_skills()

    skill_system = SkillSystem()
    skill_system.initialize(skill_dirs=[str(tmpdir)])

    matches = skill_system.route("今天天气怎么样")
    assert len(matches) == 0

    print("[OK] test_e2e_fallback_when_no_match")


def test_e2e_corrupt_skill_does_not_block():
    """E2E: 损坏的 Skill 不阻塞其他 Skill 加载"""
    tmpdir = _create_e2e_skills()

    skill_system = SkillSystem()
    skill_system.initialize(skill_dirs=[str(tmpdir)])

    all_skills = skill_system.list_all_skills()
    assert len(all_skills) >= 2

    instructions = skill_system.get_skill_instructions("device-backup")
    assert len(instructions) > 50

    content = skill_system.get_skill_instructions("corrupt-skill")
    assert isinstance(content, str)

    print("[OK] test_e2e_corrupt_skill_does_not_block")


def test_e2e_skill_reload_consistency():
    """E2E: Skill 热加载后数据一致"""
    tmpdir = _create_e2e_skills()

    skill_system = SkillSystem()
    skill_system.initialize(skill_dirs=[str(tmpdir)])

    before = skill_system.get_skill_instructions("device-backup")
    skill_system.reload_skill("device-backup")
    after = skill_system.get_skill_instructions("device-backup")

    assert before == after

    skill_system.reload_all()
    after_all = skill_system.get_skill_instructions("device-backup")
    assert before == after_all

    print("[OK] test_e2e_skill_reload_consistency")


def test_e2e_metrics_after_routing():
    """E2E: 路由后指标计数器正常工作（模拟 graph.py 行为）"""
    from src.common.metrics import increment_counter, observe_histogram, get_metrics_collector

    collector = get_metrics_collector()

    tmpdir = _create_e2e_skills()
    skill_system = SkillSystem()
    skill_system.initialize(skill_dirs=[str(tmpdir)])

    matches = skill_system.route("备份设备配置")
    t_start = time.time()

    if matches:
        increment_counter("skill_routing_total", tags={"result": "skill_hit"})
    else:
        increment_counter("skill_routing_total", tags={"result": "rag_fallback"})

    observe_histogram("skill_routing_duration_ms", (time.time() - t_start) * 1000)

    summary = collector.get_summary()
    assert "skill_routing_total" in str(summary["counters"].keys())
    assert "skill_routing_duration_ms" in str(summary["histograms"].keys())

    print("[OK] test_e2e_metrics_after_routing")


def test_e2e_security_permission_check():
    """E2E: 权限检查不崩溃（安全降级）"""
    from src.skill_system.security import get_security_manager

    mgr = get_security_manager()
    result = mgr.check_permission("device-backup", "USER")
    assert isinstance(result, bool)

    result_admin = mgr.check_permission("device-backup", "ADMIN")
    assert isinstance(result_admin, bool)

    try:
        mgr.check_permission("non-existent-skill", "GUEST")
    except Exception as e:
        assert False, f"权限检查不应崩溃: {e}"

    print("[OK] test_e2e_security_permission_check")


def test_e2e_loader_error_resilience():
    """E2E: Loader 异常不中断其他 Skill"""
    tmpdir = _create_e2e_skills()

    loader = SkillLoader()
    loader.scan_skill_dirs([str(tmpdir)])

    metadata = loader.get_metadata("corrupt-skill")
    assert metadata is not None

    content = loader.get_skill_content("corrupt-skill")
    assert isinstance(content, str)
    assert len(content) > 0

    metadata_normal = loader.get_metadata("device-backup")
    assert metadata_normal is not None
    assert metadata_normal.name == "device-backup"

    content_normal = loader.get_skill_content("device-backup")
    assert len(content_normal) > 50

    print("[OK] test_e2e_loader_error_resilience")


def test_e2e_skill_content_has_required_sections():
    """E2E: 标准化的 Skill 具备所有必填章节"""
    tmpdir = _create_e2e_skills()

    skill_system = SkillSystem()
    skill_system.initialize(skill_dirs=[str(tmpdir)])

    instructions = skill_system.get_skill_instructions("device-backup")

    required = ["#", "## 核心原则", "## 核心能力", "## 工作流程", "## 输出格式", "## 示例", "## 注意事项"]
    for section in required:
        assert section in instructions, f"Missing required section: {section}"

    print("[OK] test_e2e_skill_content_has_required_sections")


if __name__ == "__main__":
    print("=" * 60)
    print("  E2E 集成测试: Skill → 路由 → 加载 → 降级 → 指标")
    print("=" * 60)

    test_e2e_skill_trigger_to_route()
    test_e2e_progressive_disclosure_load()
    test_e2e_disabled_skill_excluded()
    test_e2e_fallback_when_no_match()
    test_e2e_corrupt_skill_does_not_block()
    test_e2e_skill_reload_consistency()
    test_e2e_metrics_after_routing()
    test_e2e_security_permission_check()
    test_e2e_loader_error_resilience()
    test_e2e_skill_content_has_required_sections()

    print("=" * 60)
    print("  全部 10 项 E2E 测试通过!")
    print("=" * 60)

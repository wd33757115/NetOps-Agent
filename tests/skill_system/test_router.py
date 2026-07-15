# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
测试 Skill 语义路由器
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.skill_system.loader import SkillLoader
from src.skill_system.metadata import SkillMetadata
from src.skill_system.router import SemanticRouter, SkillMatch, route_query


def _create_mock_loader():
    """创建带模拟数据的 SkillLoader"""
    loader = SkillLoader()
    loader._skill_dirs = []

    skills = [
        SkillMetadata(
            name="device-backup",
            version="1.0.0",
            description="设备配置备份专家",
            category="network",
            tags=["backup", "device"],
            triggers=["备份设备配置", "配置备份", "保存配置"],
        ),
        SkillMetadata(
            name="device-patrol",
            version="1.0.0",
            description="设备巡检专家",
            category="network",
            tags=["patrol", "inspection"],
            triggers=["执行巡检", "设备巡检"],
        ),
        SkillMetadata(
            name="firewall-policy-generator",
            version="1.0.0",
            description="防火墙策略生成专家",
            category="security",
            tags=["firewall", "policy"],
            triggers=["生成防火墙策略", "防火墙策略生成"],
        ),
        SkillMetadata(
            name="config-diff-tool",
            version="1.0.0",
            description="配置对比工具",
            category="network",
            tags=["config", "diff"],
            triggers=["配置对比", "配置差异"],
        ),
        SkillMetadata(
            name="log-analyzer",
            version="1.0.0",
            description="日志分析专家",
            category="network",
            tags=["log", "analysis"],
            triggers=["分析日志", "日志分析"],
        ),
    ]

    for skill in skills:
        loader._metadata_cache[skill.name] = skill

    loader._scan_completed = True
    return loader


def test_route_skips_embedding_when_trigger_hit(monkeypatch):
    """触发词已命中时不应调用语义 Embedding（避免加载 BGE）。"""
    loader = _create_mock_loader()
    router = SemanticRouter(skill_loader=loader, use_embedding=True, use_llm_judge=False)

    called = {"semantic": False}
    original = router._semantic_match

    def _spy_semantic(query, top_k):
        called["semantic"] = True
        return original(query, top_k)

    monkeypatch.setattr(router, "_semantic_match", _spy_semantic)
    matches = router.route("生成防火墙策略，工单号：rg001", top_k=3)
    assert any(m.skill_name == "firewall-policy-generator" for m in matches)
    assert called["semantic"] is False


def test_keyword_match_trigger():
    """测试触发词匹配"""
    loader = _create_mock_loader()
    router = SemanticRouter(skill_loader=loader, use_embedding=False, use_llm_judge=False)

    matches = router._keyword_match("帮我备份设备配置")
    assert len(matches) > 0
    assert matches[0].skill_name == "device-backup"
    assert matches[0].match_type == "trigger"
    assert matches[0].confidence == 0.95
    print("[OK] test_keyword_match_trigger")


def test_keyword_match_tag():
    """测试标签匹配"""
    loader = _create_mock_loader()
    router = SemanticRouter(skill_loader=loader, use_embedding=False, use_llm_judge=False)

    matches = router._keyword_match("我需要device相关的功能")
    assert len(matches) > 0
    found = any(m.skill_name == "device-backup" for m in matches)
    assert found
    print("[OK] test_keyword_match_tag")


def test_keyword_match_no_match():
    """测试无匹配的情况"""
    loader = _create_mock_loader()
    router = SemanticRouter(skill_loader=loader, use_embedding=False, use_llm_judge=False)

    matches = router._keyword_match("今天天气怎么样")
    assert len(matches) == 0
    print("[OK] test_keyword_match_no_match")


def test_route_without_llm():
    """测试不使用 LLM Judge 的路由"""
    loader = _create_mock_loader()
    router = SemanticRouter(skill_loader=loader, use_embedding=False, use_llm_judge=False)

    matches = router.route("执行巡检", top_k=5)
    assert len(matches) > 0
    assert matches[0].skill_name == "device-patrol"
    print("[OK] test_route_without_llm")


def test_route_multiple_candidates():
    """测试多候选路由"""
    loader = _create_mock_loader()
    router = SemanticRouter(skill_loader=loader, use_embedding=False, use_llm_judge=False)

    matches = router.route("设备巡检和备份", top_k=3)
    assert len(matches) >= 1
    assert matches[0].confidence >= matches[-1].confidence
    print("[OK] test_route_multiple_candidates")


def test_skill_match_dataclass():
    """测试 SkillMatch 数据类"""
    match = SkillMatch(
        skill_name="test-skill", confidence=0.95, match_type="trigger", reason="匹配触发词: 测试"
    )
    assert match.skill_name == "test-skill"
    assert match.confidence == 0.95
    assert match.match_type == "trigger"
    print("[OK] test_skill_match_dataclass")


def test_invalidate_cache():
    """测试缓存失效"""
    loader = _create_mock_loader()
    router = SemanticRouter(skill_loader=loader, use_embedding=False, use_llm_judge=False)

    router._embedding_cache["test"] = [0.1, 0.2, 0.3]
    assert len(router._embedding_cache) == 1

    router.invalidate_cache()
    assert len(router._embedding_cache) == 0
    print("[OK] test_invalidate_cache")


@pytest.mark.skipif(
    os.getenv("RUN_EMBEDDING_TESTS", "").strip().lower() not in {"1", "true", "yes"},
    reason="requires the optional sentence-transformers model runtime",
)
def test_semantic_match_with_embedding():
    """测试语义匹配（带 embedding）"""
    loader = _create_mock_loader()
    router = SemanticRouter(
        skill_loader=loader, use_embedding=True, use_llm_judge=False, embedding_model="BAAI/bge-m3"
    )

    try:
        matches = router._semantic_match("备份配置", top_k=3)
        assert isinstance(matches, list)
        # 即使 embedding 失败也不应崩溃
        print("[OK] test_semantic_match_with_embedding")
    except ImportError:
        print("[SKIP] test_semantic_match_with_embedding (sentence_transformers not available)")


def test_empty_loader():
    """测试空加载器"""
    loader = SkillLoader()
    router = SemanticRouter(skill_loader=loader, use_embedding=False, use_llm_judge=False)

    matches = router.route("随便什么请求")
    assert isinstance(matches, list)
    print("[OK] test_empty_loader")


def test_route_query_convenience():
    """测试便捷路由函数"""
    loader = _create_mock_loader()
    matches = route_query("备份设备配置", skill_loader=loader, top_k=3)
    assert len(matches) > 0
    assert matches[0].skill_name == "device-backup"
    print("[OK] test_route_query_convenience")


if __name__ == "__main__":
    print("=" * 50)
    print("运行 Skill 语义路由器测试")
    print("=" * 50)

    test_keyword_match_trigger()
    test_keyword_match_tag()
    test_keyword_match_no_match()
    test_route_without_llm()
    test_route_multiple_candidates()
    test_skill_match_dataclass()
    test_invalidate_cache()
    test_semantic_match_with_embedding()
    test_empty_loader()
    test_route_query_convenience()

    print("=" * 50)
    print("所有测试通过!")
    print("=" * 50)

# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""公文写作 Skill 的直接执行集成测试。

该模块过去是一个手工脚本：pytest 收集模块时就会调用 ``asyncio.run``，
并且仍依赖已移除的 ``src.skills.examples`` 注册入口。现在覆盖实际的
SKILL.md -> FileBasedSkill -> 同步 handler 调用链，同时隔离 LLM、MinIO 等
外部依赖，保证其可以作为正常的测试收集与执行。
"""

import asyncio

from src.skills.registry import skill_registry


def test_official_document_skill_executes_current_sync_handler(monkeypatch):
    """文件化 Skill 应调用当前公文 handler，而不是已废弃 examples 注册器。"""
    captured: dict[str, object] = {}

    async def fake_handler(params: dict[str, object]) -> dict[str, object]:
        captured.update(params)
        return {
            "success": True,
            "message": "公文撰写完成",
            "data": {"document_content": "测试公文内容"},
        }

    monkeypatch.setattr(
        "src.skills.official_document.service.official_document_writing_handler",
        fake_handler,
    )
    skill_registry.sync_skills_from_files(force_replace=True)
    skill = skill_registry.get_skill("official-document-writing")

    assert skill is not None
    result = asyncio.run(
        skill.execute(
            document_type="请示",
            action="write",
            user_query="向信息中心申请采购新的服务器",
        )
    )

    assert result.success is True
    assert result.message == "公文撰写完成"
    assert result.data == {"document_content": "测试公文内容"}
    assert captured == {
        "document_type": "请示",
        "action": "write",
        "user_query": "向信息中心申请采购新的服务器",
    }

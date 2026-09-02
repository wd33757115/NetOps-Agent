#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
测试 LLM 决策过程
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BASE_DIR))

from src.skills.registry import skill_registry
from src.skills.loader import load_all_skills
from src.skills.skill_base import SkillDecision
from langchain_deepseek import ChatDeepSeek

# 加载技能
print("加载技能...")
load_all_skills()

# 构建 LLM
llm = ChatDeepSeek(
    model_name='deepseek-chat',
    temperature=0.1,
    max_tokens=2048,
    request_timeout=15
)
llm_with_structured_output = llm.with_structured_output(SkillDecision, method='function_calling')

# 测试查询
query = '帮我写一份会议纪要，记录上周的信息化建设推进会'
print(f"\n测试查询: {query}")

# 构建 prompt
skills_list = skill_registry.list_skills_for_llm()

prompt = f"""你是一个专业的运维助手，请理解用户的意图并选择合适的技能执行。

【用户请求】
{query}

【重要规则（必须遵循）】
1. 如果用户提到"防火墙"、"策略"、"生成"等关键词，必须优先尝试选择对应的技能！
2. 只有当确定是纯知识性问题才走 RAG

【可用技能】
{skills_list}

【输出要求】
请输出结构化的决策结果，包括：
- reasoning: 你的思考过程
- skill_name: 选择的技能名称或 null
- parameters: 提取的参数字典
- fallback_to_rag: 是否走 RAG
"""

# 调用 LLM
print("\n调用 LLM...")
try:
    decision = llm_with_structured_output.invoke(prompt)
    print(f'LLM 决策结果:')
    print(f'  reasoning: {decision.reasoning}')
    print(f'  skill_name: {decision.skill_name}')
    print(f'  parameters: {decision.parameters}')
    print(f'  fallback_to_rag: {decision.fallback_to_rag}')
except Exception as e:
    print(f'LLM 调用失败: {e}')

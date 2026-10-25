# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""自然语言 → CHAT.intent 规则草稿（LLM + 规则兜底）。"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from src.core.workflows.dsl import ChatIntentDSL, ChatIntentMatchDSL

logger = logging.getLogger(__name__)

_STOPWORDS = frozenset(
    "的 了 和 与 或 请 帮 我 你 要 想 进行 处理 一下 一个 这个 那个 根据 工单".split()
)


class ChatIntentSuggestModel(BaseModel):
    """LLM 结构化输出。"""

    description: str = Field(default="", description="触发场景说明")
    require_any: list[str] = Field(default_factory=list, description="主关键词")
    require_any_secondary: list[str] = Field(default_factory=list, description="次要关键词")
    priority: int = Field(default=50, ge=0, le=200)
    response_template: str = Field(default="")


def _heuristic_suggest(description: str, workflow_name: str) -> ChatIntentSuggestModel:
    """无 LLM 时的规则兜底：从描述中提取候选关键词。"""
    text = (description or "").strip()
    tokens: list[str] = []

    for m in re.finditer(r"[\u4e00-\u9fff]{2,8}", text):
        word = m.group()
        if word not in _STOPWORDS:
            tokens.append(word)

    for m in re.finditer(r"\b[A-Za-z][A-Za-z0-9_-]{2,}\b", text):
        tokens.append(m.group())

    seen: set[str] = set()
    primary: list[str] = []
    for t in tokens:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            primary.append(t)
        if len(primary) >= 6:
            break

    if not primary and text:
        primary = [text[:12]]

    return ChatIntentSuggestModel(
        description=text or f"触发 Workflow {workflow_name}",
        require_any=primary[:4] or ["关键词"],
        require_any_secondary=primary[4:6],
        priority=50,
    )


def _llm_suggest(description: str, workflow_name: str) -> ChatIntentSuggestModel | None:
    try:
        from langchain_deepseek import ChatDeepSeek

        from src.common.config import get_settings

        settings = get_settings()
        if not settings.DEEPSEEK_API_KEY:
            return None

        llm = ChatDeepSeek(
            model=settings.DEEPSEEK_MODEL or "deepseek-chat",
            api_key=settings.DEEPSEEK_API_KEY,
            temperature=0.2,
        )
        structured = llm.with_structured_output(ChatIntentSuggestModel, method="function_calling")
        prompt = (
            f"用户希望为 Workflow 插件「{workflow_name}」配置聊天触发规则。\n"
            f"场景描述：{description}\n\n"
            "请输出 CHAT.intent 匹配关键词：require_any 为主触发词（2～5 个），"
            "require_any_secondary 为可选次要词；description 为简短说明；priority 默认 50。"
        )
        return structured.invoke(prompt)
    except Exception as exc:
        logger.warning("LLM 生成 Chat Intent 失败，使用规则兜底: %s", exc)
        return None


def suggest_chat_intent_from_nl(
    description: str,
    workflow_name: str,
    *,
    use_llm: bool = True,
) -> dict[str, Any]:
    """自然语言描述 → ChatIntentDSL 草稿 + YAML 片段。"""
    model = _heuristic_suggest(description, workflow_name)
    source = "heuristic"
    if use_llm:
        llm_model = _llm_suggest(description, workflow_name)
        if llm_model is not None:
            model = llm_model
            source = "llm"

    chat_kwargs: dict[str, Any] = {
        "enabled": True,
        "priority": model.priority,
        "description": model.description,
        "match": ChatIntentMatchDSL(
            require_any=model.require_any,
            require_any_secondary=model.require_any_secondary,
        ),
    }
    if model.response_template:
        chat_kwargs["response_template"] = model.response_template
    chat = ChatIntentDSL(**chat_kwargs)

    import yaml

    yaml_dict = {
        "workflow": workflow_name,
        "priority": chat.priority,
        "description": chat.description,
        "match": {
            "require_any": chat.match.require_any,
        },
    }
    if chat.match.require_any_secondary:
        yaml_dict["match"]["require_any_secondary"] = chat.match.require_any_secondary
    if chat.response_template:
        yaml_dict["response_template"] = chat.response_template

    return {
        "success": True,
        "source": source,
        "chat_intent": chat.model_dump(),
        "chat_intent_yaml": yaml.dump(yaml_dict, allow_unicode=True, sort_keys=False),
        "tips": [
            "仅当 Workflow 声明 required_context 时，聊天请求才需要提供对应信息",
            "保存前请在「匹配预览」中验证",
        ],
    }

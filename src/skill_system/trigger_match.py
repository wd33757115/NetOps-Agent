# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Skill 触发词匹配（支持「写一份请示」匹配「写请示」等口语变体）。"""

from __future__ import annotations

import re

# 「写」与文种之间允许插入的助词/数量词/标点
_WRITE_GAP = (
    r"[\s，。、！？,.;\-()（）]*"
    r"(?:帮我|请|麻烦)?"
    r"[\s，。、！？,.;\-()（）]*"
    r"(?:一|两|几|某)?"
    r"(?:份|个|条|则|篇)?"
    r"[\s，。、！？,.;\-()（）]*"
)


def _compact(text: str) -> str:
    """去掉空白并小写，用于忽略「生成 TextFSM」vs「生成TEXTFSM」差异。"""
    return re.sub(r"\s+", "", (text or "").lower())


def trigger_matches(trigger: str, query: str) -> bool:
    """
    判断触发词是否命中用户话術。

    - 精确子串：「公文写作」 in query
    - 宽松匹配：触发词以「写」开头时，「写请示」可匹配「写一份请示」「帮我写请示」
    """
    trigger = (trigger or "").strip()
    query = (query or "").strip()
    if not trigger or not query:
        return False

    tl, ql = trigger.lower(), query.lower()
    if tl in ql:
        return True

    ct, cq = _compact(trigger), _compact(query)
    if ct and ct in cq:
        return True

    if trigger.startswith("写") and len(trigger) > 1:
        tail = trigger[1:]
        pattern = r"写" + _WRITE_GAP + re.escape(tail)
        if re.search(pattern, ql, re.IGNORECASE):
            return True

    return False

# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""从用户自然语言中提取工单号等通用工具。"""

from __future__ import annotations

import re


def extract_ticket_id(query: str) -> str | None:
    """
    从用户话术中提取工单号。

    支持：
    - 工单号：test001 / 工单号 test001 / 工单号test001
    - ticket_id: test001 / ticket id test001
    """
    if not query:
        return None

    patterns = [
        r"工单号[：:\s]*([A-Za-z0-9_-]+)",
        r"根据工单\s*([A-Za-z0-9_-]+)",
        r"工单\s*[Ii[Dd][：:\s]*([A-Za-z0-9_-]+)",
        r"工单\s*([A-Za-z0-9_-]+)",
        r"ticket[_\s-]?id[：:\s]*([A-Za-z0-9_-]+)",
        r"\b((?:REQ|CHG|INC|TK|WO)[-_]?\d+)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            return match.group(1).strip("，。,. ")
    return None

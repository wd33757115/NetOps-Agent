# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 模板（兼容层，实际定义在 src/workflows/**/WORKFLOW.yaml）。"""

from __future__ import annotations

from src.core.workflows.registry import TEMPLATES, get_template, load_workflows

# 向后兼容
ITSM_FIREWALL_CHANGE = get_template("itsm-firewall-change")

__all__ = ["TEMPLATES", "get_template", "load_workflows", "ITSM_FIREWALL_CHANGE"]

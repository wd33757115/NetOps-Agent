# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 编排引擎（ITSM 等多步长时任务）。"""

from src.core.workflows.engine import WorkflowEngine
from src.core.workflows.registry import get_template, list_templates, load_workflows

__all__ = ["WorkflowEngine", "get_template", "list_templates", "load_workflows"]

# 向后兼容：延迟解析
def __getattr__(name: str):
    if name == "ITSM_FIREWALL_CHANGE":
        return get_template("itsm-firewall-change")
    raise AttributeError(name)

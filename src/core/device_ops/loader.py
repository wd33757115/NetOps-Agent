# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Load the platform device operations tool module."""

from __future__ import annotations

from types import ModuleType


def import_netops_agent_tools() -> ModuleType:
    """Return the decoupled PostgreSQL-backed device operations module."""
    from src.core.device_ops import tools

    return tools

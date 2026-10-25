# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Inventory-backed execution helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class InventoryExecutionContext:
    """Loaded inventory tool context for one runner invocation."""

    netops: Any
    db_manager: Any
    tool: Any
    execution_plan: Any | None
    device_filter: Any | None


def load_inventory_execution_context(
    *,
    tool_factory_name: str,
    filter_params: dict[str, Any] | None,
    execution_plan_data: dict[str, Any] | None,
    tools_loader: Callable[[], Any] | None = None,
) -> InventoryExecutionContext:
    """Load netops tools, DB manager, optional plan, and optional device filter."""
    from src.core.collection import execution_plan_from_metadata
    from src.core.device_ops.loader import import_netops_agent_tools

    explicit_plan = (
        execution_plan_from_metadata(execution_plan_data)
        if execution_plan_data
        else None
    )
    loader = tools_loader or import_netops_agent_tools
    netops = loader()
    db_manager = netops.DBManager()
    tool_factory = getattr(netops, tool_factory_name)
    tool = tool_factory(db_manager)
    device_filter = None
    if explicit_plan is None:
        device_filter = (
            netops.DeviceFilter(**filter_params)
            if filter_params
            else netops.DeviceFilter()
        )
    return InventoryExecutionContext(
        netops=netops,
        db_manager=db_manager,
        tool=tool,
        execution_plan=explicit_plan,
        device_filter=device_filter,
    )


def run_plan_or_filter(
    *,
    context: InventoryExecutionContext,
    plan_runner: Callable[[Any], Any],
    filter_runner: Callable[[Any], Any],
) -> Any:
    """Run an async tool method by explicit plan when present, otherwise by filter."""
    if context.execution_plan is not None:
        return asyncio.run(plan_runner(context.execution_plan))
    return asyncio.run(filter_runner(context.device_filter))

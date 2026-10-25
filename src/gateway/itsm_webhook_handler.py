# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""通用 ITSM Webhook 处理（读取 Workflow 插件包，无硬编码工单类型）。"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette import status

from src.core.plugins.context_mapping import map_request_to_context
from src.core.plugins.itsm_webhook import get_itsm_webhook_registry
from src.core.workflows.engine import WorkflowEngine
from src.core.workflows.registry import get_template
from src.gateway.schemas import ITSMWorkflowStartResponse


async def handle_itsm_webhook(route_key: str, body: dict[str, Any], user) -> JSONResponse:
    """Start the route's Workflow for the account Django authenticated (HTTP Basic)."""
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="ITSM Webhook 需要平台账号：请经工作台 /api/itsm/webhook/<route_key>/ 调用并携带用户名和密码",
        )
    registry = get_itsm_webhook_registry()
    plugin = registry.get_by_route(route_key)
    if not plugin:
        raise HTTPException(status_code=404, detail=f"未知 ITSM Webhook 路由: {route_key}")

    template = get_template(plugin.workflow)
    if not template:
        raise HTTPException(status_code=500, detail=f"Workflow 未注册: {plugin.workflow}")

    try:
        context = map_request_to_context(body, plugin.context_mapping)
        run_id = WorkflowEngine.start(
            plugin.workflow,
            context,
            source="itsm_webhook",
            user_id=user.user_id,
        )
        ticket_id = context.get("ticket_id") or body.get("ticket_id") or ""
        resp = ITSMWorkflowStartResponse(
            workflow_run_id=run_id,
            ticket_id=str(ticket_id),
            status="accepted",
            message=plugin.accepted_message,
            query_endpoint=f"/api/v1/workflows/{run_id}",
        )
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=resp.model_dump())
    except Exception as exc:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "workflow_run_id": str(uuid.uuid4()),
                "ticket_id": body.get("ticket_id", ""),
                "status": "failed",
                "message": f"Workflow 启动失败: {exc}",
                "query_endpoint": "",
            },
        )

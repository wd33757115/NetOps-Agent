# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

# =============================================================================
# NetOps Multi-Agent System - FastAPI Gateway (v2.0)
# =============================================================================
#
# 启动命令：
#
# 【开发环境 - 单 worker（支持热重载）】
#   python -m src.gateway.main
#   uvicorn src.gateway.main:app --host 0.0.0.0 --port 8000 --reload
#
# 【生产环境】使用 Gunicorn + UvicornWorker，并按 CPU/IO 负载配置 worker。
#
# 【开发环境 - 禁用热重载但多 worker 测试】
#   uvicorn src.gateway.main:app --host 0.0.0.0 --port 8000 --workers 4
#
# =============================================================================

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(BASE_DIR))


from src.common.config import get_settings
from src.core.logging import bind_context, configure_logging, get_logger, reset_context

settings = get_settings()
configure_logging(log_level=settings.LOG_LEVEL, log_format=settings.LOG_FORMAT)
log = get_logger("gateway")

import json
import uuid

import uvicorn
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from src.auth.dependencies import get_optional_user
from src.auth.models import CurrentUser
from src.gateway.audit_service import write_audit_log
from src.gateway.bff_security import is_enforce_bff_origin_enabled


from src.platform.authorization.client import require_app


from src.gateway.artifacts_api import router as artifacts_router
from src.gateway.official_documents_api import router as official_documents_router
from src.gateway.bff_security import (
    is_bff_bypass_path,
    is_trusted_bff_request,
    reject_envelope,
)
from src.gateway.conversation_service import get_conversation_service
from src.gateway.devices_api import router as devices_router
from src.gateway.events_api import router as events_router
from src.gateway.exception_handlers import register_exception_handlers
from src.gateway.maintenance_api import router as maintenance_router
from src.gateway.notification_api import router as notification_router
from src.gateway.offline_patrol_api import router as offline_patrol_router
from src.gateway.parser_api import router as parser_router
from src.gateway.patrol_api import router as patrol_router
from src.gateway.patrol_reports_api import router as patrol_reports_router
from src.gateway.schemas import (
    AddMessageRequest,
    ChatRequest,
    ChatResponse,
    ConversationDetailResponse,
    ConversationResponse,
    CreateConversationRequest,
    HealthResponse,
    ITSMEventRequest,
    ITSMFirewallPolicyRequest,
    ITSMWorkflowStartResponse,
    MessageResponse,
    TaskResponse,
    UpdateConversationRequest,
)
from src.gateway.skills_api import router as skills_router
from src.gateway.storage_api import router as storage_router
from src.gateway.workflow_api import router as workflow_router
from src.infrastructure.db.migrations import require_database_schema_current
from src.infrastructure.db.postgres import (
    require_postgres_connection,
    verify_postgres_connection,
)

CELERY_AVAILABLE = False
EXECUTE_FIREWALL_POLICY_TASK = None

# Services verify platform schema readiness; only platform bootstrap writes DDL/seeds.
require_postgres_connection()
require_database_schema_current()


# =============================================================================
# 连接池与资源管理（Lifespan）
# =============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI Lifespan：管理应用生命周期中的资源

    启动时：
    - 初始化 Redis 连接池（Celery broker + result backend）
    - 验证 PostgreSQL 连接
    - 延迟加载聊天执行入口

    关闭时：
    - 关闭 Redis 连接池
    - 关闭 PostgreSQL 引擎
    - 清理临时资源
    """
    import redis.asyncio as aioredis

    log.info("gateway_startup_begin", project=settings.PROJECT_NAME)

    # --- 启动：初始化 Redis 连接池 ---
    app.state.redis_pool = None
    try:
        redis_url = settings.redis_url
        app.state.redis_pool = aioredis.ConnectionPool.from_url(
            redis_url,
            max_connections=20,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
            retry_on_timeout=True,
        )
        test_conn = aioredis.Redis(connection_pool=app.state.redis_pool)
        await test_conn.ping()
        log.info("redis_connected", redis_url=redis_url)
    except Exception as e:
        log.warning("redis_unavailable", error=str(e))

    # --- 启动：验证 PostgreSQL ---
    require_postgres_connection()
    log.info("postgres_connected", host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT)

    # --- 启动：加载 Workflow / ITSM 插件包 ---
    try:
        from src.core.plugins.chat_intent import get_chat_intent_registry
        from src.core.plugins.itsm_webhook import get_itsm_webhook_registry
        from src.core.workflows.registry import load_workflows

        wf_count = len(load_workflows(force=True))
        get_itsm_webhook_registry().load(force=True)
        intent_count = len(get_chat_intent_registry().all_intents())
        log.info("workflow_plugins_loaded", workflow_count=wf_count, chat_intent_count=intent_count)

        from src.core.workflows.reload_bus import start_reload_listener

        start_reload_listener()
        log.info("workflow_reload_listener_started")

        from src.core.events.worker import start_event_consumers

        start_event_consumers()
        log.info("event_consumers_started")
    except Exception as e:
        log.warning("workflow_plugins_load_failed", error=str(e))

    # --- 启动：统一加载 SKILL.md + SkillSystem + Registry ---
    try:
        from src.skills.bootstrap import bootstrap_skills

        skill_count = bootstrap_skills(force=True)
        log.info("skill_bootstrap_complete", skill_count=skill_count)
    except Exception as e:
        log.warning("skill_bootstrap_failed", error=str(e))

    # --- 启动：延迟加载 Agent Graph（避免启动时因网络问题卡住）---
    log.info("agent_executor", executor="dsh")

    enforce = is_enforce_bff_origin_enabled()
    log.info(
        "gateway_startup_complete",
        bff_origin_enforcement=enforce,
        docs_url=f"http://localhost:{settings.FASTAPI_PORT}/docs",
    )

    yield

    # --- 关闭：清理资源 ---
    log.info("gateway_shutdown_begin")

    try:
        from src.core.workflows.reload_bus import stop_reload_listener

        stop_reload_listener()
        log.info("workflow_reload_listener_stopped")
    except Exception:
        pass

    try:
        from src.core.events.worker import stop_event_consumers

        stop_event_consumers()
    except Exception:
        pass

    if app.state.redis_pool:
        await app.state.redis_pool.disconnect()
        log.info("redis_pool_closed")

    try:
        from src.infrastructure.db.postgres import engine

        engine.dispose()
        log.info("postgres_engine_disposed")
    except Exception:
        pass

    log.info("gateway_shutdown_complete")


# =============================================================================
# FastAPI 应用实例
# =============================================================================
app = FastAPI(
    title="NetOps Multi-Agent System API Gateway",
    description="NetOps AI Agent System - FastAPI Gateway with Async Celery Skill Execution",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.include_router(skills_router)
app.include_router(storage_router)
app.include_router(artifacts_router)
app.include_router(official_documents_router)  # Legacy URL, same application admission
from src.platform.kernel.registry import mount_apps
mount_apps(app)
from src.gateway.device_terminal import router as device_terminal_router
app.include_router(device_terminal_router)
app.include_router(workflow_router)
app.include_router(notification_router)
app.include_router(events_router)
app.include_router(parser_router, prefix="/api/v1")
app.include_router(devices_router)
app.include_router(patrol_router)
app.include_router(offline_patrol_router)
app.include_router(patrol_reports_router)
app.include_router(maintenance_router)
register_exception_handlers(app)

# =============================================================================
# CORS 中间件（生产环境请限制 origins）
# =============================================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# 内部请求验证中间件（拒绝未经 Django BFF 转发的请求）
# =============================================================================
@app.middleware("http")
async def bff_origin_check(request: Request, call_next):
    if not is_enforce_bff_origin_enabled():
        return await call_next(request)

    if is_bff_bypass_path(request.url.path):
        return await call_next(request)

    # The deployment probe is not a browser request: it carries its own deployment credential.
    if request.url.path == "/_internal/app-packages":
        return await call_next(request)

    if not is_trusted_bff_request(request.headers):
        request_id = (
            getattr(request.state, "request_id", None)
            or request.headers.get("X-Request-Id")
            or str(uuid.uuid4())
        )
        return JSONResponse(
            status_code=403,
            content=reject_envelope(request_id=request_id),
            headers={"X-Request-Id": request_id},
        )

    return await call_next(request)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """注入 request_id 并记录 HTTP 请求摘要。"""
    request_id = (
        request.headers.get("X-Request-Id")
        or request.headers.get("X-Request-ID")
        or str(uuid.uuid4())
    )
    request.state.request_id = request_id
    tokens = bind_context(request_id=request_id)
    path = request.url.path
    skip_access_log = path in ("/health", "/api/v1/notifications/")
    start = datetime.now(timezone.utc)
    try:
        response = await call_next(request)
        if not skip_access_log:
            duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
            log.info(
                "http_request_completed",
                method=request.method,
                path=path,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
        response.headers["X-Request-Id"] = request_id
        return response
    except Exception as exc:
        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        log.error(
            "http_request_failed",
            method=request.method,
            path=path,
            duration_ms=duration_ms,
            error=str(exc),
            exc_info=exc,
        )
        raise
    finally:
        reset_context(tokens)


# =============================================================================
# 依赖注入：从 app.state 获取预加载的服务
# =============================================================================
# =============================================================================
# API 端点
# =============================================================================


@app.get("/", tags=["System"])
async def root():
    return {
        "service": settings.PROJECT_NAME,
        "version": "2.0.0",
        "status": "running",
        "description": "NetOps Multi-Agent System API Gateway",
    }


@app.get("/health", tags=["System"], response_model=HealthResponse)
async def health_check():
    services = {}

    try:
        services["postgres"] = verify_postgres_connection()
    except Exception:
        services["postgres"] = False


    try:
        if app.state.redis_pool:
            import redis.asyncio as aioredis

            r = aioredis.Redis(connection_pool=app.state.redis_pool)
            await r.ping()
            services["redis"] = True
        else:
            services["redis"] = False
    except Exception:
        services["redis"] = False

    all_healthy = all(services.values())
    return HealthResponse(status="healthy" if all_healthy else "degraded", services=services)


@app.get("/health/diagnostics", tags=["System"], response_model=None)
async def health_diagnostics():
    """一键诊断：PostgreSQL / Redis / Celery / MinIO / Qdrant / RAG 等。"""
    from src.gateway.diagnostics import run_diagnostics

    return run_diagnostics().model_dump(mode="json")


@app.post("/api/chat/", tags=["Chat"], response_model=ChatResponse)
@app.post("/api/v1/chat", tags=["Chat"], response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, user: CurrentUser = Depends(require_app("assistant"))):
    from src.gateway.chat_stream import chat

    return await chat(request, user)


@app.post("/api/v1/chat/stream", tags=["Chat"])
async def chat_stream_endpoint(request: ChatRequest, user: CurrentUser = Depends(require_app("assistant"))):
    from src.gateway.chat_stream import prepare, stream_chat

    run, body = await asyncio.to_thread(prepare, request, user)

    async def event_generator():
        async for event in stream_chat(request, user, run, body):
            yield f"event: {event['event']}\ndata: {event['data']}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.websocket("/ws/v1/chat")
async def websocket_chat_endpoint(websocket: WebSocket):
    # The supported authenticated streaming transport is the existing BFF SSE route.
    await websocket.close(code=1008, reason="Use authenticated /api/v1/chat/stream")


@app.post("/api/v1/itsm/webhook", tags=["ITSM"])
async def itsm_webhook_endpoint(event: ITSMEventRequest, background_tasks: BackgroundTasks):
    """
    ITSM Webhook 接收入口（通用事件）

    - 事件持久化
    - 后台异步调用 Agent
    - 返回 Ack
    """
    task_id = str(uuid.uuid4())

    response = {
        "task_id": task_id,
        "event_id": event.event_id,
        "status": "accepted",
        "message": "ITSM 事件已接收，后台处理中...",
        "query_endpoint": f"/api/v1/tasks/{task_id}",
    }

    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=response)


@app.post("/api/v1/itsm/webhook/callback", tags=["ITSM"])
async def itsm_callback_endpoint(request: dict):
    """
    ITSM 回调接收端点（模拟外部 ITSM 系统，供 E2E / 联调）

    须在通配路由 /{route_key} 之前注册，避免 callback 被当作 workflow route_key。
    """
    log.info(
        "itsm_callback_received",
        callback_id=request.get("callback_id"),
        source_ticket_id=request.get("source_ticket_id"),
    )

    response = {
        "status": "success",
        "message": "回调已接收",
        "callback_id": request.get("callback_id"),
        "source_ticket_id": request.get("source_ticket_id"),
    }
    return JSONResponse(status_code=status.HTTP_200_OK, content=response)


@app.post(
    "/api/v1/itsm/webhook/firewall-policy", tags=["ITSM"], response_model=ITSMWorkflowStartResponse
)
async def itsm_firewall_policy_webhook(request: ITSMFirewallPolicyRequest, raw: Request):
    """ITSM Webhook 兼容入口（转发至插件 route_key=firewall-policy）。"""
    from src.auth.security import resolve_current_user
    from src.gateway.itsm_webhook_handler import handle_itsm_webhook

    return await handle_itsm_webhook(
        "firewall-policy", request.model_dump(), resolve_current_user(raw.headers)
    )


@app.post(
    "/api/v1/itsm/webhook/{route_key}", tags=["ITSM"], response_model=ITSMWorkflowStartResponse
)
async def itsm_webhook_by_route(route_key: str, request: Request):
    """通用 ITSM Webhook（路由由 Workflow 插件包 ITSM.webhook.yaml 定义）。"""
    if route_key in ("callback",):
        raise HTTPException(
            status_code=404, detail=f"请使用专用端点: /api/v1/itsm/webhook/{route_key}"
        )
    from src.auth.security import resolve_current_user
    from src.gateway.itsm_webhook_handler import handle_itsm_webhook

    user = resolve_current_user(request.headers)
    body = await request.json() if user is not None else {}
    return await handle_itsm_webhook(route_key, body, user)


@app.get("/api/v1/tasks/{task_id}", tags=["Tasks"], response_model=TaskResponse)
async def get_task_status(task_id: str):
    """查询 Celery 任务状态"""
    from src.core.celery_tasks.celery_app import celery

    try:
        result = celery.AsyncResult(task_id)

        if result.state == "PENDING":
            return TaskResponse(
                task_id=task_id, status="pending", celery_task_id=task_id, result="任务等待执行"
            )
        elif result.state == "STARTED":
            return TaskResponse(
                task_id=task_id, status="processing", celery_task_id=task_id, result="任务执行中..."
            )
        elif result.state == "SUCCESS":
            task_result = result.get()
            return TaskResponse(
                task_id=task_id,
                status="completed",
                celery_task_id=task_id,
                result="任务执行成功",
                file_url=task_result.get("download_url"),
            )
        elif result.state == "FAILURE":
            return TaskResponse(
                task_id=task_id,
                status="failed",
                celery_task_id=task_id,
                error_message=str(result.info),
            )
        elif result.state == "RETRY":
            return TaskResponse(
                task_id=task_id, status="retry", celery_task_id=task_id, result="任务重试中..."
            )
        else:
            return TaskResponse(
                task_id=task_id,
                status=result.state.lower() if result.state else "pending",
                celery_task_id=task_id,
            )
    except Exception as e:
        return TaskResponse(task_id=task_id, status="failed", error_message=str(e))


# =============================================================================
# 对话管理 API 端点
# =============================================================================


@app.post("/api/v1/conversations", tags=["Conversations"], response_model=ConversationResponse)
async def create_conversation(
    request: CreateConversationRequest,
    user: CurrentUser = Depends(require_app("assistant")),
):
    """创建新对话"""
    conv_service = get_conversation_service()
    user_id = user.user_id if user else request.user_id
    conversation = conv_service.create_conversation(
        title=request.title, user_id=user_id, thread_id=request.thread_id
    )

    return ConversationResponse(
        id=conversation["id"],
        title=conversation["title"],
        user_id=conversation["user_id"],
        thread_id=conversation["thread_id"],
        status=conversation["status"],
        summary=conversation["summary"],
        created_at=conversation["created_at"],
        updated_at=conversation["updated_at"],
        message_count=0,
    )


@app.get("/api/v1/conversations", tags=["Conversations"], response_model=list[ConversationResponse])
async def get_conversations(
    user_id: str | None = None,
    limit: int = 20,
    offset: int = 0,
    user: CurrentUser = Depends(require_app("assistant")),
):
    """获取对话列表（登录用户仅能看到自己的会话）"""
    conv_service = get_conversation_service()
    effective_user_id = user.user_id if user else user_id
    if is_enforce_bff_origin_enabled() and not effective_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    conversations = conv_service.get_conversations(
        user_id=effective_user_id, limit=limit, offset=offset
    )

    result = []
    for conv in conversations:
        result.append(
            ConversationResponse(
                id=conv["id"],
                title=conv["title"],
                user_id=conv["user_id"],
                thread_id=conv["thread_id"],
                status=conv["status"],
                summary=conv["summary"],
                created_at=conv["created_at"],
                updated_at=conv["updated_at"],
                message_count=conv.get("message_count", 0),
            )
        )

    return result


@app.get(
    "/api/v1/conversations/{conversation_id}",
    tags=["Conversations"],
    response_model=ConversationDetailResponse,
)
async def get_conversation_detail(
    conversation_id: str,
    user: CurrentUser = Depends(require_app("assistant")),
):
    """获取对话详情（含消息）"""
    conv_service = get_conversation_service()
    if user:
        conv = conv_service.get_conversation_for_user(conversation_id, user.user_id)
        if not conv:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )
        data = conv_service.get_conversation_with_messages(conversation_id)
    else:
        data = conv_service.get_conversation_with_messages(conversation_id)

    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    conversation = data["conversation"]
    messages = data["messages"]

    message_responses = []
    for msg in messages:
        message_responses.append(
            MessageResponse(
                id=msg["id"],
                role=msg["role"],
                content=msg["content"],
                agent_type=msg.get("agent_type"),
                celery_task_id=msg.get("celery_task_id"),
                download_url=msg.get("download_url"),
                references=msg.get("references"),
                created_at=msg["created_at"],
            )
        )

    return ConversationDetailResponse(
        conversation=ConversationResponse(
            id=conversation["id"],
            title=conversation["title"],
            user_id=conversation["user_id"],
            thread_id=conversation["thread_id"],
            status=conversation["status"],
            summary=conversation["summary"],
            created_at=conversation["created_at"],
            updated_at=conversation["updated_at"],
            message_count=len(messages),
        ),
        messages=message_responses,
    )


@app.put(
    "/api/v1/conversations/{conversation_id}",
    tags=["Conversations"],
    response_model=ConversationResponse,
)
async def update_conversation(conversation_id: str, request: UpdateConversationRequest, user: CurrentUser = Depends(require_app("assistant"))):
    """更新对话信息"""
    conv_service = get_conversation_service()
    if not conv_service.get_conversation_for_user(conversation_id, user.user_id):
        raise HTTPException(404, "Conversation not found")

    update_data = {}
    if request.title is not None:
        update_data["title"] = request.title
    if request.summary is not None:
        update_data["summary"] = request.summary

    conversation = conv_service.update_conversation(conversation_id, **update_data)

    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    messages = conv_service.get_messages(conversation_id)

    return ConversationResponse(
        id=conversation["id"],
        title=conversation["title"],
        user_id=conversation["user_id"],
        thread_id=conversation["thread_id"],
        status=conversation["status"],
        summary=conversation["summary"],
        created_at=conversation["created_at"],
        updated_at=conversation["updated_at"],
        message_count=len(messages),
    )


@app.delete(
    "/api/v1/conversations/{conversation_id}",
    tags=["Conversations"],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_conversation(conversation_id: str, user: CurrentUser = Depends(require_app("assistant"))):
    """删除对话"""
    conv_service = get_conversation_service()
    if not conv_service.get_conversation_for_user(conversation_id, user.user_id):
        raise HTTPException(404, "Conversation not found")
    success = conv_service.delete_conversation(conversation_id)

    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    return None


@app.post(
    "/api/v1/conversations/{conversation_id}/messages",
    tags=["Conversations"],
    response_model=MessageResponse,
)
async def add_message(conversation_id: str, request: AddMessageRequest, user: CurrentUser = Depends(require_app("assistant"))):
    """添加消息到对话"""
    conv_service = get_conversation_service()
    if not conv_service.get_conversation_for_user(conversation_id, user.user_id):
        raise HTTPException(404, "Conversation not found")

    conversation = conv_service.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    message = conv_service.add_message(
        conversation_id=conversation_id,
        role=request.role,
        content=request.content,
        agent_type=request.agent_type,
        celery_task_id=request.celery_task_id,
        download_url=request.download_url,
        references=request.references,
    )

    return MessageResponse(
        id=message["id"],
        role=message["role"],
        content=message["content"],
        agent_type=message.get("agent_type"),
        celery_task_id=message.get("celery_task_id"),
        download_url=message.get("download_url"),
        references=message.get("references"),
        created_at=message["created_at"],
    )


@app.post("/api/v1/conversations/{conversation_id}/summarize", tags=["Conversations"])
async def summarize_conversation(conversation_id: str, user: CurrentUser = Depends(require_app("assistant"))):
    """生成对话总结和标题"""
    conv_service = get_conversation_service()
    if not conv_service.get_conversation_for_user(conversation_id, user.user_id):
        raise HTTPException(404, "Conversation not found")

    conversation = conv_service.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    title = conv_service.generate_title(conversation_id)
    summary = conv_service.summarize_conversation(conversation_id)

    conv_service.update_conversation(conversation_id, title=title, summary=summary)

    return {
        "conversation_id": conversation_id,
        "title": title,
        "summary": summary,
        "message": "对话总结生成成功",
    }


# =============================================================================
# 启动入口（开发环境）
# =============================================================================
def start():
    """
    开发环境启动入口
    生产环境请使用文件顶部的 Gunicorn 命令。
    """
    target_port = settings.FASTAPI_PORT
    host = settings.FASTAPI_HOST

    log.info(
        "gateway_dev_start",
        project=settings.PROJECT_NAME,
        host=host,
        port=target_port,
        docs_url=f"http://localhost:{target_port}/docs",
    )

    # Windows 多 worker 模式（4个worker，适合12700K）
    uvicorn.run(
        "src.gateway.main:app",
        host=host,
        port=target_port,
        reload=settings.DEBUG,
        workers=4,
        timeout_keep_alive=120,
    )


if __name__ == "__main__":
    start()

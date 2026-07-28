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

os.environ["LLAMA_INDEX_CACHE_DIR"] = str(Path("./cache/llama_index").absolute())
os.environ["LLAMA_INDEX_DISABLE_HTTP_CACHE"] = "1"

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


def get_supervisor_graph():
    """加载 Supervisor v2 协同图（v1 已废弃）。"""
    from src.agents.supervisor.graph_v2 import compiled_graph_v2

    log.info("supervisor_v2_enabled")
    return compiled_graph_v2()


def _human_message(content: str):
    """Load the AI message type only when a chat transport is used."""
    from langchain_core.messages import HumanMessage

    return HumanMessage(content=content)


from src.gateway.artifacts_api import router as artifacts_router
from src.gateway.bff_security import (
    is_bff_bypass_path,
    is_trusted_bff_request,
    reject_envelope,
)
from src.gateway.conversation_service import get_conversation_service
from src.gateway.devices_api import router as devices_router
from src.gateway.events_api import router as events_router
from src.gateway.exception_handlers import register_exception_handlers
from src.gateway.knowledge_api import router as knowledge_router
from src.gateway.maintenance_api import router as maintenance_router
from src.gateway.notification_api import router as notification_router
from src.gateway.offline_patrol_api import router as offline_patrol_router
from src.gateway.parser_api import router as parser_router
from src.gateway.patrol_api import router as patrol_router
from src.gateway.schemas import (
    AddMessageRequest,
    ChatFileUploadRequest,
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
    RAGSearchRequest,
    RAGSearchResponse,
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

# =============================================================================
# 数据库初始化（非阻塞，失败不阻止启动）
# =============================================================================
require_postgres_connection()
require_database_schema_current()
from src.core.parser.bootstrap import bootstrap_shared_templates
from src.core.check_items.catalog import CheckItemCatalogRepository
from src.core.check_items.identity import EntityIdentityPolicyRepository

parser_bootstrap = (
    bootstrap_shared_templates()
    if settings.PARSER_BOOTSTRAP_SHARED_TEMPLATES_ON_STARTUP
    else {"enabled": False, "imported": 0, "skipped": 0, "errors": []}
)
log.info("parser_assets_bootstrapped", **parser_bootstrap)

check_item_bootstrap = CheckItemCatalogRepository().bootstrap(
    actor="gateway-startup"
)
log.info("check_item_catalog_bootstrapped", **check_item_bootstrap)

identity_policy_bootstrap = EntityIdentityPolicyRepository().bootstrap(
    actor="gateway-startup"
)
log.info("entity_identity_policy_bootstrapped", **identity_policy_bootstrap)


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
    - 预加载 RAG 服务和 Agent Graph
    - 预热 Qdrant 向量数据库连接（如已配置）

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

    # --- 启动：预加载 RAG 服务 ---
    try:
        from src.core.rag_service.service import get_rag_service

        app.state.rag_service = get_rag_service()
        log.info("rag_service_loaded")
    except Exception as e:
        log.warning("rag_service_load_failed", error=str(e))
        app.state.rag_service = None

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

        skill_count = bootstrap_skills(
            rag_service=getattr(app.state, "rag_service", None),
            force=True,
        )
        log.info("skill_bootstrap_complete", skill_count=skill_count)
    except Exception as e:
        log.warning("skill_bootstrap_failed", error=str(e))

    # --- 启动：延迟加载 Agent Graph（避免启动时因网络问题卡住）---
    app.state.agent_graph = None
    log.info("agent_graph_lazy_load")

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
app.include_router(knowledge_router)
app.include_router(storage_router)
app.include_router(artifacts_router)
app.include_router(workflow_router)
app.include_router(notification_router)
app.include_router(events_router)
app.include_router(parser_router)
app.include_router(devices_router)
app.include_router(patrol_router)
app.include_router(offline_patrol_router)
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
def get_rag():
    return app.state.rag_service


def get_graph():
    return app.state.agent_graph


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

    services["rag"] = app.state.rag_service is not None

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


@app.post("/api/chat/", tags=["Chat"], response_model=ChatResponse, status_code=status.HTTP_200_OK)
async def chat_endpoint_legacy(request: ChatRequest):
    """Legacy chat endpoint for backward compatibility"""
    return await chat_endpoint(request)


@app.post(
    "/api/v1/chat", tags=["Chat"], response_model=ChatResponse, status_code=status.HTTP_200_OK
)
async def chat_endpoint(
    request: ChatRequest,
    http_request: Request,
    user: CurrentUser | None = Depends(get_optional_user),
):
    """
    统一聊天接口（REST，异步执行）

    - 支持多轮对话状态持久化到 PostgreSQL
    - Supervisor Agent 自动路由
    - Skill 执行使用 Celery 异步（非阻塞）
    - RAG Metadata 过滤
    - 自动生成对话标题
    """
    if is_enforce_bff_origin_enabled() and not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")

    if user and not user.can_execute_skills():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前角色仅可浏览，无法执行运维 Skill",
        )

    effective_user_id = user.user_id if user else request.user_id

    conv_service = get_conversation_service()
    conversation_id = request.thread_id

    if not conversation_id:
        conversation = conv_service.create_conversation(
            title="新对话",
            user_id=effective_user_id,
        )
        conversation_id = conversation["id"]
    else:
        if effective_user_id:
            conversation = conv_service.get_conversation_for_user(
                conversation_id, effective_user_id
            )
        else:
            conversation = conv_service.get_conversation(conversation_id)
        if not conversation:
            conversation = conv_service.create_conversation(
                title="新对话",
                user_id=effective_user_id,
            )
            conversation_id = conversation["id"]

    thread_id = f"thread-{conversation_id.split('-')[-1]}"
    if user and user.thread_prefix:
        thread_id = f"{user.thread_prefix}-{conversation_id.split('-')[-1]}"

    conv_service.update_conversation(
        conversation_id, thread_id=thread_id, user_id=effective_user_id
    )

    config = {"configurable": {"thread_id": thread_id}}

    # 延迟加载 Agent Graph
    agent_graph = app.state.agent_graph
    if not agent_graph:
        log.info("chat_graph_lazy_loading")
        try:
            agent_graph = get_supervisor_graph()
            app.state.agent_graph = agent_graph
            log.info("chat_graph_loaded")
        except Exception as e:
            log.error("chat_graph_load_failed", error=str(e), exc_info=e)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Agent Graph loading failed: {str(e)}",
            )

    try:
        initial_state = {
            "messages": [_human_message(request.query)],
            "source": request.source.value,
            "task_id": str(uuid.uuid4()),
            "thread_id": thread_id,
        }
        if user:
            initial_state["user_id"] = user.user_id
            initial_state["user_role"] = user.role

        if request.metadata_filters:
            initial_state["metadata_filters"] = request.metadata_filters

        if request.uploaded_file_path:
            initial_state["uploaded_file_path"] = request.uploaded_file_path

        if request.ticket_id:
            initial_state["ticket_id"] = request.ticket_id

        log.debug(
            "chat_request_prepared",
            state_keys=sorted(initial_state.keys()),
            ticket_id=initial_state.get("ticket_id"),
        )

        graph_timeout = int(os.getenv("CHAT_GRAPH_TIMEOUT", "170"))

        # [Async] run graph.invoke in thread pool to avoid blocking event loop
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(agent_graph.invoke, initial_state, config),
                timeout=graph_timeout,
            )
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=(
                    f"Agent 处理超时（{graph_timeout}秒）。"
                    "若执行防火墙/备份类 Skill，请确认 Celery Worker 已启动；"
                    "首次语义路由可能较慢，触发词命中后应已跳过 Embedding。"
                ),
            )

        response_msg = result["messages"][-1].content
        next_agent = result.get("next_agent")
        references = result.get("knowledge_references")
        celery_task_id = result.get("celery_task_id")

        from src.gateway.diagnostics import extract_download_url_from_graph_result

        download_url = extract_download_url_from_graph_result(result)

        # 保存用户消息和Agent回复到数据库
        conv_service.add_message(
            conversation_id=conversation_id, role="user", content=request.query
        )

        conv_service.add_message(
            conversation_id=conversation_id,
            role="assistant",
            content=response_msg,
            agent_type=next_agent,
            celery_task_id=celery_task_id,
            download_url=download_url,
            references=references,
        )

        # 生成对话标题
        title = conv_service.generate_title(conversation_id)
        conv_service.update_conversation(conversation_id, title=title)

        write_audit_log(
            action="chat",
            user_id=effective_user_id,
            username=user.username if user else None,
            resource_type="conversation",
            resource_id=conversation_id,
            detail={"agent_type": next_agent, "query_len": len(request.query or "")},
            ip_address=http_request.client.host if http_request.client else None,
        )

        log.info(
            "chat_completed",
            conversation_id=conversation_id,
            next_agent=next_agent,
            celery_task_id=celery_task_id,
        )

        return ChatResponse(
            response=response_msg,
            thread_id=conversation_id,
            agent_type=next_agent,
            task_id=result.get("task_id"),
            celery_task_id=celery_task_id,
            download_url=download_url,
            references=references,
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent invocation failed: {str(e)}",
        )


@app.post("/api/v1/chat/stream", tags=["Chat"])
async def chat_stream_endpoint(
    request: ChatRequest,
    http_request: Request,
    user: CurrentUser | None = Depends(get_optional_user),
):
    """SSE 流式聊天：实时推送 LangGraph 节点进度 + Langfuse trace。"""
    agent_graph = app.state.agent_graph
    if not agent_graph:
        try:
            agent_graph = get_supervisor_graph()
            app.state.agent_graph = agent_graph
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Agent Graph loading failed: {str(exc)}",
            )

    enforce_auth = is_enforce_bff_origin_enabled()

    try:
        from src.gateway.chat_stream import stream_supervisor_chat
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"AI chat dependencies are not installed: {exc.name}",
        ) from exc

    async def event_generator():
        async for event in stream_supervisor_chat(
            request=request,
            http_request=http_request,
            user=user,
            agent_graph=agent_graph,
            enforce_auth=enforce_auth,
        ):
            event_type = event.get("event", "message")
            data = event.get("data", "")
            yield f"event: {event_type}\ndata: {data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.websocket("/ws/v1/chat")
async def websocket_chat_endpoint(websocket: WebSocket):
    """
    WebSocket 实时聊天接口

    - 流式返回状态（thinking / routing / retrieving / answering）
    - 支持保持对话上下文
    - 使用 ainvoke 异步调用 Agent Graph
    """
    if is_enforce_bff_origin_enabled() and not is_trusted_bff_request(websocket.headers):
        await websocket.close(code=4403, reason="Access only allowed via Django BFF")
        return

    await websocket.accept()
    thread_id = f"ws-thread-{uuid.uuid4().hex[:12]}"
    config = {"configurable": {"thread_id": thread_id}}

    agent_graph = app.state.agent_graph
    if not agent_graph:
        try:
            agent_graph = get_supervisor_graph()
            app.state.agent_graph = agent_graph
        except Exception as exc:
            await websocket.close(code=1013, reason=f"AI chat unavailable: {exc}")
            return

    try:
        while True:
            data = await websocket.receive_text()

            try:
                payload = json.loads(data)
                query = payload.get("query", "")
                client_thread_id = payload.get("thread_id")
                if client_thread_id:
                    thread_id = client_thread_id
                    config = {"configurable": {"thread_id": thread_id}}
            except json.JSONDecodeError:
                query = data

            await websocket.send_json(
                {
                    "type": "status",
                    "status": "thinking",
                    "thread_id": thread_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

            try:
                # [Async] run graph.invoke in thread pool
                result = await asyncio.to_thread(
                    agent_graph.invoke, {"messages": [_human_message(query)]}, config
                )
                response = result["messages"][-1].content

                await websocket.send_json(
                    {
                        "type": "message",
                        "content": response,
                        "thread_id": thread_id,
                        "agent_type": result.get("next_agent"),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )

            except Exception as e:
                await websocket.send_json(
                    {"type": "error", "error": str(e), "thread_id": thread_id}
                )

    except WebSocketDisconnect:
        log.info("websocket_disconnected", thread_id=thread_id)
    except Exception as e:
        log.warning("websocket_error", thread_id=thread_id, error=str(e), exc_info=e)
    finally:
        pass


@app.post("/api/v1/rag/search", tags=["RAG"], response_model=RAGSearchResponse)
async def rag_search_endpoint(request: RAGSearchRequest):
    """
    统一 RAG 检索服务 API（供所有 Agent 或外部系统调用）
    """
    rag_service = app.state.rag_service
    if not rag_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="RAG Service not available"
        )

    result = rag_service.retrieve_formatted(
        query=request.query, top_k=request.top_k, metadata_filters=request.metadata_filters
    )

    return RAGSearchResponse(count=result["count"], results=result["references"])


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
async def itsm_firewall_policy_webhook(request: ITSMFirewallPolicyRequest):
    """ITSM Webhook 兼容入口（转发至插件 route_key=firewall-policy）。"""
    from src.gateway.itsm_webhook_handler import handle_itsm_webhook

    return await handle_itsm_webhook("firewall-policy", request.model_dump())


@app.post(
    "/api/v1/itsm/webhook/{route_key}", tags=["ITSM"], response_model=ITSMWorkflowStartResponse
)
async def itsm_webhook_by_route(route_key: str, request: Request):
    """通用 ITSM Webhook（路由由 Workflow 插件包 ITSM.webhook.yaml 定义）。"""
    if route_key in ("callback",):
        raise HTTPException(
            status_code=404, detail=f"请使用专用端点: /api/v1/itsm/webhook/{route_key}"
        )
    body = await request.json()
    from src.gateway.itsm_webhook_handler import handle_itsm_webhook

    return await handle_itsm_webhook(route_key, body)


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


@app.post("/api/v1/chat/upload", tags=["Chat"])
async def chat_upload_file(request: ChatFileUploadRequest):
    """
    聊天文件上传接口

    - 用户上传策略 Excel 文件用于生成防火墙策略
    - 返回临时文件路径供后续处理
    """
    import base64
    import tempfile

    temp_dir = tempfile.mkdtemp(prefix="chat_upload_")
    file_path = os.path.join(temp_dir, request.filename)

    try:
        file_content = base64.b64decode(request.file_content)
        with open(file_path, "wb") as f:
            f.write(file_content)

        response = {
            "thread_id": request.thread_id,
            "filename": request.filename,
            "file_path": file_path,
            "status": "uploaded",
            "message": f"文件 {request.filename} 上传成功",
            "next_steps": "请继续输入命令执行策略生成，如：生成防火墙策略",
        }
        return JSONResponse(status_code=status.HTTP_200_OK, content=response)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"error": str(e)}
        )


# =============================================================================
# 对话管理 API 端点
# =============================================================================


@app.post("/api/v1/conversations", tags=["Conversations"], response_model=ConversationResponse)
async def create_conversation(
    request: CreateConversationRequest,
    user: CurrentUser | None = Depends(get_optional_user),
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
    user: CurrentUser | None = Depends(get_optional_user),
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
    user: CurrentUser | None = Depends(get_optional_user),
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
async def update_conversation(conversation_id: str, request: UpdateConversationRequest):
    """更新对话信息"""
    conv_service = get_conversation_service()

    update_data = {}
    if request.title is not None:
        update_data["title"] = request.title
    if request.status is not None:
        update_data["status"] = request.status
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
async def delete_conversation(conversation_id: str):
    """删除对话"""
    conv_service = get_conversation_service()
    success = conv_service.delete_conversation(conversation_id)

    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    return None


@app.post(
    "/api/v1/conversations/{conversation_id}/messages",
    tags=["Conversations"],
    response_model=MessageResponse,
)
async def add_message(conversation_id: str, request: AddMessageRequest):
    """添加消息到对话"""
    conv_service = get_conversation_service()

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
async def summarize_conversation(conversation_id: str):
    """生成对话总结和标题"""
    conv_service = get_conversation_service()

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

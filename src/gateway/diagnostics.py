# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""全栈服务一键诊断（供 /health/diagnostics 与 Status 页面使用）。"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.common.config import get_settings

CheckStatus = Literal["ok", "degraded", "down", "skipped"]


class ServiceCheckResult(BaseModel):
    id: str
    name: str
    status: CheckStatus
    message: str
    latency_ms: float | None = None
    detail: dict[str, Any] | None = None


class DiagnosticsResponse(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    summary: str
    checks: list[ServiceCheckResult]


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def _check_postgres() -> ServiceCheckResult:
    t0 = time.perf_counter()
    try:
        from src.infrastructure.db.postgres import verify_postgres_connection

        ok = verify_postgres_connection()
        return ServiceCheckResult(
            id="postgres",
            name="PostgreSQL",
            status="ok" if ok else "down",
            message="连接正常" if ok else "无法连接数据库",
            latency_ms=_ms(t0),
        )
    except Exception as exc:
        return ServiceCheckResult(
            id="postgres",
            name="PostgreSQL",
            status="down",
            message=str(exc)[:200],
            latency_ms=_ms(t0),
        )


def _check_redis() -> ServiceCheckResult:
    t0 = time.perf_counter()
    settings = get_settings()
    try:
        import redis

        client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        client.ping()
        return ServiceCheckResult(
            id="redis",
            name="Redis",
            status="ok",
            message=f"{settings.REDIS_HOST}:{settings.REDIS_PORT} PING 成功",
            latency_ms=_ms(t0),
            detail={"db": settings.REDIS_DB},
        )
    except Exception as exc:
        return ServiceCheckResult(
            id="redis",
            name="Redis",
            status="down",
            message=str(exc)[:200],
            latency_ms=_ms(t0),
        )


def _check_celery_broker() -> ServiceCheckResult:
    t0 = time.perf_counter()
    settings = get_settings()
    broker = settings.CELERY_BROKER_URL
    try:
        if broker.startswith("redis://"):
            import redis

            client = redis.from_url(broker, socket_connect_timeout=3, socket_timeout=3)
            client.ping()
            msg = "Redis Broker 可达"
        elif broker.startswith("amqp://") or broker.startswith("amqps://"):
            from kombu import Connection

            with Connection(broker, connect_timeout=3) as conn:
                conn.connect()
            msg = "RabbitMQ Broker 可达"
        else:
            return ServiceCheckResult(
                id="celery_broker",
                name="Celery Broker",
                status="skipped",
                message=f"未识别的 Broker URL: {broker[:40]}...",
                latency_ms=_ms(t0),
            )
        return ServiceCheckResult(
            id="celery_broker",
            name="Celery Broker",
            status="ok",
            message=msg,
            latency_ms=_ms(t0),
            detail={"url": broker.split("@")[-1] if "@" in broker else broker},
        )
    except Exception as exc:
        return ServiceCheckResult(
            id="celery_broker",
            name="Celery Broker",
            status="down",
            message=str(exc)[:200],
            latency_ms=_ms(t0),
            detail={"url": broker},
        )


def _check_celery_worker() -> ServiceCheckResult:
    t0 = time.perf_counter()
    try:
        from src.core.celery_tasks.celery_exec import celery_workers_available

        ok = celery_workers_available()
        return ServiceCheckResult(
            id="celery_worker",
            name="Celery Worker",
            status="ok" if ok else "down",
            message="Worker 已注册并就绪" if ok else "无 Worker 响应（防火墙/备份类 Skill 无法执行）",
            latency_ms=_ms(t0),
        )
    except Exception as exc:
        return ServiceCheckResult(
            id="celery_worker",
            name="Celery Worker",
            status="down",
            message=str(exc)[:200],
            latency_ms=_ms(t0),
        )


def _check_celery_beat() -> ServiceCheckResult:
    t0 = time.perf_counter()
    try:
        from src.core.celery_tasks.beat_health import read_celery_beat_heartbeat

        heartbeat = read_celery_beat_heartbeat()
        detail = heartbeat.to_dict()
        if heartbeat.status == "ok":
            return ServiceCheckResult(
                id="celery_beat",
                name="Celery Beat",
                status="ok",
                message=f"调度心跳正常，最近一次为 {heartbeat.age_seconds:.1f} 秒前",
                latency_ms=_ms(t0),
                detail=detail,
            )
        if heartbeat.status == "stale":
            message = f"调度心跳已过期（{heartbeat.age_seconds:.1f} 秒）"
        elif heartbeat.status == "invalid":
            message = "调度心跳格式无效"
        else:
            message = "尚未收到调度心跳；Celery Beat 或 netops.health Worker 未运行"
        return ServiceCheckResult(
            id="celery_beat",
            name="Celery Beat",
            status="down",
            message=message,
            latency_ms=_ms(t0),
            detail=detail,
        )
    except Exception as exc:
        return ServiceCheckResult(
            id="celery_beat",
            name="Celery Beat",
            status="down",
            message=str(exc)[:200],
            latency_ms=_ms(t0),
        )


def _check_minio() -> ServiceCheckResult:
    t0 = time.perf_counter()
    settings = get_settings()
    try:
        from src.infrastructure.storage.minio_client import get_minio_storage

        storage = get_minio_storage()
        if not storage.is_ready():
            return ServiceCheckResult(
                id="minio",
                name="MinIO",
                status="down",
                message="客户端未初始化",
                latency_ms=_ms(t0),
            )
        exists = storage._client.bucket_exists(storage._bucket_name)
        return ServiceCheckResult(
            id="minio",
            name="MinIO",
            status="ok" if exists else "degraded",
            message=f"Bucket '{storage._bucket_name}' 可用" if exists else "Bucket 不存在",
            latency_ms=_ms(t0),
            detail={"endpoint": settings.MINIO_ENDPOINT},
        )
    except Exception as exc:
        return ServiceCheckResult(
            id="minio",
            name="MinIO",
            status="down",
            message=str(exc)[:200],
            latency_ms=_ms(t0),
        )






def run_diagnostics() -> DiagnosticsResponse:
    """同步执行全部诊断项。"""
    checks = [
        _check_postgres(),
        _check_redis(),
        _check_celery_broker(),
        _check_celery_worker(),
        _check_celery_beat(),
        _check_minio(),
    ]

    down = sum(1 for c in checks if c.status == "down")
    degraded = sum(1 for c in checks if c.status == "degraded")

    if down > 0:
        overall: Literal["healthy", "degraded", "unhealthy"] = "unhealthy"
        summary = f"{down} 项不可用，{degraded} 项降级"
    elif degraded > 0:
        overall = "degraded"
        summary = f"核心服务可用，{degraded} 项降级"
    else:
        overall = "healthy"
        summary = "全部检查通过"

    return DiagnosticsResponse(status=overall, summary=summary, checks=checks)


def extract_download_url_from_graph_result(result: dict[str, Any]) -> str | None:
    """从 Supervisor 图结果中提取首个 Skill 下载链接。"""
    from src.core.workflows.artifacts import collect_download_links

    intermediate = result.get("intermediate_results") or {}
    for item in intermediate.values():
        if not isinstance(item, dict):
            continue
        links = collect_download_links(result=item)
        if links:
            return links[0]["url"]
        url = item.get("download_url")
        if url:
            return str(url)
    return None

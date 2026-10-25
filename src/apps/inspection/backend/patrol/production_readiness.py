"""Production admission checks for formal device patrol batches."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal

PatrolReadinessStatus = Literal["ok", "warning", "blocked"]


@dataclass(frozen=True)
class PatrolReadinessCheck:
    id: str
    name: str
    scope: str
    status: PatrolReadinessStatus
    message: str
    remediation: str
    latency_ms: float
    detail: dict[str, Any]

    @property
    def blocking(self) -> bool:
        return self.status == "blocked"

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "blocking": self.blocking,
            "failure_code": f"patrol_{self.id}_not_ready" if self.blocking else None,
        }


class PatrolProductionReadinessService:
    """Evaluate dependencies required to finish and deliver a patrol batch."""

    schema_version = "patrol-production-readiness/v1"
    default_patrol_queue = "netops.patrol"

    def evaluate(
        self,
        *,
        workflow_name: str = "patrol-change-event",
        launch_mode: str = "direct",
        executing_worker_queues: list[str] | None = None,
    ) -> dict[str, Any]:
        workflow_check = self._check_workflow(workflow_name, launch_mode)
        required_queues = list(
            workflow_check.detail.get("required_queues") or [self.default_patrol_queue]
        )
        checks = [
            self._check_postgres(),
            self._check_database_schema(),
            self._check_redis(),
            self._check_celery_broker(),
            self._check_celery_queues(
                required_queues,
                executing_worker_queues=executing_worker_queues,
            ),
            self._check_celery_beat(),
            self._check_minio(),
            workflow_check,
        ]
        blockers = [item for item in checks if item.blocking]
        warnings = [item for item in checks if item.status == "warning"]
        status = "blocked" if blockers else "degraded" if warnings else "ready"
        return {
            "schema_version": self.schema_version,
            "status": status,
            "can_launch": not blockers,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "workflow_name": workflow_name,
            "launch_mode": launch_mode,
            "blocking_count": len(blockers),
            "warning_count": len(warnings),
            "checks": [item.to_dict() for item in checks],
            "blockers": [item.to_dict() for item in blockers],
            "warnings": [item.to_dict() for item in warnings],
        }

    def _run_check(
        self,
        *,
        check_id: str,
        name: str,
        scope: str,
        remediation: str,
        probe,
    ) -> PatrolReadinessCheck:
        started = time.perf_counter()
        try:
            status, message, detail = probe()
        except Exception as exc:
            status = "blocked"
            message = str(exc)[:300] or f"{name} check failed"
            detail = {"exception_type": type(exc).__name__}
        return PatrolReadinessCheck(
            id=check_id,
            name=name,
            scope=scope,
            status=status,
            message=message,
            remediation=remediation,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            detail=dict(detail or {}),
        )

    def _check_postgres(self) -> PatrolReadinessCheck:
        def probe():
            from src.infrastructure.db.postgres import verify_postgres_connection

            if not verify_postgres_connection():
                raise RuntimeError("PostgreSQL 连接失败")
            return "ok", "PostgreSQL 连接正常", {}

        return self._run_check(
            check_id="postgres",
            name="巡检数据库",
            scope="persistence",
            remediation="检查 PostgreSQL 容器、连接参数和账号权限。",
            probe=probe,
        )

    def _check_database_schema(self) -> PatrolReadinessCheck:
        def probe():
            from src.infrastructure.db.migrations import current_revision, head_revision

            current = current_revision()
            expected = head_revision()
            if current != expected:
                return (
                    "blocked",
                    f"数据库 Schema 未升级到当前版本：{current or 'unversioned'} -> {expected}",
                    {"current_revision": current, "expected_revision": expected},
                )
            return (
                "ok",
                f"数据库 Schema 已处于 {expected}",
                {"current_revision": current, "expected_revision": expected},
            )

        return self._run_check(
            check_id="database_schema",
            name="数据库版本",
            scope="persistence",
            remediation="执行 python -m src.platform.database.bootstrap apply（Windows 为 scripts\\windows\\bootstrap.ps1 -Action apply），再重新检查。",
            probe=probe,
        )

    def _check_redis(self) -> PatrolReadinessCheck:
        def probe():
            from src.auth.token_store import get_redis

            client = get_redis()
            if client is None or not client.ping():
                raise RuntimeError("Redis PING 失败")
            return "ok", "Redis 连接正常", {}

        return self._run_check(
            check_id="redis",
            name="运行状态存储",
            scope="coordination",
            remediation="检查 Redis 服务、密码和网络连通性。",
            probe=probe,
        )

    def _check_celery_broker(self) -> PatrolReadinessCheck:
        def probe():
            from kombu import Connection

            from src.common.config import get_settings

            broker_url = get_settings().CELERY_BROKER_URL
            with Connection(broker_url, connect_timeout=3) as connection:
                connection.connect()
                transport = str(connection.transport.driver_name or "unknown")
            return "ok", "Celery Broker 可达", {"transport": transport}

        return self._run_check(
            check_id="celery_broker",
            name="任务消息队列",
            scope="scheduling",
            remediation="检查 RabbitMQ/Redis Broker、账号和网络连通性。",
            probe=probe,
        )

    def _check_celery_queues(
        self,
        required_queues: list[str],
        *,
        executing_worker_queues: list[str] | None = None,
    ) -> PatrolReadinessCheck:
        queues = sorted({str(item).strip() for item in required_queues if str(item).strip()})
        current_task_queues = sorted(
            {
                str(item).strip()
                for item in executing_worker_queues or []
                if str(item).strip() in queues
            }
        )

        def probe():
            from src.core.celery_tasks.celery_app import celery
            from src.core.celery_tasks.celery_exec import CELERY_INSPECT_TIMEOUT

            inspect_queues = [queue for queue in queues if queue not in current_task_queues]
            responses = {}
            if inspect_queues:
                responses = (
                    celery.control.inspect(timeout=CELERY_INSPECT_TIMEOUT).active_queues() or {}
                )
            if not responses and inspect_queues:
                raise RuntimeError("没有 Celery Worker 响应队列检查")
            consumers: dict[str, list[str]] = {
                queue: (["current-task@trusted-delivery"] if queue in current_task_queues else [])
                for queue in queues
            }
            observed: dict[str, list[str]] = {}
            for worker_name, worker_queues in responses.items():
                names = sorted(
                    {
                        str(item.get("name") or "").strip()
                        for item in (worker_queues or [])
                        if str(item.get("name") or "").strip()
                    }
                )
                observed[str(worker_name)] = names
                for queue in queues:
                    if queue in names:
                        consumers[queue].append(str(worker_name))
            missing = [queue for queue, workers in consumers.items() if not workers]
            detail = {
                "required_queues": queues,
                "queue_consumers": consumers,
                "workers": observed,
                "current_task_queues": current_task_queues,
            }
            if missing:
                return (
                    "blocked",
                    f"巡检队列没有可用 Worker：{', '.join(missing)}",
                    {**detail, "missing_queues": missing},
                )
            return "ok", "巡检队列已有可用 Worker", detail

        return self._run_check(
            check_id="celery_patrol_queues",
            name="巡检执行 Worker",
            scope="scheduling",
            remediation="启动订阅所需巡检队列的 Celery Worker，并核对 Worker 代码版本。",
            probe=probe,
        )

    def _check_celery_beat(self) -> PatrolReadinessCheck:
        def probe():
            from src.core.celery_tasks.beat_health import read_celery_beat_heartbeat

            heartbeat = read_celery_beat_heartbeat()
            detail = heartbeat.to_dict()
            if heartbeat.status != "ok":
                return (
                    "blocked",
                    f"Celery Beat 心跳状态为 {heartbeat.status}",
                    detail,
                )
            return (
                "ok",
                f"Celery Beat 心跳正常，最近一次为 {heartbeat.age_seconds:.1f} 秒前",
                detail,
            )

        return self._run_check(
            check_id="celery_beat",
            name="巡检恢复调度",
            scope="scheduling",
            remediation="启动 Celery Beat，并确认 netops.health 队列有 Worker 消费。",
            probe=probe,
        )

    def _check_minio(self) -> PatrolReadinessCheck:
        def probe():
            from src.infrastructure.storage.minio_client import get_minio_storage

            storage = get_minio_storage()
            if not storage.is_ready() or storage._client is None:  # noqa: SLF001
                raise RuntimeError("MinIO 客户端未就绪")
            bucket = str(storage.bucket_name or "")
            if not bucket or not storage._client.bucket_exists(bucket):  # noqa: SLF001
                raise RuntimeError(f"MinIO Bucket 不可用：{bucket or '未配置'}")
            return "ok", f"巡检交付 Bucket '{bucket}' 可访问", {"bucket": bucket}

        return self._run_check(
            check_id="minio",
            name="巡检交付存储",
            scope="delivery",
            remediation="检查 MinIO 服务、Bucket、访问密钥和存储空间。",
            probe=probe,
        )

    def _check_workflow(self, workflow_name: str, launch_mode: str) -> PatrolReadinessCheck:
        def probe():
            if launch_mode == "direct":
                from src.core.celery_tasks.celery_app import celery as app
                from src.apps.inspection.backend import online_tasks  # noqa: F401

                names = ["netops.inspection.execute_online", "netops.inspection.recover_online"]
                missing = [name for name in names if name not in app.tasks]
                queues = sorted(
                    {str(app.amqp.router.route({}, name).get("queue").name) for name in names}
                )
                return (
                    "blocked" if missing else "ok",
                    "巡检固定任务未注册" if missing else "巡检固定业务任务已就绪",
                    {"required_queues": queues, "tasks": names, "missing_tasks": missing},
                )

            from src.core.skills.celery_routing import resolve_skill_celery_queue
            from src.core.workflows.registry import get_template
            from src.skills.skill_manager import get_skill_manager

            template = get_template(workflow_name)
            if template is None:
                return (
                    "blocked",
                    f"巡检 Workflow 不存在：{workflow_name}",
                    {"workflow_name": workflow_name, "required_queues": []},
                )
            if not template.steps:
                return (
                    "blocked",
                    f"巡检 Workflow 没有可执行步骤：{workflow_name}",
                    {"workflow_name": workflow_name, "required_queues": []},
                )
            skills = {
                str(item.get("name") or ""): item
                for item in get_skill_manager().list_all_skills()
                if item.get("name")
            }
            required_skills = sorted(
                {step.skill_name for step in template.steps if not step.is_subworkflow}
            )
            missing = [name for name in required_skills if name not in skills]
            disabled = [
                name
                for name in required_skills
                if name in skills and not bool(skills[name].get("enabled", True))
            ]
            required_queues = sorted(
                {resolve_skill_celery_queue(name) for name in required_skills}
                | {self.default_patrol_queue}
            )
            detail = {
                "workflow_name": workflow_name,
                "workflow_version": template.version,
                "step_count": len(template.steps),
                "skills": required_skills,
                "missing_skills": missing,
                "disabled_skills": disabled,
                "required_queues": required_queues,
            }
            if missing or disabled:
                reasons = []
                if missing:
                    reasons.append(f"未注册 Skill: {', '.join(missing)}")
                if disabled:
                    reasons.append(f"已禁用 Skill: {', '.join(disabled)}")
                return "blocked", "；".join(reasons), detail
            return (
                "ok",
                f"Workflow {workflow_name} 的 {len(template.steps)} 个步骤已就绪",
                detail,
            )

        return self._run_check(
            check_id="execution" if launch_mode == "direct" else "workflow",
            name="巡检执行任务",
            scope="execution",
            remediation="重新部署巡检业务包并检查 Worker 任务和队列注册。",
            probe=probe,
        )


__all__ = ["PatrolProductionReadinessService", "PatrolReadinessCheck"]

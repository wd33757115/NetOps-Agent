# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import re
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(ENV_FILE)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_ignore_empty=True, extra="ignore")

    LLM_PROVIDER: str = "deepseek"
    # Parser generation uses an OpenAI-compatible provider boundary.  Keep the
    # legacy DeepSeek settings as the default so existing deployments do not
    # need to migrate configuration in lock-step.
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = ""
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    LLM_MODEL: str = "deepseek-v4-pro"
    LLM_TEMPERATURE: float = 0.1
    LLM_REQUEST_TIMEOUT_SECONDS: int = 240
    # Parser generation has an explicit wall-clock budget in addition to the
    # provider SDK's connect/read/write timeout.
    PARSER_MODEL_REQUEST_HARD_TIMEOUT_SECONDS: int = 480
    PARSER_GENERATION_TOTAL_TIMEOUT_SECONDS: int = 1500
    PARSER_MODEL_CALL_HEARTBEAT_SECONDS: int = 15
    PARSER_LLM_MAX_COMPLETION_TOKENS: int = 32768
    # A single agent turn may inspect local evidence and run several local
    # tools before it submits one immutable candidate to the outer validator.
    PARSER_AGENT_MAX_TOOL_ROUNDS: int = 6
    PARSER_LLM_REVIEW_MAX_COMPLETION_TOKENS: int = 8192
    # Optional HMAC key for stable Parser-sample pseudonyms. Empty reuses JWT.
    PARSER_SANITIZATION_SECRET: str = ""
    PARSER_GENERATION_TASK_SOFT_TIME_LIMIT_SECONDS: int = 1740
    PARSER_GENERATION_TASK_TIME_LIMIT_SECONDS: int = 1800

    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "netops_agent"
    POSTGRES_USER: str = "netops"
    POSTGRES_PASSWORD: str = "netops123456"
    # Per process, per engine. Windows solo workers use a smaller explicit budget.
    POSTGRES_POOL_SIZE: int = Field(default=5, ge=1, le=50)
    POSTGRES_MAX_OVERFLOW: int = Field(default=5, ge=0, le=50)
    POSTGRES_POOL_TIMEOUT: int = Field(default=10, ge=1, le=120)
    POSTGRES_CONNECT_TIMEOUT: int = Field(default=10, ge=1, le=60)

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str | None = None

    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"
    CELERY_TASK_SOFT_TIME_LIMIT: int = 300
    CELERY_TASK_TIME_LIMIT: int = 360
    CELERY_MAX_RETRIES: int = 3
    # 空字符串表示自动：Windows → solo，Linux/macOS → prefork
    CELERY_WORKER_POOL: str = ""
    CELERY_WORKER_QUEUES: str = (
        "netops.default,netops.firewall,netops.device,netops.health,netops.patrol,netops.ai"
    )
    CELERY_WORKER_ROLE: str = "all"
    CELERY_BEAT_HEARTBEAT_INTERVAL_SECONDS: int = 15
    CELERY_BEAT_HEARTBEAT_STALE_AFTER_SECONDS: int = 45
    CELERY_BEAT_HEARTBEAT_TTL_SECONDS: int = 180

    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_SECURE: bool = False
    MINIO_BUCKET_NAME: str = "netops-files"
    # 面向用户/ITSM 的公网入口（无尾斜杠），如 https://netops.example.com
    PUBLIC_APP_URL: str = ""
    # 下载链接 HMAC 密钥（默认同 JWT_SECRET_KEY）
    ARTIFACT_DOWNLOAD_SECRET: str = ""

    STORAGE_MAX_FILE_BYTES: int = 500 * 1024 * 1024  # 单文件 500MB
    STORAGE_MAX_USER_BYTES: int = 20 * 1024 * 1024 * 1024  # 个人空间 20GB

    # 巡检 Snapshot / Change / Event MVP 存储
    @field_validator("STORAGE_MAX_FILE_BYTES", "STORAGE_MAX_USER_BYTES", mode="before")
    @classmethod
    def parse_storage_byte_limit(cls, value):
        if value is None or isinstance(value, int):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return value
            try:
                return int(text)
            except ValueError:
                pass
            if re.fullmatch(r"[\d\s*+\-/()]+", text):
                return int(eval(text, {"__builtins__": {}}, {}))
        return value


    FASTAPI_HOST: str = "0.0.0.0"
    FASTAPI_PORT: int = 8000


    LANGFUSE_HOST: str | None = None
    LANGFUSE_PUBLIC_KEY: str | None = None
    LANGFUSE_SECRET_KEY: str | None = None

    ITSM_WEBHOOK_SECRET: str = "itsm-secret-2026"
    ITSM_CALLBACK_URL: str | None = None

    ENFORCE_BFF_ORIGIN: bool | None = None

    PROJECT_NAME: str = "NetOps-MultiAgent"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "console"  # console | json

    # Embedding（Skill Catalog / 分级路由）
    EMBEDDING_MODEL: str = "BAAI/bge-m3"
    EMBEDDING_DEVICE: str = "cpu"

    # Skill Catalog + 分级路由（Phase 2）
    SKILL_CATALOG_ENABLED: bool = True
    SKILL_CATALOG_USE_TIERED_ROUTING: bool = True
    SKILL_CATALOG_SEMANTIC_ROUTING_ENABLED: bool = False
    SKILL_CATALOG_SEMANTIC_MIN_SCORE: float = 0.72
    PRE_PROCESS_TOP_K: int = 5
    PRE_PROCESS_HARD_LIMIT: int = 8

    # Skill 执行限流
    SKILL_RATE_LIMIT_ENABLED: bool = True
    SKILL_RATE_LIMIT_PER_USER: int = 30
    SKILL_RATE_LIMIT_PER_SKILL: int = 200

    # Skill 治理与归档（Phase 3）
    SKILL_GOVERNANCE_ENABLED: bool = True
    PLATFORM_VERSION: str = "1.0.0"
    SKILL_EXEC_ARCHIVE_ENABLED: bool = True
    SKILL_EXEC_ARCHIVE_AFTER_DAYS: int = 90
    SKILL_EXEC_ARCHIVE_BATCH_SIZE: int = 500

    # Redis Streams EventBus（Phase 1）
    EVENT_BUS_ENABLED: bool = True
    EVENT_BUS_STREAM_MAXLEN: int = 100_000
    EVENT_BUS_CONSUMER_BATCH_SIZE: int = 20
    EVENT_BUS_CONSUMER_BLOCK_MS: int = 500
    EVENT_BUS_PENDING_MIN_IDLE_MS: int = 60_000
    EVENT_BUS_RECLAIM_BATCH_SIZE: int = 20
    EVENT_BUS_PROCESSING_LEASE_MS: int = 120_000
    EVENT_BUS_MAX_PROCESSING_RETRIES: int = 3
    EVENT_BUS_POLL_IDLE_SEC: float = 1.0
    # 生产默认 true：EventBus 与 DB 通知双写，Consumer 未启动时用户仍能收到站内信
    EVENT_BUS_DIRECT_NOTIFY_FALLBACK: bool = True

    # 巡检并发与规则运行时治理
    PATROL_MAX_CONCURRENCY: int = 50
    PATROL_SITE_MAX_CONCURRENCY: int = 10
    PATROL_DISTRIBUTED_CONCURRENCY_ENABLED: bool = True
    PATROL_CONCURRENCY_FAIL_CLOSED: bool = True
    PATROL_CONCURRENCY_LEASE_SECONDS: int = 180
    PATROL_CONCURRENCY_WAIT_SECONDS: int = 60
    PATROL_RUN_LEASE_SECONDS: int = 900
    PATROL_WORKFLOW_RUNNING_STALE_SECONDS: int = 900
    PATROL_WORKFLOW_QUEUED_STALE_SECONDS: int = 14_400
    PATROL_LOCAL_OUTPUT_RETENTION_HOURS: int = 72
    PATROL_OFFLINE_CURRENT_MAX_AGE_HOURS: int = 24
    PATROL_CURRENT_AUTHORITY_REQUEST_TTL_HOURS: int = 4
    # 批次结果深分页游标：空密钥时复用 JWT_SECRET_KEY。
    CHECK_ITEM_RESULT_CURSOR_SECRET: str = ""
    CHECK_ITEM_RESULT_CURSOR_TTL_SECONDS: int = 3600
    CHECK_ITEM_RESULT_MAX_OFFSET: int = 1000
    # block=exclude from ready with error; skip=exclude with skip code; warn=allow launch
    PATROL_PARSER_COVERAGE_POLICY: str = "skip"
    REDIS_RECONNECT_BACKOFF_SECONDS: int = 5
    EVENT_RULE_ALLOW_BUILTIN_FALLBACK: bool = False
    # Deprecated compatibility flag. Production patrol is evidence-only and
    # cannot create Parser generation candidates.
    PRODUCTION_PARSER_BACKLOG_ENABLED: bool = False
    PARSER_LLM_GENERATION_ENABLED: bool = False
    PARSER_BACKFILL_ENABLED: bool = False
    PARSER_BACKLOG_ALERT_THRESHOLD: int = 20
    PARSER_CANDIDATE_STALE_MINUTES: int = 60
    # Stale parser-job reclamation: dispatched-but-unclaimed jobs and jobs whose
    # worker died after marking them running are treated as failed past these ages.
    # A queued generation may legitimately wait behind several 30-minute
    # Parser jobs when the AI worker uses the Windows solo pool.  Keep the
    # dispatch watchdog above that normal queueing window; running jobs still
    # use the separate execution timeout below.
    PARSER_JOB_PENDING_STALE_MINUTES: int = 60
    PARSER_JOB_RUNNING_STALE_MINUTES: int = 30
    PARSER_BLOCKED_RATIO_ALERT_THRESHOLD: float = 0.2
    # Production template_missing / parse_failed → gap ledger only (no auto LLM).
    PRODUCTION_GAP_LEDGER_ENABLED: bool = True
    PRODUCTION_GAP_LEDGER_JOB_THRESHOLD: int = 3
    # Impact-weighted promote: gaps with production_impact >= this value promote at 1 occurrence.
    PRODUCTION_GAP_LEDGER_IMPACT_FAST_TRACK: int = 50
    # Quality gate for auto-built rule generation contexts (P0-2 auto_build_context).
    RULE_CONTEXT_MIN_RECORDS: int = 10
    RULE_CONTEXT_MIN_FIELD_FILL_RATE: float = 0.6

    # 与 Django SIMPLE_JWT 共用（默认与 SECRET_KEY 一致，生产务必在 .env 中显式设置）
    JWT_SECRET_KEY: str = "django-insecure-default-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    NETOPS_APP_INTERNAL_SECRET: str = ""
    NETOPS_AUTHORITY_URL: str = "http://127.0.0.1:8001"

    @property
    def postgres_url(self) -> str:
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @property
    def postgres_url_asyncpg(self) -> str:
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @property
    def redis_url(self) -> str:
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"


settings = Settings()


def get_settings() -> Settings:
    return settings

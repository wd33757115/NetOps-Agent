# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import re

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    LLM_PROVIDER: str = "deepseek"
    DEEPSEEK_API_KEY: str = ""
    LLM_MODEL: str = "deepseek-chat"
    LLM_TEMPERATURE: float = 0.1

    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "netops_agent"
    POSTGRES_USER: str = "netops"
    POSTGRES_PASSWORD: str = "netops123456"

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
    CELERY_WORKER_QUEUES: str = "netops.default,netops.firewall,netops.device"

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

    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION: str = "netops_knowledge"

    FASTAPI_HOST: str = "0.0.0.0"
    FASTAPI_PORT: int = 8000

    STREAMLIT_SERVER_PORT: int = 8501

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
    USE_SUPERVISOR_V2: bool = True

    # Embedding（Skill Catalog / 分级路由）
    EMBEDDING_MODEL: str = "BAAI/bge-m3"
    EMBEDDING_DEVICE: str = "cpu"

    # Skill Catalog + 分级路由（Phase 2）
    SKILL_CATALOG_ENABLED: bool = True
    SKILL_CATALOG_USE_TIERED_ROUTING: bool = True
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
    EVENT_BUS_POLL_IDLE_SEC: float = 1.0
    # 生产默认 true：EventBus 与 DB 通知双写，Consumer 未启动时用户仍能收到站内信
    EVENT_BUS_DIRECT_NOTIFY_FALLBACK: bool = True

    # 与 Django SIMPLE_JWT 共用（默认与 SECRET_KEY 一致，生产务必在 .env 中显式设置）
    JWT_SECRET_KEY: str = "django-insecure-default-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"

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

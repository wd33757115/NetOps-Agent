# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import sys
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

BASE_DIR = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.common.config import get_settings

settings = get_settings()


engine = create_engine(
    settings.postgres_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=False
)

async_engine = create_async_engine(
    settings.postgres_url_asyncpg,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=False
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
AsyncSessionLocal = sessionmaker(
    async_engine, class_=AsyncSession, autocommit=False, autoflush=False
)


@contextmanager
def get_db_session():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@asynccontextmanager
async def get_async_db_session():
    session = AsyncSessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def init_postgres_schema():
    from langgraph.checkpoint.postgres import PostgresSaver

    print("[OK] Initializing PostgreSQL Schema for LangGraph...")

    with PostgresSaver.from_conn_string(settings.postgres_url) as saver:
        saver.setup()

    print("[OK] PostgreSQL LangGraph Schema initialized")


def verify_postgres_connection() -> bool:
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            return result.scalar() == 1
    except Exception as e:
        print(f"[ERROR] PostgreSQL connection failed: {e}")
        return False


def require_postgres_connection() -> None:
    """Raise when PostgreSQL cannot serve an authoritative operation."""
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        if result.scalar() != 1:
            raise RuntimeError("PostgreSQL health check returned an unexpected result")


def get_postgres_saver():
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver

    conn = psycopg.connect(settings.postgres_url, autocommit=True)
    return PostgresSaver(conn)


def get_async_postgres_saver():
    import psycopg
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    conn = psycopg.AsyncConnection.connect(settings.postgres_url, autocommit=True)
    return AsyncPostgresSaver(conn)

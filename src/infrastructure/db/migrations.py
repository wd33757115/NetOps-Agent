"""Alembic migration helpers for the authoritative PostgreSQL schema."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from src.infrastructure.db.postgres import engine

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BASELINE_REVISION = "0001_platform_baseline"


def alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return config


def head_revision() -> str:
    return str(ScriptDirectory.from_config(alembic_config()).get_current_head())


def current_revision() -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def has_legacy_application_schema() -> bool:
    return "netops_conversations" in inspect(engine).get_table_names()


def adopt_legacy_schema() -> bool:
    """Stamp a pre-Alembic application database after an explicit operator request."""
    if current_revision() is not None or not has_legacy_application_schema():
        return False
    command.stamp(alembic_config(), BASELINE_REVISION)
    return True


def upgrade_database(revision: str = "head") -> None:
    command.upgrade(alembic_config(), revision)


def require_database_schema_current() -> None:
    current = current_revision()
    head = head_revision()
    if current != head:
        raise RuntimeError(
            "PostgreSQL schema is not current: "
            f"current={current or 'unversioned'}, expected={head}. "
            "Run: python -m src.platform.database.bootstrap apply"
        )

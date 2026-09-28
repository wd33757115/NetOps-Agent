from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from src.common.config import get_settings
from src.infrastructure.db.models import Base
from src.core.official_documents import models as official_document_models  # noqa: F401
from src.core.patrol import report_models as patrol_report_models  # noqa: F401

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.postgres_url.replace("%", "%%"))
target_metadata = Base.metadata

# Autogeneration must not propose changes/drops to App-owned business tables.
# Existing historical revision files remain immutable and continue to replay.
from src.platform.kernel.registry import discover
app_owned_tables = {
    table for manifest in discover().values()
    for table in manifest.data.get("database", {}).get("tables", [])
}


def include_name(name: str | None, type_: str, parent_names: dict[str, str]) -> bool:
    """Leave LangGraph-managed checkpoint tables outside application migrations."""
    if type_ == "table" and name in app_owned_tables | {
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoints",
    }:
        return False
    return True


def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "table" and name in app_owned_tables)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_name=include_name,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_name=include_name,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

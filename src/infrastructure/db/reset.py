"""Database reset helpers for development and controlled maintenance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, inspect, select, text

from src.infrastructure.db.models import Base
from src.infrastructure.db.postgres import get_db_session

RESET_CONFIRMATION = "RESET BUSINESS DATA"

PRESERVED_TABLES = {
    "netops_skill_catalog",
    "netops_workflow_plugins",
    "netops_workflow_plugin_versions",
    "netops_workflow_market_templates",
    "parser_control_settings",
}


@dataclass(frozen=True)
class DatabaseResetResult:
    success: bool
    dry_run: bool
    total_rows: int
    table_counts: dict[str, int]
    deleted_tables: list[str]
    preserved_tables: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "dry_run": self.dry_run,
            "total_rows": self.total_rows,
            "table_counts": self.table_counts,
            "deleted_tables": self.deleted_tables,
            "preserved_tables": self.preserved_tables,
        }


def business_table_names(*, include_configuration: bool = False) -> list[str]:
    tables = sorted(Base.metadata.tables)
    if include_configuration:
        return tables
    return [name for name in tables if name not in PRESERVED_TABLES]


def reset_business_data(
    *,
    dry_run: bool = True,
    confirmation: str | None = None,
    include_configuration: bool = False,
) -> DatabaseResetResult:
    """Delete business rows while preserving schema and selected static config."""

    if not dry_run and confirmation != RESET_CONFIRMATION:
        raise ValueError(f'confirmation must be "{RESET_CONFIRMATION}"')

    target_names = set(business_table_names(include_configuration=include_configuration))
    preserved_names = sorted(set(Base.metadata.tables) - target_names)

    with get_db_session() as db:
        existing_names = set(inspect(db.get_bind()).get_table_names())
        tables = [
            table
            for table in reversed(Base.metadata.sorted_tables)
            if table.name in target_names and table.name in existing_names
        ]
        table_counts: dict[str, int] = {}
        for table in tables:
            count_statement = select(func.count()).select_from(table)
            table_counts[table.name] = int(
                db.execute(count_statement).scalar() or 0
            )

        if not dry_run and tables:
            dialect_name = str(db.get_bind().dialect.name or "").casefold()
            if dialect_name == "postgresql":
                identifier_preparer = db.get_bind().dialect.identifier_preparer
                table_names = ", ".join(
                    identifier_preparer.quote(table.name) for table in tables
                )
                db.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY"))
            else:
                # SQLite is used by focused repository tests and has no
                # PostgreSQL TRUNCATE syntax.  Deleting in reverse dependency
                # order preserves the same business-data reset semantics.
                for table in tables:
                    db.execute(table.delete())

    return DatabaseResetResult(
        success=True,
        dry_run=dry_run,
        total_rows=sum(table_counts.values()),
        table_counts=dict(sorted(table_counts.items())),
        deleted_tables=sorted(table_counts),
        preserved_tables=preserved_names,
    )

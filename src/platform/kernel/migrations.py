"""Package-local revision chains, shared environment, isolated version tables.

No stamp, downgrade or automatic modification of the historical platform chain.
"""

import os
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL

from .registry import ROOT


def business_engine():
    url = os.getenv("NETOPS_PACKAGE_DATABASE_URL")
    if url:
        return configured_engine(url)
    return configured_engine(
        URL.create(
            "postgresql+psycopg2",
            username=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=os.getenv("POSTGRES_DB", "netops_agent"),
        )
    )


@lru_cache
def configured_engine(url):
    return create_engine(url, pool_pre_ping=True)


def version_table(manifest):
    name = "alembic_app_" + manifest.id.replace("-", "_")
    if len(name) > 63:
        raise ValueError("Application id too long for migration version table")
    return name


def configuration(manifest):
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parent / "migration_env").replace("%", "%%")
    )
    config.set_main_option(
        "version_locations",
        str(manifest.path.parent / manifest.data["database"]["migrations"] / "versions").replace(
            "%", "%%"
        ),
    )
    config.set_main_option("path_separator", "os")
    script = ScriptDirectory.from_config(config)
    if script.get_heads() != [manifest.data["database"]["head"]]:
        raise ValueError("Manifest head must equal the single package migration head")
    return config, script


@contextmanager
def migration_connection(engine):
    with engine.connect() as connection:
        if connection.dialect.name == "sqlite":
            # sqlite3's legacy transaction mode does not BEGIN for DDL.
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        elif connection.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(728031922)"))
        else:
            raise ValueError("Package migration supports PostgreSQL and SQLite only")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def legacy_ready(connection, database):
    minimum = database.get("minimum_revision")
    if minimum:
        script = ScriptDirectory(str(ROOT / "migrations"))
        current = MigrationContext.configure(connection).get_current_heads()
        for head in current:
            if minimum in {r.revision for r in script.iterate_revisions(head, "base")}:
                break
        else:
            raise ValueError(f"Run platform migrations first; required revision: {minimum}")
    existing = set(inspect(connection).get_table_names())
    if not set(database.get("tables", [])) <= existing:
        raise ValueError("Required historical business tables are missing")


def migrate(manifest, engine=None, *, check=False):
    database = manifest.data.get("database", {"mode": "none"})
    if database["mode"] == "none":
        return None
    engine = engine or business_engine()
    with migration_connection(engine) as connection:
        if database["mode"] == "legacy":
            legacy_ready(connection, database)
            return "legacy"
        config, script = configuration(manifest)
        table = version_table(manifest)
        context = MigrationContext.configure(connection, opts={"version_table": table})
        current = context.get_current_heads()
        target = database["head"]
        if current and (
            len(current) != 1 or current[0] not in {r.revision for r in script.walk_revisions()}
        ):
            raise ValueError("Unknown database revision; downgrade/automatic stamp is forbidden")
        if check:
            if current != (target,):
                raise ValueError("Package database migration is not ready; run python -m src.platform.database.bootstrap apply")
        else:
            # A new chain cannot silently adopt already existing tables.
            existing = set(inspect(connection).get_table_names())
            inherited = database.get("handoff", {})
            if not current and inherited:
                if set(inherited.get("columns", {})) != set(inherited["tables"]):
                    raise ValueError(
                        "Handoff requires an explicit column contract for each inherited table"
                    )
                legacy_ready(
                    connection,
                    {"minimum_revision": inherited["revision"], "tables": inherited["tables"]},
                )
                for name, expected in inherited["columns"].items():
                    columns = {c["name"]: c for c in inspect(connection).get_columns(name)}
                    if not expected or not set(expected) <= set(columns):
                        raise ValueError(f"Handoff column contract mismatch: {name}")
                    for column, specification in expected.items():
                        actual = columns[column]
                        if (
                            str(actual["type"]).lower() != specification["type"].lower()
                            or actual["nullable"] != specification["nullable"]
                        ):
                            raise ValueError(
                                f"Handoff column type/nullability mismatch: {name}.{column}"
                            )
            collisions = (set(database["tables"]) & existing) - set(inherited.get("tables", []))
            if not current and collisions:
                raise ValueError(
                    f"Tables already exist without this migration chain: {sorted(collisions)}"
                )
            config.attributes.update(connection=connection, version_table=table)
            command.upgrade(config, target)
        if not set(database["tables"]) <= set(inspect(connection).get_table_names()):
            raise ValueError("Migration did not create all declared tables")
        return target

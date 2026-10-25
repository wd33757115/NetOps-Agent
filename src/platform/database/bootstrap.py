"""Single administrative entry point: python -m src.platform.database.bootstrap apply|check."""
from __future__ import annotations

import argparse
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import inspect, text

ROOT = Path(__file__).resolve().parents[3]
LOCK_ID = 728031924


@contextmanager
def bootstrap_lock(engine):
    # Session lock survives Alembic's autocommit blocks (concurrent indexes).
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        if not connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_ID}).scalar():
            raise ValueError("Database bootstrap is already running; retry after it completes")
        try:
            yield
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_ID})


def django_setup():
    import django
    from src.common.config import get_settings
    settings = get_settings()
    # The platform and authority must use the same database, including when the
    # command is run without a shell that previously loaded .env.
    for key in ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
        os.environ.setdefault(key, str(getattr(settings, key)))
    sys.path.insert(0, str(ROOT / "web/django_backend"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_backend.settings")
    django.setup()
    from django.db import connection
    if connection.vendor != "postgresql":
        raise ValueError("Platform bootstrap requires PostgreSQL")


def held_packages(manifests):
    """Live applications that cannot run as they are; they stay closed, never block."""
    from django.db import connection
    from bff.app_lifecycle import live_changes
    from bff.models import AppInstallation
    if AppInstallation._meta.db_table not in connection.introspection.table_names():
        return {}
    return live_changes({r.app_id: r for r in AppInstallation.objects.all()}, manifests)


def seed_once(engine, owner, version, callback, *, check=False):
    with engine.connect() as connection:
        completed = connection.execute(text(
            "SELECT 1 FROM netops_bootstrap_steps WHERE owner=:owner AND version=:version"
        ), {"owner": owner, "version": str(version)}).scalar()
    if completed:
        return
    if check:
        raise ValueError(f"Initial data missing for {owner}@{version}; run bootstrap apply")
    callback()  # Hooks must be idempotent; failures are never marked successful.
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO netops_bootstrap_steps(owner, version, completed_at) "
            "VALUES (:owner, :version, CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING"
        ), {"owner": owner, "version": str(version)})


def seed_platform():
    from src.skills.bootstrap import bootstrap_skills
    bootstrap_skills(force=True, persist_catalog=True)


def run(action, *, adopt_existing=False):
    from src.infrastructure.db.postgres import engine, require_postgres_connection
    from src.infrastructure.db.migrations import (
        adopt_legacy_schema, upgrade_database, require_database_schema_current,
    )
    from src.platform.kernel.registry import discover, load_symbol
    from src.platform.kernel.migrations import migrate
    from django.core.management import call_command
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    require_postgres_connection()
    django_setup()
    manifests = discover()
    if os.getenv("NETOPS_PACKAGE_DATABASE_URL"):
        raise ValueError("Unified bootstrap uses POSTGRES_*; remove NETOPS_PACKAGE_DATABASE_URL")
    check = action == "check"
    # Platform readiness and application readiness are separate: a platform fault
    # stops startup; an application fault keeps only that application closed.
    issues = {}

    def execute():
        if check:
            require_database_schema_current()
        else:
            if adopt_existing:
                adopt_legacy_schema()
            upgrade_database()
            with engine.begin() as namespace:
                namespace.execute(text("CREATE SCHEMA IF NOT EXISTS django"))
        # Check the namespace before Django can fall back to public search_path.
        if "django" not in inspect(engine).get_schema_names():
            raise ValueError("Missing authority schema; run bootstrap apply")
        if check:
            executor = MigrationExecutor(connection)
            executor.loader.check_consistent_history(connection)
            if executor.migration_plan(executor.loader.graph.leaf_nodes()):
                raise ValueError("Authority migrations pending; run bootstrap apply")
        else:
            call_command("migrate", interactive=False, verbosity=0)
            from bff.default_admin import PASSWORD, USERNAME, ensure_default_admin
            if ensure_default_admin():
                print(f"[OK] Default administrator {USERNAME} / {PASSWORD} created; "
                      "change the password after the first login", flush=True)
        from bff.app_lifecycle import lifecycle_lock
        with lifecycle_lock():
            # A live package with changed code keeps its deployed schema until it
            # is drained and upgraded; its new revisions must not run under it.
            issues.update(held_packages(manifests))
            for manifest in manifests.values():
                if manifest.id in issues:
                    continue
                try:
                    # Schema ownership stays with the package; only the platform
                    # executes its declared revision chain, in dependency order.
                    head = migrate(manifest, engine=engine, check=check)
                    print(f"[OK] schema {manifest.id}: {head or 'platform services'}", flush=True)
                except Exception as exc:
                    issues[manifest.id] = f"{type(exc).__name__}: {exc}"
            seed_once(engine, "platform.skills", "1", seed_platform, check=check)
            for manifest in manifests.values():
                database = manifest.data.get("database", {})
                if manifest.id in issues or not database.get("bootstrap"):
                    continue
                try:
                    seed_once(engine, manifest.id, database["bootstrap_version"],
                              lambda m=manifest: load_symbol(m.data["database"]["bootstrap"])(),
                              check=check)
                except Exception as exc:
                    issues[manifest.id] = f"{type(exc).__name__}: {exc}"
        if not check:
            # Package registration is administrative initialization, not startup.
            # It skips held packages and reports per-package failures itself.
            from io import StringIO
            report = StringIO()
            call_command("app_packages", "prepare", verbosity=0, stdout=report)
            for line in report.getvalue().splitlines():
                print(line, flush=True)
                app_id, _, rest = line.partition(" ")
                if rest.startswith(("failed: ", "skipped: ")):
                    issues.setdefault(app_id, rest.split(": ", 1)[1])
        else:
            from bff.models import AppInstallation
            records = {r.app_id: r for r in AppInstallation.objects.all()}
            for manifest in manifests.values():
                record = records.get(manifest.id)
                if manifest.id not in issues and (
                    not record or record.deployment_digest != manifest.digest
                    or record.state not in {"installed", "disabled", "enabled"}
                ):
                    issues[manifest.id] = "package registration pending; run bootstrap apply"
    try:
        if check:
            execute()
        else:
            with bootstrap_lock(engine):
                execute()
        print(f"[OK] Platform database {action} complete", flush=True)
        for app_id, reason in issues.items():
            print(f"[APP] {app_id} unavailable: {reason}", flush=True)
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["apply", "check"])
    parser.add_argument("--adopt-existing", action="store_true",
                        help="Explicitly adopt a verified pre-Alembic database; never used during startup")
    args = parser.parse_args(argv)
    if args.adopt_existing and args.action != "apply":
        parser.error("--adopt-existing requires apply")
    run(args.action, adopt_existing=args.adopt_existing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

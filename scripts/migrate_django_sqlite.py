#!/usr/bin/env python3
"""Migrate Django users and RBAC data from SQLite to PostgreSQL."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import psycopg2


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DJANGO_ROOT = PROJECT_ROOT / "web" / "django_backend"
DEFAULT_SQLITE = DJANGO_ROOT / "db.sqlite3"


def _run_manage(*args: str, env: dict[str, str]) -> None:
    subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=DJANGO_ROOT,
        env=env,
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", default=str(DEFAULT_SQLITE))
    parser.add_argument(
        "--fixture",
        default=str(
            PROJECT_ROOT
            / ".runtime"
            / "migration-backup"
            / "django-auth-data.json"
        ),
    )
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite).resolve()
    fixture_path = Path(args.fixture).resolve()
    if not sqlite_path.is_file():
        raise FileNotFoundError(sqlite_path)
    fixture_path.parent.mkdir(parents=True, exist_ok=True)

    sqlite_env = dict(os.environ)
    sqlite_env["DJANGO_DATABASE_ENGINE"] = "sqlite"
    sqlite_env["DJANGO_SQLITE_PATH"] = str(sqlite_path)
    with fixture_path.open("w", encoding="utf-8") as output:
        subprocess.run(
            [
                sys.executable,
                "manage.py",
                "dumpdata",
                "auth.user",
                "auth.group",
                "--natural-foreign",
                "--natural-primary",
                "--indent",
                "2",
            ],
            cwd=DJANGO_ROOT,
            env=sqlite_env,
            stdout=output,
            check=True,
        )

    connection = psycopg2.connect(
        dbname=os.getenv("POSTGRES_DB", "netops_agent"),
        user=os.getenv("POSTGRES_USER", "netops"),
        password=os.getenv("POSTGRES_PASSWORD", "netops123456"),
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("CREATE SCHEMA IF NOT EXISTS django")
        connection.commit()
    finally:
        connection.close()

    postgres_env = dict(os.environ)
    postgres_env["DJANGO_DATABASE_ENGINE"] = "postgresql"
    _run_manage("migrate", "--noinput", env=postgres_env)
    _run_manage("loaddata", str(fixture_path), env=postgres_env)
    _run_manage("check", env=postgres_env)
    print(f"Django data migrated to PostgreSQL; fixture: {fixture_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

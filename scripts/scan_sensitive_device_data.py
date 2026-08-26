"""Scan PostgreSQL for residual device-identifying data without printing values."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from src.infrastructure.db.postgres import get_db_session

DEFAULT_TERMS: tuple[str, ...] = ()
SEARCHABLE_TYPES = (
    "character",
    "character varying",
    "text",
    "json",
    "jsonb",
)
SEARCHABLE_ARRAY_TYPES = ("_text", "_varchar", "_bpchar")
EXCLUDED_DIRECTORY_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
    "site-packages",
    "venv",
}


def _columns_to_scan(db: Any, schema: str) -> list[dict[str, str]]:
    statement = text(
        """
        SELECT table_schema, table_name, column_name, data_type, udt_name
        FROM information_schema.columns
        WHERE table_schema = :schema
          AND (
              data_type = ANY(:searchable_types)
              OR (data_type = 'ARRAY' AND udt_name = ANY(:searchable_array_types))
          )
        ORDER BY table_name, ordinal_position
        """
    )
    rows = db.execute(
        statement,
        {
            "schema": schema,
            "searchable_types": list(SEARCHABLE_TYPES),
            "searchable_array_types": list(SEARCHABLE_ARRAY_TYPES),
        },
    ).mappings()
    return [dict(row) for row in rows]


def _normalized_terms(values: Iterable[str] | None) -> list[str]:
    source = values or DEFAULT_TERMS
    return list(dict.fromkeys(value.strip() for value in source if value.strip()))


def scan_sensitive_device_data(
    *, schema: str = "public", terms: Iterable[str] | None = None
) -> dict[str, Any]:
    """Return only locations and counts for matching device-identifying data."""
    normalized_terms = _normalized_terms(terms)
    if not normalized_terms:
        raise ValueError("at least one non-empty search term is required")

    with get_db_session() as db:
        identifier_preparer = db.get_bind().dialect.identifier_preparer
        columns = _columns_to_scan(db, schema)
        matches: list[dict[str, Any]] = []

        for column in columns:
            table_name = identifier_preparer.quote(column["table_name"])
            column_name = identifier_preparer.quote(column["column_name"])
            qualified_table = f"{identifier_preparer.quote(schema)}.{table_name}"
            statement = text(
                "SELECT count(*) FROM "
                f"{qualified_table} "
                "WHERE strpos(lower(CAST(" + column_name + " AS text)), lower(:term)) > 0"
            )
            for term in normalized_terms:
                count = int(db.execute(statement, {"term": term}).scalar_one() or 0)
                if count:
                    matches.append(
                        {
                            "term": term,
                            "table": column["table_name"],
                            "column": column["column_name"],
                            "count": count,
                        }
                    )

    return {
        "success": True,
        "schema": schema,
        "terms": normalized_terms,
        "scanned_column_count": len(columns),
        "match_count": sum(item["count"] for item in matches),
        "matches": matches,
        "values_redacted": True,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }


def scan_project_files(
    *, project_root: Path = ROOT, terms: Iterable[str] | None = None
) -> dict[str, Any]:
    """Scan project files while excluding dependency and cache directories."""
    normalized_terms = _normalized_terms(terms)
    if not normalized_terms:
        raise ValueError("at least one non-empty search term is required")

    root = project_root.resolve()
    matches: list[dict[str, Any]] = []
    scanned_file_count = 0
    skipped_binary_file_count = 0
    skipped_error_count = 0
    lowered_terms = {term: term.casefold() for term in normalized_terms}

    for current_root, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = [
            directory
            for directory in directories
            if directory not in EXCLUDED_DIRECTORY_NAMES
        ]
        for filename in filenames:
            path = Path(current_root, filename)
            try:
                raw = path.read_bytes()
            except OSError:
                skipped_error_count += 1
                continue
            if b"\x00" in raw[:8192]:
                skipped_binary_file_count += 1
                continue

            scanned_file_count += 1
            relative_path = path.relative_to(root).as_posix()
            content = raw.decode("utf-8", errors="ignore").casefold()
            path_value = relative_path.casefold()
            for term, lowered_term in lowered_terms.items():
                content_count = content.count(lowered_term)
                path_count = path_value.count(lowered_term)
                if content_count or path_count:
                    matches.append(
                        {
                            "term": term,
                            "path": relative_path,
                            "content_count": content_count,
                            "path_count": path_count,
                        }
                    )

    return {
        "project_root": str(root),
        "excluded_directories": sorted(EXCLUDED_DIRECTORY_NAMES),
        "scanned_file_count": scanned_file_count,
        "skipped_binary_file_count": skipped_binary_file_count,
        "skipped_error_count": skipped_error_count,
        "match_count": sum(
            item["content_count"] + item["path_count"] for item in matches
        ),
        "matches": matches,
        "values_redacted": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan PostgreSQL for residual device data without exposing values."
    )
    parser.add_argument("--schema", default="public", help="PostgreSQL schema to scan")
    parser.add_argument(
        "--term",
        action="append",
        dest="terms",
        help="literal search term; repeat to add more terms",
    )
    parser.add_argument(
        "--fail-on-match",
        action="store_true",
        help="exit with status 2 when at least one residual match is found",
    )
    parser.add_argument(
        "--scope",
        choices=("all", "database", "filesystem"),
        default="all",
        help="scan PostgreSQL, project files, or both (default: all)",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=ROOT,
        help="project root for filesystem scanning",
    )
    args = parser.parse_args()

    normalized_terms = _normalized_terms(args.terms)
    database_result = (
        scan_sensitive_device_data(schema=args.schema, terms=normalized_terms)
        if args.scope in {"all", "database"}
        else None
    )
    filesystem_result = (
        scan_project_files(project_root=args.project_root, terms=normalized_terms)
        if args.scope in {"all", "filesystem"}
        else None
    )
    result = {
        "success": True,
        "terms": normalized_terms,
        "database": database_result,
        "filesystem": filesystem_result,
        "match_count": sum(
            section["match_count"]
            for section in (database_result, filesystem_result)
            if section is not None
        ),
        "values_redacted": True,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if args.fail_on_match and result["match_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

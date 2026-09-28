#!/usr/bin/env python3
"""Audit governed Markdown metadata, topic ownership, and local links."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "docs" / "document-governance.yaml"
ALLOWED_STATUSES = {"proposed", "active", "superseded", "archived"}
LINK_RE = re.compile(r"!?\[[^\]]*]\((?P<target>[^)]+)\)")
GOVERNED_INDEX_RE = re.compile(
    r"<!--\s*governed-index:\s*(?P<status>active|proposed|historical)\s*-->"
)
DOCUMENT_STATUS_RE = re.compile(
    r"<!--\s*document-status:\s*(?P<status>superseded|archived)\s*-->"
)
STATUS_BANNER_LINE_LIMIT = 20


def _date(value: Any, *, label: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD") from exc


def _load_registry(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("document governance registry must be an object")
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("unsupported document governance schema")
    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError("document governance registry requires documents")
    return payload


def _relative_doc_path(value: Any) -> Path:
    raw = str(value or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or ".." in Path(raw).parts:
        raise ValueError(f"invalid governed document path: {raw or '<empty>'}")
    return Path(raw)


def _local_link_target(source: Path, raw_target: str) -> Path | None:
    target = raw_target.strip().strip("<>")
    if not target or target.startswith("#"):
        return None
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return None
    decoded = unquote(parsed.path)
    if not decoded:
        return None
    return (source.parent / decoded).resolve()


def audit(
    *,
    registry_path: Path,
    today: date,
    all_links: bool,
    strict_unregistered: bool,
) -> dict[str, Any]:
    payload = _load_registry(registry_path)
    docs_root = registry_path.parent.resolve()
    review_days = max(int(payload.get("review_interval_days") or 120), 1)
    errors: list[str] = []
    warnings: list[str] = []
    rows: dict[str, dict[str, Any]] = {}
    active_by_topic: dict[str, list[str]] = {}

    for index, raw in enumerate(payload["documents"], start=1):
        if not isinstance(raw, dict):
            errors.append(f"documents[{index}] must be an object")
            continue
        try:
            relative = _relative_doc_path(raw.get("path"))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        key = relative.as_posix()
        if key in rows:
            errors.append(f"duplicate governed document: {key}")
            continue
        status = str(raw.get("status") or "").strip()
        topic = str(raw.get("topic") or "").strip()
        owner = str(raw.get("owner") or "").strip()
        if status not in ALLOWED_STATUSES:
            errors.append(f"{key}: invalid status {status or '<empty>'}")
        if not topic:
            errors.append(f"{key}: topic is required")
        absolute = (docs_root / relative).resolve()
        try:
            absolute.relative_to(docs_root)
        except ValueError:
            errors.append(f"{key}: path escapes docs root")
            continue
        if not absolute.is_file():
            errors.append(f"{key}: file does not exist")
        if status in {"active", "proposed"} and not owner:
            errors.append(f"{key}: {status} document requires owner")
        verified: date | None = None
        if status == "active":
            try:
                verified = _date(
                    raw.get("last_verified"),
                    label=f"{key}.last_verified",
                )
            except ValueError as exc:
                errors.append(str(exc))
            if verified is not None:
                age = (today - verified).days
                if age < 0:
                    errors.append(f"{key}: last_verified is in the future")
                elif age > review_days:
                    errors.append(
                        f"{key}: active document review is {age} days old "
                        f"(limit {review_days})"
                    )
            active_by_topic.setdefault(topic, []).append(key)
        if status == "superseded":
            replacement = str(raw.get("superseded_by") or "").strip()
            if not replacement:
                errors.append(f"{key}: superseded_by is required")
        rows[key] = {
            **raw,
            "path": key,
            "absolute": absolute,
            "status": status,
            "topic": topic,
        }

    for topic, paths in sorted(active_by_topic.items()):
        if len(paths) > 1:
            errors.append(
                f"topic {topic!r} has multiple active documents: "
                + ", ".join(sorted(paths))
            )

    for key, row in rows.items():
        if row["status"] != "superseded":
            continue
        replacement = str(row.get("superseded_by") or "").replace(
            "\\",
            "/",
        )
        replacement_row = rows.get(replacement)
        if replacement_row is None:
            errors.append(
                f"{key}: superseded_by is not governed: {replacement}"
            )
        elif replacement_row["status"] not in {"active", "proposed"}:
            errors.append(
                f"{key}: replacement {replacement} is "
                f"{replacement_row['status']}, expected active or proposed"
            )

    markdown_files = sorted(docs_root.rglob("*.md"))
    # Local-only plans and validation reports are not part of the distributed
    # manuals. Apply the same Git rules locally and in a clean checkout.
    if registry_path.resolve() == DEFAULT_REGISTRY.resolve():
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-z", "--stdin"],
            cwd=ROOT,
            input="\0".join(str(p) for p in markdown_files).encode("utf-8") + b"\0",
            stdout=subprocess.PIPE,
            check=False,
        )
        if result.returncode not in (0, 1):
            raise ValueError("Cannot inspect documentation distribution rules")
        ignored = {Path(p).resolve() for p in result.stdout.decode("utf-8").split("\0") if p}
        markdown_files = [p for p in markdown_files if p.resolve() not in ignored]
    registered = {
        row["absolute"].resolve()
        for row in rows.values()
        if row["absolute"].is_file()
    }
    unregistered = [
        path.relative_to(docs_root).as_posix()
        for path in markdown_files
        if path.resolve() not in registered
    ]
    if unregistered:
        message = (
            f"{len(unregistered)} Markdown documents are not governed: "
            + ", ".join(unregistered[:10])
        )
        if len(unregistered) > 10:
            message += ", ..."
        (errors if strict_unregistered else warnings).append(message)

    link_sources = (
        markdown_files
        if all_links
        else sorted(registered)
    )
    broken_links: list[str] = []
    index_status_violations: list[str] = []
    status_banner_violations: list[str] = []
    rows_by_absolute = {
        row["absolute"].resolve(): row
        for row in rows.values()
        if row["absolute"].is_file()
    }
    for source in link_sources:
        text = source.read_text(encoding="utf-8")
        index_markers = list(GOVERNED_INDEX_RE.finditer(text))
        for match in LINK_RE.finditer(text):
            raw_target = match.group("target")
            target = _local_link_target(source, raw_target)
            if target is None:
                continue
            if not target.exists():
                line = text.count("\n", 0, match.start()) + 1
                broken_links.append(
                    f"{source.relative_to(docs_root).as_posix()}:{line}: "
                    f"broken link {raw_target}"
                )
                continue
            marker = next(
                (
                    item
                    for item in reversed(index_markers)
                    if item.start() < match.start()
                ),
                None,
            )
            if marker is None:
                continue
            index_status = marker.group("status")
            if index_status == "historical":
                continue
            target_row = rows_by_absolute.get(target.resolve())
            if target_row is None:
                continue
            expected_status = index_status
            if target_row["status"] != expected_status:
                line = text.count("\n", 0, match.start()) + 1
                index_status_violations.append(
                    f"{source.relative_to(docs_root).as_posix()}:{line}: "
                    f"{index_status} index links to "
                    f"{target_row['status']} document {target_row['path']}"
                )

    for key, row in rows.items():
        source = row["absolute"]
        if not source.is_file():
            continue
        text = source.read_text(encoding="utf-8")
        head = "\n".join(text.splitlines()[:STATUS_BANNER_LINE_LIMIT])
        marker = DOCUMENT_STATUS_RE.search(head)
        if row["status"] == "superseded":
            if marker is None or marker.group("status") != "superseded":
                status_banner_violations.append(
                    f"{key}: superseded document requires a "
                    "document-status marker in the first "
                    f"{STATUS_BANNER_LINE_LIMIT} lines"
                )
                continue
            replacement = rows.get(
                str(row.get("superseded_by") or "").replace("\\", "/")
            )
            replacement_target = (
                replacement["absolute"].resolve()
                if replacement is not None
                else None
            )
            has_replacement_link = any(
                _local_link_target(source, match.group("target"))
                == replacement_target
                for match in LINK_RE.finditer(head)
            )
            if not has_replacement_link:
                status_banner_violations.append(
                    f"{key}: superseded banner must link to "
                    f"{row.get('superseded_by')}"
                )
        elif marker is not None and marker.group("status") == "superseded":
            status_banner_violations.append(
                f"{key}: {row['status']} document contains a superseded marker"
            )
    errors.extend(broken_links)
    errors.extend(index_status_violations)
    errors.extend(status_banner_violations)

    return {
        "success": not errors,
        "registry": str(registry_path),
        "governed_count": len(rows),
        "active_count": sum(
            1 for row in rows.values() if row["status"] == "active"
        ),
        "superseded_count": sum(
            1 for row in rows.values() if row["status"] == "superseded"
        ),
        "archived_count": sum(
            1 for row in rows.values() if row["status"] == "archived"
        ),
        "unregistered_count": len(unregistered),
        "broken_link_count": len(broken_links),
        "index_status_violation_count": len(index_status_violations),
        "status_banner_violation_count": len(status_banner_violations),
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY,
    )
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=date.today(),
        help="Override audit date using YYYY-MM-DD.",
    )
    parser.add_argument(
        "--all-links",
        action="store_true",
        help="Check links in every Markdown file, not only governed files.",
    )
    parser.add_argument(
        "--strict-unregistered",
        action="store_true",
        help="Fail when any Markdown file is absent from the registry.",
    )
    args = parser.parse_args()
    try:
        result = audit(
            registry_path=args.registry.resolve(),
            today=args.today,
            all_links=args.all_links,
            strict_unregistered=args.strict_unregistered,
        )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"document audit configuration error: {exc}", file=sys.stderr)
        return 2
    for warning in result["warnings"]:
        print(f"[WARN] {warning}")
    for error in result["errors"]:
        print(f"[ERROR] {error}")
    print(
        "Document audit: "
        f"governed={result['governed_count']} "
        f"active={result['active_count']} "
        f"superseded={result['superseded_count']} "
        f"archived={result['archived_count']} "
        f"unregistered={result['unregistered_count']} "
        f"broken_links={result['broken_link_count']} "
        f"index_status_violations={result['index_status_violation_count']} "
        f"status_banner_violations={result['status_banner_violation_count']}"
    )
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

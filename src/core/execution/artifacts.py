# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Artifact helpers shared by execution runners."""

from __future__ import annotations

import os
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ArchiveSpec:
    """Describe how output files should be archived."""

    object_prefix: str
    zip_prefix: str
    default_ticket_prefix: str
    log_event_prefix: str


def archive_output_files(
    *,
    output_files: list[str],
    ticket_id: str | None,
    minio_storage_factory: Callable[[], Any],
    spec: ArchiveSpec,
    cleanup_after_upload: bool = False,
) -> str | None:
    """Zip and upload runner output files, returning a presigned URL if available."""
    minio_client = minio_storage_factory()
    if not minio_client or not minio_client.is_ready() or not output_files:
        return None

    archive_ticket = ticket_id or (
        f"{spec.default_ticket_prefix}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, f"{spec.zip_prefix}_{archive_ticket}.zip")
        files: list[tuple[str, str]] = []
        used_names: set[str] = set()
        for index, value in enumerate(output_files):
            path = os.path.abspath(str(value))
            if not os.path.isfile(path):
                continue
            base_name = os.path.basename(path)
            archive_name = base_name
            if archive_name.casefold() in used_names:
                archive_name = f"{index + 1:04d}-{base_name}"
            used_names.add(archive_name.casefold())
            files.append((path, archive_name))
            manifest = f"{path}.collection.json"
            if os.path.isfile(manifest):
                manifest_name = f"{archive_name}.collection.json"
                used_names.add(manifest_name.casefold())
                files.append((manifest, manifest_name))
        if not files:
            return None
        with zipfile.ZipFile(
            zip_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            allowZip64=True,
        ) as archive:
            for source, archive_name in files:
                archive.write(source, arcname=archive_name)

        object_name = f"{spec.object_prefix}/{archive_ticket}/{os.path.basename(zip_path)}"
        log.info(f"{spec.log_event_prefix}_minio_upload", object_name=object_name)
        with open(zip_path, "rb") as file_obj:
            upload_success = minio_client.upload_file(object_name, file_obj)

        if upload_success:
            download_url = minio_client.get_presigned_url(
                object_name,
                expires=3600 * 24 * 7,
            )
            log.info(f"{spec.log_event_prefix}_minio_uploaded", download_url=download_url)
            if cleanup_after_upload:
                _cleanup_uploaded_files(files)
            return download_url
    return None


def _cleanup_uploaded_files(files: list[tuple[str, str]]) -> None:
    parents = set()
    for source, _archive_name in files:
        try:
            parents.add(os.path.dirname(source))
            os.unlink(source)
        except FileNotFoundError:
            continue
        except OSError as exc:
            log.warning("patrol_local_output_cleanup_failed", path=source, error=str(exc))
    for directory in sorted(parents, key=len, reverse=True):
        try:
            os.rmdir(directory)
        except OSError:
            continue


def cleanup_expired_output_captures(
    output_root: str | Path,
    *,
    retention_hours: int,
    now: datetime | None = None,
) -> dict[str, int]:
    """Delete only expired collector files that have a sidecar manifest."""

    root = Path(output_root).resolve()
    if not root.exists() or retention_hours <= 0:
        return {"examined": 0, "deleted_files": 0, "deleted_directories": 0}
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=retention_hours)
    examined = 0
    deleted_files = 0
    deleted_directories = 0
    for manifest in root.rglob("*.collection.json"):
        try:
            resolved_manifest = manifest.resolve()
            if root not in resolved_manifest.parents:
                continue
            examined += 1
            modified = datetime.fromtimestamp(
                resolved_manifest.stat().st_mtime,
                tz=timezone.utc,
            )
            if modified >= cutoff:
                continue
            output_path = Path(str(resolved_manifest)[: -len(".collection.json")])
            for candidate in (output_path, resolved_manifest):
                if candidate.is_file() and root in candidate.resolve().parents:
                    candidate.unlink()
                    deleted_files += 1
            capture_dir = resolved_manifest.parent
            if capture_dir != root and root in capture_dir.parents:
                try:
                    capture_dir.rmdir()
                    deleted_directories += 1
                except OSError:
                    pass
        except OSError as exc:
            log.warning(
                "patrol_expired_output_cleanup_failed",
                path=str(manifest),
                error=str(exc),
            )
    return {
        "examined": examined,
        "deleted_files": deleted_files,
        "deleted_directories": deleted_directories,
    }

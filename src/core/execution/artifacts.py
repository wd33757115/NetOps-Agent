# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Artifact helpers shared by execution runners."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

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
        output_dir = os.path.dirname(output_files[0]) if output_files else tmpdir
        shutil.make_archive(zip_path.replace(".zip", ""), "zip", output_dir)

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
            return download_url
    return None

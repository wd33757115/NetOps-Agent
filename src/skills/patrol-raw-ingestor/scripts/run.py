#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.patrol.modes import normalize_patrol_mode  # noqa: E402
from src.core.patrol.raw_importer import import_raw_path, preview_raw_path  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import raw patrol CLI capture into snapshot store"
    )
    parser.add_argument("--params", required=True, help="params.json path")
    args = parser.parse_args()
    with open(args.params, encoding="utf-8-sig") as f:
        params = json.load(f)
    mode = normalize_patrol_mode(params.get("mode"))
    metadata = dict(params.get("metadata") or {})
    if params.get("dataset_id"):
        metadata["dataset_id"] = params["dataset_id"]
    offline_job_id = str(params.get("offline_job_id") or "").strip()
    period_role = str(params.get("period_role") or "").strip()
    file_path = params.get("file_path")
    if offline_job_id:
        if not period_role:
            raise ValueError("period_role is required with offline_job_id")
        if file_path:
            raise ValueError("offline_job_id and file_path are mutually exclusive")
        from src.core.patrol.offline_jobs import OfflinePatrolJobService

        result = (
            OfflinePatrolJobService().run_preview(offline_job_id)
            if params.get("preview")
            else OfflinePatrolJobService().import_period(offline_job_id, period_role)
        )
    elif params.get("preview"):
        if not file_path:
            raise ValueError("file_path or offline_job_id is required")
        result = preview_raw_path(
            file_path=file_path,
            mode=mode,
            vendor=params.get("vendor"),
            model=params.get("model"),
            metadata=metadata,
        )
    else:
        if not file_path:
            raise ValueError("file_path or offline_job_id is required")
        result = import_raw_path(
            file_path=file_path,
            mode=mode,
            run_id=params.get("run_id"),
            vendor=params.get("vendor"),
            model=params.get("model"),
            observed_at=params.get("observed_at"),
            metadata=metadata,
        )
    if result.get("success") and not result.get("message"):
        if params.get("preview"):
            result["message"] = (
                f"导入预览完成：扫描 {result.get('file_count', 0)} 个文件，"
                f"预计新增 {result.get('new_snapshots', 0)} 条快照，"
                f"重复 {result.get('duplicate_snapshots', 0)} 条"
            )
        else:
            result["message"] = (
                f"巡检样本导入完成：{result.get('file_count', 1)} 个文件，"
                f"{result.get('command_count', 0)} 条命令，"
                f"新增 {result.get('new_snapshot_count', 0)} 条快照，"
                f"重复 {result.get('duplicate_snapshot_count', 0)} 条"
            )
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())

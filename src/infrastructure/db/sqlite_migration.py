"""One-time, idempotent migration from legacy SQLite stores to PostgreSQL."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from src.platform.inventory.repository import DeviceRepository
from src.core.patrol.repository import PatrolRepository
from src.infrastructure.db.models import CommandSnapshot, NetworkDevice, PatrolRun
from src.infrastructure.db.postgres import get_db_session


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_value(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _dataset_key(mode: str, dataset_id: str | None) -> str:
    return f"{mode}:{dataset_id or 'legacy'}"


def _run_key(mode: str, run_id: str) -> str:
    return f"migrated:{mode}:{run_id}"


def inspect_device_inventory(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    with sqlite3.connect(str(source)) as connection:
        connection.row_factory = sqlite3.Row
        tables = _tables(connection)
        required = {"devices", "groups", "group_devices"}
        if not required.issubset(tables):
            raise ValueError(f"unsupported device SQLite schema: {source}")
        devices = connection.execute("SELECT * FROM devices").fetchall()
        groups = connection.execute("SELECT * FROM groups").fetchall()
        memberships = connection.execute("SELECT * FROM group_devices").fetchall()
    return {
        "source": str(source),
        "devices": len(devices),
        "groups": len(groups),
        "memberships": len(memberships),
        "credentials_requiring_rebind": sum(
            1 for row in devices if "password" in row.keys() and row["password"]
        ),
    }


def migrate_device_inventory(
    path: str | Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    source = Path(path)
    preview = inspect_device_inventory(source)
    if dry_run:
        return {**preview, "dry_run": True}

    repository = DeviceRepository()
    with sqlite3.connect(str(source)) as connection:
        connection.row_factory = sqlite3.Row
        devices = connection.execute("SELECT * FROM devices").fetchall()
        groups = connection.execute("SELECT * FROM groups").fetchall()
        memberships = connection.execute("SELECT * FROM group_devices").fetchall()

    device_map: dict[int, str] = {}
    group_map: dict[int, str] = {}
    for row in devices:
        keys = set(row.keys())
        device_id = repository.upsert_device(
            device_name=str(row["device_name"]),
            management_ip=str(row["ip"]),
            username=row["username"] if "username" in keys else None,
            credential_ref=None,
            vendor=row["vendor"] if "vendor" in keys else None,
            model=row["model"] if "model" in keys else None,
            output_path=row["out_path"] if "out_path" in keys else None,
            command_profile=row["command_file"] if "command_file" in keys else None,
            legacy_device_id=int(row["device_id"]),
            attributes={
                "credential_migration_required": bool(
                    row["password"] if "password" in keys else None
                ),
                "legacy_source": source.name,
            },
        )
        device_map[int(row["device_id"])] = device_id
    for row in groups:
        group_map[int(row["group_id"])] = repository.ensure_group(
            str(row["group_name"])
        )
    migrated_memberships = 0
    for row in memberships:
        device_id = device_map.get(int(row["device_id"]))
        group_id = group_map.get(int(row["group_id"]))
        if device_id and group_id:
            repository.add_device_to_group(device_id, group_id)
            migrated_memberships += 1

    with get_db_session() as db:
        target_devices = db.query(NetworkDevice).count()
    return {
        **preview,
        "dry_run": False,
        "migrated_devices": len(device_map),
        "migrated_groups": len(group_map),
        "migrated_memberships": migrated_memberships,
        "target_devices": target_devices,
    }


def inspect_patrol_store(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    with sqlite3.connect(str(source)) as connection:
        connection.row_factory = sqlite3.Row
        tables = _tables(connection)
        required = {"netops_patrol_runs", "netops_device_snapshots"}
        if not required.issubset(tables):
            raise ValueError(f"unsupported patrol SQLite schema: {source}")
        run_count = connection.execute(
            "SELECT COUNT(*) FROM netops_patrol_runs"
        ).fetchone()[0]
        snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM netops_device_snapshots"
        ).fetchone()[0]
        raw_hash_mismatches = 0
        for row in connection.execute(
            "SELECT raw_text, raw_text_hash FROM netops_device_snapshots"
        ):
            digest = hashlib.sha256((row["raw_text"] or "").encode("utf-8")).hexdigest()
            if digest != row["raw_text_hash"]:
                raw_hash_mismatches += 1
    return {
        "source": str(source),
        "runs": int(run_count),
        "snapshots": int(snapshot_count),
        "raw_hash_mismatches": raw_hash_mismatches,
    }


def migrate_patrol_store(
    path: str | Path,
    *,
    mode: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    source = Path(path)
    preview = inspect_patrol_store(source)
    if dry_run:
        return {**preview, "mode": mode, "dry_run": True}

    repository = PatrolRepository()
    with sqlite3.connect(str(source)) as connection:
        connection.row_factory = sqlite3.Row
        runs = connection.execute("SELECT * FROM netops_patrol_runs").fetchall()
        snapshots = connection.execute(
            "SELECT * FROM netops_device_snapshots ORDER BY created_at"
        ).fetchall()

    run_map: dict[str, str] = {}
    dataset_keys: set[str] = set()
    snapshots_by_run: dict[str, list[sqlite3.Row]] = {}
    for snapshot in snapshots:
        snapshots_by_run.setdefault(str(snapshot["run_id"]), []).append(snapshot)
        dataset_keys.add(_dataset_key(mode, snapshot["dataset_id"]))
    for dataset_key in sorted(dataset_keys):
        repository.ensure_dataset(
            dataset_id=dataset_key,
            name=dataset_key.split(":", 1)[1],
            mode=mode,
            source="sqlite_migration",
            metadata={"legacy_source": str(source)},
        )
    run_rows = {str(row["run_id"]): row for row in runs}
    for legacy_run_id in sorted(set(run_rows) | set(snapshots_by_run)):
        row = run_rows.get(legacy_run_id)
        run_snapshots = snapshots_by_run.get(legacy_run_id, [])
        dataset_id = (
            _dataset_key(mode, run_snapshots[0]["dataset_id"])
            if run_snapshots
            else None
        )
        target_run_id = _run_key(mode, legacy_run_id)
        run_map[legacy_run_id] = repository.create_run(
            run_id=target_run_id,
            source=str(row["source"]) if row else "sqlite_migration",
            mode=mode,
            dataset_id=dataset_id,
            metadata={
                **(_json_object(row["metadata_json"]) if row else {}),
                "legacy_run_id": legacy_run_id,
                "legacy_source": str(source),
            },
            started_at=row["started_at"] if row else None,
        )

    migrated = 0
    duplicates = 0
    for row in snapshots:
        keys = set(row.keys())
        dataset_id = _dataset_key(mode, row["dataset_id"])
        snapshot_id, created = repository.save_snapshot_idempotent(
            snapshot_id=str(row["snapshot_id"]),
            run_id=run_map[str(row["run_id"])],
            dataset_id=dataset_id,
            device_id=str(row["device_id"]),
            device_name=str(row["device_name"]),
            ip=row["ip"],
            vendor=row["vendor"],
            model=row["model"],
            command=str(row["command"]),
            command_canonical=str(row["command_canonical"]),
            parser_name=row["parser_name"],
            parser_version=row["parser_version"],
            observed_at=row["observed_at"],
            structured_json=_json_value(row["structured_json"]),
            raw_text=row["raw_text"] or "",
            parse_status=str(row["parse_status"]),
            source_file_hash=(
                row["source_file_hash"] if "source_file_hash" in keys else None
            ),
            import_fingerprint=(
                row["import_fingerprint"] if "import_fingerprint" in keys else None
            ),
            start_line=row["start_line"] if "start_line" in keys else None,
            end_line=row["end_line"] if "end_line" in keys else None,
            metadata={"legacy_source": str(source)},
        )
        if snapshot_id != str(row["snapshot_id"]):
            raise RuntimeError("snapshot identity changed during migration")
        migrated += int(created)
        duplicates += int(not created)
    for target_run_id in run_map.values():
        repository.finish_run(target_run_id)

    with get_db_session() as db:
        target_snapshots = (
            db.query(CommandSnapshot)
            .filter(
                CommandSnapshot.metadata_json["legacy_source"].as_string()
                == str(source)
            )
            .count()
        )
        target_runs = (
            db.query(PatrolRun)
            .filter(PatrolRun.metadata_json["legacy_source"].as_string() == str(source))
            .count()
        )
    return {
        **preview,
        "mode": mode,
        "dry_run": False,
        "migrated_snapshots": migrated,
        "duplicate_snapshots": duplicates,
        "target_snapshots": target_snapshots,
        "target_runs": target_runs,
        "datasets": len(dataset_keys),
    }

# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Import offline raw patrol captures into the snapshot store."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from src.core.patrol.command_splitter import (
    infer_device_from_filename,
    inspect_raw_capture,
    split_cli_capture,
)
from src.core.patrol.modes import (
    PARSER_VALIDATION_MODE,
    PRODUCTION_MODE,
    normalize_patrol_mode,
)
from src.core.patrol.repository import PatrolRepository
from src.core.parser.identity import DeviceIdentityResolver
from src.core.parser.runtime import ParserRuntime, ParserRuntimeResult
from src.infrastructure.db.postgres import require_postgres_connection


class ParserCoverageError(RuntimeError):
    def __init__(self, message: str, violations: list[dict[str, Any]]):
        super().__init__(message)
        self.violations = violations


def _non_repairable_output(raw_text: str) -> bool:
    text = (raw_text or "").lower()
    return len(text.strip()) < 20 or any(
        marker in text
        for marker in (
            "connection timed out",
            "connection reset",
            "command execution failed",
            "ssh connection failed",
            "output truncated",
            "invalid command at",
            "unrecognized command found",
            "incomplete command found",
        )
    )


def _read_text_best_effort(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "utf-16-le", "utf-16-be", "gb18030", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _dataset_id(path: Path, metadata: dict[str, Any] | None) -> str:
    explicit = str((metadata or {}).get("dataset_id") or "").strip()
    if explicit:
        return explicit
    source = path if path.is_dir() else path.parent
    label = source.name or "parser-validation"
    digest = hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()[:10]
    return f"{label}-{digest}"


def _resolve_capture_device(
    *,
    path: Path,
    text: str,
    explicit_device_name: str | None = None,
    explicit_ip: str | None = None,
) -> tuple[str, str | None, Any]:
    inferred_name, inferred_ip = infer_device_from_filename(path)
    inspected = inspect_raw_capture(text)
    ip = explicit_ip or inferred_ip
    # Field-export filenames usually carry the inventory name and management IP.
    # Device prompts can be hostnames, aliases, or stale names, so use them only
    # when the file name does not provide a stronger asset identity.
    device_name = (
        explicit_device_name
        or (inferred_name if inferred_name and inferred_ip else None)
        or inspected.device_name
        or inferred_name
        or path.stem
    )
    return device_name, ip, inspected


def _import_fingerprint(
    *,
    mode: str,
    dataset_id: str,
    source_file_hash: str,
    device_id: str,
    canonical_command: str,
    raw_text: str,
) -> str:
    raw_hash = hashlib.sha256((raw_text or "").encode("utf-8")).hexdigest()
    value = "|".join(
        [
            "v2",
            mode,
            dataset_id,
            source_file_hash,
            device_id,
            canonical_command.strip().lower(),
            raw_hash,
        ]
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def preview_raw_path(
    *,
    file_path: str | Path,
    mode: str = PARSER_VALIDATION_MODE,
    vendor: str | None = None,
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Analyze an import without writing snapshots or parser jobs."""
    require_postgres_connection()
    mode = normalize_patrol_mode(mode)
    path = Path(file_path)
    if path.is_file():
        files = [path]
    elif path.is_dir():
        files = sorted(
            item
            for item in path.rglob("*")
            if item.is_file() and item.suffix.lower() in {".txt", ".log"}
        )
    else:
        raise FileNotFoundError(f"patrol source path does not exist: {path}")
    if not files:
        raise ValueError(f"no txt/log patrol files found under: {path}")

    runtime = ParserRuntime()
    repository = PatrolRepository()
    dataset_id = _dataset_id(path, metadata)
    totals = {
        "ready": 0,
        "template_missing": 0,
        "parse_failed": 0,
        "ignored": 0,
        "identity_unknown": 0,
        "duplicate_snapshots": 0,
        "new_snapshots": 0,
    }
    devices: set[str] = set()
    commands: set[str] = set()
    file_results: list[dict[str, Any]] = []
    existing_fingerprints = repository.list_import_fingerprints(mode=mode)
    for candidate in files:
        text = _read_text_best_effort(candidate)
        file_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
        resolved_name, resolved_ip, _inspected = _resolve_capture_device(
            path=candidate,
            text=text,
        )
        device_id = f"{resolved_name}-{resolved_ip}" if resolved_ip else resolved_name
        blocks = split_cli_capture(text, device_name=resolved_name)
        identity = DeviceIdentityResolver().resolve(
            blocks=blocks,
            path=candidate,
            vendor=vendor,
            model=model,
        )
        devices.add(device_id)
        statuses: dict[str, int] = {}
        for block in blocks:
            commands.add(block.command_canonical)
            fingerprint = _import_fingerprint(
                mode=mode,
                dataset_id=dataset_id,
                source_file_hash=file_hash,
                device_id=device_id,
                canonical_command=block.command_canonical,
                raw_text=block.raw_output,
            )
            if fingerprint in existing_fingerprints:
                totals["duplicate_snapshots"] += 1
                statuses["duplicate"] = statuses.get("duplicate", 0) + 1
                continue
            totals["new_snapshots"] += 1
            parsed = runtime.parse(
                identity=identity,
                command=block.command_canonical,
                raw_text=block.raw_output,
            )
            if parsed.policy_status == "ignored":
                status = "ignored"
            elif parsed.identity_status != "known":
                status = "identity_unknown"
            elif parsed.parser_status in {"success", "empty"}:
                status = "ready"
            elif parsed.failure_code == "template_missing":
                status = "template_missing"
            else:
                status = "parse_failed"
            totals[status] += 1
            statuses[status] = statuses.get(status, 0) + 1
        file_results.append(
            {
                "file_path": str(candidate),
                "device_id": device_id,
                "vendor": identity.vendor,
                "model": identity.model,
                "identity_status": identity.status,
                "command_count": len(blocks),
                "statuses": statuses,
            }
        )
    return {
        "success": True,
        "preview": True,
        "mode": mode,
        "dataset_id": dataset_id,
        "source_path": str(path),
        "file_count": len(files),
        "device_count": len(devices),
        "command_count": sum(item["command_count"] for item in file_results),
        "unique_commands": len(commands),
        **totals,
        "files": file_results,
    }


def import_raw_capture(
    *,
    file_path: str | Path,
    db_path: str | Path | None = None,
    mode: str = PRODUCTION_MODE,
    run_id: str | None = None,
    device_name: str | None = None,
    ip: str | None = None,
    vendor: str | None = None,
    model: str | None = None,
    observed_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    require_postgres_connection()
    mode = normalize_patrol_mode(mode)
    path = Path(file_path)
    text = _read_text_best_effort(path)
    source_file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    device_name, ip, inspected = _resolve_capture_device(
        path=path,
        text=text,
        explicit_device_name=device_name,
        explicit_ip=ip,
    )
    device_id = f"{device_name}-{ip}" if ip else device_name
    blocks = split_cli_capture(text, device_name=device_name)
    identity = DeviceIdentityResolver().resolve(
        blocks=blocks,
        path=path,
        vendor=vendor,
        model=model,
    )
    vendor = identity.vendor
    model = identity.model

    runtime = ParserRuntime()
    coverage = [
        runtime.check_coverage(
            identity=identity,
            command=block.command_canonical,
        )
        for block in blocks
    ]
    blocking = [
        item
        for item in coverage
        if item["policy_status"] == "eligible"
        and item["coverage_status"] != "ready"
    ]

    parsed_blocks = []
    for block in blocks:
        try:
            parsed = runtime.parse(
                identity=identity,
                command=block.command_canonical,
                raw_text=block.raw_output,
            )
        except Exception as exc:
            parsed = ParserRuntimeResult(
                policy_status="eligible",
                identity_status=identity.status,
                parser_status="failed",
                canonical_command=block.command_canonical,
                handling="structured",
                failure_code=f"parser_exception:{type(exc).__name__}",
                identity_evidence={
                    "confidence": identity.confidence,
                    "source": identity.source,
                    "command": identity.evidence_command,
                    "text": identity.evidence_text,
                },
            )
        parsed_blocks.append((block, parsed))
    parse_violations = [
        {
            "canonical_command": parsed.canonical_command,
            "parser_status": parsed.parser_status,
            "failure_code": parsed.failure_code,
            "template_id": parsed.template_id,
            "template_version": parsed.template_version,
        }
        for _, parsed in parsed_blocks
        if parsed.policy_status == "eligible"
        and parsed.parser_status not in {"success", "empty"}
    ]

    repository = PatrolRepository()
    dataset_id = _dataset_id(path, metadata)
    fingerprints = [
        _import_fingerprint(
            mode=mode,
            dataset_id=dataset_id,
            source_file_hash=source_file_hash,
            device_id=device_id,
            canonical_command=block.command_canonical,
            raw_text=block.raw_output,
        )
        for block, _ in parsed_blocks
    ]
    existing = repository.find_snapshots_by_import_fingerprints(set(fingerprints))
    if fingerprints and all(fingerprint in existing for fingerprint in fingerprints):
        existing_snapshots = [existing[fingerprint] for fingerprint in fingerprints]
        return {
            "success": True,
            "run_id": existing_snapshots[0].run_id,
            "device_id": device_id,
            "device_name": device_name,
            "ip": ip,
            "command_count": len(blocks),
            "snapshot_ids": [
                snapshot.snapshot_id for snapshot in existing_snapshots
            ],
            "new_snapshot_count": 0,
            "duplicate_snapshot_count": len(existing_snapshots),
            "duplicate_snapshot_ids": [
                snapshot.snapshot_id for snapshot in existing_snapshots
            ],
            "commands": [block.command_canonical for block in blocks],
            "vendor": vendor,
            "model": model,
            "family": identity.family,
            "identity_status": identity.status,
            "mode": mode,
            "dataset_id": dataset_id,
            "storage": "postgresql",
            "legacy_db_path_ignored": str(db_path) if db_path else None,
            "coverage": coverage,
            "coverage_violations": blocking,
            "parse_violations": parse_violations,
            "production_parser_ready": not (
                mode == PRODUCTION_MODE and (blocking or parse_violations)
            ),
            "parser_results": [
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "command": snapshot.command_canonical,
                    "parse_status": snapshot.parse_status,
                    "candidate_job_id": None,
                }
                for snapshot in existing_snapshots
            ],
        }
    repository.ensure_dataset(
        dataset_id=dataset_id,
        name=str((metadata or {}).get("dataset_name") or dataset_id),
        mode=mode,
        source="imported_file",
        metadata={
            "source_path": str(path.parent),
            **(metadata or {}),
        },
    )
    rid = repository.create_run(
        run_id=run_id,
        source="imported_file",
        mode=mode,
        dataset_id=dataset_id,
        metadata={
            "file_path": str(path),
            "prompt_style": inspected.prompt_style,
            "patrol_mode": mode,
            "dataset_id": dataset_id,
            **(metadata or {}),
        },
        started_at=observed_at,
    )

    snapshot_ids: list[str] = []
    duplicate_snapshot_ids: list[str] = []
    parser_results: list[dict[str, Any]] = []
    for (block, parsed), fingerprint in zip(
        parsed_blocks, fingerprints, strict=True
    ):
        parse_status = "raw_only"
        structured = None
        parser_name = None
        parser_version = None
        if parsed.policy_status == "ignored":
            parse_status = "ignored"
        elif parsed.identity_status != "known":
            parse_status = "identity_unknown"
        elif parsed.parser_status == "unavailable":
            parse_status = "template_missing"
        elif parsed.parser_status == "empty":
            parse_status = "parse_empty"
            structured = []
        elif parsed.parser_status == "success":
            parse_status = "parsed"
            structured = parsed.structured_data
        else:
            parse_status = "parse_failed"
        parser_name = parsed.template_id
        parser_version = parsed.template_version
        snapshot_id, snapshot_created = repository.save_snapshot_idempotent(
            run_id=rid,
            device_id=device_id,
            device_name=device_name,
            ip=ip,
            vendor=vendor,
            model=model,
            command=block.command,
            command_canonical=block.command_canonical,
            raw_text=block.raw_output,
            observed_at=observed_at or inspected.observed_at_text,
            structured_json=structured,
            parser_name=parser_name,
            parser_version=parser_version,
            parse_status=parse_status,
            dataset_id=dataset_id,
            source_file_hash=source_file_hash,
            import_fingerprint=fingerprint,
            start_line=block.start_line,
            end_line=block.end_line,
            metadata={
                "patrol_mode": mode,
                "source_path": str(path),
            },
        )
        snapshot_ids.append(snapshot_id)
        if not snapshot_created:
            duplicate_snapshot_ids.append(snapshot_id)
        candidate_job_id = None
        if snapshot_created and mode == PARSER_VALIDATION_MODE and parsed.failure_code in {
            "template_missing",
            "template_parse_error",
            "unexpected_empty",
            "required_fields_missing",
            "field_validation_failed",
            "primary_key_invalid",
            "primary_key_duplicate",
            "asset_hash_mismatch",
        } and not _non_repairable_output(block.raw_output):
            from src.core.parser.repository import ParserRepository

            candidate_job_id = ParserRepository().create_candidate(
                job_type=(
                    "CREATE_TEMPLATE"
                    if parsed.failure_code == "template_missing"
                    else "REPAIR_TEMPLATE"
                ),
                vendor=identity.vendor or "",
                model=identity.model or "",
                family=identity.family,
                command=parsed.canonical_command,
                snapshot_id=snapshot_id,
                device_id=device_id,
                raw_text=block.raw_output,
                failure_code=parsed.failure_code,
                dataset_id=dataset_id,
            )
        parser_results.append(
            {
                "snapshot_id": snapshot_id,
                "command": block.command_canonical,
                "parse_status": parse_status,
                "candidate_job_id": candidate_job_id,
            }
        )
    repository.finish_run(rid)
    return {
        "success": True,
        "run_id": rid,
        "device_id": device_id,
        "device_name": device_name,
        "ip": ip,
        "command_count": len(blocks),
        "snapshot_ids": snapshot_ids,
        "new_snapshot_count": len(snapshot_ids) - len(duplicate_snapshot_ids),
        "duplicate_snapshot_count": len(duplicate_snapshot_ids),
        "duplicate_snapshot_ids": duplicate_snapshot_ids,
        "commands": [b.command_canonical for b in blocks],
        "vendor": vendor,
        "model": model,
        "family": identity.family,
        "identity_status": identity.status,
        "mode": mode,
        "dataset_id": dataset_id,
        "storage": "postgresql",
        "legacy_db_path_ignored": str(db_path) if db_path else None,
        "coverage": coverage,
        "coverage_violations": blocking,
        "parse_violations": parse_violations,
        "production_parser_ready": not (
            mode == PRODUCTION_MODE and (blocking or parse_violations)
        ),
        "parser_results": parser_results,
    }


def import_raw_path(
    *,
    file_path: str | Path,
    db_path: str | Path | None = None,
    mode: str = PRODUCTION_MODE,
    run_id: str | None = None,
    vendor: str | None = None,
    model: str | None = None,
    observed_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Import one capture file or every txt/log capture in a directory."""
    path = Path(file_path)
    if path.is_file():
        return import_raw_capture(
            file_path=path,
            db_path=db_path,
            mode=mode,
            run_id=run_id,
            vendor=vendor,
            model=model,
            observed_at=observed_at,
            metadata=metadata,
        )
    if not path.is_dir():
        raise FileNotFoundError(f"巡检数据路径不存在: {path}")

    files = sorted(
        candidate
        for candidate in path.rglob("*")
        if candidate.is_file() and candidate.suffix.lower() in {".txt", ".log"}
    )
    if not files:
        raise ValueError(f"目录中未发现 txt/log 巡检文件: {path}")

    rid = run_id
    dataset_id = _dataset_id(path, metadata)
    imported: list[dict[str, Any]] = []
    for candidate in files:
        result = import_raw_capture(
            file_path=candidate,
            db_path=db_path,
            mode=mode,
            run_id=rid,
            vendor=vendor,
            model=model,
            observed_at=observed_at,
            metadata={
                "source_directory": str(path),
                "dataset_id": dataset_id,
                **(metadata or {}),
            },
        )
        rid = result["run_id"]
        imported.append(result)

    return {
        "success": True,
        "mode": normalize_patrol_mode(mode),
        "run_id": rid,
        "dataset_id": dataset_id,
        "storage": "postgresql",
        "legacy_db_path_ignored": str(db_path) if db_path else None,
        "source_path": str(path),
        "file_count": len(imported),
        "device_count": len({item["device_id"] for item in imported}),
        "command_count": sum(item["command_count"] for item in imported),
        "snapshot_ids": [
            snapshot_id
            for item in imported
            for snapshot_id in item["snapshot_ids"]
        ],
        "new_snapshot_count": sum(item["new_snapshot_count"] for item in imported),
        "duplicate_snapshot_count": sum(
            item["duplicate_snapshot_count"] for item in imported
        ),
        "files": [
            {
                "file_path": str(candidate),
                "device_id": result["device_id"],
                "command_count": result["command_count"],
            }
            for candidate, result in zip(files, imported, strict=True)
        ],
    }

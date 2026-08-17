#!/usr/bin/env python3
"""Benchmark the check-item runtime and final-report path against PostgreSQL.

The benchmark writes realistic batch, target, execution, assessment, manifest,
and report rows inside one outer transaction. By default that transaction is
rolled back, so the production database is not polluted by capacity evidence.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import tracemalloc
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.common.config import settings
from src.core.check_items.batch_results import (
    CheckItemBatchResultService,
    _cursor_scope,
    _encode_cursor,
)
from src.core.check_items.catalog import CheckItemCatalogRepository
from src.core.check_items.planning import (
    CheckItemEffectivePlanService,
    EffectivePlanRequest,
    build_inventory_snapshot,
    build_target_manifest,
)
from src.core.check_items.report_manifest import ReportManifestService
from src.core.check_items.reporting import (
    build_check_item_report_view,
    render_check_item_artifacts,
)
from src.infrastructure.db.models import (
    CheckExecutionRecord,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
)
from src.infrastructure.db.postgres import engine


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(
        0,
        min(
            len(ordered) - 1,
            round((len(ordered) - 1) * percentile),
        ),
    )
    return ordered[rank]


def _timed(call: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    result = call()
    return result, time.perf_counter() - started


def _device(index: int) -> dict[str, Any]:
    return {
        "device_id": f"capacity-device-{index:05d}",
        "device_name": f"Capacity-H3C-{index:05d}",
        "management_ip": f"198.18.{index // 254}.{(index % 254) + 1}",
        "vendor": "H3C",
        "family": "Comware7",
        "model": "S5130S-54S-HI-G",
        "os_version": "7.1",
        "capabilities": ["optical_diagnostics"],
    }


def run_benchmark(
    *,
    device_count: int,
    expected_check_item_count: int,
    page_size: int,
    page_samples: int,
    output_path: Path | None,
    commit: bool,
) -> dict[str, Any]:
    if device_count < 1:
        raise ValueError("device_count must be positive")
    if page_size < 1 or page_size > 200:
        raise ValueError("page_size must be between 1 and 200")
    if page_samples < 3:
        raise ValueError("page_samples must be at least 3")

    batch_id = (
        f"capacity-check-item-{device_count}-"
        f"{uuid.uuid4().hex[:12]}"
    )
    devices = tuple(_device(index) for index in range(device_count))
    target_manifest = build_target_manifest(
        devices=devices,
        selector={"benchmark": True, "device_count": device_count},
    )
    inventory_manifest = build_inventory_snapshot(devices)

    connection = engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    )
    outer_transaction = connection.begin()
    session_factory = sessionmaker(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    @contextmanager
    def scope():
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    tracemalloc.start()
    total_started = time.perf_counter()
    report: dict[str, Any] = {}
    try:
        catalog_repository = CheckItemCatalogRepository(scope)
        catalog = catalog_repository.load_active()
        if not catalog.check_items:
            catalog_repository.bootstrap(actor="capacity-benchmark")
            catalog = catalog_repository.load_active()
        check_item_ids = tuple(
            sorted(item.id for item in catalog.check_items)
        )
        if len(check_item_ids) != expected_check_item_count:
            raise AssertionError(
                "capacity benchmark catalog scope mismatch: "
                f"expected {expected_check_item_count} real check items, "
                f"found {len(check_item_ids)}"
            )
        now = datetime.now(timezone.utc)

        with scope() as db:
            db.add(
                PatrolBatchRecord(
                    patrol_batch_id=batch_id,
                    workflow_name="check-item-capacity-benchmark",
                    launch_mode="workflow",
                    patrol_mode="production",
                    target_type="devices",
                    target_label=f"{device_count} device capacity benchmark",
                    selector_json={
                        "benchmark": True,
                        "device_count": device_count,
                    },
                    target_count=device_count,
                    launched_count=device_count,
                    skipped_count=0,
                    target_manifest_digest=target_manifest["digest"],
                    inventory_snapshot_digest=inventory_manifest["digest"],
                    dispatch_status="completed",
                    dispatch_requested_at=now,
                    dispatch_started_at=now,
                    dispatch_completed_at=now,
                    report_status="not_requested",
                    archived=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.flush()
            db.add_all(
                [
                    PatrolBatchDeviceRecord(
                        id=str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"netops/capacity-target/{batch_id}/"
                                f"{device['device_id']}",
                            )
                        ),
                        patrol_batch_id=batch_id,
                        device_id=device["device_id"],
                        device_name=device["device_name"],
                        management_ip=device["management_ip"],
                        selection_status="launched",
                        preflight_status="ready",
                        dispatch_status="dispatched",
                        dispatch_attempts=1,
                        dispatched_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                    for device in devices
                ]
            )

        request = EffectivePlanRequest(
            patrol_batch_id=batch_id,
            devices=devices,
            check_item_ids=check_item_ids,
            target_manifest_digest=target_manifest["digest"],
            inventory_snapshot_digest=inventory_manifest["digest"],
            current_observed_at=now,
            actor="capacity-benchmark",
        )
        planner = CheckItemEffectivePlanService(
            scope,
            catalog=catalog,
        )
        preview, preview_seconds = _timed(
            lambda: planner.preview(request)
        )
        frozen, freeze_seconds = _timed(
            lambda: planner.freeze(request)
        )

        result_service = CheckItemBatchResultService(scope)
        live_summary, summary_seconds = _timed(
            lambda: result_service.summary(batch_id)
        )
        if live_summary is None:
            raise AssertionError("batch result summary was not created")

        total_rows = len(frozen.executions)
        max_offset = max(0, total_rows - page_size)
        max_shallow_offset = min(
            max_offset,
            int(settings.CHECK_ITEM_RESULT_MAX_OFFSET),
        )
        offsets = sorted(
            {
                round(max_shallow_offset * index / (page_samples - 1))
                for index in range(page_samples)
            }
        )
        page_timings: list[float] = []
        for offset in offsets:
            page, duration = _timed(
                lambda offset=offset: result_service.list_results(
                    batch_id,
                    limit=page_size,
                    offset=offset,
                )
            )
            if page["total"] != total_rows:
                raise AssertionError(
                    "server-side page total differs from frozen execution count"
                )
            page_timings.append(duration)

        cursor_timings: list[float] = []
        cursor: str | None = None
        for _index in range(min(page_samples, len(offsets))):
            cursor_page, duration = _timed(
                lambda cursor=cursor: result_service.list_results(
                    batch_id,
                    limit=page_size,
                    cursor=cursor,
                )
            )
            cursor_timings.append(duration)
            cursor = cursor_page.get("next_cursor")
            if not cursor:
                break

        deep_anchor_offset = max(0, total_rows - page_size - 1)
        with scope() as db:
            deep_anchor = (
                db.query(
                    CheckExecutionRecord.device_id,
                    CheckExecutionRecord.check_item_version_id,
                    CheckExecutionRecord.id,
                )
                .filter(
                    CheckExecutionRecord.patrol_batch_id == batch_id
                )
                .order_by(
                    CheckExecutionRecord.device_id,
                    CheckExecutionRecord.check_item_version_id,
                    CheckExecutionRecord.id,
                )
                .offset(deep_anchor_offset)
                .first()
            )
        if deep_anchor is None:
            raise AssertionError("deep cursor anchor was not found")
        deep_cursor = _encode_cursor(
            patrol_batch_id=batch_id,
            scope_digest=_cursor_scope(
                search=None,
                assessment_status=None,
                completeness_status=None,
                source_identity_status=None,
                output_quality_status=None,
                freshness_status=None,
                coverage_status=None,
            ),
            values=(
                str(deep_anchor.device_id),
                str(deep_anchor.check_item_version_id),
                str(deep_anchor.id),
            ),
            total=total_rows,
        )
        deep_cursor_count_queries = 0

        def track_deep_cursor_sql(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ):
            nonlocal deep_cursor_count_queries
            if "count(" in str(statement).lower():
                deep_cursor_count_queries += 1

        event.listen(
            connection,
            "before_cursor_execute",
            track_deep_cursor_sql,
        )
        try:
            deep_cursor_page, deep_cursor_seconds = _timed(
                lambda: result_service.list_results(
                    batch_id,
                    limit=page_size,
                    cursor=deep_cursor,
                )
            )
        finally:
            event.remove(
                connection,
                "before_cursor_execute",
                track_deep_cursor_sql,
            )
        if deep_cursor_page["total"] != total_rows:
            raise AssertionError("deep cursor lost the first-page total")
        if deep_cursor_count_queries:
            raise AssertionError(
                "deep cursor page repeated an aggregate count query"
            )
        cursor_timings.append(deep_cursor_seconds)

        filtered_page, filtered_seconds = _timed(
            lambda: result_service.list_results(
                batch_id,
                limit=page_size,
                offset=0,
                assessment_status="unable_to_determine",
                completeness_status="insufficient",
            )
        )
        search_page, search_seconds = _timed(
            lambda: result_service.list_results(
                batch_id,
                limit=page_size,
                offset=0,
                search=devices[-1]["management_ip"],
            )
        )
        if search_page["total"] != len(check_item_ids):
            raise AssertionError(
                "device search must return every check item for one target"
            )

        manifest_service = ReportManifestService(scope)
        manifest, manifest_seconds = _timed(
            lambda: manifest_service.build(
                batch_id,
                generated_by="capacity-benchmark",
            )
        )
        if manifest["status"] != "final":
            reasons = (
                manifest["source_facts_json"]["readiness"]["reason_codes"]
            )
            raise AssertionError(
                f"capacity report was not final: {reasons}"
            )
        report_view = build_check_item_report_view(manifest)
        artifacts, artifact_seconds = _timed(
            lambda: render_check_item_artifacts(manifest)
        )

        assessment_counts = dict(
            report_view.get("assessment_counts") or {}
        )
        if assessment_counts.get("healthy", 0):
            raise AssertionError(
                "missing runtime assets must not produce healthy conclusions"
            )
        honest_terminal_count = sum(
            int(assessment_counts.get(status, 0))
            for status in (
                "unable_to_determine",
                "not_applicable",
                "informational",
            )
        )
        if honest_terminal_count != total_rows:
            raise AssertionError(
                "every non-healthy capacity result must retain an explicit "
                "unknown, not-applicable, or informational conclusion"
            )
        if report_view["conclusion"]["code"] != "partially_unknown":
            raise AssertionError(
                "all missing-asset scope must produce partially_unknown"
            )

        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        report = {
            "schema_version": "check-item-capacity-benchmark/v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rolled_back": not commit,
            "batch_id": batch_id,
            "input": {
                "device_count": device_count,
                "check_item_count": len(check_item_ids),
                "expected_check_item_count": expected_check_item_count,
                "page_size": page_size,
                "page_samples": len(offsets),
            },
            "facts": {
                "report_transaction_isolation": (
                    connection.get_isolation_level()
                ),
                "planned_pair_count": len(preview.executions),
                "frozen_execution_count": total_rows,
                "terminal_assessment_count": len(
                    frozen.terminal_assessments
                ),
                "asset_gap_work_item_count": len(frozen.asset_gaps),
                "asset_gap_affected_execution_count": sum(
                    int(item.get("affected_execution_count") or 0)
                    for item in frozen.asset_gaps
                ),
                "filtered_unable_count": filtered_page["total"],
                "deep_cursor_anchor_offset": deep_anchor_offset,
                "deep_cursor_count_queries": (
                    deep_cursor_count_queries
                ),
                "report_manifest_status": manifest["status"],
                "report_conclusion": report_view["conclusion"],
                "assessment_status_counts": assessment_counts,
                "artifact_bytes": {
                    name: len(artifact.content)
                    for name, artifact in artifacts.items()
                },
                "source_facts_digest": manifest["source_digest"],
            },
            "timings_seconds": {
                "preview": preview_seconds,
                "freeze": freeze_seconds,
                "summary": summary_seconds,
                "result_page_min": min(page_timings),
                "result_page_mean": statistics.fmean(page_timings),
                "result_page_p95": _percentile(page_timings, 0.95),
                "result_page_max": max(page_timings),
                "cursor_page_min": min(cursor_timings),
                "cursor_page_mean": statistics.fmean(cursor_timings),
                "cursor_page_p95": _percentile(
                    cursor_timings,
                    0.95,
                ),
                "cursor_page_max": max(cursor_timings),
                "filtered_page": filtered_seconds,
                "search_page": search_seconds,
                "report_manifest": manifest_seconds,
                "report_artifacts": artifact_seconds,
                "total": time.perf_counter() - total_started,
            },
            "memory_bytes": {
                "current": current_bytes,
                "peak": peak_bytes,
            },
            "slo": {
                "preview_under_180_seconds": preview_seconds <= 180,
                "freeze_under_180_seconds": freeze_seconds <= 180,
                "result_page_p95_under_1_second": (
                    _percentile(page_timings, 0.95) <= 1
                ),
                "cursor_page_p95_under_1_second": (
                    _percentile(cursor_timings, 0.95) <= 1
                ),
                "formal_report_under_600_seconds": (
                    manifest_seconds + artifact_seconds <= 600
                ),
                "truthful_unknown_conclusion": (
                    honest_terminal_count == total_rows
                    and assessment_counts.get("healthy", 0) == 0
                ),
            },
        }
        if commit:
            outer_transaction.commit()
        else:
            outer_transaction.rollback()
    except Exception:
        if outer_transaction.is_active:
            outer_transaction.rollback()
        raise
    finally:
        tracemalloc.stop()
        connection.close()

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if not all(report["slo"].values()):
        raise AssertionError(
            "one or more check-item capacity SLOs failed; evidence saved to "
            f"{output_path}: "
            + json.dumps(report["slo"], ensure_ascii=False)
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devices", type=int, default=2000)
    parser.add_argument("--expected-check-items", type=int, default=20)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--page-samples", type=int, default=20)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            ".tmp/check-item-capacity/"
            "check-item-runtime-capacity.json"
        ),
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Keep benchmark rows. The default rolls the outer transaction back.",
    )
    args = parser.parse_args()
    report = run_benchmark(
        device_count=args.devices,
        expected_check_item_count=args.expected_check_items,
        page_size=args.page_size,
        page_samples=args.page_samples,
        output_path=args.output,
        commit=args.commit,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

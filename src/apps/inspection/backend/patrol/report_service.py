"""Offline report orchestration over the frozen analysis, with local evidence only."""

from collections import defaultdict
from copy import deepcopy

from src.core.patrol.command_splitter import canonicalize_command
from src.core.patrol.offline_jobs import OfflinePatrolJobService


def validate_comparison_references(items, snapshots, periods):
    """Reject historical cross-command/cross-run evidence instead of publishing it."""
    runs = {p.get("role"): p.get("patrol_run_id") for p in periods if p.get("patrol_run_id")}
    for item in items:
        expected_command = canonicalize_command(item.get("command") or "")
        for side in ("baseline", "current"):
            evidence = item.get(side) or item.get(side + "_evidence") or {}
            identifier = evidence.get("snapshot_id")
            if not identifier:
                continue
            snapshot = snapshots.get(identifier)
            if (
                snapshot is None
                or (
                    expected_command
                    and canonicalize_command(snapshot.canonical_command) != expected_command
                )
                or (
                    item.get("device_id")
                    and item["device_id"]
                    not in {snapshot.device_id, getattr(snapshot, "inventory_device_id", None)}
                )
                or (runs.get(side) and snapshot.run_id != runs[side])
            ):
                raise ValueError(
                    "历史分析的证据引用与设备、命令或巡检周期不一致，无法生成可靠报告。"
                    "请使用“复用文件新建分析”重新分析；原任务会保留。"
                )


def load_comparison_coverage(db, report, periods):
    """Use durable comparison counts, not the number of emitted changes."""
    from src.infrastructure.db.models import PatrolAnalysisAttemptRecord

    raw = report.setdefault("unstructured_comparison", {})
    raw["comparison_coverage_known"] = False
    analysis = (report.get("evidence") or {}).get("analysis") or {}
    if not analysis.get("attempt_id"):
        return
    event_attempt = db.get(PatrolAnalysisAttemptRecord, analysis["attempt_id"])
    if event_attempt is None or not event_attempt.completed_at:
        return
    baseline_run = next(
        (p.get("patrol_run_id") for p in periods if p.get("role") == "baseline"), None
    )
    candidates = [
        a
        for a in db.query(PatrolAnalysisAttemptRecord)
        .filter(
            PatrolAnalysisAttemptRecord.run_id == event_attempt.run_id,
            PatrolAnalysisAttemptRecord.stage == "change_detection",
            PatrolAnalysisAttemptRecord.status == "completed",
            PatrolAnalysisAttemptRecord.completed_at <= event_attempt.completed_at,
        )
        .all()
        if (a.scope_json or {}).get("analysis_revision", "original")
        == analysis.get("revision", "original")
        and (a.scope_json or {}).get("previous_run_id") == baseline_run
        and (a.scope_json or {}).get("full_run") is True
    ]
    if len(candidates) != 1 or "unstructured_compared_outputs" not in (
        candidates[0].result_json or {}
    ):
        return
    outputs = candidates[0].result_json["unstructured_compared_outputs"]
    count = len({tuple(key) for key in outputs})
    raw.update(
        compared_output_count=count,
        comparison_coverage_known=True,
        comparison_attempt_id=candidates[0].id,
    )
    report.setdefault("metrics", {})["unstructured_compared_count"] = count


def report_context(job_id, *, owner_id=None, is_admin=False):
    service = OfflinePatrolJobService()
    detail = service.get_job(job_id, owner_id=owner_id, is_admin=is_admin, purpose="offline_patrol")
    if detail.get("status") != "completed":
        raise ValueError("任务完成后才能生成报告")
    report = service.build_leadership_report_data(
        job_id, detail=detail, include_raw_evidence=True, include_source=True
    )
    source = report.pop("_source")
    from src.core.patrol.report_drafting import digest, encoded

    report["source_observations_digest"] = digest(
        sorted(source.get("observations") or [], key=encoded)
    )
    items = report.setdefault("structured_event_evidence", {}).setdefault("items", [])
    # Preserve all event decisions when several rules share the same change.
    for item in list(items):
        represented = (item.get("event") or {}).get("event_id")
        for event in source.get("events") or []:
            change_id = event.get("source_change_id") or (event.get("evidence_chain") or {}).get(
                "change_id"
            )
            event_id = event.get("id") or event.get("event_id")
            if change_id and change_id == item.get("change_id") and event_id != represented:
                extra = deepcopy(item)
                extra["event"] = {
                    **event,
                    "event_id": event_id,
                    "event_label": event.get("event_type"),
                }
                items.append(extra)
    from src.infrastructure.db.models import CommandSnapshot
    from src.infrastructure.db.postgres import get_db_session

    snapshot_ids = {
        f.get("snapshot_id") for f in source.get("current_findings") or [] if f.get("snapshot_id")
    }
    trend_ids = {
        t.get("observation_id") for t in (report.get("numeric_trends") or {}).get("items", [])
    }
    trend_observations = {
        o.get("id"): o for o in source.get("observations", []) if o.get("id") in trend_ids
    }
    snapshot_ids.update(
        o["snapshot_id"] for o in trend_observations.values() if o.get("snapshot_id")
    )
    comparisons = list(items) + list(
        (report.get("unstructured_comparison") or {}).get("items") or []
    )
    for item in comparisons:
        for side in ("baseline", "current"):
            evidence = item.get(side) or item.get(side + "_evidence") or {}
            if evidence.get("snapshot_id"):
                snapshot_ids.add(evidence["snapshot_id"])
    with get_db_session() as db:
        load_comparison_coverage(db, report, detail.get("periods") or [])
        snapshot_rows = (
            db.query(CommandSnapshot).filter(CommandSnapshot.id.in_(snapshot_ids)).all()
            if snapshot_ids
            else []
        )
        snapshots = {s.id: service._snapshot_comparison_evidence(s) for s in snapshot_rows}
        validate_comparison_references(
            comparisons, {s.id: s for s in snapshot_rows}, detail.get("periods") or []
        )
        # Ongoing risks may have no change event. Still show the exact baseline
        # chosen for this job, never an unrelated latest/nearest snapshot.
        baseline_runs = {
            p.get("patrol_run_id")
            for p in detail.get("periods") or []
            if p.get("role") == "baseline" and p.get("patrol_run_id")
        }
        baselines = {}
        if len(baseline_runs) == 1 and snapshot_rows:
            candidates = defaultdict(list)
            for row in (
                db.query(CommandSnapshot)
                .filter(
                    CommandSnapshot.run_id == next(iter(baseline_runs)),
                    CommandSnapshot.device_id.in_({s.device_id for s in snapshot_rows}),
                )
                .all()
            ):
                candidates[(row.device_id, row.canonical_command)].append(row)
            for row in snapshot_rows:
                matches = candidates[(row.device_id, row.canonical_command)]
                if len(matches) == 1:
                    baselines[row.id] = service._snapshot_comparison_evidence(matches[0])
    seen = {
        (
            i.get("device_id"),
            i.get("entity_key"),
            i.get("field"),
            (i.get("current") or {}).get("snapshot_id"),
        )
        for i in items
        if (i.get("event") or {}).get("event_id")
    }
    for f in source.get("current_findings") or []:
        key = (f.get("device_id"), f.get("entity_key"), f.get("field"), f.get("snapshot_id"))
        # Threshold recovery and baseline-deviation health are independent findings.
        if key in seen and not f.get("health_assessment"):
            continue
        current = dict(snapshots.get(f.get("snapshot_id")) or {})
        current["value"] = f.get("value")
        items.append(
            {
                "current_finding": True,
                "health_assessment": f.get("health_assessment"),
                "finding_id": f.get("observation_id"),
                "device_id": f.get("device_id"),
                "device_name": f.get("device_name"),
                "command": current.get("command"),
                "entity_type": f.get("entity_type"),
                "entity_key": f.get("entity_key"),
                "field": f.get("field"),
                "current": current,
                "baseline": baselines.get(f.get("snapshot_id")) or {},
                "event": {"event_label": "当前风险（不要求本期新增事件）"},
            }
        )
        seen.add(key)
    represented_observations = {
        (item.get("current") or {}).get("observation_id") or item.get("finding_id")
        for item in items
    }
    for trend in (report.get("numeric_trends") or {}).get("items", []):
        observation_id = trend.get("observation_id")
        observation = trend_observations.get(observation_id)
        if not observation or observation_id in represented_observations:
            continue
        snapshot_id = observation.get("snapshot_id")
        current = dict(snapshots.get(snapshot_id) or {})
        current.update(value=observation.get("value"), observation_id=observation_id)
        items.append(
            {
                "numeric_trend": True,
                "trend_explanation": trend.get("explanation"),
                "finding_id": observation_id,
                "device_id": observation.get("device_id"),
                "device_name": observation.get("device_name"),
                "command": current.get("command"),
                "entity_key": observation.get("entity_key"),
                "entity_type": observation.get("entity_type"),
                "field": observation.get("field"),
                "current": current,
                "baseline": baselines.get(snapshot_id) or {},
            }
        )
    return report, source


def export_default(job_id):
    from src.core.patrol.report_renderer import render_report
    from src.core.patrol.report_templates import builtin_bytes

    report, source = report_context(job_id)
    return render_report(builtin_bytes(), report, source)

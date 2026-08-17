"""Exercise trusted RolloutStage execution with Q2/Q3 lixian evidence.

Candidate Parser and rule assets are assembled as transaction-scoped draft
RuntimeBundles. Sanitized Q2/Q3 samples are copied into immutable Snapshot
periods, then the production RolloutCampaign/Outbox/StageExecution path performs
same-source candidate projection and evidence evaluation. The outer database
transaction is always rolled back, the MinIO evidence prefix is deleted, and no
candidate is approved or activated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.test.check_item_golden_runtime_replay import (  # noqa: E402
    _database_baseline,
    _expand_regression_samples,
    _load_assets,
    _load_source,
    _resolve_template_id,
    _seed_runtime_bundle,
)
from src.core.check_items.catalog import CheckItemCatalogRepository  # noqa: E402
from src.core.check_items.rollout import RolloutCampaignService  # noqa: E402
from src.core.check_items.rollout_execution import (  # noqa: E402
    RolloutOutboxRepository,
    RolloutStageExecutionService,
)
from src.infrastructure.db.models import (  # noqa: E402
    CommandSnapshot,
    ComparisonContextRecord,
    NetworkDevice,
    PatrolRun,
)
from src.infrastructure.db.postgres import engine  # noqa: E402
from src.infrastructure.storage.minio_client import (  # noqa: E402
    get_minio_storage,
)


SCENARIOS = (
    {
        "scenario_id": "cpu-h3c-msr5620",
        "check_item_id": "system.cpu_usage",
        "template_scope": {
            "vendor": "H3C",
            "profile": "MSR5620",
            "command": "display cpu",
        },
        "regression_globs": {
            "baseline": ["lixian/Q2/*RT5620*.txt"],
            "current": ["lixian/Q3/*RT5620*.txt"],
        },
        "asset_file": "cpu.json",
    },
    {
        "scenario_id": "interface-h3c-msr5620",
        "check_item_id": "interface.oper_status",
        "template_scope": {
            "vendor": "H3C",
            "profile": "MSR5620",
            "command": "display interface brief",
        },
        "regression_globs": {
            "baseline": ["lixian/Q2/*RT5620*.txt"],
            "current": ["lixian/Q3/*RT5620*.txt"],
        },
        "asset_file": "interface-h3c.json",
    },
    {
        "scenario_id": "optical-h3c-s6850",
        "check_item_id": "interface.optical_power",
        "template_scope": {
            "vendor": "H3C",
            "profile": "S6850-56HF",
            "command": "display transceiver diagnosis interface",
        },
        "regression_globs": {
            "baseline": ["lixian/Q2/SW*.txt"],
            "current": ["lixian/Q3/SW*.txt"],
        },
        "asset_path": ROOT / ".tmp" / "s6850-r6-rule-chain-dry-run-v7.json",
    },
)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _classify_acceptance(summary: dict[str, Any]) -> dict[str, Any]:
    """Separate evidence-pipeline success from rollout eligibility."""

    metrics = dict(summary.get("metrics") or {})
    execution_evidence_complete = bool(
        str(summary.get("execution_status") or "") == "completed"
        and int(summary.get("target_count") or 0) > 0
        and int(summary.get("snapshot_count") or 0)
        == int(summary.get("projection_count") or -1)
        and float(metrics.get("runtime_bundle_coverage_rate") or 0.0) == 1.0
        and bool(summary.get("database_unchanged"))
        and bool(summary.get("minio_cleanup_verified"))
    )
    rollout_eligible = bool(
        execution_evidence_complete
        and str(summary.get("campaign_status") or "") == "completed"
        and str(summary.get("stage_attempt_status") or "") == "passed"
    )
    acceptance_status = (
        "eligible"
        if rollout_eligible
        else (
            "fail_closed"
            if execution_evidence_complete
            else "evidence_incomplete"
        )
    )
    return {
        "success": execution_evidence_complete,
        "execution_evidence_complete": execution_evidence_complete,
        "rollout_eligible": rollout_eligible,
        "acceptance_status": acceptance_status,
    }


def _utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _prepare_scenarios(asset_dir: Path) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for item in SCENARIOS:
        template_id = _resolve_template_id(dict(item["template_scope"]))
        source_template, samples = _load_source(
            template_id,
            regression_samples=_expand_regression_samples(
                dict(item["regression_globs"])
            ),
        )
        asset_path = item.get("asset_path")
        assets = _load_assets(
            Path(asset_path)
            if asset_path is not None
            else asset_dir / str(item["asset_file"])
        )
        grouped: dict[str, set[str]] = defaultdict(set)
        for sample in samples:
            grouped[str(sample["device_id"])].add(str(sample["period"]))
        complete_devices = {
            device_id
            for device_id, periods in grouped.items()
            if periods == {"baseline", "current"}
        }
        complete_samples = [
            sample
            for sample in samples
            if str(sample["device_id"]) in complete_devices
        ]
        if not complete_samples:
            raise AssertionError(
                f"Q2/Q3 complete device pair is missing: {item['scenario_id']}"
            )
        prepared.append(
            {
                **item,
                "template_id": template_id,
                "source_template": source_template,
                "samples": complete_samples,
                "candidate": assets,
            }
        )
    return prepared


def _seed_inventory(
    db: Any,
    prepared: list[dict[str, Any]],
    *,
    token: str,
    now: datetime,
) -> dict[str, str]:
    identity: dict[str, dict[str, str]] = {}
    for item in prepared:
        template = item["source_template"]
        for sample in item["samples"]:
            source_id = str(sample["device_id"])
            identity.setdefault(
                source_id,
                {
                    "vendor": str(sample.get("vendor") or template["vendor"]),
                    "model": str(sample.get("model") or template.get("model") or ""),
                    "family": str(template.get("family") or ""),
                },
            )
    device_map: dict[str, str] = {}
    for ordinal, (source_id, values) in enumerate(
        sorted(identity.items()),
        start=1,
    ):
        device_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"netops/lixian-rollout-device/{token}/{source_id}",
            )
        )
        db.add(
            NetworkDevice(
                id=device_id,
                device_name=f"rollout-device-{ordinal}-{token[:6]}",
                management_ip=f"192.0.2.{ordinal}",
                vendor=values["vendor"],
                model=values["model"],
                family=values["family"] or None,
                site="transactional-lixian",
                role="validation",
                status="active",
                attributes_json={
                    "source": "lixian_sanitized_q2_q3",
                    "production_authoritative": False,
                },
                created_at=now,
                updated_at=now,
            )
        )
        device_map[source_id] = device_id
    db.flush()
    return device_map


def _seed_snapshots(
    db: Any,
    prepared: list[dict[str, Any]],
    runtimes_by_track: dict[str, dict[str, dict[str, Any]]],
    device_map: dict[str, str],
    *,
    token: str,
    now: datetime,
) -> dict[tuple[str, str], dict[str, list[dict[str, Any]]]]:
    grouped: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = (
        defaultdict(lambda: {"baseline": [], "current": []})
    )
    run_rows: dict[str, PatrolRun] = {}
    run_snapshot_counts: Counter[str] = Counter()
    run_commands: dict[str, set[str]] = defaultdict(set)

    for item in prepared:
        scenario_id = str(item["scenario_id"])
        candidate_runtime = runtimes_by_track["candidate"][scenario_id]
        check_item_version_id = str(candidate_runtime["check_item"].version_id)
        command = str(item["source_template"]["canonical_command"])
        for sample in item["samples"]:
            source_device = str(sample["device_id"])
            device_id = device_map[source_device]
            period = str(sample["period"])
            observed_at = _utc(sample["observed_at"])
            run_id = (
                f"lixian-rollout-{token}-{period}-"
                f"{device_id.replace('-', '')[:12]}"
            )
            if run_id not in run_rows:
                row = PatrolRun(
                    id=run_id,
                    mode="parser_validation",
                    source="offline_file",
                    status="completed",
                    collection_status="completed",
                    parser_readiness_status="ready",
                    fact_readiness_status="ready",
                    analysis_status="completed",
                    baseline_eligible=True,
                    run_purpose="historical_backfill",
                    logical_run_id=run_id,
                    attempt_number=0,
                    owner_generation=1,
                    device_count=1,
                    command_count=0,
                    started_at=observed_at,
                    completed_at=observed_at,
                    metadata_json={
                        "purpose": "rollout_shadow_validation",
                        "period": period,
                        "production_authoritative": False,
                    },
                    created_at=observed_at,
                    updated_at=observed_at,
                )
                db.add(row)
                db.flush()
                run_rows[run_id] = row
            raw_text = str(sample["sanitized_text"])
            for track in ("baseline", "candidate"):
                runtime = runtimes_by_track[track][scenario_id]
                implementation_id = str(
                    runtime["bundle"]["implementation_version_id"]
                )
                snapshot_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"netops/lixian-rollout-snapshot/{token}/{track}/"
                        f"{scenario_id}/{sample['sample_id']}",
                    )
                )
                db.add(
                    CommandSnapshot(
                        id=snapshot_id,
                        run_id=run_id,
                        inventory_device_id=device_id,
                        device_id=device_id,
                        execution_item_id=(
                            f"rollout-item-{track}-{snapshot_id[:12]}"
                        ),
                        implementation_version_id=implementation_id,
                        check_item_ids_json=[str(item["check_item_id"])],
                        device_name=f"device-{device_id[:8]}",
                        management_ip=None,
                        vendor=str(sample.get("vendor") or ""),
                        model=str(sample.get("model") or ""),
                        command=command,
                        canonical_command=command,
                        parser_template_id=None,
                        parser_version=None,
                        observed_at=observed_at,
                        structured_json=None,
                        raw_text=raw_text,
                        raw_text_hash=hashlib.sha256(
                            raw_text.encode("utf-8")
                        ).hexdigest(),
                        parse_status="not_attempted",
                        output_status="normal",
                        snapshot_kind="full",
                        metadata_json={
                            "source_sample_id": str(sample["sample_id"]),
                            "rollout_source_track": track,
                            "sanitized": True,
                            "production_authoritative": False,
                        },
                        created_at=observed_at,
                    )
                )
                if track == "candidate":
                    grouped[(scenario_id, device_id)][period].append(
                        {
                            "snapshot_id": snapshot_id,
                            "run_id": run_id,
                            "observed_at": observed_at,
                            "check_item_version_id": check_item_version_id,
                            "implementation_version_id": implementation_id,
                        }
                    )
                run_snapshot_counts[run_id] += 1
            run_commands[run_id].add(command)

    for run_id, row in run_rows.items():
        row.command_count = len(run_commands[run_id])
        row.metadata_json = {
            **dict(row.metadata_json or {}),
            "snapshot_count": run_snapshot_counts[run_id],
        }
    db.flush()
    return grouped


def _seed_contexts(
    db: Any,
    grouped: dict[tuple[str, str], dict[str, list[dict[str, Any]]]],
    *,
    token: str,
    now: datetime,
) -> tuple[list[dict[str, Any]], list[str]]:
    matrix: list[dict[str, Any]] = []
    context_ids: list[str] = []
    for (scenario_id, device_id), periods in sorted(grouped.items()):
        if not periods["baseline"] or not periods["current"]:
            raise AssertionError(
                f"incomplete frozen periods: {scenario_id}/{device_id}"
            )
        baseline = sorted(
            periods["baseline"],
            key=lambda item: item["observed_at"],
        )
        current = sorted(
            periods["current"],
            key=lambda item: item["observed_at"],
        )
        scope = current[-1]
        context_payload = {
            "scenario_id": scenario_id,
            "device_id": device_id,
            "check_item_version_id": scope["check_item_version_id"],
            "baseline_snapshot_ids": [
                item["snapshot_id"] for item in baseline
            ],
            "current_snapshot_ids": [
                item["snapshot_id"] for item in current
            ],
        }
        context_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"netops/lixian-rollout-context/{token}/"
                f"{scenario_id}/{device_id}",
            )
        )
        db.add(
            ComparisonContextRecord(
                id=context_id,
                digest=_digest(context_payload),
                mode="explicit_periods",
                device_id=device_id,
                check_item_version_id=scope["check_item_version_id"],
                current_observed_at=current[-1]["observed_at"],
                baseline_observed_at=baseline[-1]["observed_at"],
                baseline_run_id=baseline[-1]["run_id"],
                baseline_snapshot_ids_json=[
                    item["snapshot_id"] for item in baseline
                ],
                canonical_schema_version="canonical-observation/v3",
                entity_identity_policy_version="check-item-catalog/v7",
                selection_reason="frozen lixian Q2/Q3 shadow validation",
                metadata_json={
                    "purpose": "shadow_validation",
                    "production_authoritative": False,
                },
                created_at=now,
            )
        )
        matrix.append(
            {
                "scenario_id": scenario_id,
                "device_id": device_id,
                "check_item_version_id": scope["check_item_version_id"],
                "implementation_version_id": scope[
                    "implementation_version_id"
                ],
            }
        )
        context_ids.append(context_id)
    db.flush()
    return matrix, context_ids


def _render_report(summary: dict[str, Any]) -> str:
    metrics = dict(summary.get("metrics") or {})
    lines = [
        "# Lixian Q2/Q3 可信灰度执行验收",
        "",
        "## 边界",
        "",
        "- 数据源为脱敏后的 `lixian` Q2/Q3 Parser Job 样本。",
        "- baseline 与 candidate 使用独立采集实现和 RuntimeBundle，读取同一份冻结样本形成双轨投影。",
        "- baseline RuntimeBundle 只在事务内临时激活，candidate 保持 draft；两条路线均不构成生产事实。",
        "- 数据库事务已回滚，MinIO 临时证据已删除，未审批、未激活、未切换生产路由。",
        "",
        "## 执行结果",
        "",
        f"- Campaign 状态：`{summary['campaign_status']}`",
        f"- 阶段尝试状态：`{summary['stage_attempt_status']}`",
        f"- StageExecution 状态：`{summary['execution_status']}`",
        f"- 目标对：{summary['target_count']}",
        f"- 冻结 Snapshot：{summary['snapshot_count']}",
        f"- 隔离投影：{summary['projection_count']}",
        f"- 不可变比较证据：`{summary['comparison_manifest_id']}`",
        "",
        "## 机器反算指标",
        "",
        "| 指标 | 值 |",
        "|---|---:|",
    ]
    for key, value in sorted(metrics.items()):
        lines.append(f"| `{key}` | {value} |")
    lines.extend(
        [
            "",
            "## 比较域",
            "",
            "| 比较域 | 状态 |",
            "|---|---|",
        ]
    )
    for key, value in sorted(
        dict(summary.get("comparison_statuses") or {}).items()
    ):
        lines.append(f"| `{key}` | {value} |")
    lines.extend(
        [
            "",
            "## 候选目标诊断",
            "",
            "| 实现 | Parser | Observation | Assessment | 阻断原因 |",
            "|---|---|---:|---|---|",
        ]
    )
    for item in summary.get("target_diagnostics") or []:
        parser_status = ", ".join(
            f"{key}={value}"
            for key, value in sorted(
                dict(item.get("parser_status_counts") or {}).items()
            )
        )
        blockers = ", ".join(item.get("blocking_reason_codes") or []) or "-"
        lines.append(
            "| {scenario} | {parser} | {observations} | {assessment} | "
            "{blockers} |".format(
                scenario=item["scenario_id"],
                parser=parser_status or "-",
                observations=item["observation_count"],
                assessment=item["assessment_status"],
                blockers=blockers,
            )
        )
    lines.extend(
        [
            "",
            "## 发布结论",
            "",
            (
                "本次证明可信 `rollout-target/v3` 能以独立 baseline/candidate "
                "采集实现从同一组冻结证据形成可复核的双轨投影和比较证据。"
                "正式发布仍需人工审批、"
                "`live_capture`、目标矩阵冻结和两个生产周期。"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def run(*, asset_dir: Path, output_dir: Path) -> dict[str, Any]:
    before = _database_baseline()
    prepared = _prepare_scenarios(asset_dir)
    token = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    connection = engine.connect()
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

    campaign_id: str | None = None
    evidence_prefix: str | None = None
    summary: dict[str, Any] | None = None
    storage = get_minio_storage()
    try:
        catalog_repository = CheckItemCatalogRepository(scope)
        catalog_repository.bootstrap(actor="lixian-rollout-e2e")
        catalog = catalog_repository.load_active()
        runtimes_by_track: dict[str, dict[str, dict[str, Any]]] = {
            "baseline": {},
            "candidate": {},
        }
        for index, item in enumerate(prepared, start=1):
            scenario_id = str(item["scenario_id"])
            runtimes_by_track["baseline"][scenario_id] = _seed_runtime_bundle(
                scope=scope,
                catalog=catalog,
                check_item_id=str(item["check_item_id"]),
                source_template=item["source_template"],
                candidate_assets=item["candidate"]["candidate_assets"],
                token=f"{token}-{index}-baseline",
                activate_bundle=True,
            )
            runtimes_by_track["candidate"][scenario_id] = _seed_runtime_bundle(
                scope=scope,
                catalog=catalog,
                check_item_id=str(item["check_item_id"]),
                source_template=item["source_template"],
                candidate_assets=item["candidate"]["candidate_assets"],
                token=f"{token}-{index}-candidate",
                activate_bundle=False,
            )

        with scope() as db:
            device_map = _seed_inventory(
                db,
                prepared,
                token=token,
                now=now,
            )
            periods = _seed_snapshots(
                db,
                prepared,
                runtimes_by_track,
                device_map,
                token=token,
                now=now,
            )
            matrix, context_ids = _seed_contexts(
                db,
                periods,
                token=token,
                now=now,
            )

        runtime_refs = []
        runtime_by_track: dict[str, dict[str, dict[str, Any]]] = {
            "baseline": {},
            "candidate": {},
        }
        for track, runtimes in runtimes_by_track.items():
            for scenario_id, runtime in runtimes.items():
                bundle = dict(runtime["bundle"])
                runtime_by_track[track][scenario_id] = bundle
                runtime_refs.append(
                    {
                        "role": track,
                        "version_id": str(bundle["version_id"]),
                        "digest": str(bundle["digest"]),
                    }
                )
        target_matrix = [
            {
                "device_id": item["device_id"],
                "check_item_version_id": item["check_item_version_id"],
                "baseline_route": {
                    "mode": "runtime_bundle",
                    "runtime_bundle_version_id": runtime_by_track["baseline"][
                        item["scenario_id"]
                    ]["version_id"],
                },
                "candidate_runtime_bundle_version_id": (
                    runtime_by_track["candidate"][item["scenario_id"]][
                        "version_id"
                    ]
                ),
            }
            for item in matrix
        ]
        observed_times = [
            _utc(sample["observed_at"])
            for item in prepared
            for sample in item["samples"]
        ]
        controller = RolloutCampaignService(scope)
        campaign = controller.create(
            name=f"Lixian Q2/Q3 trusted shadow {token}",
            description="Transaction-scoped trusted rollout acceptance.",
            actor="rollout-campaign-maker",
            idempotency_key=f"lixian-rollout-create-{token}",
        )
        campaign = controller.freeze_target(
            campaign["id"],
            target_manifest={
                "selector": {
                    "purpose": "shadow_validation",
                    "source": "lixian_q2_q3",
                },
                "matrix": target_matrix,
            },
            runtime_bundle_refs=runtime_refs,
            comparison_context_ids=context_ids,
            baseline_window={
                "start_at": (
                    min(observed_times) - timedelta(days=1)
                ).isoformat(),
                "end_at": (
                    max(observed_times) + timedelta(days=1)
                ).isoformat(),
            },
            thresholds={},
            rollback_plan={
                "mode": "runtime_bundle",
                "verified": True,
                "evidence_refs": [
                    {
                        "ref": "transactional-baseline-route-rehearsal",
                        "production_authoritative": False,
                    }
                ],
            },
            actor="rollout-release-manager",
            reason="freeze Q2/Q3 shadow targets and candidate digests",
            idempotency_key=f"lixian-rollout-freeze-{token}",
        )
        campaign_id = str(campaign["id"])
        evidence_prefix = f"rollout/evidence/{campaign_id}/"
        started = controller.start_stage(
            campaign_id,
            actor="rollout-stage-operator",
            reason="run same-source Q2/Q3 trusted shadow evaluation",
            idempotency_key=f"lixian-rollout-start-{token}",
        )
        execution_id = str(started["stage_executions"][0]["id"])

        outbox = RolloutOutboxRepository(scope)
        claimed = outbox.claim_pending(limit=10)
        matching = [
            item
            for item in claimed
            if str(item["aggregate_id"]) == execution_id
        ]
        if len(matching) != 1:
            raise AssertionError(
                "trusted StageExecution Outbox was not claimed exactly once"
            )
        outbox.mark_published(
            str(matching[0]["id"]),
            claim_token=str(matching[0]["claim_token"]),
        )

        executor = RolloutStageExecutionService(
            scope,
            object_storage=storage,
        )
        result = executor.execute(
            execution_id,
            worker_owner=f"lixian-e2e-worker-{token}",
            worker_task_id=f"lixian-e2e-task-{token}",
        )
        detail = executor.get(execution_id)
        if detail is None:
            raise AssertionError("completed StageExecution cannot be reloaded")
        evidence = dict(detail.get("comparison_evidence") or {})
        manifest = dict(evidence.get("manifest_json") or {})
        if manifest.get("raw_cli_included") is not False:
            raise AssertionError("comparison evidence exposed raw CLI")
        projections = list(detail.get("projections") or [])
        if len(projections) != len(target_matrix) * 2:
            raise AssertionError(
                "baseline/candidate projection cardinality is incomplete"
            )
        if any(
            dict(item.get("facts_json") or {}).get("raw_cli_included")
            is not False
            for item in projections
        ):
            raise AssertionError("isolated projection exposed raw CLI")

        comparison_statuses = {
            str(domain): str(dict(value or {}).get("status") or "unknown")
            for domain, value in dict(
                manifest.get("comparisons") or {}
            ).items()
        }
        scenario_by_bundle = {
            str(bundle["version_id"]): scenario_id
            for scenario_id, bundle in runtime_by_track["candidate"].items()
        }
        target_diagnostics = []
        for projection in projections:
            if str(projection.get("track")) != "candidate":
                continue
            facts = dict(projection.get("facts_json") or {})
            assessment = dict(facts.get("assessment") or {})
            parse_rows = list(facts.get("parse_results") or [])
            target_diagnostics.append(
                {
                    "scenario_id": scenario_by_bundle.get(
                        str(projection.get("runtime_bundle_version_id") or ""),
                        "unknown",
                    ),
                    "target_key": str(projection.get("target_key") or ""),
                    "projection_status": str(
                        projection.get("status") or ""
                    ),
                    "projection_failure_code": (
                        projection.get("failure_code")
                    ),
                    "parser_status_counts": dict(
                        sorted(
                            Counter(
                                str(item.get("parser_status") or "unknown")
                                for item in parse_rows
                            ).items()
                        )
                    ),
                    "parser_failure_codes": sorted(
                        {
                            str(item.get("failure_code"))
                            for item in parse_rows
                            if item.get("failure_code")
                        }
                    ),
                    "observation_count": int(
                        assessment.get("observation_count") or 0
                    ),
                    "assessment_status": str(
                        assessment.get("assessment_status") or "unknown"
                    ),
                    "completeness_status": str(
                        assessment.get("completeness_status") or "unknown"
                    ),
                    "blocking_reason_codes": list(
                        assessment.get("blocking_reason_codes") or []
                    ),
                    "change_count": len(facts.get("changes") or []),
                    "event_count": len(facts.get("events") or []),
                    "finding_count": len(facts.get("findings") or []),
                }
            )
        target_diagnostics.sort(
            key=lambda item: (
                item["scenario_id"],
                item["target_key"],
            )
        )
        current_attempt = dict(result["campaign"]["current_attempt"])
        summary = {
            "success": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "trusted_rollout_snapshot_replay",
            "target_manifest_schema": str(
                campaign["target_manifest_json"]["schema_version"]
            ),
            "purpose": "shadow_validation",
            "source": "lixian/Q2,Q3 sanitized Parser Job Samples",
            "raw_cli_included": False,
            "production_assets_activated": False,
            "production_route_changed": False,
            "campaign_id": campaign_id,
            "campaign_status": str(result["campaign"]["status"]),
            "stage_attempt_status": str(current_attempt["status"]),
            "execution_id": execution_id,
            "execution_status": str(detail["status"]),
            "target_count": len(target_matrix),
            "snapshot_count": len(
                detail.get("source_snapshot_ids_json") or []
            ),
            "projection_count": len(projections),
            "comparison_manifest_id": str(
                detail.get("comparison_manifest_id") or ""
            ),
            "comparison_manifest_digest": str(
                evidence.get("manifest_digest") or ""
            ),
            "artifact_sha256": str(
                evidence.get("artifact_sha256") or ""
            ),
            "metrics": dict(result.get("metrics") or {}),
            "comparison_statuses": dict(
                sorted(comparison_statuses.items())
            ),
            "target_diagnostics": target_diagnostics,
            "scenario_count": len(prepared),
            "candidate_parser_ids": [
                str(item["template_id"]) for item in prepared
            ],
            "candidate_runtime_bundle_ids": [
                str(item["version_id"])
                for item in runtime_by_track["candidate"].values()
            ],
            "baseline_runtime_bundle_ids": [
                str(item["version_id"])
                for item in runtime_by_track["baseline"].values()
            ],
            "candidate_implementation_version_ids": [
                str(item["implementation_version_id"])
                for item in runtime_by_track["candidate"].values()
            ],
            "baseline_implementation_version_ids": [
                str(item["implementation_version_id"])
                for item in runtime_by_track["baseline"].values()
            ],
        }
    finally:
        outer_transaction.rollback()
        connection.close()
        deleted_objects = (
            storage.delete_objects_by_prefix(evidence_prefix)
            if evidence_prefix
            else 0
        )

    if summary is None:
        raise AssertionError("trusted rollout summary was not produced")
    after = _database_baseline()
    summary["database_before"] = before
    summary["database_after"] = after
    summary["database_unchanged"] = before == after
    summary["minio_deleted_object_count"] = deleted_objects
    summary["minio_cleanup_verified"] = deleted_objects >= 1
    summary.update(_classify_acceptance(summary))
    if not summary["database_unchanged"]:
        raise AssertionError(
            f"transactional rollout changed production state: {before} != {after}"
        )
    if not summary["minio_cleanup_verified"]:
        raise AssertionError("temporary rollout evidence was not removed")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (output_dir / "acceptance-report.md").write_text(
        _render_report(summary),
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--asset-dir",
        type=Path,
        default=ROOT / ".tmp" / "golden-rule-chain-full",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / ".tmp" / "check-item-rollout-stage-lixian-e2e",
    )
    args = parser.parse_args()
    summary = run(
        asset_dir=args.asset_dir.resolve(),
        output_dir=args.output_dir.resolve(),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

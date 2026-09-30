"""Reconcile one patrol run into terminal check-item facts."""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

from src.core.check_items.execution import (
    CheckItemExecutionService,
    EvidenceExecutionResultInput,
    StructuredExecutionResultInput,
    TerminalFailureInput,
    TextExecutionResultInput,
)
from src.infrastructure.db.models import (
    CheckExecutionRecord,
    CheckItemVersionRecord,
    CommandSnapshot,
    ComparisonContextRecord,
    EvidenceQualificationRecord,
    NetworkChangeRecord,
    NetworkEventOccurrenceRecord,
    NetworkEventRecord,
    PatrolCollectionResult,
    StateObservationRecord,
)
from src.infrastructure.db.postgres import get_db_session


class CheckItemRunFinalizer:
    """Materialize the device x check-item result matrix after analysis."""

    _PARSED_STATUSES = {"parsed", "parse_empty"}
    _TEXT_STATUSES = {"raw_hashed", "parsed", "parse_empty", "raw_only"}
    _FAILURE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
    _COLLECTION_FAILURE_ALIASES = {
        "timeout": "command_timeout",
        "failed": "command_failed",
        "cancelled": "command_cancelled",
        "collection_failed": "command_failed",
    }

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = (
            get_db_session
        ),
        *,
        execution_service: CheckItemExecutionService | None = None,
    ) -> None:
        self._session_scope = session_scope
        self._execution_service = execution_service or (
            CheckItemExecutionService(session_scope)
        )

    def finalize(
        self,
        *,
        patrol_batch_id: str,
        run_id: str,
        device_id: str,
    ) -> dict[str, Any]:
        contexts = self._load_contexts(
            patrol_batch_id=patrol_batch_id,
            run_id=run_id,
            device_id=device_id,
        )
        results: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for context in contexts:
            execution = context["execution"]
            if execution["assessment_id"]:
                results.append(
                    {
                        "execution_id": execution["id"],
                        "status": "already_terminal",
                        "assessment_id": execution["assessment_id"],
                    }
                )
                continue
            try:
                collection_failure = self._collection_terminal_failure(
                    context
                )
                if collection_failure is not None:
                    self._qualify_available_evidence(context)
                    result = self._execution_service.fail_execution(
                        str(execution["id"]),
                        status="collection_failed",
                        reason_codes=collection_failure["reason_codes"],
                        evidence_refs=collection_failure["evidence_refs"],
                    )
                    failures.append(
                        {
                            "execution_id": execution["id"],
                            "reason_code": collection_failure[
                                "reason_codes"
                            ][0],
                            "error": collection_failure["message"],
                        }
                    )
                else:
                    result = self._finalize_one(context)
            except Exception as exc:
                reason = (
                    "parse_or_observation_missing"
                    if context["result_mode"] == "structured"
                    else "comparison_input_missing"
                    if context["result_mode"]
                    in {"normalized_diff", "hash_only"}
                    else "evidence_missing"
                )
                result = self._execution_service.fail_execution(
                    str(execution["id"]),
                    status=(
                        "parse_failed"
                        if context["result_mode"] == "structured"
                        else "comparison_failed"
                        if context["result_mode"]
                        in {"normalized_diff", "hash_only"}
                        else "collection_failed"
                    ),
                    reason_codes=(reason, type(exc).__name__),
                    evidence_refs=tuple(context["snapshot_ids"]),
                )
                failures.append(
                    {
                        "execution_id": execution["id"],
                        "reason_code": reason,
                        "error": str(exc),
                    }
                )
            results.append(result)
        return {
            "success": not failures,
            "patrol_batch_id": patrol_batch_id,
            "run_id": run_id,
            "device_id": device_id,
            "execution_count": len(contexts),
            "terminal_count": len(results),
            "failure_count": len(failures),
            "failures": failures,
            "results": results,
        }

    def fail_device(
        self,
        *,
        patrol_batch_id: str,
        device_id: str,
        reason_code: str,
        status: str = "collection_failed",
        evidence_refs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        with self._session_scope() as db:
            rows = (
                db.query(CheckExecutionRecord)
                .filter(
                    CheckExecutionRecord.patrol_batch_id
                    == patrol_batch_id,
                    CheckExecutionRecord.device_id == device_id,
                    CheckExecutionRecord.assessment_id.is_(None),
                )
                .all()
            )
            execution_ids = [str(item.id) for item in rows]
        results = [
            self._execution_service.fail_execution(
                execution_id,
                status=status,
                reason_codes=(reason_code,),
                evidence_refs=evidence_refs,
            )
            for execution_id in execution_ids
        ]
        return {
            "patrol_batch_id": patrol_batch_id,
            "device_id": device_id,
            "failed_execution_count": len(results),
            "results": results,
        }

    def fail_devices(
        self,
        *,
        patrol_batch_id: str,
        failures: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Settle terminal device failures without per-device DB lookups."""

        by_device = {
            str(item.get("device_id") or ""): dict(item)
            for item in failures
            if str(item.get("device_id") or "")
        }
        if not by_device:
            return {
                "patrol_batch_id": patrol_batch_id,
                "failed_execution_count": 0,
                "results": [],
            }
        execution_overrides: dict[str, dict[str, Any]] = {}
        for device_id, failure in by_device.items():
            if str(failure.get("status") or "") not in {
                "parse_failed",
                "normalization_failed",
            }:
                continue
            run_id = str(failure.get("run_id") or "")
            if not run_id:
                continue
            contexts = self._load_contexts(
                patrol_batch_id=patrol_batch_id,
                run_id=run_id,
                device_id=device_id,
            )
            for context in contexts:
                execution = context["execution"]
                if execution.get("assessment_id"):
                    continue
                collection_failure = self._collection_terminal_failure(
                    context
                )
                if collection_failure is not None:
                    execution_overrides[str(execution["id"])] = {
                        "status": "collection_failed",
                        "reason_codes": collection_failure[
                            "reason_codes"
                        ],
                        "evidence_refs": collection_failure[
                            "evidence_refs"
                        ],
                    }
                    continue
                self._qualify_available_evidence(context)
                execution_overrides[str(execution["id"])] = {
                    "status": str(failure["status"]),
                    "reason_codes": (
                        str(failure["reason_code"]),
                    ),
                    "evidence_refs": tuple(
                        dict.fromkeys(
                            [
                                *(
                                    str(item)
                                    for item in failure.get(
                                        "evidence_refs",
                                        (),
                                    )
                                    if str(item)
                                ),
                                *(
                                    str(item)
                                    for item in context.get(
                                        "snapshot_ids",
                                        (),
                                    )
                                    if str(item)
                                ),
                            ]
                        )
                    ),
                }
        with self._session_scope() as db:
            rows = (
                db.query(CheckExecutionRecord)
                .filter(
                    CheckExecutionRecord.patrol_batch_id
                    == patrol_batch_id,
                    CheckExecutionRecord.device_id.in_(by_device),
                    CheckExecutionRecord.assessment_id.is_(None),
                )
                .order_by(
                    CheckExecutionRecord.device_id,
                    CheckExecutionRecord.check_item_version_id,
                )
                .all()
            )
            inputs: list[TerminalFailureInput] = []
            for row in rows:
                failure = execution_overrides.get(str(row.id))
                if failure is None:
                    device_failure = by_device[str(row.device_id)]
                    failure = {
                        "status": str(device_failure["status"]),
                        "reason_codes": (
                            str(device_failure["reason_code"]),
                        ),
                        "evidence_refs": tuple(
                            device_failure.get("evidence_refs") or ()
                        ),
                    }
                inputs.append(
                    TerminalFailureInput(
                        execution_id=str(row.id),
                        status=str(failure["status"]),
                        reason_codes=tuple(failure["reason_codes"]),
                        evidence_refs=tuple(failure["evidence_refs"]),
                    )
                )
        coverage_failures = [
            item
            for item in inputs
            if item.status in {"parse_failed", "normalization_failed"}
        ]
        bulk_failures = [
            item
            for item in inputs
            if item.status not in {"parse_failed", "normalization_failed"}
        ]
        results = self._execution_service.fail_executions_bulk(bulk_failures)
        results.extend(
            self._execution_service.fail_execution(
                item.execution_id,
                status=item.status,
                reason_codes=item.reason_codes,
                evidence_refs=item.evidence_refs,
            )
            for item in coverage_failures
        )
        return {
            "patrol_batch_id": patrol_batch_id,
            "failed_execution_count": len(results),
            "results": results,
        }

    def _finalize_one(self, context: dict[str, Any]) -> dict[str, Any]:
        execution = context["execution"]
        self._qualify_available_evidence(context)
        snapshot_facts = tuple(context["snapshot_facts"])
        snapshots = context["selected_snapshots"]
        if not snapshots:
            raise ValueError("no selected collection output exists")
        result_mode = context["result_mode"]
        evidence_refs = tuple(
            item
            for item in [
                *(str(item["id"]) for item in snapshots),
                str(execution["runtime_bundle_version_id"] or ""),
            ]
            if item
        )
        assessed_at = datetime.now(timezone.utc)
        if result_mode == "structured":
            observations = tuple(context["observations"])
            return self._execution_service.finalize_structured(
                StructuredExecutionResultInput(
                    execution_id=str(execution["id"]),
                    observations=observations,
                    snapshot_facts=snapshot_facts,
                    change_ids=tuple(context["change_ids"]),
                    event_ids=tuple(context["event_ids"]),
                    event_occurrence_ids=tuple(
                        context["event_occurrence_ids"]
                    ),
                    expected_entity_keys=tuple(
                        context.get("expected_entity_keys") or ()
                    ),
                    coverage_denominator_ref=dict(
                        context.get("coverage_denominator_ref") or {}
                    ),
                    evidence_refs=evidence_refs,
                    assessed_at=assessed_at,
                )
            )
        if result_mode in {"normalized_diff", "hash_only"}:
            current = snapshots[-1]
            return self._execution_service.finalize_text(
                TextExecutionResultInput(
                    execution_id=str(execution["id"]),
                    current_snapshot_id=str(current["id"]),
                    previous_snapshot_id=context[
                        "previous_snapshot_id"
                    ],
                    evidence_refs=evidence_refs,
                    assessed_at=assessed_at,
                )
            )
        if result_mode == "evidence_only":
            return self._execution_service.finalize_evidence(
                EvidenceExecutionResultInput(
                    execution_id=str(execution["id"]),
                    evidence_refs=evidence_refs,
                    assessed_at=assessed_at,
                )
            )
        raise ValueError(f"unsupported check-item result mode: {result_mode}")

    def _qualify_available_evidence(self, context: dict[str, Any]) -> None:
        snapshot_facts = tuple(context["snapshot_facts"])
        if not snapshot_facts:
            return
        self._execution_service.qualify_evidence(
            str(context["execution"]["id"]),
            snapshot_facts=snapshot_facts,
            evidence_refs=tuple(
                str(item["id"])
                for item in snapshot_facts
                if item.get("id")
            ),
            qualified_at=datetime.now(timezone.utc),
        )

    @classmethod
    def _collection_terminal_failure(
        cls,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Classify collection failures before Parser and comparison failures."""

        if context["selected_snapshots"]:
            return None
        rows = tuple(context.get("collection_results") or ())
        complete_rows = tuple(
            item
            for item in rows
            if str(item.get("execution_status") or "").lower()
            in {"complete", "completed"}
        )
        failed_rows = tuple(
            item
            for item in rows
            if str(item.get("execution_status") or "").lower()
            in {"failed", "timeout", "cancelled"}
        )
        if complete_rows:
            if context.get("action_snapshots"):
                return None
            return {
                "reason_codes": ("snapshot_persistence_failed",),
                "evidence_refs": tuple(
                    str(item.get("id"))
                    for item in complete_rows
                    if item.get("id")
                ),
                "message": (
                    "collection completed but no command snapshot was "
                    "persisted for the check item"
                ),
            }
        if failed_rows:
            reasons: list[str] = []
            for item in failed_rows:
                raw_code = str(item.get("failure_code") or "").strip().lower()
                execution_status = str(
                    item.get("execution_status") or "failed"
                ).strip().lower()
                candidate = cls._COLLECTION_FAILURE_ALIASES.get(
                    raw_code,
                    raw_code,
                )
                if not cls._FAILURE_CODE_RE.fullmatch(candidate):
                    candidate = cls._COLLECTION_FAILURE_ALIASES.get(
                        execution_status,
                        "command_failed",
                    )
                if candidate not in reasons:
                    reasons.append(candidate)
            return {
                "reason_codes": tuple(reasons or ["command_failed"]),
                "evidence_refs": tuple(
                    dict.fromkeys(
                        [
                            *(
                                str(item.get("id"))
                                for item in failed_rows
                                if item.get("id")
                            ),
                            *(
                                str(item.get("id"))
                                for item in context.get(
                                    "action_snapshots",
                                    (),
                                )
                                if item.get("id")
                            ),
                        ]
                    )
                ),
                "message": "all collection alternatives failed",
            }
        if context.get("action_snapshots"):
            return None
        return {
            "reason_codes": ("collection_evidence_missing",),
            "evidence_refs": (),
            "message": (
                "no command collection result or command snapshot exists "
                "for the check item"
            ),
        }

    def _load_contexts(
        self,
        *,
        patrol_batch_id: str,
        run_id: str,
        device_id: str,
    ) -> list[dict[str, Any]]:
        with self._session_scope() as db:
            rows = (
                db.query(CheckExecutionRecord, CheckItemVersionRecord)
                .join(
                    CheckItemVersionRecord,
                    CheckItemVersionRecord.id
                    == CheckExecutionRecord.check_item_version_id,
                )
                .filter(
                    CheckExecutionRecord.patrol_batch_id
                    == patrol_batch_id,
                    CheckExecutionRecord.device_id == device_id,
                )
                .order_by(CheckItemVersionRecord.check_item_id)
                .all()
            )
            comparison_ids = {
                str(execution.comparison_context_id)
                for execution, _check_version in rows
                if execution.comparison_context_id
            }
            comparison_rows = (
                db.query(ComparisonContextRecord)
                .filter(ComparisonContextRecord.id.in_(comparison_ids))
                .all()
                if comparison_ids
                else []
            )
            comparison_by_id = {
                str(item.id): item for item in comparison_rows
            }
            baseline_snapshot_ids = {
                str(snapshot_id)
                for item in comparison_rows
                for snapshot_id in item.baseline_snapshot_ids_json or []
                if snapshot_id
            }
            baseline_observations = (
                db.query(StateObservationRecord)
                .filter(
                    StateObservationRecord.snapshot_id.in_(
                        baseline_snapshot_ids
                    )
                )
                .all()
                if baseline_snapshot_ids
                else []
            )
            baseline_observations_by_snapshot: dict[str, list[Any]] = {}
            for observation in baseline_observations:
                baseline_observations_by_snapshot.setdefault(
                    str(observation.snapshot_id),
                    [],
                ).append(observation)
            version_ids = {
                str(execution.check_item_version_id)
                for execution, _check_version in rows
            }
            qualification_rows = (
                db.query(EvidenceQualificationRecord)
                .filter(
                    EvidenceQualificationRecord.device_id == device_id,
                    EvidenceQualificationRecord.check_item_version_id.in_(
                        version_ids
                    ),
                    EvidenceQualificationRecord.source_identity_status
                    == "confirmed",
                    EvidenceQualificationRecord.output_quality_status
                    == "complete",
                    EvidenceQualificationRecord.freshness_status == "fresh",
                )
                .order_by(
                    EvidenceQualificationRecord.qualified_at.desc(),
                    EvidenceQualificationRecord.id.desc(),
                )
                .all()
                if version_ids
                else []
            )
            snapshots = (
                db.query(CommandSnapshot)
                .filter(
                    CommandSnapshot.run_id == run_id,
                    CommandSnapshot.inventory_device_id == device_id,
                )
                .order_by(
                    CommandSnapshot.observed_at,
                    CommandSnapshot.id,
                )
                .all()
            )
            if not snapshots:
                snapshots = (
                    db.query(CommandSnapshot)
                    .filter(
                        CommandSnapshot.run_id == run_id,
                        CommandSnapshot.device_id == device_id,
                    )
                    .order_by(
                        CommandSnapshot.observed_at,
                        CommandSnapshot.id,
                    )
                    .all()
                )
            collection_results = (
                db.query(PatrolCollectionResult)
                .filter(
                    PatrolCollectionResult.run_id == run_id,
                    (
                        PatrolCollectionResult.inventory_device_id
                        == device_id
                    )
                    | (PatrolCollectionResult.device_id == device_id),
                )
                .order_by(
                    PatrolCollectionResult.collection_action_id,
                    PatrolCollectionResult.created_at,
                    PatrolCollectionResult.id,
                )
                .all()
            )
            collection_results_by_action: dict[str, list[Any]] = {}
            for item in collection_results:
                action_id = str(item.collection_action_id or "")
                if action_id:
                    collection_results_by_action.setdefault(
                        action_id,
                        [],
                    ).append(item)
            observations = (
                db.query(StateObservationRecord)
                .filter(StateObservationRecord.run_id == run_id)
                .all()
            )
            observations_by_snapshot: dict[str, list[Any]] = {}
            for observation in observations:
                observations_by_snapshot.setdefault(
                    str(observation.snapshot_id),
                    [],
                ).append(observation)

            contexts: list[dict[str, Any]] = []
            for execution, check_version in rows:
                action_ids = {
                    str(item)
                    for item in execution.collection_action_ids_json or []
                }
                action_snapshots = [
                    item
                    for item in snapshots
                    if str(item.collection_action_id or "") in action_ids
                ]
                action_collection_results = [
                    item
                    for action_id in action_ids
                    for item in collection_results_by_action.get(
                        action_id,
                        [],
                    )
                ]
                candidate_snapshots = [
                    item
                    for item in action_snapshots
                    if self._snapshot_candidate(
                        item,
                        check_item_version_id=str(
                            execution.check_item_version_id
                        ),
                    )
                ]
                bound_snapshots = [
                    item
                    for item in candidate_snapshots
                    if self._snapshot_bound(
                        item,
                        check_item_version_id=str(
                            execution.check_item_version_id
                        ),
                    )
                ]
                selected_snapshots = [
                    item
                    for item in bound_snapshots
                    if self._snapshot_selected(
                        item,
                        check_item_version_id=str(
                            execution.check_item_version_id
                        ),
                        result_mode=str(check_version.result_mode),
                    )
                ]
                qualification_snapshots = (
                    bound_snapshots
                    if bound_snapshots
                    else candidate_snapshots
                )
                selected_ids = {
                    str(item.id) for item in selected_snapshots
                }
                observation_rows = [
                    observation
                    for snapshot_id in selected_ids
                    for observation in observations_by_snapshot.get(
                        snapshot_id,
                        [],
                    )
                ]
                changes = (
                    db.query(NetworkChangeRecord)
                    .filter(
                        NetworkChangeRecord.run_id == run_id,
                        NetworkChangeRecord.check_execution_id
                        == execution.id,
                    )
                    .all()
                )
                change_ids = {str(item.id) for item in changes}
                events = (
                    db.query(NetworkEventRecord)
                    .filter(
                        NetworkEventRecord.run_id == run_id,
                        NetworkEventRecord.source_change_id.in_(
                            change_ids
                        ),
                    )
                    .all()
                    if change_ids
                    else []
                )
                event_occurrences = (
                    db.query(NetworkEventOccurrenceRecord)
                    .filter(
                        NetworkEventOccurrenceRecord.source_change_id.in_(
                            change_ids
                        )
                    )
                    .order_by(
                        NetworkEventOccurrenceRecord.observed_at,
                        NetworkEventOccurrenceRecord.id,
                    )
                    .all()
                    if change_ids
                    else []
                )
                previous_snapshot_id = self._previous_snapshot_id(
                    db,
                    execution=execution,
                    current=(
                        selected_snapshots[-1]
                        if selected_snapshots
                        else None
                    ),
                )
                comparison = comparison_by_id.get(
                    str(execution.comparison_context_id or "")
                )
                expected_entity_keys: list[tuple[str, str]] = []
                coverage_denominator_ref: dict[str, Any] = {}
                if comparison is not None:
                    candidate_snapshot_ids = {
                        str(item)
                        for item in (
                            comparison.baseline_snapshot_ids_json or []
                        )
                        if item
                    }
                    qualification = next(
                        (
                            item
                            for item in qualification_rows
                            if str(item.check_item_version_id)
                            == str(execution.check_item_version_id)
                            and candidate_snapshot_ids
                            and candidate_snapshot_ids.issubset(
                                {
                                    str(snapshot_id)
                                    for snapshot_id in (
                                        item.snapshot_ids_json or []
                                    )
                                    if snapshot_id
                                }
                            )
                        ),
                        None,
                    )
                    if qualification is not None:
                        canonical_contract = dict(
                            check_version.canonical_contract_json or {}
                        )
                        expected_observation_types = {
                            str(item.get("observation_type") or "")
                            for item in (
                                canonical_contract.get("observations") or []
                            )
                            if isinstance(item, dict)
                            and item.get("observation_type")
                        }
                        expected_entity_type = str(
                            canonical_contract.get("entity_type") or ""
                        )
                        expected_entity_keys = sorted(
                            {
                                (
                                    str(observation.entity_type),
                                    str(observation.entity_key),
                                )
                                for snapshot_id in candidate_snapshot_ids
                                for observation in (
                                    baseline_observations_by_snapshot.get(
                                        snapshot_id,
                                        [],
                                    )
                                )
                                if (
                                    (
                                        observation.observation_type
                                        in expected_observation_types
                                    )
                                    or (
                                        expected_entity_type
                                        and str(observation.entity_type)
                                        == expected_entity_type
                                    )
                                )
                            }
                        )
                        coverage_denominator_ref = {
                            "basis": "derived_entity_set",
                            "source": (
                                "previous_qualified_observation"
                            ),
                            "comparison_context_id": str(comparison.id),
                            "baseline_run_id": comparison.baseline_run_id,
                            "baseline_snapshot_ids": sorted(
                                candidate_snapshot_ids
                            ),
                            "evidence_qualification_id": str(
                                qualification.id
                            ),
                            "evidence_qualification_digest": str(
                                qualification.digest
                            ),
                        }
                contexts.append(
                    {
                        "execution": execution.to_dict(),
                        "result_mode": str(check_version.result_mode),
                        "snapshot_ids": [
                            str(item.id)
                            for item in qualification_snapshots
                        ],
                        "snapshot_facts": [
                            item.to_dict()
                            for item in qualification_snapshots
                        ],
                        "action_snapshots": [
                            item.to_dict() for item in action_snapshots
                        ],
                        "collection_results": [
                            item.to_dict()
                            for item in action_collection_results
                        ],
                        "selected_snapshots": [
                            item.to_dict()
                            for item in selected_snapshots
                        ],
                        "observations": [
                            self._observation_payload(item)
                            for item in observation_rows
                        ],
                        "change_ids": sorted(change_ids),
                        "event_ids": sorted(
                            str(item.id) for item in events
                        ),
                        "event_occurrence_ids": sorted(
                            str(item.id) for item in event_occurrences
                        ),
                        "expected_entity_keys": expected_entity_keys,
                        "coverage_denominator_ref": (
                            coverage_denominator_ref
                        ),
                        "previous_snapshot_id": previous_snapshot_id,
                    }
                )
            return contexts

    @classmethod
    def _snapshot_selected(
        cls,
        snapshot: CommandSnapshot,
        *,
        check_item_version_id: str,
        result_mode: str,
    ) -> bool:
        if not cls._snapshot_bound(
            snapshot,
            check_item_version_id=check_item_version_id,
        ):
            return False
        if result_mode == "evidence_only":
            # The raw output is the evidence; a command other check items parse
            # may carry template_missing without affecting it.
            return True
        allowed_statuses = (
            cls._PARSED_STATUSES
            if result_mode == "structured"
            else cls._TEXT_STATUSES
        )
        return str(snapshot.parse_status) in allowed_statuses

    @staticmethod
    def _snapshot_bound(
        snapshot: CommandSnapshot,
        *,
        check_item_version_id: str,
    ) -> bool:
        if not CheckItemRunFinalizer._snapshot_candidate(
            snapshot,
            check_item_version_id=check_item_version_id,
        ):
            return False
        metadata = dict(snapshot.metadata_json or {})
        all_bindings = [
            dict(item)
            for item in metadata.get("check_item_bindings", [])
            if isinstance(item, dict)
        ]
        selected = [
            dict(item)
            for item in metadata.get(
                "selected_check_item_bindings",
                [],
            )
            if isinstance(item, dict)
        ]
        if all_bindings:
            selected_versions = {
                str(item.get("check_item_version_id") or "")
                for item in selected
            }
            if check_item_version_id not in selected_versions:
                return False
        elif selected and check_item_version_id not in {
            str(item.get("check_item_version_id") or "")
            for item in selected
        }:
            return False
        return True

    @staticmethod
    def _snapshot_candidate(
        snapshot: CommandSnapshot,
        *,
        check_item_version_id: str,
    ) -> bool:
        metadata = dict(snapshot.metadata_json or {})
        all_bindings = [
            dict(item)
            for item in metadata.get("check_item_bindings", [])
            if isinstance(item, dict)
        ]
        if all_bindings:
            return check_item_version_id in {
                str(item.get("check_item_version_id") or "")
                for item in all_bindings
            }
        selected = [
            dict(item)
            for item in metadata.get(
                "selected_check_item_bindings",
                [],
            )
            if isinstance(item, dict)
        ]
        if selected:
            return check_item_version_id in {
                str(item.get("check_item_version_id") or "")
                for item in selected
            }
        return True

    @staticmethod
    def _previous_snapshot_id(
        db: Any,
        *,
        execution: CheckExecutionRecord,
        current: CommandSnapshot | None,
    ) -> str | None:
        if current is None or not execution.comparison_context_id:
            return None
        context = db.get(
            ComparisonContextRecord,
            execution.comparison_context_id,
        )
        if context is None:
            return None
        candidate_ids = [
            str(item)
            for item in context.baseline_snapshot_ids_json or []
        ]
        if not candidate_ids:
            return None
        candidates = (
            db.query(CommandSnapshot)
            .filter(
                CommandSnapshot.id.in_(candidate_ids),
                CommandSnapshot.canonical_command
                == current.canonical_command,
            )
            .order_by(
                CommandSnapshot.observed_at.desc(),
                CommandSnapshot.id.desc(),
            )
            .all()
        )
        return str(candidates[0].id) if candidates else None

    @staticmethod
    def _observation_payload(row: StateObservationRecord) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "device_id": str(row.device_id),
            "run_id": str(row.run_id),
            "snapshot_id": str(row.snapshot_id),
            "entity_type": str(row.entity_type),
            "entity_key": str(row.entity_key),
            "field": str(row.field_name),
            "observation_type": row.observation_type,
            "semantic_type": row.semantic_type,
            "unit": row.unit,
            "dimensions": dict(row.dimensions_json or {}),
            "value": row.value_json,
            "value_text": row.value_text,
            "value_number": row.value_number,
            "command": str(row.canonical_command),
            "observed_at": row.observed_at,
            "parser_template_id": row.parser_template_id,
            "parser_version": row.parser_version,
            "contract_version": row.contract_version,
        }

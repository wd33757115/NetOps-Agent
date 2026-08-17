#!/usr/bin/env python
"""Verify Parser semantic review and release approval through the real BFF/API.

The script creates one isolated parser-validation snapshot and generated Parser
candidate, uses two temporary authenticated users for review and release, and
removes only records carrying its random fixture scope. It never touches an
existing production candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
DJANGO_ROOT = ROOT / "web" / "django_backend"
for import_root in (ROOT, DJANGO_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_backend.settings")

import django  # noqa: E402

django.setup()

from bff.roles import assign_user_role  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402

from scripts.test.rule_asset_source_import_full_stack_e2e import (  # noqa: E402
    _login,
    _request_json,
)
from src.core.parser.repository import ParserTemplateRepository  # noqa: E402
from src.core.parser.textfsm_dialect import (  # noqa: E402
    DEFAULT_TEXTFSM_DIALECT,
    dialect_hash,
    installed_textfsm_version,
)
from src.core.parser_generation import (  # noqa: E402
    normalize_parser_spec_payload,
    parser_spec_digest,
)
from src.infrastructure.db.models import (  # noqa: E402
    AuditLogRecord,
    CommandSnapshot,
    ObservationContractRecord,
    ParserDeviceProfile,
    ParserIdentitySignature,
    ParserOutboxEvent,
    ParserTemplate,
    PatrolDataset,
    PatrolRun,
    RuleAssetCommandUnitRecord,
    RuleGenerationContextRecord,
    RuleGenerationJobRecord,
)
from src.infrastructure.db.postgres import get_db_session  # noqa: E402


def _digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _create_user(username: str, password: str) -> None:
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username=username,
        email=f"{username}@example.invalid",
        password=password,
    )
    assign_user_role(user, "operator")
    user.save()


def _delete_users(usernames: list[str]) -> None:
    get_user_model().objects.filter(username__in=usernames).delete()


def _create_fixture(suffix: str) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    vendor = "H3C"
    model = f"S9999-E2E-{suffix.upper()}"
    family = "Comware7"
    command = "display interface brief"
    dataset_id = f"e2e-semantic-review-dataset-{suffix}"
    run_id = f"e2e-semantic-review-run-{suffix}"
    snapshot_id = f"e2e-semantic-review-snapshot-{suffix}"
    raw_text = (
        "Brief information on interfaces in route mode:\n"
        "Interface            Link Protocol Main IP         Description\n"
        "GigabitEthernet1/0/1 UP   UP       10.0.0.1        uplink\n"
        "GigabitEthernet1/0/2 DOWN DOWN     --              spare\n"
    )
    raw_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    with get_db_session() as db:
        db.add(
            PatrolDataset(
                id=dataset_id,
                name=f"Parser semantic review E2E {suffix}",
                mode="parser_validation",
                source="e2e_fixture",
                status="active",
                metadata_json={"fixture": "parser_semantic_review_e2e"},
                imported_at=now,
            )
        )
        db.flush()
        db.add(
            PatrolRun(
                id=run_id,
                mode="parser_validation",
                source="e2e_fixture",
                dataset_id=dataset_id,
                status="completed",
                collection_status="completed",
                parser_readiness_status="ready",
                fact_readiness_status="ready",
                analysis_status="completed",
                baseline_eligible=False,
                run_purpose="parser_validation",
                logical_run_id=run_id,
                attempt_number=0,
                device_count=1,
                command_count=1,
                started_at=now,
                completed_at=now,
                metadata_json={"fixture": "parser_semantic_review_e2e"},
            )
        )
        db.flush()
        db.add(
            CommandSnapshot(
                id=snapshot_id,
                run_id=run_id,
                dataset_id=dataset_id,
                inventory_device_id=None,
                device_id=f"external-{suffix}",
                execution_item_id=f"execution-{suffix}",
                device_name=f"E2E-SW-{suffix}",
                management_ip=None,
                vendor=vendor,
                model=model,
                command=command,
                canonical_command=command,
                observed_at=now,
                structured_json=None,
                raw_text=raw_text,
                raw_text_hash=raw_hash,
                parse_status="template_missing",
                output_status="complete",
                snapshot_kind="full",
                metadata_json={"fixture": "parser_semantic_review_e2e"},
            )
        )

    source_spec = normalize_parser_spec_payload(
        {
            "entity": "interface",
            "primary_keys": ["interface"],
            "fields": {
                "interface": {
                    "required": True,
                    "type": "interface_name",
                    "role": "identity",
                },
                "oper_status": {
                    "required": True,
                    "type": "text",
                    "role": "state",
                    "monitor": True,
                },
            },
            "allow_empty": False,
            "empty_patterns": [],
        }
    )
    sample_manifest = [
        {
            "sample_id": snapshot_id,
            "role": "generation",
            "sha256": raw_hash,
            "expectation": None,
        }
    ]
    validation = {
        "entity_type": "interface",
        "primary_keys": ["interface"],
        "required_fields": ["interface", "oper_status"],
        "optional_fields": [],
        "validators": {
            "interface": {"type": "interface_name"},
            "oper_status": {"type": "text"},
        },
        "allow_empty": False,
        "empty_patterns": [],
        "contract_source": "candidate_result_human_review_pending",
        "human_direct_review_required": True,
        "simple_parser": {
            "pipeline_version": "simple_v2",
            "validation_mode": "generation_sample_only",
            "status": "candidate_ready",
            "model_calls": 0,
            "best_attempt_id": f"fixture-attempt-{suffix}",
            "hard_gates": {
                "compiled": True,
                "executed": True,
                "source_executed": True,
                "source_shape_compatible": True,
            },
        },
        "parser_spec": source_spec,
        "parser_spec_sha256": parser_spec_digest(source_spec),
        "sample_manifest": sample_manifest,
        "sample_manifest_sha256": _digest(sample_manifest),
        "textfsm_dialect": {
            "runtime_version": installed_textfsm_version(),
            "dialect_version": DEFAULT_TEXTFSM_DIALECT.dialect_version,
            "dialect_hash": dialect_hash(DEFAULT_TEXTFSM_DIALECT),
            "prompt_version": DEFAULT_TEXTFSM_DIALECT.prompt_version,
        },
    }
    template_text = (
        "Value interface (GigabitEthernet\\S+)\n"
        "Value oper_status (\\S+)\n"
        "\n"
        "Start\n"
        " ^${interface}\\s+${oper_status}\\s+\\S+.* -> Record\n"
    )
    template_sha = hashlib.sha256(template_text.encode("utf-8")).hexdigest()
    template_id = ParserTemplateRepository().register_template(
        vendor=vendor,
        model=model,
        family=family,
        profile=model,
        command=command,
        category="e2e_semantic_review",
        version=f"candidate-{suffix}",
        asset_path=f"postgresql://parser-template/{template_sha}",
        template_text=template_text,
        sha256=template_sha,
        status="pending_approval",
        validation=validation,
    )
    return {
        "vendor": vendor,
        "model": model,
        "family": family,
        "command": command,
        "dataset_id": dataset_id,
        "run_id": run_id,
        "snapshot_id": snapshot_id,
        "template_id": template_id,
    }


def _cleanup_fixture(scope: dict[str, str], usernames: list[str]) -> dict[str, int]:
    template_id = scope["template_id"]
    source_ref = f"parser_template:{template_id}"
    with get_db_session() as db:
        rule_job_ids = [
            str(row.id)
            for row in db.query(RuleGenerationJobRecord.id)
            .filter(RuleGenerationJobRecord.source_ref == source_ref)
            .all()
        ]
        if rule_job_ids:
            db.query(RuleGenerationContextRecord).filter(
                RuleGenerationContextRecord.job_id.in_(rule_job_ids)
            ).delete(synchronize_session=False)
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id.in_(rule_job_ids)
            ).delete(synchronize_session=False)
        db.query(RuleAssetCommandUnitRecord).filter(
            RuleAssetCommandUnitRecord.parser_template_id == template_id
        ).delete(synchronize_session=False)
        db.query(ParserOutboxEvent).filter(
            ParserOutboxEvent.aggregate_id == template_id
        ).delete(synchronize_session=False)
        db.query(AuditLogRecord).filter(
            (AuditLogRecord.resource_id == template_id)
            | (AuditLogRecord.username.in_(usernames))
        ).delete(synchronize_session=False)
        db.query(ObservationContractRecord).filter(
            ObservationContractRecord.source_template_id == template_id
        ).delete(synchronize_session=False)
        db.query(ParserIdentitySignature).filter(
            ParserIdentitySignature.vendor == scope["vendor"],
            ParserIdentitySignature.model == scope["model"],
        ).delete(synchronize_session=False)
        db.query(ParserDeviceProfile).filter(
            ParserDeviceProfile.vendor == scope["vendor"],
            ParserDeviceProfile.model == scope["model"],
        ).delete(synchronize_session=False)
        db.query(ParserTemplate).filter(ParserTemplate.id == template_id).delete(
            synchronize_session=False
        )
        db.query(CommandSnapshot).filter(
            CommandSnapshot.id == scope["snapshot_id"]
        ).delete(synchronize_session=False)
        db.query(PatrolRun).filter(PatrolRun.id == scope["run_id"]).delete(
            synchronize_session=False
        )
        db.query(PatrolDataset).filter(
            PatrolDataset.id == scope["dataset_id"]
        ).delete(synchronize_session=False)
    _delete_users(usernames)
    with get_db_session() as db:
        return {
            "templates": db.query(ParserTemplate)
            .filter(ParserTemplate.id == template_id)
            .count(),
            "contracts": db.query(ObservationContractRecord)
            .filter(ObservationContractRecord.source_template_id == template_id)
            .count(),
            "outbox": db.query(ParserOutboxEvent)
            .filter(ParserOutboxEvent.aggregate_id == template_id)
            .count(),
            "snapshots": db.query(CommandSnapshot)
            .filter(CommandSnapshot.id == scope["snapshot_id"])
            .count(),
            "rule_jobs": db.query(RuleGenerationJobRecord)
            .filter(RuleGenerationJobRecord.source_ref == source_ref)
            .count(),
        }


def _rejection(response: httpx.Response, expected_text: str) -> dict[str, Any]:
    if response.status_code not in {400, 409}:
        raise AssertionError(
            f"expected governed rejection, got {response.status_code}: {response.text}"
        )
    if expected_text.casefold() not in response.text.casefold():
        raise AssertionError(f"rejection did not explain {expected_text!r}: {response.text}")
    try:
        payload: Any = response.json()
    except ValueError:
        payload = {"raw": response.text}
    return {"status_code": response.status_code, "payload": payload}


def run(
    *,
    base_url: str,
    output_path: Path,
) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:12]
    reviewer = f"e2e-reviewer-{suffix}"
    releaser = f"e2e-releaser-{suffix}"
    password = f"E2e-{uuid.uuid4().hex}!aA1"
    usernames = [reviewer, releaser]
    scope: dict[str, str] = {}
    summary: dict[str, Any] = {
        "schema_version": "parser-semantic-review-full-stack-e2e/v1",
        "success": False,
        "flow_success": False,
        "cleanup_success": False,
        "base_url": base_url,
        "actors": {"semantic_reviewer": reviewer, "release_approver": releaser},
    }
    reviewer_client = httpx.Client(base_url=base_url.rstrip("/"), timeout=30)
    releaser_client = httpx.Client(base_url=base_url.rstrip("/"), timeout=30)
    try:
        _create_user(reviewer, password)
        _create_user(releaser, password)
        scope = _create_fixture(suffix)
        reviewer_client.headers["Authorization"] = (
            f"Bearer {_login(reviewer_client, username=reviewer, password=password)}"
        )
        releaser_client.headers["Authorization"] = (
            f"Bearer {_login(releaser_client, username=releaser, password=password)}"
        )
        template_id = scope["template_id"]
        before = _request_json(
            reviewer_client,
            "GET",
            f"/api/parser/templates/{template_id}/comparison/",
        )
        if dict(before.get("review_readiness") or {}).get("can_review") is not True:
            raise AssertionError(f"isolated candidate is not reviewable: {before}")
        if dict(before.get("activation_readiness") or {}).get("can_activate") is not False:
            raise AssertionError("candidate became activatable before semantic review")

        release_before_review = _rejection(
            releaser_client.post(
                f"/api/parser/templates/{template_id}/activate/",
                json={"reason": "E2E must reject release before semantic review"},
            ),
            "human semantic review",
        )
        review = _request_json(
            reviewer_client,
            "POST",
            f"/api/parser/templates/{template_id}/semantic-review/",
            json_body={
                "reason": (
                    "E2E reviewed every parsed row and the interface primary key"
                ),
                "result_confirmed": True,
                "primary_key_confirmed": True,
            },
        )
        if review.get("template_status") != "pending_approval":
            raise AssertionError("semantic review unexpectedly activated the candidate")
        semantic_review = dict(review.get("semantic_review") or {})
        if semantic_review.get("reviewed_by") != reviewer:
            raise AssertionError("semantic review did not use the authenticated actor")

        after_review = _request_json(
            reviewer_client,
            "GET",
            f"/api/parser/templates/{template_id}/comparison/",
        )
        if dict(after_review.get("activation_readiness") or {}).get(
            "can_activate"
        ) is not True:
            raise AssertionError(f"reviewed candidate is not releasable: {after_review}")
        same_actor_release = _rejection(
            reviewer_client.post(
                f"/api/parser/templates/{template_id}/activate/",
                json={"reason": "E2E same actor release must be rejected"},
            ),
            "different release approver",
        )
        activated = _request_json(
            releaser_client,
            "POST",
            f"/api/parser/templates/{template_id}/activate/",
            json_body={
                "reason": "E2E independent release after frozen semantic review"
            },
        )
        if activated.get("status") != "active":
            raise AssertionError(f"candidate was not activated: {activated}")

        resolved = ParserTemplateRepository().resolve_active(
            vendor=scope["vendor"],
            model=scope["model"],
            family=scope["family"],
            command=scope["command"],
        )
        if resolved is None or str(resolved.id) != template_id:
            raise AssertionError("runtime resolver did not read the independently approved Parser")
        with get_db_session() as db:
            persisted = db.get(ParserTemplate, template_id)
            audits = (
                db.query(AuditLogRecord)
                .filter(AuditLogRecord.resource_id == template_id)
                .order_by(AuditLogRecord.created_at, AuditLogRecord.id)
                .all()
            )
            outbox = (
                db.query(ParserOutboxEvent)
                .filter(ParserOutboxEvent.aggregate_id == template_id)
                .all()
            )
            contract = (
                db.query(ObservationContractRecord)
                .filter(
                    ObservationContractRecord.source_template_id == template_id,
                    ObservationContractRecord.status == "active",
                )
                .one_or_none()
            )
            actions = [str(item.action) for item in audits]
            if actions.count("parser_template_semantic_reviewed") != 1:
                raise AssertionError(f"semantic review audit is incomplete: {actions}")
            if actions.count("parser_template_activated") != 1:
                raise AssertionError(f"release activation audit is incomplete: {actions}")
            if not outbox:
                raise AssertionError("Parser activation did not create an outbox event")
            if contract is None:
                raise AssertionError("Parser activation did not activate its contract")
            summary.update(
                {
                    "scope": scope,
                    "before_review": {
                        "template_status": persisted.status if persisted else None,
                        "sample_count": before.get("sample_count"),
                        "parsed_record_count": sum(
                            len(
                                dict(item.get("proposed") or {}).get("records") or []
                            )
                            for item in (before.get("rows") or [])
                        ),
                        "review_readiness": before.get("review_readiness"),
                        "activation_readiness": before.get("activation_readiness"),
                    },
                    "release_before_review": release_before_review,
                    "semantic_review": semantic_review,
                    "after_review": {
                        "semantic_review": after_review.get("semantic_review"),
                        "activation_readiness": after_review.get(
                            "activation_readiness"
                        ),
                    },
                    "same_actor_release": same_actor_release,
                    "activation": {
                        "status": activated.get("status"),
                        "approved_by": releaser,
                        "resolver_template_id": str(resolved.id),
                        "audit_actions": actions,
                        "outbox_statuses": [str(item.status) for item in outbox],
                        "contract_status": contract.status,
                    },
                }
            )
        summary["flow_success"] = True
    except Exception as exc:
        summary["run_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        reviewer_client.close()
        releaser_client.close()
        try:
            if scope:
                cleanup = _cleanup_fixture(scope, usernames)
            else:
                _delete_users(usernames)
                cleanup = {}
            summary["cleanup"] = cleanup
            summary["cleanup_success"] = all(value == 0 for value in cleanup.values())
        except Exception as exc:
            summary["cleanup_error"] = f"{type(exc).__name__}: {exc}"
        summary["success"] = bool(
            summary["flow_success"] and summary["cleanup_success"]
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:3000")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / ".tmp"
        / "parser-semantic-review-full-stack-e2e"
        / "summary.json",
    )
    args = parser.parse_args()
    summary = run(base_url=args.base_url, output_path=args.output)
    print(
        json.dumps(
            {
                "success": summary["success"],
                "flow_success": summary["flow_success"],
                "cleanup_success": summary["cleanup_success"],
                "run_error": summary.get("run_error"),
                "cleanup_error": summary.get("cleanup_error"),
                "evidence": str(args.output.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

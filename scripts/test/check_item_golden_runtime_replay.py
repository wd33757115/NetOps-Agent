#!/usr/bin/env python3
"""Transactionally replay golden check items with real Q2/Q3 samples.

The script uses generated candidate assets to exercise RuntimeBundle assembly,
StateObservation normalization, ChangePolicy comparison and EventRule matching.
All database writes live inside one outer transaction and are always rolled
back. Output artifacts contain only aggregate facts and pseudonymous evidence
identifiers; raw CLI and real device identities are never written.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import sys
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import textfsm
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.check_items.catalog import CheckItemCatalogRepository  # noqa: E402
from src.core.check_items.runtime_bundle import (  # noqa: E402
    CheckItemRuntimeBundleService,
)
from src.core.observation.repository import (  # noqa: E402
    ObservationContractRepository,
)
from src.core.parser.parser_bundle import parse_parser_bundle  # noqa: E402
from src.core.parser.repository import ParserTemplateRepository  # noqa: E402
from src.core.parser.sanitization import sanitize_parser_sample  # noqa: E402
from src.core.parser.textfsm_input import prepare_textfsm_input  # noqa: E402
from src.core.patrol.command_splitter import (  # noqa: E402
    infer_device_from_filename,
    split_cli_capture,
)
from src.core.patrol.change_detector import (  # noqa: E402
    _detect_observation_changes_against_history_impl,
)
from src.core.patrol.event_builder import build_events_from_changes  # noqa: E402
from src.core.patrol.event_rules import (  # noqa: E402
    event_rule_definitions_from_snapshot,
    event_rules_from_snapshot,
)
from src.core.patrol.rule_replay import (  # noqa: E402
    replay_event_rule_definitions_against_changes,
)
from src.core.state.normalizer import observations_from_snapshot  # noqa: E402
from src.core.state.observation_applicability import (  # noqa: E402
    APPLICABILITY_CONTEXT_KEY,
)
from src.core.state.repository import StateRepository  # noqa: E402
from src.infrastructure.db.models import (  # noqa: E402
    ChangePolicyRecord,
    ChangePolicyVersionRecord,
    CheckItemImplementationLinkRecord,
    CheckItemRuntimeBundleVersionRecord,
    CollectionImplementationParserBindingRecord,
    CollectionImplementationRecord,
    CollectionImplementationVersionRecord,
    CommandSnapshot,
    EventRuleRecord,
    EventRuleVersionRecord,
    ObservationContractRecord,
    ParserTemplate,
)
from src.infrastructure.db.postgres import engine, get_db_session  # noqa: E402


SCENARIOS = (
    {
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
        "expected_event_types": {
            "CpuUtilizationHigh",
            "CpuUtilizationRecovered",
        },
    },
    {
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
        "expected_event_types": {"InterfaceDown", "InterfaceUp"},
    },
    {
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
        "expected_event_types": {
            "OpticalRxPowerCritical",
            "OpticalRxPowerRecovered",
            "OpticalTxPowerCritical",
            "OpticalTxPowerRecovered",
            "TransceiverAbsent",
            "TransceiverRestored",
        },
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


def _database_baseline() -> dict[str, int]:
    with get_db_session() as db:
        return {
            "active_parser_count": int(
                db.query(ParserTemplate)
                .filter(ParserTemplate.status == "active")
                .count()
            ),
            "active_contract_count": int(
                db.query(ObservationContractRecord)
                .filter(ObservationContractRecord.status == "active")
                .count()
            ),
            "active_policy_count": int(
                db.query(ChangePolicyVersionRecord)
                .filter(ChangePolicyVersionRecord.status == "active")
                .count()
            ),
            "active_event_rule_count": int(
                db.query(EventRuleVersionRecord)
                .filter(EventRuleVersionRecord.status == "active")
                .count()
            ),
            "active_runtime_bundle_count": int(
                db.query(CheckItemRuntimeBundleVersionRecord)
                .filter(CheckItemRuntimeBundleVersionRecord.status == "active")
                .count()
            ),
        }


def _parse(template_text: str, sample_text: str) -> Any:
    bundle = parse_parser_bundle(template_text)
    if bundle is not None:
        record_sets: dict[str, list[dict[str, Any]]] = {}
        for record_set in bundle.record_sets:
            parser = textfsm.TextFSM(io.StringIO(record_set.template_text))
            rows = parser.ParseText(
                prepare_textfsm_input(
                    record_set.template_text,
                    sample_text,
                )
            )
            record_sets[record_set.name] = [
                {
                    str(field): value
                    for field, value in zip(
                        parser.header,
                        row,
                        strict=True,
                    )
                }
                for row in rows
            ]
        return {
            "format": "netops.structured_record_sets/v1",
            "record_sets": record_sets,
        }
    parser = textfsm.TextFSM(io.StringIO(template_text))
    return [
        {
            str(field): value
            for field, value in zip(parser.header, row, strict=True)
        }
        for row in parser.ParseText(
            prepare_textfsm_input(template_text, sample_text)
        )
    ]


def _parsed_record_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict) and isinstance(
        value.get("record_sets"), dict
    ):
        return sum(
            len(records)
            for records in value["record_sets"].values()
            if isinstance(records, list)
        )
    return 0


def _period(dataset_id: str | None) -> str | None:
    value = str(dataset_id or "").casefold()
    if "previous" in value or "baseline" in value:
        return "baseline"
    if "current" in value:
        return "current"
    return None


def _pseudonymous_device(value: str) -> str:
    return "device-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _read_text_best_effort(path: Path) -> str:
    data = path.read_bytes()
    for encoding in (
        "utf-8-sig",
        "utf-8",
        "utf-16",
        "utf-16-le",
        "utf-16-be",
        "gb18030",
        "gbk",
    ):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _parse_regression_sample_args(values: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for value in values:
        period, separator, raw_path = str(value).partition("=")
        normalized_period = period.strip().casefold()
        if separator != "=" or normalized_period not in {"baseline", "current"}:
            raise ValueError(
                "regression samples must use baseline=PATH or current=PATH"
            )
        path = Path(raw_path.strip()).resolve()
        if not path.is_file():
            raise ValueError(f"regression sample file does not exist: {path}")
        entries.append({"period": normalized_period, "path": path})
    periods = {str(item["period"]) for item in entries}
    if entries and periods != {"baseline", "current"}:
        raise ValueError(
            "regression samples require at least one baseline and one current file"
        )
    return entries


def _expand_regression_samples(
    values: dict[str, list[str]] | None,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for period, patterns in sorted(dict(values or {}).items()):
        normalized_period = str(period).strip().casefold()
        if normalized_period not in {"baseline", "current"}:
            raise ValueError(f"unsupported regression period: {period}")
        matched: set[Path] = set()
        for pattern in patterns:
            matched.update(path.resolve() for path in ROOT.glob(str(pattern)))
        if not matched:
            raise AssertionError(
                f"no regression files matched {normalized_period}: {patterns}"
            )
        entries.extend(
            {"period": normalized_period, "path": path}
            for path in sorted(matched)
            if path.is_file()
        )
    periods = {str(item["period"]) for item in entries}
    if periods != {"baseline", "current"}:
        raise AssertionError(
            "default regression evidence requires baseline and current files"
        )
    return entries


def _resolve_template_id(scope: dict[str, Any]) -> str:
    expected = {
        "vendor": str(scope.get("vendor") or "").strip().casefold(),
        "profile": str(scope.get("profile") or "").strip().casefold(),
        "command": str(scope.get("command") or "").strip().casefold(),
    }
    if not all(expected.values()):
        raise AssertionError(f"Parser template scope is incomplete: {scope}")
    with get_db_session() as db:
        rows = [
            {
                "id": str(item.id),
                "vendor": str(item.vendor or ""),
                "profile": str(item.profile or ""),
                "command": str(item.canonical_command or ""),
                "status": str(item.status or ""),
            }
            for item in db.query(ParserTemplate).order_by(
                ParserTemplate.created_at.desc(),
                ParserTemplate.id.desc(),
            ).all()
        ]
        matches = [
            item
            for item in rows
            if item["vendor"].strip().casefold() == expected["vendor"]
            and item["profile"].strip().casefold() == expected["profile"]
            and item["command"].strip().casefold()
            == expected["command"]
        ]
    for status in ("pending_approval", "active"):
        eligible = [item for item in matches if item["status"] == status]
        if len(eligible) == 1:
            return eligible[0]["id"]
        if len(eligible) > 1:
            raise AssertionError(
                "Parser scope is ambiguous for status "
                f"{status}: {scope}; candidates={[item['id'] for item in eligible]}"
            )
    raise AssertionError(
        "no pending or active Parser matches stable scope: "
        + json.dumps(scope, ensure_ascii=False, sort_keys=True)
    )


def _load_regression_samples(
    source_template: dict[str, Any],
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    command = str(source_template["canonical_command"])
    detached: list[dict[str, Any]] = []
    period_ordinals = {"baseline": 0, "current": 0}
    period_origins = {
        "baseline": datetime(2000, 1, 1, tzinfo=timezone.utc),
        "current": datetime(2000, 1, 2, tzinfo=timezone.utc),
    }
    for entry in entries:
        path = Path(entry["path"])
        period = str(entry["period"])
        period_ordinals[period] += 1
        inferred_name, inferred_ip = infer_device_from_filename(path)
        blocks = split_cli_capture(
            _read_text_best_effort(path),
            device_name=inferred_name,
        )
        matches = [
            block for block in blocks if block.command_canonical == command
        ]
        if len(matches) != 1:
            raise AssertionError(
                f"expected one {command!r} block in regression sample, "
                f"got {len(matches)}: {path.name}"
            )
        block = matches[0]
        sanitized = sanitize_parser_sample(block.raw_output)
        if not sanitized.text.strip():
            raise AssertionError(
                f"regression command output is empty after sanitization: {path.name}"
            )
        source_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        identity_source = str(
            inferred_ip or inferred_name or block.device_name or path.stem
        )
        detached.append(
            {
                "sample_id": f"regression-{source_digest[:20]}-{period}",
                "dataset_id": f"lixian-{period}-regression",
                "period": period,
                "device_id": _pseudonymous_device(identity_source),
                "observed_at": (
                    period_origins[period]
                    + timedelta(seconds=period_ordinals[period])
                ).isoformat(),
                "observed_at_source": "frozen_period_order_non_authoritative",
                "vendor": str(source_template.get("vendor") or ""),
                "model": str(source_template.get("model") or ""),
                "sanitized_text": sanitized.text,
                "sample_origin": "external_regression_file",
                "source_evidence_id": f"sha256:{source_digest}",
                "sanitization_version": sanitized.version,
            }
        )
    return detached


def _load_source(
    template_id: str,
    *,
    regression_samples: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with get_db_session() as db:
        template = db.get(ParserTemplate, template_id)
        if template is None:
            raise AssertionError(f"Parser candidate is missing: {template_id}")
        detached_template = template.to_dict(include_content=True)
        detached_samples: list[dict[str, Any]] = []
    if regression_samples:
        detached_samples = _load_regression_samples(
            detached_template,
            regression_samples,
        )
    else:
        evidence = ParserTemplateRepository().get_candidate_sample_evidence(
            template_id,
            include_sanitized_payload=True,
        )
        errors = list(evidence.get("errors") or [])
        if errors:
            raise AssertionError(
                "candidate sample evidence is invalid: " + "; ".join(errors)
            )
        for item in evidence.get("items") or []:
            period = _period(item.get("dataset_id"))
            if period is None:
                continue
            source = dict(item.get("source_context") or {})
            sanitized_text = str(item.get("sanitized_text") or "")
            if not sanitized_text.strip():
                raise AssertionError(
                    f"usable sanitized sample is missing: {item.get('sample_id')}"
                )
            identity_source = str(
                source.get("management_ip")
                or source.get("device_name")
                or item.get("device_id")
            )
            observed_at = source.get("observed_at")
            detached_samples.append(
                {
                    "sample_id": str(item.get("sample_id") or ""),
                    "dataset_id": str(item.get("dataset_id") or ""),
                    "period": period,
                    "device_id": _pseudonymous_device(identity_source),
                    "observed_at": (
                        observed_at.isoformat()
                        if isinstance(observed_at, datetime)
                        else str(observed_at or "")
                    ),
                    "observed_at_source": "candidate_sample_evidence",
                    "vendor": str(source.get("vendor") or detached_template["vendor"]),
                    "model": str(source.get("model") or detached_template.get("model") or ""),
                    "sanitized_text": sanitized_text,
                    "sample_origin": "immutable_candidate_sample_evidence",
                    "source_evidence_id": (
                        f"parser-candidate-sample:{item.get('sample_id')}"
                    ),
                }
            )
    if not detached_samples:
        raise AssertionError(f"no Q2/Q3 samples found for {template_id}")
    periods = {item["period"] for item in detached_samples}
    if periods != {"baseline", "current"}:
        raise AssertionError(
            f"Q2/Q3 period pair is incomplete for {template_id}: {periods}"
        )
    return detached_template, detached_samples


def _load_assets(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("success") is not True:
        raise AssertionError(f"candidate chain is not successful: {path}")
    privacy = dict(payload.get("privacy") or {})
    if privacy.get("success") is not True or privacy.get("raw_cli_included") is not False:
        raise AssertionError(f"candidate privacy gate failed: {path}")
    assets = payload.get("candidate_assets")
    if not isinstance(assets, dict):
        raise AssertionError(f"complete candidate assets are missing: {path}")
    if not isinstance(assets.get("observation_contract"), dict):
        raise AssertionError(f"ObservationContract is missing: {path}")
    if not assets.get("change_policies") or not assets.get("event_rules"):
        raise AssertionError(f"Policy or EventRule candidate is missing: {path}")
    return payload


def _clone_parser(
    db: Any,
    source: dict[str, Any],
    *,
    token: str,
    now: datetime,
) -> ParserTemplate:
    values = {
        key: copy.deepcopy(value)
        for key, value in source.items()
        if key in ParserTemplate.__table__.columns.keys()
    }
    parser_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"netops/golden-runtime-parser/{source['id']}/{token}",
        )
    )
    validation = copy.deepcopy(values.get("validation_json") or {})
    compatibility = copy.deepcopy(validation.get("compatibility") or {})
    compatibility.update(
        {
            "scope": "model",
            "vendor": source.get("vendor"),
            "family": source.get("family"),
            "model": source.get("model"),
        }
    )
    validation["compatibility"] = compatibility
    token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    values.update(
        id=parser_id,
        profile=f"golden-{token_digest[:8]}-{source['profile']}"[:128],
        version=f"golden-{token_digest[:12]}",
        asset_path=f"transaction://golden-runtime/{parser_id}",
        status="active",
        validation_json=validation,
        source_job_id=None,
        created_at=now,
        updated_at=now,
        activated_at=now,
        deprecated_at=None,
    )
    parser = ParserTemplate(**values)
    db.add(parser)
    db.flush()
    return parser


def _find_implementation(
    catalog: Any,
    *,
    check_item_id: str,
    vendor: str,
    command: str,
) -> Any:
    matches = []
    for item in catalog.implementations:
        item_vendor = str((item.match or {}).get("vendor") or "")
        commands = {str(command_item.command) for command_item in item.commands}
        if (
            check_item_id in item.check_item_ids
            and item_vendor.casefold() == vendor.casefold()
            and command in commands
        ):
            matches.append(item)
    if len(matches) != 1:
        raise AssertionError(
            f"expected one catalog implementation for {check_item_id}/"
            f"{vendor}/{command}, got {len(matches)}"
        )
    return matches[0]


def _seed_runtime_bundle(
    *,
    scope: Any,
    catalog: Any,
    check_item_id: str,
    source_template: dict[str, Any],
    candidate_assets: dict[str, Any],
    token: str,
    activate_bundle: bool = True,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    check_item = catalog.check_item(check_item_id)
    if check_item is None:
        raise AssertionError(f"active CheckItem is missing: {check_item_id}")
    source_implementation = _find_implementation(
        catalog,
        check_item_id=check_item_id,
        vendor=str(source_template["vendor"]),
        command=str(source_template["canonical_command"]),
    )
    implementation_id = f"golden-runtime-{check_item_id}-{token}"[:128]
    implementation_version_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"netops/golden-runtime-implementation/{implementation_id}/v1",
        )
    )
    implementation_payload = {
        "id": implementation_id,
        "version": 1,
        "match": {
            "vendor": source_template["vendor"],
            "family": source_template.get("family"),
            "model": source_template.get("model"),
        },
        "commands": [
            {
                "command": source_template["canonical_command"],
                "priority": 100,
            }
        ],
        "collector": source_implementation.collector,
        "parser_requirement": "structured",
        "template_scope": "model",
        "output_quality_policy": dict(
            source_implementation.output_quality_policy
        ),
        "selection_priority": 1000,
        "status": "active",
    }
    implementation_digest = _digest(implementation_payload)

    with scope() as db:
        parser = _clone_parser(
            db,
            source_template,
            token=token,
            now=now,
        )
        db.add(
            CollectionImplementationRecord(
                id=implementation_id,
                name=f"Golden runtime {check_item_id}",
                description="Transaction-scoped real-sample acceptance unit.",
                status="active",
                current_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            CollectionImplementationVersionRecord(
                id=implementation_version_id,
                implementation_id=implementation_id,
                version=1,
                collector=str(source_implementation.collector),
                match_json=dict(implementation_payload["match"]),
                commands_json=list(implementation_payload["commands"]),
                parser_requirement="structured",
                template_scope="model",
                output_quality_policy_json=dict(
                    implementation_payload["output_quality_policy"]
                ),
                selection_priority=1000,
                status="active",
                digest=implementation_digest,
                created_by="golden-runtime-replay",
                created_at=now,
                activated_at=now,
            )
        )
        db.flush()
        db.add(
            CheckItemImplementationLinkRecord(
                id=str(uuid.uuid4()),
                check_item_version_id=check_item.version_id,
                implementation_version_id=implementation_version_id,
                priority=1000,
                required=True,
                created_at=now,
            )
        )
        db.add(
            CollectionImplementationParserBindingRecord(
                id=str(uuid.uuid4()),
                implementation_version_id=implementation_version_id,
                canonical_command=str(source_template["canonical_command"]),
                parser_template_id=str(parser.id),
                record_set="default",
                binding_role="primary",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        contract = ObservationContractRepository().upsert_contract_row(
            db,
            source_type="cli",
            schema_id=str(parser.id),
            schema_ref=f"transaction://parser-template/{parser.id}",
            category=str(
                candidate_assets["observation_contract"].get("category")
                or "structured"
            ),
            validation=copy.deepcopy(
                candidate_assets["observation_contract"]
            ),
            version=1,
            source_template_id=str(parser.id),
            source_job_id=None,
            approval={
                "kind": "transactional_acceptance",
                "production_activation": False,
            },
            status="active",
            compatibility={
                "scope": "transactional_acceptance",
                "production_activation": False,
            },
        )
        parser.contract_version = int(contract.version)
        parser.contract_sha256 = str(contract.contract_sha256)
        parser_id = str(parser.id)
        contract_id = str(contract.contract_id)

    policy_version_ids: list[str] = []
    policy_snapshots: list[dict[str, Any]] = []
    with scope() as db:
        for index, policy in enumerate(
            candidate_assets["change_policies"],
            start=1,
        ):
            observation_type = str(policy["observation_type"])
            (
                db.query(ChangePolicyVersionRecord)
                .filter(
                    ChangePolicyVersionRecord.observation_type
                    == observation_type,
                    ChangePolicyVersionRecord.status == "active",
                )
                .update(
                    {
                        "status": "deprecated",
                        "deprecated_at": now,
                    },
                    synchronize_session=False,
                )
            )
            policy_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"netops/golden-runtime-policy/{token}/{index}",
                )
            )
            version_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"netops/golden-runtime-policy-version/{token}/{index}",
                )
            )
            policy_digest = _digest(policy)
            db.add(
                ChangePolicyRecord(
                    id=policy_id,
                    name=f"golden:{token}:{index}",
                    observation_type=observation_type,
                    description="Transaction-scoped generated ChangePolicy.",
                    status="active",
                    current_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(
                ChangePolicyVersionRecord(
                    id=version_id,
                    policy_id=policy_id,
                    version=1,
                    observation_type=observation_type,
                    semantic_type=str(policy.get("semantic_type") or "state"),
                    compare_mode=str(policy["compare_mode"]),
                    policy_json=copy.deepcopy(policy),
                    validation_report_json={
                        "success": True,
                        "source": "golden-runtime-replay",
                    },
                    status="active",
                    source_job_id=None,
                    digest=policy_digest,
                    created_by="golden-runtime-replay",
                    created_at=now,
                    activated_at=now,
                )
            )
            policy_version_ids.append(version_id)
            policy_snapshots.append(
                {
                    "policy_id": policy_id,
                    "version_id": version_id,
                    "observation_type": observation_type,
                    "policy": copy.deepcopy(policy),
                }
            )

    event_rule_version_ids: list[str] = []
    event_rule_bindings: dict[str, list[str]] = {}
    event_rule_snapshots: list[dict[str, Any]] = []
    with scope() as db:
        for index, rule in enumerate(
            candidate_assets["event_rules"],
            start=1,
        ):
            rule_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"netops/golden-runtime-event-rule/{token}/{index}",
                )
            )
            version_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"netops/golden-runtime-event-rule-version/{token}/{index}",
                )
            )
            rule_payload = copy.deepcopy(rule)
            condition = dict(rule_payload["condition"])
            observation_types = [
                str(value)
                for value in condition.get("observation_types") or []
            ]
            if not observation_types:
                raise AssertionError(
                    f"EventRule has no observation binding: {rule.get('name')}"
                )
            digest = _digest(rule_payload)
            db.add(
                EventRuleRecord(
                    id=rule_id,
                    name=f"golden:{token}:{index}",
                    description=str(rule.get("description") or ""),
                    status="active",
                    current_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(
                EventRuleVersionRecord(
                    id=version_id,
                    rule_id=rule_id,
                    version=1,
                    event_type=str(rule["event_type"]),
                    severity=str(rule.get("severity") or "warning"),
                    condition_json=condition,
                    priority=int(rule.get("priority") or 100),
                    exclusive=bool(rule.get("exclusive")),
                    event_class=str(rule.get("event_class") or "fault"),
                    evaluation_scope=str(
                        rule.get("evaluation_scope") or "both"
                    ),
                    recovery_json=dict(rule.get("recovery") or {}),
                    correlation_key_json=list(
                        rule.get("correlation_keys") or []
                    ),
                    status="active",
                    source_job_id=None,
                    validation_report_json={
                        "success": True,
                        "source": "golden-runtime-replay",
                    },
                    digest=digest,
                    created_by="golden-runtime-replay",
                    created_at=now,
                    activated_at=now,
                )
            )
            event_rule_version_ids.append(version_id)
            event_rule_bindings[version_id] = observation_types
            event_rule_snapshots.append(
                {
                    **rule_payload,
                    "rule_id": rule_id,
                    "rule_name": str(rule_payload["name"]),
                    "version": 1,
                }
            )

    service = CheckItemRuntimeBundleService(scope)
    preview = service.preview_assembly(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation_version_id,
        parser_template_ids=[parser_id],
        observation_contract_ids=[contract_id],
        change_policy_version_ids=policy_version_ids,
        event_rule_version_ids=event_rule_version_ids,
    )
    if preview.get("operationally_ready") is not True:
        raise AssertionError(
            "RuntimeBundle preview is blocked: "
            + json.dumps(
                preview.get("blockers") or [],
                ensure_ascii=False,
                default=str,
            )
        )
    bundle = service.assemble(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation_version_id,
        preview_digest=str(preview["preview_digest"]),
        actor="golden-runtime-replay",
        parser_template_ids=[parser_id],
        observation_contract_ids=[contract_id],
        change_policy_version_ids=policy_version_ids,
        event_rule_version_ids=event_rule_version_ids,
    )
    if activate_bundle:
        bundle = service.activate(
            str(bundle["version_id"]),
            actor="golden-runtime-approver",
            reason="transactional Q2/Q3 real-sample acceptance",
        )
    return {
        "check_item": check_item,
        "parser_id": parser_id,
        "contract_id": contract_id,
        "contract": copy.deepcopy(
            candidate_assets["observation_contract"]
        ),
        "policy_snapshots": policy_snapshots,
        "event_rule_snapshots": event_rule_snapshots,
        "preview": preview,
        "bundle": bundle,
    }


def _observation_row(item: Any) -> dict[str, Any]:
    return StateRepository()._observation_values(item)


def _normalize_periods(
    *,
    source_template: dict[str, Any],
    samples: list[dict[str, Any]],
    contract: dict[str, Any],
    parser_id: str,
    contract_id: str,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {
        "baseline": [],
        "current": [],
    }
    contract_override = {
        **copy.deepcopy(contract),
        "schema_id": parser_id,
        "contract_id": contract_id,
        "contract_version": 1,
        "contract_sha256": _digest(contract),
    }
    for sample in samples:
        records = _parse(
            str(source_template["template_text"]),
            str(sample["sanitized_text"]),
        )
        if _parsed_record_count(records) == 0:
            raise AssertionError(
                f"Parser produced no records for sample {sample['sample_id']}"
            )
        period = str(sample["period"])
        snapshot = {
            "id": f"golden-{sample['sample_id']}",
            "run_id": f"golden-{period}",
            "device_id": sample["device_id"],
            "inventory_device_id": sample["device_id"],
            "vendor": sample["vendor"],
            "model": sample["model"],
            "canonical_command": source_template["canonical_command"],
            "structured_json": records,
            "observed_at": sample["observed_at"],
            "parser_template_id": parser_id,
            "parser_version": source_template["version"],
            "source_type": "cli",
            "execution_item_id": f"golden-execution-{sample['sample_id']}",
            "metadata_json": {
                "historical_backfill": True,
                "command_ordinal": 1,
            },
        }
        observations = observations_from_snapshot(
            snapshot,
            mode="parser_validation",
            source="offline_file",
            dataset_id=sample["dataset_id"],
            observation_contract_version=1,
            _contract_override=contract_override,
        )
        if not observations:
            raise AssertionError(
                f"Contract produced no observation for {sample['sample_id']}"
            )
        result[period].extend(_observation_row(item) for item in observations)
    return result


def _probe_change(
    rule: dict[str, Any],
    *,
    index: int,
) -> dict[str, Any]:
    condition = dict(rule.get("condition") or {})
    kind = str(condition.get("kind") or "")
    field = str((condition.get("fields") or ["value"])[0])
    entity_type = str(
        (condition.get("entity_types") or ["record"])[0]
    )
    observation_type = str(
        (condition.get("observation_types") or [field])[0]
    )
    dimensions = dict(condition.get("dimensions") or {})
    context = {
        "threshold.low": -15.0,
        "threshold.critical_low": -20.0,
        "threshold.high": 5.0,
        "threshold.critical_high": 10.0,
    }
    old: Any = "up"
    new: Any = "down"
    if kind == "threshold":
        threshold = float(condition.get("gte") or condition.get("gt") or 80)
        old, new = threshold - 1, threshold + 1
    elif kind == "threshold_recovery":
        threshold = float(condition.get("lt") or condition.get("lte") or 75)
        old, new = threshold + 1, threshold - 1
    elif kind in {"threshold_lte", "threshold_low"}:
        threshold = float(condition.get("lte") or condition.get("lt") or -15)
        old, new = threshold + 1, threshold - 1
    elif kind == "threshold_field_lte":
        reference = str(condition["reference_field"])
        threshold = context[reference]
        old, new = threshold + 1, threshold - 1
    elif kind == "threshold_field_gte":
        reference = str(condition["reference_field"])
        threshold = context[reference]
        old, new = threshold - 1, threshold + 1
    elif kind == "threshold_field_low_recovery":
        reference = str(condition["reference_field"])
        threshold = context[reference]
        hysteresis = float(condition.get("hysteresis") or 0)
        old, new = threshold - 1, threshold + hysteresis + 1
    elif kind == "threshold_field_high_recovery":
        reference = str(condition["reference_field"])
        threshold = context[reference]
        hysteresis = float(condition.get("hysteresis") or 0)
        old, new = threshold + 1, threshold - hysteresis - 1
    elif kind == "transition":
        old = str(condition.get("from_group") or "up")
        new = str(condition.get("to_group") or "down")
    elif kind == "field_change":
        old, new = "before", "after"
    else:
        raise AssertionError(
            f"unsupported golden probe condition kind: {kind}"
        )
    dimensions[APPLICABILITY_CONTEXT_KEY] = context
    return {
        "change_id": f"golden-probe-{index}",
        "run_id": "golden-probe",
        "device_id": "device-probe",
        "command": "golden-probe",
        "entity_type": entity_type,
        "entity_key": f'["probe-{index}"]',
        "field": field,
        "observation_type": observation_type,
        "old": old,
        "new": new,
        "change_type": "modified",
        "dimensions": dimensions,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "analysis_revision": "golden_probe",
    }


def _exercise_event_rules(
    snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    rules = event_rules_from_snapshot(snapshots)
    rules_by_name = {rule.rule_id: rule for rule in rules}
    verified: set[str] = set()
    details: list[dict[str, Any]] = []
    for index, snapshot in enumerate(snapshots, start=1):
        rule_name = str(
            snapshot.get("rule_name")
            or snapshot.get("name")
            or snapshot.get("rule_id")
            or ""
        )
        rule = rules_by_name.get(rule_name)
        if rule is None:
            raise AssertionError(
                f"compiled EventRule is missing from snapshot: {rule_name}"
            )
        probe = _probe_change(snapshot, index=index)
        events = build_events_from_changes([probe], rules=[rule])
        event_types = {event.event_type for event in events}
        target = str(snapshot["event_type"])
        if target not in event_types:
            raise AssertionError(
                f"EventRule probe did not emit {target}: {probe}"
            )
        verified.add(target)
        details.append(
            {
                "event_type": target,
                "condition_kind": str(
                    (snapshot.get("condition") or {}).get("kind") or ""
                ),
                "emitted": True,
            }
        )
    return {
        "verified_event_types": sorted(verified),
        "details": details,
    }


def _replay(
    *,
    source_template: dict[str, Any],
    samples: list[dict[str, Any]],
    runtime: dict[str, Any],
) -> dict[str, Any]:
    observations = _normalize_periods(
        source_template=source_template,
        samples=samples,
        contract=runtime["contract"],
        parser_id=runtime["parser_id"],
        contract_id=runtime["contract_id"],
    )
    bundle_digest = str(runtime["bundle"]["digest"])
    changes = _detect_observation_changes_against_history_impl(
        current_run_id="golden-current",
        previous_run_id="golden-baseline",
        persist=False,
        policy_bundle=runtime["policy_snapshots"],
        current_observations=observations["current"],
        prior_observations=observations["baseline"],
        analysis_revision="golden_real_q2_q3",
        rule_bundle_digest=bundle_digest,
    )
    initial = _detect_observation_changes_against_history_impl(
        current_run_id="golden-current-state",
        previous_run_id=None,
        persist=False,
        policy_bundle=runtime["policy_snapshots"],
        current_observations=observations["current"],
        prior_observations=[],
        analysis_revision="golden_real_q3_current_state",
        rule_bundle_digest=bundle_digest,
    )
    rules = event_rules_from_snapshot(runtime["event_rule_snapshots"])
    period_events = build_events_from_changes(changes["changes"], rules=rules)
    current_events = build_events_from_changes(initial["changes"], rules=rules)
    historical_replay = replay_event_rule_definitions_against_changes(
        event_rule_definitions_from_snapshot(
            runtime["event_rule_snapshots"]
        ),
        changes["changes"],
        include_active_collisions=True,
    )
    if historical_replay.get("success") is not True:
        raise AssertionError(
            "EventRule historical replay detected collisions: "
            + json.dumps(
                historical_replay.get("unexpected_matches") or [],
                ensure_ascii=False,
                default=str,
            )
        )
    return {
        "sample_count": len(samples),
        "device_count": len({item["device_id"] for item in samples}),
        "period_sample_counts": dict(
            Counter(str(item["period"]) for item in samples)
        ),
        "observation_counts": {
            key: len(value) for key, value in observations.items()
        },
        "observation_types": sorted(
            {
                str(item.get("observation_type"))
                for period_rows in observations.values()
                for item in period_rows
                if item.get("observation_type")
            }
        ),
        "compared_observation_count": int(
            changes["compared_observations"]
        ),
        "change_count": int(changes["change_count"]),
        "change_types": dict(
            Counter(
                str(item.get("change_type") or "unknown")
                for item in changes["changes"]
            )
        ),
        "changed_observation_types": dict(
            Counter(
                str(item.get("observation_type") or "unknown")
                for item in changes["changes"]
            )
        ),
        "period_event_count": len(period_events),
        "period_event_types": dict(
            Counter(event.event_type for event in period_events)
        ),
        "current_event_count": len(current_events),
        "current_event_types": dict(
            Counter(event.event_type for event in current_events)
        ),
        "historical_replay": historical_replay,
    }


def _render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# 金样板规则资产运行时验收报告",
        "",
        "## 验收边界",
        "",
        "- 数据来源：脱敏且摘要冻结的候选 Job 样本或显式 Q2/Q3 回归文件。",
        "- 验收对象：CPU 使用率、接口运行状态、接口光功率。",
        "- 资产状态：候选资产仅在测试事务中临时激活，验收结束后全部回滚。",
        "- 结论边界：报告只反映本次样本及纳入检查项，不能外推为全网健康。",
        "",
        "## 总体结论",
        "",
        (
            f"- {summary['scenario_count']} 个检查项均完成 RuntimeBundle 装配、"
            "Q2/Q3 结构化回放、变化识别和事件规则可达性验证。"
        ),
        (
            f"- 共处理 {summary['sample_count']} 份周期样本、"
            f"{summary['device_count']} 个去重设备身份、"
            f"{summary['observation_count']} 条规范观测。"
        ),
        (
            f"- 真实周期变化 {summary['change_count']} 条，"
            f"真实事件 {summary['period_event_count']} 条，"
            f"回放 current 周期状态事件 {summary['current_event_count']} 条。"
        ),
        (
            "- 数据库回滚核验："
            + ("通过。" if summary["database_unchanged"] else "失败。")
        ),
        (
            "- 历史事件估算：basis="
            + ",".join(
                summary["historical_replay"][
                    "estimated_daily_event_count_basis"
                ]
            )
            + ", reliable="
            + str(
                summary["historical_replay"][
                    "estimated_daily_event_count_reliable"
                ]
            ).lower()
            + ", sparse_history="
            + str(
                summary["historical_replay"]["sparse_history"]
            ).lower()
            + "。"
        ),
        "",
        "## 检查项结果",
        "",
        "| 检查项 | 样本/设备 | Q2/Q3 观测 | 变化 | 周期事件 | 当前事件 | 结论 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in summary["scenarios"]:
        replay = item["replay"]
        observation_count = sum(replay["observation_counts"].values())
        current_event_count = int(replay["current_event_count"])
        conclusion = (
            "回放 current 周期命中候选风险，需工程师复核"
            if current_event_count
            else "本检查项回放样本范围内未命中候选异常"
        )
        lines.append(
            "| {check_item} | {samples}/{devices} | {observations} | "
            "{changes} | {period_events} | {current_events} | {conclusion} |".format(
                check_item=item["check_item_id"],
                samples=replay["sample_count"],
                devices=replay["device_count"],
                observations=observation_count,
                changes=replay["change_count"],
                period_events=replay["period_event_count"],
                current_events=current_event_count,
                conclusion=conclusion,
            )
        )
    lines.extend(
        [
            "",
            "## 证据与限制",
            "",
            "- 每个检查项均记录候选 Parser ID、临时 RuntimeBundle digest、样本数量和规则可达性。",
            "- 报告不包含原始 CLI、真实设备名或管理 IP。",
            "- 没有观测或质量门禁失败时必须输出“无法判断”；本次所有纳入样本均形成规范观测。",
            "- 生产发布仍必须经过人工审批，本验收不会替代审批。",
            "",
        ]
    )
    return "\n".join(lines)


def run(
    *,
    asset_dir: Path,
    output_dir: Path,
    scenarios: tuple[dict[str, Any], ...] = SCENARIOS,
) -> dict[str, Any]:
    before = _database_baseline()
    token = uuid.uuid4().hex[:12]
    prepared: list[dict[str, Any]] = []
    for scenario in scenarios:
        template_id = str(scenario.get("template_id") or "").strip()
        if not template_id:
            template_id = _resolve_template_id(
                dict(scenario.get("template_scope") or {})
            )
        regression_samples = list(scenario.get("regression_samples") or [])
        if not regression_samples and scenario.get("regression_globs"):
            regression_samples = _expand_regression_samples(
                dict(scenario["regression_globs"])
            )
        source_template, samples = _load_source(
            template_id,
            regression_samples=regression_samples,
        )
        asset_path = scenario.get("asset_path")
        candidate = _load_assets(
            Path(asset_path)
            if asset_path is not None
            else asset_dir / str(scenario["asset_file"])
        )
        actual_event_types = {
            str(item.get("event_type"))
            for item in (
                candidate["candidate_assets"].get("event_rules") or []
            )
        }
        if actual_event_types != set(scenario["expected_event_types"]):
            raise AssertionError(
                f"event governance mismatch for {scenario['check_item_id']}: "
                f"{sorted(actual_event_types)}"
            )
        prepared.append(
            {
                **scenario,
                "template_id": template_id,
                "source_template": source_template,
                "samples": samples,
                "candidate": candidate,
            }
        )

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

    scenario_results: list[dict[str, Any]] = []
    try:
        catalog_repository = CheckItemCatalogRepository(scope)
        catalog_repository.bootstrap(actor="golden-runtime-replay")
        catalog = catalog_repository.load_active()
        for index, item in enumerate(prepared, start=1):
            scenario_token = f"{token}-{index}"
            runtime = _seed_runtime_bundle(
                scope=scope,
                catalog=catalog,
                check_item_id=str(item["check_item_id"]),
                source_template=item["source_template"],
                candidate_assets=item["candidate"]["candidate_assets"],
                token=scenario_token,
            )
            replay = _replay(
                source_template=item["source_template"],
                samples=item["samples"],
                runtime=runtime,
            )
            probes = _exercise_event_rules(
                runtime["event_rule_snapshots"]
            )
            if set(probes["verified_event_types"]) != set(
                item["expected_event_types"]
            ):
                raise AssertionError(
                    f"EventRule reachability is incomplete for "
                    f"{item['check_item_id']}"
                )
            scenario_results.append(
                {
                    "check_item_id": item["check_item_id"],
                    "source_parser_candidate_id": item["template_id"],
                    "sample_origins": sorted(
                        {
                            str(sample.get("sample_origin") or "unknown")
                            for sample in item["samples"]
                        }
                    ),
                    "sample_evidence_ids": sorted(
                        {
                            str(sample.get("source_evidence_id") or "")
                            for sample in item["samples"]
                            if sample.get("source_evidence_id")
                        }
                    ),
                    "observed_at_sources": sorted(
                        {
                            str(sample.get("observed_at_source") or "unknown")
                            for sample in item["samples"]
                        }
                    ),
                    "runtime_bundle_version_id": runtime["bundle"][
                        "version_id"
                    ],
                    "runtime_bundle_digest": runtime["bundle"]["digest"],
                    "runtime_preview_digest": runtime["preview"][
                        "preview_digest"
                    ],
                    "runtime_operationally_ready": runtime["preview"][
                        "operationally_ready"
                    ],
                    "fallback_resilience": runtime["preview"].get(
                        "fallback_resilience"
                    ),
                    "privacy": item["candidate"]["privacy"],
                    "replay": replay,
                    "event_rule_reachability": probes,
                }
            )
    finally:
        outer_transaction.rollback()
        connection.close()

    after = _database_baseline()
    database_unchanged = before == after
    if not database_unchanged:
        raise AssertionError(
            f"transactional replay changed production state: {before} != {after}"
        )
    device_ids = {
        sample["device_id"]
        for item in prepared
        for sample in item["samples"]
    }
    summary = {
        "success": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "transactional_real_sample_replay",
        "source": "sanitized immutable regression evidence",
        "sample_origins": sorted(
            {
                str(sample.get("sample_origin") or "unknown")
                for item in prepared
                for sample in item["samples"]
            }
        ),
        "observed_at_sources": sorted(
            {
                str(sample.get("observed_at_source") or "unknown")
                for item in prepared
                for sample in item["samples"]
            }
        ),
        "raw_cli_included": False,
        "production_assets_activated": False,
        "scenario_count": len(scenario_results),
        "sample_count": sum(
            item["replay"]["sample_count"] for item in scenario_results
        ),
        "device_count": len(device_ids),
        "observation_count": sum(
            sum(item["replay"]["observation_counts"].values())
            for item in scenario_results
        ),
        "change_count": sum(
            item["replay"]["change_count"] for item in scenario_results
        ),
        "period_event_count": sum(
            item["replay"]["period_event_count"]
            for item in scenario_results
        ),
        "current_event_count": sum(
            item["replay"]["current_event_count"]
            for item in scenario_results
        ),
        "historical_replay": {
            "matched_change_count": sum(
                item["replay"]["historical_replay"][
                    "matched_change_count"
                ]
                for item in scenario_results
            ),
            "collision_count": sum(
                item["replay"]["historical_replay"][
                    "collision_count"
                ]
                for item in scenario_results
            ),
            "estimated_daily_event_count": round(
                sum(
                    float(
                        item["replay"]["historical_replay"][
                            "estimated_daily_event_count"
                        ]
                    )
                    for item in scenario_results
                ),
                4,
            ),
            "estimated_daily_event_count_basis": sorted(
                {
                    str(
                        item["replay"]["historical_replay"].get(
                            "estimated_daily_event_count_basis"
                        )
                        or "unknown"
                    )
                    for item in scenario_results
                }
            ),
            "estimated_daily_event_count_reliable": all(
                item["replay"]["historical_replay"].get(
                    "estimated_daily_event_count_reliable"
                )
                is True
                for item in scenario_results
            ),
            "sparse_history": any(
                dict(
                    item["replay"]["historical_replay"].get(
                        "representativeness"
                    )
                    or {}
                ).get("sparse_history")
                is True
                for item in scenario_results
            ),
        },
        "database_before": before,
        "database_after": after,
        "database_unchanged": database_unchanged,
        "scenarios": scenario_results,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (output_dir / "acceptance-report.md").write_text(
        _render_markdown(summary),
        encoding="utf-8",
    )
    return summary


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--asset-dir",
        type=Path,
        default=ROOT / ".tmp" / "golden-rule-chain-full",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / ".tmp" / "check-item-golden-runtime-replay",
    )
    parser.add_argument("--template-id")
    parser.add_argument("--check-item-id")
    parser.add_argument("--candidate-file", type=Path)
    parser.add_argument(
        "--expected-event-type",
        action="append",
        default=[],
    )
    parser.add_argument(
        "--regression-sample",
        action="append",
        default=[],
        metavar="PERIOD=PATH",
        help=(
            "Use an immutable external regression capture. Repeat with "
            "baseline=PATH and current=PATH; multiple devices are allowed."
        ),
    )
    args = parser.parse_args()
    custom_values = (
        args.template_id,
        args.check_item_id,
        args.candidate_file,
        args.expected_event_type,
    )
    if any(custom_values) and not all(custom_values):
        parser.error(
            "custom replay requires --template-id, --check-item-id, "
            "--candidate-file and at least one --expected-event-type"
        )
    if args.regression_sample and not all(custom_values):
        parser.error("--regression-sample is only valid with a custom replay")
    asset_dir = args.asset_dir.resolve()
    scenarios = SCENARIOS
    if args.candidate_file:
        try:
            regression_samples = _parse_regression_sample_args(
                list(args.regression_sample)
            )
        except ValueError as exc:
            parser.error(str(exc))
        candidate_file = args.candidate_file.resolve()
        asset_dir = candidate_file.parent
        scenarios = (
            {
                "check_item_id": str(args.check_item_id),
                "template_id": str(args.template_id),
                "asset_file": candidate_file.name,
                "expected_event_types": set(
                    args.expected_event_type
                ),
                "regression_samples": regression_samples,
            },
        )
    result = run(
        asset_dir=asset_dir,
        output_dir=args.output_dir.resolve(),
        scenarios=scenarios,
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "success",
                    "scenario_count",
                    "sample_count",
                    "device_count",
                    "observation_count",
                    "change_count",
                    "period_event_count",
                    "current_event_count",
                    "database_unchanged",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

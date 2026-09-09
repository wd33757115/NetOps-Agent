"""Export reproducible human-review packs for pending Parser candidates.

The exporter is read-only. It recompiles each stored TextFSM template, parses
every sanitized Job sample, and writes the template, sample evidence, parsed
records, ParserSpec and immutable digests to a local review directory. It never
approves or activates an asset.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import textfsm
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.check_items.catalog import CheckItemCatalogRepository  # noqa: E402
from src.core.check_items.parser_capability import (  # noqa: E402
    build_parser_check_item_capability,
)
from src.core.parser.field_contract import build_parser_field_contract  # noqa: E402
from src.core.parser.identity import DeviceIdentity  # noqa: E402
from src.core.parser.parser_bundle import parse_parser_bundle  # noqa: E402
from src.core.parser.policy import PolicyDecision  # noqa: E402
from src.core.parser.repository import (  # noqa: E402
    generated_template_activation_errors,
)
from src.core.parser.runtime import ParserRuntime  # noqa: E402
from src.core.parser.textfsm_input import prepare_textfsm_input  # noqa: E402
from src.infrastructure.db.models import (  # noqa: E402
    CommandSnapshot,
    ParserCandidateSampleEvidence,
    ParserJobSample,
    ParserTemplate,
    ParserTemplateJob,
)
from src.infrastructure.db.postgres import engine  # noqa: E402

DEFAULT_CANDIDATES = (
    (
        "system.cpu_usage",
        "6a883f4d-04df-4595-81ba-637dbeabfccc",
    ),
    (
        "interface.optical_power",
        "bd1b9a28-e780-4057-84f2-28d47fbf5ddc",
    ),
)


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(value: Any) -> str:
    return _digest_bytes(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )


def _pseudonym(value: str) -> str:
    return "device-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _safe_fence(value: str) -> str:
    return str(value).replace("```", "` ` `")


StructuredParseResult = list[dict[str, Any]] | dict[str, Any]


def _parse_template(template_text: str, sample_text: str) -> StructuredParseResult:
    bundle = parse_parser_bundle(template_text)
    if bundle is not None:
        record_sets: dict[str, list[dict[str, Any]]] = {}
        for record_set in bundle.record_sets:
            parser = textfsm.TextFSM(io.StringIO(record_set.template_text))
            prepared = prepare_textfsm_input(record_set.template_text, sample_text)
            record_sets[record_set.name] = [
                {
                    str(field): value
                    for field, value in zip(parser.header, row, strict=True)
                }
                for row in parser.ParseText(prepared)
            ]
        return {
            "format": "netops.structured_record_sets/v1",
            "record_sets": record_sets,
        }
    parser = textfsm.TextFSM(io.StringIO(template_text))
    prepared = prepare_textfsm_input(template_text, sample_text)
    return [
        {str(field): value for field, value in zip(parser.header, row, strict=True)}
        for row in parser.ParseText(prepared)
    ]


def _record_set_records(
    result: StructuredParseResult,
) -> dict[str, list[dict[str, Any]]]:
    if isinstance(result, list):
        return {}
    raw_sets = result.get("record_sets")
    if not isinstance(raw_sets, dict):
        raise AssertionError("structured ParserBundle result requires record_sets")
    output: dict[str, list[dict[str, Any]]] = {}
    for name, records in raw_sets.items():
        if not isinstance(records, list) or any(
            not isinstance(record, dict) for record in records
        ):
            raise AssertionError(
                f"ParserBundle record set {name!s} must contain object records"
            )
        output[str(name)] = [dict(record) for record in records]
    return output


def _flatten_records(result: StructuredParseResult) -> list[dict[str, Any]]:
    if isinstance(result, list):
        return [dict(record) for record in result]
    return [
        {"__record_set__": name, **dict(record)}
        for name, records in _record_set_records(result).items()
        for record in records
    ]


def _record_set_counts(result: StructuredParseResult) -> dict[str, int]:
    return {
        name: len(records)
        for name, records in _record_set_records(result).items()
    }


class _CandidatePolicy:
    def __init__(self, command: str) -> None:
        self.command = command

    def decide(self, **_kwargs: Any) -> PolicyDecision:
        return PolicyDecision(
            enabled=True,
            canonical_command=self.command,
            handling="structured",
            asset_mode="full_chain",
        )


class _CandidateRegistry:
    def __init__(self, template: ParserTemplate) -> None:
        self.template = template

    def resolve_active(self, **_kwargs: Any) -> ParserTemplate:
        return self.template


def _parse_with_runtime(
    template: ParserTemplate,
    sample_text: str,
) -> tuple[StructuredParseResult, dict[str, Any]]:
    runtime = ParserRuntime(
        policy=_CandidatePolicy(str(template.canonical_command)),
        registry=_CandidateRegistry(template),
    )
    result = runtime.parse(
        identity=DeviceIdentity(
            vendor=str(template.vendor),
            model=str(template.model or template.profile),
            family=str(template.family or "") or None,
            confidence=1.0,
            evidence_text="frozen approval candidate",
            source="approval_pack",
        ),
        command=str(template.canonical_command),
        raw_text=sample_text,
    )
    if result.parser_status != "success" or not isinstance(
        result.structured_data, (list, dict)
    ):
        raise AssertionError(
            "ParserRuntime rejected candidate "
            f"{template.id}: {result.failure_code or result.parser_status}"
        )
    structured = result.structured_data
    if isinstance(structured, list):
        parsed: StructuredParseResult = [dict(item) for item in structured]
    else:
        parsed = {
            "format": str(structured.get("format") or ""),
            "record_sets": _record_set_records(structured),
        }
    flattened = _flatten_records(parsed)
    return parsed, {
        "parser_status": result.parser_status,
        "failure_code": result.failure_code,
        "template_id": result.template_id,
        "record_count": len(flattened),
        "record_set_counts": _record_set_counts(parsed),
        "parsed_digest": _digest(parsed),
    }


def _candidate_spec(raw: str) -> tuple[str, str]:
    if ":" not in raw:
        raise argparse.ArgumentTypeError("candidate must use CHECK_ITEM_ID:TEMPLATE_ID")
    check_item_id, template_id = raw.split(":", 1)
    if not check_item_id.strip() or not template_id.strip():
        raise argparse.ArgumentTypeError("candidate must use CHECK_ITEM_ID:TEMPLATE_ID")
    return check_item_id.strip(), template_id.strip()


def _current_job_sample_ids(db: Any, job_id: str) -> set[str]:
    return {
        str(sample_id)
        for (sample_id,) in db.query(ParserJobSample.id)
        .filter(
            ParserJobSample.job_id == job_id,
            ParserJobSample.lifecycle_status == "current",
        )
        .all()
    }


def _load_candidate(
    db: Any,
    *,
    check_item_id: str,
    template_id: str,
) -> dict[str, Any]:
    template = db.get(ParserTemplate, template_id)
    if template is None:
        raise AssertionError(f"Parser candidate is missing: {template_id}")
    if template.status != "pending_approval":
        raise AssertionError(
            f"Parser candidate must remain pending_approval: {template_id}={template.status}"
        )
    if not template.source_job_id:
        raise AssertionError(f"Parser candidate has no source Job: {template_id}")
    job = db.get(ParserTemplateJob, template.source_job_id)
    if job is None:
        raise AssertionError(f"Parser source Job is missing: {template.source_job_id}")
    template_text = str(template.template_text or "")
    if not template_text:
        raise AssertionError(f"Parser candidate has no immutable template body: {template_id}")
    computed_sha = _digest_bytes(template_text.encode("utf-8"))
    if computed_sha != template.sha256:
        raise AssertionError(f"Parser candidate SHA mismatch: {template_id}")
    activation_errors = generated_template_activation_errors(
        dict(template.validation_json or {}),
        source_job_id=str(template.source_job_id),
    )
    if activation_errors:
        raise AssertionError(
            "Parser candidate governance evidence is incomplete: " + "; ".join(activation_errors)
        )

    evidence_links = (
        db.query(ParserCandidateSampleEvidence)
        .filter(ParserCandidateSampleEvidence.template_id == template_id)
        .order_by(
            ParserCandidateSampleEvidence.created_at,
            ParserCandidateSampleEvidence.sample_id,
        )
        .all()
    )
    if not evidence_links:
        raise AssertionError(
            f"Parser candidate has no immutable sample evidence: {template_id}"
        )
    validation_manifest = {
        str(item.get("sample_id") or ""): item
        for item in (template.validation_json or {}).get("sample_manifest") or []
        if isinstance(item, dict) and item.get("sample_id")
    }
    linked_ids = {str(link.sample_id) for link in evidence_links}
    current_ids = _current_job_sample_ids(db, str(template.source_job_id))
    if linked_ids != set(validation_manifest) or linked_ids != current_ids:
        raise AssertionError(
            f"Parser candidate source sample set changed: {template_id}"
        )
    sample_rows: list[ParserJobSample] = []
    for link in evidence_links:
        if _digest(link.manifest_item_json) != str(link.manifest_item_sha256 or ""):
            raise AssertionError(
                f"Parser candidate evidence digest changed: {link.sample_id}"
            )
        if dict(link.manifest_item_json or {}) != dict(
            validation_manifest[str(link.sample_id)]
        ):
            raise AssertionError(
                f"Parser candidate evidence differs from its manifest: {link.sample_id}"
            )
        sample = db.get(ParserJobSample, link.sample_id)
        if sample is None or str(sample.job_id) != str(template.source_job_id):
            raise AssertionError(f"Parser candidate sample is missing: {link.sample_id}")
        sample_rows.append(sample)

    samples: list[dict[str, Any]] = []
    total_records = 0
    field_names: set[str] = set()
    aggregate_record_set_counts: dict[str, int] = {}
    for row in sample_rows:
        if row.sanitization_status != "sanitized" or not row.sanitized_text:
            raise AssertionError(f"Parser sample is not sanitized: {row.id}")
        snapshot = db.get(CommandSnapshot, row.snapshot_id)
        if snapshot is None:
            raise AssertionError(f"Parser sample Snapshot is missing: {row.id}")
        manifest_item = validation_manifest[str(row.id)]
        if str(row.sample_role or "") != str(manifest_item.get("role") or ""):
            raise AssertionError(f"Parser sample role changed: {row.id}")
        if str(row.sanitized_hash or "") != str(
            manifest_item.get("validation_sha256") or ""
        ):
            raise AssertionError(f"Parser sanitized sample changed: {row.id}")
        if str(snapshot.raw_text_hash or "") != str(manifest_item.get("sha256") or ""):
            raise AssertionError(f"Parser source Snapshot changed: {row.id}")
        parsed_result = _parse_template(template_text, str(row.sanitized_text))
        runtime_result, runtime_evidence = _parse_with_runtime(
            template,
            str(row.sanitized_text),
        )
        if runtime_result != parsed_result:
            raise AssertionError(
                f"ParserRuntime result differs from objective TextFSM replay: {row.id}"
            )
        records = _flatten_records(parsed_result)
        record_set_counts = _record_set_counts(parsed_result)
        total_records += len(records)
        for name, count in record_set_counts.items():
            aggregate_record_set_counts[name] = (
                aggregate_record_set_counts.get(name, 0) + count
            )
        for record in records:
            field_names.update(
                str(key) for key in record if str(key) != "__record_set__"
            )
        identity_source = str(
            snapshot.management_ip or snapshot.device_name or row.device_id or row.id
        )
        samples.append(
            {
                "sample_id": str(row.id),
                "snapshot_id": str(row.snapshot_id),
                "device_ref": _pseudonym(identity_source),
                "dataset_id": str(row.dataset_id or ""),
                "sample_role": str(row.sample_role or ""),
                "expectation": dict(row.expectation_json or {}),
                "sanitization": {
                    "status": row.sanitization_status,
                    "version": row.sanitization_version,
                    "sanitized_hash": row.sanitized_hash,
                    "redaction_summary": dict(row.redaction_summary_json or {}),
                },
                "observed_at": snapshot.observed_at,
                "sanitized_output": str(row.sanitized_text),
                "parsed_records": records,
                "parsed_result": parsed_result,
                "record_count": len(records),
                "record_set_counts": record_set_counts,
                "parsed_digest": _digest(parsed_result),
                "runtime_replay": runtime_evidence,
            }
        )

    parser_spec = dict(job.parser_spec_json or {})
    parser_field_contract = build_parser_field_contract(
        validation={"source_parser_spec": parser_spec},
        template_text=template_text,
    )
    if not parser_field_contract["aligned"]:
        raise AssertionError(
            "ParserSpec field inventory differs from executable TextFSM: "
            f"{template_id} ({parser_field_contract['status']})"
        )
    check_item_capability = build_parser_check_item_capability(
        check_item_id=check_item_id,
        parser_spec=parser_spec,
        vendor=str(template.vendor),
        model=str(template.model or "") or None,
        family=str(template.family or "") or None,
        profile=str(template.profile or "") or None,
        command=str(template.canonical_command),
        catalog=CheckItemCatalogRepository().load_active(),
    )
    check_item_capability["claim_boundary"]["parser_structure_proven"] = True
    check_item_capability["claim_boundary"]["parser_candidate_status"] = str(
        template.status
    )
    return {
        "schema_version": "parser-candidate-approval/v2",
        "generated_at": datetime.now(timezone.utc),
        "decision": {
            "status": "pending_human_approval",
            "automatic_activation": False,
            "required_review": [
                "TextFSM 模板是否忠实提取命令输出",
                "字段和主键是否满足巡检检查项需求",
                "每份样本的记录数量与实体是否合理",
                "兼容范围是否与样本证据一致",
            ],
        },
        "check_item_id": check_item_id,
        "parser_field_contract": parser_field_contract,
        "check_item_capability": check_item_capability,
        "template": {
            "id": str(template.id),
            "status": str(template.status),
            "vendor": str(template.vendor),
            "model": str(template.model or ""),
            "family": str(template.family or ""),
            "profile": str(template.profile),
            "canonical_command": str(template.canonical_command),
            "category": str(template.category),
            "version": str(template.version),
            "sha256": str(template.sha256),
            "computed_sha256": computed_sha,
            "template_text": template_text,
            "validation": dict(template.validation_json or {}),
        },
        "source_job": {
            "id": str(job.id),
            "status": str(job.status),
            "pipeline_version": str(job.pipeline_version),
            "parser_spec_status": str(job.parser_spec_status),
            "parser_spec_sha256": str(job.parser_spec_sha256 or ""),
            "parser_spec": parser_spec,
            "best_attempt_id": str(job.best_attempt_id or ""),
            "model_calls_used": int(job.model_calls_used or 0),
        },
        "objective_replay": {
            "compile_success": True,
            "sample_count": len(samples),
            "samples_with_records": sum(1 for sample in samples if sample["record_count"] > 0),
            "total_record_count": total_records,
            "field_names": sorted(field_names),
            "record_set_names": sorted(aggregate_record_set_counts),
            "record_set_counts": aggregate_record_set_counts,
            "runtime_parity_passed": True,
            "activation_governance_passed": True,
            "activation_errors": [],
            "sample_set_digest": _digest(
                [
                    {
                        "sample_id": item["sample_id"],
                        "sanitized_hash": item["sanitization"]["sanitized_hash"],
                        "parsed_digest": item["parsed_digest"],
                    }
                    for item in samples
                ]
            ),
        },
        "samples": samples,
    }


def _parsed_result_markdown(sample: dict[str, Any]) -> list[str]:
    parsed_result = sample.get("parsed_result", sample.get("parsed_records") or [])
    if isinstance(parsed_result, dict) and isinstance(
        parsed_result.get("record_sets"), dict
    ):
        lines = ["### 客观解析结果（按 record set）"]
        for name, records in parsed_result["record_sets"].items():
            lines.extend(
                [
                    "",
                    f"#### `{name}`（{len(records)} 条）",
                    "",
                    "```json",
                    _safe_fence(_json(records)),
                    "```",
                ]
            )
        return lines
    return [
        "### 客观解析结果",
        "",
        "```json",
        _safe_fence(_json(parsed_result)),
        "```",
    ]


def _candidate_markdown(payload: dict[str, Any]) -> str:
    template = payload["template"]
    job = payload["source_job"]
    replay = payload["objective_replay"]
    field_contract = payload["parser_field_contract"]
    capability = payload["check_item_capability"]
    shape = capability["shape_assessment"]
    mapping = capability["canonical_mapping"]
    lines = [
        f"# {payload['check_item_id']} / {template['canonical_command']}",
        "",
        f"> 本材料仅用于人工审批。导出过程只读，候选仍为 `{template['status']}`，不会自动激活。",
        "",
        "## 候选范围",
        "",
        "| 项目 | 值 |",
        "|---|---|",
        f"| Candidate ID | `{template['id']}` |",
        f"| Source Job | `{job['id']}` |",
        f"| 检查项 | `{payload['check_item_id']}` |",
        f"| 厂商 / 型号 | {template['vendor']} / {template['model'] or '-'} |",
        f"| 命令 | `{template['canonical_command']}` |",
        f"| Pipeline | `{job['pipeline_version']}` |",
        f"| 模型调用 | {job['model_calls_used']} |",
        f"| 样本 / 记录 | {replay['sample_count']} / {replay['total_record_count']} |",
        f"| 生产运行时一致 | {'是' if replay['runtime_parity_passed'] else '否'} |",
        f"| Template SHA-256 | `{template['sha256']}` |",
        f"| Sample Set SHA-256 | `{replay['sample_set_digest']}` |",
        "",
        "## ParserSpec / TextFSM 字段契约",
        "",
        f"- 状态：`{field_contract['status']}`",
        "- ParserSpec record sets：`"
        + ", ".join(field_contract["parser_spec_record_sets"])
        + "`",
        "- TextFSM record sets：`"
        + ", ".join(field_contract["template_record_sets"])
        + "`",
        "- 该证据只证明声明字段与可执行输出字段一致，不证明 canonical mapping 或生产就绪。",
        "",
        "## 能力声明边界",
        "",
        "> Parser 审批只证明该模板能够忠实结构化当前证据样本，不证明检查项、"
        "Observation Contract 或 RuntimeBundle 已完整可用。",
        "",
        f"- Parser 输出角色：`{', '.join(shape['observed_roles']) or '-'}`",
        f"- 检查项所需角色：`{', '.join(capability['target']['required_semantic_roles']) or '-'}`",
        f"- 缺失角色：`{', '.join(shape['missing_required_roles']) or '无'}`",
        f"- 标准观测映射：`{mapping['status']}`",
        "- 必需标准观测：`"
        + ", ".join(capability["target"]["required_observation_types"])
        + "`",
        "- 当前映射缺口：`"
        + (", ".join(mapping["missing_required_observation_types"]) or "待 Contract 证明")
        + "`",
        "",
        "### 能力阻断项",
        "",
        *(
            [
                f"- `{item['code']}`：{item['message']}"
                for item in capability.get("blockers") or []
            ]
            or ["- 无 Parser 形态阻断；仍需后续规则链与 RuntimeBundle 证明。"]
        ),
        "",
        "## 人工审批重点",
        "",
        "1. 模板是否忠实提取样本，不把标签、表头或占位符当成值。",
        "2. ParserSpec 的字段与主键是否符合该检查项的业务需求。",
        "3. 每份样本的解析记录数、实体键和字段值是否合理。",
        "4. 当前厂商、型号和输出结构是否足以支撑声明的兼容范围。",
        "",
        "## ParserSpec",
        "",
        "```json",
        _safe_fence(_json(job["parser_spec"])),
        "```",
        "",
        "## TextFSM 模板",
        "",
        "```textfsm",
        _safe_fence(template["template_text"]),
        "```",
    ]
    for index, sample in enumerate(payload["samples"], start=1):
        lines.extend(
            [
                "",
                f"## 样本 {index}: {sample['device_ref']}",
                "",
                f"- Sample ID: `{sample['sample_id']}`",
                f"- Dataset: `{sample['dataset_id'] or '-'}`",
                f"- Role: `{sample['sample_role'] or '-'}`",
                f"- Observed at: `{sample['observed_at']}`",
                f"- Parsed records: `{sample['record_count']}`",
                (
                    "- Record sets: `"
                    + ", ".join(
                        f"{name}={count}"
                        for name, count in sample.get("record_set_counts", {}).items()
                    )
                    + "`"
                    if sample.get("record_set_counts")
                    else "- Record sets: `single`"
                ),
                f"- Sanitized SHA-256: `{sample['sanitization']['sanitized_hash'] or '-'}`",
                "",
                "### 脱敏命令输出",
                "",
                "```text",
                _safe_fence(sample["sanitized_output"]),
                "```",
                "",
            ]
        )
        lines.extend(_parsed_result_markdown(sample))
    return "\n".join(lines).rstrip() + "\n"


def _target_matrix(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "rollout-target-draft/v1",
        "status": "blocked_by_human_approval",
        "purpose": "shadow_validation",
        "generated_at": datetime.now(timezone.utc),
        "invariants": {
            "production_activation_permitted": False,
            "candidate_runtime_bundle_required": True,
            "exact_inventory_device_ids_required": True,
            "fresh_snapshot_periods_required": 2,
        },
        "check_items": [
            {
                "check_item_id": item["check_item_id"],
                "parser_template_id": item["template"]["id"],
                "vendor": item["template"]["vendor"],
                "model": item["template"]["model"],
                "canonical_command": item["template"]["canonical_command"],
                "parser_status": item["template"]["status"],
                "parser_review_status": "ready_for_human_review",
                "check_item_capability_status": item["check_item_capability"][
                    "status"
                ],
                "check_item_capability": item["check_item_capability"],
                "check_item_runtime_ready": False,
                "runtime_bundle_version_id": None,
                "device_ids": [],
                "blocker_codes": [
                    "parser_human_approval_required",
                    *[
                        blocker["code"]
                        for blocker in item["check_item_capability"].get("blockers")
                        or []
                    ],
                    "runtime_bundle_not_assembled",
                    "inventory_scope_not_frozen",
                    "fresh_periods_not_verified",
                ],
                "blockers": [
                    "Parser candidate requires human approval",
                    *[
                        blocker["message"]
                        for blocker in item["check_item_capability"].get("blockers")
                        or []
                    ],
                    "draft RuntimeBundle has not been assembled",
                    "inventory target devices have not been frozen",
                    "two fresh production periods have not been verified",
                ],
            }
            for item in candidates
        ],
    }


def export(
    candidates: tuple[tuple[str, str], ...],
    *,
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as db:
        payloads = [
            _load_candidate(
                db,
                check_item_id=check_item_id,
                template_id=template_id,
            )
            for check_item_id, template_id in candidates
        ]

    files: list[Path] = []
    review_files: dict[str, Path] = {}
    for item in payloads:
        slug = (
            f"{item['check_item_id']}--"
            f"{item['template']['vendor']}--"
            f"{item['template']['model']}--"
            f"{item['template']['canonical_command']}"
        )
        slug = "".join(
            character if character.isalnum() or character in ".-_" else "-" for character in slug
        ).strip("-")
        json_path = output_dir / f"{slug}.json"
        markdown_path = output_dir / f"{slug}.md"
        json_path.write_text(_json(item) + "\n", encoding="utf-8")
        markdown_path.write_text(
            _candidate_markdown(item),
            encoding="utf-8",
        )
        files.extend((json_path, markdown_path))
        review_files[str(item["template"]["id"])] = markdown_path

    matrix_path = output_dir / "draft-target-matrix.json"
    matrix_path.write_text(
        _json(_target_matrix(payloads)) + "\n",
        encoding="utf-8",
    )
    files.append(matrix_path)

    manifest_path = output_dir / "manifest.json"
    sample_count = sum(item["objective_replay"]["sample_count"] for item in payloads)
    record_count = sum(item["objective_replay"]["total_record_count"] for item in payloads)
    runtime_parity_passed = all(
        item["objective_replay"]["runtime_parity_passed"] for item in payloads
    )
    activation_governance_passed = all(
        item["objective_replay"]["activation_governance_passed"] for item in payloads
    )
    field_contracts_aligned = all(
        item["parser_field_contract"]["aligned"] for item in payloads
    )
    index_lines = [
        "# 黄金 Parser 候选人工审批包",
        "",
        "> 所有候选保持 `pending_approval`。本目录只冻结人工审批证据，"
        "未执行激活、RuntimeBundle 发布或生产路由切换。",
        "",
        f"- 候选数量：{len(payloads)}",
        f"- 样本 / 记录：{sample_count} / {record_count}",
        f"- 生产运行时一致性：{'全部通过' if runtime_parity_passed else '存在阻断'}",
        f"- ParserSpec / TextFSM 字段契约：{'全部一致' if field_contracts_aligned else '存在阻断'}",
        "- 声明边界：Parser 可审批不等于检查项或 RuntimeBundle 生产就绪",
        f"- Manifest：`{manifest_path.name}`",
        f"- 目标矩阵草稿：`{matrix_path.name}`",
        "",
        "## 审批材料",
        "",
    ]
    for item in payloads:
        matching = review_files[str(item["template"]["id"])]
        index_lines.append(
            f"- [{item['check_item_id']} / "
            f"{item['template']['canonical_command']}]({matching.name})"
        )
    index_path = output_dir / "index.md"
    index_path.write_text(
        "\n".join(index_lines).rstrip() + "\n",
        encoding="utf-8",
    )
    files.append(index_path)

    manifest = {
        "schema_version": "parser-candidate-approval-pack/v2",
        "status": "ready_for_human_review",
        "parser_review_status": "ready_for_human_review",
        "check_item_runtime_ready": False,
        "generated_at": datetime.now(timezone.utc),
        "candidate_count": len(payloads),
        "sample_count": sample_count,
        "record_count": record_count,
        "candidate_ids": [item["template"]["id"] for item in payloads],
        "runtime_parity_passed": runtime_parity_passed,
        "parser_field_contracts_aligned": field_contracts_aligned,
        "activation_governance_passed": activation_governance_passed,
        "candidates": [
            {
                "check_item_id": item["check_item_id"],
                "template_id": item["template"]["id"],
                "source_job_id": item["source_job"]["id"],
                "vendor": item["template"]["vendor"],
                "model": item["template"]["model"],
                "canonical_command": item["template"]["canonical_command"],
                "status": item["template"]["status"],
                "sample_count": item["objective_replay"]["sample_count"],
                "record_count": item["objective_replay"]["total_record_count"],
                "record_set_names": item["objective_replay"].get(
                    "record_set_names", []
                ),
                "record_set_counts": item["objective_replay"].get(
                    "record_set_counts", {}
                ),
                "runtime_parity_passed": item["objective_replay"]["runtime_parity_passed"],
                "activation_governance_passed": item["objective_replay"][
                    "activation_governance_passed"
                ],
                "parser_field_contract_status": item["parser_field_contract"][
                    "status"
                ],
                "check_item_capability_status": item["check_item_capability"][
                    "status"
                ],
                "shape_assessment": item["check_item_capability"][
                    "shape_assessment"
                ],
                "canonical_mapping": item["check_item_capability"][
                    "canonical_mapping"
                ],
                "capability_blocker_codes": [
                    blocker["code"]
                    for blocker in item["check_item_capability"].get("blockers")
                    or []
                ],
                "template_sha256": item["template"]["sha256"],
                "sample_set_sha256": item["objective_replay"]["sample_set_digest"],
            }
            for item in payloads
        ],
        "production_activation_performed": False,
        "files": [
            {
                "path": path.name,
                "sha256": _digest_bytes(path.read_bytes()),
                "size_bytes": path.stat().st_size,
            }
            for path in sorted(files)
        ],
    }
    manifest_path.write_text(_json(manifest) + "\n", encoding="utf-8")
    return {
        **manifest,
        "output_dir": str(output_dir.resolve()),
        "index_path": str(index_path.resolve()),
        "manifest_path": str(manifest_path.resolve()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--candidate",
        action="append",
        type=_candidate_spec,
        help="CHECK_ITEM_ID:TEMPLATE_ID; repeat for multiple candidates",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / ".tmp" / "parser-candidate-approval-pack-20260729",
    )
    args = parser.parse_args()
    candidate_specs = tuple(args.candidate or DEFAULT_CANDIDATES)
    result = export(candidate_specs, output_dir=args.output_dir)
    print(_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

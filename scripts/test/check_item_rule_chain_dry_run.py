"""Dry-run one Parser candidate through the governed rule-asset chain.

The script executes TextFSM locally against sanitized Parser job samples, sends
only sanitized structured records to the LLM generators, and never persists or
activates an ObservationContract, ChangePolicy, EventRule, or RuntimeBundle.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any

import textfsm

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.check_items import load_canonical_catalog, load_catalog  # noqa: E402
from src.core.parser.parser_bundle import parse_parser_bundle  # noqa: E402
from src.core.parser.textfsm_input import prepare_textfsm_input  # noqa: E402
from src.core.rule_control.dataset_context import (  # noqa: E402
    sanitize_structured_records,
)
from src.core.rule_control.planning import compile_rule_targets  # noqa: E402
from src.core.rule_control.template_activation import (  # noqa: E402
    build_contract_seed,
)
from src.infrastructure.db.models import (  # noqa: E402
    ParserJobSample,
    ParserTemplate,
)
from src.infrastructure.db.postgres import get_db_session  # noqa: E402


def _load_skill(skill_name: str, module_name: str) -> Any:
    script = (
        PROJECT_ROOT
        / "src"
        / "skills"
        / skill_name
        / "scripts"
        / "run.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Skill module: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OBSERVATION_SKILL = _load_skill(
    "observation-contract-generator",
    "dry_run_observation_contract_generator",
)
POLICY_SKILL = _load_skill(
    "change-policy-generator",
    "dry_run_change_policy_generator",
)
EVENT_SKILL = _load_skill(
    "event-rule-generator",
    "dry_run_event_rule_generator",
)


def _parse_sample(template_text: str, sample_text: str) -> list[dict[str, Any]]:
    bundle = parse_parser_bundle(template_text)
    if bundle is not None:
        records: list[dict[str, Any]] = []
        for record_set in bundle.record_sets:
            parser = textfsm.TextFSM(io.StringIO(record_set.template_text))
            rows = parser.ParseText(
                prepare_textfsm_input(record_set.template_text, sample_text)
            )
            records.extend(
                {
                    "__record_set__": record_set.name,
                    **{
                        str(field): value
                        for field, value in zip(parser.header, row, strict=True)
                    },
                }
                for row in rows
            )
        return records
    parser = textfsm.TextFSM(io.StringIO(template_text))
    rows = parser.ParseText(prepare_textfsm_input(template_text, sample_text))
    return [
        {str(field): value for field, value in zip(parser.header, row, strict=True)}
        for row in rows
    ]


def _parser_contract_seed(template: ParserTemplate) -> dict[str, Any]:
    return build_contract_seed(template, dict(template.validation_json or {}))


def _governance(check_item_id: str) -> dict[str, Any]:
    check_item = load_catalog().check_item(check_item_id)
    if check_item is None:
        raise ValueError(f"unknown CheckItem: {check_item_id}")
    canonical = load_canonical_catalog()
    contract = dict(check_item.canonical_contract)
    targets: list[dict[str, Any]] = []
    for raw in contract.get("observations") or []:
        target = dict(raw)
        definition = canonical.get(str(target.get("observation_type") or ""))
        if definition is not None:
            target.update(
                canonical_description=definition.description,
                allowed_values=list(definition.allowed_values),
                value_types=list(definition.value_types),
                assessment_roles=list(definition.assessment_roles),
            )
        targets.append(target)
    return {
        "check_item": check_item,
        "observation_targets": targets,
        "canonical_identity_constraint": {
            "canonical_entity_type": str(contract.get("entity_type") or ""),
            "identity_dimensions": list(contract.get("identity_dimensions") or []),
            "identity_dimension_rules": dict(
                contract.get("identity_dimension_rules") or {}
            ),
        },
    }


def _load_records(
    *,
    template_id: str,
) -> tuple[ParserTemplate, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    with get_db_session() as db:
        template = db.get(ParserTemplate, template_id)
        if template is None:
            raise ValueError(f"Parser candidate does not exist: {template_id}")
        samples = (
            db.query(ParserJobSample)
            .filter(ParserJobSample.job_id == template.source_job_id)
            .order_by(ParserJobSample.created_at, ParserJobSample.id)
            .all()
        )
        if not samples:
            raise ValueError("Parser candidate has no job samples")
        template_text = str(template.template_text or "")
        if not template_text:
            raise ValueError("Parser candidate has no template text")
        parsed_groups: list[dict[str, Any]] = []
        for sample in samples:
            if not sample.sanitized_text:
                raise ValueError(
                    f"Parser sample is not sanitized: {sample.id}"
                )
            records = _parse_sample(template_text, str(sample.sanitized_text))
            if not records:
                raise ValueError(
                    f"Parser candidate produced no record for sample {sample.id}"
                )
            parsed_groups.append(
                {
                    "group_id": str(sample.id),
                    "sample_role": str(sample.sample_role),
                    "records": records,
                }
            )
        detached = template.to_dict(include_content=True)

    flat_records = [
        record for group in parsed_groups for record in group["records"]
    ]
    approved_fields = {
        str(field)
        for key in ("primary_keys", "required_fields", "optional_fields")
        for field in (detached.get("validation_json") or {}).get(key, [])
    }
    for record_set in (detached.get("validation_json") or {}).get(
        "record_sets",
        [],
    ):
        if not isinstance(record_set, dict):
            continue
        approved_fields.update(
            str(field)
            for key in ("primary_keys", "required_fields", "optional_fields")
            for field in record_set.get(key) or []
            if str(field).strip()
        )
    if (detached.get("validation_json") or {}).get("record_sets"):
        approved_fields.add("__record_set__")
    sanitized = sanitize_structured_records(
        flat_records,
        approved_fields=approved_fields,
    )
    sanitized_records = list(sanitized["records"])
    offset = 0
    sanitized_groups: list[dict[str, Any]] = []
    for group in parsed_groups:
        count = len(group["records"])
        sanitized_groups.append(
            {
                "group_id": group["group_id"],
                "sample_role": group["sample_role"],
                "records": sanitized_records[offset : offset + count],
            }
        )
        offset += count
    template_stub = ParserTemplate(**{
        key: value
        for key, value in detached.items()
        if key in ParserTemplate.__table__.columns.keys()
    })
    return template_stub, sanitized_records, sanitized_groups, sanitized["privacy"]


def _policy_summary(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        key: policy.get(key)
        for key in (
            "observation_type",
            "dimension_selector",
            "compare_mode",
            "thresholds",
            "threshold_references",
            "recovery_threshold",
            "deadband",
            "minimum_duration",
        )
    }


def run(
    *,
    template_id: str,
    check_item_id: str,
    max_attempts: int,
    include_assets: bool = False,
) -> dict[str, Any]:
    template, records, groups, privacy = _load_records(template_id=template_id)
    governance = _governance(check_item_id)
    contract_result = OBSERVATION_SKILL.run_contract_generation(
        {
            "mode": "infer_and_validate",
            "command": template.canonical_command,
            "vendor": "sanitized",
            "model": "sanitized",
            "records": records,
            "record_groups": groups,
            "proposal": _parser_contract_seed(template),
            "observation_targets": governance["observation_targets"],
            "strict_observation_targets": True,
            "canonical_identity_constraint": governance[
                "canonical_identity_constraint"
            ],
            "generation_provider": "llm",
            "max_agent_attempts": max_attempts,
        }
    )
    if contract_result.get("success") is not True:
        return {
            "success": False,
            "stage": "observation_contract",
            "privacy": privacy,
            "attempts": (contract_result.get("agent_loop") or {}).get(
                "attempt_count"
            ),
            "errors": (contract_result.get("validation") or {}).get("errors")
            or (
                (contract_result.get("agent_loop") or {}).get(
                    "revision_request"
                )
                or {}
            ).get("reason")
            or [],
        }

    contract = dict(contract_result["proposal"])
    plan = compile_rule_targets(
        contract=contract,
        records=records,
        record_groups=groups,
    )
    if plan.get("success") is not True:
        return {
            "success": False,
            "stage": "rule_target_planning",
            "privacy": privacy,
            "blockers": plan.get("blockers") or [],
        }

    policy_result = POLICY_SKILL.run_change_policy_generation(
        {
            "policy_targets": plan["policy_targets"],
            "historical_values_by_field": plan[
                "historical_values_by_field"
            ],
            "records": records,
            "generation_provider": "llm",
            "max_agent_attempts": max_attempts,
        }
    )
    if policy_result.get("success") is not True:
        errors = [
            error
            for result in policy_result.get("results") or []
            for error in (result.get("validation") or {}).get("errors") or []
        ]
        return {
            "success": False,
            "stage": "change_policy",
            "privacy": privacy,
            "errors": errors,
            "policies": [
                _policy_summary(policy)
                for policy in policy_result.get("target_policies") or []
            ],
        }

    constraints_by_type = {
        str(target["observation_type"]): dict(
            target.get("event_constraints") or {}
        )
        for target in plan["event_policy_targets"]
        if target.get("event_constraints")
    }
    event_result = EVENT_SKILL.run_event_rule_generation(
        {
            "contract": contract,
            "change_policies": policy_result["target_policies"],
            "event_policy_targets": plan["event_policy_targets"],
            "event_observation_types": plan["event_observation_types"],
            "event_constraints_by_observation_type": constraints_by_type,
            "generation_provider": "llm",
            "max_agent_attempts": max_attempts,
        }
    )
    event_errors = list((event_result.get("validation") or {}).get("errors") or [])
    result = {
        "success": event_result.get("success") is True,
        "stage": "completed" if event_result.get("success") else "event_rule",
        "template": {
            "id": template.id,
            "status": template.status,
            "vendor": template.vendor,
            "profile": template.profile,
            "command": template.canonical_command,
        },
        "check_item_id": check_item_id,
        "privacy": privacy,
        "sample_group_count": len(groups),
        "structured_record_count": len(records),
        "contract": {
            "attempts": (contract_result.get("agent_loop") or {}).get(
                "attempt_count"
            ),
            "entity_type": contract.get("entity_type"),
            "canonical_entity_type": contract.get("canonical_entity_type"),
            "primary_keys": contract.get("primary_keys"),
            "entity_identity_fields": contract.get(
                "entity_identity_fields"
            ),
        },
        "targets": [
            {
                "field_name": target["field_name"],
                "observation_type": target["observation_type"],
                "dimensions": target.get("dimension_selector") or {},
                "event_generation": target.get("event_generation"),
                "event_generation_reason": target.get(
                    "event_generation_reason"
                ),
            }
            for target in plan["policy_targets"]
        ],
        "policies": [
            _policy_summary(policy)
            for policy in policy_result.get("policies") or []
        ],
        "event_errors": event_errors,
        "events": [
            {
                "event_type": rule.get("event_type"),
                "event_class": rule.get("event_class"),
                "severity": rule.get("severity"),
                "evaluation_scope": rule.get("evaluation_scope"),
                "condition": rule.get("condition"),
                "resolves": (rule.get("recovery") or {}).get("resolves"),
            }
            for rule in event_result.get("rules") or []
        ],
    }
    if include_assets:
        result["candidate_assets"] = {
            "observation_contract": contract,
            "change_policies": [
                dict(policy)
                for policy in policy_result.get("policies") or []
                if isinstance(policy, dict)
            ],
            "target_change_policies": [
                dict(policy)
                for policy in policy_result.get("target_policies") or []
                if isinstance(policy, dict)
            ],
            "event_rules": [
                dict(rule)
                for rule in event_result.get("rules") or []
                if isinstance(rule, dict)
            ],
        }
    return result


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--template-id", required=True)
    parser.add_argument("--check-item-id", required=True)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--output")
    parser.add_argument(
        "--include-assets",
        action="store_true",
        help=(
            "Include complete sanitized Contract, Policy and EventRule "
            "candidates for transactional runtime replay."
        ),
    )
    args = parser.parse_args()
    result = run(
        template_id=args.template_id,
        check_item_id=args.check_item_id,
        max_attempts=max(1, args.max_attempts),
        include_assets=args.include_assets,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())

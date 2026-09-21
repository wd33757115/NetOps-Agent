"""Create safe rule-asset generation seeds from activated parser templates."""

from __future__ import annotations

import re
from typing import Any

from src.apps.rule_governance.backend.parser.repository import ParserTemplateRepository
from src.apps.rule_governance.backend.rule_control.repository import RuleGenerationJobRepository


class RuleAssetTemplateActivationService:
    """Prepare follow-up rule asset generation without exposing private samples.

    The service intentionally does not execute LLM-backed generators. It creates a
    draft RuleGenerationJob carrying parser template metadata and a list of
    missing workflow context fields. Operators, chat workflows, or UI flows can
    later provide sanitized records and historical summaries to run generation.
    """

    def __init__(
        self,
        *,
        parser_repository: Any | None = None,
        job_repository: RuleGenerationJobRepository | None = None,
    ) -> None:
        self.parser_repository = parser_repository or ParserTemplateRepository()
        self.job_repository = job_repository or RuleGenerationJobRepository()

    def seed_from_template(self, template_id: str) -> dict[str, Any]:
        template = self.parser_repository.get_template(template_id)
        if template is None:
            return {
                "success": False,
                "created": False,
                "reason": "parser_template_not_found",
                "template_id": template_id,
            }
        if getattr(template, "status", None) != "active":
            return {
                "success": False,
                "created": False,
                "reason": "parser_template_not_active",
                "template_id": template_id,
                "status": getattr(template, "status", None),
            }

        source_ref = f"parser_template:{template_id}"
        existing = self.job_repository.find_job(
            job_type="OBSERVATION_CONTRACT",
            source_ref=source_ref,
            status_not_in={"deprecated"},
        )
        if existing is not None:
            return {
                "success": True,
                "created": False,
                "job": existing,
                "template_id": template_id,
                "source_ref": source_ref,
            }

        validation = dict(getattr(template, "validation_json", None) or {})
        semantic_field = _first_semantic_field(validation)
        missing_context = _missing_context(validation, semantic_field)
        payload = {
            "template_id": template_id,
            "template_version": getattr(template, "version", None),
            "source_ref": source_ref,
            "workflow": "rule-asset-generation",
            "command": getattr(template, "canonical_command", None),
            "vendor": getattr(template, "vendor", None),
            "model": getattr(template, "model", None),
            "family": getattr(template, "family", None),
            "category": getattr(template, "category", None),
            "contract_seed": _contract_seed(template, validation),
            "workflow_context_defaults": _workflow_context_defaults(
                template,
                validation,
                semantic_field,
                source_ref=source_ref,
            ),
            "missing_context": missing_context,
            "privacy": {
                "raw_cli_included": False,
                "records_included": False,
                "historical_values_included": False,
            },
        }
        job = self.job_repository.create_job(
            job_type="OBSERVATION_CONTRACT",
            source_ref=source_ref,
            asset_ref=f"parser_template:{template_id}",
            asset_type="PARSER_TEMPLATE",
            asset_id=template_id,
            asset_version_id=getattr(template, "version", None),
            asset_digest=getattr(template, "contract_sha256", None),
            payload=payload,
            validation_report={
                "success": False,
                "generator": "parser-template-activation",
                "ready_for_generation": not missing_context,
                "missing_context": missing_context,
                "note": (
                    "Draft seed only. Provide sanitized records and historical "
                    "summaries before running rule-asset-generation."
                ),
            },
            created_by="parser-template-activation",
        )
        return {
            "success": True,
            "created": True,
            "job": job,
            "template_id": template_id,
            "source_ref": source_ref,
            "missing_context": missing_context,
        }

    def auto_build_context(
        self,
        job_id: str,
        *,
        dataset_ids: list[str] | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Build immutable context only from an explicit offline asset-source dataset."""
        from src.apps.rule_governance.backend.rule_control.dataset_context import (
            DatasetRuleGenerationContextService,
        )

        selected_dataset_ids = sorted(
            {str(item).strip() for item in (dataset_ids or []) if str(item).strip()}
        )
        if not selected_dataset_ids:
            raise ValueError(
                "select an offline patrol dataset before building rule generation context"
            )
        prepared = DatasetRuleGenerationContextService().prepare(
            job_id=job_id,
            dataset_ids=selected_dataset_ids,
            created_by=actor or "parser-template-activation",
        )
        quality = {
            "passed": bool((prepared.get("context") or {}).get("records")),
            "record_count": len((prepared.get("context") or {}).get("records") or []),
            "scope": "offline_period_authoritative_runs",
        }
        quality_passed = quality["passed"]
        job = self.job_repository.get_job(job_id)
        payload = dict((job or {}).get("payload_json") or {})
        payload["auto_context_id"] = prepared["context_id"]
        payload["auto_context_digest"] = prepared["context_digest"]
        validation = dict((job or {}).get("validation_report_json") or {})
        validation["auto_context"] = {
            "context_id": prepared["context_id"],
            "context_digest": prepared["context_digest"],
            "sampled_record_count": (prepared.get("privacy") or {}).get("sampled_record_count"),
            "quality": quality,
        }
        if quality_passed:
            payload["missing_context"] = []
            validation["ready_for_generation"] = True
            validation["missing_context"] = []
        else:
            remaining = list(payload.get("missing_context") or ["records"])
            if "records" not in remaining:
                remaining.append("records")
            payload["missing_context"] = remaining
            validation["ready_for_generation"] = False
            validation["missing_context"] = remaining
            validation["quality_gate_failed"] = quality.get("reasons") or []
        self.job_repository.update_job(
            job_id,
            payload_json=payload,
            validation_report_json=validation,
        )
        return {
            "success": True,
            "job_id": job_id,
            **prepared,
            "ready": quality_passed,
            "quality": quality,
            "missing_context": payload["missing_context"],
        }

    def build_generation_context(
        self,
        job_id: str,
        *,
        context_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        job = self.job_repository.get_job(job_id)
        if job is None:
            raise KeyError("rule generation job not found")
        payload = dict(job.get("payload_json") or {})
        if payload.get("workflow") != "rule-asset-generation":
            raise ValueError("job is not a parser template rule asset seed")
        if not str(job.get("source_ref") or "").startswith("parser_template:"):
            raise ValueError("job source is not a parser template")

        overrides = dict(context_overrides or {})
        _reject_private_context(overrides)
        defaults = dict(payload.get("workflow_context_defaults") or {})
        context = {**defaults, **overrides}
        context["source_ref"] = job.get("source_ref") or context.get("source_ref")
        template_id = str(payload.get("template_id") or "").strip()
        if template_id:
            expected_ref = f"parser_template:{template_id}"
            if context.get("source_ref") != expected_ref:
                raise ValueError("rule generation source_ref does not match parser template")
            context["schema_id"] = template_id
            context["schema_ref"] = expected_ref
            context["source_template_id"] = template_id
        missing = _generation_context_missing(
            context,
            seed_missing=list(payload.get("missing_context") or []),
        )
        return {
            "success": True,
            "ready": not missing,
            "job_id": job_id,
            "workflow": "rule-asset-generation",
            "context": context,
            "missing_context": missing,
            "privacy": {
                "raw_cli_included": False,
                "records_source": "validated_structured_records"
                if context.get("records")
                else "not_supplied",
            },
        }

    def start_generation(
        self,
        job_id: str,
        *,
        context_overrides: dict[str, Any] | None = None,
        actor: str | None = None,
        context_id: str | None = None,
        actor_user_id: str | None = None,
        trigger: str = "manual",
        policy_revision: str | None = None,
    ) -> dict[str, Any]:
        from src.apps.rule_governance.backend.identity import actor_user_id as normalize_actor_user_id

        principal = normalize_actor_user_id(actor_user_id)
        if trigger not in {"manual", "schedule"}:
            raise ValueError("unsupported rule generation trigger")
        if not context_id:
            raise ValueError(
                "rule asset generation requires an immutable offline patrol context_id"
            )
        overrides = dict(context_overrides or {})
        immutable_context: dict[str, Any] | None = None
        from src.apps.rule_governance.backend.rule_control.dataset_context import (
            DatasetRuleGenerationContextService,
        )

        immutable_context = DatasetRuleGenerationContextService().load(
            context_id=context_id,
            job_id=job_id,
        )
        if immutable_context.get("offline_job_id"):
            from src.apps.rule_governance.backend.access import authorize
            from src.core.patrol.offline_jobs import OfflinePatrolJobService
            user = authorize(principal)
            OfflinePatrolJobService().get_job(immutable_context["offline_job_id"], owner_id=principal,
                                             is_admin=user.is_admin(), purpose="rule_asset_source")
        frozen = dict(immutable_context.get("context") or {})
        for key, value in frozen.items():
            if key in overrides and overrides[key] != value:
                raise ValueError(
                    f"immutable rule generation context field cannot be overridden: {key}"
                )
        overrides = {**frozen, **overrides}

        prepared = self.build_generation_context(
            job_id,
            context_overrides=overrides,
        )
        if not prepared["ready"]:
            raise ValueError(
                "rule asset generation context is incomplete: "
                + ", ".join(prepared["missing_context"])
            )
        from src.apps.rule_governance.backend.rule_control.generation_jobs import GenerationJobs

        workflow_context = dict(prepared["context"])
        workflow_context["rule_generation_seed_job_id"] = job_id
        workflow_context["rule_generation_context_id"] = context_id
        workflow_context["rule_generation_context_digest"] = immutable_context["context_digest"]
        workflow_context["rule_generation_actor"] = actor or "parser-control-plane"
        workflow_context["rule_generation_identity"] = {
            "actor": workflow_context["rule_generation_actor"], "actor_user_id": principal,
            "trigger": trigger, "policy_revision": policy_revision,
        }
        run_id = GenerationJobs().submit(job_id, workflow_context, user_id=principal)
        return {
            "success": True,
            "execution": "rule-generation",
            "run_id": run_id,
            "job_id": job_id,
            "context_id": context_id,
            "context": workflow_context,
        }


    start_generation_workflow = start_generation


def _safe_source_field_hints(raw_fields: Any) -> dict[str, dict[str, Any]]:
    """Keep semantic labels while excluding raw evidence and device identity."""

    if not isinstance(raw_fields, dict):
        return {}
    hints: dict[str, dict[str, Any]] = {}
    identity_pattern = re.compile(
        r"(?:\b\d{1,3}(?:\.\d{1,3}){3}\b|"
        r"\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b)",
        re.IGNORECASE,
    )
    for raw_name, raw_config in raw_fields.items():
        if not isinstance(raw_config, dict):
            continue
        field_name = str(raw_name).strip()
        if not field_name:
            continue
        labels = []
        for raw_label in raw_config.get("source_labels") or []:
            label = " ".join(str(raw_label).split())
            if (
                not label
                or len(label) > 160
                or identity_pattern.search(label)
            ):
                continue
            labels.append(label)
        hint = {
            **({"source_labels": list(dict.fromkeys(labels))} if labels else {}),
            **(
                {"role": str(raw_config["role"])}
                if raw_config.get("role")
                else {}
            ),
            **(
                {"change_behavior": str(raw_config["change_behavior"])}
                if raw_config.get("change_behavior")
                else {}
            ),
            **(
                {"monitor": bool(raw_config["monitor"])}
                if raw_config.get("monitor") is not None
                else {}
            ),
            **(
                {"canonical_candidate": str(raw_config["canonical_candidate"])}
                if raw_config.get("canonical_candidate")
                else {}
            ),
        }
        if hint:
            hints[field_name] = hint
    return hints


def _parser_spec_hints(validation: dict[str, Any]) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, dict[str, Any]]],
]:
    spec = validation.get("parser_spec")
    if not isinstance(spec, dict):
        spec = validation.get("source_parser_spec")
    if not isinstance(spec, dict):
        return {}, {}
    raw_sets = spec.get("record_sets")
    if isinstance(raw_sets, dict):
        raw_sets = [
            {"name": str(name), **dict(config)}
            for name, config in raw_sets.items()
            if isinstance(config, dict)
        ]
    if isinstance(raw_sets, list):
        by_set = {
            str(item.get("name") or item.get("entity") or "").strip(): (
                _safe_source_field_hints(item.get("fields"))
            )
            for item in raw_sets
            if isinstance(item, dict)
            and str(item.get("name") or item.get("entity") or "").strip()
        }
        return {}, by_set
    return _safe_source_field_hints(spec.get("fields")), {}


def build_contract_seed(template: Any, validation: dict[str, Any]) -> dict[str, Any]:
    parent_hints, record_set_hints = _parser_spec_hints(validation)
    record_sets = [
        {
            **dict(item),
            **(
                {
                    "source_field_hints": record_set_hints.get(
                        str(item.get("name") or ""),
                        {},
                    )
                }
                if record_set_hints.get(str(item.get("name") or ""))
                else {}
            ),
        }
        for item in validation.get("record_sets") or []
        if isinstance(item, dict)
    ]
    return {
        "category": getattr(template, "category", None),
        "entity_type": validation.get("entity_type") or getattr(template, "category", None),
        "primary_keys": list(validation.get("primary_keys") or []),
        "canonical_entity_type": validation.get("canonical_entity_type"),
        "entity_identity_fields": list(
            validation.get("entity_identity_fields") or []
        ),
        "entity_dimensions": dict(validation.get("entity_dimensions") or {}),
        "required_fields": list(validation.get("required_fields") or []),
        "optional_fields": list(validation.get("optional_fields") or []),
        "ignore_fields": list(validation.get("ignore_fields") or []),
        "allow_empty": bool(validation.get("allow_empty")),
        "empty_patterns": list(validation.get("empty_patterns") or []),
        "validators": dict(validation.get("validators") or {}),
        "state_semantics": dict(validation.get("state_semantics") or {}),
        "source_field_hints": parent_hints,
        "record_sets": record_sets,
        "contract_version": validation.get("contract_version"),
        "contract_sha256": validation.get("contract_sha256"),
    }


_contract_seed = build_contract_seed


def _workflow_context_defaults(
    template: Any,
    validation: dict[str, Any],
    semantic_field: tuple[str, dict[str, Any]] | None,
    *,
    source_ref: str,
) -> dict[str, Any]:
    field_name = semantic_field[0] if semantic_field else None
    field_semantics = semantic_field[1] if semantic_field else {}
    observation_type = (
        str(field_semantics.get("observation_type") or "").strip()
        if isinstance(field_semantics, dict)
        else ""
    )
    return {
        "command": getattr(template, "canonical_command", None),
        "vendor": getattr(template, "vendor", None),
        "model": getattr(template, "model", None),
        "source_ref": source_ref,
        "schema_id": str(getattr(template, "id")),
        "schema_ref": source_ref,
        "source_template_id": str(getattr(template, "id")),
        "proposal": _contract_seed(template, validation),
        "observation_type": observation_type or None,
        "field_name": field_name,
        "field_semantics": field_semantics,
        "device_role": None,
        "policy_intent": validation.get("policy_intent"),
        "event_intent": validation.get("event_intent"),
        "event_constraints": validation.get("event_constraints"),
        "event_constraints_by_observation_type": validation.get(
            "event_constraints_by_observation_type"
        ),
        "policy_targets": _policy_targets(validation),
        "generation_provider": "llm",
        "max_agent_attempts": 3,
        "replay_limit": 1000,
    }


def _first_semantic_field(validation: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    targets = _policy_targets(validation)
    if targets:
        first = targets[0]
        return str(first["field_name"]), dict(first["field_semantics"])
    semantics = validation.get("state_semantics")
    if not isinstance(semantics, dict):
        return None
    for field_name, field_semantics in semantics.items():
        if isinstance(field_semantics, dict):
            return str(field_name), dict(field_semantics)
    return None


def _policy_targets(validation: dict[str, Any]) -> list[dict[str, Any]]:
    semantics = validation.get("state_semantics")
    if not isinstance(semantics, dict):
        return []
    targets: list[dict[str, Any]] = []
    for field_name, field_semantics in semantics.items():
        if not isinstance(field_semantics, dict):
            continue
        observation_type = str(field_semantics.get("observation_type") or "").strip()
        semantic_type = str(field_semantics.get("semantic_type") or "").strip()
        if not observation_type or semantic_type in {"identity", ""}:
            continue
        targets.append(
            {
                "field_name": str(field_name),
                "observation_type": observation_type,
                "field_semantics": dict(field_semantics),
                "requires_historical_values": semantic_type in {"gauge", "counter"},
            }
        )
    targets.sort(key=lambda item: str(item["field_name"]))
    return targets


def _missing_context(
    validation: dict[str, Any],
    semantic_field: tuple[str, dict[str, Any]] | None,
) -> list[str]:
    del semantic_field
    missing = ["records"]
    if not validation.get("primary_keys") and not validation.get("entity_type"):
        missing.append("contract_identity")
    return list(dict.fromkeys(missing))


def _reject_private_context(context: dict[str, Any]) -> None:
    forbidden = {
        "command_output",
        "password",
        "raw",
        "raw_cli",
        "raw_output",
        "raw_text",
        "secret",
        "text_output",
        "token",
    }
    found = sorted(_find_private_context_keys(context, forbidden=forbidden))
    if found:
        raise ValueError(
            "rule asset generation context must not include private raw fields: " + ", ".join(found)
        )


def _find_private_context_keys(
    value: Any,
    *,
    forbidden: set[str],
    path: str = "",
) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            nested_path = f"{path}.{key_text}" if path else key_text
            if key_text.lower() in forbidden:
                found.add(nested_path)
            found.update(
                _find_private_context_keys(
                    nested,
                    forbidden=forbidden,
                    path=nested_path,
                )
            )
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            nested_path = f"{path}[{index}]" if path else f"[{index}]"
            found.update(
                _find_private_context_keys(
                    nested,
                    forbidden=forbidden,
                    path=nested_path,
                )
            )
    return found


def _generation_context_missing(
    context: dict[str, Any],
    *,
    seed_missing: list[str],
) -> list[str]:
    missing: list[str] = []
    required = ["command", "records"]
    for key in required:
        value = context.get(key)
        if value in (None, "", [], {}):
            missing.append(key)
    records = context.get("records")
    if records not in (None, "", [], {}) and (
        not isinstance(records, list) or not all(isinstance(item, dict) for item in records)
    ):
        missing.append("records:list[object]")
    del seed_missing
    if context.get("field_semantics") and not isinstance(
        context.get("field_semantics"),
        dict,
    ):
        missing.append("field_semantics:object")
    target_resolution = str(
        context.get("target_resolution") or ""
    ).strip()
    if target_resolution == "unclassified":
        missing.append("check_item_target")
    return list(dict.fromkeys(missing))

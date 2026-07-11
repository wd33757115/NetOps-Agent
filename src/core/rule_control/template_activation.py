"""Create safe rule-asset generation seeds from activated parser templates."""

from __future__ import annotations

from typing import Any

from src.core.parser.repository import ParserRepository
from src.core.rule_control.repository import RuleGenerationJobRepository


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
        parser_repository: ParserRepository | None = None,
        job_repository: RuleGenerationJobRepository | None = None,
    ) -> None:
        self.parser_repository = parser_repository or ParserRepository()
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
                "records_source": "operator_or_workflow_supplied_sanitized_records"
                if context.get("records")
                else "not_supplied",
            },
        }

    def start_generation_workflow(
        self,
        job_id: str,
        *,
        context_overrides: dict[str, Any] | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        prepared = self.build_generation_context(
            job_id,
            context_overrides=context_overrides,
        )
        if not prepared["ready"]:
            raise ValueError(
                "rule asset generation context is incomplete: "
                + ", ".join(prepared["missing_context"])
            )
        from src.core.workflows.engine import WorkflowEngine

        run_id = WorkflowEngine.start(
            "rule-asset-generation",
            prepared["context"],
            source="parser-control-plane",
            user_id=actor,
        )
        return {
            "success": True,
            "workflow": "rule-asset-generation",
            "run_id": run_id,
            "job_id": job_id,
            "context": prepared["context"],
        }


def _contract_seed(template: Any, validation: dict[str, Any]) -> dict[str, Any]:
    return {
        "category": getattr(template, "category", None),
        "entity_type": validation.get("entity_type") or getattr(template, "category", None),
        "primary_keys": list(validation.get("primary_keys") or []),
        "required_fields": list(validation.get("required_fields") or []),
        "optional_fields": list(validation.get("optional_fields") or []),
        "ignore_fields": list(validation.get("ignore_fields") or []),
        "allow_empty": bool(validation.get("allow_empty")),
        "validators": dict(validation.get("validators") or {}),
        "state_semantics": dict(validation.get("state_semantics") or {}),
        "contract_version": validation.get("contract_version"),
        "contract_sha256": validation.get("contract_sha256"),
    }


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
        "proposal": _contract_seed(template, validation),
        "observation_type": observation_type or None,
        "field_name": field_name,
        "field_semantics": field_semantics,
        "device_role": None,
        "policy_intent": validation.get("policy_intent"),
        "event_intent": validation.get("event_intent"),
        "policy_targets": _policy_targets(validation),
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
    missing = ["records"]
    if semantic_field is None:
        missing.extend(["observation_type", "field_name", "field_semantics"])
        return missing
    field_semantics = semantic_field[1]
    if not field_semantics.get("observation_type"):
        missing.append("observation_type")
    semantic_type = str(field_semantics.get("semantic_type") or "").strip()
    if semantic_type in {"gauge", "counter"}:
        missing.append("historical_values")
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
    required = ["command", "records", "observation_type", "field_name"]
    for key in required:
        value = context.get(key)
        if value in (None, "", [], {}):
            missing.append(key)
    records = context.get("records")
    if records not in (None, "", [], {}) and (
        not isinstance(records, list) or not all(isinstance(item, dict) for item in records)
    ):
        missing.append("records:list[object]")
    if "historical_values" in seed_missing and not isinstance(
        context.get("historical_values"),
        list,
    ):
        missing.append("historical_values")
    if context.get("field_semantics") and not isinstance(
        context.get("field_semantics"),
        dict,
    ):
        missing.append("field_semantics:object")
    return list(dict.fromkeys(missing))

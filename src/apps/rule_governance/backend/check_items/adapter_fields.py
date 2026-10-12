"""Let an adapter provide check item fields it does not provide yet.

The adapter's own TextFSM template is extended, never rewritten. The outputs it
has actually parsed in patrols are the samples: the model sees a few of them,
sanitized, and afterwards every stored output is parsed with the template in
force and with the extended one. Every value the adapter already provided must
come out the same, and a new field counts only where the outputs contain it.
The trial saves nothing.
"""

from __future__ import annotations

import io
import re
from dataclasses import replace
from typing import Any

import textfsm

from src.apps.rule_governance.backend.check_items.catalog import CheckItemCatalogRepository
from src.apps.rule_governance.backend.parser.parser_bundle import parse_parser_bundle
from src.apps.rule_governance.backend.parser.repository import ParserTemplateRepository
from src.apps.rule_governance.backend.parser.sanitization import sanitize_parser_sample
from src.apps.rule_governance.backend.parser_generation import (
    ParserSample,
    ParserSpec,
    SampleRole,
    SampleSet,
    SimpleParserGenerationService,
    TemplateCandidate,
    TextFSMValidationBackend,
    validate_template_runtime_compatibility,
)
from src.apps.rule_governance.backend.parser_generation.samples import MAX_MODEL_SAMPLE_COUNT
from src.apps.rule_governance.backend.parser_generation.spec import _ALLOWED_TYPES, FieldSpec
from src.infrastructure.db.models import CollectionImplementationVersionRecord, CommandSnapshot
from src.infrastructure.db.postgres import get_db_session

# Enough for one extension and two repairs; a field the model cannot add in
# three tries is better looked at by a person than retried.
MAX_MODEL_CALLS = 3
# Distinct outputs replayed after generation. The model sees only the first few.
MAX_OUTPUTS = 30


class AdapterFieldsError(ValueError):
    """The adapter cannot be extended as asked; the message is for the engineer."""


def _projection(adapter: Any, check_item_id: str) -> dict[str, Any]:
    for output in (adapter.adapter_spec or {}).get("outputs") or []:
        if output.get("check_item_id") == check_item_id:
            return output
    raise AdapterFieldsError("这个适配器不按字段解析，无法补字段")


def missing_fields(adapter: Any, item: Any) -> list[str]:
    """Fields of the check item that the adapter's parser does not write."""

    provided = set(dict(_projection(adapter, item.id).get("projection") or {}).get("fields") or {})
    keys = set(item.canonical_contract.get("identity_dimensions") or [])
    return [
        str(row["canonical_field"])
        for row in item.canonical_contract.get("observations") or []
        if row.get("canonical_field") not in provided and row.get("canonical_field") not in keys
    ]


def record_set_template(stored_text: str, record_set: str) -> str:
    """The TextFSM the adapter reads: a stored template may bundle one per record set."""

    bundle = parse_parser_bundle(stored_text)
    if bundle is None:
        return stored_text
    for entry in bundle.record_sets:
        if entry.name == record_set:
            return entry.template_text
    if len(bundle.record_sets) == 1:
        return bundle.record_sets[0].template_text
    raise AdapterFieldsError(f"解析模板里没有记录集 {record_set}")


def _field_spec(row: dict[str, Any]) -> FieldSpec:
    definition = dict(row.get("semantic_definition") or {})
    value_type = str(row.get("value_type") or "text")
    values = tuple(str(value) for value in definition.get("allowed_values") or [])
    if value_type not in _ALLOWED_TYPES or (value_type == "enum" and not values):
        value_type = "text"
    return FieldSpec(
        name=str(row["canonical_field"]),
        # Never required here: what the device does not print cannot be demanded of its parser.
        required=False,
        value_type=value_type,
        values=values if value_type == "enum" else (),
        operational_reason=str(row.get("description") or definition.get("description") or "") or None,
    )


def _values(record: dict[str, Any], name: str) -> Any:
    value = record.get(name)
    return [str(item) for item in value] if isinstance(value, list) else str(value or "")


def stored_outputs(implementation_id: str) -> list[dict[str, str]]:
    """Distinct command outputs this adapter parsed in patrols, newest first."""

    with get_db_session() as db:
        version_ids = [
            str(row.id)
            for row in db.query(CollectionImplementationVersionRecord.id).filter(
                CollectionImplementationVersionRecord.implementation_id == implementation_id
            )
        ]
        rows = (
            db.query(CommandSnapshot.id, CommandSnapshot.raw_text, CommandSnapshot.raw_text_hash)
            .filter(
                CommandSnapshot.implementation_version_id.in_(version_ids or [""]),
                CommandSnapshot.parse_status == "parsed",
            )
            .order_by(CommandSnapshot.observed_at.desc())
            .limit(400)
        )
        outputs: dict[str, dict[str, str]] = {}
        for row in rows:
            key = str(row.raw_text_hash or row.id)
            if row.raw_text and key not in outputs:
                outputs[key] = {"id": str(row.id), "text": str(row.raw_text)}
            if len(outputs) >= MAX_OUTPUTS:
                break
        return list(outputs.values())


def base_spec(item: Any, template_text: str, records: list[dict[str, Any]], backend: Any) -> ParserSpec:
    """Describe what the template in force already does, so that only the new fields are asked of the model."""

    names = list(backend.field_names(template_text))
    keys = [name for name in item.canonical_contract.get("identity_dimensions") or [] if name in names]
    if not keys:
        raise AdapterFieldsError("解析模板里没有可以区分对象的字段，无法安全地补字段")
    # A key the device leaves blank on some objects is still a key; validation needs a stand-in for it.
    blank = {key for key in keys if any(not _values(record, key) for record in records)}
    entity = re.sub(r"[^a-z0-9_]", "_", str(item.canonical_contract.get("entity_type") or "record").lower())
    return ParserSpec(
        entity=entity if re.fullmatch(r"[a-z][a-z0-9_]*", entity) else "record",
        primary_keys=tuple(keys),
        fields=tuple(
            FieldSpec(name=name, required=name in keys and name not in blank, value_type="text",
                      primary_key_default="aggregate" if name in blank else None)
            for name in names
        ),
        presence="optional",
    )


def value_definitions(template_text: str) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Each Value of a template with the regex it captures and its options."""

    parsed = textfsm.TextFSM(io.StringIO(template_text))
    return {str(value.name): (str(value.regex), tuple(sorted(value.OptionNames()))) for value in parsed.values}


def extension_only(current_text: str, texts: dict[str, str]) -> Any:
    """A validator that sends back any candidate which touches what the adapter already provides.

    The model is free to add; it is not free to rewrite. A Value in force keeps
    its regex and options to the letter, and every sample still yields the same
    records for those Values.
    """

    in_force = value_definitions(current_text)

    def validate(template_text: str, spec: Any, samples: Any, backend: Any) -> Any:
        report = validate_template_runtime_compatibility(template_text, spec, samples, backend)
        try:
            candidate = value_definitions(template_text)
        except textfsm.TextFSMTemplateError:
            return report
        rewritten = sorted(name for name, definition in in_force.items() if candidate.get(name) != definition)
        problems = []
        if rewritten:
            problems.append(
                "existing Value definitions must stay exactly as they are; restore the regex and options of: "
                + ", ".join(f"{name} {in_force[name][0]}" for name in rewritten)
            )
        else:
            moved = replay(backend, current_text, template_text, texts, [])["differences"]
            if moved:
                problems.append(
                    "the values already provided changed; add the new fields without altering what existing rules capture: "
                    + "; ".join(moved[:5])
                )
        if not problems:
            return report
        return replace(
            report,
            passed=False,
            hard_gates={**report.hard_gates, "field_inventory_aligned": False},
            diagnostics=(*problems, *report.diagnostics),
        )

    return validate


def without_values(template_text: str, names: list[str]) -> str | None:
    """Drop Values no rule refers to; None when a rule still uses one of them."""

    lines = template_text.splitlines()
    rules = "\n".join(line for line in lines if not line.startswith("Value "))
    if any(f"${{{name}}}" in rules or re.search(rf"\${name}\b", rules) for name in names):
        return None
    kept = [
        line for line in lines
        if not (line.startswith("Value ") and any(re.search(rf"\s{re.escape(name)}\s+\(", line) for name in names))
    ]
    return "\n".join(kept) + "\n"


def replay(backend: Any, current_text: str, candidate_text: str, texts: dict[str, str], wanted: list[str]) -> dict[str, Any]:
    """Parse every sample with both templates: what was provided must not move, what is new must appear."""

    differences: list[str] = []
    found = {name: {"records": 0, "samples": 0, "examples": []} for name in wanted}
    records = 0
    for sample_id, text in texts.items():
        before = backend.parse(current_text, text)
        after = backend.parse(candidate_text, text)
        records += len(after)
        if len(before) != len(after):
            differences.append(f"样本 {sample_id}：记录数由 {len(before)} 变为 {len(after)}")
            continue
        for index, (old, new) in enumerate(zip(before, after)):
            changed = sorted(name for name in old if _values(old, name) != _values(new, name))
            if changed:
                differences.append(f"样本 {sample_id} 第 {index + 1} 条：{'、'.join(changed)} 的值变了")
        for name in wanted:
            values = [value for value in (_values(record, name) for record in after) if value]
            if values:
                found[name]["samples"] += 1
                found[name]["records"] += len(values)
                found[name]["examples"] = [*found[name]["examples"], *map(str, values)][:3]
    return {"record_count": records, "found": found, "differences": differences}


def trial_add_fields(
    implementation_id: str,
    check_item_id: str,
    fields: list[str] | None = None,
    *,
    model: Any | None = None,
) -> dict[str, Any]:
    """Ask the model to add the fields to the adapter's template and replay its stored outputs.

    ``model`` replaces the production generation agent in tests.
    """

    catalog = CheckItemCatalogRepository().load_active()
    adapter = next((row for row in catalog.implementations if row.id == implementation_id), None)
    item = catalog.check_item(check_item_id)
    if adapter is None or item is None or item.id not in adapter.check_item_ids:
        raise AdapterFieldsError("适配器或检查项不存在，或两者没有关联")
    missing = missing_fields(adapter, item)
    wanted = [name for name in (fields or missing) if name in missing]
    if not wanted:
        raise AdapterFieldsError("这个适配器已经提供了所选字段")
    rows = {row["canonical_field"]: row for row in item.canonical_contract.get("observations") or []}
    template = ParserTemplateRepository().get_template(
        str(dict(adapter.adapter_spec.get("parser") or {}).get("template_id") or "")
    )
    if template is None:
        raise AdapterFieldsError("找不到这个适配器的解析模板")
    outputs = stored_outputs(adapter.id)
    if not outputs:
        raise AdapterFieldsError("这个适配器还没有在巡检中解析过任何输出，没有样本可用来验证")

    backend = TextFSMValidationBackend()
    current_text = record_set_template(
        str(template.template_text), str(_projection(adapter, item.id).get("source_record_set") or "")
    )
    raw = {output["id"]: output["text"] for output in outputs}
    sanitized = {key: sanitize_parser_sample(text).text for key, text in raw.items()}
    samples = tuple(
        ParserSample(
            sample_id=key,
            raw_text=raw[key],
            role=SampleRole.GENERATION if index < MAX_MODEL_SAMPLE_COUNT else SampleRole.ACCEPTANCE,
            prompt_text=text if index < MAX_MODEL_SAMPLE_COUNT else None,
            validation_text_override=text,
        )
        for index, (key, text) in enumerate(sanitized.items())
    )
    result: dict[str, Any] = {
        "implementation_id": adapter.id,
        "check_item_id": item.id,
        "template_id": str(template.id),
        "requested_fields": wanted,
        "sample_count": len(outputs),
        "model_calls": 0,
    }
    current_records = [record for text in sanitized.values() for record in backend.parse(current_text, text)]
    spec = base_spec(item, current_text, current_records, backend)
    # The model is asked for the new fields only. If the template in force does not
    # pass as it is, it would start repairing things that are in production.
    untouched = validate_template_runtime_compatibility(current_text, spec, samples, backend)
    if not untouched.passed:
        return {**result, "status": "unsupported", "fields": [], "regression": None, "template_text": None,
                "reason": f"现有模板没有通过基线校验，不交给 AI 修改：{untouched.primary_diagnostic}"}

    if model is None:
        # Imported here: the generator scripts pull in the model client.
        from src.apps.rule_governance.backend.generators.textfsm_generator.scripts.parser_model import (
            TextFSMGenerationAgent,
        )

        model = TextFSMGenerationAgent(
            vendor=str(template.vendor),
            model=str(template.model or template.profile),
            command=str(template.canonical_command),
            validation_samples=samples,
        )
    extended = replace(spec, fields=(*spec.fields, *(_field_spec(rows[name]) for name in wanted)))
    outcome = SimpleParserGenerationService(
        compilers=(),
        model=model,
        backend=backend,
        max_model_calls=MAX_MODEL_CALLS,
        # The template in force is the starting point; it fails only for the fields it lacks.
        seed_candidates=(TemplateCandidate(
            template_text=current_text,
            provider="adapter:template_in_force",
            protocol_version="adapter-field-extension-v1",
            provenance={"template_id": str(template.id), "implementation_id": adapter.id},
        ),),
        validator=extension_only(current_text, sanitized),
    ).generate(job_id=f"adapter-fields:{adapter.id}", spec=extended, sample_set=SampleSet(samples))
    result.update(model_calls=outcome.model_calls, stop_reason=outcome.stop_reason)
    candidate = outcome.candidate
    if not outcome.passed or candidate is None or candidate.template_text == current_text:
        final = outcome.attempts[-1].generation_validation if outcome.attempts else None
        return {**result, "status": "failed", "fields": [], "regression": None, "template_text": None,
                "reason": final.primary_diagnostic if final is not None and not final.passed else outcome.stop_reason}

    # Judged on the outputs as the devices printed them, not on the sanitized copies the model saw.
    replayed = replay(backend, current_text, candidate.template_text, raw, wanted)
    found, differences = replayed["found"], replayed["differences"]
    provided = [name for name in wanted if found[name]["records"]]
    absent = [name for name in wanted if not found[name]["records"]]
    final_text = candidate.template_text
    if provided and absent and not differences:
        # A field the outputs never show is not something this device provides; it stays out of the template.
        trimmed = without_values(final_text, absent)
        if trimmed is not None and not replay(backend, final_text, trimmed, raw, provided)["differences"]:
            final_text = trimmed
    return {
        **result,
        "status": "regressed" if differences else "ready" if provided else "no_data",
        "record_count": replayed["record_count"],
        "fields": [{"name": name, **found[name]} for name in wanted],
        "provided_fields": provided,
        "absent_fields": absent,
        "regression": {"passed": not differences, "differences": differences[:10]},
        "template_text": final_text if provided and not differences else None,
    }


__all__ = [
    "AdapterFieldsError", "base_spec", "extension_only", "missing_fields", "record_set_template", "replay",
    "stored_outputs", "trial_add_fields", "value_definitions", "without_values",
]

"""Let an adapter provide check item fields it does not provide yet.

The adapter's own TextFSM template is extended, never rewritten. The outputs it
has actually parsed in patrols are the samples: the model sees a few of them,
sanitized, and afterwards every stored output is parsed with the template in
force and with the extended one. Every value the adapter already provided must
come out the same, and a new field counts only where the outputs contain it.
The trial saves nothing.

What the model returns is held to three things, because real trials showed it
breaking each of them. A Value in force is put back to the letter. A rule in
force stays where it is, in order: few samples cannot tell a rewrite from an
extension. And a value that parses is not yet the right value, so every new
field comes with the output line it was read from, for a person to confirm.
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


_VALUE_LINE = re.compile(r"^Value\s+(?:[A-Za-z,]+\s+)?(\w+)\s+\(")
_STATE_LINE = re.compile(r"^[A-Za-z_]\w*\s*$")


def _value_lines(template_text: str) -> dict[str, str]:
    lines = {}
    for line in template_text.splitlines():
        match = _VALUE_LINE.match(line)
        if match:
            lines[match.group(1)] = line
    return lines


def restore_values(current_text: str, candidate_text: str) -> str:
    """Put every Value in force back into the candidate exactly as it is written today.

    Asked to add fields, the model also drops ``Required`` or ``Filldown`` or
    loosens a regex it was not asked about. Sending that back costs a model
    call each time and ended trials as failures; undoing it costs nothing, and
    the replay afterwards still decides whether the candidate works.
    """

    in_force = _value_lines(current_text)
    seen: set[str] = set()
    lines = []
    for line in candidate_text.splitlines():
        match = _VALUE_LINE.match(line)
        if match and match.group(1) in in_force:
            seen.add(match.group(1))
            lines.append(in_force[match.group(1)])
        else:
            lines.append(line)
    dropped = [line for name, line in in_force.items() if name not in seen]
    if dropped:
        first = next((index for index, line in enumerate(lines) if line.startswith("Value ")), 0)
        lines[first:first] = dropped
    return "\n".join(lines) + "\n"


def state_rules(template_text: str) -> dict[str, list[str]]:
    """The rule lines of each state, in order and without their indentation."""

    states: dict[str, list[str]] = {}
    current = None
    for line in template_text.splitlines():
        if line.startswith(("Value ", "#")) or not line.strip():
            continue
        if not line.startswith((" ", "\t")) and _STATE_LINE.match(line):
            current = line.strip()
            states.setdefault(current, [])
        elif current is not None:
            states[current].append(line.strip())
    return states


def rules_lost(current_text: str, candidate_text: str) -> list[str]:
    """Rules in force that the candidate no longer has, in their state and in the same order.

    Replaying samples cannot stand in for this: with one stored output, a
    template rewritten to fit that output alone parses it exactly as before.
    """

    theirs = state_rules(candidate_text)
    lost = []
    for state, rules in state_rules(current_text).items():
        kept, position = theirs.get(state, []), 0
        for rule in rules:
            try:
                position = kept.index(rule, position) + 1
            except ValueError:
                lost.append(f"{state}: {rule}")
    return lost


def added_lines(current_text: str, candidate_text: str) -> list[str]:
    """What the candidate adds to the template in force: its new Values, then its new rules."""

    in_force = set(_value_lines(current_text).values())
    values = [line for line in _value_lines(candidate_text).values() if line not in in_force]
    rules = []
    ours = state_rules(current_text)
    for state, lines in state_rules(candidate_text).items():
        remaining = list(ours.get(state, []))
        for line in lines:
            if remaining and line == remaining[0]:
                remaining.pop(0)
            else:
                rules.append(line)
    return [*values, *rules]


class _KeepsValues:
    """The generation model, with the Values in force restored in whatever it returns."""

    def __init__(self, model: Any, current_text: str) -> None:
        self._model, self._current_text = model, current_text

    def open_session(self, spec: Any, samples: Any) -> Any:
        session, current_text = self._model.open_session(spec, samples), self._current_text

        class Session:
            def next(self, validation_feedback: dict[str, object] | None = None) -> Any:
                turn = session.next(validation_feedback)
                if turn.candidate is None:
                    return turn
                restored = restore_values(current_text, turn.candidate.template_text)
                return replace(turn, candidate=replace(turn.candidate, template_text=restored))

        return Session()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._model, name)


def extension_only(current_text: str, texts: dict[str, str]) -> Any:
    """A validator that sends back any candidate which touches what the adapter already provides.

    The model is free to add; it is not free to rewrite. A Value in force keeps
    its regex and options to the letter, a rule in force stays in its state and
    in order, and every sample still yields the same records for those Values.
    """

    in_force = value_definitions(current_text)

    def validate(template_text: str, spec: Any, samples: Any, backend: Any) -> Any:
        report = validate_template_runtime_compatibility(template_text, spec, samples, backend)
        try:
            candidate = value_definitions(template_text)
        except textfsm.TextFSMTemplateError:
            return report
        rewritten = sorted(name for name, definition in in_force.items() if candidate.get(name) != definition)
        lost = rules_lost(current_text, template_text)
        problems = []
        if rewritten:
            problems.append(
                "existing Value definitions must stay exactly as they are; restore the regex and options of: "
                + ", ".join(f"{name} {in_force[name][0]}" for name in rewritten)
            )
        if lost:
            problems.append(
                "existing rules must stay exactly as they are, in their state and in the same order; only add "
                "lines, and end an added line with '-> Continue' when an existing rule must still see the same "
                "input line. Put these back unchanged: " + " | ".join(lost[:12])
            )
        if not problems:
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


def drop_values(template_text: str, names: list[str]) -> str:
    """Take Values out of a template again, with every rule still matching the lines it matched.

    Where a rule captured one of them the capture becomes a plain group, so what
    the rule consumes does not change. A rule left capturing nothing that only
    continues does nothing at all and goes too.
    """

    definitions = value_definitions(template_text)
    plain = {}
    for name in names:
        if name in definitions:
            regex = definitions[name][0]
            plain[name] = "(?:" + regex[1:] if regex.startswith("(") and not regex.startswith("(?") else f"(?:{regex})"
    lines = []
    for line in template_text.splitlines():
        match = _VALUE_LINE.match(line)
        if match and match.group(1) in names:
            continue
        if line.startswith((" ", "\t")):
            rule = line
            for name, group in plain.items():
                rule = rule.replace("${" + name + "}", group)
            if rule != line and "${" not in rule and rule.rstrip().endswith("-> Continue"):
                continue
            line = rule
        lines.append(line)
    return "\n".join(lines) + "\n"


def field_sources(template_text: str, fields: list[str], text: str, limit: int = 2) -> dict[str, list[dict[str, str]]]:
    """For each field, output lines a rule read it from, with the captured part and the line above.

    A value that parses is not thereby the right value: a speed level reads as
    well as a speed. The line it came from is what lets a person tell them
    apart, and in tabular output the line above is the column heading.
    """

    try:
        rules = [rule for state in textfsm.TextFSM(io.StringIO(template_text)).states.values() for rule in state]
    except textfsm.TextFSMTemplateError:
        return {name: [] for name in fields}
    found: dict[str, list[dict[str, str]]] = {name: [] for name in fields}
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if all(len(found[name]) >= limit for name in fields):
            break
        for rule in rules:
            match = rule.regex_obj.match(line)
            if not match:
                continue
            for name in fields:
                value = match.groupdict().get(name)
                if not value or len(found[name]) >= limit or any(item["line"] == line.rstrip() for item in found[name]):
                    continue
                above = next(
                    (item.rstrip() for item in reversed(lines[:index]) if item.strip() and set(item.strip()) - set("-=+| ")),
                    "",
                )
                found[name].append({"value": str(value), "line": line.rstrip(), "above": above})
    return found


def check_extension(
    backend: Any, current_text: str, candidate_text: str, texts: dict[str, str], fields: list[str]
) -> dict[str, Any]:
    """Everything about an extended template that can be decided without a model.

    The trial reports it and the save decides on it again, on the outputs as
    they are stored at that moment: nothing is taken on trust from the trial.
    """

    try:
        candidate = value_definitions(candidate_text)
    except textfsm.TextFSMTemplateError as exc:
        raise AdapterFieldsError(f"补充后的解析模板无法编译：{exc}") from exc
    in_force = value_definitions(current_text)
    replayed = replay(backend, current_text, candidate_text, texts, fields)
    return {
        **replayed,
        "rewritten": sorted(name for name, definition in in_force.items() if candidate.get(name) != definition),
        "rules_lost": rules_lost(current_text, candidate_text),
        "undeclared": sorted(set(candidate) - set(in_force) - set(fields)),
    }


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
                # Distinct values say more about a field than the first three records.
                found[name]["examples"] = list(dict.fromkeys([*found[name]["examples"], *map(str, values)]))[:3]
    return {"record_count": records, "found": found, "differences": differences}


def extension_context(implementation_id: str, check_item_id: str) -> dict[str, Any]:
    """The adapter in force, the template it reads for this check item, and the outputs it has parsed."""

    catalog = CheckItemCatalogRepository().load_active()
    adapter = next((row for row in catalog.implementations if row.id == implementation_id), None)
    item = catalog.check_item(check_item_id)
    if adapter is None or item is None or item.id not in adapter.check_item_ids:
        raise AdapterFieldsError("适配器或检查项不存在，或两者没有关联")
    projection = _projection(adapter, item.id)
    template = ParserTemplateRepository().get_template(
        str(dict(adapter.adapter_spec.get("parser") or {}).get("template_id") or "")
    )
    if template is None:
        raise AdapterFieldsError("找不到这个适配器的解析模板")
    record_set = str(projection.get("source_record_set") or "")
    outputs = stored_outputs(adapter.id)
    return {
        "adapter": adapter,
        "item": item,
        "template": template,
        "record_set": record_set,
        "current_text": record_set_template(str(template.template_text), record_set),
        "outputs": {output["id"]: output["text"] for output in outputs},
        "provided": sorted(dict(projection.get("projection") or {}).get("fields") or {}),
        "base": {
            "implementation_version_id": str(adapter.version_id),
            "check_item_version_id": str(item.version_id),
            "template_id": str(template.id),
            "template_sha256": str(template.sha256),
            "record_set": record_set,
        },
    }


def _why_not(outcome: Any, detail: str) -> str:
    """Why the model's work was not taken, in words an engineer can act on."""

    stopped = str(outcome.stop_reason or "")
    if any(word in stopped for word in ("timeout", "provider", "deadline")):
        return "AI 服务没有响应，请稍后再试。"
    if "rules must stay" in detail or "Value definitions must stay" in detail or "already provided changed" in detail:
        return (
            f"AI 试了 {outcome.model_calls} 次，每次都改动了已有的解析规则，结果没有采用。"
            "可以减少一次要补的字段再试。"
        )
    return f"AI 试了 {outcome.model_calls} 次，没能补上这些字段。可以减少一次要补的字段再试。"


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

    context = extension_context(implementation_id, check_item_id)
    adapter, item, template = context["adapter"], context["item"], context["template"]
    current_text, raw = context["current_text"], context["outputs"]
    missing = missing_fields(adapter, item)
    wanted = [name for name in (fields or missing) if name in missing]
    if not wanted:
        raise AdapterFieldsError("这个适配器已经提供了所选字段")
    if not raw:
        raise AdapterFieldsError("这个适配器还没有在巡检中解析过任何输出，没有样本可用来验证")
    rows = {row["canonical_field"]: row for row in item.canonical_contract.get("observations") or []}

    backend = TextFSMValidationBackend()
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
    in_force = sum(len(rules) for rules in state_rules(current_text).values())
    result: dict[str, Any] = {
        "implementation_id": adapter.id,
        "check_item_id": item.id,
        "template_id": str(template.id),
        "base": context["base"],
        "requested_fields": wanted,
        "sample_count": len(raw),
        "existing_field_count": len(context["provided"]),
        "rules_in_force": in_force,
        "model_calls": 0,
    }
    nothing = {"fields": [], "provided_fields": [], "absent_fields": [], "regression": None, "template_text": None}
    current_records = [record for text in sanitized.values() for record in backend.parse(current_text, text)]
    spec = base_spec(item, current_text, current_records, backend)
    # The model is asked for the new fields only. If the template in force does not
    # pass as it is, it would start repairing things that are in production.
    untouched = validate_template_runtime_compatibility(current_text, spec, samples, backend)
    if not untouched.passed:
        return {**result, **nothing, "status": "unsupported",
                "reason": "这个适配器现有的解析模板在它自己的输出上没有通过校验，不交给 AI 修改。",
                "detail": str(untouched.primary_diagnostic)}

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
        model=_KeepsValues(model, current_text),
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
        detail = str(final.primary_diagnostic if final is not None and not final.passed else outcome.stop_reason)
        return {**result, **nothing, "status": "failed", "reason": _why_not(outcome, detail), "detail": detail}

    # Judged on the outputs as the devices printed them, not on the sanitized copies the model saw.
    final_text = candidate.template_text
    checked = check_extension(backend, current_text, final_text, raw, wanted)
    found = checked["found"]
    provided = [name for name in wanted if found[name]["records"]]
    absent = [name for name in wanted if not found[name]["records"]]
    if provided and absent and not checked["differences"]:
        # A field the outputs never show is not something this device provides; it stays out of the template.
        trimmed = drop_values(final_text, absent)
        again = check_extension(backend, current_text, trimmed, raw, provided)
        if not (again["differences"] or again["rewritten"] or again["rules_lost"]):
            final_text, checked = trimmed, again
    differences = [
        *checked["differences"],
        *(f"已有字段 {name} 的定义被改动" for name in checked["rewritten"]),
        *(f"已有规则被改动：{rule}" for rule in checked["rules_lost"]),
    ]
    sources: dict[str, list[dict[str, str]]] = {name: [] for name in provided}
    for text in raw.values():
        for name, lines in field_sources(final_text, provided, text).items():
            sources[name] = [*sources[name], *lines][:2]
        if all(len(lines) >= 2 for lines in sources.values()):
            break
    added = added_lines(current_text, final_text) if provided and not differences else []
    return {
        **result,
        "status": "regressed" if differences else "ready" if provided else "no_data",
        "record_count": checked["record_count"],
        "fields": [
            {
                "name": name,
                "description": str(rows[name].get("description") or ""),
                **found[name],
                "sources": sources.get(name, []),
            }
            for name in wanted
        ],
        "provided_fields": provided,
        "absent_fields": absent,
        "regression": {"passed": not differences, "differences": differences[:10]},
        "rules_added": sum(1 for line in added if not line.startswith("Value ")),
        "added_lines": added,
        "template_text": final_text if provided and not differences else None,
        **({"reason": "补上字段的同时，已有字段的解析结果变了，所以没有采用。"} if differences else {}),
    }


__all__ = [
    "AdapterFieldsError", "added_lines", "base_spec", "check_extension", "drop_values", "extension_context",
    "extension_only", "field_sources", "missing_fields", "record_set_template", "replay", "restore_values",
    "rules_lost", "state_rules", "stored_outputs", "trial_add_fields", "value_definitions",
]

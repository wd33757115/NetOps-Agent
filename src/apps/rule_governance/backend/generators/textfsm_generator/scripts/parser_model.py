"""Bounded LLM adapter for the Simple Parser pipeline."""

from __future__ import annotations

import re

from src.apps.rule_governance.backend.generators.textfsm_generator.scripts.llm_generator import (
    ParserGenerationToolbox,
    ProviderTextFSMToolSession,
    build_parser_bundle_prompt_bundle,
    build_prompt_bundle,
    create_textfsm_tool_session,
)

from src.apps.rule_governance.backend.parser.parser_bundle import build_parser_bundle, parse_parser_bundle
from src.apps.rule_governance.backend.parser.structure_profiler import profile_sample_set
from src.apps.rule_governance.backend.parser_generation import ParserSpec, ParserSpecDocument, TemplateCandidate
from src.apps.rule_governance.backend.parser_generation.samples import ParserSample, SampleRole
from src.apps.rule_governance.backend.parser_generation.service import (
    PARSER_TOOL_PROTOCOL_VERSION,
    ParserModelTurn,
)


def strip_model_template(raw: str) -> str:
    text = str(raw or "").strip()
    fence = re.search(r"```(?:textfsm|text|)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    output: list[str] = []
    started = False
    in_states = False
    for line in text.splitlines():
        stripped = line.strip()
        if not started and (stripped.startswith("Value ") or stripped == "Start"):
            started = True
        if not started or stripped.startswith("#"):
            continue
        is_state = bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", stripped))
        if is_state:
            if output and output[-1] != "":
                output.append("")
            output.append(stripped)
            in_states = True
        elif in_states and stripped:
            output.append(f"  {stripped}" if stripped.startswith("^") else line)
        elif not in_states:
            output.append(line)
    cleaned = "\n".join(output).strip() if output else text.strip()
    return f"{cleaned}\n"


def canonicalize_model_fields(
    template_text: str,
    contract_fields: tuple[str, ...],
) -> str:
    canonical = {field.casefold(): field for field in contract_fields}
    normalized: dict[str, str | None] = {}
    for field in contract_fields:
        key = re.sub(r"[^a-z0-9]+", "", field.casefold())
        if key in normalized and normalized[key] != field:
            normalized[key] = None
        else:
            normalized[key] = field
    replacements: dict[str, str] = {}
    declaration = re.compile(
        r"^(?P<indent>\s*)Value(?P<options>\s+[A-Za-z,]+)?\s+"
        r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
        r"(?P<suffix>\s+\(.*)$"
    )
    lines: list[str] = []
    for line in template_text.splitlines():
        match = declaration.match(line)
        if match:
            declared = match.group("name")
            normalized_name = re.sub(r"[^a-z0-9]+", "", declared.casefold())
            target = canonical.get(declared.casefold()) or normalized.get(normalized_name)
            if target and target != declared:
                replacements[declared] = target
            options = [
                option.strip()
                for option in str(match.group("options") or "").split(",")
                if option.strip() and option.strip().casefold() != "required"
            ]
            line = (
                f"{match.group('indent')}Value"
                f"{' ' + ','.join(options) if options else ''} "
                f"{target or declared}{match.group('suffix')}"
            )
        lines.append(line)
    normalized = "\n".join(lines)
    for declared, target in replacements.items():
        normalized = re.sub(
            rf"\$\{{{re.escape(declared)}\}}",
            "${" + target + "}",
            normalized,
        )
    return f"{normalized.rstrip()}\n"


def _render_samples(samples: tuple[str, ...]) -> str:
    return "\n\n".join(
        f'<CLI_SAMPLE id="generation-{index}">\n{text}\n</CLI_SAMPLE>'
        for index, text in enumerate(samples, start=1)
    )


class TextFSMGenerationAgent:
    """Opens a provider-neutral evidence agent for one frozen ParserSpec."""

    def __init__(
        self,
        *,
        vendor: str,
        model: str,
        command: str,
        validation_samples: tuple[ParserSample, ...] = (),
    ) -> None:
        self.vendor = vendor
        self.model = model
        self.command = command
        self.validation_samples = validation_samples

    @staticmethod
    def _field_bindings(spec: ParserSpec) -> dict[str, list[dict[str, str]]]:
        return {
            field.name: [{"source_label": label} for label in field.source_labels]
            for field in spec.fields
            if field.source_labels
        }

    def open_session(
        self,
        spec: ParserSpec | ParserSpecDocument,
        samples: tuple[str, ...],
    ) -> TextFSMParserModelSession:
        rendered = _render_samples(samples)
        parse_plan = profile_sample_set(
            [(f"generation-{index}", text) for index, text in enumerate(samples, start=1)]
        )
        if isinstance(spec, ParserSpecDocument):
            bundle = build_parser_bundle_prompt_bundle(
                vendor=self.vendor,
                model=self.model,
                command=self.command,
                cli_output=rendered,
                parser_spec=spec.to_dict(),
                parse_plan=parse_plan,
            )
            return TextFSMParserModelSession(
                session=create_textfsm_tool_session(
                    bundle,
                    bundle_mode=True,
                    toolbox=ParserGenerationToolbox(
                        spec=spec,
                        samples=self._validation_samples(samples),
                        vendor=self.vendor,
                        command=self.command,
                    ),
                ),
                record_set_fields={
                    record_set.record_set_name: (
                        *record_set.required_fields,
                        *record_set.optional_fields,
                    )
                    for record_set in spec.record_sets
                },
            )
        bundle = build_prompt_bundle(
            vendor=self.vendor,
            model=self.model,
            command=self.command,
            cli_output=rendered,
            required_fields=list(spec.required_fields),
            optional_fields=list(spec.optional_fields),
            primary_keys=list(spec.primary_keys),
            validators=spec.validators,
            parse_plan=parse_plan,
            field_bindings=self._field_bindings(spec),
            allow_empty=spec.allow_empty,
            empty_patterns=list(spec.empty_patterns),
        )
        return TextFSMParserModelSession(
            session=create_textfsm_tool_session(
                bundle,
                toolbox=ParserGenerationToolbox(
                    spec=spec,
                    samples=self._validation_samples(samples),
                    vendor=self.vendor,
                    command=self.command,
                ),
            ),
            contract_fields=(*spec.required_fields, *spec.optional_fields),
        )

    def _validation_samples(self, prompt_samples: tuple[str, ...]) -> tuple[ParserSample, ...]:
        if self.validation_samples:
            return self.validation_samples
        return tuple(
            ParserSample(
                sample_id=f"generation-{index}",
                raw_text=text,
                validation_text_override=text,
                role=SampleRole.GENERATION,
            )
            for index, text in enumerate(prompt_samples, start=1)
        )


class TextFSMParserModelSession:
    def __init__(
        self,
        *,
        session: ProviderTextFSMToolSession,
        contract_fields: tuple[str, ...] = (),
        record_set_fields: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.session = session
        self.contract_fields = contract_fields
        self.record_set_fields = dict(record_set_fields or {})

    def next(
        self,
        validation_feedback: dict[str, object] | None = None,
    ) -> ParserModelTurn:
        turn = self.session.next(validation_feedback)
        if turn.candidate_text is None:
            return ParserModelTurn(
                candidate=None,
                submission_mode=turn.submission_mode,
                review_reason=turn.review_reason,
                protocol_error=turn.protocol_error,
                model_name=turn.model_name,
                token_usage=turn.token_usage or None,
                latency_ms=turn.latency_ms,
                protocol_version=PARSER_TOOL_PROTOCOL_VERSION,
                retryable=turn.retryable,
            )
        if self.record_set_fields:
            bundle = parse_parser_bundle(turn.candidate_text)
            if bundle is None:
                return ParserModelTurn(
                    candidate=None,
                    submission_mode="tool_protocol_error",
                    protocol_error="model returned an invalid Parser bundle",
                    model_name=turn.model_name,
                    token_usage=turn.token_usage or None,
                    latency_ms=turn.latency_ms,
                    protocol_version=PARSER_TOOL_PROTOCOL_VERSION,
                )
            templates = {
                item.name: canonicalize_model_fields(
                    strip_model_template(item.template_text),
                    self.record_set_fields.get(item.name, ()),
                )
                for item in bundle.record_sets
            }
            if set(templates) != set(self.record_set_fields):
                return ParserModelTurn(
                    candidate=None,
                    submission_mode="tool_protocol_error",
                    protocol_error=("Parser bundle record sets do not match the frozen ParserSpec"),
                    model_name=turn.model_name,
                    token_usage=turn.token_usage or None,
                    latency_ms=turn.latency_ms,
                    protocol_version=PARSER_TOOL_PROTOCOL_VERSION,
                )
            template = build_parser_bundle(templates).serialize()
        else:
            template = canonicalize_model_fields(
                strip_model_template(turn.candidate_text),
                self.contract_fields,
            )
        candidate = TemplateCandidate(
            template_text=template,
            provider=f"llm:{turn.submission_mode}",
            model_name=turn.model_name,
            token_usage=turn.token_usage or None,
            latency_ms=turn.latency_ms,
            protocol_version=PARSER_TOOL_PROTOCOL_VERSION,
        )
        return ParserModelTurn(
            candidate=candidate,
            submission_mode=turn.submission_mode,
            model_name=turn.model_name,
            token_usage=turn.token_usage or None,
            latency_ms=turn.latency_ms,
            protocol_version=PARSER_TOOL_PROTOCOL_VERSION,
            retryable=turn.retryable,
        )


# Existing script entry points import this name.  Preserve it until all callers
# have moved, while the implementation itself is provider-neutral.
TextFSMParserModel = TextFSMGenerationAgent

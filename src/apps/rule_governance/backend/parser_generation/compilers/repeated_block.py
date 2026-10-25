"""Deterministic compiler for repeated key/value entity blocks."""

from __future__ import annotations

import re

from src.apps.rule_governance.backend.parser.evidence_line import (
    EvidenceCapture,
    compile_evidence_line,
)
from src.apps.rule_governance.backend.parser.textfsm_input import TEXTFSM_END_OF_INPUT

from ..samples import ParserSample
from ..service import TemplateCandidate
from ..spec import ParserSpec
from .common import (
    field_label_map,
    literal_line_pattern,
    normalize_label,
    value_declarations,
    value_pattern,
)


class RepeatedBlockCompiler:
    name = "repeated_block"

    @staticmethod
    def _label_tokens(value: str) -> set[str]:
        return {
            {"alarms": "alarm", "warnings": "warning"}.get(token, token)
            for token in re.split(r"[^a-z0-9]+", str(value or "").casefold())
            if token
        }

    @staticmethod
    def _numeric_family_pattern(values: set[str]) -> str:
        first = min(values)
        parts = re.split(r"(\d+)", first)
        candidate = "".join(r"\d+" if part.isdigit() else re.escape(part) for part in parts)
        if values and all(re.fullmatch(candidate, value) for value in values):
            return candidate
        return "(?:" + "|".join(re.escape(value) for value in sorted(values)) + ")"

    def _grouped_measurements(
        self,
        spec: ParserSpec,
        samples: tuple[ParserSample, ...],
    ) -> list[tuple[str, str, tuple[str | None, ...]]]:
        metric_rows: dict[str, tuple[str, str]] = {}
        pattern = re.compile(
            r"^\s*([A-Za-z][A-Za-z ]{1,30}?)\s+"
            r"[-+]?\d+(?:\.\d+)?\s*([A-Za-z%]+)\s+"
            r"[-+]?\d+(?:\.\d+)?\s*[A-Za-z%]+\s+"
            r"[-+]?\d+(?:\.\d+)?\s*[A-Za-z%]+\s+"
            r"[-+]?\d+(?:\.\d+)?\s*[A-Za-z%]+\s+"
            r"[-+]?\d+(?:\.\d+)?\s*[A-Za-z%]+\s*$",
            re.MULTILINE,
        )
        for sample in samples:
            for match in pattern.finditer(sample.validation_text):
                raw_label = re.sub(r"\s+", " ", match.group(1).strip())
                metric_rows.setdefault(
                    normalize_label(raw_label),
                    (raw_label, match.group(2)),
                )
        output: list[tuple[str, str, tuple[str | None, ...]]] = []
        for _normalized, (raw_label, unit) in metric_rows.items():
            metric_tokens = self._label_tokens(raw_label)
            positions: list[tuple[str, ...]] = [
                (),
                ("alarm", "high"),
                ("alarm", "low"),
                ("warning", "high"),
                ("warning", "low"),
            ]
            fields: list[str | None] = []
            for qualifiers in positions:
                required_tokens = metric_tokens | set(qualifiers)
                field_name = next(
                    (
                        field.name
                        for field in spec.fields
                        if field.value_type in {"number", "percent"}
                        and any(
                            required_tokens.issubset(self._label_tokens(label))
                            and (
                                qualifiers
                                or self._label_tokens(label) == metric_tokens
                            )
                            for label in field.source_labels
                        )
                    ),
                    None,
                )
                fields.append(field_name)
            if fields[0] and any(fields[1:]):
                output.append((raw_label, unit, tuple(fields)))
        return output

    def _compile_nested_records(
        self,
        spec: ParserSpec,
        generation_samples: tuple[ParserSample, ...],
    ) -> TemplateCandidate | None:
        if len(spec.primary_keys) != 2:
            return None
        expectations = [
            sample.expectation
            for sample in generation_samples
            if sample.expectation and sample.expectation.primary_keys
        ]
        if len(expectations) != len(generation_samples):
            return None
        expected_keys = [
            key
            for expectation in expectations
            for key in expectation.primary_keys
            if len(key) == 2
        ]
        if not expected_keys:
            return None
        parent_key, child_key = spec.primary_keys
        parent_values = {key[0] for key in expected_keys}
        repeated_parents = {
            parent
            for parent in parent_values
            if sum(key[0] == parent for key in expected_keys) >= 2
        }
        if not repeated_parents:
            return None
        parent_pattern = self._numeric_family_pattern(parent_values)
        if any(
            len(
                [
                    line
                    for line in sample.validation_text.splitlines()
                    if re.fullmatch(rf"\s*{parent_pattern}\s*", line)
                ]
            )
            < 2
            for sample in generation_samples
        ):
            return None

        child_field = spec.field_map[child_key]
        inline_label = next(
            (
                str(label).strip().rstrip(":")
                for label in child_field.source_labels
                if str(label).strip().endswith(":")
            ),
            None,
        )
        if not inline_label:
            return None
        inline_literal = r"\s+".join(
            re.escape(token) for token in inline_label.split()
        )
        inline_rule = (
            rf"  ^\s*{inline_literal}:\s*${{{child_key}}}(?:\s+.*)?$$"
        )

        grouped_rows = self._grouped_measurements(spec, generation_samples)
        if not grouped_rows:
            return None
        included_fields = {parent_key, child_key}
        measurement_rules: list[str] = []
        for raw_label, unit, fields in grouped_rows:
            parts = [
                f"${{{field_name}}}"
                if field_name
                else r"[-+]?\d+(?:\.\d+)?"
                for field_name in fields
            ]
            included_fields.update(field_name for field_name in fields if field_name)
            value_columns = "".join(
                (
                    ("" if index == 0 else r"\s+")
                    + value
                    + rf"\s+{re.escape(unit)}"
                    + r"(?:\s+(?:\+\+?|--?))?"
                )
                for index, value in enumerate(parts)
            )
            row_literal = r"\s+".join(
                re.escape(token) for token in raw_label.split()
            )
            measurement_rules.append(
                rf"  ^\s*{row_literal}\s+{value_columns}\s*$$"
            )

        child_values = {value.casefold() for value in child_field.values}
        marker_labels = list(child_field.source_labels)
        marker_labels.extend(
            label
            for field in spec.fields
            if field.name not in spec.primary_keys
            for label in field.source_labels
            if str(label).strip().split(" ", 1)[0].casefold() in child_values
        )
        marker_rules: list[str] = []
        required_filldown = {parent_key}
        for raw_label in dict.fromkeys(str(label).strip() for label in marker_labels):
            if not raw_label or raw_label.endswith(":"):
                continue
            first_token, separator, remainder = raw_label.partition(" ")
            if not separator or first_token.casefold() not in child_values:
                continue
            associated = next(
                (
                    field
                    for field in spec.fields
                    if field.name not in spec.primary_keys
                    and any(
                        normalize_label(label) == normalize_label(raw_label)
                        for label in field.source_labels
                    )
                ),
                None,
            )
            marker_guard = rf"(?=(?i:{re.escape(first_token)})\s)"
            if associated and re.search(r"\bis\b", remainder, re.IGNORECASE):
                prefix, _is_token, _tail = remainder.partition("is")
                if not prefix.strip():
                    marker_rules.append(
                        rf"  ^\s*{marker_guard}${{{child_key}}}\s+is\s+"
                        rf"${{{associated.name}}}\s*$$"
                    )
                    included_fields.add(associated.name)
                    if associated.required:
                        required_filldown.add(associated.name)
                    continue
            remainder_literal = r"\s+".join(
                re.escape(token) for token in remainder.split()
            )
            marker_rules.append(
                rf"  ^\s*{marker_guard}${{{child_key}}}\s+"
                rf"{remainder_literal}\s*$$"
            )

        simple_rules: list[str] = []
        for field in spec.fields:
            if field.name in included_fields:
                continue
            label = next(
                (
                    str(item).strip()
                    for item in field.source_labels
                    if str(item).strip().endswith("=")
                ),
                None,
            )
            if not label:
                continue
            prefix = r"\s+".join(
                re.escape(token) for token in label.rstrip("=").split()
            )
            simple_rules.append(
                rf"  ^\s*{prefix}\s*=\s*${{{field.name}}}\s*$$"
            )
            included_fields.add(field.name)

        ordered_fields = [
            field.name for field in spec.fields if field.name in included_fields
        ]
        declarations = []
        for field_name in ordered_fields:
            filldown = " Filldown" if field_name in required_filldown else ""
            pattern = (
                parent_pattern
                if field_name == parent_key
                else value_pattern(spec.field_map[field_name])
            )
            declarations.append(f"Value{filldown} {field_name} ({pattern})")

        end_rule = rf"  ^{TEXTFSM_END_OF_INPUT}$$ -> Record EOF"
        parent_boundary = rf"  ^{parent_pattern}\s*$$ -> Continue.Record"
        parent_capture = rf"  ^${{{parent_key}}}\s*$$ -> Block"
        block_rules = [
            end_rule,
            parent_boundary,
            parent_capture,
            f"{inline_rule} -> Lane",
            *marker_rules,
            *measurement_rules,
            *simple_rules,
        ]
        lane_rules = [
            end_rule,
            parent_boundary,
            parent_capture,
            rf"  ^\s*{inline_literal}:\s*\S+(?:\s+.*)?$$ -> Continue.Record",
            f"{inline_rule} -> Lane",
            *measurement_rules,
            *simple_rules,
        ]
        template = (
            "\n".join(declarations)
            + f"\n\nStart\n  ^${{{parent_key}}}\\s*$$ -> Block\n\nBlock\n"
            + "\n".join(block_rules)
            + "\n\nLane\n"
            + "\n".join(lane_rules)
            + "\n\nEOF\n"
        )
        return TemplateCandidate(
            template,
            "deterministic:repeated_block:nested_records",
        )

    @staticmethod
    def _context_bindings(spec: ParserSpec) -> dict[tuple[str, ...], str]:
        bindings: dict[tuple[str, ...], str] = {}
        for field in spec.fields:
            for label in field.source_labels:
                path = tuple(
                    normalize_label(item)
                    for item in str(label).split("/")
                    if normalize_label(item)
                )
                if len(path) >= 2:
                    bindings[path] = field.name
        return bindings

    @staticmethod
    def _column_labels(line: str) -> list[str]:
        return [item.strip() for item in re.split(r"\s{2,}", line.strip()) if item.strip()]

    def _compile_sectioned_table(
        self,
        spec: ParserSpec,
        generation_samples: tuple[ParserSample, ...],
    ) -> TemplateCandidate | None:
        if len(spec.primary_keys) != 1:
            return None
        bindings = self._context_bindings(spec)
        if not bindings:
            return None
        key = spec.primary_keys[0]
        first_lines = [line for line in generation_samples[0].raw_text.splitlines() if line.strip()]
        anchor_candidates: list[tuple[str, str]] = []
        for line in first_lines:
            match = re.match(r"^\s*(\S+)\s+(.{3,100}?):\s*$", line)
            if match:
                anchor_candidates.append((match.group(1), match.group(2)))
        suffix_counts: dict[str, int] = {}
        for _value, suffix in anchor_candidates:
            suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
        anchor_suffix = next(
            (suffix for suffix, count in suffix_counts.items() if count >= 2),
            None,
        )
        if not anchor_suffix:
            return None
        for sample in generation_samples:
            anchors = [
                line
                for line in sample.validation_text.splitlines()
                if re.match(rf"^\s*\S+\s+{re.escape(anchor_suffix)}:\s*$", line)
            ]
            if not anchors:
                return None

        section_names = list(
            dict.fromkeys(
                path[0]
                for path in bindings
                if not (
                    len(path) == 2
                    and spec.field_map[bindings[path]].value_type == "enum"
                )
            )
        )
        section_lines: dict[str, str] = {}
        section_rules: dict[str, list[str]] = {}
        captured_fields = {key}
        for section_name in section_names:
            section_index = next(
                (
                    index
                    for index, line in enumerate(first_lines)
                    if normalize_label(line.rstrip(":")) == section_name
                ),
                None,
            )
            if section_index is None:
                continue
            section_line = first_lines[section_index]
            section_lines[section_name] = section_line
            leaves = {
                path[-1]: field
                for path, field in bindings.items()
                if path[0] == section_name
            }
            header_index = next(
                (
                    index
                    for index in range(section_index + 1, min(len(first_lines), section_index + 8))
                    if len(
                        set(
                            normalize_label(label)
                            for label in self._column_labels(first_lines[index])
                        )
                        .intersection(leaves)
                    )
                    >= 1
                ),
                None,
            )
            if header_index is None:
                continue
            columns = [
                normalize_label(label)
                for label in self._column_labels(first_lines[header_index])
            ]
            row_labels = list(
                dict.fromkeys(
                    path[1]
                    for path in bindings
                    if len(path) >= 3 and path[0] == section_name
                )
            )
            if row_labels:
                for row_label in row_labels:
                    fields = [bindings.get((section_name, row_label, column)) for column in columns]
                    if not any(fields):
                        continue
                    captured_fields.update(field for field in fields if field)
                    raw_row_label = next(
                        (
                            match.group(1)
                            for line in first_lines[header_index + 1 : header_index + 8]
                            if (match := re.match(r"^\s*(\S+)", line))
                            and normalize_label(match.group(1)) == row_label
                        ),
                        row_label,
                    )
                    parts = [rf"{re.escape(raw_row_label)}"]
                    parts.extend(f"${{{field}}}" if field else r"\S+" for field in fields)
                    section_rules.setdefault(section_name, []).append(
                        r"  ^\s*" + r"\s+".join(parts) + r"\s*$$"
                    )
            else:
                fields = [bindings.get((section_name, column)) for column in columns]
                if any(fields):
                    captured_fields.update(field for field in fields if field)
                    parts = [f"${{{field}}}" if field else r"\S+" for field in fields]
                    section_rules.setdefault(section_name, []).append(
                        r"  ^\s*" + r"\s+".join(parts) + r"\s*$$"
                    )

        enum_rules: list[str] = []
        for path, field_name in bindings.items():
            field = spec.field_map[field_name]
            if len(path) != 2 or field.value_type != "enum":
                continue
            prefix = next(
                (
                    str(label).split("/", 1)[0].strip()
                    for label in field.source_labels
                    if normalize_label(str(label).split("/", 1)[0]) == path[0]
                ),
                "",
            )
            if not prefix:
                continue
            enum_rules.append(
                rf"  ^\s*{re.escape(prefix)}\s+${{{field_name}}}[.]?\s*$$"
            )
            captured_fields.add(field_name)

        if not section_rules and not enum_rules:
            return None
        ordered_fields = [field.name for field in spec.fields if field.name in captured_fields]
        start_anchor = rf"  ^\s*${{{key}}}\s+{re.escape(anchor_suffix)}:\s*$$ -> Block"
        boundary = rf"  ^\s*\S+\s+{re.escape(anchor_suffix)}:\s*$$ -> Continue.Record"
        boundary_capture = (
            rf"  ^\s*${{{key}}}\s+{re.escape(anchor_suffix)}:\s*$$ -> Block"
        )
        end_rule = rf"  ^{TEXTFSM_END_OF_INPUT}$$ -> Record EOF"
        transitions = [
            f"  {literal_line_pattern(line)} -> Section{index}"
            for index, line in enumerate(section_lines.values(), start=1)
        ]
        states: list[str] = [
            "Block",
            end_rule,
            boundary,
            boundary_capture,
            *enum_rules,
            *transitions,
            "",
        ]
        for index, section_name in enumerate(section_lines, start=1):
            states.extend(
                [
                    f"Section{index}",
                    end_rule,
                    boundary,
                    boundary_capture,
                    *enum_rules,
                    *transitions,
                    *section_rules.get(section_name, []),
                    "",
                ]
            )
        template = (
            f"{value_declarations(spec, ordered_fields)}\n\nStart\n{start_anchor}\n\n"
            + "\n".join(states)
            + "\nEOF\n"
        )
        return TemplateCandidate(template, "deterministic:repeated_block:sectioned_table")

    @staticmethod
    def _label_pattern(value: str) -> str:
        return r"\s+".join(re.escape(token) for token in str(value).split())

    @classmethod
    def _anchor_line_for_key(
        cls,
        spec: ParserSpec,
        raw_text: str,
        key_values: tuple[str, ...],
    ) -> str | None:
        if len(key_values) != len(spec.primary_keys):
            return None
        for line in raw_text.splitlines():
            cursor = 0
            matched = True
            for field_name, raw_value in zip(spec.primary_keys, key_values):
                field = spec.field_map[field_name]
                labels = tuple(field.source_labels) or (
                    field_name.replace("_", " "),
                )
                match = next(
                    (
                        candidate
                        for label in labels
                        if (
                            candidate := re.search(
                                rf"{cls._label_pattern(label)}\s+"
                                rf"{re.escape(str(raw_value))}(?=\s|:|$)",
                                line[cursor:],
                                re.IGNORECASE,
                            )
                        )
                    ),
                    None,
                )
                if match is None:
                    matched = False
                    break
                cursor += match.end()
            if matched:
                return line
        return None

    @staticmethod
    def _python_rule(pattern: str, spec: ParserSpec) -> re.Pattern[str]:
        rendered = pattern.replace("$$", "$")
        for field_name in spec.primary_keys:
            rendered = rendered.replace(
                f"${{{field_name}}}",
                f"(?P<{field_name}>{value_pattern(spec.field_map[field_name])})",
            )
        return re.compile(rendered)

    @classmethod
    def _metric_rule(
        cls,
        field_name: str,
        spec: ParserSpec,
        lines: list[str],
    ) -> str | None:
        field = spec.field_map[field_name]
        for label in field.source_labels:
            label_matcher = re.compile(
                cls._label_pattern(label),
                re.IGNORECASE,
            )
            for line in lines:
                label_match = label_matcher.search(line)
                if label_match is None:
                    continue
                numeric = [
                    match
                    for match in re.finditer(
                        r"[-+]?\d+(?:\.\d+)?%?",
                        line,
                    )
                    if match.end() <= label_match.start()
                ]
                if not numeric:
                    continue
                value_match = numeric[-1]
                return compile_evidence_line(
                    line,
                    (
                        EvidenceCapture(
                            literal=value_match.group(0),
                            pattern=value_pattern(field),
                            field=field_name,
                        ),
                    ),
                )
        return None

    def _compile_labeled_metric_blocks(
        self,
        spec: ParserSpec,
        generation_samples: tuple[ParserSample, ...],
    ) -> TemplateCandidate | None:
        """Compile repeated headers followed by labeled scalar measurements."""

        if not spec.primary_keys or not generation_samples:
            return None
        expectations = [
            sample.expectation
            for sample in generation_samples
            if sample.expectation and sample.expectation.primary_keys
        ]
        if len(expectations) != len(generation_samples):
            return None
        first_key = min(
            tuple(str(item) for item in key)
            for key in expectations[0].primary_keys
        )
        anchor_line = self._anchor_line_for_key(
            spec,
            generation_samples[0].raw_text,
            first_key,
        )
        if anchor_line is None:
            return None
        anchor_rule = compile_evidence_line(
            anchor_line,
            tuple(
                EvidenceCapture(
                    literal=value,
                    pattern=value_pattern(spec.field_map[field_name]),
                    field=field_name,
                )
                for field_name, value in zip(spec.primary_keys, first_key)
            ),
        )
        try:
            anchor_matcher = self._python_rule(anchor_rule, spec)
        except re.error:
            return None
        for sample, expectation in zip(generation_samples, expectations):
            actual_keys = {
                tuple(str(match.group(field_name)) for field_name in spec.primary_keys)
                for line in sample.validation_text.splitlines()
                if (match := anchor_matcher.fullmatch(line))
            }
            expected_keys = {
                tuple(str(item) for item in key)
                for key in expectation.primary_keys
            }
            if actual_keys != expected_keys:
                return None

        first_lines = generation_samples[0].raw_text.splitlines()
        metric_rules: list[tuple[str, str]] = []
        for field in spec.fields:
            if field.name in spec.primary_keys or not field.source_labels:
                continue
            rule = self._metric_rule(field.name, spec, first_lines)
            if rule is None:
                continue
            matcher = re.compile(
                rule.replace("$$", "$").replace(
                    f"${{{field.name}}}",
                    f"(?:{value_pattern(field)})",
                )
            )
            minimum_matches = (
                len(expectations[0].primary_keys) if field.required else 1
            )
            if any(
                sum(
                    bool(matcher.fullmatch(line))
                    for line in sample.validation_text.splitlines()
                )
                < (
                    len(expectation.primary_keys)
                    if field.required
                    else minimum_matches
                )
                for sample, expectation in zip(generation_samples, expectations)
            ):
                continue
            metric_rules.append((field.name, rule))

        captured_fields = {
            *spec.primary_keys,
            *(field_name for field_name, _rule in metric_rules),
        }
        if not set(spec.required_fields).issubset(captured_fields):
            return None
        ordered_fields = [
            field.name for field in spec.fields if field.name in captured_fields
        ]
        boundary_rule = anchor_rule
        for field_name in spec.primary_keys:
            boundary_rule = boundary_rule.replace(
                f"${{{field_name}}}",
                f"(?:{value_pattern(spec.field_map[field_name])})",
            )
        block_rules = [
            rf"  ^{TEXTFSM_END_OF_INPUT}$$ -> Record EOF",
            f"  {boundary_rule} -> Continue.Record",
            f"  {anchor_rule}",
            *(f"  {rule}" for _field_name, rule in metric_rules),
        ]
        template = (
            f"{value_declarations(spec, ordered_fields)}\n\n"
            f"Start\n  {anchor_rule} -> Block\n\n"
            "Block\n"
            + "\n".join(block_rules)
            + "\n\nEOF\n"
        )
        return TemplateCandidate(
            template,
            "deterministic:repeated_block:labeled_metrics",
        )

    def compile(
        self,
        spec: ParserSpec,
        generation_samples: tuple[ParserSample, ...],
    ) -> TemplateCandidate | None:
        metrics = self._compile_labeled_metric_blocks(spec, generation_samples)
        if metrics is not None:
            return metrics
        nested = self._compile_nested_records(spec, generation_samples)
        if nested is not None:
            return nested
        sectioned = self._compile_sectioned_table(spec, generation_samples)
        if sectioned is not None:
            return sectioned
        if len(spec.primary_keys) != 1:
            return None
        key = spec.primary_keys[0]
        labels = field_label_map(spec)
        anchor_labels = {
            normalize_label(label)
            for label in (key, key.replace("_", " "), *spec.field_map[key].source_labels)
        }
        sample_layouts: list[tuple[str, list[tuple[str, str]]]] = []
        for sample in generation_samples:
            anchors: list[re.Match[str]] = []
            mapped_rows: list[tuple[str, str]] = []
            for line in sample.validation_text.splitlines():
                anchor = re.match(r"^\s*([A-Za-z][\w /-]*?)\s+(\S+)\s*:\s*$", line)
                if anchor and normalize_label(anchor.group(1)) in anchor_labels:
                    anchors.append(anchor)
                    continue
                match = re.match(r"^\s*([^:]{1,80}?)\s*:\s*(.*?)\s*$", line)
                if not match:
                    continue
                field = labels.get(normalize_label(match.group(1)))
                if field and field != key and field not in {item[0] for item in mapped_rows}:
                    mapped_rows.append((field, match.group(1).strip()))
            if len(anchors) < 2 or len({match.group(1) for match in anchors}) != 1:
                return None
            if not (set(spec.required_fields) - {key}).issubset(
                {field for field, _label in mapped_rows}
            ):
                return None
            sample_layouts.append((anchors[0].group(1), mapped_rows))
        if not sample_layouts or any(layout != sample_layouts[0] for layout in sample_layouts[1:]):
            return None
        prefix, mapped_rows = sample_layouts[0]
        fields = [key, *(field for field, _label in mapped_rows)]
        anchor_rule = rf"^\s*{re.escape(prefix)}\s+${{{key}}}\s*:\s*$$"
        rules = [f"  {anchor_rule} -> Block", "", "Block"]
        for index, (field, label) in enumerate(mapped_rows):
            action = " -> Record Start" if index == len(mapped_rows) - 1 else ""
            rules.append(rf"  ^\s*{re.escape(label)}\s*:\s*${{{field}}}\s*$${action}")
        template = (
            f"{value_declarations(spec, fields)}\n\nStart\n"
            + "\n".join(rules)
            + "\n\nEOF\n"
        )
        return TemplateCandidate(template, "deterministic:repeated_block")

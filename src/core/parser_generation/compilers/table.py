"""Deterministic compiler for whitespace-delimited CLI tables."""

from __future__ import annotations

import re

from ..samples import ParserSample
from ..service import TemplateCandidate
from ..spec import ParserSpec
from .common import (
    literal_line_pattern,
    render_row_pattern,
    value_declarations,
    value_pattern,
)


class TableCompiler:
    name = "table"

    @staticmethod
    def _field_labels(field_name: str, spec: ParserSpec) -> tuple[str, ...]:
        field = spec.field_map[field_name]
        return tuple(
            dict.fromkeys(
                label
                for label in (
                    *field.source_labels,
                    field_name.replace("_", " "),
                )
                if str(label).strip()
            )
        )

    @classmethod
    def _key_value_label(
        cls,
        line: str,
        spec: ParserSpec,
        field_name: str,
    ) -> str | None:
        for label in sorted(cls._field_labels(field_name, spec), key=len, reverse=True):
            tokens = re.findall(r"\S+", label)
            if not tokens:
                continue
            label_pattern = r"\s+".join(re.escape(token) for token in tokens)
            match = re.match(
                rf"^\s*(?i:{label_pattern})\s*:\s*(?P<value>\S(?:.*\S)?)\s*$",
                line,
            )
            if match and re.fullmatch(
                value_pattern(spec.field_map[field_name]),
                match.group("value"),
            ):
                return label_pattern
        return None

    @staticmethod
    def _field_positions(
        line: str,
        spec: ParserSpec,
        *,
        required_fields: tuple[str, ...] | None = None,
    ) -> list[tuple[int, str]]:
        positions: list[tuple[int, str]] = []
        occupied: set[str] = set()
        for field in spec.fields:
            labels = sorted(
                (field.name.replace("_", " "), *field.source_labels),
                key=len,
                reverse=True,
            )
            for label in labels:
                tokens = [re.escape(token) for token in re.findall(r"\S+", label)]
                if not tokens:
                    continue
                match = re.search(
                    r"(?<!\w)" + r"\s+".join(tokens) + r"(?!\w)",
                    line,
                    re.IGNORECASE,
                )
                if match:
                    positions.append((match.start(), field.name))
                    occupied.add(field.name)
                    break
        required = set(required_fields if required_fields is not None else spec.required_fields)
        if not required.issubset(occupied):
            return []
        return sorted(positions)

    @staticmethod
    def _aligned_mapping(
        *,
        field_positions: list[tuple[int, str]],
        data_lines: list[str],
        spec: ParserSpec,
        required_fields: tuple[str, ...] | None = None,
    ) -> list[str | None] | None:
        token_rows = [list(re.finditer(r"\S+", line)) for line in data_lines]
        token_rows = [row for row in token_rows if row]
        if not token_rows:
            return None
        tokens = max(token_rows, key=len)
        mapped: list[str | None] = [None] * len(tokens)
        used_indexes: set[int] = set()
        for position, field in field_positions:
            available = [index for index in range(len(tokens)) if index not in used_indexes]
            if not available:
                break
            index = min(available, key=lambda item: abs(tokens[item].start() - position))
            mapped[index] = field
            used_indexes.add(index)
        required = set(required_fields if required_fields is not None else spec.required_fields)
        if not required.issubset(field for field in mapped if field):
            return None
        captured_indexes = [index for index, field in enumerate(mapped) if field]
        if captured_indexes:
            last_captured_index = max(captured_indexes)
            last_field = mapped[last_captured_index]
            if last_field and spec.field_map[last_field].value_type == "text":
                mapped = mapped[: last_captured_index + 1]
        return mapped

    @staticmethod
    def _context_anchor(
        line: str,
        spec: ParserSpec,
        key: str,
    ) -> tuple[str, str] | None:
        field = spec.field_map[key]
        labels = sorted(
            {key, key.replace("_", " "), *field.source_labels},
            key=len,
            reverse=True,
        )
        for label in labels:
            tokens = re.findall(r"\S+", label)
            if not tokens:
                continue
            label_pattern = r"\s+".join(re.escape(token) for token in tokens)
            before_value = re.match(
                rf"^\s*(?i:{label_pattern})\s+(?P<value>\S+)\s*:\s*$",
                line,
            )
            if before_value:
                return label_pattern, "label_value"
            after_colon = re.match(
                rf"^\s*(?i:{label_pattern})\s*:\s*(?P<value>\S+)\s*$",
                line,
            )
            if after_colon:
                return label_pattern, "label_colon_value"
        return None

    @staticmethod
    def _optional_trailing_field(
        mapped: list[str | None],
        data_lines: list[str],
    ) -> bool:
        captured_indexes = [index for index, field in enumerate(mapped) if field]
        if not captured_indexes:
            return False
        last_captured_index = max(captured_indexes)
        if last_captured_index != len(mapped) - 1:
            return False
        return any(
            len(re.findall(r"\S+", line)) <= last_captured_index
            for line in data_lines
            if line.strip()
            and not re.fullmatch(r"[-=+_|.\s]+", line)
        )

    @staticmethod
    def _context_anchor_rule(
        *,
        label_pattern: str,
        style: str,
        key: str,
    ) -> str:
        if style == "label_colon_value":
            return rf"  ^\s*(?i:{label_pattern})\s*:\s*${{{key}}}\s*$$"
        return rf"  ^\s*(?i:{label_pattern})\s+${{{key}}}\s*:\s*$$"

    def _compile_context_table(
        self,
        spec: ParserSpec,
        generation_samples: tuple[ParserSample, ...],
    ) -> TemplateCandidate | None:
        if len(spec.primary_keys) != 1:
            return None
        key = spec.primary_keys[0]
        data_required = tuple(field for field in spec.required_fields if field != key)
        if not data_required:
            return None

        layouts: dict[tuple[str | None, ...], dict[str, object]] = {}
        anchor_rules: list[str] = []
        for sample in generation_samples:
            lines = sample.validation_text.splitlines()
            anchors = [
                (index, anchor)
                for index, line in enumerate(lines)
                if (anchor := self._context_anchor(line, spec, key)) is not None
            ]
            if not anchors:
                return None
            sample_layouts = 0
            for anchor_position, (line_index, (label_pattern, style)) in enumerate(anchors):
                anchor_rule = self._context_anchor_rule(
                    label_pattern=label_pattern,
                    style=style,
                    key=key,
                )
                if anchor_rule not in anchor_rules:
                    anchor_rules.append(anchor_rule)
                section_end = (
                    anchors[anchor_position + 1][0]
                    if anchor_position + 1 < len(anchors)
                    else len(lines)
                )
                for header_index in range(line_index + 1, section_end):
                    header = lines[header_index]
                    positions = self._field_positions(
                        header,
                        spec,
                        required_fields=data_required,
                    )
                    if not positions:
                        continue
                    data_lines = [
                        line
                        for line in lines[header_index + 1 : section_end]
                        if line.strip()
                    ]
                    mapped = self._aligned_mapping(
                        field_positions=positions,
                        data_lines=data_lines,
                        spec=spec,
                        required_fields=data_required,
                    )
                    if mapped is None:
                        continue
                    row_rule = render_row_pattern(
                        mapped,
                        spec,
                        required_fields=data_required,
                        optional_trailing_field=self._optional_trailing_field(
                            mapped,
                            data_lines,
                        ),
                    )
                    if row_rule is None:
                        continue
                    signature = tuple(mapped)
                    layout = layouts.setdefault(
                        signature,
                        {
                            "mapped": mapped,
                            "headers": [],
                            "optional_trailing_field": False,
                        },
                    )
                    layout["optional_trailing_field"] = bool(
                        layout["optional_trailing_field"]
                    ) or self._optional_trailing_field(mapped, data_lines)
                    headers = layout["headers"]
                    if isinstance(headers, list) and header not in headers:
                        headers.append(header)
                    sample_layouts += 1
                    break
            if sample_layouts == 0:
                return None

        fields = [
            key,
            *dict.fromkeys(
                field
                for layout in layouts.values()
                for field in layout["mapped"]
                if field and field != key
            ),
        ]
        header_rules = [
            f"  {literal_line_pattern(str(header))}"
            for layout in layouts.values()
            for header in layout["headers"]
        ]
        row_rules = [
            row_rule
            for layout in layouts.values()
            if (
                row_rule := render_row_pattern(
                    list(layout["mapped"]),
                    spec,
                    required_fields=data_required,
                    optional_trailing_field=bool(
                        layout["optional_trailing_field"]
                    ),
                )
            )
        ]
        template = (
            value_declarations(
                spec,
                fields,
                filldown_fields=frozenset({key}),
            )
            + "\n\nStart\n"
            + "\n".join((*anchor_rules, *header_rules, *row_rules, r"  ^\s*$$"))
            + "\n\nEOF\n"
        )
        return TemplateCandidate(template, "deterministic:table:context")

    def _compile_summary_table(
        self,
        spec: ParserSpec,
        generation_samples: tuple[ParserSample, ...],
    ) -> TemplateCandidate | None:
        """Compile a key-value summary followed by one target entity table."""

        if not generation_samples:
            return None
        context_labels: dict[str, list[str]] = {}
        for field in spec.fields:
            if field.name in spec.primary_keys:
                continue
            sample_patterns: list[str] = []
            for sample in generation_samples:
                pattern = next(
                    (
                        matched
                        for line in sample.validation_text.splitlines()
                        if (
                            matched := self._key_value_label(
                                line,
                                spec,
                                field.name,
                            )
                        )
                    ),
                    None,
                )
                if pattern is None:
                    break
                sample_patterns.append(pattern)
            if len(sample_patterns) == len(generation_samples):
                context_labels[field.name] = list(dict.fromkeys(sample_patterns))

        required_context = set(spec.required_fields).intersection(context_labels)
        row_required = tuple(
            name for name in spec.required_fields if name not in required_context
        )
        if (
            not required_context
            or not row_required
            or not set(spec.primary_keys).issubset(row_required)
        ):
            return None

        layouts: dict[tuple[str | None, ...], dict[str, object]] = {}
        for sample in generation_samples:
            sample_layouts = 0
            lines = sample.validation_text.splitlines()
            for line_index, header in enumerate(lines):
                positions = self._field_positions(
                    header,
                    spec,
                    required_fields=row_required,
                )
                if not positions:
                    continue
                # A nearby physical row is enough to infer column alignment. Keeping
                # this window bounded prevents a later prose footer from becoming
                # the representative row for a long table.
                data_lines = [
                    line
                    for line in lines[line_index + 1 : line_index + 41]
                    if line.strip()
                    and not re.fullmatch(r"\s*[-=+_|.]+\s*", line)
                ]
                mapped = self._aligned_mapping(
                    field_positions=positions,
                    data_lines=data_lines,
                    spec=spec,
                    required_fields=row_required,
                )
                if mapped is None:
                    continue
                row_rule = render_row_pattern(
                    mapped,
                    spec,
                    required_fields=row_required,
                )
                if row_rule is None:
                    continue
                signature = tuple(mapped)
                layout = layouts.setdefault(
                    signature,
                    {"mapped": mapped, "headers": []},
                )
                headers = layout["headers"]
                if isinstance(headers, list) and header not in headers:
                    headers.append(header)
                sample_layouts += 1
            if sample_layouts == 0:
                return None

        if not layouts:
            return None
        context_fields = [
            field.name for field in spec.fields if field.name in context_labels
        ]
        row_fields = list(
            dict.fromkeys(
                field
                for layout in layouts.values()
                for field in layout["mapped"]
                if field
            )
        )
        fields = list(dict.fromkeys((*context_fields, *row_fields)))
        context_rules = [
            (
                rf"  ^\s*(?i:{label_pattern})\s*:\s*"
                rf"${{{field_name}}}\s*$$"
            )
            for field_name in context_fields
            for label_pattern in context_labels[field_name]
        ]
        header_rules = list(
            dict.fromkeys(
                f"  {literal_line_pattern(str(header))} -> Rows"
                for layout in layouts.values()
                for header in layout["headers"]
            )
        )
        row_rules = list(
            dict.fromkeys(
                row_rule
                for layout in layouts.values()
                if (
                    row_rule := render_row_pattern(
                        list(layout["mapped"]),
                        spec,
                        required_fields=row_required,
                    )
                )
            )
        )
        template = (
            value_declarations(
                spec,
                fields,
                filldown_fields=frozenset(context_fields),
            )
            + "\n\nStart\n"
            + "\n".join((*context_rules, *header_rules, r"  ^.*"))
            + "\n\nRows\n"
            + "\n".join(
                (
                    *context_rules,
                    *header_rules,
                    *row_rules,
                    r"  ^\s*$$",
                    r"  ^.*",
                )
            )
            + "\n\nEOF\n"
        )
        return TemplateCandidate(
            template,
            "deterministic:table:summary_context",
        )

    def compile(
        self,
        spec: ParserSpec,
        generation_samples: tuple[ParserSample, ...],
    ) -> TemplateCandidate | None:
        summary_table = self._compile_summary_table(spec, generation_samples)
        if summary_table is not None:
            return summary_table
        context_table = self._compile_context_table(spec, generation_samples)
        if context_table is not None:
            return context_table
        layouts: dict[tuple[str | None, ...], dict[str, object]] = {}
        for sample in generation_samples:
            sample_layouts = 0
            lines = sample.validation_text.splitlines()
            for line_index, line in enumerate(lines):
                positions = self._field_positions(line, spec)
                if not positions:
                    continue
                data_lines: list[str] = []
                for following in lines[line_index + 1 :]:
                    if not following.strip():
                        break
                    if following.strip().endswith(":") and data_lines:
                        break
                    data_lines.append(following)
                mapped = self._aligned_mapping(
                    field_positions=positions,
                    data_lines=data_lines,
                    spec=spec,
                )
                if mapped is None:
                    continue
                optional_trailing_field = self._optional_trailing_field(
                    mapped,
                    data_lines,
                )
                if render_row_pattern(
                    mapped,
                    spec,
                    optional_trailing_field=optional_trailing_field,
                ):
                    signature = tuple(mapped)
                    layout = layouts.setdefault(
                        signature,
                        {
                            "mapped": mapped,
                            "headers": [],
                            "optional_trailing_field": False,
                        },
                    )
                    layout["optional_trailing_field"] = bool(
                        layout["optional_trailing_field"]
                    ) or optional_trailing_field
                    headers = layout["headers"]
                    if isinstance(headers, list) and line not in headers:
                        headers.append(line)
                    sample_layouts += 1
            if sample_layouts == 0:
                return None
        if not layouts:
            return None
        fields = list(
            dict.fromkeys(
                field
                for layout in layouts.values()
                for field in layout["mapped"]
                if field
            )
        )
        transitions: list[str] = []
        states: list[str] = []
        for index, layout in enumerate(layouts.values()):
            mapped = list(layout["mapped"])
            headers = list(layout["headers"])
            header_rules = [
                f"  {literal_line_pattern(str(header))} -> Rows{index}" for header in headers
            ]
            transitions.extend(header_rules)
            row_rule = render_row_pattern(
                mapped,
                spec,
                optional_trailing_field=bool(
                    layout["optional_trailing_field"]
                ),
            )
            if row_rule is None:
                continue
            states.extend(
                [
                    f"Rows{index}",
                    *header_rules,
                    r"  ^\s*$$ -> Start",
                    r"  ^\s*.*:\s*$$ -> Start",
                    row_rule,
                    "",
                ]
            )
        template = (
            f"{value_declarations(spec, fields)}\n\n"
            "Start\n"
            + "\n".join(transitions)
            + "\n\n"
            + "\n".join(states)
            + "\nEOF\n"
        )
        return TemplateCandidate(template, "deterministic:table")

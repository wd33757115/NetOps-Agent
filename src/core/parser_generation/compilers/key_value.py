"""Deterministic compiler for one-record key/value outputs."""

from __future__ import annotations

import re

from ..samples import ParserSample
from ..service import TemplateCandidate
from ..spec import ParserSpec
from .common import field_label_map, normalize_label, value_declarations


class KeyValueCompiler:
    name = "key_value"

    def compile(
        self,
        spec: ParserSpec,
        generation_samples: tuple[ParserSample, ...],
    ) -> TemplateCandidate | None:
        labels = field_label_map(spec)
        orders: list[list[tuple[str, str]]] = []
        for sample in generation_samples:
            rows: list[tuple[str, str]] = []
            for line in sample.validation_text.splitlines():
                match = re.match(r"^\s*([^:]{1,80}?)\s*:\s*(.*?)\s*$", line)
                if not match:
                    continue
                field = labels.get(normalize_label(match.group(1)))
                if field and field not in {item[0] for item in rows}:
                    rows.append((field, match.group(1).strip()))
            orders.append(rows)
        if not orders or not orders[0]:
            return None
        field_order = [field for field, _label in orders[0]]
        if any([field for field, _label in rows] != field_order for rows in orders):
            return None
        if not set(spec.required_fields).issubset(field_order):
            return None
        rules: list[str] = []
        for index, (field, label) in enumerate(orders[0]):
            action = " -> Record" if index == len(orders[0]) - 1 else ""
            rules.append(rf"  ^\s*{re.escape(label)}\s*:\s*${{{field}}}\s*$${action}")
        template = (
            f"{value_declarations(spec, field_order)}\n\nStart\n"
            + "\n".join(rules)
            + "\n"
        )
        return TemplateCandidate(template, "deterministic:key_value")

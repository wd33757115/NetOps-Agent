"""Immutable multi-record Parser asset envelope."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

PARSER_BUNDLE_FORMAT = "netops.parser_bundle/v1"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_MAX_RECORD_SETS = 16
_MAX_TEMPLATE_CHARS = 128_000


class ParserBundleError(ValueError):
    """Raised when an immutable Parser bundle is malformed."""


@dataclass(frozen=True)
class ParserBundleRecordSet:
    name: str
    template_text: str


@dataclass(frozen=True)
class ParserBundle:
    record_sets: tuple[ParserBundleRecordSet, ...]
    format: str = PARSER_BUNDLE_FORMAT

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "record_sets": [
                {
                    "name": item.name,
                    "template_text": item.template_text,
                }
                for item in self.record_sets
            ],
        }

    def serialize(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def build_parser_bundle(templates: dict[str, str]) -> ParserBundle:
    return _parse_payload(
        {
            "format": PARSER_BUNDLE_FORMAT,
            "record_sets": [
                {"name": name, "template_text": template}
                for name, template in templates.items()
            ],
        }
    )


def parse_parser_bundle(content: str) -> ParserBundle | None:
    text = str(content or "").strip()
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("format") != PARSER_BUNDLE_FORMAT:
        return None
    return _parse_payload(payload)


def _parse_payload(payload: dict[str, Any]) -> ParserBundle:
    raw_sets = payload.get("record_sets")
    if not isinstance(raw_sets, list) or not raw_sets:
        raise ParserBundleError("Parser bundle requires a non-empty record_sets array")
    if len(raw_sets) > _MAX_RECORD_SETS:
        raise ParserBundleError(
            f"Parser bundle exceeds {_MAX_RECORD_SETS} record sets"
        )
    record_sets: list[ParserBundleRecordSet] = []
    names: set[str] = set()
    for index, raw in enumerate(raw_sets):
        if not isinstance(raw, dict):
            raise ParserBundleError(f"record_sets[{index}] must be an object")
        name = str(raw.get("name") or "").strip()
        template_text = str(raw.get("template_text") or "").strip()
        if not _NAME_RE.fullmatch(name):
            raise ParserBundleError(f"invalid record-set name: {name or '<empty>'}")
        if name in names:
            raise ParserBundleError(f"duplicate record-set name: {name}")
        if not template_text:
            raise ParserBundleError(f"record set {name} requires template_text")
        if len(template_text) > _MAX_TEMPLATE_CHARS:
            raise ParserBundleError(
                f"record set {name} exceeds {_MAX_TEMPLATE_CHARS} template characters"
            )
        names.add(name)
        record_sets.append(
            ParserBundleRecordSet(
                name=name,
                template_text=f"{template_text.rstrip()}\n",
            )
        )
    return ParserBundle(record_sets=tuple(record_sets))

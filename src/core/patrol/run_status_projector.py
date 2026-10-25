"""Authoritative patrol run readiness projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.core.patrol.statuses import readiness_status

PARSER_READY_STATUSES = {
    "parsed",
    "parse_empty",
    "ignored",
    "raw_hashed",
    "not_applicable",
}
FACT_READY_STATUSES = {"ready", "empty", "ignored", "not_applicable"}


def _status_value(item: Any, field: str) -> str:
    if isinstance(item, dict):
        return str(item.get(field) or "")
    return str(getattr(item, field, "") or "")


@dataclass(frozen=True)
class PatrolRunReadiness:
    parser_readiness_status: str
    fact_readiness_status: str


class PatrolRunStatusProjector:
    """Projects run-level readiness from persisted command results."""

    @staticmethod
    def project(command_results: Iterable[Any]) -> PatrolRunReadiness:
        rows = list(command_results)
        return PatrolRunReadiness(
            parser_readiness_status=readiness_status(
                [_status_value(row, "parse_status") for row in rows],
                ready=PARSER_READY_STATUSES,
            ),
            fact_readiness_status=readiness_status(
                [_status_value(row, "fact_status") for row in rows],
                ready=FACT_READY_STATUSES,
            ),
        )

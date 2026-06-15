"""Patrol execution modes."""

from __future__ import annotations

PRODUCTION_MODE = "production"
PARSER_VALIDATION_MODE = "parser_validation"
VALID_PATROL_MODES = {PRODUCTION_MODE, PARSER_VALIDATION_MODE}


def normalize_patrol_mode(mode: str | None) -> str:
    value = str(mode or PRODUCTION_MODE).strip().lower().replace("-", "_")
    aliases = {
        "prod": PRODUCTION_MODE,
        "formal": PRODUCTION_MODE,
        "test": PARSER_VALIDATION_MODE,
        "validation": PARSER_VALIDATION_MODE,
        "parser_test": PARSER_VALIDATION_MODE,
    }
    value = aliases.get(value, value)
    if value not in VALID_PATROL_MODES:
        raise ValueError(
            f"invalid patrol mode: {mode}; expected production or parser_validation"
        )
    return value

"""State observation layer for normalized NetOps facts."""

from __future__ import annotations

from typing import Any

__all__ = ["normalize_state_from_params"]


def __getattr__(name: str) -> Any:
    """Load the normalizer only when the package-level helper is requested."""

    if name == "normalize_state_from_params":
        from src.core.state.normalizer import normalize_state_from_params

        return normalize_state_from_params
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

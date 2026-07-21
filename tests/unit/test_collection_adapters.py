"""Unit tests for collection adapters registry."""

from __future__ import annotations

import pytest

from src.core.collection.adapters import (
    CLICollectionAdapter,
    GnmiCollectionAdapter,
    get_adapter,
)


def test_get_adapter_cli() -> None:
    adapter = get_adapter("cli")
    assert isinstance(adapter, CLICollectionAdapter)
    assert adapter.source == "cli"


def test_gnmi_stub_raises() -> None:
    adapter = get_adapter("gnmi")
    assert isinstance(adapter, GnmiCollectionAdapter)
    with pytest.raises(NotImplementedError):
        adapter.collect({"device_id": "d1"}, commands=["path"])


def test_unknown_adapter() -> None:
    with pytest.raises(KeyError):
        get_adapter("unknown-source")

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.core.check_items.report_manifest import (
    _prepare_snapshot_transaction,
)


class _Connection:
    def __init__(
        self,
        *,
        isolation_level: str,
        in_transaction: bool,
    ) -> None:
        self.dialect = SimpleNamespace(name="postgresql")
        self._isolation_level = isolation_level
        self._in_transaction = in_transaction

    def get_isolation_level(self) -> str:
        return self._isolation_level

    def in_transaction(self) -> bool:
        return self._in_transaction


class _Session:
    def __init__(
        self,
        bind: Any,
        *,
        established_isolation_level: str = "REPEATABLE READ",
    ) -> None:
        self.bind = bind
        self.established_isolation_level = established_isolation_level
        self.connection_calls: list[dict[str, Any] | None] = []

    def get_bind(self) -> Any:
        return self.bind

    def connection(
        self,
        *,
        execution_options: dict[str, Any] | None = None,
    ) -> _Connection:
        self.connection_calls.append(execution_options)
        if isinstance(self.bind, _Connection):
            return self.bind
        return _Connection(
            isolation_level=self.established_isolation_level,
            in_transaction=True,
        )


def test_snapshot_transaction_is_not_required_for_sqlite() -> None:
    session = _Session(SimpleNamespace(dialect=SimpleNamespace(name="sqlite")))

    _prepare_snapshot_transaction(session)

    assert session.connection_calls == []


def test_snapshot_transaction_is_established_before_queries() -> None:
    session = _Session(
        SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    )

    _prepare_snapshot_transaction(session)

    assert session.connection_calls == [
        {"isolation_level": "REPEATABLE READ"}
    ]


def test_existing_repeatable_read_transaction_is_accepted() -> None:
    connection = _Connection(
        isolation_level="REPEATABLE READ",
        in_transaction=True,
    )
    session = _Session(connection)

    _prepare_snapshot_transaction(session)

    assert session.connection_calls == [None]


def test_existing_read_committed_transaction_is_rejected() -> None:
    connection = _Connection(
        isolation_level="READ COMMITTED",
        in_transaction=True,
    )
    session = _Session(connection)

    with pytest.raises(
        RuntimeError,
        match="already in READ COMMITTED",
    ):
        _prepare_snapshot_transaction(session)

    assert session.connection_calls == []


def test_failed_isolation_setup_is_rejected() -> None:
    session = _Session(
        SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
        established_isolation_level="READ COMMITTED",
    )

    with pytest.raises(
        RuntimeError,
        match="could not establish REPEATABLE READ",
    ):
        _prepare_snapshot_transaction(session)

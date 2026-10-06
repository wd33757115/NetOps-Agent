"""Version 1 of the in-process NETCONF definition contract, read-only.

A consumer (topology) opens its own read-only platform NETCONF session under its own
``device.netconf`` grant and login, and passes it here. This contract chooses the
published definition for a check item on a device family and maps what the device
returns into that check item's Value rows (``backend/mapping.py``). It exposes no
draft, publication or trial operations.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from .backend import mapping, standard

CONTRACT_VERSION = 1
DefinitionError = mapping.DefinitionError
MappingError = mapping.MappingError

__all__ = [
    "CONTRACT_VERSION",
    "DefinitionError",
    "DefinitionReader",
    "MappingError",
    "NetconfRead",
    "NetconfSelection",
    "StandardReader",
    "StandardSelection",
    "announced",
]


@dataclass(frozen=True)
class NetconfSelection:
    """The published way to read one check item from one device family over NETCONF."""

    check_item_id: str
    definition_id: str
    version: int
    sha256: str
    module: str | None  # the module the device must announce in its hello
    definitions: Mapping[str, mapping.Definition]  # the definition and the tables it looks up

    @property
    def ref(self):
        return f"{self.definition_id}@v{self.version}"


@dataclass(frozen=True)
class NetconfRead:
    rows: list[dict[str, str]]  # the check item's Values, written as the CLI prints them
    refs: list[int]  # the device row (1-based) each output row came from
    problems: list[dict[str, Any]]  # row, key, value, kind, raw
    read: int  # rows the device returned
    replies: list[dict[str, str]]  # [{definition, filter, reply}] in the order read


def announced(selection: NetconfSelection, capabilities: Iterable[str]) -> bool:
    """Whether a device's hello announces the definition's module (RFC 6020 module
    capabilities); a definition that names no module is not checked."""
    if not selection.module:
        return True
    from urllib.parse import parse_qs

    return any(
        selection.module in parse_qs(capability.partition("?")[2]).get("module", [])
        for capability in capabilities
    )


def _published():
    from .backend.service import published_versions

    return published_versions()


class DefinitionReader:
    """Select published definitions and read them over a consumer's NETCONF session."""

    def __init__(self, loader: Callable[[], Mapping[str, tuple]] | None = None) -> None:
        """``loader``: {id: (definition, version, sha256)}; the published ones by default."""
        self._loader = loader or _published
        self._published = None

    def _resolved(self):
        if self._published is None:
            self._published = self._loader()
        return self._published

    def select(
        self, check_item_ids: Iterable[str], device: Mapping[str, Any]
    ) -> dict[str, NetconfSelection]:
        """{check item: selection} for a device {"vendor", "platform"}; a check item two
        definitions claim for the same family is not selected (publishing refuses it)."""
        published = self._resolved()
        definitions = {key: value[0] for key, value in published.items()}
        vendor = str(device.get("vendor") or "").casefold()
        platform = device.get("platform")
        wanted = set(check_item_ids)
        found = {}
        for key, (definition, version, sha256) in sorted(published.items()):
            if (
                definition.check_item in wanted
                and definition.vendor.casefold() == vendor
                and platform in definition.platforms
            ):
                needed = {key: definition}
                for lookup in definition.lookups():
                    if lookup.definition in definitions:
                        needed[lookup.definition] = definitions[lookup.definition]
                found.setdefault(definition.check_item, []).append(
                    NetconfSelection(
                        definition.check_item, key, version, sha256, definition.module, needed
                    )
                )
        return {item: chosen[0] for item, chosen in found.items() if len(chosen) == 1}

    def read(
        self,
        selection: NetconfSelection,
        session,
        *,
        fields: Mapping[str, Mapping[str, Any]],
        guard: Callable[[], Any] | None = None,
    ) -> NetconfRead:
        """Read the definition (the tables it looks up first) and map its rows.

        ``fields`` is the check item's ``Selection.fields`` from rule governance. A
        session or RPC failure raises the platform's DeviceAccessError; a reply that is
        not XML raises MappingError.
        """
        fetch = mapping.fetcher(session)
        replies = []

        def recorded(definition, subtree):
            if guard is not None:
                guard()
            reply = fetch(definition, subtree)
            replies.append({"definition": definition.id, "filter": subtree, "reply": reply})
            return reply

        mapped = mapping.collect(
            selection.definitions, selection.definition_id, recorded, fields=fields
        )
        return NetconfRead(
            mapped.rows,
            mapped.refs,
            [asdict(problem) for problem in mapped.problems],
            mapped.read,
            replies,
        )


@dataclass(frozen=True)
class StandardSelection:
    """The mapping that reads a device family's tables as standard records."""

    mapping: standard.StandardMapping

    @property
    def ref(self):
        return f"{self.mapping.id}@{self.mapping.sha256[:12]}"


class StandardReader:
    """Select a device family's standard mapping and read it over a consumer's session.

    The records are named after the standard leaves (``backend/standard.py``); the
    consumer places them in the standard trees.
    """

    def __init__(
        self, loader: Callable[[], Mapping[str, standard.StandardMapping]] | None = None
    ) -> None:
        """``loader``: {id: mapping}; the mappings shipped with the app by default."""
        self._loader = loader or standard.shipped
        self._mappings = None

    def select(self, device: Mapping[str, Any]) -> StandardSelection | None:
        """The one mapping for a device {"vendor", "platform"}, or None."""
        if self._mappings is None:
            self._mappings = self._loader()
        vendor = str(device.get("vendor") or "").casefold()
        found = [
            item
            for item in self._mappings.values()
            if item.vendor.casefold() == vendor and device.get("platform") in item.platforms
        ]
        return StandardSelection(found[0]) if len(found) == 1 else None

    def read(self, selection: StandardSelection, session, *, guard=None) -> dict[str, Any]:
        """{"records", "problems", "replies", "failed"}; a table the device refuses
        (an RPC error) is failed, a lost session raises the platform's error."""
        from src.platform.device_access.contracts import DeviceAccessError

        class RefusedError(Exception):
            pass

        def fetch(subtree):
            if guard is not None:
                guard()
            try:
                return session.get(subtree)
            except DeviceAccessError as exc:
                if exc.failure_code == "netconf_rpc_error":
                    raise RefusedError(str(exc)) from exc
                raise

        return standard.read(selection.mapping, fetch, refused=(RefusedError,))

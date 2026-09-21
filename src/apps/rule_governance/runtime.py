"""Version 1 of the in-process rule-consumption contract.

Inspection authorizes its own caller before using these read operations. Reading
published assets or a previously frozen version does not require admission to the
rule-authoring App. This module deliberately exposes no approval, publication,
generation or lifecycle operations. It is not an untrusted-code sandbox.

Existing serialized bundles and their digest algorithms remain unchanged. A new
publication only changes future selection; historical runs resolve their exact
version IDs, including deprecated versions, and validate the stored digests.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from typing import Any

from .backend.parser.identity import DeviceIdentity, DeviceIdentityResolver
from .backend.parser.runtime_bundle import ParserRuntimeBundle, ParserTemplateRef
from .backend.parser.runtime_repository import ParserRuntimeRepository
from .backend.rule_control.runtime_assets import (
    RuntimeRuleAssetBundle,
    RuntimeRuleAssetResolver,
)

CONTRACT_VERSION = 1

__all__ = [
    "CONTRACT_VERSION",
    "CheckItemRuntimeReader",
    "DeviceIdentity",
    "DeviceIdentityResolver",
    "ParserRuntimeBundle",
    "ParserTemplateRef",
    "ParserRuntimeRepository",
    "RuntimeRuleAssetBundle",
    "RuntimeRuleAssetResolver",
]


class CheckItemRuntimeReader:
    """Read exact asset versions without exposing the authoring service API."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] | None = None,
    ) -> None:
        from .backend.check_items.runtime_bundle import CheckItemRuntimeBundleService

        self._service = (
            CheckItemRuntimeBundleService(session_scope=session_scope)
            if session_scope is not None
            else CheckItemRuntimeBundleService()
        )

    def get_active(
        self, *, check_item_version_id: str, implementation_version_id: str
    ) -> dict[str, Any] | None:
        return self._service.get_active(
            check_item_version_id=check_item_version_id,
            implementation_version_id=implementation_version_id,
        )

    def get_active_many(
        self, pairs: Iterable[tuple[str, str]]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        return self._service.get_active_many(pairs)

    def get_version(self, version_id: str) -> dict[str, Any] | None:
        return self._service.get_version(version_id)

    def get_rule_assets(self, version_id: str) -> dict[str, Any] | None:
        return self._service.get_rule_assets(version_id)

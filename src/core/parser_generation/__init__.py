"""Compatibility exports; retain this path for legacy submodule shims."""
from importlib import import_module as _import_module

_impl = _import_module('src.apps.rule_governance.backend.parser_generation')
__all__ = getattr(_impl, "__all__", [])


def __getattr__(name):
    return getattr(_impl, name)

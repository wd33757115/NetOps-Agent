"""Compatibility import; rule governance owns the API implementation."""
import importlib as _importlib
import sys as _sys
_sys.modules[__name__] = _importlib.import_module("src.apps.rule_governance.backend.api")

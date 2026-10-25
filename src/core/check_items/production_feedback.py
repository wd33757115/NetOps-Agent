"""Compatibility import; implementation owned by the rule governance package."""
import importlib as _importlib
import sys as _sys
_sys.modules[__name__] = _importlib.import_module('src.apps.rule_governance.backend.check_items.production_feedback')

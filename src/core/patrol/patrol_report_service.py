"""Compatibility import; implementation is owned by its business package."""
import importlib as _importlib
import sys as _sys
_sys.modules[__name__] = _importlib.import_module('src.apps.inspection.backend.patrol.patrol_report_service')

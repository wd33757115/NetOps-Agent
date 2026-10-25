"""Compatibility import for the platform credential boundary."""
import importlib as _i
import sys as _s
_s.modules[__name__] = _i.import_module('src.platform.inventory.credentials.service')

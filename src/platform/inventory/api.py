"""Compatibility import; device HTTP implementation belongs to device-management.

The shared repository, credentials and readiness projections remain platform services.
"""
import sys
from importlib import import_module
sys.modules[__name__] = import_module("src.apps.device_management.backend.api")

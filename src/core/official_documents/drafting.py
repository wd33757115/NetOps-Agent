"""Compatibility import; implementation belongs to the application package."""
import importlib
import sys
sys.modules[__name__] = importlib.import_module("src.apps.official_documents.backend.drafting")

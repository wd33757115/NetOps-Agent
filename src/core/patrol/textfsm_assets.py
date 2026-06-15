"""Compatibility imports for the platform parser asset store.

New code must resolve templates through TemplateRegistry. Files are not active
assets merely because they exist on disk.
"""

from src.core.parser.assets import (
    DEFAULT_ASSET_ROOT as SHARED_TEXTFSM_ROOT,
    TemplateAssetStore,
    safe_slug,
)

__all__ = ["SHARED_TEXTFSM_ROOT", "TemplateAssetStore", "safe_slug"]

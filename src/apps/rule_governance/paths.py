"""Stable paths independent of the location of compatibility modules."""

from pathlib import Path

from src.platform.kernel.registry import ROOT

PACKAGE_ROOT = Path(__file__).resolve().parent
RESOURCES = PACKAGE_ROOT / "resources"
# Existing database records and generator scripts use this mutable directory.
# Keep it outside the package content digest throughout the P4 transition.
TEMPLATE_STORE = ROOT / "src" / "skills" / "shared" / "textfsm-templates"
VALUE_MAPPING_CONFIG = ROOT / "config" / "parser_value_mappings.yaml"

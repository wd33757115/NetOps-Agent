"""Compatibility module alias for the package-owned TextFSM generator."""
from pathlib import Path
import importlib
import sys
_root = Path(__file__).resolve().parents[4]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
sys.modules[__name__] = importlib.import_module('src.apps.rule_governance.backend.generators.textfsm_generator.scripts.report_generator')

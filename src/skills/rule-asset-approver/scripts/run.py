"""Legacy script entry; business implementation lives in the rule governance package."""
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[4]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
_target = _root / 'src/apps/rule_governance/backend/generators/rule_asset_approver/scripts/run.py'
__file__ = str(_target)
exec(compile(_target.read_text(encoding="utf-8-sig"), str(_target), "exec"), globals())

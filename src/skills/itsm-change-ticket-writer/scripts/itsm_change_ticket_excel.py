"""Compatibility entry; implementation is owned by the firewall capability package."""
from pathlib import Path
import sys
_target = Path(__file__).resolve().parents[4] / 'src/apps/firewall_policy/backend/engine/ticket/scripts/itsm_change_ticket_excel.py'
sys.path.insert(0, str(_target.parent))
__file__ = str(_target)
exec(compile(_target.read_text(encoding="utf-8-sig"), str(_target), "exec"), globals())

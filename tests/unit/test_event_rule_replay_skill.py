from __future__ import annotations

import hashlib
import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.infrastructure.db.models import NetworkChangeRecord
from src.infrastructure.db.postgres import get_db_session


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "skills"
    / "event-rule-replay"
    / "scripts"
    / "run.py"
)
SPEC = importlib.util.spec_from_file_location("event_rule_replay_run", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
run_replay = MODULE.run_replay


def test_event_rule_replay_skill_replays_candidate_rules():
    change_id = str(uuid.uuid4())
    key = hashlib.sha256(change_id.encode("utf-8")).hexdigest()
    with get_db_session() as db:
        db.add(
            NetworkChangeRecord(
                id=change_id,
                idempotency_key=key,
                run_id="pytest-event-rule-replay-skill",
                device_id="lab-device-replay-skill",
                canonical_command="display cpu",
                entity_type="cpu",
                entity_key="slot=1",
                field="cpu_5s",
                old_value_json=20,
                new_value_json=96,
                change_type="modified",
                source="pytest",
                event_status="pending",
                detected_at=datetime.now(timezone.utc),
                confidence=1.0,
            )
        )
    try:
        result = run_replay(
            {
                "candidate_rules": [
                    {
                        "name": "pytest-cpu-high-replay-skill",
                        "event_type": "CPUHigh",
                        "severity": "warning",
                        "condition": {
                            "kind": "threshold",
                            "entity_types": ["cpu"],
                            "fields": ["cpu_5s"],
                            "gte": 90,
                        },
                    }
                ],
                "include_active_collisions": False,
                "limit": 100,
            }
        )

        assert result["success"] is True
        assert result["matched_change_count"] >= 1
        assert result["event_types"]["CPUHigh"] >= 1
    finally:
        with get_db_session() as db:
            db.query(NetworkChangeRecord).filter(NetworkChangeRecord.id == change_id).delete()

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from src.core.patrol.event_rules import EventRuleDefinition
from src.core.patrol.rule_replay import replay_event_rule_definitions
from src.infrastructure.db.models import NetworkChangeRecord
from src.infrastructure.db.postgres import get_db_session


def test_event_rule_replay_matches_historical_change_without_private_payload():
    change_id = str(uuid.uuid4())
    key = hashlib.sha256(change_id.encode("utf-8")).hexdigest()
    with get_db_session() as db:
        db.add(
            NetworkChangeRecord(
                id=change_id,
                idempotency_key=key,
                run_id="pytest-rule-replay",
                device_id="lab-device-1",
                canonical_command="display cpu",
                entity_type="cpu",
                entity_key="slot=1",
                field="cpu_5s",
                old_value_json=30,
                new_value_json=95,
                change_type="modified",
                source="pytest",
                event_status="pending",
                detected_at=datetime.now(timezone.utc),
                confidence=1.0,
            )
        )
    try:
        result = replay_event_rule_definitions(
            [
                EventRuleDefinition(
                    rule_id="cpu-high-candidate",
                    version=1,
                    event_type="CPUHigh",
                    severity="warning",
                    condition={
                        "kind": "threshold",
                        "entity_types": ["cpu"],
                        "fields": ["cpu_5s"],
                        "gte": 90,
                    },
                )
            ],
            include_active_collisions=False,
            limit=100,
        )

        assert result["success"] is True
        assert result["matched_change_count"] >= 1
        assert result["event_types"]["CPUHigh"] >= 1
        assert result["matched_entity_types"]["cpu"] >= 1
        assert result["top_devices"]["lab-device-1"] >= 1
        assert result["unexpected_matches"] == []
    finally:
        with get_db_session() as db:
            db.query(NetworkChangeRecord).filter(NetworkChangeRecord.id == change_id).delete()

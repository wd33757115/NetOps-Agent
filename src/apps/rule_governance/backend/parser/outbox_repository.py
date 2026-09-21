"""Transactional Parser outbox persistence and publisher claims."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_

from src.infrastructure.db.models import ParserOutboxEvent
from src.infrastructure.db.postgres import get_db_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def add_outbox_row(
    db: Any,
    *,
    event_type: str,
    aggregate_id: str,
    payload: dict[str, Any],
) -> None:
    """Add an event using the caller's transaction."""
    db.add(
        ParserOutboxEvent(
            id=str(uuid.uuid4()),
            event_type=event_type,
            aggregate_id=aggregate_id,
            payload_json=payload,
            status="pending",
            created_at=_utcnow(),
        )
    )


class ParserOutboxRepository:
    def claim_pending(
        self,
        limit: int = 100,
        *,
        lease_seconds: int = 120,
    ) -> list[ParserOutboxEvent]:
        now = _utcnow()
        with get_db_session() as db:
            rows = (
                db.query(ParserOutboxEvent)
                .filter(
                    or_(
                        ParserOutboxEvent.status == "pending",
                        and_(
                            ParserOutboxEvent.status == "publishing",
                            ParserOutboxEvent.lease_until < now,
                        ),
                    )
                )
                .order_by(ParserOutboxEvent.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
                .all()
            )
            for row in rows:
                row.status = "publishing"
                row.claim_token = uuid.uuid4().hex
                row.lease_until = now + timedelta(seconds=max(30, int(lease_seconds)))
                row.attempts = int(row.attempts or 0) + 1
                row.last_error = None
            db.flush()
            for row in rows:
                db.expunge(row)
            return rows

    def mark_published(self, event_id: str, *, claim_token: str) -> None:
        from .repository import ParserJobStateError

        with get_db_session() as db:
            updated = (
                db.query(ParserOutboxEvent)
                .filter(
                    ParserOutboxEvent.id == event_id,
                    ParserOutboxEvent.status == "publishing",
                    ParserOutboxEvent.claim_token == claim_token,
                )
                .update(
                    {
                        ParserOutboxEvent.status: "published",
                        ParserOutboxEvent.published_at: _utcnow(),
                        ParserOutboxEvent.claim_token: None,
                        ParserOutboxEvent.lease_until: None,
                    },
                    synchronize_session=False,
                )
            )
            if updated != 1:
                raise ParserJobStateError(f"parser outbox claim was fenced: {event_id}")

    def release(self, event_id: str, *, claim_token: str, error: str) -> None:
        with get_db_session() as db:
            db.query(ParserOutboxEvent).filter(
                ParserOutboxEvent.id == event_id,
                ParserOutboxEvent.status == "publishing",
                ParserOutboxEvent.claim_token == claim_token,
            ).update(
                {
                    ParserOutboxEvent.status: "pending",
                    ParserOutboxEvent.claim_token: None,
                    ParserOutboxEvent.lease_until: None,
                    ParserOutboxEvent.last_error: str(error)[:4000],
                },
                synchronize_session=False,
            )

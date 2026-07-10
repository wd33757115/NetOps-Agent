"""Celery delivery task for the durable network-event outbox."""

from src.core.celery_tasks.celery_app import celery
from src.core.patrol.event_builder import publish_event_outbox


@celery.task(name="publish_network_event_outbox")
def publish_network_event_outbox():
    message_ids = publish_event_outbox(limit=200)
    return {"published_message_ids": message_ids, "published_count": len(message_ids)}

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from kombu import Connection

from src.auth.token_store import get_redis
from src.core.events.idempotency import (
    claim_processing,
    is_processed,
    mark_processed,
    renew_processing,
)
from src.core.events.streams import (
    IDEMPOTENCY_PREFIX,
    IDEMPOTENCY_PROCESSING_PREFIX,
)
from src.core.patrol.change_detector import NetworkChange
from src.core.patrol.change_event_repository import (
    ChangeEventRepository,
    deterministic_change_id,
)
from src.core.patrol.change_policy_repository import ChangePolicyRepository
from src.core.patrol.repository import PatrolRepository
from src.core.patrol.event_builder import NetworkEvent
from src.core.parser.repository import ParserRepository
from src.core.workflows.repository import create_notification
from src.gateway.audit_service import write_audit_log
from src.infrastructure.db.models import (
    AuditLogRecord,
    ChangePolicyRecord,
    ChangePolicyVersionRecord,
    ParserJobSample,
    NetworkChangeRecord,
    NetworkEventRecord,
    Notification,
    ParserOutboxEvent,
    ParserTemplateJob,
    PatrolRun,
)
from src.infrastructure.db.postgres import get_db_session

pytestmark = pytest.mark.integration


def _redis_or_skip():
    client = get_redis()
    if client is None:
        pytest.skip("Redis is unavailable")
    try:
        client.ping()
    except Exception as exc:
        pytest.skip(f"Redis is unavailable: {exc}")
    return client


def test_real_redis_processing_claim_is_atomic():
    client = _redis_or_skip()
    group = f"fault-injection-{uuid.uuid4().hex}"
    occurrence_id = uuid.uuid4().hex
    owners = [uuid.uuid4().hex for _ in range(16)]
    try:
        with ThreadPoolExecutor(max_workers=len(owners)) as executor:
            claimed = list(
                executor.map(
                    lambda owner: claim_processing(
                        group,
                        occurrence_id,
                        owner_token=owner,
                        lease_ms=30_000,
                    ),
                    owners,
                )
            )
        assert claimed.count("claimed") == 1
        assert claimed.count("busy") == len(owners) - 1
        winner = owners[claimed.index("claimed")]

        mark_processed(group, occurrence_id, owner_token=winner)

        assert is_processed(group, occurrence_id) is True
        assert (
            claim_processing(
                group,
                occurrence_id,
                owner_token=uuid.uuid4().hex,
                lease_ms=30_000,
            )
            == "completed"
        )
    finally:
        client.delete(
            f"{IDEMPOTENCY_PREFIX}{group}:{occurrence_id}",
            f"{IDEMPOTENCY_PROCESSING_PREFIX}{group}:{occurrence_id}",
        )


def test_real_redis_processing_lease_can_be_renewed():
    client = _redis_or_skip()
    group = f"lease-renewal-{uuid.uuid4().hex}"
    event_id = uuid.uuid4().hex
    owner = uuid.uuid4().hex
    try:
        assert claim_processing(group, event_id, owner_token=owner, lease_ms=1_000) == "claimed"
        time.sleep(0.7)
        assert renew_processing(
            group, event_id, owner_token=owner, lease_ms=1_000
        ) is True
        time.sleep(0.5)
        assert (
            claim_processing(
                group, event_id, owner_token=uuid.uuid4().hex, lease_ms=1_000
            )
            == "busy"
        )
    finally:
        client.delete(f"{IDEMPOTENCY_PROCESSING_PREFIX}{group}:{event_id}")


def test_real_redis_pending_message_is_reclaimed_after_worker_loss():
    client = _redis_or_skip()
    stream = f"netops:fault-injection:{uuid.uuid4().hex}"
    group = f"group-{uuid.uuid4().hex}"
    try:
        client.xgroup_create(stream, group, id="0", mkstream=True)
        message_id = client.xadd(stream, {"event": "worker-loss"})
        delivered = client.xreadgroup(
            groupname=group,
            consumername="dead-worker",
            streams={stream: ">"},
            count=1,
        )
        assert delivered
        time.sleep(0.02)

        claimed = client.xautoclaim(
            stream,
            group,
            "replacement-worker",
            min_idle_time=10,
            start_id="0-0",
            count=1,
        )

        messages = claimed[1]
        assert len(messages) == 1
        assert str(messages[0][0]) == str(message_id)
        assert client.xack(stream, group, message_id) == 1
    finally:
        client.delete(stream)


def test_real_broker_redelivery_fences_live_database_lease(postgres_available):
    del postgres_available
    broker_url = "amqp://guest:guest@localhost:5672//"
    queue_name = f"netops-fault-injection-{uuid.uuid4().hex}"
    logical_run_id = f"patrol-{uuid.uuid4()}"
    repository = PatrolRepository()
    first_connection = None
    second_connection = None
    try:
        repository.create_run(
            run_id=logical_run_id,
            source="device-patrol",
            mode="production",
            logical_run_id=logical_run_id,
            worker_id="worker-A",
            lease_seconds=900,
        )
        first_connection = Connection(broker_url)
        first_connection.connect()
        first_queue = first_connection.SimpleQueue(queue_name)
        first_queue.put({"logical_run_id": logical_run_id})
        first_delivery = first_queue.get(block=True, timeout=5)
        assert first_delivery.delivery_info["redelivered"] is False

        # Abrupt channel/connection loss leaves the delivery unacknowledged.
        first_connection.close()
        first_connection = None

        second_connection = Connection(broker_url)
        second_connection.connect()
        second_queue = second_connection.SimpleQueue(queue_name)
        redelivery = second_queue.get(block=True, timeout=5)
        assert redelivery.delivery_info["redelivered"] is True

        decision = repository.prepare_redelivery_attempt(
            logical_run_id,
            worker_id="worker-B",
        )

        assert decision.action == "allocate_new_attempt"
        assert repository.get_run(logical_run_id)["status"] == "abandoned"
        assert repository.get_run(decision.run_id)["worker_id"] == "worker-B"
        redelivery.ack()
        second_connection.close()
        second_connection = None
    finally:
        if first_connection is not None:
            first_connection.close()
        if second_connection is not None:
            second_connection.close()
        try:
            with Connection(broker_url) as cleanup_connection:
                cleanup_connection.channel().queue_delete(queue=queue_name)
        except Exception:
            pass
        with get_db_session() as db:
            db.query(PatrolRun).filter(PatrolRun.logical_run_id == logical_run_id).delete(
                synchronize_session=False
            )


def test_irreversible_consumers_have_durable_occurrence_receipts(postgres_available):
    del postgres_available
    audit_key = f"audit-consumer:{uuid.uuid4().hex}"
    notification_key = f"notify-consumer:{uuid.uuid4().hex}"
    try:
        for _ in range(2):
            write_audit_log(
                action="fault_injection",
                resource_type="event_occurrence",
                resource_id=audit_key,
                idempotency_key=audit_key,
                raise_on_error=True,
            )
            create_notification(
                user_id="fault-injection-user",
                title="Occurrence handled",
                idempotency_key=notification_key,
            )
        with get_db_session() as db:
            assert (
                db.query(AuditLogRecord)
                .filter(AuditLogRecord.idempotency_key == audit_key)
                .count()
                == 1
            )
            assert (
                db.query(Notification)
                .filter(Notification.idempotency_key == notification_key)
                .count()
                == 1
            )
    finally:
        with get_db_session() as db:
            db.query(AuditLogRecord).filter(
                AuditLogRecord.idempotency_key == audit_key
            ).delete(synchronize_session=False)
            db.query(Notification).filter(
                Notification.idempotency_key == notification_key
            ).delete(synchronize_session=False)


def test_change_policy_activation_is_globally_unique_per_observation_type(
    postgres_available,
):
    del postgres_available
    observation_type = f"pytest.optical.{uuid.uuid4().hex}"
    repository = ChangePolicyRepository()
    versions = []
    try:
        for suffix, threshold in (("a", -17), ("b", -18)):
            versions.append(
                repository.create_policy_version(
                    name=f"pytest-policy-{suffix}-{uuid.uuid4().hex}",
                    policy={
                        "observation_type": observation_type,
                        "semantic_type": "gauge",
                        "compare_mode": "threshold_and_deadband",
                        "thresholds": {"low": threshold},
                        "recovery_threshold": threshold + 2,
                    },
                    validation_report={"success": True},
                    created_by="fault-injection",
                )
            )
        repository.activate_policy_version(versions[0]["version_id"])
        repository.activate_policy_version(versions[1]["version_id"])

        active = [
            item
            for item in repository.list_active_policies()
            if item["observation_type"] == observation_type
        ]
        assert len(active) == 1
        assert active[0]["version_id"] == versions[1]["version_id"]
        with get_db_session() as db:
            first = db.get(ChangePolicyVersionRecord, versions[0]["version_id"])
            assert first.status == "deprecated"
    finally:
        policy_ids = [item["policy_id"] for item in versions]
        with get_db_session() as db:
            db.query(ChangePolicyVersionRecord).filter(
                ChangePolicyVersionRecord.policy_id.in_(policy_ids)
            ).delete(synchronize_session=False)
            db.query(ChangePolicyRecord).filter(
                ChangePolicyRecord.id.in_(policy_ids)
            ).delete(synchronize_session=False)


def test_explicit_replay_has_distinct_change_derivation_identity(postgres_available):
    del postgres_available
    run_id = f"replay-{uuid.uuid4()}"
    repository = ChangeEventRepository()

    def change(revision: str, bundle: str) -> NetworkChange:
        return NetworkChange(
            change_id=str(uuid.uuid4()),
            run_id=run_id,
            device_id="SW1",
            command="show interface status",
            entity_type="interface_l2",
            entity_key='["Gi1/0/1"]',
            field="status",
            old_value="up",
            new_value="down",
            change_type="modified",
            previous_snapshot_id="snapshot-before",
            current_snapshot_id="snapshot-after",
            detected_at="2026-07-11T01:00:00+00:00",
            processed_at="2026-07-11T01:00:00+00:00",
            analysis_revision=revision,
            rule_bundle_digest=bundle,
        )

    try:
        assert repository.save_changes([change("original", "bundle-v1")]) == 1
        assert repository.save_changes([change("replay-v2", "bundle-v2")]) == 1
        with get_db_session() as db:
            rows = (
                db.query(NetworkChangeRecord)
                .filter(NetworkChangeRecord.run_id == run_id)
                .order_by(NetworkChangeRecord.analysis_revision)
                .all()
            )
            assert len(rows) == 2
            assert {row.analysis_revision for row in rows} == {"original", "replay-v2"}
            assert {row.rule_bundle_digest for row in rows} == {
                "bundle-v1",
                "bundle-v2",
            }
    finally:
        with get_db_session() as db:
            db.query(NetworkChangeRecord).filter(
                NetworkChangeRecord.run_id == run_id
            ).delete(synchronize_session=False)


def test_change_retry_uses_canonical_deterministic_id(postgres_available):
    del postgres_available
    run_id = f"deterministic-change-{uuid.uuid4()}"
    repository = ChangeEventRepository()

    def candidate() -> NetworkChange:
        return NetworkChange(
            change_id=str(uuid.uuid4()),
            run_id=run_id,
            device_id="SW1",
            command="show interface status",
            entity_type="interface_l2",
            entity_key='["Gi1/0/1"]',
            field="status",
            old_value="up",
            new_value="down",
            change_type="modified",
            previous_snapshot_id="before",
            current_snapshot_id="after",
            detected_at="2026-07-11T01:00:00+00:00",
            analysis_revision="original",
            rule_bundle_digest="bundle-v1",
        )

    first = candidate()
    second = candidate()
    expected_id = deterministic_change_id(first)
    try:
        assert repository.save_changes([first]) == 1
        assert repository.save_changes([second]) == 0
        with get_db_session() as db:
            row = db.query(NetworkChangeRecord).filter_by(run_id=run_id).one()
            assert row.id == expected_id == deterministic_change_id(second)
    finally:
        with get_db_session() as db:
            db.query(NetworkChangeRecord).filter_by(run_id=run_id).delete()


def test_parser_outbox_claim_has_single_publisher_owner(postgres_available):
    del postgres_available
    event_id = str(uuid.uuid4())
    with get_db_session() as db:
        db.add(
            ParserOutboxEvent(
                id=event_id,
                event_type="parser.template.activated",
                aggregate_id=str(uuid.uuid4()),
                payload_json={"template_id": "template-1"},
                status="pending",
                created_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
            )
        )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(
                executor.map(
                    lambda _index: ParserRepository().claim_pending_outbox(limit=1),
                    range(2),
                )
            )
        claimed = [row for batch in claims for row in batch if row.id == event_id]
        assert len(claimed) == 1
        assert claimed[0].claim_token
    finally:
        with get_db_session() as db:
            db.query(ParserOutboxEvent).filter_by(id=event_id).delete()


def test_production_parser_candidate_creation_is_concurrently_deduplicated(postgres_available):
    del postgres_available
    token = uuid.uuid4().hex
    run_id = f"parser-concurrency-{token}"
    patrol = PatrolRepository()
    patrol.create_run(run_id=run_id, mode="production", source="fault-injection")
    snapshot_ids = []
    for index in range(2):
        snapshot_id, _ = patrol.save_snapshot_idempotent(
            run_id=run_id,
            device_id=f"SW{index + 1}",
            device_name=f"SW{index + 1}",
            vendor="H3C",
            model="S6860-54HF",
            command="display interface brief",
            command_canonical="display interface brief",
            raw_text=f"GE1/0/{index + 1} UP UP\n",
            parse_status="template_missing",
            execution_item_id=f"item-{index}-{token}",
            import_fingerprint=f"fingerprint-{index}-{token}",
        )
        snapshot_ids.append(snapshot_id)

    def create(item: tuple[str, str]) -> str:
        snapshot_id, device_id = item
        return ParserRepository().create_candidate(
            job_type="CREATE_TEMPLATE",
            vendor="H3C",
            model="S6860-54HF",
            family="Comware7",
            command="display interface brief",
            snapshot_id=snapshot_id,
            device_id=device_id,
            failure_code="template_missing",
        )

    job_ids: list[str] = []
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            job_ids = list(executor.map(create, zip(snapshot_ids, ["SW1", "SW2"])))
        assert len(set(job_ids)) == 1
        with get_db_session() as db:
            assert db.query(ParserJobSample).filter_by(job_id=job_ids[0]).count() == 2
    finally:
        with get_db_session() as db:
            if job_ids:
                db.query(ParserOutboxEvent).filter_by(aggregate_id=job_ids[0]).delete()
                db.query(ParserJobSample).filter_by(job_id=job_ids[0]).delete()
                db.query(ParserTemplateJob).filter_by(id=job_ids[0]).delete()
            db.query(PatrolRun).filter_by(id=run_id).delete()


def test_event_batches_acquire_correlation_locks_in_global_order(postgres_available):
    del postgres_available
    suffix = uuid.uuid4().hex

    def event(rule_id: str, correlation_key: str, change_id: str) -> NetworkEvent:
        return NetworkEvent(
            event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{suffix}|{change_id}")),
            event_type="SharedFault",
            severity="major",
            device_id="SW1",
            entity_type="interface_l2",
            entity_key='["Gi1/0/1"]',
            source_change_id=change_id,
            run_id=None,
            occurred_at="2026-07-11T01:00:00+00:00",
            status="open",
            payload={
                "correlation_key": correlation_key,
                "rule": {"rule_id": rule_id, "version": 1, "event_class": "fault"},
            },
        )

    a1 = event("FaultA", f"A-{suffix}", f"a1-{suffix}")
    b1 = event("FaultB", f"B-{suffix}", f"b1-{suffix}")
    a2 = event("FaultA", f"A-{suffix}", f"a2-{suffix}")
    b2 = event("FaultB", f"B-{suffix}", f"b2-{suffix}")
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda batch: ChangeEventRepository().save_events(batch),
                    ([a1, b1], [b2, a2]),
                )
            )
        assert sum(results) == 2
    finally:
        with get_db_session() as db:
            db.query(NetworkEventRecord).filter(
                NetworkEventRecord.correlation_key.in_([f"A-{suffix}", f"B-{suffix}"])
            ).delete(synchronize_session=False)

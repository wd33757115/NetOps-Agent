# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""EventBus 与 Consumer 单元测试。"""

from unittest.mock import patch

from src.core.events.bus import EventBus
from src.core.events.domain_event import DomainEvent
from src.core.events.publishers import publish_skill_execution_event
from src.core.events.streams import STREAM_SKILL_EXECUTION
from src.core.skills.result import ExecutionContext, SkillExecutionResult, SkillStatus


class FakeRedis:
    def __init__(self):
        self.streams: dict[str, list[tuple[str, dict]]] = {}
        self.groups: set[tuple[str, str]] = set()
        self.kv: dict[str, str] = {}
        self._id_counter = 0
        self.pending: dict[tuple[str, str], dict[str, tuple[str, dict]]] = {}
        self.acked: list[tuple[str, str, str]] = []

    def xadd(self, stream, fields, maxlen=None, approximate=True):
        self._id_counter += 1
        msg_id = f"{self._id_counter}-0"
        self.streams.setdefault(stream, []).append((msg_id, fields))
        if len(self.streams[stream]) > (maxlen or 100000):
            self.streams[stream] = self.streams[stream][-(maxlen or 100000) :]
        return msg_id

    def xgroup_create(self, stream, group, id="0", mkstream=True):
        if mkstream:
            self.streams.setdefault(stream, [])
        key = (stream, group)
        if key in self.groups:
            raise Exception("BUSYGROUP Consumer Group name already exists")
        self.groups.add(key)

    def xreadgroup(self, groupname, consumername, streams, count=1, block=0):
        out = []
        for stream, marker in streams.items():
            if marker != ">":
                continue
            pending = [(mid, fld) for mid, fld in self.streams.get(stream, []) if True]
            if not pending:
                continue
            # 简化：每次返回最早未 ack 的一条（测试用）
            ack_key = f"ack:{stream}:{groupname}"
            idx = int(self.kv.get(ack_key, "0"))
            items = self.streams.get(stream, [])
            if idx >= len(items):
                continue
            mid, fld = items[idx]
            self.kv[ack_key] = str(idx + 1)
            self.pending.setdefault((stream, groupname), {})[mid] = (consumername, fld)
            out.append((stream, [(mid, fld)]))
        return out or None

    def xack(self, stream, group, msg_id):
        self.pending.setdefault((stream, group), {}).pop(msg_id, None)
        self.acked.append((stream, group, msg_id))
        return 1

    def xautoclaim(
        self,
        stream,
        group,
        consumer,
        min_idle_time,
        start_id="0-0",
        count=1,
    ):
        messages = []
        for msg_id, (_owner, fields) in list(
            self.pending.setdefault((stream, group), {}).items()
        )[:count]:
            self.pending[(stream, group)][msg_id] = (consumer, fields)
            messages.append((msg_id, fields))
        return ("0-0", messages, [])

    def set(self, key, value, ex=None):
        self.kv[key] = value

    def exists(self, key):
        return 1 if key in self.kv else 0

    def xrevrange(self, stream, count=50):
        items = self.streams.get(stream, [])
        return list(reversed(items[-count:]))

    def xinfo_groups(self, stream):
        return [{"name": g[1], "pending": 0, "consumers": 1, "last-delivered-id": "0-0"} for g in self.groups if g[0] == stream]


@patch("src.core.events.bus.get_redis")
@patch("src.common.config.get_settings")
def test_event_bus_publish(mock_settings, mock_get_redis):
    fake = FakeRedis()
    mock_get_redis.return_value = fake
    mock_settings.return_value.EVENT_BUS_ENABLED = True
    mock_settings.return_value.EVENT_BUS_STREAM_MAXLEN = 1000

    event = DomainEvent(event_type="skill.executed", correlation_id="msg-1", payload={"skill_name": "x"})
    msg_id = EventBus.publish(STREAM_SKILL_EXECUTION, event)
    assert msg_id is not None
    assert len(fake.streams[STREAM_SKILL_EXECUTION]) == 1


@patch("src.core.events.bus.get_redis")
@patch("src.common.config.get_settings")
def test_publish_skill_execution_event(mock_settings, mock_get_redis):
    fake = FakeRedis()
    mock_get_redis.return_value = fake
    mock_settings.return_value.EVENT_BUS_ENABLED = True
    mock_settings.return_value.EVENT_BUS_STREAM_MAXLEN = 1000

    ser = SkillExecutionResult(
        skill_name="firewall-policy-generator",
        status=SkillStatus.SUCCESS,
        message="ok",
        context=ExecutionContext(source="chat", user_id="u1", message_id="m1"),
    )
    publish_skill_execution_event(ser)
    assert STREAM_SKILL_EXECUTION in fake.streams


@patch("src.core.events.consumers.audit.write_audit_log")
def test_audit_consumer_handles_skill_event(mock_audit):
    from src.core.events.consumers.audit import AuditConsumer

    event = DomainEvent(
        event_type="skill.executed",
        correlation_id="m1",
        source="chat",
        payload={"skill_name": "test-skill", "execution_id": "e1", "status": "success", "user_id": "u1"},
    )
    AuditConsumer().handle(event)
    mock_audit.assert_called_once()
    assert mock_audit.call_args.kwargs["action"] == "skill_execute"


class RecordingConsumer:
    name = "recording"
    group = "recording-group"
    streams = [STREAM_SKILL_EXECUTION]

    def __init__(self, *, fail: bool = False):
        from src.core.events.consumers.base import StreamConsumer

        self._delegate_type = type(
            "_RecordingStreamConsumer",
            (StreamConsumer,),
            {
                "name": self.name,
                "group": self.group,
                "streams": self.streams,
                "handle": lambda delegate, event: self._handle(event),
            },
        )
        self.delegate = self._delegate_type()
        self.fail = fail
        self.handled: list[str] = []

    def _handle(self, event):
        if self.fail:
            raise RuntimeError("consumer failed")
        self.handled.append(event.idempotency_key)


def _consumer_settings():
    return type(
        "Settings",
        (),
        {
            "EVENT_BUS_CONSUMER_BATCH_SIZE": 20,
            "EVENT_BUS_CONSUMER_BLOCK_MS": 0,
            "EVENT_BUS_PENDING_MIN_IDLE_MS": 1,
            "EVENT_BUS_RECLAIM_BATCH_SIZE": 20,
        },
    )()


def test_consumer_idempotency_is_occurrence_scoped():
    fake = FakeRedis()
    consumer = RecordingConsumer()
    events = [
        DomainEvent(
            event_id="aggregate-1",
            event_type="network.interface_down",
            payload={"occurrence_id": occurrence_id},
        )
        for occurrence_id in ("occ-1", "occ-2", "occ-3", "occ-2")
    ]
    for event in events:
        fake.xadd(STREAM_SKILL_EXECUTION, event.to_stream_fields())

    with (
        patch("src.core.events.consumers.base.get_redis", return_value=fake),
        patch("src.core.events.idempotency.get_redis", return_value=fake),
        patch("src.core.events.consumers.base.get_settings", return_value=_consumer_settings()),
    ):
        for _ in events:
            consumer.delegate.poll_once()

    assert consumer.handled == ["occ-1", "occ-2", "occ-3"]
    assert len(fake.acked) == 4


def test_consumer_reclaims_pending_message_after_worker_loss():
    fake = FakeRedis()
    event = DomainEvent(event_type="skill.executed", payload={"occurrence_id": "occ-lost"})
    fake.xadd(STREAM_SKILL_EXECUTION, event.to_stream_fields())
    fake.xreadgroup(
        groupname=RecordingConsumer.group,
        consumername="dead-worker",
        streams={STREAM_SKILL_EXECUTION: ">"},
    )
    consumer = RecordingConsumer()

    with (
        patch("src.core.events.consumers.base.get_redis", return_value=fake),
        patch("src.core.events.idempotency.get_redis", return_value=fake),
        patch("src.core.events.consumers.base.get_settings", return_value=_consumer_settings()),
    ):
        assert consumer.delegate.poll_once() == 1

    assert consumer.handled == ["occ-lost"]
    assert not fake.pending[(STREAM_SKILL_EXECUTION, RecordingConsumer.group)]


def test_consumer_keeps_original_pending_when_dlq_publish_fails():
    fake = FakeRedis()
    event = DomainEvent(event_type="skill.executed", payload={"occurrence_id": "occ-bad"})
    fake.xadd(STREAM_SKILL_EXECUTION, event.to_stream_fields())
    consumer = RecordingConsumer(fail=True)

    with (
        patch("src.core.events.consumers.base.get_redis", return_value=fake),
        patch("src.core.events.idempotency.get_redis", return_value=fake),
        patch("src.core.events.consumers.base.get_settings", return_value=_consumer_settings()),
        patch("src.core.events.consumers.base.EventBus.publish_dlq", return_value=None),
    ):
        assert consumer.delegate.poll_once() == 0

    assert len(fake.pending[(STREAM_SKILL_EXECUTION, RecordingConsumer.group)]) == 1
    assert fake.acked == []

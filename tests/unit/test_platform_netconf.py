"""Read-only NETCONF over SSH on the device access platform (no live SSH)."""

from contextlib import contextmanager
from dataclasses import replace

import pytest

from src.platform.device_access import governance
from src.platform.device_access.contracts import (
    AccessContext,
    AccessGrant,
    ConnectionProfile,
    DeviceAccessError,
    Target,
)
from src.platform.device_access.netconf import BASE_1_0, BASE_1_1, NetconfSession, check_filter
from src.platform.device_access.service import DeviceAccessService

EOM = b"]]>]]>"
NS = "urn:ietf:params:xml:ns:netconf:base:1.0"
ARP = '<top xmlns="http://www.h3c.com/netconf/data:1.0"><ARP><ArpTable/></ARP></top>'


def hello(*bases):
    caps = "".join(f"<capability>{base}</capability>" for base in bases)
    return f'<hello xmlns="{NS}"><capabilities>{caps}</capabilities><session-id>7</session-id></hello>'


def reply(message_id, body):
    return f'<rpc-reply xmlns="{NS}" message-id="{message_id}">{body}</rpc-reply>'


def chunked(text, *sizes):
    data, out, at = text.encode(), b"", 0
    for size in sizes:
        out += b"\n#%d\n" % size + data[at : at + size]
        at += size
    if at < len(data):
        out += b"\n#%d\n" % (len(data) - at) + data[at:]
    return out + b"\n##\n"


class Channel:
    """Feeds the session byte pieces as recv() results; records what it sends."""

    def __init__(self, *pieces):
        self.pieces, self.sent, self.closed = list(pieces), [], False

    def settimeout(self, value):
        pass

    def recv(self, size):
        if not self.pieces:
            raise TimeoutError()
        piece = self.pieces.pop(0)
        if len(piece) > size:
            self.pieces.insert(0, piece[size:])
            piece = piece[:size]
        return piece

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


class Client:
    closed = False

    def close(self):
        self.closed = True


class Operation:
    def __init__(self):
        self.events, self.secrets = [], ()

    def event(self, kind, **detail):
        self.events.append((kind, detail))


def session(*pieces, limit=1024 * 1024):
    profile = ConnectionProfile("p", "operator", password="x", port=830, max_output_bytes=limit)
    return NetconfSession(Client(), Channel(*pieces), profile, Operation())


# --- filters ---------------------------------------------------------------------------


def test_a_filter_is_required_and_must_be_plain_data_nodes():
    assert check_filter(f"  {ARP}  ") == ARP
    assert check_filter("<a/><b/>") == "<a/><b/>"  # several top-level nodes
    for bad in ("", "   ", None, "<a>", "text only", f'<a xmlns="{NS}"><b/></a>',
                f'<top><edit-config xmlns="{NS}"/></top>'):
        with pytest.raises(DeviceAccessError) as error:
            check_filter(bad)
        assert error.value.failure_code == "invalid_request"


def test_a_filter_with_a_dtd_or_entities_is_refused():
    bomb = '<!DOCTYPE a [<!ENTITY x "xxxxxxxxxx"><!ENTITY y "&x;&x;&x;">]><a>&y;</a>'
    with pytest.raises(DeviceAccessError):
        check_filter(bomb)
    with pytest.raises(DeviceAccessError):
        check_filter("<a>" + "x" * (64 * 1024) + "</a>")


# --- framing and RPCs ------------------------------------------------------------------


def test_base_1_0_hello_get_and_audit():
    data = '<data><top xmlns="http://www.h3c.com/netconf/data:1.0"><ARP/></top></data>'
    s = session(hello(BASE_1_0).encode() + EOM, reply(1, data).encode()[:20],
                reply(1, data).encode()[20:] + EOM)
    assert s.hello(5) == (BASE_1_0,) and not s.chunked
    client_hello = s.channel.sent[0]
    assert client_hello.endswith(EOM) and BASE_1_1.encode() in client_hello
    result = s.get(ARP)
    assert result.startswith(f"<ns0:data xmlns:ns0=\"{NS}\"") and "ARP" in result
    request = s.channel.sent[1].decode()
    assert request.endswith("]]>]]>") and 'message-id="1"' in request
    assert f'<get><filter type="subtree">{ARP}</filter></get>' in request
    kinds = [kind for kind, _ in s.operation.events]
    assert kinds == ["netconf_rpc_requested", "netconf_rpc_finished"]
    finished = s.operation.events[1][1]
    assert finished["rpc"] == "get" and finished["reply_bytes"] > 0 and "reply_sha256" in finished


def test_base_1_1_is_chunked_both_ways():
    data = "<data><top><Route/></top></data>"
    s = session(hello(BASE_1_0, BASE_1_1).encode() + EOM, chunked(reply(1, data), 7, 3)[:9],
                chunked(reply(1, data), 7, 3)[9:])
    s.hello(5)
    assert s.chunked
    assert "Route" in s.get_config("<top><Route/></top>")
    sent = s.channel.sent[1]
    assert sent.startswith(b"\n#") and sent.endswith(b"\n##\n")
    assert b"<get-config><source><running/></source>" in sent


def test_an_rpc_error_is_reported_and_the_session_stays_usable():
    error = (f'<rpc-error><error-type>application</error-type><error-tag>invalid-value</error-tag>'
             f'<error-severity>error</error-severity><error-message>no such table</error-message></rpc-error>')
    s = session(hello(BASE_1_0).encode() + EOM, reply(1, error).encode() + EOM,
                reply(2, "<data/>").encode() + EOM)
    s.hello(5)
    with pytest.raises(DeviceAccessError) as failure:
        s.get(ARP)
    assert failure.value.failure_code == "netconf_rpc_error" and "no such table" in str(failure.value)
    assert s.usable and s.get(ARP).endswith("/>")
    assert s.operation.events[1][1]["failure_code"] == "netconf_rpc_error"


@pytest.mark.parametrize(
    "answer,code",
    [
        (reply(5, "<data/>").encode() + EOM, "output_incomplete"),  # not our message-id
        (b"<rpc-reply" + b"x" * 300, "output_limit"),
        (b"", "timeout"),
    ],
)
def test_a_broken_reply_retires_the_session(answer, code):
    s = session(hello(BASE_1_0).encode() + EOM, *([answer] if answer else []), limit=256)
    s.hello(5)
    with pytest.raises(DeviceAccessError) as failure:
        s.get(ARP)
    assert failure.value.failure_code == code and not s.usable
    with pytest.raises(DeviceAccessError):
        s.get(ARP)


def test_only_filtered_reads_leave_the_session():
    s = session(hello(BASE_1_0).encode() + EOM)
    s.hello(5)
    with pytest.raises(DeviceAccessError):
        s.get_config(ARP, source="url")
    with pytest.raises(DeviceAccessError):
        s.get("")
    assert len(s.channel.sent) == 1  # only the hello went out


def test_a_device_without_a_netconf_base_is_refused():
    s = session(hello("urn:example:other").encode() + EOM)
    with pytest.raises(DeviceAccessError) as failure:
        s.hello(5)
    assert failure.value.failure_code == "platform_unsupported"


def test_close_sends_close_session_and_closes_the_transport():
    s = session(hello(BASE_1_0).encode() + EOM, reply(1, "<ok/>").encode() + EOM)
    s.hello(5)
    s.close()
    assert b"<close-session/>" in s.channel.sent[-1] and s.channel.closed and s.client.closed


# --- the service's grant ---------------------------------------------------------------


class Admission:
    held = False

    @contextmanager
    def hold(self, target, profile):
        self.held = True
        yield lambda: None


class Connector:
    def __init__(self, session):
        self.session, self.calls = session, []

    def connect(self, target, profile, operation):
        self.calls.append((target.host, profile.port))
        self.session.operation = operation
        return self.session


@pytest.fixture
def world(monkeypatch):
    events = []
    monkeypatch.setattr(governance, "write_event", lambda **row: events.append(row) or "audit-1")
    target = Target("br1", "192.0.2.11", display_name="BR1")
    profile = ConnectionProfile("netconf", "operator", password="sensitive", port=830)
    fake = session(reply(1, "<data/>").encode() + EOM)
    fake.capabilities = (BASE_1_0,)
    connector = Connector(fake)
    service = DeviceAccessService(
        target_resolver=lambda ref: target,
        profile_resolver=lambda ref: profile,
        admission=Admission(),
        netconf=connector,
    )
    context = AccessContext("alice", "netconf-api", (AccessGrant("br1", "netconf", action="device.netconf"),))
    return locals()


def test_a_netconf_session_needs_its_own_grant(world):
    service, context = world["service"], world["context"]
    for denied in (
        replace(context, grants=(AccessGrant("br1", "netconf"),)),  # device.collect is not enough
        replace(context, grants=(AccessGrant("br1", "netconf", action="device.terminal"),)),
        replace(context, actor_id=""),
    ):
        with pytest.raises(DeviceAccessError) as failure:
            with service.netconf(denied, "br1", "netconf"):
                pass
        assert failure.value.failure_code == "not_authorized"
    assert world["connector"].calls == []


def test_a_granted_session_is_admitted_audited_and_closed(world):
    service, context = world["service"], world["context"]
    with service.netconf(context, "br1", "netconf") as (netconf, guard):
        guard()
        assert netconf.get(ARP) == f'<ns0:data xmlns:ns0="{NS}" />'
    assert world["connector"].calls == [("192.0.2.11", 830)]
    assert service.admission.held and world["fake"].channel.closed
    kinds = [row["kind"] for row in world["events"]]
    assert kinds[:2] == ["connecting", "connected"] and kinds[-1] == "disconnected"
    assert "netconf_rpc_finished" in kinds
    assert all(row["source_app"] == "netconf-api" for row in world["events"])

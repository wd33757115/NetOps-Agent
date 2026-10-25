"""NETCONF over SSH (RFC 6241, RFC 6242), read-only.

A NETCONF session is the SSH platform's other channel: the same strict host-key trust
(keys are pinned per port, 830 like any other), credentials, admission and audit as a CLI
session. Only <get> and <get-config> with a subtree filter are sent, and the RPC is built
here from the filter: nothing a caller passes can become an edit, a lock, an action or a
CLI command. A filter is required, so a whole datastore (with its account secrets) is
never read. Replies are parsed with defusedxml.
"""

from __future__ import annotations

import hashlib
import time
from threading import Event, Timer
from xml.sax.saxutils import quoteattr

from defusedxml import ElementTree

from .contracts import DeviceAccessError
from .errors import failure_code

NETCONF_PORT = 830
BASE = "urn:ietf:params:xml:ns:netconf:base:1.0"
BASE_1_0 = "urn:ietf:params:netconf:base:1.0"
BASE_1_1 = "urn:ietf:params:netconf:base:1.1"
END_OF_MESSAGE = b"]]>]]>"
MAX_FILTER_BYTES = 64 * 1024
SOURCES = frozenset({"running", "candidate", "startup"})


def check_filter(subtree):
    """A subtree filter as sent: well-formed XML of data nodes, none in NETCONF's own namespace."""
    if not isinstance(subtree, str) or not subtree.strip():
        raise DeviceAccessError("invalid_request", "NETCONF 读取必须带过滤条件")
    text = subtree.strip()
    if len(text.encode("utf-8")) > MAX_FILTER_BYTES:
        raise DeviceAccessError("invalid_request", "NETCONF 过滤条件过大")
    try:
        # Wrapped so several top-level nodes parse; defusedxml refuses DTDs and entities.
        root = ElementTree.fromstring(f"<filter>{text}</filter>")
    except Exception:
        raise DeviceAccessError("invalid_request", "NETCONF 过滤条件不是合法 XML") from None
    if not len(root) or any(child.tag.startswith(f"{{{BASE}}}") for child in root.iter()):
        raise DeviceAccessError("invalid_request", "NETCONF 过滤条件只能包含数据节点")
    return text


def rpc_error(reply):
    """The first <rpc-error> message of a reply, or None."""
    for error in reply.iter(f"{{{BASE}}}rpc-error"):
        severity = error.findtext(f"{{{BASE}}}error-severity") or "error"
        if severity != "warning":
            tag = error.findtext(f"{{{BASE}}}error-tag") or "error"
            message = (error.findtext(f"{{{BASE}}}error-message") or "").strip()
            return f"{tag}: {message}" if message else tag
    return None


class NetconfSession:
    """One NETCONF session on an SSH "netconf" subsystem channel. Not thread-safe."""

    def __init__(self, client, channel, profile, operation):
        self.client, self.channel, self.profile, self.operation = client, channel, profile, operation
        self.capabilities = ()
        self.chunked = False
        self.message_id = 0
        self.usable = True
        self._buffer = b""

    # --- framing (RFC 6242) -------------------------------------------------------

    def _recv(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DeviceAccessError("timeout", "NETCONF reply was not received before timeout")
        self.channel.settimeout(remaining)
        try:
            chunk = self.channel.recv(65536)
        except TimeoutError:
            raise DeviceAccessError("timeout", "NETCONF reply was not received before timeout") from None
        if not chunk:
            raise DeviceAccessError("connection_closed", "NETCONF channel closed before the reply")
        self._buffer += chunk
        if len(self._buffer) > self.profile.max_output_bytes:
            raise DeviceAccessError("output_limit", "NETCONF reply exceeds the byte limit")

    def _read_message(self, timeout):
        deadline = time.monotonic() + timeout
        if not self.chunked:
            while END_OF_MESSAGE not in self._buffer:
                self._recv(deadline)
            message, _, self._buffer = self._buffer.partition(END_OF_MESSAGE)
            return message
        # Chunked: "\n#<size>\n<data>" repeated, then "\n##\n" ends the message.
        message = b""
        while True:
            while not (self._buffer.startswith(b"\n#") and b"\n" in self._buffer[2:]):
                if len(self._buffer) >= 2 and not self._buffer.startswith(b"\n#"):
                    raise DeviceAccessError("output_incomplete", "NETCONF chunk framing is invalid")
                self._recv(deadline)
            end = self._buffer.index(b"\n", 2)
            header = self._buffer[2:end]
            if header == b"#":
                self._buffer = self._buffer[end + 1 :]
                return message
            if not header.isdigit() or not 0 < int(header) <= 4294967295:
                raise DeviceAccessError("output_incomplete", "NETCONF chunk framing is invalid")
            start, size = end + 1, int(header)
            while len(self._buffer) < start + size:
                self._recv(deadline)
            message += self._buffer[start : start + size]
            self._buffer = self._buffer[start + size :]
            if len(message) > self.profile.max_output_bytes:
                raise DeviceAccessError("output_limit", "NETCONF reply exceeds the byte limit")

    def _send(self, text):
        data = text.encode("utf-8")
        if self.chunked:
            data = b"\n#%d\n" % len(data) + data + b"\n##\n"
        else:
            data += END_OF_MESSAGE
        self.channel.sendall(data)

    # --- protocol -----------------------------------------------------------------

    def hello(self, timeout):
        server = ElementTree.fromstring(self._read_message(timeout))
        capabilities = tuple(
            (item.text or "").strip() for item in server.iter(f"{{{BASE}}}capability")
        )
        if BASE_1_0 not in capabilities and BASE_1_1 not in capabilities:
            raise DeviceAccessError("platform_unsupported", "device offers no NETCONF base")
        self._send(
            f'<?xml version="1.0" encoding="UTF-8"?><hello xmlns="{BASE}"><capabilities>'
            f"<capability>{BASE_1_0}</capability><capability>{BASE_1_1}</capability>"
            "</capabilities></hello>"
        )
        # RFC 6242: chunked framing once both ends announced base:1.1.
        self.chunked = BASE_1_1 in capabilities
        self.capabilities = capabilities
        return capabilities

    def get(self, subtree, *, timeout=60):
        """<get> state and configuration under a subtree filter; returns the <data> XML."""
        text = check_filter(subtree)
        return self._rpc("get", f'<get><filter type="subtree">{text}</filter></get>', text, timeout)

    def get_config(self, subtree, *, source="running", timeout=60):
        """<get-config> from a datastore under a subtree filter; returns the <data> XML."""
        if source not in SOURCES:
            raise DeviceAccessError("invalid_request", "unknown NETCONF datastore")
        text = check_filter(subtree)
        body = (
            f"<get-config><source><{source}/></source>"
            f'<filter type="subtree">{text}</filter></get-config>'
        )
        return self._rpc("get-config", body, text, timeout)

    def _rpc(self, name, body, subtree, timeout):
        if not self.usable:
            raise DeviceAccessError("connection_closed", "NETCONF session is no longer usable")
        self.message_id += 1
        ticket = {
            "rpc": name,
            "message_id": self.message_id,
            "filter": subtree[:4096],
            "filter_sha256": hashlib.sha256(subtree.encode("utf-8")).hexdigest(),
        }
        self.operation.event("netconf_rpc_requested", **ticket)
        started = time.monotonic()
        try:
            self._send(
                f'<?xml version="1.0" encoding="UTF-8"?><rpc message-id={quoteattr(str(self.message_id))}'
                f' xmlns="{BASE}">{body}</rpc>'
            )
            raw = self._read_message(timeout)
            reply = ElementTree.fromstring(raw)
            if reply.get("message-id") != str(self.message_id):
                raise DeviceAccessError("output_incomplete", "NETCONF reply does not match the request")
            error = rpc_error(reply)
            if error:
                raise DeviceAccessError("netconf_rpc_error", error[:500])
            data = reply.find(f"{{{BASE}}}data")
            text = ElementTree.tostring(data, encoding="unicode") if data is not None else ""
        except Exception as exc:
            if failure_code(exc) != "netconf_rpc_error":
                self.usable = False  # only a well-framed <rpc-error> leaves the stream in step
            self.operation.event(
                "netconf_rpc_finished",
                status="failed",
                **ticket,
                seconds=round(time.monotonic() - started, 3),
                failure_code=failure_code(exc),
            )
            raise
        self.operation.event(
            "netconf_rpc_finished",
            status="succeeded",
            **ticket,
            seconds=round(time.monotonic() - started, 3),
            reply_bytes=len(raw),
            reply_sha256=hashlib.sha256(raw).hexdigest(),
        )
        return text

    def close(self):
        if self.usable:
            try:
                self.message_id += 1
                self._send(
                    f'<rpc message-id="{self.message_id}" xmlns="{BASE}"><close-session/></rpc>'
                )
                self._read_message(5)
            except Exception:
                pass  # closing anyway
        self.usable = False
        try:
            self.channel.close()
        except (EOFError, OSError):
            pass
        finally:
            self.client.close()


class NetconfConnector:
    """Opens NETCONF sessions with the SSH platform's trust and credential rules."""

    def __init__(self, *, host_key_verifier=None):
        self.host_key_verifier = host_key_verifier

    def connect(self, target, profile, operation):
        import paramiko

        from src.platform.inventory.credentials import CredentialResolver

        from .host_keys import DatabaseHostKeyPolicy

        if not profile.ssh_strict or self.host_key_verifier is None:
            raise DeviceAccessError("host_key_rejected", "strict host-key verification is required")
        if not target.host:
            raise DeviceAccessError("invalid_target", "device management address is required")
        password = profile.password or CredentialResolver().resolve_password(profile.credential_ref)
        if not password:
            raise DeviceAccessError("credential_unavailable", "device credential is unavailable")
        if password not in operation.secrets:
            operation.secrets += (password,)
        client = paramiko.SSHClient()
        expired = Event()

        def expire():
            expired.set()
            client.close()

        timer = Timer(profile.session_setup_timeout, expire)
        timer.daemon = True
        try:
            timer.start()
            client.set_missing_host_key_policy(
                DatabaseHostKeyPolicy(target, profile, self.host_key_verifier)
            )
            client.connect(
                hostname=target.host,
                port=profile.port,
                username=profile.username,
                password=password,
                timeout=profile.conn_timeout,
                auth_timeout=profile.auth_timeout,
                banner_timeout=profile.banner_timeout,
                channel_timeout=profile.prompt_timeout,
                allow_agent=False,
                look_for_keys=False,
            )
            channel = client.get_transport().open_session(timeout=profile.prompt_timeout)
            channel.invoke_subsystem("netconf")
            session = NetconfSession(client, channel, profile, operation)
            session.hello(profile.prompt_timeout)
            return session
        except Exception:
            client.close()
            raise
        finally:
            timer.cancel()
            if timer.ident is not None:
                timer.join(timeout=1)
            if expired.is_set():
                client.close()
                raise DeviceAccessError("timeout", "NETCONF session setup exceeded its time budget")

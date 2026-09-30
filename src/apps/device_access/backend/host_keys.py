"""Host keys, SecureCRT style: confirm on first sight, warn when a key changes.

The terminal asks the user before trusting a key (``auto_trust=False``).
Topology discovery records a first-seen key itself (``auto_trust=True``).
A changed key always stops the connection until the user confirms it.
"""

import ipaddress

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.governance import current_operation, write_event
from src.platform.device_access.host_keys import fingerprint, known_hosts_line, parse_public_key

from .models import DOMAIN, HostKey, now


def metadata(row):
    return {
        "host": row.host,
        "port": row.port,
        "status": row.status,
        "key_type": row.key_type,
        "fingerprint": row.fingerprint_sha256,
        "observed_key_type": row.observed_key_type,
        "observed_fingerprint": row.observed_fingerprint,
        "first_seen": row.first_seen.isoformat() if row.first_seen else None,
        "changed_at": row.changed_at.isoformat() if row.changed_at else None,
    }


class HostKeyStore:
    def __init__(self, *, sessions=None):
        from .service import sessions as default_sessions

        self.sessions = sessions or default_sessions

    @staticmethod
    def _audit(session, kind, host, port, *, actor=None, **detail):
        operation = current_operation()
        if operation is not None:
            detail.update(
                session_id=operation.session_id,
                target_ref=operation.target_ref,
                device_name=operation.device_name,
            )
        write_event(
            actor_id=actor or (operation.actor_id if operation else "system"),
            source_app=operation.source_app if operation else "device-access",
            kind=kind,
            detail={"host": host, "port": port, **detail},
            session=session,
        )

    def verify(self, host, port, key, *, auto_trust=False):
        host, observed = str(ipaddress.ip_address(host)), fingerprint(key)
        for attempt in range(2):
            code = None
            try:
                with self.sessions() as session:
                    row = session.get(HostKey, (DOMAIN, host, port), with_for_update=True)
                    if row is None:
                        row = HostKey(domain=DOMAIN, host=host, port=port, first_seen=now())
                        session.add(row)
                    if row.status != "trusted":
                        row.key_type, row.public_key = key.get_name(), key.get_base64()
                        row.fingerprint_sha256 = observed
                        row.status = "trusted" if auto_trust else "pending"
                        row.revision = (row.revision or 0) + 1
                        self._audit(
                            session,
                            "host_key_observed",
                            host,
                            port,
                            fingerprint=observed,
                            auto_trust=auto_trust,
                        )
                        if not auto_trust:
                            code = "host_key_unknown"
                    elif row.fingerprint_sha256 != observed:
                        if row.observed_fingerprint != observed:
                            row.observed_key_type = key.get_name()
                            row.observed_public_key = key.get_base64()
                            row.observed_fingerprint, row.changed_at = observed, now()
                            row.revision += 1
                            self._audit(
                                session,
                                "host_key_changed",
                                host,
                                port,
                                previous_fingerprint=row.fingerprint_sha256,
                                fingerprint=observed,
                            )
                        code = "host_key_changed"
                    elif row.observed_fingerprint:
                        # The trusted key is back; forget the stale warning.
                        row.observed_key_type = row.observed_public_key = None
                        row.observed_fingerprint = None
                        self._audit(session, "host_key_restored", host, port, fingerprint=observed)
                # Committed before raising, so the user can confirm what was seen.
                if code == "host_key_unknown":
                    raise DeviceAccessError(code, "首次连接此设备，请确认主机密钥指纹")
                if code:
                    raise DeviceAccessError(code, "设备主机密钥已变化，请确认后再连接")
                return True
            except IntegrityError:
                if attempt:
                    raise DeviceAccessError("host_key_rejected", "主机密钥记录冲突") from None

    def get(self, host, port):
        with self.sessions() as session:
            row = session.get(HostKey, (DOMAIN, str(ipaddress.ip_address(host)), port))
            if row is None:
                raise HTTPException(404, "没有此设备的主机密钥记录")
            return metadata(row)

    def accept(self, actor, host, port, confirmed):
        """Trust exactly the fingerprint the user was shown, nothing newer."""
        with self.sessions() as session:
            row = session.get(
                HostKey, (DOMAIN, str(ipaddress.ip_address(host)), port), with_for_update=True
            )
            previous = row.fingerprint_sha256 if row is not None else None
            if row is not None and row.status == "pending" and row.fingerprint_sha256 == confirmed:
                row.status = "trusted"
            elif row is not None and row.observed_fingerprint == confirmed:
                row.key_type, row.public_key = row.observed_key_type, row.observed_public_key
                row.fingerprint_sha256 = confirmed
                row.observed_key_type = row.observed_public_key = None
                row.observed_fingerprint = None
            else:
                raise HTTPException(409, "主机密钥已再次变化，请重新连接后确认")
            row.revision += 1
            row.verified_by, row.verified_at = str(actor), now()
            self._audit(
                session,
                "host_key_accepted",
                host,
                port,
                actor=actor,
                previous_fingerprint=previous,
                fingerprint=confirmed,
            )
            return metadata(row)

    def known_hosts(self, host, port):
        """A single trusted line for strict-file drivers such as Netmiko."""
        host = str(ipaddress.ip_address(host))
        with self.sessions() as session:
            row = session.get(HostKey, (DOMAIN, host, port))
            if row is None or row.status != "trusted" or row.observed_fingerprint:
                raise DeviceAccessError("host_key_rejected", "没有可用的已信任主机密钥")
            return known_hosts_line(host, port, parse_public_key(row.key_type, row.public_key))

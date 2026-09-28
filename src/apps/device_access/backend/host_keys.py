"""Persistent domain trust. Key changes require explicit administrator verification."""

import ipaddress

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.host_keys import fingerprint, known_hosts_line, parse_public_key

from .grants import authorize_address
from .models import HostKey, HostKeyPolicy, now


def metadata(row):
    return {column.name: getattr(row, column.name) for column in HostKey.__table__.columns}


class HostKeyStore:
    def __init__(self, *, sessions=None, authorize=None):
        from .service import sessions as default_sessions

        self.sessions = sessions or default_sessions
        self.authorize = authorize or authorize_address

    def policies(self):
        with self.sessions() as session:
            return [
                {"domain": row.domain, "policy": row.policy, "updated_by": row.updated_by}
                for row in session.scalars(select(HostKeyPolicy).order_by(HostKeyPolicy.domain))
            ]

    def policy(self, domain):
        with self.sessions() as session:
            row = session.get(HostKeyPolicy, domain)
            return row.policy if row else None  # Unconfigured domains retain legacy strict files.

    def set_policy(self, actor, domain, policy):
        if policy not in {"strict", "tofu_pinned"}:
            raise DeviceAccessError("invalid_request", "主机密钥策略无效")
        with self.sessions() as session:
            row = session.get(HostKeyPolicy, domain)
            if row is None:
                row = HostKeyPolicy(domain=domain)
                session.add(row)
            row.policy, row.updated_by, row.updated_at = policy, str(actor), now()
        return {"domain": domain, "policy": policy}

    def list(self):
        with self.sessions() as session:
            return [
                metadata(row)
                for row in session.scalars(
                    select(HostKey).order_by(HostKey.domain, HostKey.host, HostKey.port)
                )
            ]

    def verify(self, owner, target, port, key):
        domain, host = target.management_domain, str(ipaddress.ip_address(target.host))
        self.authorize(owner, domain, host)
        observed = fingerprint(key)
        for attempt in range(2):
            code = None
            try:
                with self.sessions() as session:
                    # Lock existing trust across check/update; uniqueness handles first-pin races.
                    policy = session.get(HostKeyPolicy, domain)
                    if policy is None or policy.policy not in {"strict", "tofu_pinned"}:
                        raise DeviceAccessError("host_key_rejected", "管理域主机密钥策略不可用")
                    row = session.get(HostKey, (domain, host, port), with_for_update=True)
                    if row is None:
                        row = HostKey(
                            domain=domain,
                            host=host,
                            port=port,
                            key_type=key.get_name(),
                            public_key=key.get_base64(),
                            fingerprint_sha256=observed,
                            first_seen=now(),
                            status="pinned",
                            revision=1,
                        )
                        session.add(row)
                        session.flush()
                    if row.fingerprint_sha256 != observed:
                        if row.observed_fingerprint != observed:
                            row.revision += 1
                        row.observed_fingerprint, row.changed_at = observed, now()
                        code = "host_key_changed"
                    elif row.observed_fingerprint:
                        code = "host_key_changed"
                    elif row.status == "revoked":
                        code = "host_key_revoked"
                    elif row.status not in {"pinned", "verified"}:
                        code = "host_key_rejected"
                    elif policy.policy == "strict" and row.status != "verified":
                        code = "host_key_rejected"
                # Commit observed changes before raising, never replace the trusted key.
                if code:
                    raise DeviceAccessError(code, "SSH 主机密钥未通过校验，请管理员核验")
                return True
            except IntegrityError:
                if attempt:
                    raise DeviceAccessError("host_key_rejected", "主机密钥固定冲突") from None

    def approve(self, actor, body):
        key = parse_public_key(body.key_type, body.public_key)
        observed = fingerprint(key)
        if body.confirm_fingerprint != observed:
            raise DeviceAccessError("invalid_request", "确认指纹与公钥不匹配")
        values = dict(
            key_type=key.get_name(),
            public_key=key.get_base64(),
            fingerprint_sha256=observed,
            status="verified",
            verified_by=str(actor),
            verified_at=now(),
            observed_fingerprint=None,
            changed_at=None,
            revision=body.revision + 1,
        )
        with self.sessions() as session:
            if body.revision == 0:
                # Offline import must not overwrite a concurrent first pin.
                session.add(
                    HostKey(
                        domain=body.domain,
                        host=body.host,
                        port=body.port,
                        first_seen=now(),
                        **values,
                    )
                )
                try:
                    session.flush()
                except IntegrityError:
                    raise HTTPException(409, "主机密钥已存在，请刷新后重新核验") from None
            else:
                result = session.execute(
                    update(HostKey)
                    .where(
                        HostKey.domain == body.domain,
                        HostKey.host == body.host,
                        HostKey.port == body.port,
                        HostKey.revision == body.revision,
                    )
                    .values(**values)
                )
                if result.rowcount != 1:
                    raise HTTPException(409, "主机密钥已更新，请刷新后重新核验")
            return metadata(session.get(HostKey, (body.domain, body.host, body.port)))

    def revoke(self, actor, body):
        with self.sessions() as session:
            result = session.execute(
                update(HostKey)
                .where(
                    HostKey.domain == body.domain,
                    HostKey.host == body.host,
                    HostKey.port == body.port,
                    HostKey.revision == body.revision,
                )
                .values(
                    status="revoked",
                    revision=body.revision + 1,
                    verified_by=str(actor),
                    verified_at=now(),
                )
            )
            if result.rowcount != 1:
                raise HTTPException(409, "主机密钥已更新，请刷新后重试")
        return {"revoked": True}

    def known_hosts(self, owner, domain, host, port):
        """Generate a scoped snapshot for strict-file drivers, never ambient trust."""
        host = str(ipaddress.ip_address(host))
        self.authorize(owner, domain, host)
        with self.sessions() as session:
            policy = session.get(HostKeyPolicy, domain)
            row = session.get(HostKey, (domain, host, port))
            if (
                policy is None
                or row is None
                or row.observed_fingerprint is not None
                or row.status == "revoked"
                or row.status not in {"pinned", "verified"}
                or (policy.policy == "strict" and row.status != "verified")
            ):
                raise DeviceAccessError("host_key_rejected", "没有可用的已固定主机密钥")
            return known_hosts_line(host, port, parse_public_key(row.key_type, row.public_key))

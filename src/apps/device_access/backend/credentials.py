"""Encrypted, owner-scoped vault. Secret resolution is a server-only interface."""

import ipaddress
import os
import re
import time
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.errors import failure_code
from src.platform.device_access.security import (
    CredentialCandidate,
    CredentialMaterial,
    CredentialStore,
    credential_order,
)

from .grants import authorize_address
from .models import CredentialSet, DeviceCredential, now


def _inventory(domain, address):
    """Read-only compatibility with explicit env: references in the asset table."""
    from src.infrastructure.db.models import NetworkDevice
    from src.infrastructure.db.postgres import get_db_session

    with get_db_session() as session:
        rows = session.scalars(
            select(NetworkDevice).where(NetworkDevice.management_ip == address)
        ).all()
        matches = [
            row
            for row in rows
            if (row.attributes_json or {}).get("management_domain", "default") == domain
        ]
        if len(matches) != 1:
            return None  # Never choose an ambiguous asset record.
        row = matches[0]
        if row.username and re.fullmatch(r"env:[A-Za-z_][A-Za-z0-9_]*", row.credential_ref or ""):
            return str(row.id), row.username, row.credential_ref
    return None


def _candidate(row):
    return CredentialCandidate(
        row.id,
        row.owner_id,
        row.domain,
        row.scope_kind,
        row.scope_value,
        row.priority,
        row.disabled,
    )


def _metadata(row):
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "name",
            "username",
            "owner_id",
            "domain",
            "scope_kind",
            "scope_value",
            "priority",
            "disabled",
        )
    } | {
        "created_at": row.created_at.isoformat(),
        "rotated_at": row.rotated_at.isoformat(),
        "has_enable_secret": bool(row.enable_secret_ciphertext),
    }


class CredentialVault:
    def __init__(self, *, sessions=None, key=None, authorize=None, inventory=None, temporary=None):
        from .service import sessions as default_sessions

        self.sessions = sessions or default_sessions
        self._key = key
        self.authorize = authorize or authorize_address
        self.inventory = inventory or _inventory
        self.temporary = temporary

    def _cipher(self):
        try:
            return Fernet(self._key or os.environ["DEVICE_ACCESS_CREDENTIAL_KEY"])
        except (KeyError, ValueError, TypeError):
            raise DeviceAccessError("credential_store_unavailable", "凭据加密密钥不可用") from None

    @staticmethod
    def _owned(session, owner, identifier):
        row = session.get(CredentialSet, identifier)
        if row is None or row.owner_id != str(owner):
            raise HTTPException(404, "凭据集不存在")
        return row

    def create(self, owner, body):
        cipher = self._cipher()
        with self.sessions() as session:
            row = CredentialSet(
                id=uuid4().hex,
                owner_id=str(owner),
                **body.model_dump(exclude={"password", "enable_secret"}),
                secret_ciphertext=cipher.encrypt(
                    body.password.get_secret_value().encode()
                ).decode(),
                enable_secret_ciphertext=(
                    cipher.encrypt(body.enable_secret.get_secret_value().encode()).decode()
                    if body.enable_secret
                    else None
                ),
                created_at=now(),
                rotated_at=now(),
                disabled=False,
            )
            session.add(row)
            return _metadata(row)

    def list(self, owner):
        with self.sessions() as session:
            return [
                _metadata(row)
                for row in session.scalars(
                    select(CredentialSet)
                    .where(CredentialSet.owner_id == str(owner))
                    .order_by(CredentialSet.created_at.desc(), CredentialSet.id)
                )
            ]

    def disable(self, owner, identifier, disabled):
        with self.sessions() as session:
            row = self._owned(session, owner, identifier)
            row.disabled = disabled
            return _metadata(row)

    def rotate(self, owner, identifier, body):
        cipher = self._cipher()
        with self.sessions() as session:
            row = self._owned(session, owner, identifier)
            row.secret_ciphertext = cipher.encrypt(
                body.password.get_secret_value().encode()
            ).decode()
            if "enable_secret" in body.model_fields_set:
                row.enable_secret_ciphertext = (
                    cipher.encrypt(body.enable_secret.get_secret_value().encode()).decode()
                    if body.enable_secret
                    else None
                )
            row.rotated_at = now()
            return _metadata(row)

    def candidates(
        self, owner, domain, address, *, device_uid=None, temporary_ref=None, credential_set_id=None
    ):
        address = str(ipaddress.ip_address(address))
        self.authorize(owner, domain, address)
        key = f"uid:{device_uid}" if device_uid else address
        with self.sessions() as session:
            memory = session.get(DeviceCredential, (str(owner), domain, key))
            remembered_id = memory.credential_set_id if memory else None
            candidates = [
                _candidate(row)
                for row in session.scalars(
                    select(CredentialSet).where(
                        CredentialSet.owner_id == str(owner),
                        CredentialSet.domain == domain,
                        CredentialSet.disabled.is_(False),
                    )
                )
            ]
        asset = self.inventory(domain, address)
        if asset:
            candidates.append(
                CredentialCandidate(
                    "env-device:" + asset[0], str(owner), domain, "device", address, -1001
                )
            )
        if temporary_ref:
            candidates.append(
                CredentialCandidate(
                    "temporary:" + temporary_ref, str(owner), domain, "temporary", address
                )
            )
        if credential_set_id is not None:
            candidates = [
                candidate for candidate in candidates if candidate.id == credential_set_id
            ]
        return credential_order(
            candidates, owner, domain, address, remembered_id=remembered_id, device_uid=device_uid
        )

    def resolve(self, owner, domain, address, candidate, *, device_uid=None):
        """No HTTP route exposes this result. Recheck ownership/scope at use time."""
        address = str(ipaddress.ip_address(address))
        self.authorize(owner, domain, address)
        if not credential_order([candidate], owner, domain, address, device_uid=device_uid):
            raise DeviceAccessError("not_authorized", "凭据作用域不匹配")
        if candidate.id.startswith("temporary:"):
            store = self.temporary or CredentialStore()
            username, password = store.get(
                candidate.id.removeprefix("temporary:"), owner, domain, address
            )
            return CredentialMaterial(username, password)
        if candidate.id.startswith("env-device:"):
            asset = self.inventory(domain, address)
            if not asset or candidate.id != "env-device:" + asset[0]:
                raise DeviceAccessError("credential_expired", "设备凭据引用已变化")
            password = os.getenv(asset[2].removeprefix("env:"))
            if not password:
                raise DeviceAccessError("credential_expired", "设备凭据不可用")
            return CredentialMaterial(asset[1], password)
        with self.sessions() as session:
            row = self._owned(session, owner, candidate.id)
            if not credential_order(
                [_candidate(row)], owner, domain, address, device_uid=device_uid
            ):
                raise DeviceAccessError("not_authorized", "凭据已禁用或作用域不匹配")
            try:
                cipher = self._cipher()
                password = cipher.decrypt(row.secret_ciphertext.encode()).decode()
                enable = (
                    cipher.decrypt(row.enable_secret_ciphertext.encode()).decode()
                    if row.enable_secret_ciphertext
                    else None
                )
            except (InvalidToken, ValueError, UnicodeError, AttributeError):
                raise DeviceAccessError("credential_expired", "凭据不可恢复，请轮换凭据") from None
            return CredentialMaterial(row.username, password, enable)

    def _remember(self, owner, domain, key, identifier=None, code=None):
        # Concurrent successful logins may insert the same composite key.
        for attempt in range(2):
            try:
                with self.sessions() as session:
                    row = session.get(DeviceCredential, (str(owner), domain, key))
                    if row is None:
                        row = DeviceCredential(owner_id=str(owner), domain=domain, device_key=key)
                        session.add(row)
                    if code is None:
                        row.credential_set_id = identifier
                        row.last_success_at = now()
                    row.last_failure_code = code
                return
            except IntegrityError:
                if attempt:
                    raise DeviceAccessError(
                        "credential_store_unavailable", "凭据记忆写入失败"
                    ) from None

    def login(
        self,
        owner,
        domain,
        address,
        connect,
        *,
        device_uid=None,
        temporary_ref=None,
        credential_set_id=None,
        budget_seconds=30,
        should_continue=None,
        on_success=None,
    ):
        """Server-only orchestration: caller supplies admission + strict SSH connect.

        connect(material, remaining_seconds) must do exactly one login attempt,
        honor the supplied deadline and never weaken host-key checking. Returned
        connection objects stay on the backend; commands are executed elsewhere.
        """
        if type(budget_seconds) not in (int, float) or not 0 < budget_seconds <= 300:
            raise DeviceAccessError("invalid_request", "凭据尝试预算无效")
        deadline = time.monotonic() + budget_seconds
        key = f"uid:{device_uid}" if device_uid else str(ipaddress.ip_address(address))
        for candidate in self.candidates(
            owner,
            domain,
            address,
            device_uid=device_uid,
            temporary_ref=temporary_ref,
            credential_set_id=credential_set_id,
        ):
            if should_continue is not None and should_continue() is False:
                raise DeviceAccessError("cancelled", "设备登录已取消")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DeviceAccessError("budget_exceeded", "设备登录已超时")
            material = self.resolve(owner, domain, address, candidate, device_uid=device_uid)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DeviceAccessError("budget_exceeded", "设备登录已超时")
            if should_continue is not None and should_continue() is False:
                raise DeviceAccessError("cancelled", "设备登录已取消")
            try:
                connection = connect(material, remaining)
            except Exception as exc:
                code = failure_code(exc)
                if code not in {
                    "authentication_failed",
                    "timeout",
                    "host_key_rejected",
                    "host_key_changed",
                    "host_key_revoked",
                    "not_authorized",
                    "cancelled",
                    "budget_exceeded",
                }:
                    code = "connection_failed"
                self._remember(owner, domain, key, code=code)
                if code != "authentication_failed":
                    raise DeviceAccessError(code, "设备连接失败，请人工确认后重试") from None
                continue
            try:
                if time.monotonic() >= deadline:
                    raise DeviceAccessError("budget_exceeded", "设备登录已超时")
                self.authorize(owner, domain, str(ipaddress.ip_address(address)))
                if should_continue is not None and should_continue() is False:
                    raise DeviceAccessError("cancelled", "设备登录已取消")
                # Only vault sets have a durable FK; env/temporary references are not persisted.
                identifier = None if ":" in candidate.id else candidate.id
                self._remember(owner, domain, key, identifier)
                if on_success is not None:
                    on_success(candidate.id)
            except Exception:
                try:
                    connection.disconnect()
                except Exception:
                    pass
                raise
            return connection
        raise DeviceAccessError("waiting_for_input", "认证失败，请输入新凭据、指定凭据集或跳过节点")

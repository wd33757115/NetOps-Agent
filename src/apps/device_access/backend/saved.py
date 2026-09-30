"""Saved sessions and one default login per platform account."""

from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.governance import write_event

from .crypto import decrypt, encrypt
from .models import DefaultLogin, SavedSession, now


def _sessions():
    from .service import sessions

    return sessions()


def view(row):
    return {
        "id": row.id,
        "name": row.name,
        "host": row.host,
        "port": row.port,
        "username": row.username,
        "uses_default": row.username is None,
        "updated_at": row.updated_at.isoformat(),
    }


def _owned(session, owner, session_id):
    row = session.get(SavedSession, session_id)
    if row is None or row.owner_id != str(owner):
        raise HTTPException(404, "会话不存在")
    return row


def _audit(session, owner, kind, row=None, **detail):
    if row is not None:
        detail.update(
            session_id=row.id,
            host=row.host,
            port=row.port,
            name=row.name,
            username=row.username,
            uses_default=row.username is None,
        )
    write_event(
        actor_id=owner, source_app="device-access", kind=kind, detail=detail, session=session
    )


def list_sessions(owner):
    with _sessions() as session:
        return [
            view(row)
            for row in session.scalars(
                select(SavedSession)
                .where(SavedSession.owner_id == str(owner))
                .order_by(SavedSession.name, SavedSession.id)
            )
        ]


def create(owner, body):
    with _sessions() as session:
        row = SavedSession(
            id=uuid4().hex,
            owner_id=str(owner),
            name=body.name,
            host=body.host,
            port=body.port,
            username=body.username,
            secret_ciphertext=encrypt(body.password.get_secret_value()) if body.password else None,
            created_at=now(),
            updated_at=now(),
        )
        session.add(row)
        _audit(
            session, owner, "session_created", row, credentials_configured=body.password is not None
        )
        return view(row)


def update(owner, session_id, body):
    fields = body.model_fields_set
    with _sessions() as session:
        row = _owned(session, owner, session_id)
        for name in ("name", "host", "port"):
            if name in fields and getattr(body, name) is not None:
                setattr(row, name, getattr(body, name))
        password = body.password.get_secret_value() if body.password else ""
        if "username" in fields and not body.username:
            row.username = row.secret_ciphertext = None
        elif "username" in fields:
            if not password and row.secret_ciphertext is None:
                raise HTTPException(422, "请同时填写密码")
            row.username = body.username
        if password:
            if row.username is None:
                raise HTTPException(422, "请同时填写用户名")
            row.secret_ciphertext = encrypt(password)
        row.updated_at = now()
        _audit(
            session,
            owner,
            "session_updated",
            row,
            changed_fields=sorted(fields),
            credentials_updated=bool(password),
            credentials_cleared="username" in fields and not body.username,
        )
        return view(row)


def delete(owner, session_id):
    with _sessions() as session:
        row = _owned(session, owner, session_id)
        _audit(session, owner, "session_deleted", row)
        session.delete(row)
    return {"deleted": True}


def set_login(owner, body):
    """Batch edit: one username/password for every selected session."""
    ciphertext = encrypt(body.password.get_secret_value())
    ids = list(dict.fromkeys(body.session_ids))
    with _sessions() as session:
        rows = [_owned(session, owner, session_id) for session_id in ids]
        for row in rows:
            row.username, row.secret_ciphertext, row.updated_at = (
                body.username,
                ciphertext,
                now(),
            )
            _audit(session, owner, "session_login_updated", row, credentials_updated=True)
        return [view(row) for row in rows]


def read_default(owner):
    with _sessions() as session:
        row = session.get(DefaultLogin, str(owner))
        return {"username": row.username if row else None, "configured": row is not None}


def save_default(owner, body):
    with _sessions() as session:
        row = session.get(DefaultLogin, str(owner))
        if row is None:
            row = DefaultLogin(owner_id=str(owner))
            session.add(row)
        row.username = body.username
        row.secret_ciphertext = encrypt(body.password.get_secret_value())
        row.updated_at = now()
        _audit(
            session, owner, "default_login_updated", username=row.username, credentials_updated=True
        )
    return read_default(owner)


def target(owner, session_id):
    """(name, host, port) of an owned session; no secrets."""
    with _sessions() as session:
        row = _owned(session, owner, session_id)
        return row.name, row.host, row.port


def login(owner, session_id=None):
    """Server-only: the session's own login, else the owner's default."""
    with _sessions() as session:
        if session_id is not None:
            row = _owned(session, owner, session_id)
            if row.username is not None and row.secret_ciphertext:
                return row.username, decrypt(row.secret_ciphertext)
        default = session.get(DefaultLogin, str(owner))
        if default is None:
            raise DeviceAccessError("credentials_required", "请输入用户名和密码")
        return default.username, decrypt(default.secret_ciphertext)


def save_login(owner, session_id, username, password):
    with _sessions() as session:
        row = _owned(session, owner, session_id)
        row.username, row.secret_ciphertext, row.updated_at = username, encrypt(password), now()
        _audit(session, owner, "session_login_saved", row, credentials_updated=True)

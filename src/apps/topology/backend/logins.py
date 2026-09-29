"""Discovery logins: a node's typed login, else the run's temporary one, else the default."""

from src.apps.device_access.backend.crypto import decrypt, encrypt
from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.security import CredentialStore

from .models import DefaultLogin, now
from .repository import sessions

# The platform keeps its management-domain parameter; this App always passes one value.
DOMAIN = "default"
# A temporary login lives for the run; it is revoked as soon as the run ends.
RUN_SECONDS = 86400


def read_default(owner):
    with sessions() as session:
        row = session.get(DefaultLogin, str(owner))
        return {"username": row.username if row else None, "configured": row is not None}


def save_default(owner, body):
    with sessions() as session:
        row = session.get(DefaultLogin, str(owner))
        if row is None:
            row = DefaultLogin(owner_id=str(owner))
            session.add(row)
        row.username = body.username
        row.secret_ciphertext = encrypt(body.password.get_secret_value())
        row.updated_at = now()
    return read_default(owner)


def has_default(owner):
    return read_default(owner)["configured"]


def put(owner, addresses, credentials):
    return CredentialStore().put(
        owner,
        DOMAIN,
        addresses,
        credentials.username,
        credentials.password.get_secret_value(),
        ttl=RUN_SECONDS,
    )


def resolve(owner, address, *refs):
    """Server-only: the first available of the given references, else the default."""
    for ref in refs:
        if ref:
            return CredentialStore().get(ref, owner, DOMAIN, address)
    with sessions() as session:
        row = session.get(DefaultLogin, str(owner))
        if row is None:
            raise DeviceAccessError("credentials_required", "请输入用户名和密码")
        return row.username, decrypt(row.secret_ciphertext)

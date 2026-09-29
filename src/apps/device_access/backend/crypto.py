"""Encrypted-at-rest passwords. Plaintext never leaves the backend."""

import os

from cryptography.fernet import Fernet, InvalidToken

from src.platform.device_access.contracts import DeviceAccessError


def _cipher():
    try:
        return Fernet(os.environ["DEVICE_ACCESS_CREDENTIAL_KEY"])
    except (KeyError, ValueError, TypeError):
        raise DeviceAccessError("credential_store_unavailable", "凭据加密密钥不可用") from None


def encrypt(password):
    return _cipher().encrypt(password.encode()).decode()


def decrypt(ciphertext):
    try:
        return _cipher().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError, UnicodeError, AttributeError):
        raise DeviceAccessError("credential_expired", "保存的密码无法解密，请重新输入") from None

"""Encryption helpers for stored provider keys (Fernet/AES-GCM via cryptography)."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.exceptions import CredentialUndecryptableError

_salt = b"myaibuddy-key-salt-v1"


def _fernet() -> Fernet:
    secret = settings.JWT_SECRET_KEY or "dev-only-insecure-fallback"
    key = hashlib.pbkdf2_hmac("sha256", secret.encode(), _salt, 120_000, dklen=32)
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt_secret(blob: bytes | None) -> str:
    if not blob:
        return ""
    try:
        return _fernet().decrypt(blob).decode()
    except InvalidToken as exc:
        # Usually JWT_SECRET_KEY changed, so every saved key is now unreadable.
        # This is fixable by re-entering the key, so it must not look like a crash.
        raise CredentialUndecryptableError(
            "This provider's saved API key can no longer be read, because the server's "
            "encryption secret changed. Please re-enter the API key in Settings."
        ) from exc


def fingerprint(plaintext: str) -> str:
    if not plaintext:
        return ""
    return hashlib.sha256(plaintext.encode()).hexdigest()[:16]

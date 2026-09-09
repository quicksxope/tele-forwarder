"""Symmetric encryption for exchange API credentials.

Uses Fernet (AES-128-CBC + HMAC-SHA256) from the `cryptography` library.

Master key is loaded from env CREDENTIAL_ENCRYPTION_KEY (base64-urlsafe, 32 bytes).
Generate once with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "CREDENTIAL_ENCRYPTION_KEY is not set. "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        _fernet = Fernet(key.encode())
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string, return base64-urlsafe ciphertext string."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a ciphertext string produced by encrypt(). Raises InvalidToken if tampered."""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Credential decryption failed — token invalid or key mismatch") from e


def generate_key() -> str:
    """Generate a new random Fernet key (call once, store in env)."""
    return Fernet.generate_key().decode()

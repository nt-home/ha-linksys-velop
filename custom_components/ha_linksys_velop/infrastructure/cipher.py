"""Authenticated encryption of the stored admin password."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from ..domain.errors import SecretUnavailableError

PREFIX = "enc:v1:"


def generate_key() -> str:
    """Return a new random key (URL-safe base64, 256 bits)."""
    return Fernet.generate_key().decode()


def is_encrypted(value: str) -> bool:
    """Return True when the value is a token produced by :class:`PasswordCipher`."""
    return value.startswith(PREFIX)


class PasswordCipher:
    """Encrypt and decrypt secrets with Fernet (AES-128-CBC + HMAC-SHA256)."""

    def __init__(self, key: str) -> None:
        """Create a cipher; raises SecretUnavailableError for a malformed key."""
        try:
            self._fernet = Fernet(key.encode())
        except ValueError as exc:
            raise SecretUnavailableError from exc

    def __repr__(self) -> str:
        """Return a representation that excludes the key."""
        return "PasswordCipher()"

    def encrypt(self, plaintext: str) -> str:
        """Return the tagged ciphertext of ``plaintext``."""
        return PREFIX + self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        """Return the plaintext, raising SecretUnavailableError on any failure."""
        if not is_encrypted(token):
            raise SecretUnavailableError
        try:
            return self._fernet.decrypt(token.removeprefix(PREFIX).encode()).decode()
        except (InvalidToken, ValueError) as exc:
            raise SecretUnavailableError from exc

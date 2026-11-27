"""Encrypted storage of the admin password.

The ciphertext lives in the config entry; the key lives in a separate private
store file (``.storage/ha_linksys_velop.key``, mode 0600). The password is
decrypted only at the moment a client is built.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store
from homeassistant.util.hass_dict import HassKey

from ..domain.errors import SecretUnavailableError
from ..infrastructure.cipher import PasswordCipher, generate_key

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

STORAGE_KEY = "ha_linksys_velop.key"
STORAGE_VERSION = 1
_FIELD = "key"


@dataclass
class _Vault:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cipher: PasswordCipher | None = None


_VAULT: HassKey[_Vault] = HassKey("ha_linksys_velop_vault")


async def _async_cipher(hass: HomeAssistant, *, create: bool) -> PasswordCipher:
    vault = hass.data.setdefault(_VAULT, _Vault())
    async with vault.lock:
        if vault.cipher is None:
            store: Store[dict[str, str]] = Store(
                hass, STORAGE_VERSION, STORAGE_KEY, private=True
            )
            data = await store.async_load()
            key = data.get(_FIELD) if isinstance(data, dict) else None
            if not isinstance(key, str) or not key:
                key = None
            if key is None:
                if not create:
                    raise SecretUnavailableError
                key = generate_key()
                await store.async_save({_FIELD: key})
            vault.cipher = PasswordCipher(key)
        return vault.cipher


async def async_encrypt_password(hass: HomeAssistant, password: str) -> str:
    """Encrypt a password for storage, creating the key on first use."""
    return (await _async_cipher(hass, create=True)).encrypt(password)


async def async_decrypt_password(hass: HomeAssistant, stored: str) -> str:
    """Decrypt a stored password; never creates a key.

    Raises:
        SecretUnavailableError: If the key is missing or the token is invalid.
    """
    return (await _async_cipher(hass, create=False)).decrypt(stored)


async def async_remove_key(hass: HomeAssistant) -> None:
    """Destroy the key store once no hub entry needs it any more."""
    vault = hass.data.setdefault(_VAULT, _Vault())
    async with vault.lock:
        vault.cipher = None
        await Store(hass, STORAGE_VERSION, STORAGE_KEY, private=True).async_remove()

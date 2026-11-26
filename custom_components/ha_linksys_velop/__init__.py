"""Linksys Velop Local: local JNAP control of MX-series mesh Wi-Fi."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_VERIFY_SSL
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .application.credentials import (
    async_decrypt_password,
    async_encrypt_password,
)
from .application.factory import create_client
from .const import (
    CONF_USE_SSL,
    CONFIG_ENTRY_MINOR_VERSION,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    PLATFORMS,
    ROUTER_ONLY_KEYS,
)
from .coordinator import VelopConfigEntry, VelopCoordinator
from .domain.errors import (
    AccountLockedError,
    InvalidAuthError,
    NotPrimaryNodeError,
    SecretUnavailableError,
    VelopError,
)
from .domain.models import NetworkMode
from .infrastructure.cipher import is_encrypted
from .services import async_register_actions

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry
    from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register integration-wide actions."""
    async_register_actions(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: VelopConfigEntry) -> bool:
    """Migrate 1.1 -> 1.2: encrypt the stored password, add verify_ssl."""
    if entry.version > 1:
        return False
    if entry.minor_version < CONFIG_ENTRY_MINOR_VERSION:
        data = dict(entry.data)
        stored = data.get(CONF_PASSWORD, "")
        if not is_encrypted(stored):
            data[CONF_PASSWORD] = await async_encrypt_password(hass, stored)
        data.setdefault(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
        hass.config_entries.async_update_entry(
            entry, data=data, minor_version=CONFIG_ENTRY_MINOR_VERSION
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: VelopConfigEntry) -> bool:
    """Set up a hub from a config entry."""
    try:
        # Decrypted only now, to build the client; never kept in the entry.
        password = await async_decrypt_password(hass, entry.data[CONF_PASSWORD])
    except SecretUnavailableError as exc:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="secret_unavailable"
        ) from exc
    client = create_client(
        hass,
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        use_ssl=entry.data[CONF_USE_SSL],
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        password=password,
    )
    del password
    try:
        identity = await client.async_get_identity()
    except (InvalidAuthError, AccountLockedError) as exc:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="invalid_auth"
        ) from exc
    except NotPrimaryNodeError as exc:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="not_primary_node"
        ) from exc
    except VelopError as exc:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="cannot_connect"
        ) from exc
    if identity.serial != entry.unique_id:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="wrong_hub"
        )

    coordinator = VelopCoordinator(hass, entry, client, identity)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if coordinator.data.mode is NetworkMode.BRIDGE:
        _prune_router_only_entities(hass, entry)
    _LOGGER.debug("Hub set up in %s mode", coordinator.data.mode.value)
    return True


def _prune_router_only_entities(hass: HomeAssistant, entry: VelopConfigEntry) -> None:
    """Remove client entities left over from router mode."""
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        key = entity.unique_id.partition("_")[2]
        if key in ROUTER_ONLY_KEYS or entity.domain == "device_tracker":
            registry.async_remove(entity.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: VelopConfigEntry) -> bool:
    """Unload a hub; coordinator listeners are removed with the platforms."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: VelopConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Refuse to remove devices that are still present on the mesh."""
    serials = {n.serial for n in entry.runtime_data.data.nodes}
    return not any(
        domain == DOMAIN and serial in serials
        for domain, serial in device_entry.identifiers
    )

"""Integration-wide actions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import probatio
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service import async_register_admin_service

from .const import DOMAIN, SERVICE_REBOOT_NODE, SERVICE_REFRESH_NOW

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

    from .coordinator import VelopConfigEntry, VelopCoordinator

ATTR_DEVICE_ID = "device_id"
_REBOOT_SCHEMA = probatio.Schema(
    {probatio.Required(ATTR_DEVICE_ID): probatio.All(cv.ensure_list, [cv.string])}
)


def _loaded_coordinators(hass: HomeAssistant) -> list[VelopCoordinator]:
    entries: list[VelopConfigEntry] = hass.config_entries.async_loaded_entries(DOMAIN)
    return [e.runtime_data for e in entries]


async def _async_reboot_node(call: ServiceCall) -> None:
    hass = call.hass
    registry = dr.async_get(hass)
    coordinators = _loaded_coordinators(hass)
    for device_id in call.data[ATTR_DEVICE_ID]:
        device = registry.async_get(device_id)
        serial = next(
            (i[1] for i in (device.identifiers if device else ()) if i[0] == DOMAIN),
            None,
        )
        coordinator = next(
            (c for c in coordinators if serial and serial in c.data.node_by_serial),
            None,
        )
        if serial is None or coordinator is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="unknown_node"
            )
        await coordinator.async_reboot_node(serial)


async def _async_refresh_now(call: ServiceCall) -> None:
    coordinators = _loaded_coordinators(call.hass)
    if not coordinators:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_hub")
    for coordinator in coordinators:
        await coordinator.async_request_refresh()


def async_register_actions(hass: HomeAssistant) -> None:
    """Register the reboot (admin only) and refresh actions."""
    async_register_admin_service(
        hass, DOMAIN, SERVICE_REBOOT_NODE, _async_reboot_node, _REBOOT_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_NOW, _async_refresh_now)

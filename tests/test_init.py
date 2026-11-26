"""End-to-end setup, entities, events and unload against the emulator."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from custom_components.ha_linksys_velop.const import DOMAIN
from tests.emulator.server import PRIMARY_SERIAL, Emulator


async def test_setup_and_unload(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """The entry loads, creates hub and child devices, and unloads."""
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_entry.state is ConfigEntryState.LOADED

    devices = dr.async_get(hass)
    hub = devices.async_get_device_by_identifier(
        (DOMAIN, PRIMARY_SERIAL), mock_entry.entry_id
    )
    child = devices.async_get_device_by_identifier(
        (DOMAIN, "12K10M22222222"), mock_entry.entry_id
    )
    assert hub is not None and child is not None
    assert child.via_device_id == hub.id
    # The role shows under the device name, e.g. in the Name and assign dialog.
    assert hub.model == "MX2000 (primary node)"
    assert child.model == "MX2000 (child node)"

    ents = er.async_entries_for_config_entry(er.async_get(hass), mock_entry.entry_id)
    assert len(ents) > 10

    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    assert mock_entry.state is ConfigEntryState.NOT_LOADED

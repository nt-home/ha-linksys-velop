"""Event platform: automation events from the hub."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.event import EventEntity
from homeassistant.core import callback

from .application.events import EVENT_TYPES, VelopEvent
from .entity import VelopEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import VelopConfigEntry, VelopCoordinator

PARALLEL_UPDATES = 0


class VelopEventEntity(VelopEntity, EventEntity):
    """Fires an event for every detected hub or node transition."""

    _attr_event_types = EVENT_TYPES

    def __init__(self, coordinator: VelopCoordinator, serial: str) -> None:
        """Create the event entity."""
        super().__init__(coordinator, serial, "hub_events")

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator events."""
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_subscribe_events(self._handle))

    @callback
    def _handle(self, event: VelopEvent) -> None:
        attributes: dict[str, str] = {}
        if event.node_name:
            attributes["node_name"] = event.node_name
        if event.node_serial:
            attributes["node_serial"] = event.node_serial
        if event.detail:
            attributes["detail"] = event.detail
        self._trigger_event(event.type.value, attributes)
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VelopConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the hub event entity."""
    coordinator = entry.runtime_data
    async_add_entities([VelopEventEntity(coordinator, coordinator.identity.serial)])

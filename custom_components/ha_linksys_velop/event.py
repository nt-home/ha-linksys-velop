"""Event platform: automation events from the hub."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.event import EventEntity
from homeassistant.core import callback

from .application.events import CLIENT_EVENT_TYPES, HUB_EVENT_TYPES, VelopEvent
from .const import OPT_CLIENT_EVENTS
from .domain.models import NetworkMode
from .entity import VelopEntity, async_add_node_entities

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import VelopConfigEntry, VelopCoordinator
    from .domain.models import Node

PARALLEL_UPDATES = 0


class VelopEventEntity(VelopEntity, EventEntity):
    """Fires an event for every detected hub or node transition."""

    _attr_event_types = HUB_EVENT_TYPES
    _key = "hub_events"

    def __init__(self, coordinator: VelopCoordinator, serial: str) -> None:
        """Create the event entity."""
        super().__init__(coordinator, serial, self._key)

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator events."""
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_subscribe_events(self._handle))

    @callback
    def _handle(self, event: VelopEvent) -> None:
        if event.type.value not in self._attr_event_types:
            return
        attributes: dict[str, str] = {}
        if event.node_name:
            attributes["node_name"] = event.node_name
        if event.node_serial:
            attributes["node_serial"] = event.node_serial
        if event.detail:
            attributes["detail"] = event.detail
        if event.client_name:
            attributes["client_name"] = event.client_name
        if event.client_mac:
            attributes["client_mac"] = event.client_mac
        self._trigger_event(event.type.value, attributes)
        self.async_write_ha_state()


class VelopClientEventEntity(VelopEventEntity):
    """Fires when a client joins or leaves this node (opt-in, router mode)."""

    _attr_event_types = CLIENT_EVENT_TYPES
    _key = "client_events"

    @callback
    def _handle(self, event: VelopEvent) -> None:
        if event.node_serial != self._serial:
            return
        super()._handle(event)


def _client_events_factory(coordinator: VelopCoordinator, node: Node) -> list[Entity]:
    return [VelopClientEventEntity(coordinator, node.serial)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VelopConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the hub event entity."""
    coordinator = entry.runtime_data
    async_add_entities([VelopEventEntity(coordinator, coordinator.identity.serial)])
    if (
        entry.options.get(OPT_CLIENT_EVENTS, False)
        and coordinator.data.mode is NetworkMode.ROUTER
    ):
        async_add_node_entities(entry, async_add_entities, _client_events_factory)

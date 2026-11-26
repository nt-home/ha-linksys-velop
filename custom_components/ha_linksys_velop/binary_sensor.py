"""Binary sensor platform."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)

from .domain.models import NodeRole
from .entity import VelopEntity, async_add_node_entities

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import VelopConfigEntry, VelopCoordinator
    from .domain.models import Node

PARALLEL_UPDATES = 0


class VelopInternetSensor(VelopEntity, BinarySensorEntity):
    """Whether the hub reports a working internet connection."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: VelopCoordinator, serial: str) -> None:
        """Create the sensor."""
        super().__init__(coordinator, serial, "internet")

    @property
    def is_on(self) -> bool | None:
        """Return True when the WAN is connected."""
        return self.snapshot.wan.connected


class VelopNodeSensor(VelopEntity, BinarySensorEntity):
    """Whether a child node is online."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: VelopCoordinator, serial: str) -> None:
        """Create the sensor."""
        super().__init__(coordinator, serial, "node_connectivity")

    @property
    def is_on(self) -> bool | None:
        """Return True when the node is online."""
        node = self.node
        return None if node is None else node.online


def _factory(coordinator: VelopCoordinator, node: Node) -> list[Entity]:
    if node.role is NodeRole.PRIMARY:
        return [VelopInternetSensor(coordinator, node.serial)]
    return [VelopNodeSensor(coordinator, node.serial)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VelopConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors for every node."""
    async_add_node_entities(entry, async_add_entities, _factory)

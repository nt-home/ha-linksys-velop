"""Binary sensor platform."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory

from .domain.models import NodeRole
from .entity import VelopEntity, async_add_node_entities

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import VelopConfigEntry, VelopCoordinator
    from .domain.models import HubSnapshot, Node

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


class VelopActivitySensor(VelopEntity, BinarySensorEntity):
    """Whether Channel Finder or a speed check is running on the hub."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: VelopCoordinator,
        serial: str,
        key: str,
        value_fn: Callable[[HubSnapshot], bool | None],
    ) -> None:
        """Create the sensor."""
        super().__init__(coordinator, serial, key)
        self._value_fn = value_fn

    @property
    def is_on(self) -> bool | None:
        """Return True while the activity runs."""
        return self._value_fn(self.snapshot)


def _speed_running(snap: HubSnapshot) -> bool | None:
    return None if snap.speed_test is None else snap.speed_test.running


def _factory(coordinator: VelopCoordinator, node: Node) -> list[Entity]:
    if node.role is NodeRole.PRIMARY:
        entities: list[Entity] = [VelopInternetSensor(coordinator, node.serial)]
        snap = coordinator.data
        if snap.channel_scan_running is not None:
            entities.append(
                VelopActivitySensor(
                    coordinator,
                    node.serial,
                    "channel_scan_running",
                    lambda s: s.channel_scan_running,
                )
            )
        if snap.speed_test is not None:
            entities.append(
                VelopActivitySensor(
                    coordinator, node.serial, "speed_test_running", _speed_running
                )
            )
        return entities
    return [VelopNodeSensor(coordinator, node.serial)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VelopConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors for every node."""
    async_add_node_entities(entry, async_add_entities, _factory)

"""Button platform: reboot, Channel Finder and speed check."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity

from .domain.models import NodeRole
from .entity import VelopEntity, async_add_node_entities

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import VelopConfigEntry, VelopCoordinator
    from .domain.models import Node

PARALLEL_UPDATES = 1


class VelopRebootButton(VelopEntity, ButtonEntity):
    """Reboot one node."""

    _attr_device_class = ButtonDeviceClass.RESTART

    def __init__(self, coordinator: VelopCoordinator, serial: str) -> None:
        """Create the button."""
        super().__init__(coordinator, serial, "reboot")

    async def async_press(self) -> None:
        """Reboot the node through the coordinator use case."""
        await self.coordinator.async_reboot_node(self._serial)


class VelopChannelScanButton(VelopEntity, ButtonEntity):
    """Start Channel Finder (scan Wi-Fi channels) on the mesh."""

    def __init__(self, coordinator: VelopCoordinator, serial: str) -> None:
        """Create the button."""
        super().__init__(coordinator, serial, "channel_scan")

    async def async_press(self) -> None:
        """Start the scan through the coordinator use case."""
        await self.coordinator.async_start_channel_scan()


class VelopSpeedTestButton(VelopEntity, ButtonEntity):
    """Start a manual speed check from the hub."""

    def __init__(self, coordinator: VelopCoordinator, serial: str) -> None:
        """Create the button."""
        super().__init__(coordinator, serial, "speed_test")

    async def async_press(self) -> None:
        """Start the speed check through the coordinator use case."""
        await self.coordinator.async_start_speed_test()


def _factory(coordinator: VelopCoordinator, node: Node) -> list[Entity]:
    entities: list[Entity] = [VelopRebootButton(coordinator, node.serial)]
    if node.role is NodeRole.PRIMARY:
        snap = coordinator.data
        if snap.channel_scan_running is not None:
            entities.append(VelopChannelScanButton(coordinator, node.serial))
        if snap.speed_test is not None:
            entities.append(VelopSpeedTestButton(coordinator, node.serial))
    return entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VelopConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up reboot buttons for every node."""
    async_add_node_entities(entry, async_add_entities, _factory)

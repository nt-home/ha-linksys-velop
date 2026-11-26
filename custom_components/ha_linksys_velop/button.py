"""Button platform: reboot the hub or a child node."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity

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


def _factory(coordinator: VelopCoordinator, node: Node) -> list[Entity]:
    return [VelopRebootButton(coordinator, node.serial)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VelopConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up reboot buttons for every node."""
    async_add_node_entities(entry, async_add_entities, _factory)

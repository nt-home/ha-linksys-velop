"""Update platform: firmware status per node (read-only)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity

from .entity import VelopEntity, async_add_node_entities

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import VelopConfigEntry, VelopCoordinator
    from .domain.models import Node

PARALLEL_UPDATES = 0


class VelopFirmwareUpdate(VelopEntity, UpdateEntity):
    """Firmware version and availability of one node."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE

    def __init__(self, coordinator: VelopCoordinator, serial: str) -> None:
        """Create the update entity."""
        super().__init__(coordinator, serial, "firmware")

    @property
    def installed_version(self) -> str | None:
        """Return the installed firmware version."""
        node = self.node
        return None if node is None else node.firmware

    @property
    def latest_version(self) -> str | None:
        """Return the newest known version, or the installed one."""
        node = self.node
        if node is None:
            return None
        status = self.snapshot.firmware.get(node.device_id)
        if status and status.available_version:
            return status.available_version
        return node.firmware


def _factory(coordinator: VelopCoordinator, node: Node) -> list[Entity]:
    return [VelopFirmwareUpdate(coordinator, node.serial)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VelopConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up firmware update entities for every node."""
    async_add_node_entities(entry, async_add_entities, _factory)

"""Shared entity base and dynamic node-entity helper."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import VelopCoordinator

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import VelopConfigEntry
    from .domain.models import HubSnapshot, Node


class VelopEntity(CoordinatorEntity[VelopCoordinator]):
    """Base entity bound to one mesh node (the hub is the primary node)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: VelopCoordinator, serial: str, key: str) -> None:
        """Create the entity for a node serial and entity key."""
        super().__init__(coordinator)
        self._serial = serial
        self._attr_unique_id = f"{serial}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, serial)})

    @property
    def snapshot(self) -> HubSnapshot:
        """Return the latest published snapshot."""
        return self.coordinator.data

    @property
    def node(self) -> Node | None:
        """Return this entity's node when still present."""
        return self.snapshot.node_by_serial.get(self._serial)

    @property
    def available(self) -> bool:
        """Entities are unavailable when the hub or the node is unreachable."""
        return super().available and self.node is not None


@callback
def async_add_node_entities(
    entry: VelopConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[VelopCoordinator, Node], list[Entity]],
) -> None:
    """Create entities for current nodes and for nodes that appear later."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [n for n in coordinator.data.nodes if n.serial not in known]
        if not new:
            return
        known.update(n.serial for n in new)
        async_add_entities([e for n in new for e in factory(coordinator, n)])

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))

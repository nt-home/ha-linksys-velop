"""Device tracker platform: presence for opted-in clients only."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEFAULT_CONSIDER_HOME,
    OPT_CONSIDER_HOME,
    OPT_TRACK_ALL,
    OPT_TRACKED_CLIENTS,
)
from .coordinator import VelopCoordinator

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import VelopConfigEntry
    from .domain.models import Client

PARALLEL_UPDATES = 0


class VelopTracker(CoordinatorEntity[VelopCoordinator], ScannerEntity):
    """Presence of one network client, from live connection state."""

    _attr_has_entity_name = True
    _attr_source_type = SourceType.ROUTER

    def __init__(
        self,
        coordinator: VelopCoordinator,
        client: Client,
        consider_home: int,
        *,
        enabled_default: bool,
    ) -> None:
        """Create the tracker keyed by the client MAC address."""
        super().__init__(coordinator)
        self._mac = client.mac
        self._consider_home = consider_home
        self._last_seen: float | None = time.monotonic() if client.connected else None
        self._attr_unique_id = client.mac
        self._attr_entity_registry_enabled_default = enabled_default
        self._attr_name = None
        self._attr_device_info = DeviceInfo(
            connections={("mac", client.mac)}, name=client.name
        )

    def _client(self) -> Client | None:
        return next(
            (c for c in self.coordinator.data.clients if c.mac == self._mac), None
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        client = self._client()
        if client is not None and client.connected:
            self._last_seen = time.monotonic()
        super()._handle_coordinator_update()

    @property
    def mac_address(self) -> str:
        """Return the client MAC address."""
        return self._mac

    @property
    def is_connected(self) -> bool:
        """Return True while connected or within the consider-home window."""
        client = self._client()
        if client is not None and client.connected:
            return True
        if self._last_seen is None:
            return False
        return time.monotonic() - self._last_seen < self._consider_home

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose only the connected node name and connection type."""
        client = self._client()
        attrs: dict[str, str] = {}
        if client is None or not client.connected:
            return attrs
        if client.connection_type:
            attrs["connection_type"] = client.connection_type
        for node in self.coordinator.data.nodes:
            if node.device_id == client.node_device_id:
                attrs["connected_node"] = node.name
        return attrs


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VelopConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create trackers for selected clients (or all when opted in)."""
    coordinator = entry.runtime_data
    track_all: bool = entry.options.get(OPT_TRACK_ALL, False)
    selected = set(entry.options.get(OPT_TRACKED_CLIENTS, []))
    consider = int(entry.options.get(OPT_CONSIDER_HOME, DEFAULT_CONSIDER_HOME))
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [
            c
            for c in coordinator.data.clients
            if c.mac not in known and (track_all or c.mac in selected)
        ]
        if not new:
            return
        known.update(c.mac for c in new)
        async_add_entities(
            [
                VelopTracker(
                    coordinator,
                    c,
                    consider,
                    enabled_default=c.mac in selected,
                )
                for c in new
            ]
        )

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))

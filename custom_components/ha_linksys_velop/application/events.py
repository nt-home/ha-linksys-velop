"""Debounced detection of automation events from consecutive snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from ..domain.models import NetworkMode

if TYPE_CHECKING:
    from ..domain.models import HubSnapshot

DEBOUNCE_REFRESHES = 2
ABSENT_LIMIT = 3


class EventType(StrEnum):
    """Event types exposed to automations."""

    WAN_CONNECTED = "wan_connected"
    WAN_DISCONNECTED = "wan_disconnected"
    NODE_ONLINE = "node_online"
    NODE_OFFLINE = "node_offline"
    NODE_ADDED = "node_added"
    NODE_REMOVED = "node_removed"
    FIRMWARE_UPDATE_AVAILABLE = "firmware_update_available"
    MODE_CHANGED = "mode_changed"
    AUTHENTICATION_FAILED = "authentication_failed"


EVENT_TYPES: list[str] = [e.value for e in EventType]


@dataclass(frozen=True, slots=True)
class VelopEvent:
    """One detected event."""

    type: EventType
    node_name: str | None = None
    node_serial: str | None = None
    detail: str | None = None


class _Debounced[T]:
    """Track a value that must be seen repeatedly before it counts as changed."""

    def __init__(self, required: int) -> None:
        self._required = required
        self.confirmed: T | None = None
        self._seen = False
        self._pending: T | None = None
        self._count = 0

    def observe(self, value: T) -> bool:
        """Record an observation and return True when the value changed."""
        if not self._seen:
            self._seen = True
            self.confirmed = value
            return False
        if value == self.confirmed:
            self._pending, self._count = None, 0
            return False
        if value == self._pending:
            self._count += 1
        else:
            self._pending, self._count = value, 1
        if self._count >= self._required:
            self.confirmed = value
            self._pending, self._count = None, 0
            return True
        return False


class EventDetector:
    """Compare snapshots and emit events; never replays the initial state."""

    def __init__(self, debounce: int = DEBOUNCE_REFRESHES) -> None:
        """Create a detector with the given debounce requirement."""
        self._debounce = debounce
        self._baseline = False
        self._wan = _Debounced[bool](debounce)
        self._mode = _Debounced[NetworkMode](debounce)
        self._online: dict[str, _Debounced[bool]] = {}
        self._known: set[str] = set()
        self._absent: dict[str, int] = {}
        self._firmware: dict[str, str | None] = {}

    def update(self, snapshot: HubSnapshot) -> tuple[list[VelopEvent], list[str]]:
        """Return detected events and serials that should be removed."""
        events: list[VelopEvent] = []
        self._wan_events(snapshot, events)
        self._mode_events(snapshot, events)
        removed = self._node_events(snapshot, events)
        self._firmware_events(snapshot, events)
        self._baseline = True
        return events, removed

    def _wan_events(self, snapshot: HubSnapshot, events: list[VelopEvent]) -> None:
        connected = snapshot.wan.connected
        if connected is None:
            return
        if self._wan.observe(connected):
            kind = EventType.WAN_CONNECTED if connected else EventType.WAN_DISCONNECTED
            events.append(VelopEvent(kind))

    def _mode_events(self, snapshot: HubSnapshot, events: list[VelopEvent]) -> None:
        if snapshot.mode is NetworkMode.UNKNOWN:
            return
        if self._mode.observe(snapshot.mode):
            events.append(
                VelopEvent(EventType.MODE_CHANGED, detail=snapshot.mode.value)
            )

    def _node_events(
        self, snapshot: HubSnapshot, events: list[VelopEvent]
    ) -> list[str]:
        current = {n.serial: n for n in snapshot.nodes}
        for serial, node in current.items():
            self._absent.pop(serial, None)
            tracker = self._online.setdefault(serial, _Debounced[bool](self._debounce))
            if tracker.observe(node.online):
                kind = EventType.NODE_ONLINE if node.online else EventType.NODE_OFFLINE
                events.append(VelopEvent(kind, node.name, serial))
            if self._baseline and serial not in self._known:
                events.append(VelopEvent(EventType.NODE_ADDED, node.name, serial))
        self._known |= set(current)
        removed: list[str] = []
        for serial in sorted(self._known - set(current)):
            self._absent[serial] = self._absent.get(serial, 0) + 1
            if self._absent[serial] >= ABSENT_LIMIT:
                removed.append(serial)
                events.append(VelopEvent(EventType.NODE_REMOVED, None, serial))
        for serial in removed:
            self._known.discard(serial)
            self._absent.pop(serial, None)
            self._online.pop(serial, None)
            self._firmware.pop(serial, None)
        return removed

    def _firmware_events(self, snapshot: HubSnapshot, events: list[VelopEvent]) -> None:
        for node in snapshot.nodes:
            status = snapshot.firmware.get(node.device_id)
            version = status.available_version if status else None
            previous = self._firmware.get(node.serial)
            if (
                self._baseline
                and version is not None
                and version != previous
                and node.serial in self._firmware
            ):
                events.append(
                    VelopEvent(
                        EventType.FIRMWARE_UPDATE_AVAILABLE,
                        node.name,
                        node.serial,
                        version,
                    )
                )
            self._firmware[node.serial] = version

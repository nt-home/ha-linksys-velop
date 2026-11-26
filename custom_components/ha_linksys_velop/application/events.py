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
    CLIENT_JOINED = "client_joined"
    CLIENT_LEFT = "client_left"


CLIENT_EVENT_TYPES: list[str] = [
    EventType.CLIENT_JOINED.value,
    EventType.CLIENT_LEFT.value,
]
HUB_EVENT_TYPES: list[str] = [
    e.value for e in EventType if e.value not in CLIENT_EVENT_TYPES
]
EVENT_TYPES: list[str] = [e.value for e in EventType]


@dataclass(frozen=True, slots=True)
class VelopEvent:
    """One detected event."""

    type: EventType
    node_name: str | None = None
    node_serial: str | None = None
    detail: str | None = None
    client_name: str | None = None
    client_mac: str | None = None


class _Debounced[T]:
    """Track a value that must be seen repeatedly before it counts as changed."""

    def __init__(self, required: int) -> None:
        self._required = required
        self.confirmed: T | None = None
        self._seen = False
        self._pending: T | None = None
        self._count = 0

    @property
    def idle_absent(self) -> bool:
        """Return True when the value is confirmed None with nothing pending."""
        return self.confirmed is None and self._pending is None

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

    def __init__(
        self, debounce: int = DEBOUNCE_REFRESHES, *, track_clients: bool = False
    ) -> None:
        """Create a detector; client join and leave events are opt-in."""
        self._debounce = debounce
        self._track_clients = track_clients
        self._attached: dict[str, _Debounced[str | None]] = {}
        self._clients_seen = False
        self._labels: dict[str, tuple[str, str]] = {}
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
        if self._track_clients:
            self._client_events(snapshot, events)
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

    def _client_events(self, snapshot: HubSnapshot, events: list[VelopEvent]) -> None:
        """Emit joined/left per node; a roam is a leave on one and a join on another.

        Only the router reports clients, so bridge mode (or an unknown mode)
        pauses tracking and forgets the state instead of reporting a mass leave.
        """
        if snapshot.mode is not NetworkMode.ROUTER:
            self._attached.clear()
            self._clients_seen = False
            return
        self._labels.update({n.device_id: (n.name, n.serial) for n in snapshot.nodes})
        now = {c.mac: c for c in snapshot.clients if c.connected and c.node_device_id}
        for mac, client in now.items():
            tracker = self._attached.get(mac)
            if tracker is None:
                tracker = self._attached[mac] = _Debounced[str | None](self._debounce)
                if self._clients_seen:
                    tracker.observe(None)  # first seen after startup: it just joined
            self._transition(tracker, client.node_device_id, mac, client.name, events)
        for mac in sorted(set(self._attached) - set(now)):
            tracker = self._attached[mac]
            name = next((c.name for c in snapshot.clients if c.mac == mac), mac)
            self._transition(tracker, None, mac, name, events)
            if tracker.idle_absent:
                del self._attached[mac]
        self._clients_seen = True

    def _transition(
        self,
        tracker: _Debounced[str | None],
        node_device_id: str | None,
        mac: str,
        name: str,
        events: list[VelopEvent],
    ) -> None:
        previous = tracker.confirmed
        if not tracker.observe(node_device_id):
            return
        for kind, device_id in (
            (EventType.CLIENT_LEFT, previous),
            (EventType.CLIENT_JOINED, node_device_id),
        ):
            label = self._labels.get(device_id) if device_id else None
            if label is not None:
                events.append(
                    VelopEvent(
                        kind,
                        label[0],
                        label[1],
                        client_name=name,
                        client_mac=mac,
                    )
                )

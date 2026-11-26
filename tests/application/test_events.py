"""Tests for the debounced event detector."""

from __future__ import annotations

from types import MappingProxyType

from custom_components.ha_linksys_velop.application.events import (
    ABSENT_LIMIT,
    EVENT_TYPES,
    EventDetector,
    EventType,
    VelopEvent,
    _Debounced,
)
from custom_components.ha_linksys_velop.domain.models import (
    FirmwareStatus,
    HubIdentity,
    HubSnapshot,
    NetworkMode,
    Node,
    NodeRole,
    SystemStats,
    WanStatus,
)


def node(serial: str, *, online: bool = True, name: str | None = None) -> Node:
    """Build a child node."""
    return Node(
        device_id=f"id-{serial}",
        serial=serial,
        name=name or serial,
        model="MX2000",
        role=NodeRole.CHILD,
        online=online,
    )


def snap(
    nodes: tuple[Node, ...] = (),
    *,
    connected: bool | None = True,
    mode: NetworkMode = NetworkMode.ROUTER,
    firmware: dict[str, str] | None = None,
) -> HubSnapshot:
    """Build a snapshot; firmware maps device id to available version."""
    return HubSnapshot(
        identity=HubIdentity("S", "M", "1"),
        wan=WanStatus(connected, None, None, None, mode),
        nodes=nodes,
        clients=(),
        firmware=MappingProxyType(
            {k: FirmwareStatus(v) for k, v in (firmware or {}).items()}
        ),
        stats=SystemStats(),
    )


def feed(det: EventDetector, *snaps: HubSnapshot) -> list[VelopEvent]:
    """Feed snapshots and collect all events."""
    out: list[VelopEvent] = []
    for s in snaps:
        events, _ = det.update(s)
        out += events
    return out


def types(events: list[VelopEvent]) -> list[EventType]:
    """Event types only."""
    return [e.type for e in events]


def test_event_types_exported() -> None:
    """EVENT_TYPES lists all values."""
    assert "wan_connected" in EVENT_TYPES
    assert len(EVENT_TYPES) == len(EventType)


def test_debounced_helper() -> None:
    """The first value is baseline; changes need N consecutive sightings."""
    d = _Debounced[int](2)
    assert d.observe(1) is False and d.confirmed == 1
    assert d.observe(2) is False
    assert d.observe(1) is False  # flap resets
    assert d.observe(2) is False
    assert d.observe(3) is False  # different pending restarts
    assert d.observe(3) is True and d.confirmed == 3
    assert d.observe(3) is False


def test_no_replay_at_baseline() -> None:
    """The first snapshot yields no events at all."""
    det = EventDetector()
    events, removed = det.update(
        snap((node("A"), node("B", online=False)), firmware={"id-A": "2.0"})
    )
    assert events == [] and removed == []


def test_wan_disconnect_debounced() -> None:
    """WAN changes only fire after two consecutive refreshes."""
    det = EventDetector()
    assert feed(det, snap(connected=True)) == []
    assert feed(det, snap(connected=False)) == []
    events = feed(det, snap(connected=False))
    assert types(events) == [EventType.WAN_DISCONNECTED]
    assert feed(det, snap(connected=False)) == []
    assert feed(det, snap(connected=True)) == []
    assert types(feed(det, snap(connected=True))) == [EventType.WAN_CONNECTED]


def test_wan_flap_suppressed() -> None:
    """A one-refresh blip never fires."""
    det = EventDetector()
    out = feed(
        det,
        snap(connected=True),
        snap(connected=False),
        snap(connected=True),
        snap(connected=False),
        snap(connected=True),
    )
    assert out == []


def test_wan_unknown_ignored() -> None:
    """connected=None is ignored and does not reset debouncing."""
    det = EventDetector()
    feed(det, snap(connected=True))
    assert feed(det, snap(connected=False), snap(connected=None)) == []
    assert types(feed(det, snap(connected=False))) == [EventType.WAN_DISCONNECTED]


def test_mode_changed() -> None:
    """Mode changes are debounced and carry the new mode."""
    det = EventDetector()
    feed(det, snap(mode=NetworkMode.ROUTER))
    assert feed(det, snap(mode=NetworkMode.BRIDGE)) == []
    events = feed(det, snap(mode=NetworkMode.BRIDGE))
    assert events == [VelopEvent(EventType.MODE_CHANGED, detail="bridge")]


def test_mode_unknown_ignored() -> None:
    """UNKNOWN mode never fires or changes baseline."""
    det = EventDetector()
    feed(det, snap(mode=NetworkMode.ROUTER))
    assert feed(det, *[snap(mode=NetworkMode.UNKNOWN)] * 5) == []
    assert feed(det, snap(mode=NetworkMode.ROUTER)) == []


def test_node_offline_online() -> None:
    """Node online state changes are debounced and carry name and serial."""
    det = EventDetector()
    feed(det, snap((node("A", name="Bedroom"),)))
    assert feed(det, snap((node("A", online=False, name="Bedroom"),))) == []
    events = feed(det, snap((node("A", online=False, name="Bedroom"),)))
    assert events == [VelopEvent(EventType.NODE_OFFLINE, "Bedroom", "A")]
    feed(det, snap((node("A"),)))
    assert types(feed(det, snap((node("A"),)))) == [EventType.NODE_ONLINE]


def test_node_added_after_baseline_only() -> None:
    """New nodes after baseline fire node_added once; initial ones do not."""
    det = EventDetector()
    assert feed(det, snap((node("A"),))) == []
    events = feed(det, snap((node("A"), node("B", name="New"))))
    assert events == [VelopEvent(EventType.NODE_ADDED, "New", "B")]
    assert feed(det, snap((node("A"), node("B")))) == []


def test_node_removed_after_three_absences() -> None:
    """A node is removed after ABSENT_LIMIT consecutive absent refreshes."""
    det = EventDetector()
    feed(det, snap((node("A"), node("B"))))
    for _ in range(ABSENT_LIMIT - 1):
        events, removed = det.update(snap((node("A"),)))
        assert events == [] and removed == []
    events, removed = det.update(snap((node("A"),)))
    assert removed == ["B"]
    assert events == [VelopEvent(EventType.NODE_REMOVED, None, "B")]
    events, removed = det.update(snap((node("A"),)))
    assert events == [] and removed == []


def test_absent_counter_resets_on_return() -> None:
    """A node reappearing resets its absence count."""
    det = EventDetector()
    feed(det, snap((node("A"), node("B"))))
    feed(det, snap((node("A"),)), snap((node("A"),)))
    feed(det, snap((node("A"), node("B"))))
    events, removed = det.update(snap((node("A"),)))
    assert events == [] and removed == []
    events, removed = det.update(snap((node("A"),)))
    assert removed == []
    _, removed = det.update(snap((node("A"),)))
    assert removed == ["B"]


def test_removed_node_readded_fires_node_added() -> None:
    """After removal a returning node is announced as added again."""
    det = EventDetector()
    feed(det, snap((node("A"), node("B"))))
    feed(det, *[snap((node("A"),))] * ABSENT_LIMIT)
    events = feed(det, snap((node("A"), node("B", name="Back"))))
    assert events == [VelopEvent(EventType.NODE_ADDED, "Back", "B")]


def test_multiple_removed_sorted() -> None:
    """Several removals are returned sorted by serial."""
    det = EventDetector()
    feed(det, snap((node("C"), node("B"), node("A"))))
    removed: list[str] = []
    for _ in range(ABSENT_LIMIT):
        _, removed = det.update(snap(()))
    assert removed == ["A", "B", "C"]


def test_firmware_update_available() -> None:
    """A new available version fires once for a known node after baseline."""
    det = EventDetector()
    n = node("A", name="Hall")
    feed(det, snap((n,)))
    events = feed(det, snap((n,), firmware={"id-A": "2.0"}))
    assert events == [
        VelopEvent(EventType.FIRMWARE_UPDATE_AVAILABLE, "Hall", "A", "2.0")
    ]
    assert feed(det, snap((n,), firmware={"id-A": "2.0"})) == []
    events = feed(det, snap((n,), firmware={"id-A": "2.1"}))
    assert [e.detail for e in events] == ["2.1"]
    # update installed then clears; no event, and the same version again re-fires
    assert feed(det, snap((n,))) == []
    assert [e.detail for e in feed(det, snap((n,), firmware={"id-A": "2.1"}))] == [
        "2.1"
    ]


def test_firmware_available_at_baseline_not_replayed() -> None:
    """A version already available at baseline does not fire."""
    det = EventDetector()
    n = node("A")
    assert (
        feed(
            det,
            snap((n,), firmware={"id-A": "2.0"}),
            snap((n,), firmware={"id-A": "2.0"}),
        )
        == []
    )


def test_firmware_for_node_added_later_not_announced_first_time() -> None:
    """A node first seen after baseline records firmware silently."""
    det = EventDetector()
    feed(det, snap((node("A"),)))
    events = feed(det, snap((node("A"), node("B")), firmware={"id-B": "9.9"}))
    assert types(events) == [EventType.NODE_ADDED]


def test_firmware_state_cleared_on_removal() -> None:
    """Removed nodes forget firmware state."""
    det = EventDetector()
    feed(det, snap((node("A"), node("B")), firmware={"id-B": "1.0"}))
    feed(det, *[snap((node("A"),))] * ABSENT_LIMIT)
    assert "B" not in det._firmware
    assert "B" not in det._online


def test_custom_debounce() -> None:
    """A debounce of 1 fires on the first change."""
    det = EventDetector(debounce=1)
    feed(det, snap(connected=True))
    assert types(feed(det, snap(connected=False))) == [EventType.WAN_DISCONNECTED]

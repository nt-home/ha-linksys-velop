"""Tests for the debounced event detector."""

from __future__ import annotations

from types import MappingProxyType

from custom_components.ha_linksys_velop.application.events import (
    ABSENT_LIMIT,
    CLIENT_EVENT_TYPES,
    EVENT_TYPES,
    HUB_EVENT_TYPES,
    EventDetector,
    EventType,
    VelopEvent,
    _Debounced,
)
from custom_components.ha_linksys_velop.domain.models import (
    Client,
    FirmwareStatus,
    HubIdentity,
    HubSnapshot,
    NetworkMode,
    Node,
    NodeRole,
    SpeedTestResult,
    SpeedTestState,
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
    clients: tuple[Client, ...] = (),
) -> HubSnapshot:
    """Build a snapshot; firmware maps device id to available version."""
    return HubSnapshot(
        identity=HubIdentity("S", "M", "1"),
        wan=WanStatus(connected, None, None, None, mode),
        nodes=nodes,
        clients=clients,
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


def client(mac: str, on: str | None, name: str | None = None) -> Client:
    """Build a client attached to node serial ``on`` (None means offline)."""
    return Client(
        mac=mac,
        name=name or mac,
        connected=on is not None,
        node_device_id=f"id-{on}" if on else None,
    )


def client_snap(
    *clients: Client, mode: NetworkMode = NetworkMode.ROUTER
) -> HubSnapshot:
    """Snapshot with nodes A and B and the given clients."""
    return snap((node("A"), node("B")), clients=clients, mode=mode)


def tracking() -> EventDetector:
    """Detector with client events switched on."""
    return EventDetector(track_clients=True)


def test_client_event_types_split() -> None:
    """Client events are kept out of the hub event list."""
    assert "client_joined" in EVENT_TYPES and "client_left" in EVENT_TYPES
    assert "client_joined" not in HUB_EVENT_TYPES
    assert CLIENT_EVENT_TYPES == ["client_joined", "client_left"]


def test_clients_off_by_default() -> None:
    """Without the option no client event is ever produced."""
    det = EventDetector()
    assert not feed(det, client_snap(), client_snap(client("m1", "A")))
    assert not det._attached  # noqa: SLF001


def test_clients_no_replay_at_baseline() -> None:
    """Clients already connected at startup raise nothing."""
    det = tracking()
    assert (
        feed(det, client_snap(client("m1", "A")), client_snap(client("m1", "A"))) == []
    )


def test_client_joined_after_baseline_is_debounced() -> None:
    """A new client joins after two consecutive sightings, carrying its node."""
    det = tracking()
    first = feed(det, client_snap(), client_snap(client("m1", "A", "Phone")))
    assert first == []
    events = feed(det, client_snap(client("m1", "A", "Phone")))
    assert events == [
        VelopEvent(
            EventType.CLIENT_JOINED, "A", "A", client_name="Phone", client_mac="m1"
        )
    ]
    assert feed(det, client_snap(client("m1", "A", "Phone"))) == []


def test_client_join_flap_suppressed() -> None:
    """A client seen once and gone again raises nothing."""
    det = tracking()
    assert not feed(
        det,
        client_snap(),
        client_snap(client("m1", "A")),
        client_snap(),
        client_snap(client("m1", "A")),
        client_snap(),
    )


def test_client_left_when_offline_or_removed() -> None:
    """A client that goes offline, or vanishes, leaves the node it was on."""
    det = tracking()
    feed(det, client_snap(client("m1", "A"), client("m2", "B")))
    events = feed(
        det,
        client_snap(client("m1", None), client("m2", "B")),
        client_snap(client("m1", None), client("m2", "B")),
    )
    assert [(e.type, e.node_serial, e.client_mac) for e in events] == [
        (EventType.CLIENT_LEFT, "A", "m1")
    ]
    gone = feed(det, client_snap(), client_snap())
    assert [(e.type, e.node_serial, e.client_mac) for e in gone] == [
        (EventType.CLIENT_LEFT, "B", "m2")
    ]
    assert not det._attached  # noqa: SLF001


def test_client_roam_leaves_one_node_and_joins_another() -> None:
    """Moving between nodes is one leave and one join."""
    det = tracking()
    feed(det, client_snap(client("m1", "A")))
    events = feed(det, client_snap(client("m1", "B")), client_snap(client("m1", "B")))
    assert [(e.type, e.node_serial) for e in events] == [
        (EventType.CLIENT_LEFT, "A"),
        (EventType.CLIENT_JOINED, "B"),
    ]


def test_client_events_paused_outside_router_mode() -> None:
    """Bridge mode reports no clients; that must not look like everyone leaving."""
    det = tracking()
    feed(det, client_snap(client("m1", "A")))
    bridge = client_snap(mode=NetworkMode.BRIDGE)
    # Only the mode change is announced, never a mass client leave.
    assert types(feed(det, bridge, bridge, bridge)) == [EventType.MODE_CHANGED]
    # Back in router mode the client state is re-learned silently.
    back = client_snap(client("m1", "A"))
    assert EventType.CLIENT_JOINED not in types(feed(det, back, back, back))


def test_client_on_unlisted_node_is_ignored() -> None:
    """A client on a node that is not in the snapshot has no node to report."""
    det = tracking()
    feed(det, client_snap(client("m1", "A")))
    moved = client_snap(client("m1", "Z"))
    events = feed(det, moved, moved)
    assert [(e.type, e.node_serial) for e in events] == [(EventType.CLIENT_LEFT, "A")]


# channel scan and speed check -------------------------------------------------


def act(
    *,
    scan: bool | None = False,
    speed: SpeedTestState | None = None,
) -> HubSnapshot:
    """Build a snapshot carrying activity state."""
    base = snap()
    return HubSnapshot(
        identity=base.identity,
        wan=base.wan,
        nodes=base.nodes,
        clients=base.clients,
        firmware=base.firmware,
        stats=base.stats,
        channel_scan_running=scan,
        speed_test=speed,
    )


def result(result_id: int, code: str = "Success") -> SpeedTestResult:
    """Build a finished speed result."""
    return SpeedTestResult(result_id, None, code, 92.0, 95.0, 3.0)


def test_channel_scan_events() -> None:
    """Start and finish fire once each and never replay at baseline."""
    det = EventDetector()
    assert feed(det, act(scan=True)) == []
    assert types(feed(det, act(scan=True))) == []
    assert types(feed(det, act(scan=False))) == [EventType.CHANNEL_SCAN_COMPLETED]
    assert feed(det, act(scan=False)) == []
    assert types(feed(det, act(scan=True))) == [EventType.CHANNEL_SCAN_STARTED]


def test_channel_scan_unsupported_is_silent() -> None:
    """None means unsupported: no events and no state kept."""
    det = EventDetector()
    assert feed(det, act(scan=None), act(scan=None)) == []
    assert feed(det, act(scan=False)) == []
    assert types(feed(det, act(scan=True))) == [EventType.CHANNEL_SCAN_STARTED]


def test_speed_test_events() -> None:
    """A run fires started, then completed with the numbers."""
    det = EventDetector()
    idle = SpeedTestState(False, result(1))
    assert feed(det, act(speed=idle)) == []
    started = feed(det, act(speed=SpeedTestState(True, result(1))))
    assert types(started) == [EventType.SPEED_TEST_STARTED]
    assert feed(det, act(speed=SpeedTestState(True, result(1)))) == []
    done = feed(det, act(speed=SpeedTestState(False, result(2))))
    assert types(done) == [EventType.SPEED_TEST_COMPLETED]
    assert done[0].data == {
        "download_mbps": 92.0,
        "upload_mbps": 95.0,
        "latency_ms": 3.0,
    }
    assert feed(det, act(speed=SpeedTestState(False, result(2)))) == []


def test_speed_test_failed_event() -> None:
    """A finished run without a success exit code is a failure event."""
    det = EventDetector()
    feed(det, act(speed=SpeedTestState(False, None)))
    out = feed(det, act(speed=SpeedTestState(False, result(1, "AbortedByUser"))))
    assert types(out) == [EventType.SPEED_TEST_FAILED]
    assert out[0].detail == "AbortedByUser"
    assert out[0].data is None


def test_speed_test_completed_without_numbers() -> None:
    """Missing numbers are left out of the event data."""
    det = EventDetector()
    feed(det, act(speed=SpeedTestState(False, None)))
    bare = SpeedTestResult(5, None, "Success")
    out = feed(det, act(speed=SpeedTestState(False, bare)))
    assert types(out) == [EventType.SPEED_TEST_COMPLETED]
    assert out[0].data == {}


def test_speed_test_existing_result_not_replayed_and_idless_ignored() -> None:
    """The first result is baseline; results without an id never fire."""
    det = EventDetector()
    assert feed(det, act(speed=SpeedTestState(False, result(4)))) == []
    idless = SpeedTestResult(None, None, "Success")
    assert feed(det, act(speed=SpeedTestState(False, idless))) == []
    assert feed(det, act(speed=SpeedTestState(False, result(4)))) == []


def test_speed_test_unsupported_is_silent() -> None:
    """No speed state means nothing to report."""
    det = EventDetector()
    assert feed(det, act(speed=None), act(speed=None)) == []

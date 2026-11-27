"""Entity, event, action and failure-mode tests against the emulated router."""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import MockConfigEntry

from custom_components.ha_linksys_velop.const import (
    DOMAIN,
    EVENT_BUS_NAME,
    OPT_CLIENT_EVENTS,
    OPT_FAST_INTERVAL,
    OPT_TRACK_ALL,
    OPT_TRACKED_CLIENTS,
)
from custom_components.ha_linksys_velop.coordinator import VelopCoordinator
from custom_components.ha_linksys_velop.sensor import _sentence
from tests.emulator.server import (
    PRIMARY_SERIAL,
    Emulator,
    client_device,
    node_device,
    three_node_mesh,
)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> VelopCoordinator:
    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


def _capture(hass: HomeAssistant) -> list[Event]:
    events: list[Event] = []
    hass.bus.async_listen(EVENT_BUS_NAME, events.append)
    return events


async def _refresh(
    hass: HomeAssistant, coordinator: VelopCoordinator, n: int = 1
) -> None:
    for _ in range(n):
        await coordinator.async_refresh()
        await hass.async_block_till_done()


async def test_router_mode_entities(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Hub and node entities reflect the emulated mesh."""
    await _setup(hass, mock_entry)
    assert hass.states.get("binary_sensor.living_room_internet").state == "on"
    assert hass.states.get("sensor.living_room_internet_status").state == "Connected"
    assert hass.states.get("sensor.living_room_mesh_operating_mode").state == "router"
    assert hass.states.get("sensor.living_room_network_clients").state == "3"
    assert hass.states.get("sensor.bedroom_mesh_backhaul_type").state == "Wireless"
    assert hass.states.get("sensor.bedroom_mesh_backhaul_speed").state == "866.0"
    assert hass.states.get("sensor.bedroom_mesh_parent_node").state == "Living Room"
    assert hass.states.get("binary_sensor.bedroom_connectivity").state == "on"
    assert hass.states.get("update.bedroom_firmware").state == "off"
    assert hass.states.get("button.office_reboot") is not None
    assert hass.states.get("event.living_room_network_events") is not None


async def test_clients_not_exposed_by_default(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """No client entities or identifiers exist unless the user opts in."""
    await _setup(hass, mock_entry)
    assert not hass.states.async_entity_ids("device_tracker")
    for state in hass.states.async_all():
        assert "AA:BB:CC" not in str(state.attributes)


async def test_tracked_clients_presence(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """Selected clients get trackers with consider-home semantics."""
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_entry, options={OPT_TRACKED_CLIENTS: ["aa:bb:cc:00:00:01"]}
    )
    coordinator = await _setup(hass, mock_entry)
    tracker = hass.states.get("device_tracker.client_1")
    assert tracker is not None and tracker.state == "home"
    assert tracker.attributes["connected_node"] == "Bedroom"
    emulator.devices = three_node_mesh(with_clients=0) + [
        d for d in emulator.devices if d["deviceID"] == "client-1"
    ]
    emulator.devices[-1]["connections"] = []
    await _refresh(hass, coordinator)
    assert hass.states.get("device_tracker.client_1").state == "home"


async def test_track_all_disabled_by_default(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Track-all trackers are created disabled in the registry."""
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_entry, options={OPT_TRACK_ALL: True})
    await _setup(hass, mock_entry)
    ents = [
        e
        for e in er.async_entries_for_config_entry(
            er.async_get(hass), mock_entry.entry_id
        )
        if e.domain == "device_tracker"
    ]
    assert len(ents) == 3
    assert all(e.disabled for e in ents)


async def test_bridge_mode_skips_router_entities(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """Bridge mode keeps nodes but never creates client entities."""
    emulator.wan_type = "Bridge"
    await _setup(hass, mock_entry)
    assert hass.states.get("sensor.living_room_mesh_operating_mode").state == "bridge"
    # No hub-wide total in bridge mode, but each node still counts its clients.
    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id(
            "sensor", DOMAIN, f"{PRIMARY_SERIAL}_clients_total"
        )
        is None
    )
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{PRIMARY_SERIAL}_client_count")
        is not None
    )
    assert hass.states.get("sensor.bedroom_network_clients").state == "0"
    assert hass.states.get("sensor.bedroom_mesh_backhaul_type") is not None
    assert not hass.states.async_entity_ids("device_tracker")


async def test_wan_events_debounced(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """WAN loss fires once after two consecutive refreshes, never at startup."""
    events = _capture(hass)
    coordinator = await _setup(hass, mock_entry)
    assert events == []
    emulator.wan_status = "Disconnected"
    await _refresh(hass, coordinator)
    assert events == []
    await _refresh(hass, coordinator)
    assert [e.data["type"] for e in events] == ["wan_disconnected"]
    assert events[0].data["device_id"]
    assert hass.states.get("binary_sensor.living_room_internet").state == "off"
    entity_state = hass.states.get("event.living_room_network_events")
    assert entity_state.attributes["event_type"] == "wan_disconnected"
    emulator.wan_status = "Connected"
    await _refresh(hass, coordinator, 2)
    assert events[-1].data["type"] == "wan_connected"


async def test_node_offline_added_removed(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """Node transitions emit events and stale devices are removed."""
    events = _capture(hass)
    coordinator = await _setup(hass, mock_entry)
    office = emulator.devices[2]
    saved = office["connections"]
    office["connections"] = []
    await _refresh(hass, coordinator, 2)
    assert events[-1].data["type"] == "node_offline"
    assert hass.states.get("binary_sensor.office_connectivity").state == "off"
    office["connections"] = saved
    await _refresh(hass, coordinator, 2)
    assert events[-1].data["type"] == "node_online"

    from tests.emulator.server import node_device  # noqa: PLC0415

    emulator.devices.append(
        node_device("node-4", "12K10M44444444", "Slave", "Garage", parent="node-1")
    )
    await _refresh(hass, coordinator)
    assert events[-1].data["type"] == "node_added"
    assert hass.states.get("button.garage_reboot") is not None

    emulator.devices = [d for d in emulator.devices if d["deviceID"] != "node-4"]
    await _refresh(hass, coordinator, 3)
    assert events[-1].data["type"] == "node_removed"
    registry = dr.async_get(hass)
    assert (
        registry.async_get_device_by_identifier(
            (DOMAIN, "12K10M44444444"), mock_entry.entry_id
        )
        is None
    )


async def test_firmware_update_event(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """A newly available firmware raises one event and flips the update entity."""
    events = _capture(hass)
    coordinator = await _setup(hass, mock_entry)
    emulator.firmware = [
        {"deviceUUID": "node-2", "availableUpdate": {"firmwareVersion": "9.9.9"}}
    ]
    coordinator._slow_at = None  # force the slow tier due
    await _refresh(hass, coordinator)
    assert [e.data["type"] for e in events] == ["firmware_update_available"]
    update = hass.states.get("update.bedroom_firmware")
    assert update.state == "on"
    assert update.attributes["latest_version"] == "9.9.9"


async def test_mode_change_reloads_once(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """Router to bridge fires mode_changed and reloads the entry."""
    events = _capture(hass)
    coordinator = await _setup(hass, mock_entry)
    emulator.wan_type = "Bridge"
    await _refresh(hass, coordinator, 2)
    await hass.async_block_till_done()
    assert [e.data["type"] for e in events] == ["mode_changed"]
    assert mock_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.living_room_network_clients") is None


async def test_reboot_button_and_cooldown(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """The hub button reboots the primary; repeats inside 60 s are refused."""
    await _setup(hass, mock_entry)
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.living_room_reboot"}, blocking=True
    )
    assert emulator.reboots == [{"action": "core/Reboot"}]
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.living_room_reboot"}, blocking=True
        )
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.bedroom_reboot"}, blocking=True
    )
    assert emulator.reboots[-1] == {"action": "core/Reboot2"}


async def test_reboot_connection_drop_is_success(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """A dropped connection while rebooting is treated as success."""
    await _setup(hass, mock_entry)
    emulator.drop_on_reboot = True
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.living_room_reboot"}, blocking=True
    )
    assert emulator.reboots == [{"action": "dropped"}]


async def test_services(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """reboot_node and refresh_now work; unknown devices are rejected."""
    await _setup(hass, mock_entry)
    registry = dr.async_get(hass)
    child = registry.async_get_device_by_identifier(
        (DOMAIN, "12K10M33333333"), mock_entry.entry_id
    )
    await hass.services.async_call(
        DOMAIN, "reboot_node", {"device_id": child.id}, blocking=True
    )
    assert emulator.reboots == [{"action": "core/Reboot2"}]
    emulator.calls.clear()
    await hass.services.async_call(DOMAIN, "refresh_now", {}, blocking=True)
    assert emulator.calls
    other = registry.async_get_or_create(
        config_entry_id=mock_entry.entry_id, identifiers={("other", "x")}
    )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "reboot_node", {"device_id": other.id}, blocking=True
        )


async def test_refresh_now_without_hub(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """refresh_now explains itself when nothing is loaded."""
    mock_entry.add_to_hass(hass)
    from custom_components.ha_linksys_velop import async_setup  # noqa: PLC0415

    await async_setup(hass, {})
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "refresh_now", {}, blocking=True)


async def test_unreachable_marks_unavailable_and_recovers(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """Failures make entities unavailable with back-off, then recovery restores."""
    coordinator = await _setup(hass, mock_entry)
    base = coordinator.update_interval
    emulator.status_override = 500
    await _refresh(hass, coordinator, 3)
    assert hass.states.get("sensor.living_room_internet_status").state == "unavailable"
    assert coordinator.update_interval > base
    assert coordinator.update_interval.total_seconds() <= 330
    emulator.status_override = None
    await _refresh(hass, coordinator)
    assert hass.states.get("sensor.living_room_internet_status").state == "Connected"
    assert coordinator.update_interval == base


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """A rejected password mid-run raises reauth and fires an event once."""
    events = _capture(hass)
    coordinator = await _setup(hass, mock_entry)
    emulator.password = "changed-password"  # noqa: S105
    await _refresh(hass, coordinator)
    assert [e.data["type"] for e in events] == ["authentication_failed"]
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_account_locked_creates_repair(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """A locked account raises a repair issue instead of retrying."""
    from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415

    coordinator = await _setup(hass, mock_entry)
    emulator.password = "changed-password"  # noqa: S105
    emulator.locked = True
    await _refresh(hass, coordinator)
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"account_locked_{PRIMARY_SERIAL}"
    )
    assert issue is not None


async def test_setup_retry_when_unreachable(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """An unreachable hub leaves the entry retrying, not failed."""
    emulator.status_override = 500
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    assert mock_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_wrong_hub_retry(
    hass: HomeAssistant, entry_data: dict[str, object], emulator: Emulator
) -> None:
    """An entry whose serial no longer matches the endpoint does not load."""
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data, unique_id="OTHER-SERIAL")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_child_node_retry(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """Pointing at a child node (not master) never loads."""
    emulator.mode = "slave"
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    assert mock_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_wrong_password_reauth(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """A wrong stored password triggers setup error + reauth."""
    emulator.password = "other-password"  # noqa: S105
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    assert mock_entry.state is ConfigEntryState.SETUP_ERROR
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_excluded_child_not_created(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Children left out in options never become devices."""
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_entry, options={"included_children": ["12K10M22222222"]}
    )
    await _setup(hass, mock_entry)
    assert hass.states.get("button.bedroom_reboot") is not None
    assert hass.states.get("button.office_reboot") is None


async def test_area_assigned_on_first_creation_only(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Areas apply when the device is created and survive later edits."""
    from homeassistant.helpers import area_registry as ar  # noqa: PLC0415

    area = ar.async_get(hass).async_create("Bedroom area")
    other = ar.async_get(hass).async_create("Elsewhere")
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_entry, options={"areas": {"12K10M22222222": area.id}}
    )
    coordinator = await _setup(hass, mock_entry)
    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier(
        (DOMAIN, "12K10M22222222"), mock_entry.entry_id
    )
    assert device.area_id == area.id
    registry.async_update_device(device.id, area_id=other.id)
    await _refresh(hass, coordinator)
    assert registry.async_get(device.id).area_id == other.id


async def test_diagnostics_redacted(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Diagnostics contain counts but no password, host, serial or MAC."""
    from custom_components.ha_linksys_velop.diagnostics import (  # noqa: PLC0415
        async_get_config_entry_diagnostics,
    )

    await _setup(hass, mock_entry)
    data = await async_get_config_entry_diagnostics(hass, mock_entry)
    text = repr(data)
    assert data["node_count"] == 3
    assert data["mode"] == "router"
    for secret in ("example-password", "127.0.0.1", PRIMARY_SERIAL, "AA:BB:CC"):
        assert secret not in text


async def test_device_removal_guard(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Present nodes cannot be deleted; unknown devices can."""
    from custom_components.ha_linksys_velop import (  # noqa: PLC0415
        async_remove_config_entry_device,
    )

    await _setup(hass, mock_entry)
    registry = dr.async_get(hass)
    live = registry.async_get_device_by_identifier(
        (DOMAIN, PRIMARY_SERIAL), mock_entry.entry_id
    )
    assert not await async_remove_config_entry_device(hass, mock_entry, live)
    ghost = registry.async_get_or_create(
        config_entry_id=mock_entry.entry_id, identifiers={(DOMAIN, "GHOST")}
    )
    assert await async_remove_config_entry_device(hass, mock_entry, ghost)


async def test_password_never_logged(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    emulator: Emulator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Debug logs across setup, failure and recovery never contain secrets."""
    caplog.set_level("DEBUG")
    coordinator = await _setup(hass, mock_entry)
    emulator.status_override = 500
    await _refresh(hass, coordinator)
    emulator.status_override = None
    emulator.password = "changed-password"  # noqa: S105
    await _refresh(hass, coordinator)
    assert "example-password" not in caplog.text
    assert "changed-password" not in caplog.text
    assert "ZXhhbXBsZS1wYXNzd29yZA" not in caplog.text


async def test_reboot_failure_paths(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """Auth failure starts reauth; other failures are reported and retryable."""
    coordinator = await _setup(hass, mock_entry)
    with pytest.raises(HomeAssistantError):
        await coordinator.async_reboot_node("UNKNOWN")
    emulator.unknown_actions = {"http://linksys.com/jnap/core/Reboot"}
    with pytest.raises(HomeAssistantError):
        await coordinator.async_reboot_node(PRIMARY_SERIAL)
    emulator.unknown_actions = set()
    await coordinator.async_reboot_node(PRIMARY_SERIAL)  # cooldown cleared on failure
    coordinator._reboot_at.clear()
    emulator.password = "changed-password"  # noqa: S105
    with pytest.raises(HomeAssistantError):
        await coordinator.async_reboot_node(PRIMARY_SERIAL)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_vanished_node_entities_unavailable(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """A node missing for one refresh makes its entities unavailable, not stale."""
    coordinator = await _setup(hass, mock_entry)
    emulator.devices = [d for d in emulator.devices if d["deviceID"] != "node-3"]
    await _refresh(hass, coordinator)
    assert hass.states.get("sensor.office_network_ipv4_address").state == "unavailable"
    assert hass.states.get("update.office_firmware").state == "unavailable"


async def test_setup_account_locked_reauth(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """A locked account during identity lookup starts reauth."""
    emulator.require_auth_for_info = True
    emulator.locked = True
    mock_entry.add_to_hass(hass)
    mock_entry.data  # noqa: B018
    await hass.config_entries.async_setup(mock_entry.entry_id)
    assert mock_entry.state is ConfigEntryState.SETUP_ERROR


async def test_tracker_consider_home_window(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """A departed client stays home inside the window, then goes away."""
    from custom_components.ha_linksys_velop.device_tracker import (  # noqa: PLC0415
        VelopTracker,
    )

    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_entry,
        options={OPT_TRACKED_CLIENTS: ["aa:bb:cc:00:00:01"], "consider_home": 0},
    )
    coordinator = await _setup(hass, mock_entry)
    emulator.devices[3]["connections"] = []
    await _refresh(hass, coordinator, 2)
    assert hass.states.get("device_tracker.client_1").state == "not_home"
    entity = VelopTracker.__new__(VelopTracker)
    entity._last_seen = None
    entity._mac = "zz"
    entity._consider_home = 10
    entity.coordinator = coordinator
    assert entity.is_connected is False


def _enable_client_events(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, OPT_CLIENT_EVENTS: True}
    )


async def test_client_events_off_by_default(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """No client activity entity or bus event exists unless opted in."""
    events = _capture(hass)
    coordinator = await _setup(hass, mock_entry)
    emulator.devices.append(client_device(9, "node-3"))
    await _refresh(hass, coordinator, 3)
    assert hass.states.get("event.office_network_client_activity") is None
    assert hass.states.get("event.living_room_network_client_activity") is None
    assert events == []


async def test_client_join_leave_events_per_node(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """A client joining, roaming and leaving fires on the node it touched."""
    _enable_client_events(hass, mock_entry)
    events = _capture(hass)
    coordinator = await _setup(hass, mock_entry)
    for name in ("living_room", "bedroom", "office"):
        assert hass.states.get(f"event.{name}_network_client_activity") is not None
    assert events == []

    emulator.devices.append(client_device(9, "node-3"))
    await _refresh(hass, coordinator)
    assert events == []
    await _refresh(hass, coordinator)
    assert [e.data["type"] for e in events] == ["client_joined"]
    data = events[0].data
    assert data["node_name"] == "Office"
    assert data["node_serial"] == "12K10M33333333"
    assert data["client_name"] == "client-9"
    assert data["client_mac"] == "aa:bb:cc:00:00:09"
    devices = dr.async_get(hass)
    office = devices.async_get_device_by_identifier(
        (DOMAIN, "12K10M33333333"), mock_entry.entry_id
    )
    assert data["device_id"] == office.id
    office_state = hass.states.get("event.office_network_client_activity")
    assert office_state.attributes["event_type"] == "client_joined"
    assert office_state.attributes["client_name"] == "client-9"
    assert (
        hass.states.get("event.bedroom_network_client_activity").attributes[
            "event_type"
        ]
        is None
    )
    # The hub events entity never receives client events.
    assert (
        hass.states.get("event.living_room_network_events").attributes["event_type"]
        is None
    )

    emulator.devices[-1]["connections"][0]["parentDeviceID"] = "node-2"
    await _refresh(hass, coordinator, 2)
    assert [(e.data["type"], e.data["node_name"]) for e in events[1:]] == [
        ("client_left", "Office"),
        ("client_joined", "Bedroom"),
    ]
    assert (
        hass.states.get("event.bedroom_network_client_activity").attributes[
            "event_type"
        ]
        == "client_joined"
    )

    emulator.devices[-1]["connections"] = []
    await _refresh(hass, coordinator, 2)
    assert (events[-1].data["type"], events[-1].data["node_name"]) == (
        "client_left",
        "Bedroom",
    )
    assert len(events) == 4


async def test_client_events_removed_when_option_off(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Switching the option off removes the per-node entities on reload."""
    _enable_client_events(hass, mock_entry)
    await _setup(hass, mock_entry)
    registry = er.async_get(hass)
    assert registry.async_get("event.office_network_client_activity") is not None
    hass.config_entries.async_update_entry(mock_entry, options={})
    await hass.config_entries.async_reload(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get("event.office_network_client_activity") is None
    assert registry.async_get("event.living_room_network_events") is not None


async def test_client_events_not_created_in_bridge_mode(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """Bridge mode has no client data, so no client activity entities exist."""
    emulator.wan_type = "Bridge"
    _enable_client_events(hass, mock_entry)
    await _setup(hass, mock_entry)
    assert hass.states.get("event.office_network_client_activity") is None
    assert hass.states.get("event.living_room_network_events") is not None


async def test_node_client_count_follows_refresh_interval(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """Per-node client counts update on every refresh, at the Velop options rate."""
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_entry, options={OPT_FAST_INTERVAL: 45})
    coordinator = await _setup(hass, mock_entry)
    assert coordinator.update_interval.total_seconds() == 45
    assert hass.states.get("sensor.bedroom_network_clients").state == "2"
    assert hass.states.get("sensor.office_network_clients").state == "0"
    emulator.devices.append(client_device(9, "node-3"))
    emulator.devices[3]["connections"] = []  # client-1 (on node-2) disconnects
    await _refresh(hass, coordinator)
    assert hass.states.get("sensor.bedroom_network_clients").state == "1"
    assert hass.states.get("sensor.office_network_clients").state == "1"


async def test_bridge_mode_node_client_count_refreshes(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """In bridge mode the node count is its Wi-Fi clients, refreshed each poll."""
    emulator.wan_type = "Bridge"
    emulator.wireless = [{"deviceID": "node-2", "connections": [{}, {}, {}]}]
    coordinator = await _setup(hass, mock_entry)
    assert hass.states.get("sensor.bedroom_network_clients").state == "3"
    assert hass.states.get("sensor.office_network_clients").state == "0"
    emulator.wireless = [{"deviceID": "node-3", "connections": [{}]}]
    await _refresh(hass, coordinator)
    assert hass.states.get("sensor.bedroom_network_clients").state == "0"
    assert hass.states.get("sensor.office_network_clients").state == "1"


# channel scan and speed check -------------------------------------------------


async def test_activity_entities_created(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """The primary node gets the buttons, running sensors and result sensors."""
    await _setup(hass, mock_entry)
    for entity_id in (
        "button.living_room_find_channels",
        "button.living_room_check_speed",
        "binary_sensor.living_room_mesh_channel_scan_running",
        "binary_sensor.living_room_speed_check_running",
        "sensor.living_room_speed_check_download",
        "sensor.living_room_speed_check_upload",
        "sensor.living_room_speed_check_ping",
        "sensor.living_room_speed_check_last_run",
    ):
        assert hass.states.get(entity_id) is not None, entity_id
    assert hass.states.get("button.bedroom_find_channels") is None
    assert hass.states.get("button.bedroom_check_speed") is None
    assert hass.states.get("sensor.living_room_speed_check_download").state == "unknown"
    registry = er.async_get(hass)
    entry = registry.async_get("sensor.living_room_speed_check_download")
    assert entry is not None
    assert entry.entity_category is not None
    assert entry.entity_category.value == "diagnostic"
    result = registry.async_get("sensor.living_room_speed_check_result")
    assert result is not None
    assert result.disabled


async def test_activity_entities_absent_when_unsupported(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """Firmware without the actions gets none of the new entities."""
    emulator.unknown_actions |= {
        "http://linksys.com/jnap/nodes/setup/GetSelectedChannels",
        "http://linksys.com/jnap/healthcheck/GetHealthCheckStatus",
        "http://linksys.com/jnap/healthcheck/GetHealthCheckResults",
    }
    await _setup(hass, mock_entry)
    assert hass.states.get("button.living_room_reboot") is not None
    for entity_id in (
        "button.living_room_find_channels",
        "button.living_room_check_speed",
        "binary_sensor.living_room_mesh_channel_scan_running",
        "binary_sensor.living_room_speed_check_running",
        "sensor.living_room_speed_check_download",
    ):
        assert hass.states.get(entity_id) is None, entity_id


async def test_speed_check_button_events_and_sensors(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """Pressing the button runs a check; results and events follow."""
    events = _capture(hass)
    coordinator = await _setup(hass, mock_entry)
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.living_room_check_speed"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert emulator.speed_starts == 1
    assert coordinator.data.speed_test is not None
    assert coordinator.data.speed_test.running is True
    assert coordinator.update_interval is not None
    assert coordinator.update_interval.total_seconds() == 5
    assert (
        hass.states.get("binary_sensor.living_room_speed_check_running").state == "on"
    )
    with pytest.raises(HomeAssistantError):  # already running
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.living_room_check_speed"},
            blocking=True,
        )
    assert emulator.speed_starts == 1
    emulator.finish_speed()
    await _refresh(hass, coordinator)
    assert [e.data["type"] for e in events] == [
        "speed_test_started",
        "speed_test_completed",
    ]
    done = events[-1].data
    assert (done["download_mbps"], done["upload_mbps"], done["latency_ms"]) == (
        92.0,
        95.0,
        3.0,
    )
    assert done["device_id"]
    assert hass.states.get("sensor.living_room_speed_check_download").state == "92.0"
    assert hass.states.get("sensor.living_room_speed_check_upload").state == "95.0"
    assert hass.states.get("sensor.living_room_speed_check_ping").state == "3.0"
    assert (
        hass.states.get("sensor.living_room_speed_check_last_run").state
        == "2026-11-27T08:47:00+00:00"
    )
    assert (
        hass.states.get("binary_sensor.living_room_speed_check_running").state == "off"
    )
    entity_state = hass.states.get("event.living_room_network_events")
    assert entity_state.attributes["event_type"] == "speed_test_completed"
    assert entity_state.attributes["download_mbps"] == 92.0
    assert coordinator.update_interval.total_seconds() == 5  # inside the grace window


async def test_speed_check_failed_event(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """An aborted run fires the failed event with the exit code."""
    events = _capture(hass)
    coordinator = await _setup(hass, mock_entry)
    await coordinator.async_start_speed_test()
    emulator.finish_speed(exit_code="SpeedTestExecutionError")
    await _refresh(hass, coordinator)
    assert [e.data["type"] for e in events][-1] == "speed_test_failed"
    assert events[-1].data["detail"] == "SpeedTestExecutionError"


async def test_channel_scan_button_and_events(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """Scan Channels starts Channel Finder and reports its start and end."""
    events = _capture(hass)
    coordinator = await _setup(hass, mock_entry)
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.living_room_find_channels"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert emulator.scan_starts == 1
    assert (
        hass.states.get("binary_sensor.living_room_mesh_channel_scan_running").state
        == "on"
    )
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.living_room_find_channels"},
            blocking=True,
        )
    assert emulator.scan_starts == 1
    emulator.finish_scan()
    await _refresh(hass, coordinator)
    assert [e.data["type"] for e in events] == [
        "channel_scan_started",
        "channel_scan_completed",
    ]
    assert (
        hass.states.get("binary_sensor.living_room_mesh_channel_scan_running").state
        == "off"
    )


async def test_app_started_run_is_detected(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """A scan or speed check started elsewhere (the app) is seen too."""
    events = _capture(hass)
    coordinator = await _setup(hass, mock_entry)
    emulator.scan_running = True
    emulator.speed_starts = 1
    emulator.speed_running = True
    await _refresh(hass, coordinator)
    assert {e.data["type"] for e in events} == {
        "channel_scan_started",
        "speed_test_started",
    }
    assert coordinator.update_interval is not None
    assert coordinator.update_interval.total_seconds() == 5


async def test_polling_returns_to_normal_when_idle(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Without a run and outside the grace window the normal interval returns."""
    coordinator = await _setup(hass, mock_entry)
    coordinator._active_until = 0.0  # noqa: SLF001
    await _refresh(hass, coordinator)
    assert coordinator.update_interval is not None
    assert coordinator.update_interval.total_seconds() == 60


async def test_start_failures(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """Hub refusals and a lost session surface as Home Assistant errors."""
    coordinator = await _setup(hass, mock_entry)
    emulator.scan_start_result = "ErrorAutoChannelSelectionAlreadyInProgress"
    with pytest.raises(HomeAssistantError) as busy:
        await coordinator.async_start_channel_scan()
    assert busy.value.translation_key == "channel_scan_running"
    emulator.scan_start_result = "_ErrorUnknownAction"
    with pytest.raises(HomeAssistantError) as unknown:
        await coordinator.async_start_channel_scan()
    assert unknown.value.translation_key == "not_supported"
    emulator.scan_start_result = "_ErrorBoom"
    with pytest.raises(HomeAssistantError) as failed:
        await coordinator.async_start_channel_scan()
    assert failed.value.translation_key == "start_failed"
    emulator.scan_start_result = "_ErrorUnauthorized"
    with pytest.raises(HomeAssistantError) as auth:
        await coordinator.async_start_channel_scan()
    assert auth.value.translation_key == "invalid_auth"
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_start_not_supported(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """The coordinator refuses to start what the firmware does not offer."""
    emulator.unknown_actions |= {
        "http://linksys.com/jnap/nodes/setup/GetSelectedChannels",
        "http://linksys.com/jnap/healthcheck/GetHealthCheckStatus",
        "http://linksys.com/jnap/healthcheck/GetHealthCheckResults",
    }
    coordinator = await _setup(hass, mock_entry)
    with pytest.raises(HomeAssistantError) as scan:
        await coordinator.async_start_channel_scan()
    assert scan.value.translation_key == "not_supported"
    with pytest.raises(HomeAssistantError) as speed:
        await coordinator.async_start_speed_test()
    assert speed.value.translation_key == "not_supported"
    assert emulator.scan_starts == 0
    assert emulator.speed_starts == 0


async def test_speed_start_failure(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """A speed check start the firmware does not know reports not supported."""
    coordinator = await _setup(hass, mock_entry)
    emulator.unknown_actions.add("http://linksys.com/jnap/healthcheck/RunHealthCheck")
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_start_speed_test()
    assert err.value.translation_key == "not_supported"


async def test_diagnostics_include_speed_summary(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """Diagnostics carry the speed numbers without identifying data."""
    from custom_components.ha_linksys_velop.diagnostics import (  # noqa: PLC0415
        async_get_config_entry_diagnostics,
    )

    emulator.speed_starts = 1
    emulator.finish_speed()
    await _setup(hass, mock_entry)
    data = await async_get_config_entry_diagnostics(hass, mock_entry)
    assert data["channel_scan_running"] is False
    assert data["speed_test"] == {
        "running": False,
        "last_exit_code": "Success",
        "last_download_mbps": 92.0,
        "last_upload_mbps": 95.0,
        "last_latency_ms": 3.0,
    }


async def test_diagnostics_speed_unsupported(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """Unsupported firmware reports no speed summary."""
    from custom_components.ha_linksys_velop.diagnostics import (  # noqa: PLC0415
        async_get_config_entry_diagnostics,
    )

    emulator.unknown_actions |= {
        "http://linksys.com/jnap/healthcheck/GetHealthCheckStatus",
        "http://linksys.com/jnap/healthcheck/GetHealthCheckResults",
    }
    await _setup(hass, mock_entry)
    data = await async_get_config_entry_diagnostics(hass, mock_entry)
    assert data["speed_test"] is None


async def test_busy_hub_timeout_keeps_data_during_run(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """A hub too busy to answer mid speed check does not go unavailable."""
    from unittest.mock import patch  # noqa: PLC0415

    from custom_components.ha_linksys_velop.domain.errors import (  # noqa: PLC0415
        ConnectionTimeoutError,
    )

    events = _capture(hass)
    coordinator = await _setup(hass, mock_entry)
    await coordinator.async_start_speed_test()
    with patch.object(
        coordinator.client, "async_fetch_fast", side_effect=ConnectionTimeoutError
    ):
        await _refresh(hass, coordinator, 3)
    assert coordinator.last_update_success is True
    assert coordinator.data.speed_test is not None
    assert coordinator.data.speed_test.running is True
    assert coordinator.update_interval is not None
    assert coordinator.update_interval.total_seconds() == 5
    assert hass.states.get("binary_sensor.living_room_internet").state != "unavailable"
    emulator.finish_speed()
    await _refresh(hass, coordinator)
    assert [e.data["type"] for e in events][-1] == "speed_test_completed"


async def test_timeout_outside_a_run_still_fails(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Without a run in progress a timeout is a normal failure."""
    from unittest.mock import patch  # noqa: PLC0415

    from custom_components.ha_linksys_velop.domain.errors import (  # noqa: PLC0415
        ConnectionTimeoutError,
    )

    coordinator = await _setup(hass, mock_entry)
    with patch.object(
        coordinator.client, "async_fetch_fast", side_effect=ConnectionTimeoutError
    ):
        await _refresh(hass, coordinator)
    assert coordinator.last_update_success is False
    assert hass.states.get("binary_sensor.living_room_internet").state == "unavailable"


async def test_parent_node_resolved_when_router_omits_parent_id(
    hass: HomeAssistant, emulator: Emulator, mock_entry: MockConfigEntry
) -> None:
    """Real routers leave parentDeviceID empty; the uplink IP names the parent."""
    emulator.devices = [
        node_device("node-1", PRIMARY_SERIAL, "Master", "Living Room"),
        node_device(
            "node-2",
            "12K10M22222222",
            "Slave",
            "Bedroom",
            ip="192.0.2.2",
            ipv6="2001:db8::2",
        ),
        node_device("node-3", "12K10M33333333", "Slave", "Office", ip="192.0.2.3"),
    ]
    await _setup(hass, mock_entry)
    assert hass.states.get("sensor.bedroom_mesh_parent_node").state == "Living Room"
    assert hass.states.get("sensor.office_mesh_parent_node").state == "Living Room"
    assert hass.states.get("sensor.bedroom_network_ipv4_address").state == "192.0.2.2"
    assert hass.states.get("sensor.bedroom_network_ipv6_address").state == "2001:db8::2"
    assert hass.states.get("sensor.office_network_ipv6_address") is None


async def test_firmware_entity_has_no_brand_picture(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """The firmware entity uses the standard icon like the other entities."""
    await _setup(hass, mock_entry)
    state = hass.states.get("update.bedroom_firmware")
    assert state is not None
    assert "entity_picture" not in state.attributes


async def test_legacy_ip_address_sensor_removed(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """The combined IP address sensor is replaced by the IPv4 and IPv6 sensors."""
    mock_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "12K10M22222222_ip_address",
        config_entry=mock_entry,
    )
    await _setup(hass, mock_entry)
    assert registry.async_get(old.entity_id) is None
    assert hass.states.get("sensor.bedroom_network_ipv4_address") is not None


async def test_values_use_sentence_case(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Router status words are shown with only the first letter capitalised."""
    await _setup(hass, mock_entry)
    assert hass.states.get("sensor.living_room_internet_status").state == "Connected"
    assert hass.states.get("sensor.bedroom_mesh_backhaul_type").state == "Wireless"
    assert hass.states.get("sensor.office_mesh_backhaul_type").state == "Wired"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("connected", "Connected"),
        ("WIRED", "Wired"),
        ("NotAvailable", "Not available"),
        ("Success", "Success"),
        ("not_available", "Not available"),
        ("", None),
        (None, None),
    ],
)
def test_sentence_case_helper(raw: str | None, expected: str | None) -> None:
    """Sentence case lowers every word and capitalises only the first letter."""
    assert _sentence(raw) == expected


async def test_entity_names_sort_in_priority_order(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """The device page sorts by name: firmware first, reboot last."""
    await _setup(hass, mock_entry)
    entities = er.async_entries_for_config_entry(
        er.async_get(hass), mock_entry.entry_id
    )
    by_device: dict[str, list[er.RegistryEntry]] = {}
    for entity in entities:
        if entity.device_id and not entity.disabled:
            by_device.setdefault(entity.device_id, []).append(entity)

    def _rank(name: str) -> int:
        tiers = ("Firmware", "Mesh", "Network")
        return next((i for i, t in enumerate(tiers) if name.startswith(t)), len(tiers))

    for members in by_device.values():
        diagnostic = sorted(
            (str(e.original_name) for e in members if e.entity_category is not None),
            key=str.casefold,
        )
        assert diagnostic[0] == "Firmware"
        ranks = [_rank(n) for n in diagnostic]
        assert ranks == sorted(ranks)
        controls = sorted(
            (str(e.original_name) for e in members if e.domain == "button"),
            key=str.casefold,
        )
        assert controls[-1] == "Reboot"


async def test_online_since_primary_uses_uptime(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    emulator: Emulator,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The hub's boot time comes from its uptime and stays steady."""
    emulator.uptime_seconds = 90061
    now = dt_util.utcnow().replace(microsecond=0)
    coordinator = await _setup(hass, mock_entry)
    boot = now - timedelta(seconds=90061)
    state = hass.states.get("sensor.living_room_online_since")
    assert dt_util.parse_datetime(state.state) == boot
    assert hass.states.get("sensor.living_room_online_for").state == ("1 day 1 hour")
    # Polling jitter does not move the time.
    freezer.tick(61)
    emulator.uptime_seconds = 90061 + 62
    await _refresh(hass, coordinator)
    assert (
        dt_util.parse_datetime(hass.states.get("sensor.living_room_online_since").state)
        == boot
    )
    assert hass.states.get("sensor.living_room_online_for").state == ("1 day 1 hour")
    # A long time passing changes the text unit by unit.
    freezer.tick(timedelta(days=7))
    emulator.uptime_seconds += 7 * 86400 + 61
    await _refresh(hass, coordinator)
    assert hass.states.get("sensor.living_room_online_for").state == ("1 week 1 day")


async def test_online_since_child_is_first_seen_and_resets_offline(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    emulator: Emulator,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A child node reports no uptime: its time is when it was seen online."""
    seen = dt_util.utcnow().replace(microsecond=0)
    coordinator = await _setup(hass, mock_entry)
    entity = "sensor.office_online_since"
    assert dt_util.parse_datetime(hass.states.get(entity).state) == seen
    freezer.tick(timedelta(hours=5, minutes=3))
    await _refresh(hass, coordinator)
    assert dt_util.parse_datetime(hass.states.get(entity).state) == seen
    assert hass.states.get("sensor.office_online_for").state == "5 hours 3 minutes"

    office = emulator.devices[2]
    saved = office["connections"]
    office["connections"] = []
    await _refresh(hass, coordinator)
    assert hass.states.get(entity).state == "unknown"
    freezer.tick(60)
    office["connections"] = saved
    await _refresh(hass, coordinator)
    assert dt_util.parse_datetime(hass.states.get(entity).state) == (
        seen + timedelta(hours=5, minutes=4)
    )


async def test_online_since_without_uptime_falls_back(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """Firmware that reports no uptime still gives the hub an online time."""
    emulator.uptime_seconds = None
    seen = dt_util.utcnow().replace(microsecond=0)
    await _setup(hass, mock_entry)
    state = hass.states.get("sensor.living_room_online_since")
    assert dt_util.parse_datetime(state.state) == seen


async def test_reboot_detected_and_boot_time_moves(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    emulator: Emulator,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A smaller uptime fires node_rebooted on the hub's node device."""
    events = _capture(hass)
    coordinator = await _setup(hass, mock_entry)
    before = hass.states.get("sensor.living_room_online_since").state
    freezer.tick(600)
    emulator.uptime_seconds = 20
    await _refresh(hass, coordinator)
    assert [e.data["type"] for e in events] == ["node_rebooted"]
    assert events[0].data["node_serial"] == PRIMARY_SERIAL
    hub = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, PRIMARY_SERIAL), mock_entry.entry_id
    )
    assert events[0].data["device_id"] == hub.id
    assert hass.states.get("sensor.living_room_online_since").state != before
    assert hass.states.get("sensor.living_room_online_for").state == "20 seconds"
    node_events = hass.states.get("event.living_room_node_events")
    assert node_events.attributes["event_type"] == "node_rebooted"
    hub_events = hass.states.get("event.living_room_network_events")
    assert hub_events.attributes["event_type"] == "node_rebooted"


async def test_node_events_entity_per_node(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """Each node gets its own events; a node going offline stays on its node."""
    events = _capture(hass)
    coordinator = await _setup(hass, mock_entry)
    for name in ("living_room", "bedroom", "office"):
        assert hass.states.get(f"event.{name}_node_events") is not None
    emulator.devices[2]["connections"] = []
    await _refresh(hass, coordinator, 2)
    assert [e.data["type"] for e in events] == ["node_offline"]
    # Earlier automations keep matching: the bus event stays on the hub device.
    hub = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, PRIMARY_SERIAL), mock_entry.entry_id
    )
    assert events[0].data["device_id"] == hub.id
    assert events[0].data["node_name"] == "Office"
    office = hass.states.get("event.office_node_events")
    assert office.attributes["event_type"] == "node_offline"
    assert office.attributes["node_serial"] == "12K10M33333333"
    assert hass.states.get("event.bedroom_node_events").attributes["event_type"] is None
    assert (
        hass.states.get("event.living_room_network_events").attributes["event_type"]
        == "node_offline"
    )


async def test_new_events_wan_ip_firmware_and_backhaul(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """WAN address, installed firmware and uplink type changes fire events."""
    events = _capture(hass)
    coordinator = await _setup(hass, mock_entry)
    emulator.devices[1]["unit"]["firmwareVersion"] = "1.1.8.0"
    emulator.backhaul[0]["connectionType"] = "Wired"
    await _refresh(hass, coordinator, 2)
    by_type = {e.data["type"]: e.data for e in events}
    assert set(by_type) == {"node_firmware_updated", "node_backhaul_changed"}
    bedroom = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, "12K10M22222222"), mock_entry.entry_id
    )
    assert by_type["node_firmware_updated"]["detail"] == "1.1.8.0"
    assert by_type["node_firmware_updated"]["device_id"] == bedroom.id
    assert by_type["node_backhaul_changed"]["detail"] == "wired"
    assert hass.states.get("event.bedroom_node_events").attributes["event_type"] in (
        "node_firmware_updated",
        "node_backhaul_changed",
    )


async def test_wan_ip_change_event(
    hass: HomeAssistant, mock_entry: MockConfigEntry, emulator: Emulator
) -> None:
    """A new WAN address fires wan_ip_changed on the hub."""
    events = _capture(hass)
    coordinator = await _setup(hass, mock_entry)
    emulator.wan_ip = "203.0.113.77"
    await _refresh(hass, coordinator, 2)
    assert [(e.data["type"], e.data["detail"]) for e in events] == [
        ("wan_ip_changed", "203.0.113.77")
    ]

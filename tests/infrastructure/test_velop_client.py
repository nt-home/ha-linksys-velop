"""Tests for the Velop client adapter."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping, Sequence
from typing import Any

import aiohttp
import pytest

from custom_components.ha_linksys_velop.domain.errors import (
    AccountLockedError,
    CannotConnectError,
    ConnectionTimeoutError,
    InvalidAuthError,
    JnapResultError,
    NotPrimaryNodeError,
    OperationInProgressError,
    UnknownActionError,
    UnsupportedDeviceError,
)
from custom_components.ha_linksys_velop.domain.models import (
    HubIdentity,
    NetworkMode,
    Node,
    NodeRole,
)
from custom_components.ha_linksys_velop.infrastructure import velop_client as vc
from custom_components.ha_linksys_velop.infrastructure.transport import (
    JNAP_BASE,
    JnapAction,
    JnapResponse,
    JnapTransport,
)
from custom_components.ha_linksys_velop.infrastructure.velop_client import VelopClient
from tests.emulator.server import PASSWORD, PRIMARY_SERIAL, Emulator

IDENTITY = HubIdentity(PRIMARY_SERIAL, "MX2000", "1.1.7", hardware="1")


@pytest.fixture
async def session() -> AsyncGenerator[aiohttp.ClientSession]:
    """A real aiohttp client session."""
    async with aiohttp.ClientSession() as sess:
        yield sess


def client_for(
    session: aiohttp.ClientSession, emu: Emulator, password: str | None = PASSWORD
) -> VelopClient:
    """Build a client wired to the emulator."""
    return VelopClient(
        JnapTransport(session, "127.0.0.1", emu.port, use_ssl=False, password=password)
    )


class FakeTransport:
    """Scripted transport for edge cases the emulator cannot produce."""

    def __init__(
        self,
        calls: Mapping[str, Any] | None = None,
        transaction: Sequence[JnapResponse] | None = None,
    ) -> None:
        self.calls = dict(calls or {})
        self.transaction = list(transaction or [])
        self.log: list[tuple[str, Any, Any]] = []

    async def async_call(
        self,
        action: str,
        request: Mapping[str, Any] | None = None,
        *,
        authenticated: bool = True,
        host: str | None = None,
    ) -> Mapping[str, Any]:
        self.log.append((action.removeprefix(JNAP_BASE), request, host))
        value = self.calls[action.removeprefix(JNAP_BASE)]
        if isinstance(value, BaseException):
            raise value
        return value

    async def async_transaction(
        self, actions: Sequence[JnapAction], *, authenticated: bool = True
    ) -> list[JnapResponse]:
        return self.transaction


def fake(
    calls: Mapping[str, Any] | None = None,
    transaction: Sequence[JnapResponse] | None = None,
) -> tuple[VelopClient, FakeTransport]:
    """Build a client over a fake transport."""
    transport = FakeTransport(calls, transaction)
    return VelopClient(transport), transport  # type: ignore[arg-type]


def ok(output: Mapping[str, Any] | None = None) -> JnapResponse:
    """An OK response."""
    return JnapResponse("OK", output or {})


def err(result: str) -> JnapResponse:
    """A failed response."""
    return JnapResponse(result, {})


# identity -------------------------------------------------------------------


async def test_identity(session: aiohttp.ClientSession, emulator: Emulator) -> None:
    """Identity is read without authentication."""
    identity = await client_for(session, emulator, None).async_get_identity()
    assert identity.serial == PRIMARY_SERIAL
    assert identity.model == "MX2000"
    assert emulator.calls == [
        ("core/GetDeviceInfo", False),
        ("nodes/smartmode/GetDeviceMode", False),
    ]


async def test_identity_not_primary(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """A child-mode node raises NotPrimaryNodeError."""
    emulator.mode = "slave"
    with pytest.raises(NotPrimaryNodeError):
        await client_for(session, emulator).async_get_identity()


async def test_identity_mode_action_unknown_tolerated(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """A missing GetDeviceMode action is tolerated."""
    emulator.unknown_actions.add(f"{JNAP_BASE}nodes/smartmode/GetDeviceMode")
    assert (await client_for(session, emulator).async_get_identity()).serial


async def test_identity_missing_serial() -> None:
    """A payload without a serial is not a Velop."""
    client, _ = fake({"core/GetDeviceInfo": {"modelNumber": "x"}})
    with pytest.raises(UnsupportedDeviceError):
        await client.async_get_identity()


@pytest.mark.parametrize(
    "mode_error",
    [
        UnknownActionError("_ErrorUnknownAction"),
        InvalidAuthError(),
        JnapResultError("Other"),
        CannotConnectError(),
        ConnectionTimeoutError(),
        UnsupportedDeviceError(),
        NotPrimaryNodeError(),
        AccountLockedError(),
    ],
)
async def test_identity_mode_errors_tolerated(mode_error: Exception) -> None:
    """Any domain error from GetDeviceMode is swallowed."""
    client, _ = fake(
        {
            "core/GetDeviceInfo": {"serialNumber": "S1"},
            "nodes/smartmode/GetDeviceMode": mode_error,
        }
    )
    assert (await client.async_get_identity()).serial == "S1"


async def test_identity_info_errors_propagate() -> None:
    """Errors from GetDeviceInfo itself propagate."""
    client, _ = fake({"core/GetDeviceInfo": CannotConnectError()})
    with pytest.raises(CannotConnectError):
        await client.async_get_identity()


# validate credentials -------------------------------------------------------


async def test_validate_ok(session: aiohttp.ClientSession, emulator: Emulator) -> None:
    """Valid credentials pass."""
    await client_for(session, emulator).async_validate_credentials()
    assert emulator.calls == [("devicelist/GetDevices", True)]


async def test_validate_wrong_password(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """Wrong credentials raise InvalidAuthError, or lock error when locked."""
    client = client_for(session, emulator, "nope-password")
    with pytest.raises(InvalidAuthError):
        await client.async_validate_credentials()
    emulator.locked = True
    with pytest.raises(AccountLockedError):
        await client.async_validate_credentials()


async def test_validate_tolerates_generic_error() -> None:
    """A generic non-auth result inside the transaction does not fail setup."""
    client, _ = fake(transaction=[err("Error")])
    await client.async_validate_credentials()


async def test_validate_auth_result_is_fatal() -> None:
    """Auth and lock results inside the transaction still fail validation."""
    client, _ = fake(transaction=[err("_ErrorUnauthorized")])
    with pytest.raises(InvalidAuthError):
        await client.async_validate_credentials()
    client, _ = fake(transaction=[err("ErrorAdminAccountLocked")])
    with pytest.raises(AccountLockedError):
        await client.async_validate_credentials()


# fast fetch -----------------------------------------------------------------


async def test_fast_router(session: aiohttp.ClientSession, emulator: Emulator) -> None:
    """Router mode returns WAN, nodes, clients and backhaul."""
    snap = await client_for(session, emulator).async_fetch_fast(IDENTITY)
    assert snap.wan.mode is NetworkMode.ROUTER
    assert snap.wan.connected is True
    assert snap.wan.ip_address == "203.0.113.9"
    assert len(snap.nodes) == 3
    assert len(snap.clients) == 3
    assert snap.nodes[1].backhaul is not None
    assert snap.nodes[1].backhaul.signal_dbm == -57
    assert snap.nodes[2].backhaul is not None
    assert snap.nodes[2].backhaul.connection_type == "wired"
    assert [n.client_count for n in snap.nodes] == [1, 2, 0]
    assert [c[0] for c in emulator.calls] == [
        "devicelist/GetDevices",
        "nodes/diagnostics/GetBackhaulInfo",
        "router/GetWANStatus",
        "nodes/setup/GetSelectedChannels",
        "healthcheck/GetHealthCheckStatus",
        "healthcheck/GetHealthCheckResults",
    ]
    assert snap.channel_scan_running is False
    assert snap.speed_test is not None
    assert snap.speed_test.running is False
    assert snap.speed_test.latest is None


async def test_fast_bridge(session: aiohttp.ClientSession, emulator: Emulator) -> None:
    """Bridge mode omits clients but keeps nodes."""
    emulator.wan_type = "Bridge"
    snap = await client_for(session, emulator).async_fetch_fast(IDENTITY)
    assert snap.wan.mode is NetworkMode.BRIDGE
    assert snap.clients == ()
    assert len(snap.nodes) == 3
    # The emulator reports no Wi-Fi clients, so every node counts zero.
    assert [n.client_count for n in snap.nodes] == [0, 0, 0]


async def test_fast_bridge_counts_wireless_clients_per_node(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """Bridge mode counts each node's Wi-Fi clients from its own action."""
    emulator.wan_type = "Bridge"
    emulator.wireless = [
        {"deviceID": "node-2", "connections": [{"macAddress": "x"}, {}]},
        {"deviceID": "node-3", "connections": "junk"},
        {"connections": [{}]},
    ]
    snap = await client_for(session, emulator).async_fetch_fast(IDENTITY)
    by_id = {n.device_id: n.client_count for n in snap.nodes}
    assert by_id == {"node-1": 0, "node-2": 2, "node-3": 0}


async def test_fast_bridge_counts_unknown_when_action_missing(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """If the router lacks the action the per-node counts stay unknown."""
    emulator.wan_type = "Bridge"
    emulator.unknown_actions.add(
        f"{JNAP_BASE}nodes/networkconnections/GetNodesWirelessNetworkConnections"
    )
    snap = await client_for(session, emulator).async_fetch_fast(IDENTITY)
    assert [n.client_count for n in snap.nodes] == [None, None, None]


async def test_router_mode_does_not_call_wireless_action(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """Router mode keeps its device-list counts and skips the extra request."""
    await client_for(session, emulator).async_fetch_fast(IDENTITY)
    assert not any("GetNodesWirelessNetworkConnections" in c[0] for c in emulator.calls)


async def test_fast_tolerates_unknown_actions(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """Unknown WAN and backhaul actions are tolerated."""
    emulator.unknown_actions |= {
        f"{JNAP_BASE}router/GetWANStatus",
        f"{JNAP_BASE}nodes/diagnostics/GetBackhaulInfo",
    }
    snap = await client_for(session, emulator).async_fetch_fast(IDENTITY)
    assert snap.wan.mode is NetworkMode.UNKNOWN
    assert snap.wan.connected is None and snap.wan.wan_type is None
    assert snap.clients == ()
    assert len(snap.nodes) == 3
    assert all(n.backhaul is None for n in snap.nodes)


async def test_fast_devices_unknown_falls_back_to_primary(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """Without a device list the primary is synthesised from identity."""
    emulator.unknown_actions.add(f"{JNAP_BASE}devicelist/GetDevices")
    snap = await client_for(session, emulator).async_fetch_fast(IDENTITY)
    assert len(snap.nodes) == 1
    primary = snap.nodes[0]
    assert primary.role is NodeRole.PRIMARY
    assert primary.serial == PRIMARY_SERIAL
    assert primary.device_id == PRIMARY_SERIAL
    assert primary.name == "MX2000 5678"
    assert (primary.online, primary.firmware, primary.hardware) == (True, "1.1.7", "1")


async def test_fast_missing_primary_prepended(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """If only children are listed, the identity primary is prepended."""
    emulator.devices = [d for d in emulator.devices if d["deviceID"] != "node-1"]
    snap = await client_for(session, emulator).async_fetch_fast(IDENTITY)
    assert snap.nodes[0].role is NodeRole.PRIMARY
    assert snap.nodes[0].serial == PRIMARY_SERIAL
    assert len(snap.nodes) == 3


@pytest.mark.parametrize(
    ("password", "locked", "exc"),
    [
        ("wrong-password", False, InvalidAuthError),
        ("wrong-password", True, AccountLockedError),
        (None, False, InvalidAuthError),
    ],
)
async def test_fast_auth_failures(
    session: aiohttp.ClientSession,
    emulator: Emulator,
    password: str | None,
    locked: bool,
    exc: type[Exception],
) -> None:
    """Auth failures inside the transaction surface as domain errors."""
    emulator.locked = locked
    with pytest.raises(exc):
        await client_for(session, emulator, password).async_fetch_fast(IDENTITY)


async def test_slow_auth_failure(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """Slow fetch also raises on auth failure."""
    with pytest.raises(InvalidAuthError):
        await client_for(session, emulator, "bad-password").async_fetch_slow(IDENTITY)
    emulator.locked = True
    with pytest.raises(AccountLockedError):
        await client_for(session, emulator, "bad-password").async_fetch_slow(IDENTITY)


async def test_fast_tolerates_misc_failures() -> None:
    """Generic and not-primary failures are tolerated per action."""
    client, _ = fake(
        transaction=[
            err("ErrorDeviceNotInMasterMode"),
            err("_ErrorBoom"),
            err("X"),
            err("_ErrorUnknownAction"),
            err("Y"),
            err("Z"),
        ]
    )
    snap = await client.async_fetch_fast(IDENTITY)
    assert snap.wan.mode is NetworkMode.UNKNOWN
    assert [n.role for n in snap.nodes] == [NodeRole.PRIMARY]
    assert snap.clients == ()
    assert snap.channel_scan_running is None
    assert snap.speed_test is None


async def test_fast_auth_in_any_position_raises() -> None:
    """An auth failure in any slot is fatal."""
    for slot in range(6):
        responses = [ok(), ok(), ok(), ok(), ok(), ok()]
        responses[slot] = err("_ErrorUnauthorized")
        client, _ = fake(transaction=responses)
        with pytest.raises(InvalidAuthError):
            await client.async_fetch_fast(IDENTITY)


# slow fetch -----------------------------------------------------------------


async def test_slow(session: aiohttp.ClientSession, emulator: Emulator) -> None:
    """Firmware and stats are mapped."""
    emulator.firmware = [
        {
            "deviceUUID": "node-2",
            "availableUpdate": {"firmwareVersion": "1.2.0"},
        }
    ]
    snap = await client_for(session, emulator).async_fetch_slow(IDENTITY)
    assert snap.firmware["node-2"].available_version == "1.2.0"
    assert snap.stats.cpu_load == 12.5
    assert snap.stats.memory_load == 40.0


async def test_slow_tolerates_unknown_actions(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """Unknown firmware/stats actions yield empty data."""
    emulator.unknown_actions |= {
        f"{JNAP_BASE}nodes/firmwareupdate/GetFirmwareUpdateStatus",
        f"{JNAP_BASE}diagnostics/GetSystemStats",
    }
    snap = await client_for(session, emulator).async_fetch_slow(IDENTITY)
    assert snap.firmware == {}
    assert snap.stats.cpu_load is None and snap.stats.memory_load is None


async def test_slow_tolerates_generic_failure() -> None:
    """A generic failure is tolerated."""
    client, _ = fake(transaction=[err("_ErrorBoom"), ok({"CPULoad": 1})])
    snap = await client.async_fetch_slow(IDENTITY)
    assert snap.firmware == {} and snap.stats.cpu_load == 1.0


# reboot ---------------------------------------------------------------------


def _node(role: NodeRole, ip: str | None = "127.0.0.1") -> Node:
    return Node(
        device_id="node-2",
        serial="SER2",
        name="n",
        model="m",
        role=role,
        online=True,
        ip_address=ip,
    )


async def test_reboot_primary(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """The primary uses core/Reboot."""
    await client_for(session, emulator).async_reboot(_node(NodeRole.PRIMARY))
    assert emulator.reboots == [{"action": "core/Reboot"}]


async def test_reboot_child(session: aiohttp.ClientSession, emulator: Emulator) -> None:
    """A child uses core/Reboot2."""
    await client_for(session, emulator).async_reboot(_node(NodeRole.CHILD))
    assert emulator.reboots == [{"action": "core/Reboot2"}]


async def test_reboot_child_uuid_sent() -> None:
    """Reboot2 carries the device UUID."""
    client, transport = fake({"core/Reboot2": {}})
    await client.async_reboot(_node(NodeRole.CHILD))
    assert transport.log == [("core/Reboot2", {"deviceUUID": "node-2"}, None)]


async def test_reboot_child_falls_back_to_node_ip(
    session: aiohttp.ClientSession,
    emulator: Emulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown Reboot2 falls back to Reboot on the node's own address."""
    # The emulator listens on loopback, which production code refuses.
    monkeypatch.setattr(
        "custom_components.ha_linksys_velop.infrastructure.velop_client."
        "validate_node_address",
        lambda raw: raw,
    )
    emulator.unknown_actions.add(f"{JNAP_BASE}core/Reboot2")
    await client_for(session, emulator).async_reboot(_node(NodeRole.CHILD))
    assert emulator.reboots == [{"action": "core/Reboot"}]


async def test_reboot_fallback_host_passed() -> None:
    """The fallback call targets the node IP."""
    client, transport = fake(
        {"core/Reboot2": UnknownActionError("x"), "core/Reboot": {}}
    )
    await client.async_reboot(_node(NodeRole.CHILD, "192.0.2.2"))
    assert transport.log[-1] == ("core/Reboot", None, "192.0.2.2")


async def test_reboot_child_unknown_without_ip_raises(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """With no node IP the unknown-action error propagates."""
    emulator.unknown_actions.add(f"{JNAP_BASE}core/Reboot2")
    with pytest.raises(UnknownActionError):
        await client_for(session, emulator).async_reboot(_node(NodeRole.CHILD, None))
    assert emulator.reboots == []


async def test_reboot_dropped_connection_is_success(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """The hub dropping the connection means the reboot started."""
    emulator.drop_on_reboot = True
    await client_for(session, emulator).async_reboot(_node(NodeRole.PRIMARY))
    assert emulator.reboots == [{"action": "dropped"}]


async def test_reboot_timeout_is_success() -> None:
    """Timeouts (a CannotConnectError) during reboot count as success."""
    client, _ = fake({"core/Reboot": ConnectionTimeoutError()})
    await client.async_reboot(_node(NodeRole.PRIMARY))


async def test_reboot_auth_error_propagates(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """Auth failure on reboot is not swallowed."""
    with pytest.raises(InvalidAuthError):
        await client_for(session, emulator, "bad-password").async_reboot(
            _node(NodeRole.PRIMARY)
        )


def test_module_constants() -> None:
    """Action names are built on the JNAP base."""
    assert f"{JNAP_BASE}core/Reboot2" == vc.ACTION_REBOOT_NODE


# channel scan and speed check ------------------------------------------------


async def test_start_channel_scan(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """Channel Finder starts with the auto channel selection action."""
    client = client_for(session, emulator)
    await client.async_start_channel_scan()
    assert emulator.scan_starts == 1
    snap = await client.async_fetch_fast(IDENTITY)
    assert snap.channel_scan_running is True


async def test_start_channel_scan_already_running(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """The hub's already-in-progress result maps to its own error."""
    emulator.scan_start_result = "ErrorAutoChannelSelectionAlreadyInProgress"
    with pytest.raises(OperationInProgressError):
        await client_for(session, emulator).async_start_channel_scan()


async def test_start_speed_test_and_read_results(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """A speed check runs, then its numbers arrive in Mbps."""
    client = client_for(session, emulator)
    await client.async_start_speed_test()
    assert emulator.speed_starts == 1
    running = (await client.async_fetch_fast(IDENTITY)).speed_test
    assert running is not None
    assert running.running is True
    assert running.latest is None
    emulator.finish_speed()
    done = (await client.async_fetch_fast(IDENTITY)).speed_test
    assert done is not None
    assert done.running is False
    assert done.latest is not None
    assert done.latest.success
    assert done.latest.download_mbps == 92.0
    assert done.latest.upload_mbps == 95.0
    assert done.latest.latency_ms == 3
    assert done.latest.timestamp is not None


async def test_fast_speed_test_unsupported(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """Firmware without the actions leaves the features unknown."""
    emulator.unknown_actions |= {
        f"{JNAP_BASE}nodes/setup/GetSelectedChannels",
        f"{JNAP_BASE}healthcheck/GetHealthCheckStatus",
        f"{JNAP_BASE}healthcheck/GetHealthCheckResults",
    }
    snap = await client_for(session, emulator).async_fetch_fast(IDENTITY)
    assert snap.channel_scan_running is None
    assert snap.speed_test is None
    assert len(snap.nodes) == 3

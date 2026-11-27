"""Adapter implementing the hub port on top of the JNAP transport."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..domain.errors import (
    AccountLockedError,
    CannotConnectError,
    InvalidAuthError,
    NotPrimaryNodeError,
    UnknownActionError,
    UnsupportedDeviceError,
    VelopError,
)
from ..domain.models import (
    FastSnapshot,
    FirmwareStatus,
    HubIdentity,
    NetworkMode,
    Node,
    NodeRole,
    SlowSnapshot,
    SpeedTestState,
    SystemStats,
    WanStatus,
)
from ..domain.validation import validate_node_address
from . import mapper
from .transport import (
    JNAP_BASE,
    JnapAction,
    JnapResponse,
    JnapTransport,
    raise_for_result,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_LOGGER = logging.getLogger(__name__)

ACTION_DEVICE_INFO = f"{JNAP_BASE}core/GetDeviceInfo"
ACTION_DEVICE_MODE = f"{JNAP_BASE}nodes/smartmode/GetDeviceMode"
ACTION_GET_DEVICES = f"{JNAP_BASE}devicelist/GetDevices"
ACTION_WAN_STATUS = f"{JNAP_BASE}router/GetWANStatus"
ACTION_BACKHAUL = f"{JNAP_BASE}nodes/diagnostics/GetBackhaulInfo"
ACTION_WIRELESS_CONNECTIONS = (
    f"{JNAP_BASE}nodes/networkconnections/GetNodesWirelessNetworkConnections"
)
ACTION_FIRMWARE = f"{JNAP_BASE}nodes/firmwareupdate/GetFirmwareUpdateStatus"
ACTION_SYSTEM_STATS = f"{JNAP_BASE}diagnostics/GetSystemStats"
ACTION_CHANNEL_SCAN_STATUS = f"{JNAP_BASE}nodes/setup/GetSelectedChannels"
ACTION_CHANNEL_SCAN_START = f"{JNAP_BASE}nodes/setup/StartAutoChannelSelection"
ACTION_SPEED_STATUS = f"{JNAP_BASE}healthcheck/GetHealthCheckStatus"
ACTION_SPEED_RESULTS = f"{JNAP_BASE}healthcheck/GetHealthCheckResults"
ACTION_SPEED_START = f"{JNAP_BASE}healthcheck/RunHealthCheck"
SPEED_TEST_MODULE = "SpeedTest"
SPEED_RESULTS_REQUEST = {
    "healthCheckModule": SPEED_TEST_MODULE,
    "includeModuleResults": True,
    "lastNumberOfResults": 5,
}
ACTION_REBOOT = f"{JNAP_BASE}core/Reboot"
ACTION_REBOOT_NODE = f"{JNAP_BASE}core/Reboot2"


class VelopClient:
    """Talk to one Velop primary node and map answers to domain models."""

    def __init__(self, transport: JnapTransport) -> None:
        """Create the adapter around a transport."""
        self._transport = transport

    async def async_get_identity(self) -> HubIdentity:
        """Return the hub identity; raises when the host is not a primary node."""
        output = await self._transport.async_call(
            ACTION_DEVICE_INFO, authenticated=False
        )
        identity = mapper.map_identity(output)
        if identity is None:
            raise UnsupportedDeviceError
        try:
            mode = await self._transport.async_call(
                ACTION_DEVICE_MODE, authenticated=False
            )
        except UnknownActionError, InvalidAuthError:
            return identity
        except VelopError:
            _LOGGER.debug("GetDeviceMode unavailable")
            return identity
        if not mapper.is_primary_mode(mode):
            raise NotPrimaryNodeError
        return identity

    async def async_validate_credentials(self) -> None:
        """Check the admin credentials with an authenticated action.

        Sent as a transaction, like every refresh: some firmware answers a
        standalone call with a generic error. Only authentication and lock
        results are fatal here; the node fetch reports any other problem.
        """
        responses = await self._transport.async_transaction(
            [JnapAction(ACTION_GET_DEVICES, {"sinceRevision": 0})]
        )
        _raise_if_fatal(responses[0])

    async def async_fetch_fast(self, identity: HubIdentity) -> FastSnapshot:
        """Fetch WAN status, nodes, clients and activity in one transaction."""
        responses = await self._transport.async_transaction(
            [
                # The hub authenticates a transaction by its first action, and
                # GetWANStatus is public: keep an authenticated action first.
                JnapAction(ACTION_GET_DEVICES, {"sinceRevision": 0}),
                JnapAction(ACTION_BACKHAUL),
                JnapAction(ACTION_WAN_STATUS),
                JnapAction(ACTION_CHANNEL_SCAN_STATUS),
                JnapAction(ACTION_SPEED_STATUS),
                JnapAction(ACTION_SPEED_RESULTS, SPEED_RESULTS_REQUEST),
                JnapAction(ACTION_SYSTEM_STATS),
            ]
        )
        (
            devices_resp,
            backhaul_resp,
            wan_resp,
            scan_resp,
            speed_resp,
            results_resp,
            uptime_resp,
        ) = responses
        for response in responses:
            _raise_if_fatal(response)
        wan = _wan_or_unknown(wan_resp)
        backhaul = mapper.map_backhaul(backhaul_resp.output) if backhaul_resp.ok else {}
        if devices_resp.ok:
            nodes, clients = mapper.map_devices(
                devices_resp.output,
                backhaul,
                include_clients=wan.mode is NetworkMode.ROUTER,
                primary_serial=identity.serial,
            )
        else:
            nodes, clients = (), ()
        if not any(n.role is NodeRole.PRIMARY for n in nodes):
            nodes = (_fallback_primary(identity), *nodes)
        if wan.mode is NetworkMode.BRIDGE:
            nodes = await self._bridge_client_counts(nodes)
        return FastSnapshot(
            wan=wan,
            nodes=nodes,
            clients=clients,
            channel_scan_running=(
                mapper.map_channel_scan(scan_resp.output) if scan_resp.ok else None
            ),
            speed_test=_speed_state(speed_resp, results_resp),
            uptime_seconds=(
                mapper.map_uptime(uptime_resp.output) if uptime_resp.ok else None
            ),
        )

    async def _bridge_client_counts(self, nodes: tuple[Node, ...]) -> tuple[Node, ...]:
        """Count Wi-Fi clients per node; the bridge device list has no parents."""
        try:
            output = await self._transport.async_call(ACTION_WIRELESS_CONNECTIONS)
        except VelopError:
            _LOGGER.debug("Per-node client counts are unavailable in bridge mode")
            return mapper.with_client_counts(nodes, None)
        return mapper.with_client_counts(nodes, mapper.map_wireless_counts(output))

    async def async_fetch_slow(self, identity: HubIdentity) -> SlowSnapshot:
        """Fetch firmware status and system statistics."""
        responses = await self._transport.async_transaction(
            [JnapAction(ACTION_FIRMWARE), JnapAction(ACTION_SYSTEM_STATS)]
        )
        for response in responses:
            _raise_if_fatal(response)
        firmware_resp, stats_resp = responses
        firmware: Mapping[str, FirmwareStatus] = (
            mapper.map_firmware(firmware_resp.output) if firmware_resp.ok else {}
        )
        stats = mapper.map_stats(stats_resp.output) if stats_resp.ok else SystemStats()
        return SlowSnapshot(firmware=firmware, stats=stats)

    async def async_reboot(self, node: Node) -> None:
        """Reboot a node; a dropped connection means the reboot started."""
        try:
            await self._reboot(node)
        except CannotConnectError:
            _LOGGER.debug("Connection dropped while rebooting; treating as success")

    async def async_start_channel_scan(self) -> None:
        """Start Channel Finder (auto channel selection) on the mesh."""
        await self._transport.async_call(ACTION_CHANNEL_SCAN_START)

    async def async_start_speed_test(self) -> None:
        """Start a speed check from the hub."""
        await self._transport.async_call(
            ACTION_SPEED_START, {"runHealthCheckModule": SPEED_TEST_MODULE}
        )

    async def _reboot(self, node: Node) -> None:
        if node.role is NodeRole.PRIMARY:
            await self._transport.async_call(ACTION_REBOOT)
            return
        try:
            await self._transport.async_call(
                ACTION_REBOOT_NODE, {"deviceUUID": node.device_id}
            )
        except UnknownActionError:
            # The address is reported by the hub: send the password only to a
            # private IP literal, never to a name or public address.
            target = validate_node_address(node.ip_address)
            if target is None:
                raise
            await self._transport.async_call(ACTION_REBOOT, host=target)


def _raise_if_fatal(response: JnapResponse) -> None:
    """Raise for auth/lock results; tolerate unsupported or failing actions."""
    if response.ok:
        return
    try:
        raise_for_result(response.result)
    except UnknownActionError, NotPrimaryNodeError:
        return
    except InvalidAuthError, AccountLockedError:
        raise
    except VelopError:
        _LOGGER.debug("Action failed with result %s", response.result)


def _speed_state(status: JnapResponse, results: JnapResponse) -> SpeedTestState | None:
    return mapper.map_speed_test(
        status.output if status.ok else None, results.output if results.ok else None
    )


def _wan_or_unknown(response: JnapResponse) -> WanStatus:
    if response.ok:
        return mapper.map_wan(response.output)
    return WanStatus(
        connected=None,
        status=None,
        wan_type=None,
        ip_address=None,
        mode=NetworkMode.UNKNOWN,
    )


def _fallback_primary(identity: HubIdentity) -> Node:
    return Node(
        device_id=identity.serial,
        serial=identity.serial,
        name=f"{identity.model} {identity.serial[-4:]}",
        model=identity.model,
        role=NodeRole.PRIMARY,
        online=True,
        firmware=identity.firmware,
        hardware=identity.hardware,
    )

"""Immutable domain models published as the refresh snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


class NetworkMode(StrEnum):
    """How the hub is operating."""

    ROUTER = "router"
    BRIDGE = "bridge"
    UNKNOWN = "unknown"


class NodeRole(StrEnum):
    """Role of a mesh node."""

    PRIMARY = "primary"
    CHILD = "child"


@dataclass(frozen=True, slots=True)
class HubIdentity:
    """Unauthenticated identity of the primary node."""

    serial: str
    model: str
    firmware: str
    hardware: str | None = None
    manufacturer: str = "Linksys"
    services: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class WanStatus:
    """WAN connectivity facts."""

    connected: bool | None
    status: str | None
    wan_type: str | None
    ip_address: str | None
    mode: NetworkMode


@dataclass(frozen=True, slots=True)
class Backhaul:
    """Uplink description for one child node."""

    connection_type: str | None
    speed_mbps: float | None
    signal_dbm: int | None
    parent_ip: str | None


@dataclass(frozen=True, slots=True)
class Node:
    """One mesh node (primary or child)."""

    device_id: str
    serial: str
    name: str
    model: str
    role: NodeRole
    online: bool
    firmware: str | None = None
    hardware: str | None = None
    mac: str | None = None
    ip_address: str | None = None
    parent_device_id: str | None = None
    location: str | None = None
    client_count: int | None = None
    backhaul: Backhaul | None = None


@dataclass(frozen=True, slots=True)
class Client:
    """One network client as seen in the device list."""

    mac: str
    name: str
    connected: bool
    connection_type: str | None = None
    node_device_id: str | None = None
    ip_address: str | None = None


@dataclass(frozen=True, slots=True)
class FirmwareStatus:
    """Firmware update state for one node."""

    available_version: str | None
    update_pending: bool = False


@dataclass(frozen=True, slots=True)
class SystemStats:
    """Optional system statistics for the hub."""

    cpu_load: float | None = None
    memory_load: float | None = None


SPEED_TEST_SUCCESS = "Success"


@dataclass(frozen=True, slots=True)
class SpeedTestResult:
    """One finished speed check as reported by the hub."""

    result_id: int | None
    timestamp: datetime | None
    exit_code: str
    download_mbps: float | None = None
    upload_mbps: float | None = None
    latency_ms: float | None = None
    server_id: str | None = None

    @property
    def success(self) -> bool:
        """Return True when the hub finished the test with usable numbers."""
        return self.exit_code == SPEED_TEST_SUCCESS


@dataclass(frozen=True, slots=True)
class SpeedTestState:
    """Speed check activity: whether one is running and the last finished one."""

    running: bool
    latest: SpeedTestResult | None = None


@dataclass(frozen=True, slots=True)
class FastSnapshot:
    """Data refreshed on the fast interval.

    ``channel_scan_running`` and ``speed_test`` are None when the firmware does
    not offer the feature.
    """

    wan: WanStatus
    nodes: tuple[Node, ...]
    clients: tuple[Client, ...] = ()
    channel_scan_running: bool | None = None
    speed_test: SpeedTestState | None = None


@dataclass(frozen=True, slots=True)
class SlowSnapshot:
    """Data refreshed on the slow interval."""

    firmware: Mapping[str, FirmwareStatus] = field(
        default_factory=lambda: MappingProxyType({})
    )
    stats: SystemStats = SystemStats()


@dataclass(frozen=True, slots=True)
class HubSnapshot:
    """Immutable data published by the coordinator on every refresh."""

    identity: HubIdentity
    wan: WanStatus
    nodes: tuple[Node, ...]
    clients: tuple[Client, ...]
    firmware: Mapping[str, FirmwareStatus]
    stats: SystemStats
    channel_scan_running: bool | None = None
    speed_test: SpeedTestState | None = None

    @property
    def mode(self) -> NetworkMode:
        """Return the hub operating mode."""
        return self.wan.mode

    @property
    def primary(self) -> Node | None:
        """Return the primary node when present."""
        return next((n for n in self.nodes if n.role is NodeRole.PRIMARY), None)

    @property
    def node_by_serial(self) -> dict[str, Node]:
        """Return nodes keyed by serial number."""
        return {n.serial: n for n in self.nodes}

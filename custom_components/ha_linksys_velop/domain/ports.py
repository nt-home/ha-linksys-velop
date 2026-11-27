"""Port contracts implemented by the infrastructure layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .models import FastSnapshot, HubIdentity, Node, SlowSnapshot


class HubPort(Protocol):
    """Contract for talking to one Velop hub."""

    async def async_get_identity(self) -> HubIdentity:
        """Return the unauthenticated identity of the primary node."""

    async def async_validate_credentials(self) -> None:
        """Raise when the stored admin credentials are not accepted."""

    async def async_fetch_fast(self, identity: HubIdentity) -> FastSnapshot:
        """Return the fast-refresh data."""

    async def async_fetch_slow(self, identity: HubIdentity) -> SlowSnapshot:
        """Return the slow-refresh data."""

    async def async_reboot(self, node: Node) -> None:
        """Reboot the given node (the primary node reboots the whole hub)."""

    async def async_start_channel_scan(self) -> None:
        """Start the hub's Channel Finder scan (auto channel selection)."""

    async def async_start_speed_test(self) -> None:
        """Start a speed check from the hub to the internet."""

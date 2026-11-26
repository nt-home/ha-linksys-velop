"""Composition helpers that build the hub adapter from configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..infrastructure.transport import JnapTransport
from ..infrastructure.velop_client import VelopClient

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..domain.ports import HubPort


def create_client(
    hass: HomeAssistant,
    host: str,
    port: int,
    *,
    use_ssl: bool,
    password: str,
) -> HubPort:
    """Return a hub adapter; the Velop certificate is deliberately not verified."""
    session = async_get_clientsession(hass, verify_ssl=False)
    transport = JnapTransport(session, host, port, use_ssl=use_ssl, password=password)
    return VelopClient(transport)

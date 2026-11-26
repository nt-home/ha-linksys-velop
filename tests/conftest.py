"""Shared fixtures."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from pytest_homeassistant.common import MockConfigEntry

from custom_components.ha_linksys_velop.const import CONF_USE_SSL, DOMAIN
from tests.emulator.server import PASSWORD, PRIMARY_SERIAL, Emulator


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow loading the integration from custom_components."""


@pytest.fixture(autouse=True)
def allow_loopback(socket_enabled: None) -> Generator[None]:
    """Let tests talk to the localhost emulator and bypass the host guard."""
    with patch(
        "custom_components.ha_linksys_velop.config_flow.validate_host",
        side_effect=lambda h: h.strip(),
    ):
        yield


@pytest.fixture
async def emulator() -> AsyncGenerator[Emulator]:
    """Run a router-mode three-node emulator."""
    emu = Emulator()
    await emu.start()
    yield emu
    await emu.stop()


@pytest.fixture
def entry_data(emulator: Emulator) -> dict[str, object]:
    """Config entry data pointing at the emulator."""
    return {
        CONF_HOST: "127.0.0.1",
        CONF_PORT: emulator.port,
        CONF_USE_SSL: False,
        CONF_PASSWORD: PASSWORD,
    }


@pytest.fixture
def mock_entry(entry_data: dict[str, object]) -> MockConfigEntry:
    """A config entry for the emulator hub."""
    return MockConfigEntry(
        domain=DOMAIN,
        data=entry_data,
        unique_id=PRIMARY_SERIAL,
        title="Velop MX2000 5678",
    )

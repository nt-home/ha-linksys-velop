"""Regression tests for the 1.0.0 security hardening."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import Context, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant.common import MockConfigEntry

from custom_components.ha_linksys_velop.application.credentials import STORAGE_KEY
from custom_components.ha_linksys_velop.const import CONF_USE_SSL, DOMAIN
from custom_components.ha_linksys_velop.domain.errors import InvalidHostError
from custom_components.ha_linksys_velop.domain.validation import (
    validate_host,
    validate_node_address,
)
from custom_components.ha_linksys_velop.infrastructure.transport import (
    JnapTransport,
)
from tests.emulator.server import PASSWORD, Emulator

pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.mark.parametrize(
    "host",
    [
        "127.1",
        "2130706433",
        "0x7f.1",
        "10.1",
        "foo.localhost",
        "localhost.",
        "fe80::1%eth0",
        "fd00::1%eth0",
        "255.255.255.255",
        "::ffff:127.0.0.1",
    ],
)
def test_validate_host_rejects_loopback_aliases(host: str) -> None:
    with pytest.raises(InvalidHostError):
        validate_host(host)


@pytest.mark.parametrize("host", ["192.168.1.1", "router.lan", "my-router.example.com"])
def test_validate_host_accepts_lan_targets(host: str) -> None:
    assert validate_host(host) == host


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("192.168.1.2", "192.168.1.2"),
        (None, None),
        ("", None),
        ("evil.example.com", None),
        ("8.8.8.8", None),
        ("127.0.0.1", None),
        ("169.254.169.254", None),
        ("fe80::1%eth0", None),
    ],
)
def test_validate_node_address(raw: str | None, expected: str | None) -> None:
    assert validate_node_address(raw) == expected


async def test_deeply_nested_json_is_unsupported_device(
    hass: HomeAssistant, emulator: Emulator
) -> None:
    from aiohttp import ClientSession  # noqa: PLC0415

    from custom_components.ha_linksys_velop.domain.errors import (  # noqa: PLC0415
        UnsupportedDeviceError,
    )

    emulator.raw_override = b"[" * 200_000
    async with ClientSession() as session:
        transport = JnapTransport(
            session, "127.0.0.1", emulator.port, use_ssl=False, password=None
        )
        with pytest.raises(UnsupportedDeviceError):
            await transport.async_call("core/GetDeviceInfo", authenticated=False)


async def _loaded(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_reboot_service_is_admin_only(
    hass: HomeAssistant, emulator: Emulator, mock_entry: MockConfigEntry
) -> None:
    await _loaded(hass, mock_entry)
    device = next(
        iter(dr.async_entries_for_config_entry(dr.async_get(hass), mock_entry.entry_id))
    )
    # The first user Home Assistant creates is its owner, who is always admin.
    await hass.auth.async_create_user("owner", group_ids=[GROUP_ID_ADMIN])
    user = await hass.auth.async_create_user("guest", group_ids=[GROUP_ID_USER])
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            "reboot_node",
            {"device_id": device.id},
            blocking=True,
            context=Context(user_id=user.id),
        )
    assert emulator.reboots == []
    admin = await hass.auth.async_create_user("boss", group_ids=[GROUP_ID_ADMIN])
    await hass.services.async_call(
        DOMAIN,
        "reboot_node",
        {"device_id": device.id},
        blocking=True,
        context=Context(user_id=admin.id),
    )
    assert emulator.reboots


async def test_failed_form_never_echoes_password(hass: HomeAssistant) -> None:
    form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {
            CONF_HOST: "10.1",
            CONF_PORT: 443,
            CONF_PASSWORD: "hunter2-secret",
            CONF_USE_SSL: True,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert "hunter2-secret" not in repr(result["data_schema"].schema.items())
    for key in result["data_schema"].schema:
        if str(key) == CONF_PASSWORD:
            assert (key.description or {}).get("suggested_value") is None


async def test_reconfigure_moving_hub_requires_password(
    hass: HomeAssistant, emulator: Emulator, mock_entry: MockConfigEntry
) -> None:
    await _loaded(hass, mock_entry)
    form = await mock_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {CONF_HOST: "192.0.2.50", CONF_PORT: 443, CONF_USE_SSL: True},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "password_required"}


async def test_removing_last_entry_destroys_the_key(
    hass: HomeAssistant,
    emulator: Emulator,
    mock_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    await _loaded(hass, mock_entry)
    from custom_components.ha_linksys_velop.application.credentials import (  # noqa: PLC0415
        async_encrypt_password,
    )

    await async_encrypt_password(hass, PASSWORD)
    assert STORAGE_KEY in hass_storage
    await hass.config_entries.async_remove(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert STORAGE_KEY not in hass_storage

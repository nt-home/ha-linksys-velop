"""Config, reconfigure, reauth and options flows against the emulated hub."""

from __future__ import annotations

import socket
from typing import Any
from unittest.mock import patch

import probatio
import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant.common import MockConfigEntry

from custom_components.ha_linksys_velop import config_flow
from custom_components.ha_linksys_velop.application.credentials import (
    async_decrypt_password,
    async_encrypt_password,
)
from custom_components.ha_linksys_velop.const import (
    CONF_USE_SSL,
    DOMAIN,
    OPT_AREAS,
    OPT_CONSIDER_HOME,
    OPT_FAST_INTERVAL,
    OPT_INCLUDED_CHILDREN,
    OPT_SLOW_INTERVAL,
    OPT_TRACK_ALL,
    OPT_TRACKED_CLIENTS,
)
from custom_components.ha_linksys_velop.domain.errors import (
    InvalidHostError,
    VelopError,
)
from custom_components.ha_linksys_velop.domain.validation import validate_host
from tests.emulator.server import (
    PASSWORD,
    PRIMARY_SERIAL,
    Emulator,
    node_device,
)

WRONG = "wrong-password"  # noqa: S105
NEW_PASSWORD = "new-example-password"  # noqa: S105
CHILD_2 = "12K10M22222222"
CHILD_3 = "12K10M33333333"
LABEL_1 = "Living Room (MX2000 5678)"
LABEL_2 = "Bedroom (MX2000 2222)"
LABEL_3 = "Office (MX2000 3333)"
SECRETS = (PASSWORD, WRONG, NEW_PASSWORD)


def _user_input(emulator: Emulator, **over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        CONF_HOST: "127.0.0.1",
        CONF_PORT: emulator.port,
        CONF_PASSWORD: PASSWORD,
        CONF_USE_SSL: False,
    }
    data.update(over)
    return data


def _assert_no_secret(result: Any, *secrets: str) -> None:
    """A non-final flow result must never echo a password."""
    text = repr(result)
    for secret in secrets or SECRETS:
        assert secret not in text


async def _start_user(hass: HomeAssistant) -> Any:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def _submit_user(hass: HomeAssistant, emulator: Emulator, **over: Any) -> Any:
    form = await _start_user(hass)
    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        form["flow_id"], _user_input(emulator, **over)
    )


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


# --------------------------------------------------------------------------
# User flow
# --------------------------------------------------------------------------


async def _stored(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """Return the decrypted password stored in an entry."""
    return await async_decrypt_password(hass, entry.data[CONF_PASSWORD])


async def test_user_flow_happy_path(hass: HomeAssistant, emulator: Emulator) -> None:
    """The user step validates the hub and creates the entry directly."""
    result = await _submit_user(hass, emulator)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Velop MX2000 5678"
    assert result["data"] == {
        CONF_HOST: "127.0.0.1",
        CONF_PORT: emulator.port,
        CONF_USE_SSL: False,
        CONF_VERIFY_SSL: False,
        CONF_PASSWORD: result["data"][CONF_PASSWORD],
    }
    assert result["data"][CONF_PASSWORD].startswith("enc:v1:")
    assert PASSWORD not in str(result["data"])
    assert await _stored(hass, result["result"]) == PASSWORD
    # No node picker: every node is added and HA's own dialog assigns areas.
    assert result["options"] == {}
    entry = result["result"]
    assert entry.unique_id == PRIMARY_SERIAL
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    registry = dr.async_get(hass)
    for serial in (PRIMARY_SERIAL, CHILD_2, CHILD_3):
        assert registry.async_get_device_by_identifier((DOMAIN, serial), entry.entry_id)


async def test_user_flow_empty_port_defaults(
    hass: HomeAssistant, emulator: Emulator
) -> None:
    """A missing port defaults to 80 (HTTP) and fails to connect on loopback."""
    form = await _start_user(hass)
    user_input = _user_input(emulator)
    del user_input[CONF_PORT]
    with patch(
        "custom_components.ha_linksys_velop.config_flow.validate_port",
        side_effect=config_flow.validate_port,
    ) as spy:
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], user_input
        )
    spy.assert_called_once_with(None, use_ssl=False)
    assert spy.call_args is not None
    assert result["type"] in (FlowResultType.FORM, FlowResultType.CREATE_ENTRY)


async def test_probe_blank_string_port(hass: HomeAssistant, emulator: Emulator) -> None:
    """The probe treats an empty-string port like a missing one."""
    flow = config_flow.VelopConfigFlow()
    flow.hass = hass
    with (
        patch(
            "custom_components.ha_linksys_velop.config_flow.validate_port",
            side_effect=config_flow.validate_port,
        ) as spy,
        pytest.raises(VelopError),
    ):
        await flow._async_probe(_user_input(emulator, **{CONF_PORT: ""}))
    spy.assert_called_once_with(None, use_ssl=False)


async def test_user_flow_default_port_ssl(
    hass: HomeAssistant, emulator: Emulator
) -> None:
    """An empty port with SSL maps to 443."""
    form = await _start_user(hass)
    user_input = _user_input(emulator, **{CONF_USE_SSL: True})
    del user_input[CONF_PORT]
    with patch(
        "custom_components.ha_linksys_velop.config_flow.validate_port",
        side_effect=config_flow.validate_port,
    ) as spy:
        await hass.config_entries.flow.async_configure(form["flow_id"], user_input)
    spy.assert_called_once_with(None, use_ssl=True)


async def test_user_flow_out_of_range_port(
    hass: HomeAssistant, emulator: Emulator
) -> None:
    """The selector rejects a port outside 1-65535 before the probe runs."""
    form = await _start_user(hass)
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            form["flow_id"], _user_input(emulator, **{CONF_PORT: 70000})
        )


async def test_probe_port_out_of_range(hass: HomeAssistant, emulator: Emulator) -> None:
    """The domain rule still rejects a bad port if the selector is bypassed."""
    flow = config_flow.VelopConfigFlow()
    flow.hass = hass
    with pytest.raises(InvalidHostError):
        await flow._async_probe(_user_input(emulator, **{CONF_PORT: 70000}))


async def test_user_flow_wrong_password(
    hass: HomeAssistant, emulator: Emulator
) -> None:
    """A wrong password re-shows the form with invalid_auth and no entry."""
    result = await _submit_user(hass, emulator, **{CONF_PASSWORD: WRONG})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}
    _assert_no_secret(result)
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_user_flow_account_locked(
    hass: HomeAssistant, emulator: Emulator
) -> None:
    """A locked admin account is reported distinctly."""
    emulator.locked = True
    result = await _submit_user(hass, emulator, **{CONF_PASSWORD: WRONG})
    assert result["errors"] == {"base": "account_locked"}
    _assert_no_secret(result)
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_user_flow_cannot_connect(
    hass: HomeAssistant, emulator: Emulator
) -> None:
    """A closed port gives cannot_connect."""
    result = await _submit_user(hass, emulator, **{CONF_PORT: _closed_port()})
    assert result["errors"] == {"base": "cannot_connect"}
    _assert_no_secret(result)


async def test_user_flow_timeout(hass: HomeAssistant, emulator: Emulator) -> None:
    """A slow hub gives timeout."""
    emulator.delay = 1.0
    with patch(
        "custom_components.ha_linksys_velop.infrastructure.transport.TOTAL_TIMEOUT",
        0.2,
    ):
        result = await _submit_user(hass, emulator)
    assert result["errors"] == {"base": "timeout"}


@pytest.mark.parametrize("override", ["status", "raw"])
async def test_user_flow_unsupported_device(
    hass: HomeAssistant, emulator: Emulator, override: str
) -> None:
    """A non-JNAP responder gives unsupported_device."""
    if override == "status":
        emulator.status_override = 404
    else:
        emulator.raw_override = b"not json"
    result = await _submit_user(hass, emulator)
    assert result["errors"] == {"base": "unsupported_device"}


async def test_user_flow_not_primary_node(
    hass: HomeAssistant, emulator: Emulator
) -> None:
    """A child node is rejected."""
    emulator.mode = "slave"
    result = await _submit_user(hass, emulator)
    assert result["errors"] == {"base": "not_primary_node"}


async def test_user_flow_invalid_host(hass: HomeAssistant, emulator: Emulator) -> None:
    """The real host validation rejects loopback and reports it on the field."""
    with patch(
        "custom_components.ha_linksys_velop.config_flow.validate_host",
        side_effect=validate_host,
    ):
        result = await _submit_user(hass, emulator)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_HOST: "invalid_host"}
    assert hass.config_entries.async_entries(DOMAIN) == []


def test_error_key_unknown() -> None:
    """An unmapped domain error falls back to unknown."""
    assert config_flow._error_key(VelopError("x")) == "unknown"


async def test_user_flow_duplicate_updates_host(
    hass: HomeAssistant, emulator: Emulator, mock_entry: MockConfigEntry
) -> None:
    """Configuring a known hub aborts and refreshes host and port."""
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_entry, data={**mock_entry.data, CONF_HOST: "192.0.2.77", CONF_PORT: 81}
    )
    with patch(
        "custom_components.ha_linksys_velop.async_setup_entry", return_value=True
    ):
        result = await _submit_user(hass, emulator)
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_entry.data[CONF_HOST] == "127.0.0.1"
    assert mock_entry.data[CONF_PORT] == emulator.port
    assert await _stored(hass, mock_entry) == PASSWORD


async def test_user_flow_bridge_mode(hass: HomeAssistant, emulator: Emulator) -> None:
    """A hub in bridge mode can still be added."""
    emulator.wan_type = "Bridge"
    result = await _submit_user(hass, emulator)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Velop MX2000 5678"


async def test_user_flow_self_signed_https(hass: HomeAssistant) -> None:
    """HTTPS works against a self-signed certificate (no verification)."""
    emu = Emulator()
    await emu.start(use_ssl=True)
    try:
        result = await _submit_user(hass, emu, **{CONF_USE_SSL: True})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_USE_SSL] is True
    finally:
        await emu.stop()


async def test_user_flow_single_node(hass: HomeAssistant, emulator: Emulator) -> None:
    """A mesh with only the primary node can be added."""
    emulator.devices = [d for d in emulator.devices if d["deviceID"] == "node-1"]
    result = await _submit_user(hass, emulator)
    assert result["type"] is FlowResultType.CREATE_ENTRY


# --------------------------------------------------------------------------
# Reconfigure
# --------------------------------------------------------------------------


async def _start_reconfigure(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    entry.add_to_hass(hass)
    return await entry.start_reconfigure_flow(hass)


async def test_reconfigure_happy_path_keeps_password(
    hass: HomeAssistant, emulator: Emulator, mock_entry: MockConfigEntry
) -> None:
    """An empty password keeps the stored one; the entry reloads."""
    form = await _start_reconfigure(hass, mock_entry)
    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "reconfigure"
    with patch(
        "custom_components.ha_linksys_velop.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"],
            {CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port, CONF_USE_SSL: False},
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert await _stored(hass, mock_entry) == PASSWORD
    assert mock_entry.unique_id == PRIMARY_SERIAL


async def test_reconfigure_new_password(
    hass: HomeAssistant, emulator: Emulator, mock_entry: MockConfigEntry
) -> None:
    """A new password replaces the stored one once it validates."""
    emulator.password = NEW_PASSWORD
    form = await _start_reconfigure(hass, mock_entry)
    with patch(
        "custom_components.ha_linksys_velop.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"],
            {
                CONF_HOST: "127.0.0.1",
                CONF_PORT: emulator.port,
                CONF_PASSWORD: NEW_PASSWORD,
                CONF_USE_SSL: False,
            },
        )
        await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert await _stored(hass, mock_entry) == NEW_PASSWORD


@pytest.mark.parametrize(
    ("setup", "error"),
    [
        ("wrong_password", "invalid_auth"),
        ("closed", "cannot_connect"),
    ],
)
async def test_reconfigure_errors(
    hass: HomeAssistant,
    emulator: Emulator,
    mock_entry: MockConfigEntry,
    setup: str,
    error: str,
) -> None:
    """Failures re-show the form and leave the entry untouched."""
    form = await _start_reconfigure(hass, mock_entry)
    user_input = {
        CONF_HOST: "127.0.0.1",
        CONF_PORT: emulator.port,
        CONF_PASSWORD: WRONG if setup == "wrong_password" else "",
        CONF_USE_SSL: False,
    }
    if setup == "closed":
        user_input[CONF_PORT] = _closed_port()
    result = await hass.config_entries.flow.async_configure(form["flow_id"], user_input)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    _assert_no_secret(result)
    assert await _stored(hass, mock_entry) == PASSWORD


async def test_reconfigure_invalid_host(
    hass: HomeAssistant, emulator: Emulator, mock_entry: MockConfigEntry
) -> None:
    """An invalid host is reported on the host field."""
    form = await _start_reconfigure(hass, mock_entry)
    with patch(
        "custom_components.ha_linksys_velop.config_flow.validate_host",
        side_effect=validate_host,
    ):
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"],
            {CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port, CONF_USE_SSL: False},
        )
    assert result["errors"] == {CONF_HOST: "invalid_host"}


async def test_reconfigure_wrong_hub(
    hass: HomeAssistant,
    emulator: Emulator,
    mock_entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A different serial aborts with wrong_hub."""
    form = await _start_reconfigure(hass, mock_entry)

    def other() -> dict[str, Any]:
        out = Emulator._a_core_GetDeviceInfo(emulator)
        out["output"]["serialNumber"] = "99Z99Z99999999"
        return out

    monkeypatch.setattr(emulator, "_a_core_GetDeviceInfo", other)
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port, CONF_USE_SSL: False},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_hub"
    assert mock_entry.unique_id == PRIMARY_SERIAL


# --------------------------------------------------------------------------
# Reauth
# --------------------------------------------------------------------------


async def _reauth_form(
    hass: HomeAssistant, emulator: Emulator, entry_data: dict[str, Any]
) -> tuple[MockConfigEntry, Any]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**entry_data, CONF_PASSWORD: await async_encrypt_password(hass, WRONG)},
        unique_id=PRIMARY_SERIAL,
        title="Velop MX2000 5678",
    )
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = list(entry.async_get_active_flows(hass, {config_entries.SOURCE_REAUTH}))
    assert len(flows) == 1
    form = await hass.config_entries.flow.async_configure(flows[0]["flow_id"])
    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "reauth_confirm"
    return entry, form


async def test_reauth_success(
    hass: HomeAssistant, emulator: Emulator, entry_data: dict[str, Any]
) -> None:
    """A valid new password updates the entry and reloads it."""
    entry, form = await _reauth_form(hass, emulator, entry_data)
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert await _stored(hass, entry) == PASSWORD
    assert entry.state is ConfigEntryState.LOADED


async def test_reauth_wrong_password_then_success(
    hass: HomeAssistant, emulator: Emulator, entry_data: dict[str, Any]
) -> None:
    """A bad password shows invalid_auth; a later good one succeeds."""
    entry, form = await _reauth_form(hass, emulator, entry_data)
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {CONF_PASSWORD: "still-wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    _assert_no_secret(result, "still-wrong", PASSWORD)
    assert await _stored(hass, entry) == WRONG
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {CONF_PASSWORD: PASSWORD}
    )
    assert result["reason"] == "reauth_successful"


async def test_reauth_wrong_hub(
    hass: HomeAssistant,
    emulator: Emulator,
    entry_data: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reauth against a different hub aborts with wrong_hub."""
    entry, form = await _reauth_form(hass, emulator, entry_data)

    def other() -> dict[str, Any]:
        out = Emulator._a_core_GetDeviceInfo(emulator)
        out["output"]["serialNumber"] = "99Z99Z99999999"
        return out

    monkeypatch.setattr(emulator, "_a_core_GetDeviceInfo", other)
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_hub"
    assert await _stored(hass, entry) == WRONG


# --------------------------------------------------------------------------
# Options
# --------------------------------------------------------------------------


LABELS = {1: LABEL_1, 2: LABEL_2, 3: LABEL_3}


def _k(label: str, field: str) -> str:
    """Form key of a node field: the node label, or its numbered Area/Remove key."""
    slot = next(i for i, name in LABELS.items() if name == label)
    return {
        "Name": config_flow._name_key(slot),
        "Area": config_flow._area_key(slot),
        "Remove node": config_flow._remove_key(slot),
    }[field]


def _cards(form: Any) -> dict[str, dict[str, Any]]:
    """Map section name to its inner fields for an options form."""
    return {
        str(k): {str(i): i for i in v.schema.schema}
        for k, v in form["data_schema"].schema.items()
    }


def _sections(form: Any) -> dict[str, Any]:
    return {str(k): v for k, v in form["data_schema"].schema.items()}


async def test_options_flow_full(
    hass: HomeAssistant, emulator: Emulator, mock_entry: MockConfigEntry
) -> None:
    """One card per node; names, areas and removal are saved and applied."""
    await _setup(hass, mock_entry)
    assert mock_entry.state is ConfigEntryState.LOADED
    areas = ar.async_get(hass)
    den = areas.async_create("Den")
    cellar = areas.async_create("Cellar")
    devices = dr.async_get(hass)

    form = await hass.config_entries.options.async_init(mock_entry.entry_id)
    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "init"
    cards = _cards(form)
    assert list(cards) == ["refresh", "nodes", "presence"]
    assert set(cards["refresh"]) == {OPT_FAST_INTERVAL, OPT_SLOW_INTERVAL}
    # The primary node has no remove switch; child nodes do, default off.
    mesh = cards["nodes"]
    # Each node: its title as the first input's label, then area and (for
    # child nodes only) the remove switch.
    assert list(mesh) == [
        LABEL_1,
        _k(LABEL_1, "Name"),
        _k(LABEL_1, "Area"),
        LABEL_2,
        _k(LABEL_2, "Name"),
        _k(LABEL_2, "Area"),
        _k(LABEL_2, "Remove node"),
        LABEL_3,
        _k(LABEL_3, "Name"),
        _k(LABEL_3, "Area"),
        _k(LABEL_3, "Remove node"),
    ]
    # Unique keys, but every node shows the same visible input labels; the
    # node title rows are literal constants.
    shown = {k.replace("\u200b", "") for k in mesh}
    assert shown == {
        LABEL_1,
        LABEL_2,
        LABEL_3,
        "Name",
        "Assign area",
        "Remove node",
    }
    assert mesh[_k(LABEL_2, "Remove node")].default() is False
    assert mesh[_k(LABEL_2, "Name")].default() == "Bedroom"
    assert set(cards["presence"]) == {
        OPT_TRACK_ALL,
        OPT_TRACKED_CLIENTS,
        OPT_CONSIDER_HOME,
    }
    inner = _sections(form)["presence"].schema.schema
    client_opts = inner[cards["presence"][OPT_TRACKED_CLIENTS]].config["options"]
    assert {o["value"] for o in client_opts} == {
        "aa:bb:cc:00:00:01",
        "aa:bb:cc:00:00:02",
        "aa:bb:cc:00:00:03",
    }

    result = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            "refresh": {OPT_FAST_INTERVAL: 30, OPT_SLOW_INTERVAL: 20},
            "nodes": {
                _k(LABEL_1, "Name"): "Hall hub",
                _k(LABEL_2, "Name"): "Bedroom",
                _k(LABEL_2, "Area"): den.id,
                _k(LABEL_2, "Remove node"): False,
                _k(LABEL_3, "Name"): "Office",
                _k(LABEL_3, "Area"): cellar.id,
                _k(LABEL_3, "Remove node"): True,
            },
            "presence": {
                OPT_TRACK_ALL: False,
                OPT_TRACKED_CLIENTS: ["aa:bb:cc:00:00:01"],
                OPT_CONSIDER_HOME: 120,
            },
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert mock_entry.options == {
        OPT_FAST_INTERVAL: 30,
        OPT_SLOW_INTERVAL: 20,
        OPT_TRACK_ALL: False,
        OPT_TRACKED_CLIENTS: ["aa:bb:cc:00:00:01"],
        OPT_CONSIDER_HOME: 120,
        OPT_AREAS: {CHILD_2: den.id, CHILD_3: cellar.id},
        OPT_INCLUDED_CHILDREN: [CHILD_2],
    }
    assert mock_entry.state is ConfigEntryState.LOADED
    hub = devices.async_get_device_by_identifier(
        (DOMAIN, PRIMARY_SERIAL), mock_entry.entry_id
    )
    assert hub is not None
    assert hub.name_by_user == "Hall hub"
    bedroom = devices.async_get_device_by_identifier(
        (DOMAIN, CHILD_2), mock_entry.entry_id
    )
    assert bedroom is not None and bedroom.area_id == den.id
    # The removed child is gone from the registry.
    assert (
        devices.async_get_device_by_identifier((DOMAIN, CHILD_3), mock_entry.entry_id)
        is None
    )

    # Second pass: the removed node is still offered (switched on) so it can
    # be brought back; an unchanged name is not stored as a user name.
    form = await hass.config_entries.options.async_init(mock_entry.entry_id)
    cards = _cards(form)
    assert list(cards) == ["refresh", "nodes", "presence"]
    assert cards["nodes"][_k(LABEL_3, "Remove node")].default() is True
    assert cards["nodes"][_k(LABEL_1, "Name")].default() == "Hall hub"
    suggested = {
        str(k): (k.description or {}).get("suggested_value")
        for k in _sections(form)["nodes"].schema.schema
    }
    assert suggested[_k(LABEL_3, "Area")] == cellar.id
    result = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            "refresh": {OPT_FAST_INTERVAL: 30, OPT_SLOW_INTERVAL: 20},
            "nodes": {
                LABEL_1: "",
                _k(LABEL_1, "Name"): "Living Room",
                _k(LABEL_2, "Name"): "Bedroom",
                _k(LABEL_2, "Area"): den.id,
                _k(LABEL_2, "Remove node"): False,
                _k(LABEL_3, "Name"): "Office",
                _k(LABEL_3, "Remove node"): False,
            },
            "presence": {OPT_TRACK_ALL: True, OPT_CONSIDER_HOME: 60},
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert mock_entry.options[OPT_INCLUDED_CHILDREN] == [CHILD_2, CHILD_3]
    assert mock_entry.options[OPT_AREAS] == {CHILD_2: den.id}
    assert mock_entry.options[OPT_TRACK_ALL] is True
    hub = devices.async_get_device_by_identifier(
        (DOMAIN, PRIMARY_SERIAL), mock_entry.entry_id
    )
    assert hub is not None and hub.name_by_user is None


async def test_options_flow_area_for_unknown_device(
    hass: HomeAssistant, emulator: Emulator, mock_entry: MockConfigEntry
) -> None:
    """A node with no registered device is skipped when applying names and areas."""
    await _setup(hass, mock_entry)
    area = ar.async_get(hass).async_create("Hall")
    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier(
        (DOMAIN, CHILD_2), mock_entry.entry_id
    )
    assert device is not None
    registry.async_remove_device(device.id)
    form = await hass.config_entries.options.async_init(mock_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            "refresh": {OPT_FAST_INTERVAL: 30, OPT_SLOW_INTERVAL: 20},
            "nodes": {
                _k(LABEL_2, "Name"): "Renamed",
                _k(LABEL_2, "Area"): area.id,
            },
            "presence": {},
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_entry.options[OPT_AREAS] == {CHILD_2: area.id}


@pytest.mark.parametrize("prior", [True, False])
async def test_options_flow_without_runtime_data(
    hass: HomeAssistant, entry_data: dict[str, Any], prior: bool
) -> None:
    """With the entry not loaded the form has no node or client fields."""
    options = {OPT_INCLUDED_CHILDREN: [CHILD_2]} if prior else {}
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=entry_data,
        options=options,
        unique_id=PRIMARY_SERIAL,
    )
    entry.add_to_hass(hass)
    form = await hass.config_entries.options.async_init(entry.entry_id)
    cards = _cards(form)
    assert list(cards) == ["refresh", "presence"]
    assert OPT_TRACKED_CLIENTS not in cards["presence"]
    result = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {"refresh": {OPT_FAST_INTERVAL: 30, OPT_SLOW_INTERVAL: 20}, "presence": {}},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    if prior:
        assert result["data"][OPT_INCLUDED_CHILDREN] == [CHILD_2]
    else:
        assert OPT_INCLUDED_CHILDREN not in result["data"]


def test_node_device_helper_used() -> None:
    """Sanity check on the emulator helper used by these tests."""
    assert (
        node_device("n", "SER1234", "Slave", "X")["unit"]["serialNumber"] == "SER1234"
    )


def test_slot_keys_are_unique_but_display_identically() -> None:
    """Area and Remove node keys differ per slot yet show the same text."""
    areas = {config_flow._area_key(i) for i in range(1, 20)}
    removes = {config_flow._remove_key(i) for i in range(1, 20)}
    assert len(areas) == len(removes) == 19
    assert {k.replace("\u200b", "") for k in areas} == {"Assign area"}
    assert {k.replace("\u200b", "") for k in removes} == {"Remove node"}


async def test_options_form_serialises_for_the_frontend(
    hass: HomeAssistant, emulator: Emulator, mock_entry: MockConfigEntry
) -> None:
    """The options form converts to the field list the frontend receives."""
    await _setup(hass, mock_entry)
    form = await hass.config_entries.options.async_init(mock_entry.entry_id)
    fields = probatio.to_field_list(
        form["data_schema"], custom_serializer=cv.custom_serializer
    )
    assert [f["name"] for f in fields] == ["refresh", "nodes", "presence"]
    assert all(f["type"] == "expandable" for f in fields)
    nodes = next(f for f in fields if f["name"] == "nodes")
    # Home Assistant does not support nesting sections, so every field is flat.
    assert all(f.get("type") != "expandable" for f in nodes["schema"])
    titles = [f for f in nodes["schema"] if f.get("type") == "constant"]
    assert [f["name"] for f in titles] == [LABEL_1, LABEL_2, LABEL_3]
    assert all(f["value"] == "" for f in titles)
    assert len(nodes["schema"]) == 11

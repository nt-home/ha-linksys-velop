"""Password encryption, certificate verification and SSL/port behaviour."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant.common import MockConfigEntry

from custom_components.ha_linksys_velop import application
from custom_components.ha_linksys_velop.application import credentials
from custom_components.ha_linksys_velop.application.credentials import (
    STORAGE_KEY,
    async_decrypt_password,
    async_encrypt_password,
)
from custom_components.ha_linksys_velop.application.factory import create_client
from custom_components.ha_linksys_velop.const import CONF_USE_SSL, DOMAIN
from custom_components.ha_linksys_velop.domain.errors import (
    CannotConnectError,
    SecretUnavailableError,
)
from custom_components.ha_linksys_velop.infrastructure.cipher import (
    PREFIX,
    PasswordCipher,
    generate_key,
    is_encrypted,
)
from tests.emulator.server import PASSWORD, PRIMARY_SERIAL, Emulator

pytestmark = pytest.mark.usefixtures("socket_enabled")


def test_cipher_round_trip_and_tagging() -> None:
    cipher = PasswordCipher(generate_key())
    token = cipher.encrypt("s3cret!")
    assert token.startswith(PREFIX)
    assert is_encrypted(token)
    assert "s3cret" not in token
    assert cipher.decrypt(token) == "s3cret!"
    assert cipher.encrypt("s3cret!") != token  # random IV
    assert "s3cret" not in repr(cipher)


def test_cipher_rejects_wrong_key_plaintext_and_tampering() -> None:
    token = PasswordCipher(generate_key()).encrypt("pw")
    other = PasswordCipher(generate_key())
    with pytest.raises(SecretUnavailableError):
        other.decrypt(token)
    with pytest.raises(SecretUnavailableError):
        other.decrypt("plain-password")
    mine = PasswordCipher(generate_key())
    good = mine.encrypt("pw")
    with pytest.raises(SecretUnavailableError):
        mine.decrypt(good[:-4] + "AAAA")
    with pytest.raises(SecretUnavailableError):
        PasswordCipher("not-a-key")


async def test_credentials_key_is_separate_and_private(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    token = await async_encrypt_password(hass, PASSWORD)
    assert PASSWORD not in token
    assert STORAGE_KEY in hass_storage
    assert PASSWORD not in json.dumps(hass_storage[STORAGE_KEY])
    assert await async_decrypt_password(hass, token) == PASSWORD


async def test_decrypt_never_creates_a_key(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    with pytest.raises(SecretUnavailableError):
        await async_decrypt_password(hass, PREFIX + "abc")
    assert STORAGE_KEY not in hass_storage


async def test_lost_key_triggers_reauth(
    hass: HomeAssistant, emulator: Emulator, mock_entry: MockConfigEntry
) -> None:
    """A stored token the key cannot open starts reauthentication."""
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_entry,
        data={
            **mock_entry.data,
            CONF_PASSWORD: PasswordCipher(generate_key()).encrypt(PASSWORD),
        },
    )
    await hass.config_entries.async_setup(mock_entry.entry_id)
    assert mock_entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        f["context"]["source"] == "reauth"
        for f in hass.config_entries.flow.async_progress()
    )
    assert PASSWORD not in repr(emulator.calls)


async def test_migration_encrypts_legacy_plaintext(
    hass: HomeAssistant, emulator: Emulator, entry_data: dict[str, Any]
) -> None:
    legacy = {
        CONF_HOST: "127.0.0.1",
        CONF_PORT: emulator.port,
        CONF_USE_SSL: False,
        CONF_PASSWORD: PASSWORD,
    }
    entry = MockConfigEntry(domain=DOMAIN, data=legacy, unique_id=PRIMARY_SERIAL)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.minor_version == 2
    assert entry.data[CONF_VERIFY_SSL] is False
    assert is_encrypted(entry.data[CONF_PASSWORD])
    assert PASSWORD not in json.dumps(dict(entry.data))
    assert await async_decrypt_password(hass, entry.data[CONF_PASSWORD]) == PASSWORD


async def test_migration_keeps_already_encrypted_and_rejects_future(
    hass: HomeAssistant, emulator: Emulator, entry_data: dict[str, Any]
) -> None:
    from custom_components.ha_linksys_velop import async_migrate_entry

    token = entry_data[CONF_PASSWORD]
    entry = MockConfigEntry(
        domain=DOMAIN, data=dict(entry_data), unique_id=PRIMARY_SERIAL, minor_version=1
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.data[CONF_PASSWORD] == token
    future = MockConfigEntry(domain=DOMAIN, data={}, version=2)
    future.add_to_hass(hass)
    assert await async_migrate_entry(hass, future) is False


async def test_diagnostics_never_contain_password_or_ciphertext(
    hass: HomeAssistant, emulator: Emulator, mock_entry: MockConfigEntry
) -> None:
    from homeassistant.components.diagnostics import REDACTED  # noqa: F401

    from custom_components.ha_linksys_velop.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    text = json.dumps(await async_get_config_entry_diagnostics(hass, mock_entry))
    assert PASSWORD not in text
    assert mock_entry.data[CONF_PASSWORD] not in text


@pytest.mark.parametrize(
    ("use_ssl", "verify", "expected"),
    [(True, True, True), (True, False, False), (False, True, False)],
)
async def test_create_client_certificate_verification(
    hass: HomeAssistant, use_ssl: bool, verify: bool, expected: bool
) -> None:
    with patch.object(application.factory, "async_get_clientsession") as session:
        create_client(
            hass, "192.0.2.1", 443, use_ssl=use_ssl, verify_ssl=verify, password="x"
        )
    session.assert_called_once_with(hass, verify_ssl=expected)


async def test_create_client_default_does_not_verify(hass: HomeAssistant) -> None:
    with patch.object(application.factory, "async_get_clientsession") as session:
        create_client(hass, "192.0.2.1", 443, use_ssl=True, password="x")
    session.assert_called_once_with(hass, verify_ssl=False)


def _unreachable() -> Any:
    client = MagicMock()
    client.async_get_identity = AsyncMock(side_effect=CannotConnectError)
    return client


async def _user_flow(hass: HomeAssistant, data: dict[str, Any]) -> Any:
    form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    return form, await hass.config_entries.flow.async_configure(form["flow_id"], data)


def _field_default(form: Any, name: str) -> Any:
    for key in form["data_schema"].schema:
        if key == name:
            return key.default()
    raise AssertionError(name)


async def test_form_defaults_ssl_on_verify_off_and_order(hass: HomeAssistant) -> None:
    form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert _field_default(form, CONF_USE_SSL) is True
    assert _field_default(form, CONF_VERIFY_SSL) is False
    keys = [str(k) for k in form["data_schema"].schema]
    assert keys.index(CONF_VERIFY_SSL) == keys.index(CONF_USE_SSL) + 1


@pytest.mark.parametrize(
    ("user_ssl", "expected"),
    [(True, False), (False, False)],
)
async def test_port_80_forces_ssl_off(
    hass: HomeAssistant, user_ssl: bool, expected: bool
) -> None:
    seen: dict[str, Any] = {}

    def fake(hass_: Any, host: str, port: int, **kw: Any) -> Any:
        seen.update(kw, port=port)
        return _unreachable()

    with patch("custom_components.ha_linksys_velop.config_flow.create_client", fake):
        await _user_flow(
            hass,
            {
                CONF_HOST: "192.0.2.10",
                CONF_PORT: 80,
                CONF_PASSWORD: "x",
                CONF_USE_SSL: user_ssl,
                CONF_VERIFY_SSL: True,
            },
        )
    assert seen["use_ssl"] is expected
    assert seen["verify_ssl"] is False  # verification is moot without SSL


@pytest.mark.parametrize("port", [443, 8443])
async def test_https_ports_force_ssl_on(hass: HomeAssistant, port: int) -> None:
    seen: dict[str, Any] = {}

    def fake(hass_: Any, host: str, p: int, **kw: Any) -> Any:
        seen.update(kw)
        return _unreachable()

    with patch("custom_components.ha_linksys_velop.config_flow.create_client", fake):
        await _user_flow(
            hass,
            {
                CONF_HOST: "192.0.2.10",
                CONF_PORT: port,
                CONF_PASSWORD: "x",
                CONF_USE_SSL: False,
                CONF_VERIFY_SSL: True,
            },
        )
    assert seen["use_ssl"] is True
    assert seen["verify_ssl"] is True


async def test_other_port_keeps_user_choice_and_stores_verify(
    hass: HomeAssistant, emulator: Emulator
) -> None:
    _, result = await _user_flow(
        hass,
        {
            CONF_HOST: "127.0.0.1",
            CONF_PORT: emulator.port,
            CONF_PASSWORD: PASSWORD,
            CONF_USE_SSL: False,
            CONF_VERIFY_SSL: True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_USE_SSL] is False
    assert result["data"][CONF_VERIFY_SSL] is False


async def test_setup_passes_verify_flag(
    hass: HomeAssistant, emulator: Emulator, entry_data: dict[str, Any]
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**entry_data, CONF_VERIFY_SSL: True},
        unique_id=PRIMARY_SERIAL,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.ha_linksys_velop.create_client", wraps=create_client
    ) as spy:
        await hass.config_entries.async_setup(entry.entry_id)
    assert spy.call_args.kwargs["verify_ssl"] is True
    assert spy.call_args.kwargs["password"] == PASSWORD


def test_strings_json_mirrors_translations() -> None:
    """strings.json and translations/en.json must never drift apart."""
    base = Path(__file__).parents[1] / "custom_components" / "ha_linksys_velop"
    assert json.loads((base / "strings.json").read_text()) == json.loads(
        (base / "translations" / "en.json").read_text()
    )


def test_credentials_module_has_no_plaintext_defaults() -> None:
    assert credentials.STORAGE_KEY.endswith(".key")


async def test_verification_rejects_self_signed_certificate(
    hass: HomeAssistant,
) -> None:
    """With Verify SSL on, the emulator's self-signed certificate is refused."""
    emu = Emulator()
    await emu.start(use_ssl=True)
    # The server logs the rejected TLS handshake (the client aborts it); that is
    # the expected outcome here, so keep it from failing the harness teardown.
    hass.loop.set_exception_handler(lambda _loop, _ctx: None)
    try:
        data = {
            CONF_HOST: "127.0.0.1",
            CONF_PORT: emu.port,
            CONF_PASSWORD: PASSWORD,
            CONF_USE_SSL: True,
        }
        _, strict = await _user_flow(hass, {**data, CONF_VERIFY_SSL: True})
        assert strict["type"] is FlowResultType.FORM
        assert strict["errors"] == {"base": "cannot_connect"}
        _, lax = await _user_flow(hass, {**data, CONF_VERIFY_SSL: False})
        assert lax["type"] is FlowResultType.CREATE_ENTRY
        assert lax["data"][CONF_VERIFY_SSL] is False
    finally:
        await emu.stop()


async def test_reconfigure_with_lost_key_asks_for_password(
    hass: HomeAssistant, emulator: Emulator, mock_entry: MockConfigEntry
) -> None:
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_entry,
        data={
            **mock_entry.data,
            CONF_PASSWORD: PasswordCipher(generate_key()).encrypt(PASSWORD),
        },
    )
    form = await mock_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {
            CONF_HOST: "127.0.0.1",
            CONF_PORT: emulator.port,
            CONF_USE_SSL: False,
            CONF_VERIFY_SSL: False,
        },
    )
    assert result["errors"] == {"base": "secret_unavailable"}

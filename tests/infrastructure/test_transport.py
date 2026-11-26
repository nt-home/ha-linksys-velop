"""Tests for the JNAP transport against the emulator and ad-hoc servers."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any
from unittest.mock import MagicMock, patch

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.ha_linksys_velop.domain.errors import (
    AccountLockedError,
    CannotConnectError,
    ConnectionTimeoutError,
    InvalidAuthError,
    JnapResultError,
    NotPrimaryNodeError,
    UnknownActionError,
    UnsupportedDeviceError,
)
from custom_components.ha_linksys_velop.infrastructure import transport as tr
from custom_components.ha_linksys_velop.infrastructure.transport import (
    ACTION_TRANSACTION,
    JNAP_BASE,
    JnapAction,
    JnapResponse,
    JnapTransport,
    raise_for_result,
)
from tests.emulator.server import PASSWORD, Emulator

GET_INFO = f"{JNAP_BASE}core/GetDeviceInfo"
GET_DEVICES = f"{JNAP_BASE}devicelist/GetDevices"
WAN = f"{JNAP_BASE}router/GetWANStatus"


@pytest.fixture
async def session() -> AsyncGenerator[aiohttp.ClientSession]:
    """A real aiohttp client session."""
    async with aiohttp.ClientSession() as sess:
        yield sess


def make(
    session: aiohttp.ClientSession,
    emulator: Emulator,
    password: str | None = PASSWORD,
) -> JnapTransport:
    """Build a transport aimed at the emulator."""
    return JnapTransport(
        session, "127.0.0.1", emulator.port, use_ssl=False, password=password
    )


def raw(emulator: Emulator, payload: object) -> None:
    """Make the emulator answer every request with this JSON payload."""
    emulator.raw_override = json.dumps(payload).encode()


# result mapping -------------------------------------------------------------


def test_raise_for_result_ok() -> None:
    """OK does not raise."""
    raise_for_result("OK")


@pytest.mark.parametrize(
    ("result", "exc"),
    [
        ("_ErrorUnauthorized", InvalidAuthError),
        ("ErrorInvalidAdminPassword", InvalidAuthError),
        ("ErrorAdminAccountLocked", AccountLockedError),
        ("ErrorPasswordCheckDelayed", AccountLockedError),
        ("_ErrorUnknownAction", UnknownActionError),
        ("ErrorDeviceNotInMasterMode", NotPrimaryNodeError),
        ("_ErrorSomethingElse", JnapResultError),
    ],
)
def test_raise_for_result_mapping(result: str, exc: type[Exception]) -> None:
    """Each result code maps to its domain error."""
    with pytest.raises(exc) as info:
        raise_for_result(result)
    assert type(info.value) is exc
    if exc in (UnknownActionError, JnapResultError):
        assert info.value.result == result  # type: ignore[attr-defined]


def test_jnap_response_ok() -> None:
    """ok reflects the result."""
    assert JnapResponse("OK", {}).ok
    assert not JnapResponse("_Error", {}).ok


@pytest.mark.parametrize(
    ("result", "exc"),
    [
        ("_ErrorUnauthorized", InvalidAuthError),
        ("ErrorAdminAccountLocked", AccountLockedError),
        ("_ErrorUnknownAction", UnknownActionError),
        ("ErrorDeviceNotInMasterMode", NotPrimaryNodeError),
        ("ErrorOther", JnapResultError),
    ],
)
async def test_call_maps_results(
    session: aiohttp.ClientSession,
    emulator: Emulator,
    result: str,
    exc: type[Exception],
) -> None:
    """async_call and async_transaction raise mapped errors."""
    raw(emulator, {"result": result})
    client = make(session, emulator)
    with pytest.raises(exc):
        await client.async_call(GET_DEVICES)
    with pytest.raises(exc):
        await client.async_transaction([JnapAction(GET_DEVICES)])


# auth / headers -------------------------------------------------------------


async def test_authenticated_call(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """The right password authenticates; the header is sent."""
    out = await make(session, emulator).async_call(GET_DEVICES)
    assert out["devices"]
    assert emulator.calls[-1] == ("devicelist/GetDevices", True)


async def test_unauthenticated_call_sends_no_auth(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """authenticated=False omits credentials."""
    out = await make(session, emulator).async_call(GET_INFO, authenticated=False)
    assert out["modelNumber"] == "MX2000"
    assert emulator.calls == [("core/GetDeviceInfo", False)]


async def test_wrong_password(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """A wrong password yields InvalidAuthError or lock error."""
    client = make(session, emulator, "wrong-password")
    with pytest.raises(InvalidAuthError):
        await client.async_call(GET_DEVICES)
    emulator.locked = True
    with pytest.raises(AccountLockedError):
        await client.async_call(GET_DEVICES)


async def test_no_password_never_sends_auth(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """A transport without a password cannot authenticate."""
    client = make(session, emulator, None)
    with pytest.raises(InvalidAuthError):
        await client.async_call(GET_DEVICES)
    assert emulator.calls[-1][1] is False


async def test_headers_and_body_on_wire(session: aiohttp.ClientSession) -> None:
    """Verify headers, JSON body and Basic auth encoding."""
    seen: dict[str, Any] = {}

    async def handler(request: web.Request) -> web.Response:
        seen["headers"] = dict(request.headers)
        seen["body"] = await request.json()
        return web.json_response({"result": "OK", "output": {"k": "v"}})

    async with serve(handler) as server:
        client = JnapTransport(
            session, "127.0.0.1", server.port, use_ssl=False, password="pw"
        )
        out = await client.async_call(GET_INFO, {"a": 1})
        assert out == {"k": "v"}
        assert seen["body"] == {"a": 1}
        assert seen["headers"]["X-JNAP-Action"] == GET_INFO
        assert seen["headers"]["X-JNAP-Authorization"] == "Basic YWRtaW46cHc="
        assert seen["headers"]["Content-Type"].startswith("application/json")
        await client.async_call(GET_INFO, authenticated=False)
        assert "X-JNAP-Authorization" not in seen["headers"]
        assert seen["body"] == {}


# transaction framing --------------------------------------------------------


async def test_transaction_bare_list_body(session: aiohttp.ClientSession) -> None:
    """Transactions post a bare JSON list of {action, request}."""
    seen: dict[str, Any] = {}

    async def handler(request: web.Request) -> web.Response:
        seen["body"] = await request.json()
        seen["action"] = request.headers["X-JNAP-Action"]
        return web.json_response(
            {
                "result": "OK",
                "responses": [{"result": "OK", "output": {"n": i}} for i in range(2)],
            }
        )

    async with serve(handler) as server:
        client = JnapTransport(
            session, "127.0.0.1", server.port, use_ssl=False, password="pw"
        )
        res = await client.async_transaction(
            [JnapAction(WAN), JnapAction(GET_DEVICES, {"sinceRevision": 0})]
        )
    assert seen["action"] == ACTION_TRANSACTION
    assert seen["body"] == [
        {"action": WAN, "request": {}},
        {"action": GET_DEVICES, "request": {"sinceRevision": 0}},
    ]
    assert [r.output for r in res] == [{"n": 0}, {"n": 1}]


async def test_transaction_against_emulator(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """The emulator answers a transaction in order."""
    res = await make(session, emulator).async_transaction(
        [JnapAction(WAN), JnapAction(GET_DEVICES)]
    )
    assert all(r.ok for r in res)
    assert res[0].output["wanStatus"] == "Connected"


async def test_wrapped_body_rejected(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """A wrapped (dict) transaction body is rejected by the hub."""
    client = make(session, emulator)
    data = await client._post(
        ACTION_TRANSACTION,
        {"actions": [{"action": WAN, "request": {}}]},
        authenticated=True,
        host=None,
    )
    assert data == {"result": "_ErrorInvalidInput"}
    with pytest.raises(JnapResultError):
        tr._parse_transaction(data, 1)


async def test_transaction_responses_nested_under_output(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """responses nested under output is accepted."""
    raw(
        emulator,
        {
            "result": "OK",
            "output": {"responses": [{"result": "OK", "output": {"x": 1}}]},
        },
    )
    res = await make(session, emulator).async_transaction([JnapAction(WAN)])
    assert res == [JnapResponse("OK", {"x": 1})]


async def test_response_key_instead_of_result(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """A `response` key is accepted in place of `result`."""
    raw(emulator, {"response": "OK", "output": {"x": 1}})
    out = await make(session, emulator).async_call(WAN)
    assert out == {"x": 1}
    raw(
        emulator,
        {"response": "OK", "responses": [{"response": "OK", "output": {"y": 2}}]},
    )
    res = await make(session, emulator).async_transaction([JnapAction(WAN)])
    assert res[0].output == {"y": 2}


@pytest.mark.parametrize(
    "payload",
    [
        {"result": "OK"},
        {"result": "OK", "responses": []},
        {"result": "OK", "responses": "x"},
        {"result": "OK", "responses": [{"result": "OK"}, {"result": "OK"}]},
        {"result": "OK", "responses": ["x"]},
        {"result": "OK", "responses": [{"result": 5}]},
        {"result": "OK", "output": "x"},
        {"result": 5},
        {"nothing": 1},
        [{"result": "OK"}],
        "OK",
        None,
        5,
    ],
)
async def test_transaction_malformed(
    session: aiohttp.ClientSession, emulator: Emulator, payload: object
) -> None:
    """Malformed transaction payloads are UnsupportedDevice."""
    raw(emulator, payload)
    with pytest.raises(UnsupportedDeviceError):
        await make(session, emulator).async_transaction([JnapAction(WAN)])


async def test_transaction_non_ok_top_level_maps(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """A non-OK top-level transaction result maps via raise_for_result."""
    raw(emulator, {"result": "_ErrorUnauthorized", "responses": []})
    with pytest.raises(InvalidAuthError):
        await make(session, emulator).async_transaction([JnapAction(WAN)])


@pytest.mark.parametrize("payload", [[1], "x", 5, None, {"output": {}}, {"result": 1}])
async def test_call_malformed(
    session: aiohttp.ClientSession, emulator: Emulator, payload: object
) -> None:
    """Non-dict JSON or a missing result is UnsupportedDevice."""
    raw(emulator, payload)
    with pytest.raises(UnsupportedDeviceError):
        await make(session, emulator).async_call(WAN)


async def test_call_non_dict_output_becomes_empty(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """A non-dict output is treated as empty."""
    raw(emulator, {"result": "OK", "output": [1, 2]})
    assert await make(session, emulator).async_call(WAN) == {}


# HTTP-level failures --------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "exc"),
    [
        (401, InvalidAuthError),
        (403, InvalidAuthError),
        (404, UnsupportedDeviceError),
        (500, UnsupportedDeviceError),
        (204, UnsupportedDeviceError),
    ],
)
async def test_http_status(
    session: aiohttp.ClientSession,
    emulator: Emulator,
    status: int,
    exc: type[Exception],
) -> None:
    """HTTP statuses map to domain errors."""
    emulator.status_override = status
    with pytest.raises(exc):
        await make(session, emulator).async_call(WAN)


async def test_invalid_json(session: aiohttp.ClientSession, emulator: Emulator) -> None:
    """Invalid JSON is UnsupportedDevice."""
    emulator.raw_override = b"<html>not json</html>"
    with pytest.raises(UnsupportedDeviceError):
        await make(session, emulator).async_call(WAN)


async def test_oversized_body(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """Bodies over 2 MiB are rejected."""
    emulator.raw_override = (
        b'{"result":"OK","output":{"p":"' + b"a" * (tr.MAX_BODY_BYTES + 10) + b'"}}'
    )
    with pytest.raises(UnsupportedDeviceError):
        await make(session, emulator).async_call(WAN)


async def test_body_just_under_limit_ok(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """A large body below the limit is accepted."""
    pad = "a" * (tr.MAX_BODY_BYTES - 100)
    emulator.raw_override = json.dumps({"result": "OK", "output": {"p": pad}}).encode()
    out = await make(session, emulator).async_call(WAN)
    assert len(out["p"]) == len(pad)


async def test_timeout(session: aiohttp.ClientSession, emulator: Emulator) -> None:
    """A slow hub raises ConnectionTimeoutError (a CannotConnectError)."""
    emulator.delay = 1.0
    with (
        patch.object(tr, "TOTAL_TIMEOUT", 0.1),
        pytest.raises(ConnectionTimeoutError) as info,
    ):
        await make(session, emulator).async_call(WAN)
    assert isinstance(info.value, CannotConnectError)


async def test_connection_refused(session: aiohttp.ClientSession) -> None:
    """A closed port raises CannotConnectError."""
    async with serve(_ok_handler) as server:
        port = server.port
    client = JnapTransport(session, "127.0.0.1", port, use_ssl=False, password="x")
    with pytest.raises(CannotConnectError) as info:
        await client.async_call(WAN)
    assert not isinstance(info.value, ConnectionTimeoutError)


async def test_no_redirects_followed(session: aiohttp.ClientSession) -> None:
    """Redirects are not followed and count as unsupported."""
    hits: list[str] = []

    async def target(request: web.Request) -> web.Response:
        hits.append("target")
        return web.json_response({"result": "OK"})

    async def redirect(request: web.Request) -> web.Response:
        raise web.HTTPFound("/elsewhere")

    app = web.Application()
    app.router.add_post("/JNAP/", redirect)
    app.router.add_post("/elsewhere", target)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    try:
        client = JnapTransport(
            session, "127.0.0.1", server.port, use_ssl=False, password="x"
        )
        with pytest.raises(UnsupportedDeviceError):
            await client.async_call(WAN)
        assert hits == []
    finally:
        await server.close()


async def test_host_override(
    session: aiohttp.ClientSession, emulator: Emulator
) -> None:
    """host= redirects a single call to another address."""
    client = JnapTransport(
        session, "192.0.2.77", emulator.port, use_ssl=False, password=PASSWORD
    )
    out = await client.async_call(GET_INFO, authenticated=False, host="127.0.0.1")
    assert out["serialNumber"]


# misc -----------------------------------------------------------------------


def test_repr_hides_password(session: aiohttp.ClientSession) -> None:
    """repr and str never contain the password or its encoding."""
    secret = "example-password"  # noqa: S105
    client = JnapTransport(MagicMock(), "192.0.2.1", 80, use_ssl=True, password=secret)
    text = repr(client) + str(client)
    assert secret not in text
    assert "YWRtaW4" not in text
    assert "ssl=True" in text


@pytest.mark.parametrize(
    ("host", "ssl", "override", "expected"),
    [
        ("192.0.2.1", False, None, "http://192.0.2.1:80/JNAP/"),
        ("192.0.2.1", True, None, "https://192.0.2.1:80/JNAP/"),
        ("2001:db8::1", False, None, "http://[2001:db8::1]:80/JNAP/"),
        ("router.example", False, None, "http://router.example:80/JNAP/"),
        ("192.0.2.1", False, "2001:db8::2", "http://[2001:db8::2]:80/JNAP/"),
        ("192.0.2.1", False, "192.0.2.9", "http://192.0.2.9:80/JNAP/"),
    ],
)
def test_url(host: str, ssl: bool, override: str | None, expected: str) -> None:
    """_url builds scheme/bracketing correctly."""
    client = JnapTransport(MagicMock(), host, 80, use_ssl=ssl, password=None)
    assert client._url(override) == expected


# helpers --------------------------------------------------------------------


async def _ok_handler(request: web.Request) -> web.Response:
    return web.json_response({"result": "OK"})


class _Serve:
    def __init__(self, handler: Callable[[web.Request], Awaitable[web.Response]]):
        app = web.Application()
        app.router.add_post("/JNAP/", handler)
        self._server = TestServer(app, host="127.0.0.1")

    async def __aenter__(self) -> TestServer:
        await self._server.start_server()
        return self._server

    async def __aexit__(self, *exc: object) -> None:
        await self._server.close()


def serve(handler: Callable[[web.Request], Awaitable[web.Response]]) -> _Serve:
    """Serve one handler at /JNAP/ on loopback."""
    return _Serve(handler)

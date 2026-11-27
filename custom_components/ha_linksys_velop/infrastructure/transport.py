"""JNAP HTTP transport: framing, limits and result-code mapping."""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import aiohttp

from ..domain.errors import (
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

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_LOGGER = logging.getLogger(__name__)

JNAP_PATH = "/JNAP/"
JNAP_BASE = "http://linksys.com/jnap/"
ACTION_TRANSACTION = f"{JNAP_BASE}core/Transaction"
MAX_BODY_BYTES = 2 * 1024 * 1024
CONNECT_TIMEOUT = 5
TOTAL_TIMEOUT = 10
USERNAME = "admin"

RESULT_OK = "OK"
_AUTH_RESULTS = frozenset({"_ErrorUnauthorized", "ErrorInvalidAdminPassword"})
_LOCK_RESULTS = frozenset({"ErrorAdminAccountLocked", "ErrorPasswordCheckDelayed"})
_IN_PROGRESS_RESULT = "ErrorAutoChannelSelectionAlreadyInProgress"


@dataclass(frozen=True, slots=True)
class JnapAction:
    """One action inside a transaction."""

    action: str
    request: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class JnapResponse:
    """The result of one action."""

    result: str
    output: Mapping[str, Any]

    @property
    def ok(self) -> bool:
        """Return True when the action succeeded."""
        return self.result == RESULT_OK


def raise_for_result(result: str) -> None:
    """Map a non-OK JNAP result code to a domain error."""
    if result == RESULT_OK:
        return
    if result in _AUTH_RESULTS:
        raise InvalidAuthError
    if result in _LOCK_RESULTS:
        raise AccountLockedError
    if result == "_ErrorUnknownAction":
        raise UnknownActionError(result)
    if result == "ErrorDeviceNotInMasterMode":
        raise NotPrimaryNodeError
    if result == _IN_PROGRESS_RESULT:
        raise OperationInProgressError(result)
    raise JnapResultError(result)


class JnapTransport:
    """Send JNAP requests to one hub using a shared aiohttp session."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        *,
        use_ssl: bool,
        password: str | None,
    ) -> None:
        """Create a transport; the password is never logged or exposed."""
        self._session = session
        self._host = host
        self._port = port
        self._use_ssl = use_ssl
        self._auth = (
            "Basic " + base64.b64encode(f"{USERNAME}:{password}".encode()).decode()
            if password is not None
            else None
        )

    def __repr__(self) -> str:
        """Return a representation that excludes credentials."""
        return f"JnapTransport(port={self._port}, ssl={self._use_ssl})"

    def _url(self, host: str | None = None) -> str:
        target = host or self._host
        if ":" in target:
            target = f"[{target}]"
        scheme = "https" if self._use_ssl else "http"
        return f"{scheme}://{target}:{self._port}{JNAP_PATH}"

    async def _post(
        self,
        action: str,
        body: object,
        *,
        authenticated: bool,
        host: str | None,
    ) -> Any:
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "X-JNAP-Action": action,
        }
        if authenticated and self._auth is not None:
            headers["X-JNAP-Authorization"] = self._auth
        timeout = aiohttp.ClientTimeout(total=TOTAL_TIMEOUT, connect=CONNECT_TIMEOUT)
        try:
            async with self._session.post(
                self._url(host),
                json=body,
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
            ) as resp:
                if resp.status in (401, 403):
                    raise InvalidAuthError
                if resp.status != 200:
                    raise UnsupportedDeviceError
                raw = await _read_limited(resp)
        except TimeoutError as exc:
            raise ConnectionTimeoutError from exc
        except aiohttp.ClientError as exc:
            raise CannotConnectError from exc
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise UnsupportedDeviceError from exc

    async def async_call(
        self,
        action: str,
        request: Mapping[str, Any] | None = None,
        *,
        authenticated: bool = True,
        host: str | None = None,
    ) -> Mapping[str, Any]:
        """Run one action and return its output, raising on a non-OK result."""
        data = await self._post(
            action, dict(request or {}), authenticated=authenticated, host=host
        )
        response = _parse_single(data)
        raise_for_result(response.result)
        return response.output

    async def async_transaction(
        self, actions: Sequence[JnapAction], *, authenticated: bool = True
    ) -> list[JnapResponse]:
        """Run several actions in one request, returning one response each."""
        body = [{"action": a.action, "request": dict(a.request or {})} for a in actions]
        data = await self._post(
            ACTION_TRANSACTION, body, authenticated=authenticated, host=None
        )
        return _parse_transaction(data, len(actions))


async def _read_limited(resp: aiohttp.ClientResponse) -> bytes:
    """Read a response body, rejecting anything over the size limit."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in resp.content.iter_chunked(64 * 1024):
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise UnsupportedDeviceError
        chunks.append(chunk)
    return b"".join(chunks)


def _result_of(data: Mapping[str, Any]) -> str:
    result = data.get("result", data.get("response"))
    if not isinstance(result, str):
        raise UnsupportedDeviceError
    return result


def _output_of(data: Mapping[str, Any]) -> Mapping[str, Any]:
    output = data.get("output", {})
    return output if isinstance(output, dict) else {}


def _parse_single(data: object) -> JnapResponse:
    if not isinstance(data, dict):
        raise UnsupportedDeviceError
    return JnapResponse(_result_of(data), _output_of(data))


def _parse_transaction(data: object, expected: int) -> list[JnapResponse]:
    if not isinstance(data, dict):
        raise UnsupportedDeviceError
    result = _result_of(data)
    raw = data.get("responses")
    if raw is None and isinstance(data.get("output"), dict):
        raw = data["output"].get("responses")
    if not isinstance(raw, list):
        # No per-action results: the top-level code is the only signal.
        raise_for_result(result)
        raise UnsupportedDeviceError
    # The hub sets a generic top-level "Error" whenever any single action
    # fails, so the per-action results are authoritative.
    if len(raw) != expected:
        raise_for_result(result)
        raise UnsupportedDeviceError
    responses: list[JnapResponse] = []
    for item in raw:
        if not isinstance(item, dict):
            raise UnsupportedDeviceError
        responses.append(JnapResponse(_result_of(item), _output_of(item)))
    return responses

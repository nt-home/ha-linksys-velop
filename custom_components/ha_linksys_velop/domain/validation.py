"""Pure validation rules for user supplied hub addresses."""

from __future__ import annotations

import ipaddress
import re

from .errors import InvalidHostError

MIN_PORT = 1
MAX_PORT = 65535
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)
_METADATA_ADDRESS = ipaddress.ip_address("169.254.169.254")
# A final label made only of digits or a hex literal is read by the resolver as
# a shorthand IPv4 address (``127.1``, ``2130706433``, ``0x7f.1``).
_NUMERIC_TLD_RE = re.compile(r"^(0[xX][0-9A-Fa-f]*|[0-9]+)$")


def _is_forbidden(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    return (
        address.is_loopback
        or address.is_unspecified
        or address.is_multicast
        or address.is_link_local
        or address.is_reserved
        or address == _METADATA_ADDRESS
    )


def validate_host(raw: str) -> str:
    """Return a clean host name or address.

    Args:
        raw: The value typed by the user.

    Returns:
        The validated host without surrounding whitespace.

    Raises:
        InvalidHostError: If the value is a URL, contains forbidden parts, or is a
            loopback, unspecified, multicast or link-local address.
    """
    host = raw.strip()
    if not host:
        raise InvalidHostError("empty")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        lowered = host.lower().rstrip(".")
        if (
            not _HOSTNAME_RE.match(host)
            or lowered == "localhost"
            or lowered.endswith(".localhost")
            or _NUMERIC_TLD_RE.match(lowered.rsplit(".", 1)[-1])
        ):
            raise InvalidHostError("format") from None
        return host
    if getattr(address, "scope_id", None) or _is_forbidden(address):
        raise InvalidHostError("forbidden")
    return str(address)


def validate_node_address(raw: str | None) -> str | None:
    """Return a node address safe to receive the admin password, else None.

    The address comes from the hub's own answer, so it is untrusted: only a
    private IP literal is accepted, never a name or a public address.
    """
    if not raw:
        return None
    try:
        address = ipaddress.ip_address(raw.strip())
    except ValueError:
        return None
    if getattr(address, "scope_id", None) or _is_forbidden(address):
        return None
    return str(address) if address.is_private else None


def validate_port(port: int | None, *, use_ssl: bool) -> int:
    """Return the effective port, defaulting by transport when empty.

    Raises:
        InvalidHostError: If the port is outside 1-65535.
    """
    if port is None:
        return 443 if use_ssl else 80
    if not MIN_PORT <= port <= MAX_PORT:
        raise InvalidHostError("port")
    return port

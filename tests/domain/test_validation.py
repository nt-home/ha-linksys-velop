"""Tests for host and port validation."""

from __future__ import annotations

import pytest

from custom_components.ha_linksys_velop.domain.errors import InvalidHostError
from custom_components.ha_linksys_velop.domain.validation import (
    validate_host,
    validate_port,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("192.0.2.1", "192.0.2.1"),
        ("  192.0.2.1  ", "192.0.2.1"),
        ("10.0.0.1", "10.0.0.1"),
        ("2001:db8::1", "2001:db8::1"),
        ("[2001:db8::1]", "2001:db8::1"),
        ("2001:0DB8:0:0:0:0:0:1", "2001:db8::1"),
        ("router.example.com", "router.example.com"),
        ("velop", "velop"),
        ("My-Router.local", "My-Router.local"),
        ("a" * 63, "a" * 63),
    ],
)
def test_validate_host_accepts(raw: str, expected: str) -> None:
    """Valid hosts are returned normalised."""
    assert validate_host(raw) == expected


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("http://192.0.2.1", "format"),
        ("192.0.2.1/path", "format"),
        ("192.0.2.1:80", "format"),
        ("user@host", "format"),
        ("-bad.example.com", "format"),
        ("bad-.example.com", "format"),
        ("bad host", "format"),
        ("a" * 64, "format"),
        ("a" * 254, "format"),
        ("exa_mple.com", "format"),
        ("localhost", "format"),
        ("LOCALHOST", "format"),
        ("127.0.0.1", "forbidden"),
        ("::1", "forbidden"),
        ("0.0.0.0", "forbidden"),  # noqa: S104
        ("::", "forbidden"),
        ("224.0.0.1", "forbidden"),
        ("ff02::1", "forbidden"),
        ("169.254.1.1", "forbidden"),
        ("169.254.169.254", "forbidden"),
        ("fe80::1", "forbidden"),
    ],
)
def test_validate_host_rejects(raw: str, reason: str) -> None:
    """Invalid or dangerous hosts raise with a reason."""
    with pytest.raises(InvalidHostError, match=reason):
        validate_host(raw)


@pytest.mark.parametrize(
    ("port", "ssl", "expected"),
    [
        (None, False, 80),
        (None, True, 443),
        (1, False, 1),
        (65535, True, 65535),
        (8080, False, 8080),
    ],
)
def test_validate_port_ok(port: int | None, ssl: bool, expected: int) -> None:
    """Ports default by transport and accept the valid range."""
    assert validate_port(port, use_ssl=ssl) == expected


@pytest.mark.parametrize("port", [0, -1, 65536, 100000])
def test_validate_port_rejects(port: int) -> None:
    """Out-of-range ports raise."""
    with pytest.raises(InvalidHostError, match="port"):
        validate_port(port, use_ssl=False)

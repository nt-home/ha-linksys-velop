"""Tests for the plain-language duration text."""

from __future__ import annotations

import pytest

from custom_components.ha_linksys_velop.domain.duration import humanize_duration

DAY = 86400


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (-5, "0 seconds"),
        (0, "0 seconds"),
        (1, "1 second"),
        (45, "45 seconds"),
        (60, "1 minute"),
        (125, "2 minutes 5 seconds"),
        (3600, "1 hour"),
        (3 * 3600 + 5 * 60, "3 hours 5 minutes"),
        (7200, "2 hours"),
        (3 * DAY + 4 * 3600, "3 days 4 hours"),
        (7 * DAY, "1 week"),
        (40 * DAY, "1 month 1 week"),
        (365 * DAY + 65 * DAY, "1 year 2 months"),
        (2 * 365 * DAY, "2 years"),
        (59.9, "59 seconds"),
    ],
)
def test_humanize_duration(seconds: float, expected: str) -> None:
    """The unit follows the size of the value."""
    assert humanize_duration(seconds) == expected


def test_humanize_duration_parts() -> None:
    """The number of shown units can be widened."""
    assert humanize_duration(3 * DAY + 4 * 3600 + 5 * 60, parts=3) == (
        "3 days 4 hours 5 minutes"
    )
    assert humanize_duration(3 * DAY + 4 * 3600, parts=1) == "3 days"

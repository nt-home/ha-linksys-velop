"""Plain-language duration text that picks its unit from the size of the value."""

from __future__ import annotations

# Calendar-free approximations: a month is 30 days and a year 365 days.
_UNITS: tuple[tuple[str, int], ...] = (
    ("year", 365 * 86400),
    ("month", 30 * 86400),
    ("week", 7 * 86400),
    ("day", 86400),
    ("hour", 3600),
    ("minute", 60),
    ("second", 1),
)


def humanize_duration(seconds: float, *, parts: int = 2) -> str:
    """Return e.g. ``45 seconds``, ``3 hours 5 minutes`` or ``1 year 2 months``.

    The largest unit that fits comes first, followed by up to ``parts - 1``
    of the next smaller units that are not zero. Negative values read as zero.
    """
    remaining = max(int(seconds), 0)
    out: list[str] = []
    started = False
    for index, (name, size) in enumerate(_UNITS):
        count, remaining = divmod(remaining, size)
        if count:
            out.append(f"{count} {name}{'' if count == 1 else 's'}")
            started = True
        # After the first unit only the directly following unit is shown.
        if started and (len(out) >= parts or index == len(_UNITS) - 1):
            break
        if started and not count:
            break
    return " ".join(out) if out else "0 seconds"

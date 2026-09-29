"""Day types for ZKM Iława timetables (Roboczy / Sobota / Niedziela) and school days."""
from __future__ import annotations

import datetime as dt


def _easter(y: int) -> dt.date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(y, month, day + 1)


def public_holidays(y: int) -> set[dt.date]:
    e = _easter(y)
    fixed = [(1, 1), (1, 6), (5, 1), (5, 3), (8, 15), (11, 1), (11, 11), (12, 24), (12, 25), (12, 26)]
    days = {dt.date(y, mo, d) for mo, d in fixed}
    days |= {e, e + dt.timedelta(days=1), e + dt.timedelta(days=49), e + dt.timedelta(days=60)}
    return days


def day_type(d: dt.date) -> str:
    if d in public_holidays(d.year) or d.weekday() == 6:
        return "Niedziela"
    if d.weekday() == 5:
        return "Sobota"
    return "Roboczy"


def is_school_day(d: dt.date) -> bool:
    """Approximate: Monday-Friday, not a public holiday, 1 Sep - 26 Jun, no summer break."""
    if day_type(d) != "Roboczy":
        return False
    return not (dt.date(d.year, 6, 27) <= d <= dt.date(d.year, 8, 31))

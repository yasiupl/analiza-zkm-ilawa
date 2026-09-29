"""Day counts per ZKM day type in a year."""
from __future__ import annotations

import datetime as dt

from ..calendar import day_type, is_school_day


def day_counts(year: int) -> dict[str, int]:
    n = {"Roboczy": 0, "Sobota": 0, "Niedziela": 0, "Roboczy szkolny": 0}
    d = dt.date(year, 1, 1)
    while d.year == year:
        t = day_type(d)
        n[t] += 1
        if t == "Roboczy" and is_school_day(d):
            n["Roboczy szkolny"] += 1
        d += dt.timedelta(days=1)
    return n

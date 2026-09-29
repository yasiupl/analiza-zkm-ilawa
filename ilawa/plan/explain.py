"""What each proposed change gives: good transfers per day and the trains concerned.

The contribution of one change is the number of good transfers (train, line) per
day that the proposal loses when only this trip goes back to its current times.
The trains are the ones whose transfer to or from this line becomes good (or
stops being good) on the planning date. Train arrivals include the delay buffer
of their category, as in the optimiser.

A change with no contribution of its own keeps another change possible: a driver
break, the layover of the bus or the order of the trips of the line.
"""
from __future__ import annotations

import numpy as np

from .. import model as M
from .optimize import count_good, shape, station_times
from .timetable import Trip


def good_pairs(trips: list[Trip], tarr: list[dict], tdep: list[dict], station: set, m1: float, m2: float,
               buffers: dict | None = None, good: float = M.GOOD) -> set:
    """Set of good (direction, train index, line) on one date."""
    buffers = buffers or {}
    dep_by, arr_by = {}, {}
    for t in trips:
        arr, dep = station_times(t, shape(t, station))
        if dep is not None:
            dep_by.setdefault(t.route, []).append(dep)
        if arr is not None:
            arr_by.setdefault(t.route, []).append(arr)
    out = set()
    a = np.array([x["t"] + buffers.get(x["cat"], 0.0) for x in tarr], float)
    b = np.array([x["t"] for x in tdep], float)
    for r, d in dep_by.items():
        if len(a):
            g = M.train_to_bus(a, np.sort(np.array(d, float)), m1, good)["good"]
            out |= {("t2b", i, r) for i in np.flatnonzero(g)}
    for r, d in arr_by.items():
        if len(b):
            g = M.bus_to_train(b, np.sort(np.array(d, float)), m2, good)["good"]
            out |= {("b2t", i, r) for i in np.flatnonzero(g)}
    return out


def _label(x: dict) -> str:
    return f"{x['cat']} {x['no']} ({x['time']})"


def contributions(orig: list[Trip], new: list[Trip], changes: list[dict], trains_by_date: dict, rep,
                  station: set, m1: float, m2: float, good: float, buffers: dict | None = None) -> None:
    """Add 'gain_per_day', 'gains' and 'losses' to each change (in place)."""
    by_id = {t.id: i for i, t in enumerate(new)}
    orig_by_id = {t.id: t for t in orig}
    nd = max(1, len(trains_by_date))
    full = count_good(new, trains_by_date, station, m1, m2, good, buffers)["total"] / nd
    tarr, tdep = trains_by_date[rep]
    s_full = good_pairs(new, tarr, tdep, station, m1, m2, buffers, good)
    for c in changes:
        k = by_id[c["trip"]]
        back = list(new)
        back[k] = orig_by_id[c["trip"]]
        c["gain_per_day"] = round(full - count_good(back, trains_by_date, station, m1, m2, good, buffers)["total"] / nd, 2)
        s_back = good_pairs(back, tarr, tdep, station, m1, m2, buffers, good)
        name = lambda p: ("z " if p[0] == "t2b" else "na ") + _label((tarr if p[0] == "t2b" else tdep)[p[1]])
        c["gains"] = [name(p) for p in sorted(s_full - s_back, key=lambda p: (p[0], p[1])) if p[2] == c["route"]]
        c["losses"] = [name(p) for p in sorted(s_back - s_full, key=lambda p: (p[0], p[1])) if p[2] == c["route"]]

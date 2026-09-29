"""Extra trips that fill gaps with buses and drivers that already wait at the station.

A bus that waits at the station longer than one round trip of its line (plus
two minimum layovers) can run one extra round trip without a new vehicle and
without a new duty. The marginal cost is the energy and maintenance of the extra
kilometres. The value is the number of new good transfers (train, line) that the
extra departure and the extra arrival give, and the gap in the line timetable
that the trip fills.
"""
from __future__ import annotations

import numpy as np

from .. import model as M
from .blocks import Block
from .optimize import shape, station_times
from .timetable import Feed, hhmm


def round_trips(blocks: list[Block], station: set) -> dict[str, dict]:
    """Typical round trip of each line from the station back to the station."""
    out: dict[str, list] = {}
    for b in blocks:
        for i, t in enumerate(b.trips):
            if shape(t, station).kind != "start":
                continue
            for u in b.trips[i + 1:i + 3]:
                if shape(u, station).kind == "end" and u.route == t.route:
                    out.setdefault(t.route, []).append((t, u))
                    break
    res = {}
    for r, pairs in out.items():
        durs = [u.end - t.start for t, u in pairs]
        med = float(np.median(durs))
        t, u = min(pairs, key=lambda x: abs((x[1].end - x[0].start) - med))
        res[r] = {"duration": med, "out": t, "back": u, "n": len(pairs)}
    return res


def candidates(feed: Feed, blocks: list[Block], station: set, trains: tuple[list, list], m1: float, m2: float,
               good: float, min_layover: int, road_factor: float, unit_km_cost: float,
               min_gap_in_line: int = 40) -> list[dict]:
    tarr, tdep = trains
    ta = np.array([x["t"] for x in tarr])
    td = np.array([x["t"] for x in tdep])
    rts = round_trips(blocks, station)
    dep_by, arr_by = {}, {}
    for b in blocks:
        for t in b.trips:
            arr, dep = station_times(t, shape(t, station))
            if dep is not None:
                dep_by.setdefault(t.route, []).append(dep)
            if arr is not None:
                arr_by.setdefault(t.route, []).append(arr)
    out = []
    for b in blocks:
        for i, j in zip(b.trips, b.trips[1:]):
            if not (i.last in station and j.first in station):
                continue
            idle = j.start - i.end
            for r, rt in rts.items():
                dur = rt["duration"]
                if idle < dur + 2 * min_layover or r not in (i.route, j.route):
                    continue
                deps = np.sort(np.array(dep_by.get(r, []), float))
                arrs = np.sort(np.array(arr_by.get(r, []), float))
                g0 = int(M.train_to_bus(ta, deps, m1)["good"].sum()) if len(deps) and len(ta) else 0
                h0 = int(M.bus_to_train(td, arrs, m2)["good"].sum()) if len(arrs) and len(td) else 0
                best = None
                for start in range(int(i.end + min_layover), int(j.start - dur - min_layover) + 1):
                    back = start + dur
                    g1 = int(M.train_to_bus(ta, np.sort(np.append(deps, start)), m1)["good"].sum()) if len(ta) else 0
                    h1 = int(M.bus_to_train(td, np.sort(np.append(arrs, back)), m2)["good"].sum()) if len(td) else 0
                    gain = (g1 - g0) + (h1 - h0)
                    prev = deps[deps < start].max() if (deps < start).any() else None
                    nxt = deps[deps > start].min() if (deps > start).any() else None
                    gap = (nxt - prev) if prev is not None and nxt is not None else None
                    key = (gain, gap or 0)
                    if best is None or key > best[0]:
                        best = (key, start, back, gain, gap, prev, nxt)
                if best is None or best[3] <= 0:
                    continue
                _, start, back, gain, gap, prev, nxt = best
                km = feed.trip_km(rt["out"], road_factor) + feed.trip_km(rt["back"], road_factor)
                out.append({"block": b.id, "line": r, "idle_from": hhmm(i.end), "idle_to": hhmm(j.start),
                            "idle_min": idle, "depart": hhmm(start), "return": hhmm(back), "new_transfers": gain,
                            "line_gap_min": gap, "prev_dep": hhmm(prev) if prev is not None else None,
                            "next_dep": hhmm(nxt) if nxt is not None else None, "km": km,
                            "marginal_cost": km * unit_km_cost,
                            "fills_gap": bool(gap and gap >= min_gap_in_line)})
    best = {}
    for c in out:  # one proposal per idle window: the best line
        k = (c["block"], c["idle_from"])
        if k not in best or (c["new_transfers"], c["fills_gap"]) > (best[k]["new_transfers"], best[k]["fills_gap"]):
            best[k] = c
    return sorted(best.values(), key=lambda c: (-c["new_transfers"], c["marginal_cost"]))

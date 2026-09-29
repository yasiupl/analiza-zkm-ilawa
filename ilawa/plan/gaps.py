"""Extra trips that fill gaps with buses and drivers that already wait at the station.

A bus that waits at the station longer than one round trip of its line (plus
two minimum layovers) can run one extra round trip without a new vehicle.
- When the wait is inside one driver duty, the driver is already paid. The trip
  is accepted only when the duty still has the break that the law requires.
- When a duty ends at the start of the wait, the first driver runs the extra trip:
  the duty becomes longer (paid driver time) and must stay within the longest duty.
The marginal cost is the energy and maintenance of the extra kilometres plus the
extra driver time. The value is the number of new good transfers (train, line)
that the extra departure and the extra arrival give, and the gap in the line
timetable that the trip fills. One candidate is kept for each gap of a line.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from .. import model as M
from .blocks import Block
from .duties import Duty, check_breaks
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


def _duty_with(d: Duty, after, start: int, back: int) -> Duty:
    """Copy of duty d with an extra trip start..back after the trip `after`."""
    trips, dh = list(d.trips), list(d.dh or [0] * (len(d.trips) - 1))
    k = trips.index(after)
    extra = SimpleNamespace(start=start, end=back)
    trips.insert(k + 1, extra)
    if k < len(dh):
        dh.insert(k, 0)
    else:
        dh.append(0)
    return Duty(d.block, trips, d.prep, dh)


def candidates(feed: Feed, blocks: list[Block], station: set, trains: tuple[list, list], m1: float, m2: float,
               good: float, min_layover: int, road_factor: float, unit_km_cost: float,
               min_gap_in_line: int = 40, duties: list[Duty] | None = None, driver_pln_per_hour: float = 0.0,
               overhead_share: float = 0.0, max_duty: int = 510, rules: dict | None = None) -> list[dict]:
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
    rules = rules or {}
    duty_of = {id(t): d for d in (duties or []) for t in d.trips}
    out = []
    for b in blocks:
        for i, j in zip(b.trips, b.trips[1:]):
            if not (i.last in station and j.first in station):
                continue
            idle = j.start - i.end
            d = duty_of.get(id(i))
            for r, rt in rts.items():
                dur = rt["duration"]
                if idle < dur + 2 * min_layover or r not in (i.route, j.route):
                    continue
                deps = np.sort(np.array(dep_by.get(r, []), float))
                arrs = np.sort(np.array(arr_by.get(r, []), float))
                g0 = int(M.train_to_bus(ta, deps, m1, good)["good"].sum()) if len(deps) and len(ta) else 0
                h0 = int(M.bus_to_train(td, arrs, m2, good)["good"].sum()) if len(arrs) and len(td) else 0
                best = None
                for start in range(int(i.end + min_layover), int(j.start - dur - min_layover) + 1):
                    back = start + dur
                    g1 = int(M.train_to_bus(ta, np.sort(np.append(deps, start)), m1, good)["good"].sum()) if len(ta) else 0
                    h1 = int(M.bus_to_train(td, np.sort(np.append(arrs, back)), m2, good)["good"].sum()) if len(td) else 0
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
                inside, extra_driver = True, 0.0
                if d is not None:
                    nd = _duty_with(d, i, start, back)
                    inside = j in d.trips
                    if not inside:                       # the duty of the first driver gets longer
                        extra_driver = (back - i.end) / 60
                        if nd.length > max_duty:
                            continue
                    chk = check_breaks(nd, **rules)
                    if not (chk["ok_breaks"] and chk["ok_driving"]):
                        continue
                km = feed.trip_km(rt["out"], road_factor) + feed.trip_km(rt["back"], road_factor)
                cost = (km * unit_km_cost + extra_driver * driver_pln_per_hour) * (1 + overhead_share)
                out.append({"block": b.id, "line": r, "idle_from": hhmm(i.end), "idle_to": hhmm(j.start),
                            "idle_min": idle, "depart": hhmm(start), "return": hhmm(back), "new_transfers": gain,
                            "line_gap_min": gap, "prev_dep": hhmm(prev) if prev is not None else None,
                            "next_dep": hhmm(nxt) if nxt is not None else None, "km": km,
                            "marginal_cost": cost, "extra_driver_h": extra_driver, "inside_duty": inside,
                            "fills_gap": bool(gap and gap >= min_gap_in_line)})
    # One candidate per block idle window (the best line), then one per gap of a line.
    rank = lambda c: (c["new_transfers"], c["fills_gap"], -c["marginal_cost"])
    best = {}
    for c in out:
        k = (c["block"], c["idle_from"])
        if k not in best or rank(c) > rank(best[k]):
            best[k] = c
    per_gap = {}
    for c in best.values():
        k = (c["line"], c["prev_dep"], c["next_dep"])
        if k not in per_gap or rank(c) > rank(per_gap[k]):
            per_gap[k] = c
    return sorted(per_gap.values(), key=lambda c: (-c["new_transfers"], c["marginal_cost"]))

"""Shift ZKM trips by a few minutes so that more trains get a good bus connection.

Model (mixed integer program, solved with CBC through PuLP):
- Each trip gets one shift for its part before the station and one for its part
  after the station (a trip that starts or ends at the station has one part).
  A shift is a whole number of minutes in [-max_shift, +max_shift].
- A trip through the station may wait longer there: the part after the station
  moves at least as much as the part before it, and at most max_extra_dwell more.
- Vehicle blocks stay as they are: the layover between two trips of one bus stays
  at least min_layover. Layovers that are driver breaks (>= break_part minutes)
  stay breaks, and each duty keeps at least the break time the law requires.
- The first departure and the last arrival of each block and of each duty may not
  move so that the total vehicle time or the total driver time grows (the cost
  does not increase).
- Trips of one line keep their order at the station.
- Optionally (direction = "buffer") a bus may only arrive at the station earlier
  and leave it later: each change gives the transfer more time, never less.
- Objective: the mean number of good transfers (train, line) per day over the chosen
  dates, minus a penalty for each changed trip, for each minute of change at other
  stops and for each minute of extra dwell. A trip moves only when this pays.
  An optional limit on the number of changed trips gives the best proposal with
  at most that many changes (use it to draw the gain curve).
A good transfer: the bus leaves the station m_t2b + buffer .. m_t2b + buffer + good
minutes after the train arrives, or arrives m_b2t + good .. m_b2t minutes before
the train leaves. The buffer covers the usual delay of the train category.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

import numpy as np

from .blocks import Block
from .duties import Duty, check_breaks
from .timetable import Trip


@dataclass
class Params:
    station_stops: set
    m_t2b: float = 4
    m_b2t: float = 5
    good: float = 10
    max_shift: int = 5
    school_max_shift: int = 2
    max_extra_dwell: int = 10
    min_layover: int = 2
    habit_penalty: float = 0.02
    trip_change_penalty: float = 0.25
    dwell_penalty: float = 0.005
    break_part: int = 15
    need_6h: int = 30
    need_8h: int = 45
    buffers: dict = field(default_factory=dict)
    keep_cost: bool = True
    max_changes: int | None = None     # largest number of changed trips (None = no limit)
    fixed: dict = field(default_factory=dict)   # trip id -> (a, b): shifts kept from an earlier stage
    time_limit: int = 120
    direction: str = "any"            # any | buffer (arrivals only earlier, departures only later)


@dataclass
class TripShape:
    kind: str            # none | start | end | through | loop
    i_arr: int | None    # index of the arrival at the station
    i_dep: int | None    # index of the departure from the station


def shape(t: Trip, station: set) -> TripShape:
    idx = [i for i, (s, _) in enumerate(t.stops) if s in station]
    n = len(t.stops)
    if not idx:
        return TripShape("none", None, None)
    i_arr, i_dep = idx[0], idx[-1]
    if i_arr == 0 and i_dep == n - 1:
        return TripShape("loop", n - 1, 0)
    if i_arr == 0:
        return TripShape("start", None, i_dep)
    if i_dep == n - 1:
        return TripShape("end", i_arr, None)
    return TripShape("through", i_arr, i_dep)


def station_times(t: Trip, sh: TripShape) -> tuple[int | None, int | None]:
    arr = t.stops[sh.i_arr][1] if sh.i_arr is not None else None
    dep = t.stops[sh.i_dep][1] if sh.i_dep is not None else None
    return arr, dep


def apply_shifts(t: Trip, sh: TripShape, a: int, b: int) -> Trip:
    """New trip with shift a before the station and b from the departure on."""
    n = copy.copy(t)
    if sh.kind in ("none", "loop", "end"):
        n.stops = [(s, m + a) for s, m in t.stops]
    elif sh.kind == "start":
        n.stops = [(s, m + b) for s, m in t.stops]
    else:
        n.stops = [(s, m + (a if i <= sh.i_arr else b)) for i, (s, m) in enumerate(t.stops)]
    return n


def optimise(trips: list[Trip], blocks: list[Block], duties: list[Duty], trains_by_date: dict,
             p: Params, log=print) -> dict:
    import pulp

    prob = pulp.LpProblem("zkm_transfers", pulp.LpMaximize)
    shapes = {t.id: shape(t, p.station_stops) for t in trips}
    X: dict = {}

    def var(tid: str, part: str, lim: int):
        key = (tid, part)
        if key not in X:
            X[key] = {v: pulp.LpVariable(f"x_{len(X)}_{v + lim}", cat="Binary") for v in range(-lim, lim + 1)}
            prob.addConstraint(pulp.lpSum(X[key].values()) == 1)
        return X[key]

    def val(d):
        return pulp.lpSum(v * x for v, x in d.items())

    parts, penalty, changed = {}, [], []
    for t in trips:
        sh = shapes[t.id]
        lim = p.school_max_shift if "s" in (t.flags or "") else p.max_shift
        if sh.kind == "through":
            A, B = var(t.id, "a", lim), var(t.id, "b", lim)
            prob += val(B) - val(A) >= 0
            prob += val(B) - val(A) <= p.max_extra_dwell
            penalty.append(p.dwell_penalty * (val(B) - val(A)))
            penalty.append(p.habit_penalty * pulp.lpSum(abs(v) * x for v, x in A.items()))
            penalty.append(p.habit_penalty * pulp.lpSum(abs(v) * x for v, x in B.items()))
        else:
            A = B = var(t.id, "a", lim)
            penalty.append(p.habit_penalty * pulp.lpSum(abs(v) * x for v, x in A.items()))
        if sh.kind == "through":                 # changed when either part moves
            z = pulp.LpVariable(f"z_{len(changed)}", 0, 1)
            prob += z >= 1 - A[0]
            prob += z >= 1 - B[0]
        else:
            z = 1 - A[0]
        changed.append(z)
        penalty.append(p.trip_change_penalty * z)
        parts[t.id] = (A, B)
        if p.direction == "buffer" and sh.kind != "none":
            # The transfer gets more time, never less: the bus arrives at the
            # station earlier (or at the same time) and leaves later (or at the same time).
            bad = []
            if sh.kind in ("end", "through", "loop"):
                bad += [x for v, x in A.items() if v > 0]
            if sh.kind in ("start", "through", "loop"):
                bad += [x for v, x in B.items() if v < 0]
            for x in bad:
                prob += x == 0
        if t.id in p.fixed:
            fa, fb = p.fixed[t.id]
            prob += A[fa] == 1
            prob += B[fb] == 1
    if p.max_changes is not None:
        prob += pulp.lpSum(changed) <= p.max_changes

    def s_start(t):
        return parts[t.id][0]

    def s_end(t):
        return parts[t.id][1]

    for b in blocks:  # layovers of each bus (plus the empty run, if any)
        req = b.req or [p.min_layover] * (len(b.trips) - 1)
        for (i, j), r in zip(zip(b.trips, b.trips[1:]), req):
            cur = j.start - i.end
            prob += val(s_start(j)) - val(s_end(i)) >= min(r, cur) - cur
    for d in duties:  # driver breaks (rest time = gap minus empty run)
        chk = check_breaks(d, p.break_part, p.need_6h, p.need_8h)
        dh = d.dh or [0] * (len(d.trips) - 1)
        brk = [(i, j, j.start - i.end - x) for (i, j), x in zip(zip(d.trips, d.trips[1:]), dh)
               if j.start - i.end - x >= p.break_part]
        for i, j, c in brk:
            prob += val(s_start(j)) - val(s_end(i)) >= p.break_part - c
        if chk["break_needed"] and brk:
            need = min(chk["break_needed"], chk["break_total"])
            prob += pulp.lpSum(c + val(s_start(j)) - val(s_end(i)) for i, j, c in brk) >= need
    if p.keep_cost:  # total vehicle time and total driver time do not grow
        prob += pulp.lpSum(val(s_end(b.trips[-1])) - val(s_start(b.trips[0])) for b in blocks) <= 0
        prob += pulp.lpSum(val(s_end(d.trips[-1])) - val(s_start(d.trips[0])) for d in duties) <= 0
    by_dep, by_arr = {}, {}
    for t in trips:
        arr, dep = station_times(t, shapes[t.id])
        if dep is not None:
            by_dep.setdefault((t.route, t.direction), []).append((dep, t))
        if arr is not None:
            by_arr.setdefault((t.route, t.direction), []).append((arr, t))
    for group, which in ((by_dep, 1), (by_arr, 0)):  # order of trips at the station
        for lst in group.values():
            lst.sort(key=lambda x: x[0])
            for (t1, a1), (t2, a2) in zip(lst, lst[1:]):
                if t2 - t1 >= 1:
                    prob += val(parts[a2.id][which]) - val(parts[a1.id][which]) >= 1 - (t2 - t1)

    t2b_w, b2t_w = {}, {}
    nd = max(1, len(trains_by_date))           # weights: good transfers per day
    for d, (tarr, tdep) in trains_by_date.items():
        for tr in tarr:
            k = round(tr["t"] + p.buffers.get(tr["cat"], 0.0), 1)
            t2b_w[k] = t2b_w.get(k, 0) + 1 / nd
        for tr in tdep:
            k = round(tr["t"], 1)
            b2t_w[k] = b2t_w.get(k, 0) + 1 / nd
    routes = sorted({t.route for t in trips})
    dep_list, arr_list = {r: [] for r in routes}, {r: [] for r in routes}
    for t in trips:
        arr, dep = station_times(t, shapes[t.id])
        if dep is not None:
            dep_list[t.route].append((dep, parts[t.id][1]))
        if arr is not None:
            arr_list[t.route].append((arr, parts[t.id][0]))
    objective, n_y = [], 0
    for ta, w in t2b_w.items():
        lo, hi = ta + p.m_t2b, ta + p.m_t2b + p.good
        for r in routes:
            terms = [x for dep, d in dep_list[r] for v, x in d.items() if lo <= dep + v <= hi]
            if terms:
                y = pulp.LpVariable(f"y{n_y}", 0, 1)
                n_y += 1
                prob += y <= pulp.lpSum(terms)
                objective.append(w * y)
    for td, w in b2t_w.items():
        lo, hi = td - p.m_b2t - p.good, td - p.m_b2t
        for r in routes:
            terms = [x for arr, d in arr_list[r] for v, x in d.items() if lo <= arr + v <= hi]
            if terms:
                y = pulp.LpVariable(f"y{n_y}", 0, 1)
                n_y += 1
                prob += y <= pulp.lpSum(terms)
                objective.append(w * y)
    prob += pulp.lpSum(objective) - pulp.lpSum(penalty)
    log(f"  model: {len(X)} shift variables, {n_y} transfer terms, {len(prob.constraints)} constraints")
    status = prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=p.time_limit))

    def chosen(d):
        return max(d.items(), key=lambda kv: kv[1].value() or 0)[0]

    new_trips, changes, shifts = [], [], {}
    for t in trips:
        A, B = parts[t.id]
        a, b = chosen(A), chosen(B)
        sh = shapes[t.id]
        nt = apply_shifts(t, sh, a, b)
        new_trips.append(nt)
        shifts[t.id] = (a, b)
        if a or b:
            arr0, dep0 = station_times(t, sh)
            arr1, dep1 = station_times(nt, sh)
            changes.append({"trip": t.id, "route": t.route, "headsign": t.headsign, "kind": sh.kind,
                            "shift_before": a if sh.kind != "start" else 0,
                            "shift_after": b if sh.kind in ("through", "start") else a,
                            "station_arr": (arr0, arr1), "station_dep": (dep0, dep1),
                            "first_stop": (t.stops[0][1], nt.stops[0][1]), "last_stop": (t.stops[-1][1], nt.stops[-1][1])})
    return {"status": pulp.LpStatus[status], "objective": pulp.value(prob.objective), "trips": new_trips,
            "changes": changes, "shifts": {k: v for k, v in shifts.items() if v != (0, 0)}}


def optimise_stages(trips, blocks, duties, trains_by_date, p: Params, stages: list[int], log=print) -> dict:
    """Solve in stages that build on each other.

    Stage k may change at most stages[k] trips and keeps every change of the stages
    before it, so the operator can bring in the changes step by step. The last stage
    has no limit (other than the penalties). Each change gets the number of the
    stage that brings it in.
    """
    import dataclasses
    fixed, first, curve, res = {}, {}, [], None
    limits = [k for k in sorted(set(stages)) if k > 0] + [None]
    for n, k in enumerate(limits, 1):
        q = dataclasses.replace(p, max_changes=k, fixed=dict(fixed))
        res = optimise(trips, blocks, duties, trains_by_date, q, log=log if n == 1 else (lambda *a: None))
        for tid, v in res["shifts"].items():
            fixed[tid] = v
            first.setdefault(tid, n)
        curve.append({"stage": n, "limit": k, "changes": len(res["changes"]), "status": res["status"],
                      "trips": res["trips"]})
        if k is not None and len(res["changes"]) < k:
            break                               # the limit does not bind: later stages add nothing
    for c in res["changes"]:
        c["stage"] = first.get(c["trip"], len(curve))
    res["stages"] = curve
    return res


def rebuild_blocks(blocks: list[Block], new_trips: list[Trip]) -> list[Block]:
    by_id = {t.id: t for t in new_trips}
    return [Block(b.id, [by_id[t.id] for t in b.trips], list(b.dh), list(b.req)) for b in blocks]


def rebuild_duties(duties: list[Duty], new_trips: list[Trip]) -> list[Duty]:
    by_id = {t.id: t for t in new_trips}
    return [Duty(d.block, [by_id[t.id] for t in d.trips], d.prep, list(d.dh)) for d in duties]


def count_good(trips: list[Trip], trains_by_date: dict, station: set, m1: float, m2: float, good: float,
               buffers: dict | None = None) -> dict:
    """Good transfers (train, line) per date and in total."""
    from .. import model as M
    buffers = buffers or {}
    dep_by, arr_by = {}, {}
    for t in trips:
        arr, dep = station_times(t, shape(t, station))
        if dep is not None:
            dep_by.setdefault(t.route, []).append(dep)
        if arr is not None:
            arr_by.setdefault(t.route, []).append(arr)
    tot = {"t2b": 0, "b2t": 0, "pairs_t2b": 0, "pairs_b2t": 0}
    per_date = {}
    for d, (tarr, tdep) in trains_by_date.items():
        a = np.array([x["t"] + buffers.get(x["cat"], 0.0) for x in tarr])
        b = np.array([x["t"] for x in tdep])
        g1 = sum(int(M.train_to_bus(a, np.sort(bd), m1, good)["good"].sum()) for bd in dep_by.values()) if len(a) else 0
        g2 = sum(int(M.bus_to_train(b, np.sort(ba), m2, good)["good"].sum()) for ba in arr_by.values()) if len(b) else 0
        per_date[str(d)] = {"t2b": g1, "b2t": g2}
        tot["t2b"] += g1
        tot["b2t"] += g2
        tot["pairs_t2b"] += len(a) * len(dep_by)
        tot["pairs_b2t"] += len(b) * len(arr_by)
    tot["per_date"] = per_date
    tot["total"] = tot["t2b"] + tot["b2t"]
    tot["n_dates"] = len(per_date)
    return tot

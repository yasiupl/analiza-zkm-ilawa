"""Test of a bus timetable on past days with the actual times of the trains.

For each past day with delay data, each train at the station gets its actual
arrival and departure: from the PKP PLK monitor (all carriers) when it has the
train, else from the PKP Intercity history (spoznienia.me), else the planned
time (the train counts as punctual; the source is recorded). The bus times are
the planned times of the timetable under test (there are no data on actual bus
times).

A transfer (train, line) is good when the wait after the walk is not longer
than the good window:
- train -> bus: the bus leaves between arrival + m1 and arrival + m1 + window;
- bus -> train: the bus arrives between departure - m2 - window and departure - m2.
A transfer is caught when the wait after the walk is not longer than the good
window plus the delay buffer of the train category. With the planned times this
is the target of the optimiser; with the actual times it shows how many
passengers get the bus (or the train) that the timetable gives them.
The mean wait is the wait after the walk for the next bus of the line (train ->
bus), at most wait_cap minutes.
"""
from __future__ import annotations

import collections
import datetime as dt

import numpy as np
import pandas as pd

from .. import model as M
from ..calendar import day_type
from .trains import station_trains


def delay_table(ic: pd.DataFrame, mon: pd.DataFrame) -> dict:
    """(date, train number) -> {"arr": min, "dep": min, "src": ...}; the monitor wins."""
    out: dict = {}
    for src, df in (("spoznienia.me", ic), ("PKP PLK", mon)):
        if df is None or df.empty:
            continue
        for r in df.itertuples():
            for kind, dcol, col in (("arr", "date_arr", "arr_delay"), ("dep", "date_dep", "dep_delay")):
                d = getattr(r, dcol, None) if hasattr(r, dcol) else getattr(r, "date_arr", None)
                v = getattr(r, col, None)
                if d is None or v is None or pd.isna(v):
                    continue
                key = (str(d)[:10], str(r.number))
                out.setdefault(key, {})[kind] = float(v)
                out[key].setdefault("src", src)
                if src == "PKP PLK":
                    out[key]["src"] = src
    return out


def actual_trains(tr: pd.DataFrame, d: dt.date, delays: dict) -> tuple[list[dict], list[dict]]:
    """Trains at the station on date d with planned ('t') and actual ('act') minutes."""
    arr, dep = station_trains(tr, d)
    for lst, kind in ((arr, "arr"), (dep, "dep")):
        for x in lst:
            dl = delays.get((str(d), x["no"]), {})
            x["delay"] = dl.get(kind)
            x["src"] = dl.get("src", "plan") if kind in dl else "plan"
            x["act"] = x["t"] + (x["delay"] or 0.0)
    return arr, dep


def _events(bus: list[dict]) -> tuple[dict, dict]:
    dep, arr = collections.defaultdict(list), collections.defaultdict(list)
    for b in bus:
        if b.get("dep") is not None:
            dep[b["route"]].append(b["dep"])
        if b.get("arr") is not None:
            arr[b["route"]].append(b["arr"])
    return ({k: np.sort(np.array(v, float)) for k, v in dep.items()},
            {k: np.sort(np.array(v, float)) for k, v in arr.items()})


def count(bus: list[dict], arr: list[dict], dep: list[dict], m1: float, m2: float, window: float,
          key: str, buffers: dict | None = None, wait_cap: float = 60.0) -> dict:
    """Good and caught transfers (train, line) with train times from field `key` ('t' or 'act')."""
    buffers = buffers or {}
    d_by, a_by = _events(bus)
    a = np.array([x[key] for x in arr], float)
    ab = np.array([buffers.get(x["cat"], 0.0) for x in arr], float)
    t = np.array([x[key] for x in dep], float)
    tb = np.array([buffers.get(x["cat"], 0.0) for x in dep], float)
    out = {"t2b": 0, "b2t": 0, "caught_t2b": 0, "caught_b2t": 0, "pairs_t2b": 0, "pairs_b2t": 0}
    waits = []
    if len(a):
        for v in d_by.values():
            r = M.train_to_bus(a, v, m1, window)
            w = r["wait"] - m1
            out["t2b"] += int(r["good"].sum())
            out["caught_t2b"] += int(np.sum(np.nan_to_num(w, nan=np.inf) <= window + ab))
            out["pairs_t2b"] += len(a)
            waits.extend(np.minimum(np.nan_to_num(w, nan=wait_cap), wait_cap).tolist())
    if len(t):
        for v in a_by.values():
            r = M.bus_to_train(t, v, m2, window)
            w = r["lead"] - m2
            out["b2t"] += int(r["good"].sum())
            out["caught_b2t"] += int(np.sum(np.nan_to_num(w, nan=np.inf) <= window + tb))
            out["pairs_b2t"] += len(t)
    out["total"] = out["t2b"] + out["b2t"]
    out["caught"] = out["caught_t2b"] + out["caught_b2t"]
    out["wait_t2b"] = float(np.mean(waits)) if waits else None
    return out


def past_days(delays: dict, first: dt.date, last: dt.date, min_trains: int = 20) -> list[dt.date]:
    """Days in [first, last] with delay data for at least `min_trains` trains."""
    n = collections.Counter(k[0] for k in delays)
    return [first + dt.timedelta(days=i) for i in range((last - first).days + 1)
            if n.get(str(first + dt.timedelta(days=i)), 0) >= min_trains]


def evaluate(timetables: dict[str, dict[str, list[dict]]], tr: pd.DataFrame, delays: dict, days: list[dt.date],
             windows: list[float], m1: float, m2: float, buffers: dict | None = None,
             wait_cap: float = 60.0) -> list[dict]:
    """One row per (day, timetable, window).

    timetables: name -> {day type -> list of bus events {route, arr, dep}}.
    """
    rows = []
    for d in days:
        dtp = day_type(d)
        arr, dep = actual_trains(tr, d, delays)
        ic = [x["delay"] for x in arr if x["delay"] is not None]
        info = {"date": str(d), "day_type": dtp, "trains_arr": len(arr), "trains_dep": len(dep),
                "arr_with_data": len(ic), "arr_src_plk": sum(x["src"] == "PKP PLK" for x in arr),
                "delay_mean": float(np.mean(ic)) if ic else None,
                "delay_median": float(np.median(ic)) if ic else None,
                "late_over_5": int(sum(v > 5 for v in ic))}
        for name, by_type in timetables.items():
            bus = by_type.get(dtp)
            if not bus:
                continue
            for w in windows:
                pl = count(bus, arr, dep, m1, m2, w, "t", buffers, wait_cap)
                ac = count(bus, arr, dep, m1, m2, w, "act", buffers, wait_cap)
                rows.append({**info, "timetable": name, "window": w,
                             **{f"plan_{k}": v for k, v in pl.items()}, **{f"actual_{k}": v for k, v in ac.items()}})
    return rows


def worst_days(rows: list[dict]) -> dict:
    """Day with the most trains more than 5 min late (then the largest mean delay), per day type and overall."""
    seen, out = {}, {}
    for r in rows:
        seen[r["date"]] = r
    days = sorted(seen.values(), key=lambda r: (r["late_over_5"], r["delay_mean"] or 0), reverse=True)
    for r in days:
        info = {k: r[k] for k in ("date", "day_type", "late_over_5", "delay_mean", "delay_median", "arr_with_data", "trains_arr")}
        out.setdefault(r["day_type"], info)
        out.setdefault("all", info)
    return out


def summary(rows: list[dict]) -> list[dict]:
    """Mean values per (day type, timetable, window)."""
    df = pd.DataFrame(rows)
    if df.empty:
        return []
    cols = [c for c in df.columns if c.startswith(("plan_", "actual_"))]
    g = df.groupby(["day_type", "timetable", "window"], sort=False)[cols].mean().round(2).reset_index()
    g["days"] = df.groupby(["day_type", "timetable", "window"], sort=False).size().values
    return g.to_dict("records")

"""Passenger demand at the station and the effect of shorter transfer waits.

Anchor: the average daily passenger exchange at the railway station (boardings
plus alightings, Urząd Transportu Kolejowego). Everything else is a parameter:
the share of station passengers who use the city bus, the elasticity of bus use
to the waiting time at the transfer, and the average revenue per bus trip.
The result is an estimate with a low, middle and high variant, not a forecast.
"""
from __future__ import annotations

import numpy as np

from .. import model as M


def mean_waits(train_arr: np.ndarray, train_dep: np.ndarray, dep_by_line: dict, arr_by_line: dict,
               m1: float, m2: float, cap: float = 60.0) -> dict:
    """Mean wait per (train, line) pair: train -> bus wait and bus -> train lead.
    Waits above `cap` count as `cap` (the passenger takes another way)."""
    w1, w2 = [], []
    for ln, bd in dep_by_line.items():
        if len(bd) and len(train_arr):
            w = M.train_to_bus(train_arr, np.sort(bd), m1)["wait"] - m1
            w1 += list(np.where(np.isnan(w), cap, np.minimum(w, cap)))
    for ln, ba in arr_by_line.items():
        if len(ba) and len(train_dep):
            w = M.bus_to_train(train_dep, np.sort(ba), m2)["lead"] - m2
            w2 += list(np.where(np.isnan(w), cap, np.minimum(w, cap)))
    return {"t2b": float(np.mean(w1)) if w1 else float("nan"), "b2t": float(np.mean(w2)) if w2 else float("nan"),
            "all": float(np.mean(w1 + w2)) if (w1 or w2) else float("nan"), "pairs": len(w1) + len(w2)}


def ridership_effect(wait_before: float, wait_after: float, cfg: dict) -> dict:
    """Extra bus trips and revenue per day and per year for three variants."""
    out = {}
    base_exchange = cfg["station_daily_exchange"]
    rel = (wait_after - wait_before) / wait_before if wait_before else 0.0
    for name in ("low", "mid", "high"):
        share, el, rev = cfg[f"bus_share_{name}"], cfg[f"wait_elasticity_{name}"], cfg[f"revenue_per_trip_{name}"]
        base = base_exchange * share
        change = el * rel
        extra = base * change
        out[name] = {"bus_share": share, "elasticity": el, "revenue_per_trip": rev,
                     "base_trips_day": base, "change": change, "extra_trips_day": extra,
                     "extra_trips_year": extra * 365, "extra_revenue_year": extra * 365 * rev}
    out["wait_before"], out["wait_after"], out["wait_change"] = wait_before, wait_after, rel
    return out

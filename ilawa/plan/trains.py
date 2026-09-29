"""Trains at the railway station for a date, and delay buffers from observed delays."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd


def station_trains(tr: pd.DataFrame, d: dt.date) -> tuple[list[dict], list[dict]]:
    arr = tr[(tr["arr_date"] == d) & tr["arr_min"].notna()].sort_values("arr_min")
    dep = tr[(tr["dep_date"] == d) & tr["dep_min"].notna()].sort_values("dep_min")
    a = [{"t": float(r.arr_min), "cat": str(r.category), "no": str(r.number), "rel": str(r.relation),
          "time": str(r.arr_time)[:5]} for r in arr.itertuples()]
    b = [{"t": float(r.dep_min), "cat": str(r.category), "no": str(r.number), "rel": str(r.relation),
          "time": str(r.dep_time)[:5]} for r in dep.itertuples()]
    return a, b


def delay_buffers(hist: pd.DataFrame, cap: float = 3.0, quantile: float = 0.5) -> dict[str, float]:
    """Delay at arrival that a transfer should cover, per train category (median by default)."""
    if hist is None or hist.empty:
        return {}
    out = {}
    for c, g in hist.groupby(hist["category"].fillna("?")):
        x = pd.to_numeric(g["arr_delay"], errors="coerce").dropna()
        if len(x) >= 20:
            out[str(c)] = float(min(cap, max(0.0, np.quantile(x, quantile))))
    return out

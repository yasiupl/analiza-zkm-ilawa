"""Transfer metrics between trains and buses, and a random-phase null model.

All times are minutes after midnight of the operating day (values above 1440
are after midnight). Definitions (m = walking time between platform and stop):

Train -> bus, for a train arriving at a:
  wait       = first bus departure d >= a + m, minus a
  good       = bus departs in [a + m, a + m + GOOD]
  near_miss  = bus departs in [a - 2, a + m): it leaves while passengers walk
Bus -> train, for a train departing at t:
  lead       = t minus last bus arrival b <= t - m
  good       = bus arrives in [t - m - GOOD, t - m]
  near_miss  = bus arrives in (t - m, t + 2]: it comes too late for the train
near_miss_long = near_miss and not good: the bus leaves (or comes) at the
  wrong moment and the next usable bus is later than m + GOOD. This is the case
  that passengers remember.
Null model: every bus line is shifted by its own random offset U(-30, 30) min.
It keeps the bus headways but removes any relation to the train timetable.
"""
from __future__ import annotations

import numpy as np

GOOD = 10          # minutes of acceptable waiting after the walk
MAX_WAIT = 120     # a gap above this counts as "no connection"
NEAR_BEFORE = 2    # a bus that leaves up to 2 min before the train arrival is still "seen"

# Assumed seat capacity by commercial category (proxy for passenger volume).
TRAIN_CAPACITY = {"EIP": 402, "EIC": 400, "IC": 400, "TLK": 450, "EC": 400, "EN": 300,
                  "R": 200, "RP": 200, "KS": 200, "OS": 200}
DEFAULT_TRAIN_CAPACITY = 250


def train_capacity(category: str | None) -> int:
    return TRAIN_CAPACITY.get((category or "").upper().strip(), DEFAULT_TRAIN_CAPACITY)


def _next_after(sorted_x: np.ndarray, t: np.ndarray) -> np.ndarray:
    i = np.searchsorted(sorted_x, t, side="left")
    out = np.full(t.shape, np.nan)
    ok = i < len(sorted_x)
    out[ok] = sorted_x[i[ok]]
    return out


def _prev_before(sorted_x: np.ndarray, t: np.ndarray) -> np.ndarray:
    i = np.searchsorted(sorted_x, t, side="right") - 1
    out = np.full(t.shape, np.nan)
    ok = i >= 0
    out[ok] = sorted_x[i[ok]]
    return out


def _any_in(sorted_x: np.ndarray, lo: np.ndarray, hi: np.ndarray, hi_closed: bool) -> np.ndarray:
    a = np.searchsorted(sorted_x, lo, side="left")
    b = np.searchsorted(sorted_x, hi, side="right" if hi_closed else "left")
    return b > a


def train_to_bus(train_arr: np.ndarray, bus_dep: np.ndarray, m: float, good_window: float = GOOD) -> dict:
    d = np.sort(np.asarray(bus_dep, float))
    a = np.asarray(train_arr, float)
    nxt = _next_after(d, a + m)
    wait = nxt - a
    wait[wait > MAX_WAIT] = np.nan
    near = _any_in(d, a - NEAR_BEFORE, a + m, hi_closed=False)
    good = (wait <= m + good_window)
    return {"wait": wait, "near_miss": near, "good": good}


def bus_to_train(train_dep: np.ndarray, bus_arr: np.ndarray, m: float, good_window: float = GOOD) -> dict:
    b = np.sort(np.asarray(bus_arr, float))
    t = np.asarray(train_dep, float)
    prv = _prev_before(b, t - m)
    lead = t - prv
    lead[lead > MAX_WAIT] = np.nan
    near = _any_in(b, t - m + 1e-9, t + NEAR_BEFORE, hi_closed=True)
    good = (lead <= m + good_window)
    return {"lead": lead, "near_miss": near, "good": good}


def per_bus(bus_arr: np.ndarray, bus_dep: np.ndarray, train_arr: np.ndarray,
            train_dep: np.ndarray, m: float) -> dict:
    """The per-bus view: time from a bus arrival to the next usable train, and
    time from the last usable train arrival to a bus departure."""
    td = np.sort(np.asarray(train_dep, float))
    ta = np.sort(np.asarray(train_arr, float))
    b = np.asarray(bus_arr, float)
    d = np.asarray(bus_dep, float)
    to_train = _next_after(td, b + m) - b
    from_train = d - _prev_before(ta, d - m)
    to_train[to_train > MAX_WAIT] = np.nan
    from_train[from_train > MAX_WAIT] = np.nan
    return {"bus_arr_to_next_train": to_train, "bus_dep_since_last_train": from_train}


def summary(x: np.ndarray, w: np.ndarray | None = None) -> dict:
    x = np.asarray(x, float)
    ok = ~np.isnan(x)
    if not ok.any():
        return {"n": 0}
    xs = x[ok]
    out = {"n": int(ok.sum()), "n_none": int((~ok).sum()), "mean": float(xs.mean()),
           "median": float(np.median(xs)), "p25": float(np.percentile(xs, 25)),
           "p75": float(np.percentile(xs, 75)), "p90": float(np.percentile(xs, 90))}
    if w is not None:
        ww = np.asarray(w, float)[ok]
        out["mean_weighted"] = float((xs * ww).sum() / ww.sum())
    return out


def rate(flag: np.ndarray, w: np.ndarray | None = None) -> float:
    f = np.asarray(flag, float)
    if w is None:
        return float(f.mean()) if len(f) else float("nan")
    w = np.asarray(w, float)
    return float((f * w).sum() / w.sum()) if w.sum() else float("nan")


def stats(train_arr, train_dep, bus_arr_by_line: dict, bus_dep_by_line: dict, m_t2b: float,
          m_b2t: float, w_arr=None, w_dep=None) -> dict:
    ba = np.concatenate([np.asarray(v, float) for v in bus_arr_by_line.values()] or [np.array([])])
    bd = np.concatenate([np.asarray(v, float) for v in bus_dep_by_line.values()] or [np.array([])])
    t2b = train_to_bus(train_arr, bd, m_t2b)
    b2t = bus_to_train(train_dep, ba, m_b2t)
    return {
        "t2b_near_miss": rate(t2b["near_miss"], w_arr),
        "t2b_good": rate(t2b["good"], w_arr),
        "t2b_near_miss_long": rate(t2b["near_miss"] & ~t2b["good"], w_arr),
        "t2b_wait_median": float(np.nanmedian(t2b["wait"])) if np.isfinite(t2b["wait"]).any() else np.nan,
        "t2b_wait_mean": float(np.nanmean(t2b["wait"])) if np.isfinite(t2b["wait"]).any() else np.nan,
        "b2t_near_miss": rate(b2t["near_miss"], w_dep),
        "b2t_good": rate(b2t["good"], w_dep),
        "b2t_near_miss_long": rate(b2t["near_miss"] & ~b2t["good"], w_dep),
        "b2t_lead_median": float(np.nanmedian(b2t["lead"])) if np.isfinite(b2t["lead"]).any() else np.nan,
        "b2t_lead_mean": float(np.nanmean(b2t["lead"])) if np.isfinite(b2t["lead"]).any() else np.nan,
    }


def null_model(train_arr, train_dep, bus_arr_by_line: dict, bus_dep_by_line: dict, m_t2b: float,
               m_b2t: float, w_arr=None, w_dep=None, n: int = 5000, shift: float = 30,
               seed: int = 1) -> dict:
    """Distribution of stats() when each bus line gets a random time shift."""
    rng = np.random.default_rng(seed)
    lines = sorted(set(bus_arr_by_line) | set(bus_dep_by_line))
    keys = None
    acc: dict[str, list] = {}
    for _ in range(n):
        s = {ln: rng.uniform(-shift, shift) for ln in lines}
        ba = {ln: np.asarray(v, float) + s[ln] for ln, v in bus_arr_by_line.items()}
        bd = {ln: np.asarray(v, float) + s[ln] for ln, v in bus_dep_by_line.items()}
        r = stats(train_arr, train_dep, ba, bd, m_t2b, m_b2t, w_arr, w_dep)
        keys = keys or list(r)
        for k in keys:
            acc.setdefault(k, []).append(r[k])
    return {k: np.asarray(v, float) for k, v in acc.items()}


def compare(observed: dict, null: dict) -> dict:
    """Observed value, null mean, null 5-95 % band and the share of null values
    that are lower than or equal to the observed value (empirical percentile)."""
    out = {}
    for k, v in observed.items():
        z = null[k][~np.isnan(null[k])]
        out[k] = {"observed": v, "null_mean": float(z.mean()), "null_p05": float(np.percentile(z, 5)),
                  "null_p95": float(np.percentile(z, 95)), "percentile": float((z <= v).mean())}
    return out


def offset_histogram(train_times, bus_times, lo: int = -30, hi: int = 30) -> dict:
    """Count all pairs (bus - train) in [lo, hi] minutes, 1-minute bins."""
    t = np.asarray(train_times, float)[:, None]
    b = np.asarray(bus_times, float)[None, :]
    diff = (b - t).ravel()
    diff = diff[(diff >= lo) & (diff <= hi)]
    bins = np.arange(lo, hi + 2)
    h, _ = np.histogram(np.round(diff), bins=bins)
    return {"bins": bins[:-1].tolist(), "counts": h.tolist()}


def best_shifts(train_arr, train_dep, bus_arr_by_line: dict, bus_dep_by_line: dict, m_t2b: float,
                m_b2t: float, w_arr=None, w_dep=None, span: int = 10) -> dict:
    """For each line alone: the shift (whole minutes, -span..+span) that gives the
    most good connections (train->bus plus bus->train, capacity weighted)."""
    def score(ba, bd):
        r = stats(train_arr, train_dep, ba, bd, m_t2b, m_b2t, w_arr, w_dep)
        return (r["t2b_good"] + r["b2t_good"]) / 2, r
    base, base_r = score(bus_arr_by_line, bus_dep_by_line)
    out = {"base": base, "base_detail": base_r, "lines": {}}
    for ln in sorted(set(bus_arr_by_line) | set(bus_dep_by_line)):
        best = (base, 0, base_r)
        for d in range(-span, span + 1):
            ba = {k: (v + d if k == ln else v) for k, v in bus_arr_by_line.items()}
            bd = {k: (v + d if k == ln else v) for k, v in bus_dep_by_line.items()}
            sc, r = score(ba, bd)
            if sc > best[0] + 1e-9:
                best = (sc, d, r)
        out["lines"][ln] = {"shift": best[1], "score": best[0], "gain": best[0] - base,
                            "t2b_good": best[2]["t2b_good"], "b2t_good": best[2]["b2t_good"]}
    return out

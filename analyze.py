"""Static and dynamic analysis of train-bus transfers at Iława Główna.

Run:  python analyze.py [--m-t2b 4] [--m-b2t 5] [--null 5000]

Static:  planned trains (PKP PLK schedules) against the ZKM timetable.
Dynamic: actual train times (spoznienia.me history for PKP Intercity and
         data/monitor/*.csv from monitor.py for all carriers) against the ZKM
         timetable. Bus deviation follows the ZKM tolerance (-1 to +3 min).
Output:  results/*.json and results/*.csv
"""
from __future__ import annotations

import argparse
import datetime as dt
import json

import numpy as np
import pandas as pd

from ilawa import model as M
from ilawa.calendar import day_type, is_school_day
from ilawa.common import DATA, RAW, ROOT, load_json
from ilawa.plk import schedule_rows
from ilawa.zkm import BUS_CAPACITY, load_bus_events

RES = ROOT / "results"
BUS_TT_VALID_FROM = dt.date(2026, 9, 1)   # ZKM school timetable
BUS_DEV = (-1.0, 3.0)                     # ZKM tolerance: early 1 min, late 3 min


def to_min(hms: str | None) -> float:
    if not hms or not isinstance(hms, str):
        return np.nan
    p = hms.split(":")
    return int(p[0]) * 60 + int(p[1]) + (int(p[2]) / 60 if len(p) > 2 else 0)


# ---------------------------------------------------------------- inputs
def bus_sets(d: dt.date) -> dict:
    ev = load_bus_events(day_type(d), school_day=is_school_day(d))
    arr, dep = {}, {}
    for e in ev:
        (arr if e.kind == "arr" else dep).setdefault(e.line, []).append(e.minute)
    return {"events": ev, "arr": {k: np.array(v, float) for k, v in arr.items()},
            "dep": {k: np.array(v, float) for k, v in dep.items()}}


def gtfs_category(row) -> str:
    """Commercial category from the GTFS route name, the train number or the agency."""
    for v in (row.get("route_short_name"), str(row.get("number") or "").split(" ")[0]):
        v = str(v or "").strip().upper()
        if v and v.isalpha() and len(v) <= 4:
            return v
    return {"PR": "R", "IC": "IC", "KM": "R", "KS": "R", "AR": "R"}.get(str(row.get("agency_id")), "R")


def load_gtfs_trains() -> pd.DataFrame:
    df = pd.read_csv(DATA / "trains_gtfs_ilawa.csv", dtype={"number": str})
    sd = pd.to_datetime(df["service_date"])
    for k in ("arr", "dep"):
        m = df[f"{k}_min"]
        df[f"{k}_date"] = (sd + pd.to_timedelta((m // 1440).fillna(0), unit="D")).dt.date
        df[f"{k}_min"] = m % 1440
        df[f"{k}_time"] = df[f"{k}_min"].map(lambda x: f"{int(x)//60:02d}:{int(x)%60:02d}:00" if pd.notna(x) else None)
    df["category"] = df.apply(gtfs_category, axis=1)
    df["carrier"] = df["agency_id"]
    df["capacity"] = df["category"].map(M.train_capacity)
    df["relation"] = df["origin"].fillna("?") + " – " + df["destination"].fillna("?")
    df["source"] = "GTFS mkuran.pl"
    return df


def load_planned_trains() -> pd.DataFrame:
    if not list((RAW / "plk").glob("schedules_*.json*")) and (DATA / "trains_gtfs_ilawa.csv").exists():
        return load_gtfs_trains()
    st = load_json(DATA / "plk_station.json")
    sid = int(st["id"])
    rows = []
    for f in sorted((RAW / "plk").glob("schedules_*.json*")):
        rows += schedule_rows(load_json(f), sid)
    if not rows:
        raise SystemExit("No PLK schedules in data/raw/plk. Run collect.py first.")
    df = pd.DataFrame(rows).drop_duplicates(["operating_date", "schedule_id", "order_id", "train_order_id"])
    od = pd.to_datetime(df["operating_date"])
    df["arr_date"] = (od + pd.to_timedelta(df["arr_day"].fillna(0), unit="D")).dt.date
    df["dep_date"] = (od + pd.to_timedelta(df["dep_day"].fillna(0), unit="D")).dt.date
    df["arr_min"] = df["arr_time"].map(to_min)
    df["dep_min"] = df["dep_time"].map(to_min)
    df["capacity"] = df["category"].map(M.train_capacity)
    df["relation"] = df["origin"].fillna("?") + " – " + df["destination"].fillna("?")
    df["source"] = "PKP PLK API"
    return df


def load_ic_history() -> pd.DataFrame:
    rows = []
    for f in sorted((RAW / "spoznienia").glob("*.json")):
        for r in json.loads(f.read_text(encoding="utf-8")):
            r["date"] = f.stem
            rows.append(r)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["arr_min"] = df["scheduled_arrival"].map(to_min)
    df["dep_min"] = df["scheduled_departure"].map(to_min)
    df["arr_delay"] = pd.to_numeric(df["delay_arrival_min"], errors="coerce")
    df["dep_delay"] = pd.to_numeric(df["delay_departure_min"], errors="coerce")
    df["category"] = df["train_category"]
    df["number"] = df["train_number"]
    df["relation"] = df["from_station"] + " – " + df["to_station"]
    df["source"] = "spoznienia.me"
    df["date_arr"] = df["date"]
    df["date_dep"] = df["date"]
    return df


def load_monitor() -> pd.DataFrame:
    files = sorted((DATA / "monitor").glob("*.csv"))
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_csv(f, dtype=str) for f in files], ignore_index=True)
    od = pd.to_datetime(df["operating_date"])
    for k in ("arr", "dep"):
        off = pd.to_numeric(df[f"{k}_day"], errors="coerce").fillna(0)
        df[f"date_{k}"] = (od + pd.to_timedelta(off, unit="D")).dt.strftime("%Y-%m-%d")
    df["arr_min"] = df["arr_time"].map(to_min)
    df["dep_min"] = df["dep_time"].map(to_min)
    df["arr_delay"] = pd.to_numeric(df["arr_delay"], errors="coerce")
    df["dep_delay"] = pd.to_numeric(df["dep_delay"], errors="coerce")
    df["relation"] = df["origin"] + " – " + df["destination"]
    df["source"] = "PLK monitor"
    # Only trains that the monitor saw to the end at this station.
    done = df["actual_arr"].notna() | df["actual_dep"].notna()
    return df[done]


# ---------------------------------------------------------------- static
def static_for_date(tr: pd.DataFrame, d: dt.date, m1: float, m2: float) -> pd.DataFrame:
    b = bus_sets(d)
    bd = np.sort(np.concatenate(list(b["dep"].values())))
    ba = np.sort(np.concatenate(list(b["arr"].values())))
    lo, hi = min(ba.min(), bd.min()), max(ba.max(), bd.max())
    arr = tr[(tr["arr_date"] == d) & tr["arr_min"].notna()].copy()
    dep = tr[(tr["dep_date"] == d) & tr["dep_min"].notna()].copy()
    t2b = M.train_to_bus(arr["arr_min"].to_numpy(), bd, m1)
    b2t = M.bus_to_train(dep["dep_min"].to_numpy(), ba, m2)
    dep_by_min = {e.minute: e for e in b["events"] if e.kind == "dep"}
    arr_by_min = {e.minute: e for e in b["events"] if e.kind == "arr"}
    out = []
    for i, (_, r) in enumerate(arr.iterrows()):
        w = t2b["wait"][i]
        bus = dep_by_min.get(r["arr_min"] + w) if np.isfinite(w) else None
        out.append({"date": str(d), "day_type": day_type(d), "direction": "pociąg→autobus",
                    "time": r["arr_time"][:5], "minute": r["arr_min"], "category": r["category"],
                    "number": r["number"], "carrier": r["carrier"], "relation": r["relation"],
                    "neighbour": r["prev_station"], "capacity": r["capacity"],
                    "in_bus_hours": lo <= r["arr_min"] <= hi,
                    "wait": w, "good": bool(t2b["good"][i]), "near_miss": bool(t2b["near_miss"][i]),
                    "near_miss_long": bool(t2b["near_miss"][i] and not t2b["good"][i]),
                    "bus_line": bus.line if bus else None, "bus_time": bus.hhmm if bus else None,
                    "bus_towards": bus.towards if bus else None})
    for i, (_, r) in enumerate(dep.iterrows()):
        w = b2t["lead"][i]
        bus = arr_by_min.get(r["dep_min"] - w) if np.isfinite(w) else None
        out.append({"date": str(d), "day_type": day_type(d), "direction": "autobus→pociąg",
                    "time": r["dep_time"][:5], "minute": r["dep_min"], "category": r["category"],
                    "number": r["number"], "carrier": r["carrier"], "relation": r["relation"],
                    "neighbour": r["next_station"], "capacity": r["capacity"],
                    "in_bus_hours": lo <= r["dep_min"] <= hi,
                    "wait": w, "good": bool(b2t["good"][i]), "near_miss": bool(b2t["near_miss"][i]),
                    "near_miss_long": bool(b2t["near_miss"][i] and not b2t["good"][i]),
                    "bus_line": bus.line if bus else None, "bus_time": bus.hhmm if bus else None,
                    "bus_towards": None})
    return pd.DataFrame(out)


def per_bus_for_date(tr: pd.DataFrame, d: dt.date, m1: float, m2: float) -> pd.DataFrame:
    b = bus_sets(d)
    arr = tr[(tr["arr_date"] == d) & tr["arr_min"].notna()]["arr_min"].to_numpy()
    dep = tr[(tr["dep_date"] == d) & tr["dep_min"].notna()]["dep_min"].to_numpy()
    rows = []
    for e in b["events"]:
        x = np.array([float(e.minute)])
        if e.kind == "arr":
            v = M.per_bus(x, x, arr, dep, m2)["bus_arr_to_next_train"][0]
        else:
            v = M.per_bus(x, x, arr, dep, m1)["bus_dep_since_last_train"][0]
        rows.append({"date": str(d), "day_type": day_type(d), "kind": e.kind, "line": e.line,
                     "time": e.hhmm, "towards": e.towards, "flags": e.flags, "minutes": v})
    return pd.DataFrame(rows)


def null_for_date(tr: pd.DataFrame, d: dt.date, m1: float, m2: float, n: int, weighted: bool) -> dict:
    b = bus_sets(d)
    ba_all = np.concatenate(list(b["arr"].values()))
    bd_all = np.concatenate(list(b["dep"].values()))
    lo, hi = min(ba_all.min(), bd_all.min()), max(ba_all.max(), bd_all.max())
    a = tr[(tr["arr_date"] == d) & tr["arr_min"].between(lo, hi)]
    t = tr[(tr["dep_date"] == d) & tr["dep_min"].between(lo, hi)]
    wa = a["capacity"].to_numpy(float) if weighted else None
    wt = t["capacity"].to_numpy(float) if weighted else None
    obs = M.stats(a["arr_min"].to_numpy(), t["dep_min"].to_numpy(), b["arr"], b["dep"], m1, m2, wa, wt)
    nul = M.null_model(a["arr_min"].to_numpy(), t["dep_min"].to_numpy(), b["arr"], b["dep"],
                       m1, m2, wa, wt, n=n)
    res = {"date": str(d), "day_type": day_type(d), "n_train_arr": len(a), "n_train_dep": len(t),
           "comparison": M.compare(obs, nul)}
    if weighted:
        res["best_shifts"] = M.best_shifts(a["arr_min"].to_numpy(), t["dep_min"].to_numpy(),
                                           b["arr"], b["dep"], m1, m2, wa, wt)
    return res


def per_line_for_date(tr: pd.DataFrame, d: dt.date, m1: float, m2: float, n_null: int = 0,
                      seed: int = 3) -> list[dict]:
    """The same metrics for each bus line alone: a passenger needs one line, not any bus."""
    b = bus_sets(d)
    rng = np.random.default_rng(seed)
    out = []
    for ln in sorted(set(b["arr"]) | set(b["dep"])):
        bd, ba = b["dep"].get(ln, np.array([])), b["arr"].get(ln, np.array([]))
        for direction, times, bus, fn, m in (
                ("pociąg→autobus", tr[(tr["arr_date"] == d)]["arr_min"].dropna().to_numpy(), bd, M.train_to_bus, m1),
                ("autobus→pociąg", tr[(tr["dep_date"] == d)]["dep_min"].dropna().to_numpy(), ba, M.bus_to_train, m2)):
            if len(bus) == 0:
                continue
            lo, hi = bus.min() - 60, bus.max()  # trains while this line runs
            if direction == "autobus→pociąg":
                lo, hi = bus.min(), bus.max() + 60
            t = times[(times >= lo) & (times <= hi)]
            if len(t) == 0:
                continue
            r = fn(t, bus, m)
            key = "wait" if "wait" in r else "lead"
            row = {"date": str(d), "day_type": day_type(d), "line": ln, "direction": direction,
                   "n_trains": int(len(t)), "n_buses": int(len(bus)),
                   "near_miss": float(r["near_miss"].mean()), "good": float(r["good"].mean()),
                   "median_wait": float(np.nanmedian(r[key])) if np.isfinite(r[key]).any() else None,
                   "mean_wait": float(np.nanmean(r[key])) if np.isfinite(r[key]).any() else None}
            if n_null:
                nm = np.empty(n_null)
                gd = np.empty(n_null)
                for i in range(n_null):
                    rr = fn(t, bus + rng.uniform(-30, 30), m)
                    nm[i], gd[i] = rr["near_miss"].mean(), rr["good"].mean()
                row.update({"null_near_miss": float(nm.mean()), "near_miss_pct": float((nm <= row["near_miss"]).mean()),
                            "null_good": float(gd.mean()), "good_pct": float((gd <= row["good"]).mean())})
            out.append(row)
    return out


def dynamic_per_line(hist: pd.DataFrame, m1: float, draws: int = 500, seed: int = 11) -> pd.DataFrame:
    """Train -> bus of one line: planned wait, expected actual wait, chance to keep the planned bus."""
    rng = np.random.default_rng(seed)
    out = []
    for date_s in sorted(hist["date_arr"].dropna().unique()):
        d = dt.date.fromisoformat(str(date_s)[:10])
        if d < BUS_TT_VALID_FROM:
            continue
        b = bus_sets(d)
        g = hist[(hist["date_arr"] == date_s)]
        g = g[np.isfinite(g["arr_min"]) & np.isfinite(g["arr_delay"])]
        for ln, bd in b["dep"].items():
            bd = np.sort(bd)
            sel = g[(g["arr_min"] >= bd.min() - 60) & (g["arr_min"] <= bd.max())]
            for _, r in sel.iterrows():
                a, dl = r["arr_min"], r["arr_delay"]
                pw = M.train_to_bus(np.array([a]), bd, m1)["wait"][0]
                eps = rng.uniform(*BUS_DEV, size=(draws, len(bd)))
                dd = bd[None, :] + eps
                act = a + dl
                w = np.where(dd >= act + m1, dd, np.inf).min(axis=1) - act
                w[~np.isfinite(w) | (w > M.MAX_WAIT)] = np.nan
                if not np.isfinite(pw):
                    continue
                keep = np.nan
                if np.isfinite(pw):
                    i = int(np.searchsorted(bd, a + pw - 1e-9))
                    keep = float((dd[:, i] >= act + m1).mean())
                out.append({"date": str(d), "day_type": day_type(d), "line": ln, "category": r.get("category"),
                            "number": r.get("number"), "source": r.get("source"), "delay": dl,
                            "planned_wait": pw, "actual_wait": float(np.nanmean(w)) if np.isfinite(w).any() else np.nan,
                            "planned_good": bool(np.isfinite(pw) and pw <= m1 + M.GOOD), "p_keep": keep})
    return pd.DataFrame(out)


# ---------------------------------------------------------------- dynamic
def dynamic(hist: pd.DataFrame, m1: float, m2: float, draws: int = 2000, seed: int = 7) -> pd.DataFrame:
    """For each observed train: planned vs actual transfer, bus deviation U(-1, +3)."""
    rng = np.random.default_rng(seed)
    out = []
    dates = sorted(set(hist["date_arr"].dropna()) | set(hist["date_dep"].dropna()))
    for date_s in dates:
        d = dt.date.fromisoformat(str(date_s)[:10])
        if d < BUS_TT_VALID_FROM:
            continue
        b = bus_sets(d)
        bd = np.sort(np.concatenate(list(b["dep"].values())))
        ba = np.sort(np.concatenate(list(b["arr"].values())))
        lo, hi = min(ba.min(), bd.min()), max(ba.max(), bd.max())
        g = hist[(hist["date_arr"] == date_s) | (hist["date_dep"] == date_s)]
        for _, r in g.iterrows():
            base = {"date": str(d), "day_type": day_type(d), "category": r.get("category"),
                    "number": r.get("number"), "relation": r.get("relation"), "source": r.get("source")}
            a, da = r["arr_min"], r["arr_delay"]
            if r["date_arr"] == date_s and np.isfinite(a) and np.isfinite(da) and lo <= a <= hi:
                plan = M.train_to_bus(np.array([a]), bd, m1)
                pw = plan["wait"][0]
                act = a + da
                eps = rng.uniform(*BUS_DEV, size=(draws, len(bd)))
                dd = bd[None, :] + eps
                ok = dd >= act + m1
                first = np.where(ok, dd, np.inf).min(axis=1) - act
                first[first > M.MAX_WAIT] = np.nan
                kept = np.nan
                if np.isfinite(pw) and pw <= m1 + M.GOOD:
                    i = int(np.searchsorted(bd, a + pw - 1e-9))
                    kept = float((bd[i] + eps[:, i] >= act + m1).mean())
                out.append({**base, "direction": "pociąg→autobus", "time": f"{int(a)//60:02d}:{int(a)%60:02d}",
                            "delay": da, "planned_wait": pw, "actual_wait_mean": float(np.nanmean(first)),
                            "planned_good": bool(np.isfinite(pw) and pw <= m1 + M.GOOD),
                            "p_keep_planned_bus": kept})
            t, dl = r["dep_min"], r["dep_delay"]
            if r["date_dep"] == date_s and np.isfinite(t) and np.isfinite(dl) and lo <= t <= hi:
                plan = M.bus_to_train(np.array([t]), ba, m2)
                pl = plan["lead"][0]
                if np.isfinite(pl):
                    i = int(np.searchsorted(ba, t - pl - 1e-9))
                    eps = rng.uniform(*BUS_DEV, size=draws)
                    caught = (ba[i] + eps + m2 <= t + dl)
                    out.append({**base, "direction": "autobus→pociąg", "time": f"{int(t)//60:02d}:{int(t)%60:02d}",
                                "delay": dl, "planned_wait": pl, "actual_wait_mean": float(pl + dl - eps.mean()),
                                "planned_good": bool(pl <= m2 + M.GOOD),
                                "p_keep_planned_bus": float(caught.mean())})
    return pd.DataFrame(out)


# ---------------------------------------------------------------- main
def describe(df: pd.DataFrame, weight: bool = False) -> dict:
    x = df[df["in_bus_hours"]]
    w = x["capacity"].to_numpy(float) if weight else None
    return {"n": int(len(x)), "n_outside_bus_hours": int((~df["in_bus_hours"]).sum()),
            "wait": M.summary(x["wait"].to_numpy(float), w),
            "good_rate": M.rate(x["good"].to_numpy(), w),
            "near_miss_rate": M.rate(x["near_miss"].to_numpy(), w),
            "near_miss_long_rate": M.rate(x["near_miss_long"].to_numpy(), w),
            "no_bus_rate": float(x["wait"].isna().mean()) if len(x) else None}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--m-t2b", type=float, default=4, help="walk from platform to bus stop, min")
    ap.add_argument("--m-b2t", type=float, default=5, help="walk from bus stop to platform, min")
    ap.add_argument("--null", type=int, default=5000, help="null model draws per date")
    ap.add_argument("--from-date", default=None, help="first planned date (default: today)")
    ap.add_argument("--days", type=int, default=28)
    a = ap.parse_args()
    RES.mkdir(exist_ok=True)
    m1, m2 = a.m_t2b, a.m_b2t
    tr = load_planned_trains()
    start = dt.date.fromisoformat(a.from_date) if a.from_date else dt.date.today()
    dates = [start + dt.timedelta(days=i) for i in range(a.days)]
    dates = [d for d in dates if (tr["arr_date"] == d).any() or (tr["dep_date"] == d).any()]
    if not dates:
        raise SystemExit("No planned trains in the chosen period.")

    ev = pd.concat([static_for_date(tr, d, m1, m2) for d in dates], ignore_index=True)
    pb = pd.concat([per_bus_for_date(tr, d, m1, m2) for d in dates], ignore_index=True)
    ev.to_csv(RES / "static_train_events.csv", index=False)
    pb.to_csv(RES / "static_bus_events.csv", index=False)

    summary = {"params": {"m_t2b": m1, "m_b2t": m2, "good_window": M.GOOD, "near_before": M.NEAR_BEFORE,
                          "max_wait": M.MAX_WAIT, "dates": [str(dates[0]), str(dates[-1])],
                          "bus_capacity": BUS_CAPACITY, "train_capacity": M.TRAIN_CAPACITY},
               "static": {}, "null": [], "sensitivity": [], "dynamic": {}}
    for (dtp, direction), g in ev.groupby(["day_type", "direction"]):
        summary["static"].setdefault(dtp, {})[direction] = {
            "all": describe(g), "capacity_weighted": describe(g, weight=True),
            "by_category": {c: describe(x) for c, x in g.groupby(g["category"].fillna("?"))},
            "by_neighbour": {c: describe(x) for c, x in g.groupby(g["neighbour"].fillna("?"))}}
    for (dtp, kind), g in pb.groupby(["day_type", "kind"]):
        summary["static"].setdefault(dtp, {})[f"bus_{kind}"] = M.summary(g["minutes"].to_numpy(float))

    # Null model for one representative date per day type (plus capacity weighting).
    reps: dict[str, dt.date] = {}
    for d in dates:  # prefer Tuesday-Thursday as the typical working day
        if day_type(d) != "Roboczy" or d.weekday() in (1, 2, 3):
            reps.setdefault(day_type(d), d)
    for d in dates:
        reps.setdefault(day_type(d), d)
    summary["representative_dates"] = {k: str(v) for k, v in reps.items()}
    for dtp, d in reps.items():
        for weighted in (False, True):
            r = null_for_date(tr, d, m1, m2, a.null, weighted)
            r["weighted"] = weighted
            summary["null"].append(r)
        b = bus_sets(d)
        ta = tr[(tr["arr_date"] == d)]["arr_min"].dropna().to_numpy()
        td = tr[(tr["dep_date"] == d)]["dep_min"].dropna().to_numpy()
        summary.setdefault("histograms", {})[dtp] = {
            "date": str(d),
            "bus_dep_minus_train_arr": M.offset_histogram(ta, np.concatenate(list(b["dep"].values()))),
            "train_dep_minus_bus_arr": M.offset_histogram(np.concatenate(list(b["arr"].values())), td)}
    # Per line: all dates, null model on the representative dates.
    pl = []
    for d in dates:
        pl += per_line_for_date(tr, d, m1, m2, n_null=1000 if d in reps.values() else 0)
    pl = pd.DataFrame(pl)
    pl.to_csv(RES / "static_per_line.csv", index=False)
    summary["per_line"] = {}
    for (dtp, ln, direction), g in pl.groupby(["day_type", "line", "direction"]):
        rep = g[g["date"] == str(reps.get(dtp))]
        summary["per_line"].setdefault(dtp, {}).setdefault(ln, {})[direction] = {
            "near_miss": float(g["near_miss"].mean()), "good": float(g["good"].mean()),
            "median_wait": float(g["median_wait"].median()), "mean_wait": float(g["mean_wait"].mean()),
            "n_trains_per_day": float(g["n_trains"].mean()), "n_buses_per_day": float(g["n_buses"].mean()),
            **({k: float(rep[k].iloc[0]) for k in ("null_near_miss", "near_miss_pct", "null_good", "good_pct")}
               if len(rep) and "null_near_miss" in rep else {})}

    # Sensitivity to walking time (all dates pooled).
    for mw in (2, 3, 4, 5, 6, 8):
        e2 = pd.concat([static_for_date(tr, d, mw, mw) for d in dates], ignore_index=True)
        for (dtp, direction), g in e2.groupby(["day_type", "direction"]):
            s = describe(g)
            summary["sensitivity"].append({"walk": mw, "day_type": dtp, "direction": direction,
                                           "good_rate": s["good_rate"], "near_miss_rate": s["near_miss_rate"],
                                           "median_wait": s["wait"].get("median")})

    # Dynamic analysis.
    for name, hist in (("ic_history", load_ic_history()), ("plk_monitor", load_monitor())):
        if hist.empty:
            summary["dynamic"][name] = {"n": 0}
            continue
        dy = dynamic(hist, m1, m2)
        dy.to_csv(RES / f"dynamic_{name}.csv", index=False)
        dsum = {"n_days": int(dy["date"].nunique()) if len(dy) else 0}
        for direction, g in dy.groupby("direction"):
            pg = g[g["planned_good"]]
            dsum[direction] = {
                "n": int(len(g)), "delay": M.summary(g["delay"].to_numpy(float)),
                "planned_wait": M.summary(g["planned_wait"].to_numpy(float)),
                "actual_wait": M.summary(g["actual_wait_mean"].to_numpy(float)),
                "n_planned_good": int(len(pg)),
                "p_keep_planned_bus_mean": float(pg["p_keep_planned_bus"].mean()) if len(pg) else None,
                "lost_connections_expected": float((1 - pg["p_keep_planned_bus"]).sum()) if len(pg) else None,
                "by_category": {c: {"n": int(len(x)), "delay_median": float(x["delay"].median()),
                                    "p_keep": float(x[x["planned_good"]]["p_keep_planned_bus"].mean())
                                    if x["planned_good"].any() else None}
                                for c, x in g.groupby(g["category"].fillna("?"))}}
        dl = dynamic_per_line(hist, m1)
        dl.to_csv(RES / f"dynamic_per_line_{name}.csv", index=False)
        dsum["per_line_t2b"] = {}
        for ln, g in dl.groupby("line"):
            pg = g[g["planned_good"]]
            dsum["per_line_t2b"][ln] = {
                "n": int(len(g)), "planned_wait_mean": float(g["planned_wait"].mean()),
                "actual_wait_mean": float(g["actual_wait"].mean()),
                "extra_wait_mean": float((g["actual_wait"] - g["planned_wait"]).mean()),
                "n_planned_good": int(len(pg)),
                "p_keep_planned_good": float(pg["p_keep"].mean()) if len(pg) else None}
        summary["dynamic"][name] = dsum

    (RES / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=float),
                                      encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("params",)}, ensure_ascii=False))
    print("Results in", RES)


if __name__ == "__main__":
    main()

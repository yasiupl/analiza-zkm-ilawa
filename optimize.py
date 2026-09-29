"""What key does the ZKM timetable follow, and which timetable changes add the
most good transfers?

Run:  python optimize.py [--max-shift 5] [--moves 10]     (after collect.py)
Output: results/key.json, results/optimize.json, results/optimize_moves.csv,
        results/optimize_extra.json (gain curve, whole-line shifts)

Part 1 (the key): layover of buses at Dworcowa, regularity of departures,
school trips, and the fit to each group of trains (by direction, by carrier and
by the train timetable of an earlier period) against the random-phase null model.

Part 2 (changes): a greedy search over single bus trips. Each step moves the one
trip (by -max..+max min) that adds the most good train-line transfers, in both
directions. Rules: a bus cannot leave Dworcowa before it arrives there (1 min
minimum layover), trips of one line keep their order and stay 3 min apart, and
each trip moves once. The result is a ranked list of changes with the gain of
each, checked on all days of the same type and on actual train delays.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import Counter

import numpy as np
import pandas as pd

import analyze as A
from ilawa import model as M
from ilawa.calendar import day_type, is_school_day
from ilawa.common import ROOT
from ilawa.zkm import load_bus_events

RES = ROOT / "results"
GOOD = M.GOOD
TERMINATING = {"2", "3", "4"}      # arrive at 079-00, leave again from 005-01
COUPLED = {"1": 1, "7": 0}          # arrival and departure are one trip (offset, min)
IC_FAMILY = {"IC", "EIP", "EIC", "TLK", "EC", "EN", "IC+"}
DIRECTION = {  # neighbour station -> direction group
    "Prabuty": "Malbork / Gdańsk", "Iława Miasto": "Malbork / Gdańsk", "Malbork": "Malbork / Gdańsk",
    "Działdowo": "Działdowo / Warszawa", "Smolniki": "Działdowo / Warszawa", "Warszawa Wschodnia": "Działdowo / Warszawa",
    "Jamielnik": "Jabłonowo / Toruń", "Jabłonowo Pomorskie": "Jabłonowo / Toruń",
    "Rudzienice Suskie": "Ostróda / Olsztyn", "Samborowo": "Ostróda / Olsztyn", "Ostróda": "Ostróda / Olsztyn",
}


def hhmm(m: float) -> str:
    m = int(round(m)) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


# ------------------------------------------------------------------ trips
def trips_for(d: dt.date) -> pd.DataFrame:
    """One row per bus event at Dworcowa with its pairing for layover rules."""
    ev = load_bus_events(day_type(d), school_day=is_school_day(d))
    rows = [{"line": e.line, "kind": e.kind, "t": float(e.minute), "stop": e.stop, "to": e.towards,
             "flags": e.flags} for e in ev]
    df = pd.DataFrame(rows).sort_values(["line", "kind", "t"]).reset_index(drop=True)
    df["id"] = df.index
    df["group"] = df["id"]           # events that move together share a group
    df["pair"] = -1                  # departure <-> preceding arrival (layover rule)
    for ln, off in COUPLED.items():
        a = df[(df.line == ln) & (df.kind == "arr")]
        dd = df[(df.line == ln) & (df.kind == "dep")]
        for i, r in a.iterrows():
            m = dd[np.isclose(dd.t, r.t + off)]
            if len(m):
                df.loc[m.index[0], "group"] = df.loc[i, "group"]
    for ln in TERMINATING:
        a = df[(df.line == ln) & (df.kind == "arr")].sort_values("t")
        dd = df[(df.line == ln) & (df.kind == "dep")].sort_values("t")
        used = set()
        for i, r in a.iterrows():
            cand = dd[(dd.t >= r.t + 1) & (dd.t <= r.t + 45) & ~dd.index.isin(used)]
            if len(cand):
                j = cand.index[0]
                used.add(j)
                df.loc[j, "pair"] = i
                df.loc[i, "pair"] = j
    return df


# ------------------------------------------------------------------ part 1: the key
def layovers(df: pd.DataFrame) -> dict:
    out = {}
    for ln in sorted(TERMINATING):
        dep = df[(df.line == ln) & (df.kind == "dep") & (df.pair >= 0)]
        lay = [r.t - df.loc[int(r.pair), "t"] for r in dep.itertuples()]
        n_dep = int(((df.line == ln) & (df.kind == "dep")).sum())
        if lay:
            out[ln] = {"n_dep": n_dep, "n_paired": len(lay), "median": float(np.median(lay)),
                       "p25": float(np.percentile(lay, 25)), "p75": float(np.percentile(lay, 75)),
                       "share_le_5": float(np.mean(np.array(lay) <= 5)), "values": [float(x) for x in lay]}
    return out


def regularity(df: pd.DataFrame) -> dict:
    out = {}
    for ln, g in df[df.kind == "dep"].groupby("line"):
        t = np.sort(g.t.to_numpy())
        if len(t) < 3:
            continue
        h = np.diff(t)
        mins = Counter(int(x) % 60 for x in t)
        top_min, top_n = mins.most_common(1)[0]
        out[ln] = {"n": int(len(t)), "headway_median": float(np.median(h)), "headway_min": float(h.min()),
                   "headway_max": float(h.max()), "headway_cv": float(h.std() / h.mean()),
                   "top_minute": int(top_min), "top_minute_share": top_n / len(t),
                   "distinct_minutes": len(mins)}
    return out


def group_fit(tr: pd.DataFrame, d: dt.date, m1: float, m2: float, n: int = 600) -> list[dict]:
    """Fit of the bus timetable to each train group, against the null model."""
    b = A.bus_sets(d)
    ba = np.concatenate(list(b["arr"].values()))
    bd = np.concatenate(list(b["dep"].values()))
    lo, hi = min(ba.min(), bd.min()), max(ba.max(), bd.max())
    arr = tr[(tr.arr_date == d) & tr.arr_min.between(lo, hi)].copy()
    dep = tr[(tr.dep_date == d) & tr.dep_min.between(lo, hi)].copy()
    arr["dir"] = arr.prev_station.map(DIRECTION).fillna("?")
    dep["dir"] = dep.next_station.map(DIRECTION).fillna("?")
    arr["grp"] = np.where(arr.category.isin(IC_FAMILY), "PKP Intercity", "POLREGIO")
    dep["grp"] = np.where(dep.category.isin(IC_FAMILY), "PKP Intercity", "POLREGIO")
    rows = []
    for key in ("dir", "grp"):
        for g in sorted(set(arr[key]) | set(dep[key])):
            ta = arr[arr[key] == g].arr_min.to_numpy()
            td = dep[dep[key] == g].dep_min.to_numpy()
            if len(ta) + len(td) < 4:
                continue
            obs = M.stats(ta if len(ta) else np.array([-999.]), td if len(td) else np.array([-999.]),
                          b["arr"], b["dep"], m1, m2)
            nul = M.null_model(ta if len(ta) else np.array([-999.]), td if len(td) else np.array([-999.]),
                               b["arr"], b["dep"], m1, m2, n=n, seed=5)
            c = M.compare(obs, nul)
            rows.append({"by": "kierunek" if key == "dir" else "przewoźnik", "group": g,
                         "n_arr": int(len(ta)), "n_dep": int(len(td)),
                         "t2b_good": c["t2b_good"] if len(ta) else None,
                         "t2b_near_miss": c["t2b_near_miss"] if len(ta) else None,
                         "b2t_good": c["b2t_good"] if len(td) else None,
                         "b2t_near_miss": c["b2t_near_miss"] if len(td) else None})
    return rows


# ------------------------------------------------------------------ part 2: changes
class Scorer:
    """Good train-line transfers for one date, per line, both directions."""

    def __init__(self, tr: pd.DataFrame, d: dt.date, df: pd.DataFrame, m1: float, m2: float):
        self.m1, self.m2 = m1, m2
        self.win = {}
        a = tr[(tr.arr_date == d) & tr.arr_min.notna()].sort_values("arr_min")
        b = tr[(tr.dep_date == d) & tr.dep_min.notna()].sort_values("dep_min")
        arr, dep = a.arr_min.to_numpy(), b.dep_min.to_numpy()
        la = (a.category + " " + a.number.astype(str) + " (" + a.arr_time.str[:5] + ", z " + a.prev_station.fillna("?") + ")").to_numpy()
        lb = (b.category + " " + b.number.astype(str) + " (" + b.dep_time.str[:5] + ", do " + b.next_station.fillna("?") + ")").to_numpy()
        self.ta, self.td, self.la, self.lb = {}, {}, {}, {}
        for ln, g in df.groupby("line"):  # service windows from the current timetable
            bd = g[g.kind == "dep"].t.to_numpy()
            ba = g[g.kind == "arr"].t.to_numpy()
            ma = (arr >= bd.min() - 60) & (arr <= bd.max()) if len(bd) else np.zeros(len(arr), bool)
            mb = (dep >= ba.min()) & (dep <= ba.max() + 60) if len(ba) else np.zeros(len(dep), bool)
            self.ta[ln], self.la[ln] = arr[ma], la[ma]
            self.td[ln], self.lb[ln] = dep[mb], lb[mb]

    def who(self, ln: str, bd0, ba0, bd1, ba1) -> dict:
        """Trains that gain or lose a good transfer with line ln."""
        out = {"gain": [], "lose": []}
        if len(self.ta[ln]) and len(bd0):
            g0 = M.train_to_bus(self.ta[ln], bd0, self.m1)["good"]
            g1 = M.train_to_bus(self.ta[ln], bd1, self.m1)["good"]
            out["gain"] += [f"z {x}" for x in self.la[ln][g1 & ~g0]]
            out["lose"] += [f"z {x}" for x in self.la[ln][g0 & ~g1]]
        if len(self.td[ln]) and len(ba0):
            g0 = M.bus_to_train(self.td[ln], ba0, self.m2)["good"]
            g1 = M.bus_to_train(self.td[ln], ba1, self.m2)["good"]
            out["gain"] += [f"na {x}" for x in self.lb[ln][g1 & ~g0]]
            out["lose"] += [f"na {x}" for x in self.lb[ln][g0 & ~g1]]
        return out

    def line_score(self, ln: str, bd: np.ndarray, ba: np.ndarray) -> tuple[int, int]:
        t2b = b2t = 0
        if len(bd) and len(self.ta[ln]):
            t2b = int(M.train_to_bus(self.ta[ln], bd, self.m1)["good"].sum())
        if len(ba) and len(self.td[ln]):
            b2t = int(M.bus_to_train(self.td[ln], ba, self.m2)["good"].sum())
        return t2b, b2t

    def total(self, df: pd.DataFrame) -> dict:
        out = {}
        for ln, g in df.groupby("line"):
            out[ln] = self.line_score(ln, g[g.kind == "dep"].t.to_numpy(), g[g.kind == "arr"].t.to_numpy())
        return out


class Plan:
    """Bus events as plain arrays for a fast search."""

    def __init__(self, df: pd.DataFrame):
        self.t = df.t.to_numpy(float).copy()
        self.line = df.line.to_numpy()
        self.kind = df.kind.to_numpy()
        self.pair = df.pair.to_numpy(int)
        self.group = df.group.to_numpy(int)
        n = len(self.t)
        self.members = {int(g): np.flatnonzero(self.group == g) for g in np.unique(self.group)}
        self.same = {i: np.flatnonzero((self.line == self.line[i]) & (self.kind == self.kind[i]) & (np.arange(n) != i))
                     for i in range(n)}
        self.by_line = {ln: np.flatnonzero(self.line == ln) for ln in np.unique(self.line)}

    def feasible(self, grp: int, delta: int) -> bool:
        idx = self.members[grp]
        new = self.t.copy()
        new[idx] += delta
        for i in idx:
            p = self.pair[i]
            if p >= 0:  # layover rule at Dworcowa
                if self.kind[i] == "dep" and new[i] < new[p] + 1:
                    return False
                if self.kind[i] == "arr" and new[i] > new[p] - 1:
                    return False
            o = self.same[i]
            if len(o):
                if np.min(np.abs(new[o] - new[i])) < 3:
                    return False
                if (self.t[o] < self.t[i]).sum() != (new[o] < new[i]).sum():
                    return False
        return True

    def line_arrays(self, ln: str, grp: int | None = None, delta: int = 0):
        idx = self.by_line[ln]
        t = self.t[idx].copy()
        if grp is not None:
            t[np.isin(idx, self.members[grp])] += delta
        k = self.kind[idx]
        return t[k == "dep"], t[k == "arr"]


def greedy(tr: pd.DataFrame, d: dt.date, m1: float, m2: float, max_shift: int, n_moves: int) -> dict:
    df = trips_for(d)
    sc = Scorer(tr, d, df, m1, m2)
    pl = Plan(df)
    base = sc.total(df)
    cur = {ln: sum(v) for ln, v in base.items()}
    moves, moved = [], set()
    for _ in range(n_moves):
        best = None
        for grp, idx in pl.members.items():
            if grp in moved:
                continue
            ln = pl.line[idx[0]]
            for delta in range(-max_shift, max_shift + 1):
                if delta == 0 or not pl.feasible(grp, delta):
                    continue
                bd, ba = pl.line_arrays(ln, grp, delta)
                s = sum(sc.line_score(ln, bd, ba))
                gain = s - cur[ln]
                if best is None or gain > best[0] or (gain == best[0] and abs(delta) < abs(best[2])):
                    best = (gain, grp, delta, s, ln)
        if best is None or best[0] <= 0:
            break
        gain, grp, delta, s, ln = best
        idx = pl.members[grp]
        bd0, ba0 = pl.line_arrays(ln)
        bd1, ba1 = pl.line_arrays(ln, grp, delta)
        moves.append({"line": str(ln), "events": [{"kind": str(pl.kind[i]), "stop": str(df.stop.iloc[i]),
                                                   "from": hhmm(pl.t[i]), "to": hhmm(pl.t[i] + delta),
                                                   "towards": str(df.to.iloc[i])} for i in idx],
                      "delta": int(delta), "gain": int(gain), "group": int(grp),
                      "who": sc.who(ln, bd0, ba0, bd1, ba1)})
        pl.t[idx] += delta
        moved.add(grp)
        cur[ln] = s
    df = df.copy()
    df["t"] = pl.t
    after = sc.total(df)
    return {"date": str(d), "day_type": day_type(d), "base": {k: list(v) for k, v in base.items()},
            "after": {k: list(v) for k, v in after.items()},
            "base_total": int(sum(sum(v) for v in base.values())),
            "after_total": int(sum(sum(v) for v in after.values())),
            "pairs_t2b": int(sum(len(v) for v in sc.ta.values())), "pairs_b2t": int(sum(len(v) for v in sc.td.values())),
            "moves": moves, "_df": df}


def apply_moves(d: dt.date, moves: list[dict]) -> pd.DataFrame:
    """Apply a plan made for one date to another date of the same day type."""
    df = trips_for(d)
    for mv in moves:
        for e in mv["events"]:
            h, m = map(int, e["from"].split(":"))
            sel = (df.line == mv["line"]) & (df.kind == e["kind"]) & np.isclose(df.t, h * 60 + m)
            df.loc[sel, "t"] += mv["delta"]
    return df


def realised(hist: pd.DataFrame, plans: dict, m1: float) -> dict:
    """Train -> bus good transfers with actual train arrivals, before and after the plan."""
    out = {}
    for dtp, plan in plans.items():
        before = after = n = 0
        for date_s in sorted(hist.date_arr.dropna().unique()):
            d = dt.date.fromisoformat(str(date_s)[:10])
            if d < A.BUS_TT_VALID_FROM or day_type(d) != dtp:
                continue
            g = hist[(hist.date_arr == date_s) & np.isfinite(hist.arr_min) & np.isfinite(hist.arr_delay)]
            if not len(g):
                continue
            act = (g.arr_min + g.arr_delay).to_numpy()
            df0, df1 = trips_for(d), apply_moves(d, plan["moves"])
            for ln in df0.line.unique():
                bd0 = df0[(df0.line == ln) & (df0.kind == "dep")].t.to_numpy()
                bd1 = df1[(df1.line == ln) & (df1.kind == "dep")].t.to_numpy()
                if not len(bd0):
                    continue
                sel = (g.arr_min >= bd0.min() - 60) & (g.arr_min <= bd0.max())
                a = act[sel.to_numpy()]
                if not len(a):
                    continue
                n += len(a)
                before += int(M.train_to_bus(a, bd0, m1)["good"].sum())
                after += int(M.train_to_bus(a, bd1, m1)["good"].sum())
        out[dtp] = {"pairs": n, "good_before": before, "good_after": after}
    return out


def curve(tr: pd.DataFrame, d: dt.date, m1: float, m2: float, n_moves: int = 40) -> dict:
    """Cumulative gain of the greedy search for more changes (max 3 and 5 min)."""
    out = {}
    for ms in (3, 5):
        p = greedy(tr, d, m1, m2, ms, n_moves)
        cum = [p["base_total"]]
        for mv in p["moves"]:
            cum.append(cum[-1] + mv["gain"])
        out[f"max{ms}"] = {"date": str(d), "curve": cum}
    return out


def whole_line(tr: pd.DataFrame, dates: list[dt.date], hist: pd.DataFrame, m1: float, m2: float,
               variants: dict | None = None) -> dict:
    """Best shift of each whole line on one date, then chosen variants on all weekdays and on actual delays."""
    d0 = next(d for d in dates if day_type(d) == "Roboczy" and d.weekday() in (1, 2, 3))
    df = trips_for(d0)
    sc = Scorer(tr, d0, df, m1, m2)
    base = sc.total(df)
    best = {}
    for ln in df.line.unique():
        g = df[df.line == ln]
        top = (0, 0)
        for delta in range(-15, 16):
            s = sum(sc.line_score(ln, g[g.kind == "dep"].t.to_numpy() + delta, g[g.kind == "arr"].t.to_numpy() + delta))
            if s - sum(base[ln]) > top[0]:
                top = (s - sum(base[ln]), delta)
        best[ln] = {"gain": int(top[0]), "shift": int(top[1])}
    variants = variants or {"line1": {"1": best["1"]["shift"]}, "line3": {"3": best["3"]["shift"]},
                            "line1+line3": {"1": best["1"]["shift"], "3": best["3"]["shift"]}}
    wk = [d for d in dates if day_type(d) == "Roboczy"]
    res = {"date": str(d0), "best_single_line": best, "variants": {}}
    for name, sh in variants.items():
        t0 = t1 = 0
        for d in wk:
            a0 = trips_for(d)
            s0 = Scorer(tr, d, a0, m1, m2)
            a1 = a0.copy()
            for ln, x in sh.items():
                a1.loc[a1.line == ln, "t"] += x
            t0 += sum(sum(v) for v in s0.total(a0).values())
            t1 += sum(sum(v) for v in s0.total(a1).values())
        n = b = a = 0
        for date_s in sorted(hist.date_arr.dropna().unique()):
            d = dt.date.fromisoformat(str(date_s)[:10])
            if d < A.BUS_TT_VALID_FROM or day_type(d) != "Roboczy":
                continue
            g = hist[(hist.date_arr == date_s) & np.isfinite(hist.arr_min) & np.isfinite(hist.arr_delay)]
            act = (g.arr_min + g.arr_delay).to_numpy()
            x0 = trips_for(d)
            for ln in x0.line.unique():
                bd0 = x0[(x0.line == ln) & (x0.kind == "dep")].t.to_numpy()
                if not len(bd0):
                    continue
                sel = ((g.arr_min >= bd0.min() - 60) & (g.arr_min <= bd0.max())).to_numpy()
                v = act[sel]
                n += len(v)
                b += int(M.train_to_bus(v, bd0, m1)["good"].sum())
                a += int(M.train_to_bus(v, bd0 + sh.get(ln, 0), m1)["good"].sum())
        res["variants"][name] = {"shifts": sh, "weekdays": len(wk), "planned_mean_before": t0 / len(wk),
                                 "planned_mean_after": t1 / len(wk),
                                 "realised_t2b": {"pairs": n, "good_before": b, "good_after": a}}
    return res


def ic_before_after(tr_unused, m1: float, m2: float, days=("2026-08-19", "2026-09-23"), n: int = 1000) -> dict:
    """Fit of the current weekday bus timetable to PKP Intercity trains before and after
    the PKP timetable correction of 2026-08-30 (spoznienia.me times, whole minutes)."""
    ic = A.load_ic_history()
    b = A.bus_sets(dt.date(2026, 9, 30))
    ba = np.concatenate(list(b["arr"].values()))
    bd = np.concatenate(list(b["dep"].values()))
    lo, hi = min(ba.min(), bd.min()), max(ba.max(), bd.max())
    out = {}
    for day in days:
        g = ic[ic.date == day]
        ta = g.arr_min.dropna()
        td = g.dep_min.dropna()
        ta, td = ta[(ta >= lo) & (ta <= hi)].to_numpy(), td[(td >= lo) & (td <= hi)].to_numpy()
        if not len(ta):
            continue
        obs = M.stats(ta, td, b["arr"], b["dep"], m1, m2)
        out[day] = {"n_arr": int(len(ta)), "n_dep": int(len(td)),
                    "comparison": M.compare(obs, M.null_model(ta, td, b["arr"], b["dep"], m1, m2, n=n, seed=4))}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--m-t2b", type=float, default=4)
    ap.add_argument("--m-b2t", type=float, default=5)
    ap.add_argument("--max-shift", type=int, default=5)
    ap.add_argument("--moves", type=int, default=10)
    ap.add_argument("--from-date", default="2026-09-30")
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--part", choices=["all", "key", "plan", "curve", "whole"], default="all")
    a = ap.parse_args()
    m1, m2 = a.m_t2b, a.m_b2t
    tr = A.load_planned_trains()
    start = dt.date.fromisoformat(a.from_date)
    dates = [start + dt.timedelta(days=i) for i in range(a.days)]
    reps: dict[str, dt.date] = {}
    for d in dates:
        if day_type(d) != "Roboczy" or d.weekday() in (1, 2, 3):
            reps.setdefault(day_type(d), d)
    hist = pd.concat([h for h in (A.load_ic_history(), A.load_monitor()) if len(h)], ignore_index=True)

    if a.part in ("all", "key"):
        key = {"layover": {}, "regularity": {}, "fit": {}}
        for dtp, d in reps.items():
            df = trips_for(d)
            key["layover"][dtp] = {k: {kk: vv for kk, vv in v.items() if kk != "values"} for k, v in layovers(df).items()}
            key["regularity"][dtp] = regularity(df)
            key["fit"][dtp] = group_fit(tr, d, m1, m2)
        ev = load_bus_events("Roboczy", school_day=True)
        key["school_trips"] = [{"line": e.line, "time": e.hhmm, "kind": e.kind, "flags": e.flags}
                               for e in ev if "s" in e.flags]
        key["ic_before_after"] = ic_before_after(tr, m1, m2)
        (RES / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
        print("key.json written")

    if a.part in ("all", "plan"):
        plans, rows = {}, []
        for dtp, d in reps.items():
            p = greedy(tr, d, m1, m2, a.max_shift, a.moves)
            p.pop("_df")
            checks = []
            for x in dates:
                if day_type(x) != dtp:
                    continue
                df0, df1 = trips_for(x), apply_moves(x, p["moves"])
                sc = Scorer(tr, x, df0, m1, m2)
                checks.append((sum(sum(v) for v in sc.total(df0).values()), sum(sum(v) for v in sc.total(df1).values())))
            p["check_all_days"] = {"n_days": len(checks), "base_mean": float(np.mean([c[0] for c in checks])),
                                   "after_mean": float(np.mean([c[1] for c in checks]))}
            plans[dtp] = p
            cum = p["base_total"]
            for i, mv in enumerate(p["moves"], 1):
                cum += mv["gain"]
                rows.append({"day_type": dtp, "rank": i, "line": mv["line"],
                             "change": "; ".join(f"{e['kind']} {e['stop']} {e['from']}→{e['to']}" for e in mv["events"]),
                             "delta_min": mv["delta"], "gain": mv["gain"], "cumulative": cum,
                             "gains": "; ".join(mv["who"]["gain"]), "losses": "; ".join(mv["who"]["lose"])})
        real = realised(hist, plans, m1)
        for dtp in plans:
            plans[dtp]["realised_t2b"] = real.get(dtp)
        (RES / "optimize.json").write_text(json.dumps(plans, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
        pd.DataFrame(rows).to_csv(RES / "optimize_moves.csv", index=False)
        for dtp, p in plans.items():
            print(dtp, p["date"], "good pairs", p["base_total"], "->", p["after_total"], "| all days",
                  round(p["check_all_days"]["base_mean"], 1), "->", round(p["check_all_days"]["after_mean"], 1),
                  "| realised", p["realised_t2b"])

    if a.part in ("all", "curve", "whole"):
        f = RES / "optimize_extra.json"
        extra = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        if a.part in ("all", "curve"):
            extra["curve"] = curve(tr, reps["Roboczy"], m1, m2)
        if a.part in ("all", "whole"):
            extra["whole_line"] = whole_line(tr, dates, hist, m1, m2)
        f.write_text(json.dumps(extra, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
        print("optimize_extra.json written")


if __name__ == "__main__":
    main()

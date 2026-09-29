"""Build the HTML report from the analysis results.

Run:  python report.py        (after analyze.py)
Output: reports/report_data.json and reports/przesiadki-ilawa.html
"""
from __future__ import annotations

import datetime as dt
import json
import math

import numpy as np
import pandas as pd

import analyze as A
from ilawa import model as M
from ilawa.calendar import day_type
from ilawa.common import ROOT

RES = ROOT / "results"
OUT = ROOT / "reports"
DIRS = {"t2b": "pociąg→autobus", "b2t": "autobus→pociąg"}


def clean(x):
    """JSON-safe values: NaN -> None, numpy -> python, rounded floats."""
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return None if math.isnan(x) or math.isinf(x) else round(float(x), 4)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x


def hhmm(m: float) -> str:
    m = int(round(m))
    return f"{m // 60:02d}:{m % 60:02d}"


def main() -> None:
    s = json.loads((RES / "summary.json").read_text(encoding="utf-8"))
    p = s["params"]
    m1, m2 = p["m_t2b"], p["m_b2t"]
    reps = {k: dt.date.fromisoformat(v) for k, v in s["representative_dates"].items()}
    tr = A.load_planned_trains()
    data: dict = {"generated": dt.date.today().isoformat(), "params": p,
                  "rep_dates": {k: str(v) for k, v in reps.items()}}

    # Overall comparison with the null model (representative dates).
    data["overall"] = {}
    for r in s["null"]:
        key = "weighted" if r["weighted"] else "plain"
        data["overall"].setdefault(r["day_type"], {})[key] = {
            "n_train_arr": r["n_train_arr"], "n_train_dep": r["n_train_dep"], **r["comparison"]}
        if r.get("best_shifts"):
            data.setdefault("best_shifts", {})[r["day_type"]] = r["best_shifts"]

    # Pooled static results over all planned dates.
    data["static"] = {dtp: {k: {"all": v[d]["all"], "weighted": v[d]["capacity_weighted"],
                                "by_category": v[d]["by_category"], "by_neighbour": v[d]["by_neighbour"]}
                            for k, d in DIRS.items()} | {"bus_arr": v["bus_arr"], "bus_dep": v["bus_dep"]}
                      for dtp, v in s["static"].items()}
    data["per_line"] = s["per_line"]
    data["sensitivity"] = s["sensitivity"]

    # Offset histograms in 2-minute bins, -20..+30 min, with the flat random level.
    data["hist"] = {}
    for dtp, h in s["histograms"].items():
        out = {}
        for key in ("bus_dep_minus_train_arr", "train_dep_minus_bus_arr"):
            bins, counts = np.array(h[key]["bins"]), np.array(h[key]["counts"])
            level = counts.sum() / len(counts) * 2
            edges = list(range(-20, 30, 2))
            out[key] = {"edges": edges, "counts": [int(counts[(bins >= e) & (bins < e + 2)].sum()) for e in edges],
                        "random_level": level}
        data["hist"][dtp] = {"date": h["date"], **out}

    # Timeline and collisions for each representative date.
    data["timeline"], data["collisions"] = {}, {}
    for dtp, d in reps.items():
        b = A.bus_sets(d)
        arr = tr[(tr["arr_date"] == d) & tr["arr_min"].notna()].sort_values("arr_min")
        dep = tr[(tr["dep_date"] == d) & tr["dep_min"].notna()].sort_values("dep_min")
        trains = [{"t": round(r.arr_min, 1), "time": r.arr_time[:5], "cat": r.category, "no": r.number,
                   "rel": r.relation, "from": r.prev_station} for r in arr.itertuples()]
        trains_dep = [{"t": round(r.dep_min, 1), "time": r.dep_time[:5], "cat": r.category, "no": r.number,
                       "rel": r.relation, "to": r.next_station} for r in dep.itertuples()]
        buses = [{"t": e.minute, "time": e.hhmm, "line": e.line, "kind": e.kind, "to": e.towards,
                  "flags": e.flags} for e in b["events"]]
        data["timeline"][dtp] = {"date": str(d), "trains_arr": trains, "trains_dep": trains_dep, "buses": buses}
        coll = []
        for ln, bd in b["dep"].items():
            bd = np.sort(bd)
            for i, x in enumerate(bd):
                nxt = bd[i + 1] if i + 1 < len(bd) else None
                gap = (nxt - x) if nxt is not None else None
                hit = arr[(arr["arr_min"] > x - m1) & (arr["arr_min"] <= x + M.NEAR_BEFORE)]
                if len(hit) and (gap is None or gap >= 30):
                    ev = next(e for e in b["events"] if e.kind == "dep" and e.line == ln and e.minute == x)
                    coll.append({"line": ln, "time": hhmm(x), "to": ev.towards, "next": hhmm(nxt) if nxt is not None else None,
                                 "gap": gap, "trains": [{"time": r.arr_time[:5], "cat": r.category, "no": r.number,
                                                         "rel": r.relation} for r in hit.itertuples()],
                                 "seats": int(hit["capacity"].sum())})
        data["collisions"][dtp] = sorted(coll, key=lambda c: c["time"])

    # Dynamic analysis.
    dyn = {}
    for name in ("ic_history", "plk_monitor"):
        f = RES / f"dynamic_{name}.csv"
        if not f.exists():
            continue
        x = pd.read_csv(f)
        t2b = x[x["direction"] == "pociąg→autobus"]
        b2t = x[x["direction"] == "autobus→pociąg"]
        cats = {}
        for c, g in t2b.groupby("category"):
            dl = g["delay"].to_numpy(float)
            cats[c] = {"n": int(len(g)), "median": float(np.median(dl)), "p75": float(np.percentile(dl, 75)),
                       "p90": float(np.percentile(dl, 90)), "share_over_5": float((dl > 5).mean())}
        dyn[name] = {"days": sorted(x["date"].unique().tolist()), "delay_by_category": cats,
                     "t2b": s["dynamic"][name].get("pociąg→autobus"), "b2t": s["dynamic"][name].get("autobus→pociąg"),
                     "per_line_t2b": s["dynamic"][name].get("per_line_t2b")}
        fl = RES / f"dynamic_per_line_{name}.csv"
        if fl.exists():
            y = pd.read_csv(fl)
            y = y[y["planned_good"]]
            dyn[name]["keep_by_line_category"] = {
                f"{ln}|{c}": {"n": int(len(g)), "p_keep": float(g["p_keep"].mean())}
                for (ln, c), g in y.groupby(["line", "category"]) if len(g) >= 5}
    data["dynamic"] = dyn

    # Timetable key and ranked changes (optimize.py).
    for name, key in (("key.json", "key"), ("optimize.json", "opt"), ("optimize_extra.json", "opt_extra")):
        f = RES / name
        if f.exists():
            data[key] = json.loads(f.read_text(encoding="utf-8"))

    # Counts for the header.
    data["counts"] = {dtp: {"trains": int(((tr["arr_date"] == d) | (tr["dep_date"] == d)).sum()),
                            "bus_dep": sum(len(v) for v in A.bus_sets(d)["dep"].values()),
                            "bus_arr": sum(len(v) for v in A.bus_sets(d)["arr"].values())}
                      for dtp, d in reps.items()}
    OUT.mkdir(exist_ok=True)
    js = json.dumps(clean(data), ensure_ascii=False, separators=(",", ":"))
    (OUT / "report_data.json").write_text(js, encoding="utf-8")
    tpl = (OUT / "template.html").read_text(encoding="utf-8")
    (OUT / "przesiadki-ilawa.html").write_text(tpl.replace("/*__DATA__*/null", js), encoding="utf-8")
    print(f"report data {len(js) / 1024:.0f} kB -> {OUT / 'przesiadki-ilawa.html'}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Test the current and the proposed bus timetables on past days, with the
actual times of the trains.

Each result.json of zkmplan.py gives two timetables: the current one and the
proposal (all stages). The test uses every past day in the period that has
delay data (PKP PLK monitor, PKP Intercity history). The output lists, for each
day, timetable and good window, the good transfers (train, line) with the
planned and with the actual train times, and names the worst day of each day
type (the fewest good transfers with the actual times, current timetable).

Usage:
    python3 past_test.py --days 30 --windows 5,10 data/plan/scenariusze/*/result.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import pandas as pd

import analyze as A
from ilawa.plan import pasttest as PT

ROOT = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", nargs="*", help="result.json files of zkmplan.py (default: newest run)")
    ap.add_argument("--days", type=int, default=30, help="number of past days to test (up to yesterday)")
    ap.add_argument("--to-date", default=None, help="last day of the period (default: yesterday)")
    ap.add_argument("--windows", default="5,10", help="good windows to test (min), comma separated")
    ap.add_argument("--variant", default="single", choices=["single", "interlined"])
    ap.add_argument("--m-t2b", type=float, default=4)
    ap.add_argument("--m-b2t", type=float, default=5)
    ap.add_argument("--out", default=str(ROOT / "results" / "test_na_minionych_dniach"))
    a = ap.parse_args()
    files = [Path(p) for p in a.results] or sorted((ROOT / "data" / "plan").glob("*/result.json"))[-1:]
    if not files:
        raise SystemExit("No result.json. Run zkmplan.py first.")
    timetables = {}
    for f in files:
        R = json.loads(f.read_text(encoding="utf-8"))
        name = f.parent.name
        timetables.setdefault("obecny", {d: x["bus_rep"] for d, x in R["day_types"].items()})
        timetables[name] = {d: x["variants"][a.variant]["bus_after"] for d, x in R["day_types"].items()
                            if a.variant in x["variants"]}
    tr = A.load_planned_trains()
    delays = PT.delay_table(A.load_ic_history(), A.load_monitor())
    last = dt.date.fromisoformat(a.to_date) if a.to_date else dt.date.today() - dt.timedelta(days=1)
    first = last - dt.timedelta(days=a.days - 1)
    days = PT.past_days(delays, first, last)
    feed_start = min(dt.date.fromisoformat(json.loads(f.read_text())["feed"]["feed_start_date"][:4] + "-" +
                                           json.loads(f.read_text())["feed"]["feed_start_date"][4:6] + "-" +
                                           json.loads(f.read_text())["feed"]["feed_start_date"][6:8]) for f in files)
    days = [d for d in days if d >= feed_start]
    if not days:
        raise SystemExit("No past day with delay data in the period.")
    windows = [float(w) for w in a.windows.split(",")]
    rows = pd.DataFrame(PT.evaluate(timetables, tr, delays, days, windows, a.m_t2b, a.m_b2t))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows.to_csv(out / "dni.csv", index=False)
    summ = (rows.groupby(["day_type", "timetable", "window"])[["plan_total", "actual_total", "actual_t2b", "actual_b2t"]]
            .mean().round(1).reset_index())
    summ.to_csv(out / "podsumowanie.csv", index=False)
    cur = rows[rows["timetable"] == "obecny"]
    worst = {}
    for (dtp, w), g in cur.groupby(["day_type", "window"]):
        r = g.sort_values("actual_total").iloc[0]
        worst[f"{dtp}|{w:g}"] = {"date": r["date"], "actual_total": int(r["actual_total"]),
                                 "plan_total": int(r["plan_total"]), "delay_mean": r["delay_mean"],
                                 "late_over_5": int(r["late_over_5"])}
    meta = {"days": [str(d) for d in days], "files": [str(f) for f in files], "windows": windows,
            "worst_day": worst}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"Days: {', '.join(str(d) for d in days)}")
    print(summ.to_string(index=False))
    print(json.dumps(worst, ensure_ascii=False, indent=1, default=str))
    print(f"Results in {out}")


if __name__ == "__main__":
    main()

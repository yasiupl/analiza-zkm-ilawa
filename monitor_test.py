#!/usr/bin/env python3
"""Past-day test with the measured train times from the monitor (data/monitor/*.csv).

Same method as the test on past days (ilawa.plan.pasttest): pairs train-line, walk 4 min from the platform and
5 min to the platform, good transfer up to 5 min of waiting, transfer up to 10 min of waiting, and the
„runaway” departures (the bus leaves from 2 min before the train arrives until the travellers reach the stop,
and the next bus of the line is 30 min or more later). Only the trains have measured times: buses keep the
planned times, because ZKM publishes no data on actual departures.

Current timetable: bus_rep of a zkmplan.py run (result.json). Proposal: the same timetable with all the changes
from the published list of changes (wykaz-zmian.csv, single-line blocks, all stages).

Writes results/pomiar/: dni.csv (all trains, a train without data counts as on time),
dni-tylko-zmierzone.csv (only the measured trains) and walidacja-wrzesien.csv (check against the published
test on 19 days of September). print_reports.py reads dni.csv and dni-tylko-zmierzone.csv for the section
„Pomiar faktycznych przyjazdów”; publish_site.py copies dni.csv as pomiar-dni.csv.

Usage:
    python3 monitor_test.py --plan data/plan/2026-09-30/result.json --changes data/plan/2026-09-30/wykaz-zmian-opublikowany.csv
"""
import argparse
import collections
import csv
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

import analyze as A
from ilawa.calendar import day_type
from ilawa.plan import pasttest as PT

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results" / "pomiar"
ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--plan", default=str(ROOT / "data" / "plan" / "2026-09-30" / "result.json"), help="result.json of zkmplan.py")
ap.add_argument("--changes", default=str(ROOT / "data" / "plan" / "2026-09-30" / "wykaz-zmian-opublikowany.csv"),
                help="list of changes (wykaz-zmian.csv of the published page)")
args = ap.parse_args()

R = json.load(open(args.plan, encoding="utf-8"))
cur = {d: x["bus_rep"] for d, x in R["day_types"].items()}


def tomin(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


prop = {d: [dict(b) for b in v] for d, v in cur.items()}
idx = {d: {b["id"]: b for b in v} for d, v in prop.items()}
nchg = collections.Counter()
for r in csv.DictReader(open(args.changes, encoding="utf-8")):
    if r["wariant_obiegow"] != "single":
        continue
    b = idx[r["typ_dnia"]].get(r["kurs"])
    if b is None:
        print("missing trip", r["kurs"])
        continue
    if r["nowy_przyjazd"]:
        b["arr"] = tomin(r["nowy_przyjazd"])
    if r["nowy_odjazd"]:
        b["dep"] = tomin(r["nowy_odjazd"])
    nchg[r["typ_dnia"]] += 1
print("changes applied", dict(nchg))
TT = {'obecny': cur, 'propozycja': prop}

tr = A.load_planned_trains()
mon = A.load_monitor()
delays_mon = PT.delay_table(None, mon)          # monitor only
delays_all = PT.delay_table(A.load_ic_history(), mon)

M1, M2 = 4.0, 5.0
def runaway(bus, arr, key):
    """Pairs train-line: bus leaves from arrival-2 to arrival+M1 and next bus of the line >= 30 min later."""
    deps = collections.defaultdict(list)
    for b in bus:
        if b.get('dep') is not None: deps[b['route']].append(b['dep'])
    n = 0
    for v in deps.values():
        v = np.sort(np.array(v, float))
        for x in arr:
            a = x[key]
            hit = v[(v >= a - 2) & (v < a + M1)]
            if len(hit):
                nxt = v[v >= a + M1]
                if len(nxt) == 0 or nxt[0] - hit[-1] >= 30 or nxt[0] - a - M1 >= 30 - 0:
                    # next departure the traveller can still reach is >= 30 min after the missed one
                    if len(nxt) == 0 or nxt[0] - hit[-1] >= 30: n += 1
    return n

def evaluate(days, delays, only_measured=False):
    rows = []
    for d in days:
        dtp = day_type(d)
        arr, dep = PT.actual_trains(tr, d, delays)
        if only_measured:
            arr = [x for x in arr if x['src'] == 'PKP PLK']; dep = [x for x in dep if x['src'] == 'PKP PLK']
        dl = [x['delay'] for x in arr if x['delay'] is not None]
        info = dict(date=str(d), day_type=dtp, trains_arr=len(arr), arr_measured=sum(x['src'] == 'PKP PLK' for x in arr),
                    dep_measured=sum(x['src'] == 'PKP PLK' for x in dep), trains_dep=len(dep),
                    delay_mean=float(np.mean(dl)) if dl else None, delay_median=float(np.median(dl)) if dl else None,
                    late_over_5=int(sum(v > 5 for v in dl)))
        for name, tt in TT.items():
            bus = tt.get(dtp)
            for w in (5.0, 10.0):
                pl = PT.count(bus, arr, dep, M1, M2, w, 't'); ac = PT.count(bus, arr, dep, M1, M2, w, 'act')
                rows.append({**info, 'timetable': name, 'window': w, 'plan_total': pl['total'], 'actual_total': ac['total'],
                             'plan_runaway': runaway(bus, arr, 't'), 'actual_runaway': runaway(bus, arr, 'act')})
    return pd.DataFrame(rows)

if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    # validation on September (published: 19 days 11-29.09, monitor + spoznienia.me)
    sept = [dt.date(2026, 9, 11) + dt.timedelta(days=i) for i in range(19)]
    v = evaluate(sept, delays_all)
    v.to_csv(OUT / 'walidacja-wrzesien.csv', index=False)
    print(v[(v.date == '2026-09-11')][['timetable', 'window', 'plan_total', 'actual_total', 'plan_runaway']].to_string())
    days = sorted({dt.date.fromisoformat(f) for f in mon['operating_date'].unique()})
    out = evaluate(days, delays_mon)
    out.to_csv(OUT / 'dni.csv', index=False)
    om = evaluate(days, delays_mon, only_measured=True)
    om.to_csv(OUT / 'dni-tylko-zmierzone.csv', index=False)
    print('days', [str(d) for d in days])

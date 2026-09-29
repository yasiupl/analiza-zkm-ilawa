"""One-time data collection for the Iława Główna transfer study.

Run:  python collect.py [--days-back 90]

Output (in data/raw):
  plk/schedules_<from>_<to>.json.gz   planned trains at the station, all carriers
  plk/operations_<timestamp>.json.gz  real-time snapshot: planned, actual, delay
  plk/history_probe.json              test: how far back /operations/train gives data
  spoznienia/<date>.json              historical station board, PKP Intercity only
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
import urllib.parse

from ilawa.common import (RAW, SPOZNIENIA_BASE, STATION_NAME, find_station_id, http_get,
                          plk_get, save_json)

SPOZNIENIA_FIRST_DAY = dt.date(2025, 10, 23)


def collect_plk(days_back: int) -> None:
    sid = find_station_id()
    print(f"PLK station ID for {STATION_NAME}: {sid}")
    today = dt.date.today()
    windows = []
    start = today - dt.timedelta(days=days_back)
    while start < today:
        end = min(start + dt.timedelta(days=30), today - dt.timedelta(days=1))
        windows.append((start, end))
        start = end + dt.timedelta(days=1)
    windows.append((today, today + dt.timedelta(days=30)))
    for a, b in windows:
        try:
            js, rl = plk_get("/schedules", {"dateFrom": a.isoformat(), "dateTo": b.isoformat(),
                                            "stations": sid, "dictionaries": "true",
                                            "fullRoute": "true"}, timeout=300)
            p = save_json(js, RAW / "plk" / f"schedules_{a}_{b}.json", compress=True)
            print(f"  schedules {a}..{b}: {len(js.get('routes', []))} routes -> {p.name} {rl}")
        except Exception as e:  # the API can refuse past windows; continue
            print(f"  schedules {a}..{b}: FAILED {e}")
        time.sleep(1)

    js, rl = plk_get("/operations", {"stations": sid, "withPlanned": "true", "pageSize": 5000})
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    p = save_json(js, RAW / "plk" / f"operations_{stamp}.json", compress=True)
    print(f"  operations now: {len(js.get('trains', []))} trains -> {p.name} {rl}")

    # History probe: does /operations/train give data for past days?
    probe = []
    for t in js.get("trains", [])[:2]:
        for back in (1, 3, 7, 14, 30):
            day = (today - dt.timedelta(days=back)).isoformat()
            path = f"/operations/train/{t['scheduleId']}/{t.get('trainOrderId') or t['orderId']}/{day}"
            try:
                r, _ = plk_get(path)
                sts = (r.get("stations") or []) if isinstance(r, dict) else []
                actual = sum(1 for s in sts if s.get("actualDeparture") or s.get("actualArrival"))
                probe.append({"path": path, "ok": True, "stations": len(sts), "with_actual": actual})
            except Exception as e:
                probe.append({"path": path, "ok": False, "error": str(e)[:200]})
            time.sleep(1)
    save_json(probe, RAW / "plk" / "history_probe.json")
    ok = [x for x in probe if x.get("ok") and x.get("with_actual")]
    print(f"  history probe: {len(ok)}/{len(probe)} past-day calls gave actual times")


def collect_spoznienia(days_back: int) -> None:
    out = RAW / "spoznienia"
    out.mkdir(parents=True, exist_ok=True)
    name = urllib.parse.quote(STATION_NAME)
    today = dt.date.today()
    n_new = 0
    for back in range(1, days_back + 1):
        day = today - dt.timedelta(days=back)
        if day < SPOZNIENIA_FIRST_DAY:
            break
        f = out / f"{day}.json"
        if f.exists() and f.stat().st_size > 2:
            continue
        status, body, _ = http_get(f"{SPOZNIENIA_BASE}/stations/{name}/schedule", {"date": day.isoformat()})
        if status == 200:
            f.write_bytes(body)
            n_new += 1
        else:
            print(f"  spoznienia {day}: HTTP {status}")
        time.sleep(3.2)  # service limit: 20 requests per minute
    print(f"  spoznienia.me: {n_new} new days in {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days-back", type=int, default=90)
    ap.add_argument("--skip-plk", action="store_true")
    ap.add_argument("--skip-spoznienia", action="store_true")
    a = ap.parse_args()
    if not a.skip_plk:
        print("PKP PLK open data API")
        try:
            collect_plk(min(a.days_back, 62))
        except Exception as e:  # continue with the other source
            print(f"  PLK FAILED: {e}")
    if not a.skip_spoznienia:
        print("spoznienia.me (PKP Intercity history)")
        collect_spoznienia(a.days_back)
    print("Done.")


if __name__ == "__main__":
    sys.exit(main())

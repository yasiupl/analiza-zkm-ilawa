"""Live monitoring of actual train times at Iława Główna (PKP PLK open data API).

Run continuously:        python monitor.py
Run one poll (cron):     python monitor.py --once
Export CSV only:         python monitor.py --export

Each poll reads /operations for the station and keeps the last known planned
and actual times per train in data/monitor.sqlite. Raw responses go to
data/raw/plk/ops/<date>/ (not versioned). Daily CSV files go to
data/monitor/<date>.csv (versioned).

API limits (basic key): 100 requests per hour, 1000 per day. The default
interval (5 min by day, 20 min at night) uses about 230 requests per day.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sqlite3
import sys
import time
import traceback

from ilawa.common import DATA, RAW, find_station_id, plk_get, save_json
from ilawa.plk import now_iso, operation_rows, schedule_rows

DB = DATA / "monitor.sqlite"
OUT = DATA / "monitor"
LOG = DATA.parent / "logs" / "monitor.log"

SCHEMA = """
CREATE TABLE IF NOT EXISTS routes (
  operating_date TEXT, schedule_id INT, order_id INT, train_order_id INT,
  carrier TEXT, number TEXT, category TEXT, name TEXT,
  origin TEXT, destination TEXT, prev_station TEXT, next_station TEXT,
  arr_time TEXT, dep_time TEXT, platform_arr TEXT, platform_dep TEXT, stop_type TEXT,
  PRIMARY KEY (operating_date, schedule_id, order_id, train_order_id));
CREATE TABLE IF NOT EXISTS obs (
  operating_date TEXT, schedule_id INT, order_id INT, train_order_id INT,
  status TEXT, planned_arr TEXT, planned_dep TEXT, actual_arr TEXT, actual_dep TEXT,
  arr_delay INT, dep_delay INT, confirmed INT, cancelled INT,
  first_seen TEXT, last_seen TEXT, n_seen INT,
  PRIMARY KEY (operating_date, schedule_id, order_id, train_order_id));
CREATE TABLE IF NOT EXISTS polls (ts TEXT PRIMARY KEY, trains INT, rows INT, limits TEXT, error TEXT);
"""

UPSERT_OBS = """
INSERT INTO obs VALUES (:operating_date,:schedule_id,:order_id,:train_order_id,:status,
  :planned_arr,:planned_dep,:actual_arr,:actual_dep,:arr_delay,:dep_delay,:confirmed,:cancelled,
  :seen_at,:seen_at,1)
ON CONFLICT(operating_date, schedule_id, order_id, train_order_id) DO UPDATE SET
  status=excluded.status,
  planned_arr=COALESCE(excluded.planned_arr, planned_arr),
  planned_dep=COALESCE(excluded.planned_dep, planned_dep),
  actual_arr=COALESCE(excluded.actual_arr, actual_arr),
  actual_dep=COALESCE(excluded.actual_dep, actual_dep),
  arr_delay=COALESCE(excluded.arr_delay, arr_delay),
  dep_delay=COALESCE(excluded.dep_delay, dep_delay),
  confirmed=COALESCE(excluded.confirmed, confirmed),
  cancelled=COALESCE(excluded.cancelled, cancelled),
  last_seen=excluded.last_seen, n_seen=n_seen+1
"""


def log(msg: str) -> None:
    line = f"[{now_iso()}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    return con


def refresh_routes(con: sqlite3.Connection, sid: int, day: dt.date) -> int:
    """Load planned routes for day and day+1 when the database has none for day."""
    have = con.execute("SELECT COUNT(*) FROM routes WHERE operating_date=?", (day.isoformat(),)).fetchone()[0]
    if have:
        return 0
    js, _ = plk_get("/schedules", {"dateFrom": day.isoformat(),
                                   "dateTo": (day + dt.timedelta(days=1)).isoformat(),
                                   "stations": sid, "fullRoute": "true", "dictionaries": "true"},
                    timeout=300)
    rows = schedule_rows(js, sid)
    cols = ["operating_date", "schedule_id", "order_id", "train_order_id", "carrier", "number",
            "category", "name", "origin", "destination", "prev_station", "next_station",
            "arr_time", "dep_time", "platform_arr", "platform_dep", "stop_type"]
    con.executemany(f"INSERT OR REPLACE INTO routes ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [[r.get(c) for c in cols] for r in rows])
    con.commit()
    log(f"routes loaded for {day}..{day + dt.timedelta(days=1)}: {len(rows)} rows")
    return len(rows)


def poll(con: sqlite3.Connection, sid: int) -> dict:
    ts = now_iso()
    js, limits = plk_get("/operations", {"stations": sid, "withPlanned": "true", "pageSize": 5000})
    day = ts[:10]
    save_json(js, RAW / "plk" / "ops" / day / f"{ts[11:].replace(':', '')}.json", compress=True)
    rows = list(operation_rows(js, sid, ts))
    con.executemany(UPSERT_OBS, rows)
    con.execute("INSERT OR REPLACE INTO polls VALUES (?,?,?,?,?)",
                (ts, len(js.get("trains", [])), len(rows), str(limits), None))
    con.commit()
    return {"trains": len(js.get("trains", [])), "rows": len(rows), "limits": limits}


def export_day(con: sqlite3.Connection, day: str) -> int:
    q = """
    SELECT r.operating_date, r.carrier, r.category, r.number, r.name, r.origin, r.destination,
           r.prev_station, r.next_station, r.arr_time, r.dep_time, r.platform_arr, r.platform_dep,
           o.status, o.planned_arr, o.planned_dep, o.actual_arr, o.actual_dep,
           o.arr_delay, o.dep_delay, o.confirmed, o.cancelled, o.first_seen, o.last_seen, o.n_seen,
           r.schedule_id, r.order_id, r.train_order_id
    FROM routes r LEFT JOIN obs o
      ON o.operating_date=r.operating_date AND o.schedule_id=r.schedule_id
     AND o.order_id=r.order_id AND o.train_order_id=r.train_order_id
    WHERE r.operating_date=?
    ORDER BY COALESCE(r.dep_time, r.arr_time)"""
    cur = con.execute(q, (day,))
    cols = [c[0] for c in cur.description]
    rows = cur.fetchall()
    if not rows:
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / f"{day}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    return len(rows)


def interval_for(now: dt.datetime, day_s: int, night_s: int) -> int:
    return night_s if 1 <= now.hour < 4 else day_s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=int, default=300, help="seconds between polls by day")
    ap.add_argument("--night-interval", type=int, default=1200, help="seconds between polls 01:00-04:00")
    ap.add_argument("--once", action="store_true", help="one poll, then exit (for cron or Task Scheduler)")
    ap.add_argument("--export", action="store_true", help="write CSV files for all days, then exit")
    a = ap.parse_args()
    con = connect()
    if a.export:
        days = [r[0] for r in con.execute("SELECT DISTINCT operating_date FROM routes ORDER BY 1")]
        for d in days:
            print(d, export_day(con, d))
        return
    sid = find_station_id()
    while True:
        now = dt.datetime.now()
        try:
            refresh_routes(con, sid, now.date())
            if now.hour < 4:  # trains of the previous operating day can still run
                refresh_routes(con, sid, now.date() - dt.timedelta(days=1))
            r = poll(con, sid)
            n = export_day(con, now.date().isoformat())
            if now.hour < 4:
                export_day(con, (now.date() - dt.timedelta(days=1)).isoformat())
            log(f"poll ok: {r['trains']} trains, {r['rows']} station rows, csv {n} rows, {r['limits']}")
            hourly = next((v for k, v in r["limits"].items() if "hour" in k.lower() and "remaining" in k.lower()), None)
            if hourly is not None and int(hourly) < 3:
                wait = 3600 - now.minute * 60 - now.second + 30
                log(f"hourly limit nearly used; sleep {wait} s")
                time.sleep(wait)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log(f"poll error: {e}")
            con.execute("INSERT OR REPLACE INTO polls VALUES (?,?,?,?,?)",
                        (now_iso(), None, None, None, traceback.format_exc()[-500:]))
            con.commit()
        if a.once:
            break
        time.sleep(interval_for(dt.datetime.now(), a.interval, a.night_interval))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("stopped")

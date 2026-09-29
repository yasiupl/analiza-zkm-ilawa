"""Build a GTFS feed of ZKM Iława from the public timetable on rozklad.com.

Run:  python zkm_gtfs.py [--refresh] [--pause 0.4]

Output:
  data/zkm_gtfs/*.txt and data/zkm_gtfs.zip   the GTFS feed
  data/zkm_gtfs/_report.json                  counts and checks
  data/raw/zkm/cache/                         downloaded pages (not versioned)

The feed is unofficial. Check it against the timetable of ZKM before you use it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import Counter

from ilawa import gtfs_write
from ilawa.common import DATA, RAW
from ilawa.zkm_scrape import DAY_TYPES, Client, Scraper


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client", default="ILAWA_ZKM")
    ap.add_argument("--refresh", action="store_true", help="download the pages again (ignore the cache)")
    ap.add_argument("--pause", type=float, default=0.4, help="seconds between requests")
    ap.add_argument("--days", type=int, default=365, help="feed validity in days")
    a = ap.parse_args()
    client = Client(a.client, RAW / "zkm" / "cache", pause=a.pause, refresh=a.refresh)
    sc = Scraper(client)
    print(f"{len(sc.stops)} stops, {len(sc.patterns)} patterns, lines {', '.join(sc.lines)}")
    all_trips, report = [], {"day_types": {}}
    for lp, name in DAY_TYPES.items():
        trips, stats = sc.trips(lp)
        all_trips += trips
        per_line = Counter(t.line for t in trips)
        # Check: departures at the first stop of each pattern against trips starting there.
        checks = []
        for pid, p in sc.patterns.items():
            first = p["stops"][0]["id"]
            tt = sc.stop_timetable(first, p["line"])["days"].get(name, [])
            n_trips = sum(1 for t in trips if t.pattern == pid and t.stops[0][0] == first)
            checks.append({"pattern": pid, "line": p["line"], "first_stop": first,
                           "timetable": len(tt), "trips": n_trips})
        report["day_types"][name] = {**stats, "per_line": dict(per_line), "first_stop_check": checks}
        print(f"{name}: {len(trips)} trips {dict(per_line)}; added from coverage {stats['added_from_coverage']}, "
              f"unexplained {stats['n_unexplained']}, requests so far {client.requests}")
    vf = None
    for p in sc.patterns.values():
        vf = sc.stop_timetable(p["stops"][0]["id"], p["line"])["valid_from"]
        if vf:
            break
    valid_from = dt.date.fromisoformat(vf) if vf else dt.date.today()
    feed = gtfs_write.build(all_trips, sc.stops, sc.patterns, valid_from, a.days)
    out = DATA / "zkm_gtfs"
    gtfs_write.write(feed, out, DATA / "zkm_gtfs.zip")
    report.update({"valid_from": str(valid_from), "trips": len(all_trips), "stops": len(feed["stops.txt"]),
                   "feed_version": feed["feed_info.txt"][0]["feed_version"],
                   "patterns": {pid: {"line": p["line"], "headsign": p["headsign"], "stops": [s["id"] for s in p["stops"]]}
                                for pid, p in sc.patterns.items()}})
    (out / "_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"GTFS written to {out} and {DATA / 'zkm_gtfs.zip'} (valid from {valid_from}, {len(all_trips)} trips)")


if __name__ == "__main__":
    main()

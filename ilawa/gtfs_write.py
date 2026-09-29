"""Write a GTFS feed from trips rebuilt by zkm_scrape."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import math
import zipfile
from pathlib import Path

from .calendar import public_holidays

SERVICE = {1: "RO", 2: "SO", 3: "ND"}


def _t(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}:00"


def school_breaks(year_from: int) -> list[tuple[dt.date, dt.date]]:
    """Summer break only by default; add winter breaks in config if needed."""
    return [(dt.date(year_from + 1, 6, 27), dt.date(year_from + 1, 8, 31))]


def fill_positions(stops: dict, patterns: dict) -> dict:
    """Stops without coordinates: the mean of stops with the same name, else a point
    between the neighbouring stops of a pattern (in proportion to their order)."""
    out = {k: dict(v) for k, v in stops.items()}
    for p in patterns.values():
        for x in p["stops"]:
            out.setdefault(x["id"], {"id": x["id"], "name": x["name"], "lat": float("nan"), "lon": float("nan")})
    known = lambda sid: sid in out and not math.isnan(out[sid]["lat"])
    for sid, s in out.items():
        if not known(sid):
            same = [x for x in out.values() if x["name"] == s["name"] and not math.isnan(x["lat"])]
            if same:
                s["lat"] = sum(x["lat"] for x in same) / len(same)
                s["lon"] = sum(x["lon"] for x in same) / len(same)
    for _ in range(3):
        for p in patterns.values():
            ids = [x["id"] for x in p["stops"]]
            for i, sid in enumerate(ids):
                if known(sid):
                    continue
                a = next((j for j in range(i - 1, -1, -1) if known(ids[j])), None)
                b = next((j for j in range(i + 1, len(ids)) if known(ids[j])), None)
                if a is not None and b is not None:
                    f = (i - a) / (b - a)
                    out[sid]["lat"] = out[ids[a]]["lat"] + f * (out[ids[b]]["lat"] - out[ids[a]]["lat"])
                    out[sid]["lon"] = out[ids[a]]["lon"] + f * (out[ids[b]]["lon"] - out[ids[a]]["lon"])
                    out[sid]["estimated"] = True
    return out


def build(trips: list, stops: dict, patterns: dict, valid_from: dt.date, days: int = 365,
          agency: dict | None = None, extra_breaks: list[tuple[dt.date, dt.date]] | None = None) -> dict[str, list[dict]]:
    agency = agency or {"agency_id": "ZKM", "agency_name": "ZKM Iława",
                        "agency_url": "https://zkm.miastoilawa.pl/", "agency_timezone": "Europe/Warsaw",
                        "agency_lang": "pl"}
    end = valid_from + dt.timedelta(days=days - 1)
    used = {s for tr in trips for s, _, _ in tr.stops}
    stops = fill_positions(stops, patterns)
    # Stops without coordinates get the mean of stops with the same name.
    rows_stops = []
    for sid in sorted(used):
        s = stops.get(sid, {"name": sid, "lat": float("nan"), "lon": float("nan")})
        lat, lon = s["lat"], s["lon"]
        if math.isnan(lat):
            same = [x for x in stops.values() if x["name"] == s["name"] and not math.isnan(x["lat"])]
            if same:
                lat = sum(x["lat"] for x in same) / len(same)
                lon = sum(x["lon"] for x in same) / len(same)
        rows_stops.append({"stop_id": sid, "stop_code": sid, "stop_name": s["name"],
                           "stop_lat": f"{lat:.6f}" if not math.isnan(lat) else "",
                           "stop_lon": f"{lon:.6f}" if not math.isnan(lon) else ""})
    lines = sorted({tr.line for tr in trips}, key=lambda x: (len(x), x))
    rows_routes = [{"route_id": ln, "agency_id": agency["agency_id"], "route_short_name": ln,
                    "route_long_name": "", "route_type": "3"} for ln in lines]
    dir_of = {}
    for ln in lines:  # first pattern of a line = direction 0, the others = direction 1
        for i, pid in enumerate(pid for pid, p in patterns.items() if p["line"] == ln):
            dir_of[pid] = "0" if i == 0 else "1"
    rows_trips, rows_st, count = [], [], {}
    for tr in trips:
        # "s" = only on school days; it matters on working days only.
        svc = SERVICE[tr.lpday] + ("_S" if "s" in tr.flags and tr.lpday == 1 else "")
        base = f"{tr.line}_{svc}_{_t(tr.stops[0][1])[:5].replace(':', '')}_{tr.stops[0][0]}"
        count[base] = count.get(base, 0) + 1
        tid = base if count[base] == 1 else f"{base}_{count[base]}"
        rows_trips.append({"route_id": tr.line, "service_id": svc, "trip_id": tid,
                           "trip_headsign": tr.headsign, "direction_id": dir_of.get(tr.pattern, "0"),
                           "trip_short_name": tr.flags, "pattern_id": tr.pattern})
        for i, (sid, m, _) in enumerate(tr.stops, 1):
            rows_st.append({"trip_id": tid, "arrival_time": _t(m), "departure_time": _t(m),
                            "stop_id": sid, "stop_sequence": str(i)})
    svc_used = sorted({r["service_id"] for r in rows_trips})
    wd = {"RO": "1111100", "RO_S": "1111100", "SO": "0000010", "ND": "0000001"}
    rows_cal = [{"service_id": s, **dict(zip(["monday", "tuesday", "wednesday", "thursday", "friday",
                                             "saturday", "sunday"], wd[s])),
                 "start_date": valid_from.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")} for s in svc_used]
    rows_cd = []
    hol = set()
    for y in range(valid_from.year, end.year + 1):
        hol |= {h for h in public_holidays(y) if valid_from <= h <= end}
    for h in sorted(hol):
        if h.weekday() == 6:
            continue
        for s in svc_used:
            if s in ("RO", "RO_S", "SO"):
                rows_cd.append({"service_id": s, "date": h.strftime("%Y%m%d"), "exception_type": "2"})
        if "ND" in svc_used:
            rows_cd.append({"service_id": "ND", "date": h.strftime("%Y%m%d"), "exception_type": "1"})
    if "RO_S" in svc_used:
        for a, b in school_breaks(valid_from.year) + (extra_breaks or []):
            d = a
            while d <= b:
                if d.weekday() < 5 and valid_from <= d <= end and d not in hol:
                    rows_cd.append({"service_id": "RO_S", "date": d.strftime("%Y%m%d"), "exception_type": "2"})
                d += dt.timedelta(days=1)
    digest = hashlib.sha1("".join(f"{r['trip_id']}{r['departure_time']}{r['stop_id']}" for r in rows_st).encode()).hexdigest()[:10]
    feed = {"agency.txt": [agency], "stops.txt": rows_stops, "routes.txt": rows_routes, "trips.txt": rows_trips,
            "stop_times.txt": rows_st, "calendar.txt": rows_cal, "calendar_dates.txt": rows_cd,
            "feed_info.txt": [{"feed_publisher_name": "ilawa-transport (nieoficjalny, dane z rozklad.com)",
                               "feed_publisher_url": "https://rozklad.com/maps/index.php?IDKlienta=ILAWA_ZKM",
                               "feed_lang": "pl", "feed_start_date": valid_from.strftime("%Y%m%d"),
                               "feed_end_date": end.strftime("%Y%m%d"), "feed_version": digest}]}
    return feed


def write(feed: dict[str, list[dict]], folder: Path, zip_path: Path | None = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    blobs = {}
    for name, rows in feed.items():
        buf = io.StringIO()
        if rows:
            w = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        blobs[name] = buf.getvalue()
        (folder / name).write_text(blobs[name], encoding="utf-8")
    if zip_path:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for name, text in blobs.items():
                z.writestr(name, text)

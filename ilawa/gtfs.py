"""Extract the trains that call at one station from a GTFS feed.

Source feed: https://mkuran.pl/gtfs/polish_trains.zip (all Polish railway
operators, built from PKP PLK data). The feed is large, so the extract reads
stop_times.txt as a stream and keeps only the trips that call at the station.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import zipfile
from pathlib import Path


def _rows(z: zipfile.ZipFile, name: str):
    if name not in z.namelist():
        return
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))


def _hms_to_min(s: str) -> int | None:
    if not s:
        return None
    h, m, *_ = s.split(":")
    return int(h) * 60 + int(m)


def extract_station(zip_path: Path, station_name: str, date_from: dt.date, date_to: dt.date) -> list[dict]:
    """Return one row per (service date, trip) with the call at station_name."""
    z = zipfile.ZipFile(zip_path)
    stops = {r["stop_id"]: r for r in _rows(z, "stops.txt")}
    names = {sid: r.get("stop_name", "") for sid, r in stops.items()}
    target = {sid for sid, r in stops.items() if r.get("stop_name", "").strip() == station_name}
    target |= {sid for sid, r in stops.items() if r.get("parent_station") in target}
    if not target:
        raise SystemExit(f"GTFS: no stop named {station_name!r}")

    # Pass 1: trips that call at the station.
    calls: dict[str, dict] = {}
    for r in _rows(z, "stop_times.txt"):
        if r["stop_id"] in target:
            calls[r["trip_id"]] = r
    # Pass 2: first, previous, next and last stop of those trips.
    seq: dict[str, list] = {t: [] for t in calls}
    for r in _rows(z, "stop_times.txt"):
        if r["trip_id"] in seq:
            seq[r["trip_id"]].append((int(r["stop_sequence"]), r["stop_id"]))
    trips = {r["trip_id"]: r for r in _rows(z, "trips.txt") if r["trip_id"] in calls}
    routes = {r["route_id"]: r for r in _rows(z, "routes.txt")}
    agencies = {r["agency_id"]: r for r in _rows(z, "agency.txt")}

    # Service calendar.
    services: dict[str, set] = {}
    for r in _rows(z, "calendar.txt"):
        a = dt.datetime.strptime(r["start_date"], "%Y%m%d").date()
        b = dt.datetime.strptime(r["end_date"], "%Y%m%d").date()
        wd = [r[k] == "1" for k in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")]
        d = max(a, date_from)
        while d <= min(b, date_to):
            if wd[d.weekday()]:
                services.setdefault(r["service_id"], set()).add(d)
            d += dt.timedelta(days=1)
    for r in _rows(z, "calendar_dates.txt"):
        d = dt.datetime.strptime(r["date"], "%Y%m%d").date()
        if not (date_from <= d <= date_to):
            continue
        s = services.setdefault(r["service_id"], set())
        (s.add if r["exception_type"] == "1" else s.discard)(d)

    out = []
    for trip_id, c in calls.items():
        t = trips.get(trip_id, {})
        rt = routes.get(t.get("route_id", ""), {})
        order = [sid for _, sid in sorted(seq[trip_id])]
        i = order.index(c["stop_id"])
        arr = _hms_to_min(c.get("arrival_time", "")) if i > 0 else None
        dep = _hms_to_min(c.get("departure_time", "")) if i + 1 < len(order) else None
        base = {
            "trip_id": trip_id,
            "agency": agencies.get(rt.get("agency_id", ""), {}).get("agency_name", rt.get("agency_id")),
            "agency_id": rt.get("agency_id"),
            "route_short_name": rt.get("route_short_name"),
            "route_long_name": rt.get("route_long_name"),
            "number": t.get("trip_short_name") or t.get("train_number") or "",
            "headsign": t.get("trip_headsign"),
            "origin": names.get(order[0]), "destination": names.get(order[-1]),
            "prev_station": names.get(order[i - 1]) if i > 0 else None,
            "next_station": names.get(order[i + 1]) if i + 1 < len(order) else None,
            "platform": stops.get(c["stop_id"], {}).get("platform_code"),
        }
        for d in sorted(services.get(t.get("service_id", ""), ())):
            row = dict(base)
            row["service_date"] = d.isoformat()
            # GTFS times can exceed 24:00; keep them relative to the service date.
            row["arr_min"] = arr
            row["dep_min"] = dep
            out.append(row)
    return out

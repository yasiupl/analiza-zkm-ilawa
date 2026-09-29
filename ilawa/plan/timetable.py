"""Read a GTFS feed into simple trip objects for one service day."""
from __future__ import annotations

import csv
import datetime as dt
import io
import math
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s or "")).strip().lower()


def to_min(hms: str) -> int:
    h, m, *_ = hms.split(":")
    return int(h) * 60 + int(m)


def hhmm(m: float) -> str:
    m = int(round(m))
    return f"{m // 60:02d}:{m % 60:02d}"


def dist_m(a: dict, b: dict) -> float:
    """Great-circle distance in metres between two stops (dicts with lat, lon)."""
    if any(math.isnan(x) for x in (a["lat"], a["lon"], b["lat"], b["lon"])):
        return float("nan")
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


@dataclass
class Trip:
    id: str
    route: str
    service: str
    headsign: str
    direction: str
    flags: str
    stops: list = field(default_factory=list)   # [(stop_id, minute)]

    @property
    def start(self) -> int:
        return self.stops[0][1]

    @property
    def end(self) -> int:
        return self.stops[-1][1]

    @property
    def first(self) -> str:
        return self.stops[0][0]

    @property
    def last(self) -> str:
        return self.stops[-1][0]


class Feed:
    def __init__(self, path: Path):
        self.path = Path(path)
        tables = self._read_all()
        self.stops = {}
        for r in tables.get("stops.txt", []):
            try:
                lat, lon = float(r.get("stop_lat") or "nan"), float(r.get("stop_lon") or "nan")
            except ValueError:
                lat = lon = float("nan")
            self.stops[r["stop_id"]] = {"id": r["stop_id"], "name": r.get("stop_name", ""), "lat": lat, "lon": lon}
        self.routes = {r["route_id"]: r for r in tables.get("routes.txt", [])}
        self.trips: dict[str, Trip] = {}
        for r in tables.get("trips.txt", []):
            self.trips[r["trip_id"]] = Trip(r["trip_id"], r["route_id"], r["service_id"], r.get("trip_headsign", ""),
                                            r.get("direction_id", ""), r.get("trip_short_name", ""))
        for r in sorted(tables.get("stop_times.txt", []), key=lambda r: (r["trip_id"], int(r["stop_sequence"]))):
            t = self.trips.get(r["trip_id"])
            if t is not None:
                t.stops.append((r["stop_id"], to_min(r["departure_time"] or r["arrival_time"])))
        self.calendar = tables.get("calendar.txt", [])
        self.calendar_dates = tables.get("calendar_dates.txt", [])
        self.feed_info = (tables.get("feed_info.txt") or [{}])[0]

    def _read_all(self) -> dict[str, list[dict]]:
        out = {}
        if self.path.suffix == ".zip":
            with zipfile.ZipFile(self.path) as z:
                for n in z.namelist():
                    if n.endswith(".txt"):
                        out[Path(n).name] = list(csv.DictReader(io.TextIOWrapper(z.open(n), encoding="utf-8-sig")))
        else:
            for f in self.path.glob("*.txt"):
                with f.open(encoding="utf-8-sig") as fh:
                    out[f.name] = list(csv.DictReader(fh))
        return out

    def services_on(self, d: dt.date) -> set[str]:
        ds = d.strftime("%Y%m%d")
        wd = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][d.weekday()]
        on = {r["service_id"] for r in self.calendar if r["start_date"] <= ds <= r["end_date"] and r.get(wd) == "1"}
        for r in self.calendar_dates:
            if r["date"] == ds:
                (on.add if r["exception_type"] == "1" else on.discard)(r["service_id"])
        return on

    def trips_on(self, d: dt.date) -> list[Trip]:
        on = self.services_on(d)
        return sorted((t for t in self.trips.values() if t.service in on and len(t.stops) >= 2),
                      key=lambda t: (t.start, t.route))

    def station_stops(self, name: str) -> set[str]:
        return {sid for sid, s in self.stops.items() if norm(s["name"]) == norm(name)}

    def trip_km(self, t: Trip, factor: float) -> float:
        km = 0.0
        for (a, _), (b, _) in zip(t.stops, t.stops[1:]):
            d = dist_m(self.stops[a], self.stops[b]) if a in self.stops and b in self.stops else float("nan")
            km += 0 if math.isnan(d) else d * factor / 1000
        return km

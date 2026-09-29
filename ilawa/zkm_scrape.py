"""Read the ZKM Iława timetable from rozklad.com and rebuild complete trips.

Sources on rozklad.com (all public):
  data2json.php?co=stopsLatLng   stops with coordinates
  data2json.php?co=linie         stop patterns of each line and direction
  rozQR.php?nrStop&LpDay&Time    the next 6 departures from a stop, each with all
                                 later stops of the trip and their times
  r7xp.php?cmd=rozID&ID&IDLinii  the full stop timetable of one line at one stop

Method: read every departure from the first stop of each pattern (rozQR, moving
the time forward), then compare every stop timetable (r7xp) with the trips found.
A departure that no trip explains is read from that stop, so trips that start in
the middle of a route are found too. Pages are kept in a cache folder.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from .common import http_get
from .minidom import parse

BASE = "https://rozklad.com/maps"
DAY_TYPES = {1: "Roboczy", 2: "Sobota", 3: "Niedziela"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "")
    return re.sub(r"\s+", " ", s).strip().lower()


def hm(t: int) -> str:
    return f"{t // 60:02d}:{t % 60:02d}"


class Client:
    """HTTP access with a disk cache and a pause between requests."""

    def __init__(self, client_id: str, cache: Path, pause: float = 0.4, refresh: bool = False):
        self.client_id = client_id
        self.cache = cache
        self.pause = pause
        self.refresh = refresh
        self.requests = 0
        cache.mkdir(parents=True, exist_ok=True)

    def get(self, path: str, params: dict) -> str:
        q = urllib.parse.urlencode({"IDKlienta": self.client_id, **params})
        url = f"{BASE}/{path}?{q}"
        f = self.cache / (hashlib.sha1(url.encode()).hexdigest()[:20] + ".html")
        if f.exists() and not self.refresh:
            return f.read_text(encoding="utf-8")
        status, body, _ = http_get(url, headers={"Accept": "text/html,application/json"})
        if status != 200:
            raise RuntimeError(f"HTTP {status} for {url}")
        text = body.decode("utf-8", "replace")
        f.write_text(text, encoding="utf-8")
        self.requests += 1
        time.sleep(self.pause)
        return text


# ------------------------------------------------------------------ parsers
def parse_stops(text: str) -> dict[str, dict]:
    out = {}
    for r in json.loads(text):
        try:
            lat, lon = float(r.get("Lat") or "nan"), float(r.get("Lng") or "nan")
        except ValueError:
            lat = lon = float("nan")
        out[r["ID"]] = {"id": r["ID"], "name": re.sub(r"\s+", " ", r.get("Nazwa", "")).strip(),
                        "lat": lat, "lon": lon, "lines": r.get("Linie", "")}
    return out


def parse_patterns(text: str) -> dict[str, dict]:
    """Stop patterns keyed by pattern id (IDKursu), in the order of the data."""
    out: dict[str, dict] = {}
    for r in json.loads(text):
        pid = r["IDKursu"]
        p = out.setdefault(pid, {"id": pid, "line": str(r["IDLinii"]), "dir": r.get("KierAB", ""),
                                 "headsign": re.sub(r"\s+", " ", r.get("Opis", "")).strip(), "stops": []})
        p["stops"].append({"id": r["ID"], "name": re.sub(r"\s+", " ", r.get("Nazwa", "")).strip()})
    return out


TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([a-z]*)")


def parse_rozqr(html: str) -> list[dict]:
    """Departures listed on a rozQR page, each with the rest of its trip."""
    doc = parse(html)
    deps = []
    for div in doc.find_all("div", id_prefix="collapseBus"):
        a = doc.find("a", href="#" + div.attrs.get("id", ""))
        if a is None:
            continue
        line = a.text().strip()
        tr = a.closest("tr")
        info = tr.text(" ") if tr is not None else ""
        m = re.search(r"\bo\s+(\d{1,2}):(\d{2})\s*([a-z]*)", info)
        heads = tr.find_all("span", cls="text-danger") if tr is not None else []
        headsign = heads[-1].text().strip() if heads else ""
        rows = []
        for row in div.find_all("tr"):
            tds = row.find_all("td")
            if len(tds) < 3:
                continue
            tm = TIME_RE.search(tds[1].text())
            if not tm:
                continue
            rows.append({"t": int(tm.group(1)) * 60 + int(tm.group(2)), "flags": tm.group(3),
                         "name": re.sub(r"\s+", " ", tds[2].text()).strip()})
        if not rows:
            continue
        t0 = int(m.group(1)) * 60 + int(m.group(2)) if m else rows[0]["t"]
        deps.append({"line": line, "t": t0, "flags": (m.group(3) if m else rows[0]["flags"]) or "",
                     "headsign": headsign, "rows": rows})
    return deps


def parse_stop_timetable(html: str) -> dict:
    """Departure times of one line at one stop for each day type (r7xp rozID page)."""
    doc = parse(html)
    text = doc.text(" ")
    k = re.search(r"Kierunek:\s*(.+?)\s+Ważny od:\s*(\d{4}-\d{2}-\d{2})", text)
    leg = re.search(r"Legenda:\s*(.*?)\s*Trasa:", text)
    tol = re.search(r"Tolerancja:\s*(.*?)\s*Legenda", text)
    days = {}
    for b in doc.find_all("button", cls="schowaj"):
        name = b.text().strip()
        box = doc.find(id=(b.attrs.get("data-bs-target") or "").lstrip("#"))
        if box is None or name not in DAY_TYPES.values():
            continue
        times = []
        for li in box.find_all("li", cls="odjazd-list"):
            g, mn = li.find("span", cls="godz"), li.find("span", cls="min")
            if g is None or mn is None:
                continue
            h = int(g.text().strip())
            for x in re.finditer(r"(\d{2})([a-zA-Z]*)", mn.text()):
                times.append((h * 60 + int(x.group(1)), x.group(2)))
        days[name] = times
    return {"direction": k.group(1).strip() if k else None, "valid_from": k.group(2) if k else None,
            "legend": leg.group(1).strip() if leg else None, "tolerance": tol.group(1).strip() if tol else None,
            "days": days}


# ------------------------------------------------------------------ trips
@dataclass
class Trip:
    line: str
    pattern: str
    lpday: int
    flags: str
    headsign: str
    stops: list = field(default_factory=list)   # [(stop_id, minute, flags)]
    partial_start: bool = False

    @property
    def key(self):
        return (self.line, self.lpday, self.stops[0][0], self.stops[0][1])


class Scraper:
    def __init__(self, client: Client, log=print):
        self.c = client
        self.log = log
        self.stops = parse_stops(client.get("data2json.php", {"co": "stopsLatLng"}))
        self.patterns = parse_patterns(client.get("data2json.php", {"co": "linie"}))
        self.lines = sorted({p["line"] for p in self.patterns.values()}, key=lambda x: (len(x), x))

    # -- mapping of stop names in a trip to stop ids
    def _align(self, pattern: dict, start_stop: str, rows: list[dict]) -> tuple[float, list]:
        best = (-1.0, [])
        idx = [i for i, s in enumerate(pattern["stops"]) if s["id"] == start_stop]
        for i0 in idx:
            k, ids, hit = i0, [], 0
            for j, r in enumerate(rows):
                found = None
                for kk in range(k, min(k + 8, len(pattern["stops"]))):
                    if norm(pattern["stops"][kk]["name"]) == norm(r["name"]):
                        found = kk
                        break
                if j == 0 and found != i0:
                    found = i0  # the first row is the stop we asked for
                if found is None:
                    ids.append(None)
                else:
                    ids.append(pattern["stops"][found]["id"])
                    hit += 1
                    k = found + 1
            score = hit / len(rows)
            if score > best[0]:
                best = (score, ids)
        return best

    def _nearest_by_name(self, name: str, prev: str | None) -> str | None:
        cands = [s for s in self.stops.values() if norm(s["name"]) == norm(name)]
        if not cands:
            return None
        if prev and prev in self.stops and not math.isnan(self.stops[prev]["lat"]):
            p = self.stops[prev]
            cands.sort(key=lambda s: (s["lat"] - p["lat"]) ** 2 + ((s["lon"] - p["lon"]) * 0.6) ** 2
                       if not math.isnan(s["lat"]) else 9e9)
        return cands[0]["id"]

    def _trip_from(self, dep: dict, start_stop: str, lpday: int) -> Trip | None:
        pats = [p for p in self.patterns.values() if p["line"] == dep["line"] and
                any(s["id"] == start_stop for s in p["stops"])]
        if not pats:
            return None
        scored = []
        for p in pats:
            sc, ids = self._align(p, start_stop, dep["rows"])
            scored.append((sc + (0.01 if norm(p["headsign"]) == norm(dep["headsign"]) else 0), p, ids))
        scored.sort(key=lambda x: -x[0])
        _, p, ids = scored[0]
        stops, prev = [], None
        for r, sid in zip(dep["rows"], ids):
            sid = sid or self._nearest_by_name(r["name"], prev)
            if sid is None:
                self.log(f"  ! unknown stop name {r['name']!r} (line {dep['line']})")
                continue
            if stops and r["t"] < stops[-1][1]:
                break  # guard against a jump over midnight
            stops.append((sid, r["t"], r["flags"]))
            prev = sid
        if len(stops) < 2:
            return None
        return Trip(dep["line"], p["id"], lpday, dep["flags"], dep["headsign"] or p["headsign"], stops,
                    partial_start=p["stops"][0]["id"] != start_stop)

    def departures(self, stop: str, lpday: int, t: int) -> list[dict]:
        html = self.c.get("rozQR.php", {"nrStop": stop, "LpDay": lpday, "Time": hm(t)})
        return parse_rozqr(html)

    def sweep(self, stop: str, lpday: int) -> list[dict]:
        """All departures from a stop in one day. The page shows 6 departures from
        the given time (inclusive), so the next request starts at the last minute shown."""
        out, t, seen = [], 0, set()
        while t < 24 * 60:
            deps = self.departures(stop, lpday, t)
            if not deps:
                break
            new = [d for d in deps if (d["line"], d["t"], d["flags"], d["headsign"]) not in seen]
            for d in new:
                seen.add((d["line"], d["t"], d["flags"], d["headsign"]))
                out.append(d)
            last = max(d["t"] for d in deps)
            t = last if new and last > t else last + 1
        return out

    def stop_timetable(self, stop: str, line: str) -> dict:
        return parse_stop_timetable(self.c.get("r7xp.php", {"cmd": "rozID", "ID": stop, "IDLinii": line}))

    def trips(self, lpday: int) -> tuple[list[Trip], dict]:
        trips: dict = {}
        firsts = sorted({p["stops"][0]["id"] for p in self.patterns.values()})
        for f in firsts:
            for d in self.sweep(f, lpday):
                tr = self._trip_from(d, f, lpday)
                if tr and tr.key not in trips and not tr.partial_start:
                    trips[tr.key] = tr
        # Coverage: every departure in the stop timetables must belong to a trip.
        added, unexplained = 0, []
        for line in self.lines:
            pats = [p for p in self.patterns.values() if p["line"] == line]
            order = []
            for p in pats:
                for s in p["stops"][1:-1]:
                    if s["id"] not in order:
                        order.append(s["id"])
            for s in order:
                tt = self.stop_timetable(s, line)["days"].get(DAY_TYPES[lpday], [])
                have = {st[1] for tr in trips.values() if tr.line == line for st in tr.stops if st[0] == s}
                for t, flags in tt:
                    if t in have:
                        continue
                    deps = [d for d in self.departures(s, lpday, t) if d["line"] == line and d["t"] == t]
                    tr = self._trip_from(deps[0], s, lpday) if deps else None
                    if tr and tr.key not in trips:
                        tr.partial_start = True
                        trips[tr.key] = tr
                        have |= {st[1] for st in tr.stops if st[0] == s}
                        added += 1
                    else:
                        unexplained.append((line, s, hm(t), flags))
        stats = {"trips": len(trips), "added_from_coverage": added, "unexplained": unexplained[:50],
                 "n_unexplained": len(unexplained), "requests": self.c.requests}
        return sorted(trips.values(), key=lambda x: (x.line, x.stops[0][1])), stats

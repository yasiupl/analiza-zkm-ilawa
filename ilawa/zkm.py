"""ZKM Iława bus events at stop "Dworcowa" (railway station Iława Główna).

Stop 005-01: buses leave the station toward the city (lines 1, 2, 3, 4, 7).
Stop 079-00: buses arrive from the city. Lines 2, 3, 4 end here. Line 1 continues
             to Piaskowa (it leaves the station 1 min later). Line 7 is a loop
             and calls only at 005-01 (arrival = departure).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .common import DATA

DAY_TYPES = ("Roboczy", "Sobota", "Niedziela")

# Assumed capacity (all places, seated + standing) of a 12 m city bus.
BUS_CAPACITY = 90


@dataclass(frozen=True)
class BusEvent:
    kind: str        # "arr" (bus reaches the station) or "dep" (bus leaves the station)
    line: str
    stop: str
    minute: int      # minutes after midnight
    flags: str       # timetable letters, e.g. "c", "k", "s"
    towards: str

    @property
    def hhmm(self) -> str:
        return f"{self.minute // 60:02d}:{self.minute % 60:02d}"


def _parse(tok: str) -> tuple[int, str]:
    t, _, flags = tok.partition("/")
    h, m = t.split(":")
    return int(h) * 60 + int(m), flags


def load_bus_events(day_type: str, school_day: bool = True,
                    path: Path | None = None) -> list[BusEvent]:
    """Return arrival and departure events at Dworcowa for one day type."""
    d = json.loads((path or DATA / "zkm_dworcowa.json").read_text(encoding="utf-8"))
    ev: list[BusEvent] = []
    for key, v in d.items():
        if key.startswith("_") or day_type not in v:
            continue
        line, stop = key.split("@")
        for tok in v[day_type].split():
            minute, flags = _parse(tok)
            if "s" in flags and not school_day:
                continue
            if stop == "005-01":
                ev.append(BusEvent("dep", line, stop, minute, flags, v["kier"]))
                if line == "7":  # loop line: it also brings passengers to the station
                    ev.append(BusEvent("arr", line, stop, minute, flags, "Dworcowa"))
            elif stop == "079-00":
                ev.append(BusEvent("arr", line, stop, minute, flags, "Dworcowa"))
                if line == "1":  # continues to Piaskowa from the second Dworcowa stop
                    ev.append(BusEvent("dep", line, stop, minute + 1, flags, "Piaskowa"))
    # Remove duplicates (the timetable lists some trips twice, once with "s").
    uniq = {(e.kind, e.line, e.minute): e for e in ev}
    return sorted(uniq.values(), key=lambda e: (e.minute, e.kind, e.line))

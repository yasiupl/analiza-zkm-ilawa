"""Driver duties (służby) cut from vehicle blocks, and the break rules.

Rules for drivers of regular lines up to 50 km (ustawa o czasie pracy kierowców,
art. 31a-31b): with more than 6 h of driving in a day a break of at least 30 min,
with more than 8 h at least 45 min. A break may be split into parts of at least
15 min, and the parts must be in the timetable. Here a layover of at least
`break_part` minutes counts as a break part.

A block is cut into duties at relief points (by default the station stop). The
cut is made at the last relief point before the duty would become longer than
`max_duty` minutes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .blocks import Block
from .timetable import Feed, norm


@dataclass
class Duty:
    block: int
    trips: list = field(default_factory=list)
    prep: int = 15                                   # minutes before the first trip
    dh: list = field(default_factory=list)           # empty-run minutes between trips

    @property
    def start(self) -> int:
        return self.trips[0].start - self.prep

    @property
    def end(self) -> int:
        return self.trips[-1].end

    @property
    def length(self) -> int:
        return self.end - self.start

    @property
    def driving(self) -> int:
        return sum(t.end - t.start for t in self.trips)

    def layovers(self) -> list[int]:
        """Rest time between trips (the gap minus the empty run)."""
        dh = self.dh or [0] * (len(self.trips) - 1)
        return [b.start - a.end - d for (a, b), d in zip(zip(self.trips, self.trips[1:]), dh)]


def cut_duties(feed: Feed, blocks: list[Block], relief: set[str], max_duty: int = 510,
               prep: int = 15) -> list[Duty]:
    relief = {norm(r) for r in relief}
    duties = []
    for b in blocks:
        dh = b.dh or [0] * (len(b.trips) - 1)
        cur, cur_dh = [b.trips[0]], []
        last_cut = None                               # index in cur after which we may cut
        for t, d in zip(b.trips[1:], dh):
            prev = cur[-1]
            if norm(feed.stops.get(prev.last, {}).get("name", "")) in relief:
                last_cut = len(cur)
            if t.end - (cur[0].start - prep) > max_duty and last_cut:
                duties.append(Duty(b.id, cur[:last_cut], prep, cur_dh[:last_cut - 1]))
                cur, cur_dh = cur[last_cut:] + [t], cur_dh[last_cut:] + [d]
                last_cut = None
            else:
                cur.append(t)
                cur_dh.append(d)
        duties.append(Duty(b.id, cur, prep, cur_dh))
    return duties


def check_breaks(d: Duty, part: int = 15, need_6h: int = 30, need_8h: int = 45, max_driving: int = 600) -> dict:
    parts = [x for x in d.layovers() if x >= part]
    total = sum(parts)
    need = need_8h if d.driving > 480 else need_6h if d.driving > 360 else 0
    return {"driving": d.driving, "length": d.length, "break_parts": parts, "break_total": total,
            "break_needed": need, "ok_breaks": total >= need, "ok_driving": d.driving <= max_driving}

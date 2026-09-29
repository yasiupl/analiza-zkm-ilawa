"""Vehicle blocks (obiegi): which trips one bus runs in a day.

ZKM does not publish its blocks, so they are rebuilt from the timetable. A bus
can take a trip when it waits at the same terminal (same stop, or a stop with
the same name within a radius) for at least the minimum layover. It can also
drive empty to another terminal (deadhead) up to `max_deadhead_km` when the gap
is long enough. Trips are taken in time order; the bus that waited longest takes
the next trip, and a bus of the same line comes first.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .timetable import Feed, Trip, dist_m, norm


@dataclass
class Block:
    id: int
    trips: list = field(default_factory=list)       # Trip objects in order
    dh: list = field(default_factory=list)          # deadhead minutes before trip k+1 (len = len(trips) - 1)
    req: list = field(default_factory=list)         # smallest allowed gap before trip k+1

    @property
    def start(self) -> int:
        return self.trips[0].start

    @property
    def end(self) -> int:
        return self.trips[-1].end


def same_terminal(feed: Feed, a: str, b: str, radius_m: float) -> bool:
    if a == b:
        return True
    sa, sb = feed.stops.get(a), feed.stops.get(b)
    if not sa or not sb:
        return False
    d = dist_m(sa, sb)
    if d == d and d <= 150:
        return True
    return norm(sa["name"]) == norm(sb["name"]) and (d != d or d <= radius_m)


def deadhead(feed: Feed, a: str, b: str, road_factor: float, kmh: float) -> tuple[float, float]:
    """Kilometres and minutes of an empty run between two stops."""
    sa, sb = feed.stops.get(a), feed.stops.get(b)
    if not sa or not sb:
        return float("inf"), float("inf")
    d = dist_m(sa, sb)
    if math.isnan(d):
        return float("inf"), float("inf")
    km = d * road_factor / 1000
    return km, km / kmh * 60


def build_blocks(feed: Feed, trips: list[Trip], min_layover: int = 2, radius_m: float = 400,
                 interline: bool = False, interline_at: set[str] | None = None, max_deadhead_km: float = 3.0,
                 road_factor: float = 1.3, deadhead_kmh: float = 25) -> list[Block]:
    """interline=False: a bus stays on one line all day.
    interline=True: a bus may change line when the next trip starts at a stop whose name is in interline_at."""
    interline_at = {norm(x) for x in (interline_at or set())}
    blocks: list[Block] = []
    free: list[tuple[int, str, str, int]] = []      # (free from, stop, route, block index)
    for t in sorted(trips, key=lambda x: (x.start, x.route)):
        best = None
        for i, (ft, stop, route, bi) in enumerate(free):
            if route != t.route and (not interline or norm(feed.stops.get(t.first, {}).get("name", "")) not in interline_at):
                continue
            if same_terminal(feed, stop, t.first, radius_m):
                dh_min = 0.0
            else:
                km, dh_min = deadhead(feed, stop, t.first, road_factor, deadhead_kmh)
                if km > max_deadhead_km:
                    continue
            need = min_layover + math.ceil(dh_min)
            if ft + need > t.start:
                continue
            key = (route != t.route, dh_min > 0, ft)   # same line, no empty run, longest wait
            if best is None or key < best[0]:
                best = (key, i, math.ceil(dh_min), need)
        if best is None:
            blocks.append(Block(len(blocks) + 1, [t]))
            bi = len(blocks) - 1
        else:
            _, i, dh, need = best
            bi = free.pop(i)[3]
            blocks[bi].trips.append(t)
            blocks[bi].dh.append(dh)
            blocks[bi].req.append(need)
        free.append((t.end, t.last, t.route, bi))
    return blocks


def peak_vehicles(blocks: list[Block]) -> int:
    ev = sorted([(b.start, 1) for b in blocks] + [(b.end, -1) for b in blocks], key=lambda x: (x[0], x[1]))
    cur = best = 0
    for _, d in ev:
        cur += d
        best = max(best, cur)
    return best

"""Vehicle blocks (obiegi): which trips one bus runs in a day.

ZKM does not publish its blocks, so they are rebuilt from the timetable. A bus
can take a trip when it waits at the same terminal (same stop, or a stop with
the same name within a radius) for at least the minimum layover. Trips are
taken in time order; the bus that waited longest takes the next trip (first in,
first out). With no deadhead trips this gives the smallest possible fleet.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .timetable import Feed, Trip, dist_m, norm


@dataclass
class Block:
    id: int
    trips: list = field(default_factory=list)       # Trip objects in order

    @property
    def start(self) -> int:
        return self.trips[0].start

    @property
    def end(self) -> int:
        return self.trips[-1].end

    def layovers(self) -> list[tuple[Trip, Trip, int]]:
        return [(a, b, b.start - a.end) for a, b in zip(self.trips, self.trips[1:])]


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


def build_blocks(feed: Feed, trips: list[Trip], min_layover: int = 2, radius_m: float = 400,
                 interline: bool = False, interline_at: set[str] | None = None) -> list[Block]:
    """interline=False: a bus stays on one line all day.
    interline=True: a bus may change line at a stop whose name is in interline_at."""
    interline_at = {norm(x) for x in (interline_at or set())}
    blocks: list[Block] = []
    free: list[tuple[int, str, str, int]] = []      # (free from, stop, route, block index)
    for t in sorted(trips, key=lambda x: (x.start, x.route)):
        best = None
        for i, (ft, stop, route, bi) in enumerate(free):
            if ft + min_layover > t.start or not same_terminal(feed, stop, t.first, radius_m):
                continue
            if route != t.route:
                if not interline or norm(feed.stops.get(t.first, {}).get("name", "")) not in interline_at:
                    continue
            key = (route != t.route, ft)          # same line first, then the longest wait
            if best is None or key < best[0]:
                best = (key, i)
        if best is None:
            blocks.append(Block(len(blocks) + 1, [t]))
        else:
            _, i = best
            bi = free.pop(i)[3]
            blocks[bi].trips.append(t)
        bi = next(k for k, b in enumerate(blocks) if b.trips[-1] is t)
        free.append((t.end, t.last, t.route, bi))
    return blocks


def peak_vehicles(blocks: list[Block]) -> int:
    ev = sorted([(b.start, 1) for b in blocks] + [(b.end, -1) for b in blocks], key=lambda x: (x[0], x[1]))
    cur = best = 0
    for _, d in ev:
        cur += d
        best = max(best, cur)
    return best

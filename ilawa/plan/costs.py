"""Physical indicators of a timetable and a simple cost calculator.

Indicators for one day: service kilometres, deadhead kilometres (to and from the
depot), vehicle hours, peak vehicles, driver duty hours. The calculator turns
them into money with editable unit costs (see the [costs] section of the
configuration file). The default unit costs are estimates, not ZKM data.
"""
from __future__ import annotations

import datetime as dt
import math

from .blocks import Block, peak_vehicles
from .calendar_util import day_counts
from .duties import Duty
from .timetable import Feed, dist_m


def indicators(feed: Feed, blocks: list[Block], duties: list[Duty], depot: dict, road_factor: float,
               deadhead_kmh: float) -> dict:
    service_km = sum(feed.trip_km(t, road_factor) for b in blocks for t in b.trips)
    dead_km = dead_min = 0.0
    for b in blocks:
        for sid in (b.trips[0].first, b.trips[-1].last):
            s = feed.stops.get(sid)
            d = dist_m(depot, s) if s else float("nan")
            km = 0 if math.isnan(d) else d * road_factor / 1000
            dead_km += km
            dead_min += km / deadhead_kmh * 60
    for b in blocks:  # empty runs between trips inside a block
        for a, c, d in zip(b.trips, b.trips[1:], b.dh or []):
            if d:
                sa, sc = feed.stops.get(a.last), feed.stops.get(c.first)
                dd = dist_m(sa, sc) if sa and sc else float("nan")
                dead_km += 0 if math.isnan(dd) else dd * road_factor / 1000
    vehicle_min = sum(b.end - b.start for b in blocks) + dead_min
    service_min = sum(t.end - t.start for b in blocks for t in b.trips)
    return {"trips": sum(len(b.trips) for b in blocks), "blocks": len(blocks), "peak_vehicles": peak_vehicles(blocks),
            "service_km": service_km, "deadhead_km": dead_km, "total_km": service_km + dead_km,
            "service_hours": service_min / 60, "vehicle_hours": vehicle_min / 60,
            "duties": len(duties), "duty_hours": sum(d.length for d in duties) / 60}


def daily_cost(ind: dict, c: dict) -> dict:
    """Cost of one day from indicators and unit costs (dictionary from the config)."""
    km = ind["total_km"]
    share_e = c["electric_share"]
    energy = km * ((1 - share_e) * c["diesel_l_per_100km"] / 100 * c["diesel_pln_per_l"]
                   + share_e * c["electric_kwh_per_km"] * c["electricity_pln_per_kwh"])
    maint = km * c["maintenance_pln_per_km"]
    drivers = ind["duty_hours"] * c["driver_pln_per_hour"]
    vehicles = ind["peak_vehicles"] * (1 + c["reserve_share"]) * c["vehicle_fixed_pln_per_day"]
    direct = energy + maint + drivers + vehicles
    over = direct * c["overhead_share"]
    return {"energy": energy, "maintenance": maint, "drivers": drivers, "vehicles": vehicles,
            "overhead": over, "total": direct + over, "per_km": (direct + over) / km if km else float("nan")}


def annual(per_day_type: dict[str, dict], year: int, c: dict | None = None) -> dict:
    """Year totals: daily values times the number of days of each type in the year.

    The fleet is paid for every day, so the vehicle cost of the year uses the
    largest peak of all day types on all days (when the unit costs `c` are given).
    """
    n = day_counts(year)
    out: dict = {}
    for dtp, vals in per_day_type.items():
        for k, v in vals.items():
            if isinstance(v, (int, float)) and k not in ("peak_vehicles", "blocks", "cost_per_km"):
                out[k] = out.get(k, 0) + v * n.get(dtp, 0)
    out["peak_vehicles"] = max((v.get("peak_vehicles", 0) for v in per_day_type.values()), default=0)
    if c is not None and "cost_energy" in out:
        days = sum(n.get(d, 0) for d in per_day_type)
        veh = out["peak_vehicles"] * (1 + c["reserve_share"]) * c["vehicle_fixed_pln_per_day"] * days
        direct = out["cost_energy"] + out["cost_maintenance"] + out["cost_drivers"] + veh
        out["cost_vehicles"], out["cost_overhead"] = veh, direct * c["overhead_share"]
        out["cost_total"] = direct * (1 + c["overhead_share"])
    if out.get("total_km"):
        out["cost_per_km"] = out.get("cost_total", 0) / out["total_km"]
    out["days"] = n
    return out

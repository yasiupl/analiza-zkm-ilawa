"""Parse PKP PLK open data responses into flat rows for one station."""
from __future__ import annotations

import datetime as dt
from typing import Iterable


def _dict_names(d) -> dict[int, str]:
    """A station dictionary can be {id: name} or a list of {id, name}."""
    if not d:
        return {}
    if isinstance(d, dict):
        out = {}
        for k, v in d.items():
            try:
                out[int(k)] = v if isinstance(v, str) else (v.get("name") or v.get("nm") or str(v))
            except (TypeError, ValueError):
                continue
        return out
    return {int(x.get("id")): x.get("name") or x.get("nm") for x in d if isinstance(x, dict)}


def _time(s: str | None) -> str | None:
    """'HH:MM:SS', 'HH:MM' or ISO date-time -> 'HH:MM:SS'."""
    if not s:
        return None
    if "T" in s:
        s = s.split("T", 1)[1]
    s = s.split(".")[0].split("+")[0].rstrip("Z")
    parts = s.split(":")
    if len(parts) == 2:
        parts.append("00")
    return ":".join(p.zfill(2) for p in parts[:3])


def schedule_rows(js: dict, station_id: int) -> list[dict]:
    """One row per (route, operating date) that calls at station_id."""
    dic = js.get("dictionaries") or {}
    names = _dict_names(dic.get("stations"))
    rows = []
    for r in js.get("routes", []):
        sts = sorted(r.get("stations", []), key=lambda s: s.get("orderNumber", 0))
        here = [s for s in sts if int(s.get("stationId", -1)) == station_id]
        if not here:
            continue
        s = here[0]
        idx = sts.index(s)
        first, last = sts[0], sts[-1]
        base = {
            "schedule_id": r.get("scheduleId"),
            "order_id": r.get("orderId"),
            "train_order_id": r.get("trainOrderId") or r.get("orderId"),
            "name": r.get("name"),
            "carrier": r.get("carrierCode"),
            "number": s.get("departureTrainNumber") or s.get("arrivalTrainNumber") or r.get("nationalNumber"),
            "category": s.get("departureCommercialCategory") or s.get("arrivalCommercialCategory")
                        or r.get("commercialCategorySymbol"),
            "origin": names.get(int(first.get("stationId", 0)), str(first.get("stationId"))),
            "destination": names.get(int(last.get("stationId", 0)), str(last.get("stationId"))),
            "prev_station": names.get(int(sts[idx - 1]["stationId"])) if idx > 0 else None,
            "next_station": names.get(int(sts[idx + 1]["stationId"])) if idx + 1 < len(sts) else None,
            "arr_time": _time(s.get("arrivalTime")) if idx > 0 else None,
            "dep_time": _time(s.get("departureTime")) if idx + 1 < len(sts) else None,
            "arr_day": s.get("arrivalDay") or 0,
            "dep_day": s.get("departureDay") or 0,
            "platform_arr": s.get("arrivalPlatform"),
            "platform_dep": s.get("departurePlatform"),
            "stop_type": s.get("stopTypeName"),
            "stop_type_id": s.get("stopTypeId"),
        }
        for d in r.get("operatingDates", []) or []:
            row = dict(base)
            row["operating_date"] = d[:10]
            rows.append(row)
    return rows


def operation_rows(js: dict, station_id: int, seen_at: str) -> Iterable[dict]:
    """Rows from /operations for station_id: planned, actual and delay."""
    for t in js.get("trains", []):
        for s in t.get("stations", []):
            if int(s.get("stationId", -1)) != station_id:
                continue
            yield {
                "operating_date": (t.get("operatingDate") or "")[:10],
                "schedule_id": t.get("scheduleId"),
                "order_id": t.get("orderId"),
                "train_order_id": t.get("trainOrderId") or t.get("orderId"),
                "status": t.get("trainStatus"),
                "planned_arr": s.get("plannedArrival"),
                "planned_dep": s.get("plannedDeparture"),
                "actual_arr": s.get("actualArrival"),
                "actual_dep": s.get("actualDeparture"),
                "arr_delay": s.get("arrivalDelayMinutes"),
                "dep_delay": s.get("departureDelayMinutes"),
                "confirmed": s.get("isConfirmed"),
                "cancelled": s.get("isCancelled"),
                "seen_at": seen_at,
            }


def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()

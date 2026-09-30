"""Propose corrections to the ZKM Iława timetable for better train connections.

Run (all steps):   python zkmplan.py
Without download:  python zkmplan.py --no-fetch
Other settings:    python zkmplan.py --config zkmplan.ini --from-date 2026-10-05

Steps:
 1. Download the ZKM timetable (rozklad.com -> GTFS) and the PKP PLK train
    timetable for the next 30 days (needs PLK_API_KEY in .env).
 2. For each day type (working day, Saturday, Sunday) rebuild the vehicle blocks
    and driver duties, and calculate the indicators and the cost.
 3. Shift trips by a few minutes (see zkmplan.ini) to add good transfers at the
    station, with the blocks, the driver breaks and the cost kept.
 4. Find extra round trips that buses waiting at the station can run.
 5. Estimate the effect on bus use (and on ticket revenue, see show_money).
 6. Write the results to data/plan/<date>/ (with a LaTeX list of the changes,
    zmiany.tex) and the report reports/korekty-zkm.html.
"""
from __future__ import annotations

import argparse
import configparser
import csv
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import analyze as A
from ilawa import gtfs_write
from ilawa.calendar import day_type
from ilawa.common import DATA, ROOT
from ilawa.plan import costs as C
from ilawa.plan import demand as DM
from ilawa.plan import explain as EX
from ilawa.plan import gaps as G
from ilawa.plan import optimize as O
from ilawa.plan import pasttest as PT
from ilawa.plan import trains as TR
from ilawa.plan.blocks import build_blocks
from ilawa.plan.duties import check_breaks, cut_duties
from ilawa.plan.timetable import Feed, hhmm, norm

DAY_TYPES = ("Roboczy", "Sobota", "Niedziela")


def load_config(path: Path) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    if not cfg.read(path, encoding="utf-8"):
        raise SystemExit(f"Configuration file not found: {path}")
    return cfg


def section(cfg, name: str) -> dict:
    out = {}
    for k, v in cfg[name].items():
        try:
            out[k] = float(v) if v.strip() else None
        except ValueError:
            out[k] = v.strip()
    return out


def fetch() -> None:
    import collect
    import zkm_gtfs
    print("1. Download: ZKM timetable (rozklad.com)")
    argv, sys.argv = sys.argv, ["zkm_gtfs.py"]
    try:
        zkm_gtfs.main()
    finally:
        sys.argv = argv
    print("1. Download: PKP PLK timetable")
    try:
        collect.collect_plk(0)
    except Exception as e:  # the tool can still work with the files already saved
        print(f"   PLK download failed: {e}")


def plan_dates(feed: Feed, tr: pd.DataFrame, start: dt.date, horizon: int) -> dict[str, list[dt.date]]:
    have_trains = set(tr["arr_date"]) | set(tr["dep_date"])
    out: dict[str, list[dt.date]] = {k: [] for k in DAY_TYPES}
    for i in range(horizon):
        d = start + dt.timedelta(days=i)
        if d in have_trains and feed.trips_on(d):
            out[day_type(d)].append(d)
    out["Roboczy"].sort(key=lambda d: (d.weekday() not in (1, 2, 3), d))  # Tuesday-Thursday first
    return {k: v for k, v in out.items() if v}


def make_blocks(feed, trips, blc, duc, depot, interline, station_name):
    blocks = build_blocks(feed, trips, int(blc["min_layover"]), blc["terminal_radius_m"],
                          interline=interline, interline_at={station_name},
                          max_deadhead_km=blc["max_deadhead_km"], road_factor=blc["road_factor"],
                          deadhead_kmh=blc["deadhead_kmh"])
    duties = cut_duties(feed, blocks, {x.strip() for x in str(duc["relief_points"]).split(",")},
                        int(duc["max_duty"]), int(duc["prep_minutes"]))
    return blocks, duties


def breaks_summary(duties, duc) -> dict:
    ch = [check_breaks(d, int(duc["break_part"]), int(duc["break_after_6h"]), int(duc["break_after_8h"]),
                       int(duc["max_driving"])) for d in duties]
    return {"duties": len(ch), "ok_breaks": sum(c["ok_breaks"] for c in ch),
            "ok_driving": sum(c["ok_driving"] for c in ch),
            "list": [{"block": d.block, "start": hhmm(d.start), "end": hhmm(d.end), "driving_h": round(c["driving"] / 60, 2),
                      "length_h": round(c["length"] / 60, 2), "breaks": c["break_parts"], "needed": c["break_needed"],
                      "ok": c["ok_breaks"] and c["ok_driving"]} for d, c in zip(duties, ch)]}


def by_line(trips, station):
    dep, arr = {}, {}
    for t in trips:
        sa, sd = O.station_times(t, O.shape(t, station))
        if sd is not None:
            dep.setdefault(t.route, []).append(sd)
        if sa is not None:
            arr.setdefault(t.route, []).append(sa)
    return {k: np.array(v, float) for k, v in dep.items()}, {k: np.array(v, float) for k, v in arr.items()}


def realised(hist: pd.DataFrame, feed: Feed, proposed: dict, station: set, dtp: str, m1: float,
             good: float = 10) -> dict:
    """Train -> bus good transfers with actual train arrivals, current and proposed timetable.

    The same trains are also counted with their planned arrivals (plan_before,
    plan_after), so the effect of the delays can be told apart from the choice of trains.
    """
    out = {"pairs": 0, "before": 0, "after": 0, "plan_before": 0, "plan_after": 0, "days": 0, "trains": 0}
    if hist is None or hist.empty:
        return out
    from ilawa import model as M
    for date_s in sorted(hist["date_arr"].dropna().unique()):
        d = dt.date.fromisoformat(str(date_s)[:10])
        if day_type(d) != dtp:
            continue
        trips = feed.trips_on(d)
        if not trips:
            continue
        g = hist[(hist["date_arr"] == date_s) & np.isfinite(hist["arr_min"]) & np.isfinite(hist["arr_delay"])]
        act = (g["arr_min"] + g["arr_delay"]).to_numpy(float)
        pln = g["arr_min"].to_numpy(float)
        if not len(act):
            continue
        out["days"] += 1
        out["trains"] += len(act)
        new = [proposed.get(t.id, t) for t in trips]
        d0, _ = by_line(trips, station)
        d1, _ = by_line(new, station)
        for r in d0:
            out["pairs"] += len(act)
            out["before"] += int(M.train_to_bus(act, np.sort(d0[r]), m1, good)["good"].sum())
            out["after"] += int(M.train_to_bus(act, np.sort(d1[r]), m1, good)["good"].sum())
            out["plan_before"] += int(M.train_to_bus(pln, np.sort(d0[r]), m1, good)["good"].sum())
            out["plan_after"] += int(M.train_to_bus(pln, np.sort(d1[r]), m1, good)["good"].sum())
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "zkmplan.ini"))
    ap.add_argument("--no-fetch", action="store_true", help="use the files already downloaded")
    ap.add_argument("--from-date", default=None, help="first planning date (default: tomorrow)")
    ap.add_argument("--gtfs", default=str(DATA / "zkm_gtfs.zip"), help="ZKM GTFS feed (zip or folder)")
    ap.add_argument("--day-types", default="Roboczy,Sobota,Niedziela")
    ap.add_argument("--variants", default=None, help="single, interlined or both (overrides the config)")
    ap.add_argument("--time-limit", type=int, default=None, help="solver seconds (overrides the config)")
    ap.add_argument("--set", action="append", default=[], metavar="SECTION.KEY=VALUE",
                    help="change one value of the config for this run, e.g. transfers.good_window=5")
    ap.add_argument("--out", default=None, help="folder for the results (default: data/plan/<today>)")
    ap.add_argument("--no-report", action="store_true", help="do not write reports/korekty-zkm.html")
    a = ap.parse_args()
    cfg = load_config(Path(a.config))
    for item in a.set:
        key, _, value = item.partition("=")
        sec, _, opt = key.partition(".")
        if not (sec and opt and cfg.has_section(sec)):
            raise SystemExit(f"--set {item!r}: use SECTION.KEY=VALUE with a section of the config")
        cfg.set(sec, opt, value)
    gen, trc, shc, blc, duc, coc, dmc = (section(cfg, s) for s in
                                         ("general", "transfers", "shifts", "blocks", "duties", "costs", "demand"))
    if not a.no_fetch:
        fetch()

    feed = Feed(Path(a.gtfs))
    station_name = str(gen["station_stop_name"])
    station = feed.station_stops(station_name)
    if not station:
        raise SystemExit(f"No stop named {station_name!r} in the GTFS feed.")
    if blc["depot_lat"] is not None:
        depot = {"lat": blc["depot_lat"], "lon": blc["depot_lon"]}
    else:
        cand = [s for s in feed.stops.values() if norm(str(blc["depot_stop_name"])) in norm(s["name"])]
        depot = cand[0] if cand else next(iter(feed.stops.values()))
    tr = A.load_planned_trains()
    hist = [h for h in (A.load_ic_history(), A.load_monitor()) if len(h)]
    hist = pd.concat(hist, ignore_index=True) if hist else pd.DataFrame()
    buffers = (TR.delay_buffers(hist, cap=trc["delay_buffer_cap"], quantile=trc.get("delay_buffer_quantile") or 0.5)
               if trc["delay_buffer"] == "observed" else {})
    start = dt.date.fromisoformat(a.from_date) if a.from_date else dt.date.today() + dt.timedelta(days=1)
    dates = plan_dates(feed, tr, start, int(gen["horizon_days"]))
    dates = {k: v for k, v in dates.items() if k in a.day_types.split(",")}
    variants = {"single": False, "interlined": True}
    vsel = a.variants or blc["variants"]
    if vsel in variants:
        variants = {vsel: variants[vsel]}
    if a.time_limit:
        shc["time_limit"] = a.time_limit
    m1, m2, good = trc["walk_train_to_bus"], trc["walk_bus_to_train"], trc["good_window"]
    unit_km = C.daily_cost({"total_km": 1, "duty_hours": 0, "peak_vehicles": 0}, coc)["energy"] + coc["maintenance_pln_per_km"]
    run_dir = Path(a.out) if a.out else DATA / "plan" / dt.date.today().isoformat()
    run_dir.mkdir(parents=True, exist_ok=True)
    result = {"generated": dt.datetime.now().isoformat(timespec="seconds"),
              "config": {s: dict(cfg[s]) for s in cfg.sections()}, "feed": feed.feed_info,
              "station_stops": sorted(station), "buffers": buffers, "day_types": {}}
    proposed = {}   # trip id -> shifted trip (first variant)

    for dtp, ds in dates.items():
        rep, opt_dates = ds[0], ds[: int(gen["dates_per_day_type"])]
        print(f"2. {dtp}: planning date {rep}; optimiser uses {len(opt_dates)} dates")
        trips = feed.trips_on(rep)
        trains = {d: TR.station_trains(tr, d) for d in opt_dates}
        all_trains = {d: TR.station_trains(tr, d) for d in ds}
        res = {"date": str(rep), "opt_dates": [str(d) for d in opt_dates], "all_dates": [str(d) for d in ds],
               "trips": len(trips), "trains_rep": {"arr": all_trains[rep][0], "dep": all_trains[rep][1]},
               "bus_rep": [{"id": t.id, "route": t.route, "headsign": t.headsign, "flags": t.flags,
                            "arr": O.station_times(t, O.shape(t, station))[0],
                            "dep": O.station_times(t, O.shape(t, station))[1]} for t in trips],
               "variants": {}}
        for vname, inter in variants.items():
            blocks, duties = make_blocks(feed, trips, blc, duc, depot, inter, station_name)
            ind0 = C.indicators(feed, blocks, duties, depot, blc["road_factor"], blc["deadhead_kmh"])
            cost0 = C.daily_cost(ind0, coc)
            p = O.Params(station_stops=station, m_t2b=m1, m_b2t=m2, good=good, max_shift=int(shc["max_shift"]),
                         school_max_shift=int(shc["school_max_shift"]), max_extra_dwell=int(shc["max_extra_dwell"]),
                         min_layover=int(blc["min_layover"]), habit_penalty=shc["habit_penalty"], trip_change_penalty=shc["trip_change_penalty"],
                         dwell_penalty=shc["dwell_penalty"], break_part=int(duc["break_part"]),
                         need_6h=int(duc["break_after_6h"]), need_8h=int(duc["break_after_8h"]),
                         buffers=buffers, time_limit=int(shc["time_limit"]),
                         direction=str(shc.get("shift_direction") or "any"))
            print(f"3. {dtp} / {vname}: {len(blocks)} blocks, {len(duties)} duties")
            stages = [int(float(x)) for x in str(shc.get("stages") or "").replace(" ", "").split(",") if x]
            opt = O.optimise_stages(trips, blocks, duties, trains, p, stages)
            new_trips = opt["trips"]
            EX.contributions(trips, new_trips, opt["changes"], all_trains, rep, station, m1, m2, good, buffers)
            nd_all = max(1, len(all_trains))
            gpd = lambda ts, b=None: O.count_good(ts, all_trains, station, m1, m2, good, b)["total"] / nd_all
            curve = [{"stage": 0, "limit": 0, "changes": 0, "good_per_day": gpd(trips, buffers), "good_per_day_plan": gpd(trips)}]
            for s in opt["stages"]:
                curve.append({"stage": s["stage"], "limit": s["limit"], "changes": s["changes"], "status": s["status"],
                              "good_per_day": gpd(s["trips"], buffers), "good_per_day_plan": gpd(s["trips"])})
            nblocks, nduties = O.rebuild_blocks(blocks, new_trips), O.rebuild_duties(duties, new_trips)
            ind1 = C.indicators(feed, nblocks, nduties, depot, blc["road_factor"], blc["deadhead_kmh"])
            cost1 = C.daily_cost(ind1, coc)
            g0 = O.count_good(trips, all_trains, station, m1, m2, good)
            g1 = O.count_good(new_trips, all_trains, station, m1, m2, good)
            gb0 = O.count_good(trips, all_trains, station, m1, m2, good, buffers)
            gb1 = O.count_good(new_trips, all_trains, station, m1, m2, good, buffers)
            ta = np.array([x["t"] for x in all_trains[rep][0]])
            td = np.array([x["t"] for x in all_trains[rep][1]])
            w0 = DM.mean_waits(ta, td, *by_line(trips, station), m1, m2, dmc["wait_cap"])
            w1 = DM.mean_waits(ta, td, *by_line(new_trips, station), m1, m2, dmc["wait_cap"])
            eff = DM.ridership_effect(w0["all"], w1["all"], dmc)
            gp = G.candidates(feed, nblocks, station, all_trains[rep], m1, m2, good, int(blc["min_layover"]),
                              blc["road_factor"], unit_km, duties=nduties, driver_pln_per_hour=coc["driver_pln_per_hour"],
                              overhead_share=coc["overhead_share"], max_duty=int(duc["max_duty"]),
                              rules={"part": int(duc["break_part"]), "need_6h": int(duc["break_after_6h"]),
                                     "need_8h": int(duc["break_after_8h"]), "max_driving": int(duc["max_driving"])})
            real = realised(hist, feed, {t.id: t for t in new_trips}, station, dtp, m1, good)
            res["variants"][vname] = {
                "status": opt["status"], "changes": opt["changes"], "curve": curve,
                "indicators_before": ind0, "indicators_after": ind1, "cost_before": cost0, "cost_after": cost1,
                "breaks_before": breaks_summary(duties, duc), "breaks_after": breaks_summary(nduties, duc),
                "good_before": g0, "good_after": g1, "good_buffered_before": gb0, "good_buffered_after": gb1,
                "realised_t2b": real, "waits_before": w0, "waits_after": w1, "demand": eff, "gaps": gp,
                "blocks_before": [[{"route": t.route, "start": t.start, "end": t.end} for t in b.trips] for b in blocks],
                "blocks_after": [[{"route": t.route, "start": t.start, "end": t.end} for t in b.trips] for b in nblocks],
                "bus_after": [{"id": t.id, "route": t.route, "arr": O.station_times(t, O.shape(t, station))[0],
                               "dep": O.station_times(t, O.shape(t, station))[1]} for t in new_trips],
                "bus_stages": [[{"id": t.id, "route": t.route, "arr": O.station_times(t, O.shape(t, station))[0],
                                 "dep": O.station_times(t, O.shape(t, station))[1]} for t in s["trips"]]
                               for s in opt["stages"][:-1]]}
            print(f"   {opt['status']}: good transfers {g0['total']} -> {g1['total']} over {g0['n_dates']} dates "
                  f"(with delay buffer {gb0['total']} -> {gb1['total']}); actual delays {real['before']} -> {real['after']}; "
                  f"cost/day {cost0['total']:.0f} -> {cost1['total']:.0f} PLN; changed trips {len(opt['changes'])}")
            if not proposed or vname == "single":
                for t in new_trips:
                    proposed[t.id] = t
        result["day_types"][dtp] = res

    year = start.year
    for vname in variants:
        for when in ("before", "after"):
            per = {}
            for dtp, r in result["day_types"].items():
                v = r["variants"][vname]
                per[dtp] = {**v[f"indicators_{when}"], **{f"cost_{k}": x for k, x in v[f"cost_{when}"].items()}}
            result.setdefault("annual", {}).setdefault(vname, {})[when] = C.annual(per, year, coc)
    n_days = C.day_counts(year)
    for vname in variants:              # extra bus trips in a year, all day types
        tot = {}
        for k in ("low", "mid", "high"):
            tot[k] = sum(n_days.get(dtp, 0) * r["variants"][vname]["demand"][k]["extra_trips_day"]
                         for dtp, r in result["day_types"].items())
        result.setdefault("annual_demand", {})[vname] = {**tot, "days": {d: n_days.get(d, 0) for d in result["day_types"]}}
    result["revenue_per_trip"] = DM.revenue_per_trip(dmc)
    if int(gen.get("past_days") or 0) > 0:
        result["past_test"] = past_test(result, tr, int(gen["past_days"]), m1, m2, good, buffers, dmc["wait_cap"])
    write_outputs(feed, proposed, result, run_dir)
    write_latex(result, run_dir / "zmiany.tex")
    (run_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if a.no_report:
        print(f"6. Results: {run_dir}")
        return
    from ilawa.plan import report
    shown = result if str(coc.get("show_money") or "").lower() in ("yes", "tak", "true", "1") else strip_money(result)
    out = report.build(shown, ROOT / "reports" / "zkmplan_template.html", ROOT / "reports" / "korekty-zkm.html")
    print(f"6. Results: {run_dir}\n   Report: {out}")


def past_test(result: dict, tr: pd.DataFrame, n_days: int, m1: float, m2: float, good: float,
              buffers: dict, wait_cap: float) -> dict:
    """Test the current timetable, each stage and the proposal on past days with the actual train times."""
    delays = PT.delay_table(A.load_ic_history(), A.load_monitor())
    last = dt.date.today() - dt.timedelta(days=1)
    fs = str((result.get("feed") or {}).get("feed_start_date") or "")
    first = max(last - dt.timedelta(days=n_days - 1),
                dt.date(int(fs[:4]), int(fs[4:6]), int(fs[6:8])) if len(fs) == 8 else dt.date.min)
    days = PT.past_days(delays, first, last)
    vname = "single" if "single" in next(iter(result["day_types"].values()))["variants"] else \
        next(iter(next(iter(result["day_types"].values()))["variants"]))
    tts = {"obecny": {d: r["bus_rep"] for d, r in result["day_types"].items()}}
    nst = max(len(r["variants"][vname].get("bus_stages", [])) for r in result["day_types"].values())
    for i in range(nst):
        tts[f"etap {i + 1}"] = {d: (r["variants"][vname]["bus_stages"] + [r["variants"][vname]["bus_after"]] * nst)[i]
                                for d, r in result["day_types"].items()}
    tts["propozycja"] = {d: r["variants"][vname]["bus_after"] for d, r in result["day_types"].items()}
    rows = PT.evaluate(tts, tr, delays, days, [good], m1, m2, buffers, wait_cap)
    out = {"variant": vname, "window": good, "days": [str(d) for d in days], "first": str(first), "last": str(last),
           "timetables": list(tts), "rows": rows, "summary": PT.summary(rows), "worst": PT.worst_days(rows)}
    print(f"5b. Past days with delay data: {len(days)} ({first} to {last})")
    for r in out["summary"]:
        print(f"    {r['day_type']:9} {r['timetable']:10} good {r['plan_total']:.1f} planned / {r['actual_total']:.1f} actual; "
              f"caught {r['plan_caught']:.1f} / {r['actual_caught']:.1f}; wait {r['actual_wait_t2b']:.1f} min")
    return out


def write_outputs(feed: Feed, proposed: dict, result: dict, run_dir: Path) -> None:
    fm = lambda x: hhmm(x) if x is not None else ""
    with (run_dir / "zmiany.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["typ_dnia", "wariant_obiegow", "etap", "kurs", "linia", "kierunek", "rodzaj", "zmiana_przed_dworcowa_min",
                    "zmiana_po_dworcowej_min", "przyjazd_dworcowa", "nowy_przyjazd", "odjazd_dworcowa", "nowy_odjazd",
                    "pierwszy_przystanek", "nowy", "ostatni_przystanek", "nowy", "dobre_przesiadki_dziennie",
                    "zyskuja", "traca"])
        for dtp, r in result["day_types"].items():
            for vname, v in r["variants"].items():
                for c in v["changes"]:
                    w.writerow([dtp, vname, c.get("stage", ""), c["trip"], c["route"], c["headsign"], c["kind"], c["shift_before"],
                                c["shift_after"], fm(c["station_arr"][0]), fm(c["station_arr"][1]),
                                fm(c["station_dep"][0]), fm(c["station_dep"][1]), fm(c["first_stop"][0]),
                                fm(c["first_stop"][1]), fm(c["last_stop"][0]), fm(c["last_stop"][1]),
                                c.get("gain_per_day", ""), "; ".join(c.get("gains", [])), "; ".join(c.get("losses", []))])
    tables = feed._read_all()
    rows = []
    for r in tables["stop_times.txt"]:
        t = proposed.get(r["trip_id"])
        if t is not None:
            m = t.stops[int(r["stop_sequence"]) - 1][1]
            r = {**r, "arrival_time": f"{m // 60:02d}:{m % 60:02d}:00", "departure_time": f"{m // 60:02d}:{m % 60:02d}:00"}
        rows.append(r)
    tables["stop_times.txt"] = rows
    if tables.get("feed_info.txt"):
        fi = tables["feed_info.txt"][0]
        tables["feed_info.txt"] = [{**fi, "feed_version": f"{fi.get('feed_version', '')}-propozycja"}]
    gtfs_write.write(tables, run_dir / "gtfs_propozycja", run_dir / "gtfs_propozycja.zip")



MONEY_KEYS = {"cost_before", "cost_after", "marginal_cost", "extra_revenue_year", "base_revenue_year",
              "revenue_per_trip", "revenue_per_trip_low", "revenue_per_trip_mid", "revenue_per_trip_high"}


def strip_money(obj):
    """Copy of the result without values in PLN (for a report shown before the operator's data are in)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in MONEY_KEYS or k.startswith("cost_") or k.endswith("_pln_per_l") or "_pln_" in k or k in (
                    "fare_normal", "fare_reduced"):
                continue
            out[k] = strip_money(v)
        return out
    if isinstance(obj, list):
        return [strip_money(v) for v in obj]
    return obj


def _tex(x) -> str:
    rep = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
           "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(rep.get(ch, ch) for ch in str(x))


def write_latex(result: dict, path: Path, variant: str = "single") -> None:
    """List of the proposed changes as LaTeX (longtable), one part for each day type."""
    fm = lambda m: "" if m is None else f"{int(m) // 60}:{int(m) % 60:02d}"
    pair = lambda p: "--" if p[0] is None else fm(p[0]) if p[0] == p[1] else f"{fm(p[0])}\\,$\\to$\\,\\textbf{{{fm(p[1])}}}"
    sh = lambda x: "0" if not x else f"{x:+d}".replace("-", "$-$")
    names = {"Roboczy": "Dzień roboczy", "Sobota": "Sobota", "Niedziela": "Niedziela i święta"}
    pl = lambda d: f"{int(d[8:10])}.{d[5:7]}.{d[:4]}"
    out = ["% Generated by zkmplan.py. Do not edit: run the tool again.",
           f"% Generated {result['generated']}, ZKM feed {(result.get('feed') or {}).get('feed_version', '')}"]
    for dtp, r in result["day_types"].items():
        v = r["variants"].get(variant) or next(iter(r["variants"].values()))
        g0 = v["curve"][0]["good_per_day"]
        out += [f"\\subsection*{{{names.get(dtp, dtp)}}}",
                f"Plan na dzień {pl(r['date'])}, sprawdzony na {len(r['all_dates'])} dniach tego typu "
                f"({pl(r['all_dates'][0])}--{pl(r['all_dates'][-1])}). "
                f"Dobre przesiadki dziennie (pary pociąg--linia, z zapasem na opóźnienie pociągu): obecnie {g0:.0f}.", "",
                "\\begin{tabular}{@{}lrrr@{}}\\toprule",
                "Etap & Zmienione kursy (razem) & Dobre przesiadki dziennie & Zmiana \\\\ \\midrule"]
        for c in v["curve"][1:]:
            out.append(f"{c['stage']} & {c['changes']} & {c['good_per_day']:.0f} & {100 * (c['good_per_day'] - g0) / g0:+.0f}\\,\\% \\\\")
        out += ["\\bottomrule\\end{tabular}", "",
                "\\begin{longtable}{@{}c c >{\\raggedright}p{2.4cm} l l r r >{\\raggedright\\arraybackslash}p{4.3cm}@{}}\\toprule",
                "Etap & Linia & Kierunek & Przyjazd & Odjazd & Inne & Zysk & Pociągi \\\\",
                " & & & \\multicolumn{2}{c}{na Dworcowej} & przyst. & /dzień & (dobra przesiadka po zmianie) \\\\ \\midrule\\endhead",
                "\\bottomrule\\endfoot"]
        key = lambda c: (c.get("stage", 1), c["station_dep"][0] if c["station_dep"][0] is not None else c["station_arr"][0] or 0)
        for c in sorted(v["changes"], key=key):
            other = sh(c["shift_after"]) if c["kind"] != "through" else f"{sh(c['shift_before'])}/{sh(c['shift_after'])}"
            trains = _tex("; ".join(c.get("gains", []))) or ("\\textit{zmiana towarzysząca}" if not c.get("losses") else "")
            if c.get("losses"):
                trains += ("; " if trains else "") + "\\textit{traci: " + _tex("; ".join(c["losses"])) + "}"
            out.append(f"{c.get('stage', 1)} & {_tex(c['route'])} & {_tex(c['headsign'])} & {pair(c['station_arr'])} & "
                       f"{pair(c['station_dep'])} & {other} & {c.get('gain_per_day', 0):+.1f} & {trains} \\\\")
        out += ["\\end{longtable}", ""]
    path.write_text("\n".join(out), encoding="utf-8")


if __name__ == "__main__":
    main()

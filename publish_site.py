#!/usr/bin/env python3
"""Build the public web page of the report for a static web site.

The page is one folder, <date>-raport-zkm, with these files:
  index.html                            summary, links to the reports and to the files
  przesiadki.html, korekty.html         the two web reports
  przesiadki-ilawa-glowna.pdf           printable report on the transfers
  korekty-rozkladu-zkm-ilawa.pdf        printable report on the corrections (with the list of changes)
  wykaz-zmian.csv                       list of the proposed changes
  test-minione-dni.csv                  test of the timetables on past days with the actual train times
  pomiar-dni.csv                        test with the measured train times from the monitor (monitor_test.py)
  zkm-ilawa-gtfs.zip                    current ZKM Iława timetable (GTFS, not official)
  zkm-ilawa-gtfs-propozycja.zip         timetable with the proposed changes (GTFS)

Make the data first:
    python3 report.py
    python3 zkmplan.py --no-fetch
    python3 print_reports.py --public "14-200.pl/@yasiu/<date>-raport-zkm" --pdf

Usage:
    python3 publish_site.py --out ../14-200.pl/src/@yasiu --url https://14-200.pl/@yasiu

The published pages (index.html, przesiadki.html, korekty.html) were edited by hand after the first
publication: the map in the story of index.html, local fonts, the glossary at the end of the reports,
the measured arrivals, no estimate of passengers. The templates in reports/ do not have these edits yet.
The data files of the published page (wykaz-zmian.csv, test-minione-dni.csv, GTFS) also come from a run of
zkmplan.py that is not in data/plan. So the script does not overwrite any file of the page that exists; it
only adds the missing ones. Use --overwrite only after the templates have the edits and the data come from
the current tools. The PDFs of the published page: print_reports.py --from-html <page folder> --public ... --pdf,
then copy them by hand.
"""
from __future__ import annotations

import argparse
import html
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
DRUK = REPORTS / "druk"
DAYNAME = {"Roboczy": "dzień roboczy", "Sobota": "sobota", "Niedziela": "niedziela"}

# Links between the pages in the viewer of claude.ai, replaced by links inside the folder.
ARTIFACT_LINKS = {"https://claude.ai/artifact/SjGg6GoZq5NishuoizYrvL": "przesiadki.html",
                  "https://claude.ai/artifact/LHpHerFfAXQ8om2pxWeYN2": "korekty.html"}
DL_BUTTON = ('<button type="button" id="dl-pdf" hidden>Pobierz wersję do druku (PDF)</button>'
             '<span id="dl-msg" class="dl-msg" role="status"></span>')


def newest_plan() -> Path:
    runs = sorted((ROOT / "data" / "plan").glob("*/result.json"))
    if not runs:
        raise SystemExit("No result.json in data/plan. Run zkmplan.py first.")
    return runs[-1]


def pct(x: float, d: int = 0) -> str:
    return f"{100 * x:.{d}f}".replace(".", ",") + "%"


def spct(x: float) -> str:
    return ("+" if x > 0 else "−" if x < 0 else "") + f"{abs(100 * x):.0f}%"


def num(x: float, d: int = 0) -> str:
    return f"{x:,.{d}f}".replace(",", " ").replace(".", ",")


def fdate(s: str) -> str:
    y, m, d = s[:10].split("-")
    return f"{int(d)}.{m}.{y}"


def size(p: Path) -> str:
    b = p.stat().st_size
    return f"{b / 1024:.0f} kB" if b < 1024 * 1024 else f"{b / 1024 / 1024:.1f} MB".replace(".", ",")


def public_report(src: Path, pdf: str) -> str:
    """The web report with a plain link to the PDF (no viewer script) and links inside the folder."""
    h = src.read_text(encoding="utf-8")
    if DL_BUTTON not in h:
        raise SystemExit(f"{src}: the download button is missing. Build the report again.")
    h = h.replace(DL_BUTTON, f'<a href="{pdf}" download>Pobierz wersję do druku (PDF)</a>'
                             '<a href="./">Wszystkie pliki raportu</a>')
    i = h.find("// Download of the printable version")
    if i >= 0:
        a, b = h.rfind("<script>", 0, i), h.find("</script>", i) + len("</script>")
        h = h[:a] + h[b:]
    for old, new in ARTIFACT_LINKS.items():
        h = h.replace(old, new)
    return h


def summary(D: dict, R: dict, variant: str) -> dict:
    """The numbers of the index page."""
    wd = R["day_types"]["Roboczy"]["variants"][variant]
    c = wd["curve"]
    past = {r["timetable"]: r for r in (R.get("past_test") or {}).get("summary", []) if r["day_type"] == "Roboczy"}
    nm = D["overall"]["Roboczy"]["plain"]["t2b_near_miss"]
    return {"near_all": D["static"]["Roboczy"]["t2b"]["all"]["near_miss_rate"],
            "near_test": nm["observed"], "near_null": nm["null_mean"], "near_p05": nm["null_p05"], "near_p95": nm["null_p95"],
            "test_date": D["rep_dates"]["Roboczy"], "g0": c[0]["good_per_day"], "g1": c[-1]["good_per_day"],
            "changes": c[-1]["changes"], "trips": R["day_types"]["Roboczy"]["trips"],
            "caught0": past.get("obecny", {}).get("actual_caught"), "caught1": past.get("propozycja", {}).get("actual_caught"),
            "past_days": len((R.get("past_test") or {}).get("days", [])),
            "window": float(R["config"]["transfers"]["good_window"]), "cap": float(R["config"]["transfers"]["delay_buffer_cap"]),
            "m1": float(R["config"]["transfers"]["walk_train_to_bus"]), "m2": float(R["config"]["transfers"]["walk_bus_to_train"]),
            "quantile": float(R["config"]["transfers"].get("delay_buffer_quantile") or 0.5),
            "period": D["params"]["dates"]}


def content(S: dict, files: list[tuple[str, str, Path]], date: str, author: str, code_url: str) -> str:
    e = html.escape
    card = ('<div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">'
            '<h3 class="font-heading font-bold text-xl text-gray-900 mb-2">{t}</h3><p class="text-gray-600 mb-5">{d}</p>'
            '<div class="flex flex-wrap gap-3">'
            '<a href="{h}" class="inline-flex items-center gap-2 bg-brand-500 hover:bg-brand-400 text-gray-900 px-4 py-2 rounded-lg font-bold">'
            '<i class="fa-solid fa-chart-column"></i> Otwórz raport</a>'
            '<a href="{p}" download class="inline-flex items-center gap-2 px-4 py-2 bg-white border border-gray-300 text-gray-700 font-medium rounded-lg hover:bg-gray-50">'
            '<i class="fa-solid fa-file-pdf"></i> PDF do druku</a></div></div>')
    tile = ('<div class="bg-white p-5 rounded-2xl border border-gray-100 shadow-sm"><div class="text-3xl font-bold text-gray-900">{v}</div>'
            '<div class="text-sm text-gray-600 mt-1">{k}</div></div>')
    gain = (S["g1"] - S["g0"]) / S["g0"]
    tiles = [tile.format(v=pct(S["near_all"]), k="pociągów w dzień roboczy: autobus odjeżdża, gdy podróżni idą jeszcze z peronu"),
             tile.format(v=spct(gain), k=f"dogodnych przesiadek w dzień roboczy po przesunięciu {S['changes']} z {S['trips']} kursów")]
    if S["caught0"] and S["caught1"]:
        tiles.append(tile.format(v=spct((S["caught1"] - S["caught0"]) / S["caught0"]),
                                 k=f"udanych przesiadek przy rzeczywistych godzinach pociągów ({S['past_days']} minionych dni)"))
    q = "mediana" if S["quantile"] == 0.5 else f"{S['quantile'] * 100:.0f}. percentyl"
    rows = "".join(f'<tr class="border-t border-gray-100"><td class="py-3 pr-4"><a class="text-accent-600 hover:underline font-medium" href="{e(n)}" download>{e(n)}</a></td>'
                   f'<td class="py-3 pr-4 text-gray-600">{e(d)}</td><td class="py-3 text-right whitespace-nowrap text-gray-500">{size(p)}</td></tr>'
                   for n, d, p in files)
    return f"""
            <p class="text-sm font-semibold uppercase tracking-wide text-brand-700 mb-3">Raport · komunikacja miejska</p>
            <h1 class="text-4xl md:text-5xl font-bold text-gray-900 font-heading mb-4">Autobus a pociąg: przesiadki na stacji Iława Główna</h1>
            <p class="text-gray-500 mb-8">{e(author)} · {fdate(date)}</p>

            <p class="text-xl text-gray-700 leading-relaxed mb-8">W dzień roboczy autobus ZKM odjeżdża spod dworca w chwili, gdy podróżni idą jeszcze z peronu, przy {pct(S['near_all'])} pociągów.
            Rozkład autobusów ułożony losowo daje podobny wynik, więc autobusy nie „uciekają” celowo, ale też nie są dopasowane do pociągów.
            Przesunięcie {S['changes']} kursów o kilka minut, bez nowych autobusów i kierowców, daje w dzień roboczy {num(S['g0'])} → {num(S['g1'])} dogodnych przesiadek dziennie.</p>

            <div class="grid md:grid-cols-{len(tiles)} gap-4 mb-14">{''.join(tiles)}</div>

            <h2 class="text-2xl font-bold text-gray-900 font-heading mb-6">Raporty</h2>
            <div class="grid md:grid-cols-2 gap-6 mb-14">
                {card.format(t="Przesiadki Iława Główna", d="Czy autobusy odjeżdżają spod dworca akurat wtedy, gdy przyjeżdżają pociągi? Test na tle rozkładów losowych, z rzeczywistymi godzinami pociągów.", h="przesiadki.html", p="przesiadki-ilawa-glowna.pdf")}
                {card.format(t="Korekty rozkładu ZKM Iława", d="Przesunięcia kursów o kilka minut, w etapach, z zachowaniem obiegów autobusów, przerw kierowców i liczby autobusów. Z pełnym wykazem zmian.", h="korekty.html", p="korekty-rozkladu-zkm-ilawa.pdf")}
            </div>

            <h2 class="text-2xl font-bold text-gray-900 font-heading mb-4">Dane do pobrania</h2>
            <div class="bg-white p-4 sm:p-6 rounded-2xl shadow-sm border border-gray-100 mb-4 overflow-x-auto">
                <table class="w-full text-left text-sm"><thead><tr class="text-gray-500"><th class="pb-2 pr-4 font-semibold">Plik</th><th class="pb-2 pr-4 font-semibold">Zawartość</th><th class="pb-2 text-right font-semibold">Rozmiar</th></tr></thead>
                <tbody>{rows}</tbody></table>
            </div>
            <p class="text-sm text-gray-500 mb-14">Rozkład w formacie GTFS nie jest oficjalnym rozkładem ZKM. Powstał z tabliczek przystankowych na rozklad.com. Przed użyciem porównaj go z rozkładem przewoźnika.</p>

            <h2 class="text-2xl font-bold text-gray-900 font-heading mb-4">Jak liczono</h2>
            <ul class="list-disc pl-6 space-y-2 text-gray-700 mb-14">
                <li>Dogodna przesiadka to czekanie najwyżej {num(S['window'])} minut po dojściu z peronu na przystanek Dworcowa ({num(S['m1'])} min) albo z przystanku na peron ({num(S['m2'])} min). Liczone są pary pociąg–linia.</li>
                <li>Przy przesiadce z pociągu do autobusu do przyjazdu pociągu doliczony jest zapas na opóźnienie: {q} opóźnienia pociągów danej kategorii, najwyżej {num(S['cap'])} minut.</li>
                <li>Rozkład losowy to rozkład ZKM, w którym każdą linię przesunięto o losowy czas od −30 do +30 minut. W dniu testowym ({fdate(S['test_date'])}) autobus „uciekał” przy {pct(S['near_test'])} pociągów, a rozkład losowy dawał {pct(S['near_null'])} (90% wyników: {pct(S['near_p05'])}–{pct(S['near_p95'])}).</li>
                <li>Korekty przesuwają kursy najwyżej o 5 minut. Autobus może tylko przyjechać na Dworcową wcześniej albo odjechać z niej później. Obiegi autobusów i służby kierowców odtworzono z rozkładu, dlatego każdą zmianę musi sprawdzić dyspozytor ZKM.</li>
                <li>Okres analizy: {fdate(S['period'][0])}–{fdate(S['period'][1])}. Godziny autobusów są planowe, bo nie ma publicznych danych o faktycznych odjazdach.</li>
            </ul>

            <h2 class="text-2xl font-bold text-gray-900 font-heading mb-4">Kod i źródła danych</h2>
            <ul class="space-y-2 text-gray-700">
                <li><i class="fa-brands fa-github mr-2"></i>Kod narzędzi i dane: <a class="text-accent-600 hover:underline" href="{e(code_url)}">{e(code_url.replace('https://', ''))}</a></li>
                <li><a class="text-accent-600 hover:underline" href="https://rozklad.com/maps/index.php?IDKlienta=ILAWA_ZKM">Rozkład jazdy ZKM Iława (rozklad.com)</a></li>
                <li><a class="text-accent-600 hover:underline" href="https://pdp-api.plk-sa.pl/">PKP PLK, Otwarte Dane Kolejowe</a>: rozkład i rzeczywiste godziny pociągów</li>
                <li><a class="text-accent-600 hover:underline" href="https://spoznienia.me/">spoznienia.me</a>: historia opóźnień PKP Intercity</li>
                <li><a class="text-accent-600 hover:underline" href="https://mkuran.pl/gtfs/">mkuran.pl, GTFS polish_trains</a>: rozkład kolejowy w formacie GTFS</li>
            </ul>"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="folder of the site where the page folder goes, e.g. ../14-200.pl/src/@yasiu")
    ap.add_argument("--url", required=True, help="web address of that folder, e.g. https://14-200.pl/@yasiu")
    ap.add_argument("--date", default=None, help="date in the name of the page (default: date of the newest zkmplan run)")
    ap.add_argument("--plan", default=None, help="result.json of zkmplan.py (default: newest run)")
    ap.add_argument("--variant", default="single", choices=["single", "interlined"])
    ap.add_argument("--author", default="Marcin Jasiukowicz")
    ap.add_argument("--code-url", default="https://github.com/yasiupl/analiza-zkm-ilawa")
    ap.add_argument("--overwrite", action="store_true",
                    help="write the files of the page also when they exist (this undoes the edits by hand)")
    a = ap.parse_args()
    plan = Path(a.plan) if a.plan else newest_plan()
    R = json.loads(plan.read_text(encoding="utf-8"))
    D = json.loads((REPORTS / "report_data.json").read_text(encoding="utf-8"))
    date = a.date or plan.parent.name
    name = f"{date}-raport-zkm"
    out = Path(a.out) / name
    out.mkdir(parents=True, exist_ok=True)

    pdfs = {"przesiadki-ilawa-glowna.pdf": DRUK / "przesiadki-ilawa-glowna.pdf",
            "korekty-rozkladu-zkm-ilawa.pdf": DRUK / "korekty-rozkladu-zkm-ilawa.pdf"}
    keep = lambda f: (out / f).exists() and not a.overwrite
    for n, p in pdfs.items():
        if not p.exists():
            raise SystemExit(f"{p} is missing. Run: python3 print_reports.py --public \"<address>\" --pdf")
        if keep(n):
            print(f"  {n}: exists, not overwritten (see --overwrite)")
            continue
        shutil.copyfile(p, out / n)
    for f, src, pdf in (("przesiadki.html", "przesiadki-ilawa.html", "przesiadki-ilawa-glowna.pdf"),
                        ("korekty.html", "korekty-zkm.html", "korekty-rozkladu-zkm-ilawa.pdf")):
        if keep(f):
            print(f"  {f}: exists, not overwritten (see --overwrite)")
        else:
            (out / f).write_text(public_report(REPORTS / src, pdf), encoding="utf-8")
    data = [("wykaz-zmian.csv", plan.parent / "zmiany.csv"),
            ("test-minione-dni.csv", ROOT / "results" / "test_na_minionych_dniach" / "dni.csv"),
            ("zkm-ilawa-gtfs.zip", ROOT / "data" / "zkm_gtfs.zip"),
            ("zkm-ilawa-gtfs-propozycja.zip", plan.parent / "gtfs_propozycja.zip"),
            ("pomiar-dni.csv", ROOT / "results" / "pomiar" / "dni.csv")]
    for n, p in data:
        if keep(n):
            print(f"  {n}: exists, not overwritten (see --overwrite)")
        elif p.exists():
            shutil.copyfile(p, out / n)
    files = [("przesiadki-ilawa-glowna.pdf", "Raport „Przesiadki Iława Główna”, wersja do druku", out / "przesiadki-ilawa-glowna.pdf"),
             ("korekty-rozkladu-zkm-ilawa.pdf", "Raport „Korekty rozkładu ZKM Iława” z pełnym wykazem zmian, wersja do druku", out / "korekty-rozkladu-zkm-ilawa.pdf"),
             ("wykaz-zmian.csv", "Wykaz proponowanych zmian: typ dnia, etap, kurs, godziny obecne i proponowane, pociągi", out / "wykaz-zmian.csv"),
             ("test-minione-dni.csv", "Przesiadki na minionych dniach przy rzeczywistych godzinach pociągów, obecny i proponowany rozkład", out / "test-minione-dni.csv"),
             ("zkm-ilawa-gtfs.zip", "Obecny rozkład jazdy ZKM Iława w formacie GTFS", out / "zkm-ilawa-gtfs.zip"),
             ("zkm-ilawa-gtfs-propozycja.zip", "Rozkład jazdy ZKM Iława z proponowanymi zmianami w formacie GTFS", out / "zkm-ilawa-gtfs-propozycja.zip")]
    S = summary(D, R, a.variant)
    title = "Autobus a pociąg: przesiadki na stacji Iława Główna"
    desc = (f"Raport o przesiadkach między autobusami ZKM a pociągami w Iławie: test rozkładu, propozycja korekt "
            f"({num(S['g0'])} → {num(S['g1'])} dogodnych przesiadek dziennie), dane i rozkład w formacie GTFS.")
    tpl = (REPORTS / "site_template.html").read_text(encoding="utf-8")
    page = (tpl.replace("{{TITLE}}", html.escape(title)).replace("{{DESCRIPTION}}", html.escape(desc))
            .replace("{{URL}}", f"{a.url.rstrip('/')}/{name}/")
            .replace("{{CONTENT}}", content(S, files, date, a.author, a.code_url)))
    if keep("index.html"):
        print("  index.html: exists, not overwritten (see --overwrite)")
    else:
        (out / "index.html").write_text(page, encoding="utf-8")
    print(f"Page: {out}\nAddress: {a.url.rstrip('/')}/{name}/")
    for f in sorted(out.iterdir()):
        print(f"  {f.name:34} {size(f)}")


if __name__ == "__main__":
    main()

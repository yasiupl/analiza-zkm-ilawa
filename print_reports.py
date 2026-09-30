#!/usr/bin/env python3
"""Printable versions (LaTeX, A4) of the two reports of the project.

- Attachment 2: analysis of transfers at Iława Główna (data from report.py:
  reports/report_data.json).
- Attachment 3: timetable corrections (data from zkmplan.py:
  data/plan/<date>/result.json, the newest run).

The script writes the LaTeX files and the figures (PDF) to reports/druk/.
With --pdf it also compiles them with xelatex (two passes). With --public it makes
the public versions instead (przesiadki-ilawa-glowna, korekty-rozkladu-zkm-ilawa):
no references to the letters, and the list of changes at the end.
The reports show no values in PLN: money values need the operator's data
(see show_money in zkmplan.ini).

Usage:
    python3 report.py && python3 zkmplan.py --no-fetch   # data first
    python3 print_reports.py --pdf
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402
from matplotlib import font_manager  # noqa: E402

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "reports" / "druk"
FIG = OUT / "fig"

DAYS = ["Roboczy", "Sobota", "Niedziela"]
DAYNAME = {"Roboczy": "Dzień roboczy", "Sobota": "Sobota", "Niedziela": "Niedziela i święta"}
DAYSHORT = {"Roboczy": "dzień roboczy", "Sobota": "sobota", "Niedziela": "niedziela"}
LINES = ["1", "2", "3", "4", "7"]
T2B, B2T = "pociąg→autobus", "autobus→pociąg"

# Colours: bus lines (same as the web reports), ink and state colours.
LC = {"1": "#2a78d6", "2": "#eb6834", "3": "#1baf7a", "4": "#eda100", "7": "#e87ba4"}
INK, INK2, MUTED, RULE, BAND = "#15171b", "#4a4d55", "#767a84", "#dfe0dc", "#e6e7e3"
BAD, GOOD = "#c23030", "#0a8a0a"
IC_CATS = {"IC", "EIP", "EIC", "TLK", "EC", "EN", "IC+"}


# ---------------------------------------------------------------- formatting
def tex(s) -> str:
    s = "" if s is None else str(s)
    rep = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
           "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\^{}", "→": r"$\to$", "–": "--", "−": "$-$"}
    return "".join(rep.get(c, c) for c in s)


def num(x, d=0) -> str:
    if x is None:
        return "--"
    s = f"{x:,.{d}f}".replace(",", "\u2009").replace(".", ",")
    return s.replace("\u2009", r"\,")


def pct(x, d=0) -> str:
    return "--" if x is None else f"{num(100 * x, d)}\\,\\%"


def spct(x, d=0) -> str:
    return "--" if x is None else ("+" if x > 0 else ("$-$" if x < 0 else "")) + f"{num(abs(100 * x), d)}\\,\\%"


def fdate(s: str) -> str:
    y, m, d = str(s)[:10].split("-")
    return f"{int(d)}.{m}.{y}"


def hm(m) -> str:
    if m is None:
        return "--"
    m = int(round(m))
    return f"{m // 60}:{m % 60:02d}"


def table(cols: list[tuple[str, str]], rows: list[list[str]], long: bool = False, size: str = "small") -> str:
    """cols: (header, alignment spec); rows: already formatted cells."""
    spec = "@{}" + " ".join(a for _, a in cols) + "@{}"
    head = " & ".join(h for h, _ in cols) + r" \\ \midrule"
    body = "\n".join(" & ".join(r) + r" \\" for r in rows)
    if long:
        return (f"{{\\{size}\n\\begin{{longtable}}{{{spec}}}\\toprule\n{head}\\endhead\n\\bottomrule\\endfoot\n"
                f"{body}\n\\end{{longtable}}}}\n")
    return (f"{{\\{size}\n\\begin{{center}}\\begin{{tabular}}{{{spec}}}\\toprule\n{head}\n{body}\n"
            f"\\bottomrule\\end{{tabular}}\\end{{center}}}}\n")


def dot(line: str) -> str:
    return f"\\linedot{{L{line}}}{tex(line)}"


# ---------------------------------------------------------------- figures
def setup_mpl() -> None:
    names = {f.name for f in font_manager.fontManager.ttflist}
    fam = [f for f in ("TeX Gyre Heros", "DejaVu Sans") if f in names] or ["sans-serif"]
    plt.rcParams.update({
        "font.family": fam, "font.size": 8, "axes.edgecolor": MUTED, "axes.linewidth": 0.6,
        "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2, "xtick.major.width": 0.6,
        "ytick.major.width": 0.6, "axes.spines.top": False, "axes.spines.right": False,
        "pdf.fonttype": 42, "savefig.bbox": "tight", "savefig.pad_inches": 0.03})


def fig_forest(D: dict, day: str, path: Path) -> None:
    panels = [("t2b", "near", "Pociąg → autobus: autobus „ucieka”", True),
              ("t2b", "good", "Pociąg → autobus: dobra przesiadka", False),
              ("b2t", "near", "Autobus → pociąg: podróżny nie zdąży", True),
              ("b2t", "good", "Autobus → pociąg: dobra przesiadka", False)]
    fig, axes = plt.subplots(2, 2, figsize=(6.4, 4.6), sharey=True)
    for ax, (dr, met, title, hi_bad) in zip(axes.flat, panels):
        o = D["overall"][day]["plain"][f"{dr}_{'near_miss' if met == 'near' else 'good'}"]
        rows = [("Wszystkie linie", None, o["observed"], o["null_mean"], o["null_p05"], o["null_p95"])]
        for ln in LINES:
            x = (D["per_line"][day].get(ln) or {}).get(T2B if dr == "t2b" else B2T)
            if not x or x.get("null_near_miss") is None:
                continue
            if met == "near":
                rows.append((f"Linia {ln}", ln, x["rep_near_miss"], x["null_near_miss"], x["null_near_miss_p05"], x["null_near_miss_p95"]))
            else:
                rows.append((f"Linia {ln}", ln, x["rep_good"], x["null_good"], x["null_good_p05"], x["null_good_p95"]))
        ys = list(range(len(rows)))[::-1]
        xmax = max(0.3, max(max(r[2] or 0, r[5] or 0) for r in rows)) * 1.12
        for y, (name, ln, obs, mean, p05, p95) in zip(ys, rows):
            ax.barh(y, p95 - p05, left=p05, height=0.5, color=BAND, edgecolor="none")
            ax.plot([mean, mean], [y - 0.3, y + 0.3], color=MUTED, lw=1.4)
            worse = obs > p95 if hi_bad else obs < p05
            better = obs < p05 if hi_bad else obs > p95
            c, mk = (BAD, "^" if hi_bad else "v") if worse else ((GOOD, "v" if hi_bad else "^") if better else (INK, "o"))
            ax.plot(obs, y, marker=mk, color=c, ms=6, mec="white", mew=1.2, zorder=3)
            ax.text(xmax * 0.995, y, f"{100 * obs:.0f}%".replace(".", ","), va="center", ha="right", fontsize=7, color=INK2)
        ax.set_yticks(ys, [r[0] for r in rows])
        for lab, r in zip(ax.get_yticklabels(), rows):
            lab.set_fontweight("bold" if r[1] is None else "normal")
        ax.set_xlim(0, xmax)
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{100 * v:.0f}%"))
        ax.grid(axis="x", color=RULE, lw=0.5)
        ax.set_axisbelow(True)
        ax.set_title(title, fontsize=8, loc="left", color=INK)
        ax.tick_params(axis="y", length=0)
    fig.tight_layout(h_pad=1.2, w_pad=1.0)
    fig.savefig(path)
    plt.close(fig)


def fig_hist(D: dict, day: str, path: Path) -> None:
    P = D["params"]
    fig, axes = plt.subplots(1, 2, figsize=(6.4, 2.3), sharey=True)
    for ax, (key, m, xlabel, title) in zip(axes, [
            ("bus_dep_minus_train_arr", P["m_t2b"], "odjazd autobusu minus przyjazd pociągu [min]", "Pociąg → autobus"),
            ("train_dep_minus_bus_arr", P["m_b2t"], "odjazd pociągu minus przyjazd autobusu [min]", "Autobus → pociąg")]):
        H = D["hist"][day][key]
        ax.axvspan(-2, m, color=BAD, alpha=0.09, lw=0)
        ax.axvspan(m, m + P["good_window"], color=GOOD, alpha=0.09, lw=0)
        ax.bar([e + 1 for e in H["edges"]], H["counts"], width=1.7, color=LC["1"], edgecolor="none")
        ax.axhline(H["random_level"], color=INK2, lw=1.2)
        ymax = max(max(H["counts"]), H["random_level"]) * 1.25
        ax.text(-1.6, ymax * 0.93, "ucieka", fontsize=6.5, color=BAD)
        ax.text(m + 0.4, ymax * 0.93, "dobra przesiadka", fontsize=6.5, color=GOOD)
        ax.set_ylim(0, ymax)
        ax.set_xlim(-20, 30)
        ax.set_xticks(range(-20, 31, 10))
        ax.set_xlabel(xlabel, fontsize=7)
        ax.set_title(title, fontsize=8, loc="left", color=INK)
        ax.grid(axis="y", color=RULE, lw=0.5)
        ax.set_axisbelow(True)
    axes[0].set_ylabel("pary pociąg–autobus", fontsize=7)
    fig.tight_layout(w_pad=1.0)
    fig.savefig(path)
    plt.close(fig)


def fig_timeline(D: dict, day: str, path: Path) -> None:
    P, T = D["params"], D["timeline"][day]
    lines = [ln for ln in LINES if any(b["line"] == ln and b["kind"] == "dep" for b in T["buses"])]
    lo, hi = 4.5 * 60, 23.5 * 60
    fig, ax = plt.subplots(figsize=(6.4, 0.45 * (len(lines) + 1) + 0.5))
    yT = len(lines)
    yL = {ln: len(lines) - 1 - i for i, ln in enumerate(lines)}
    deps = [b for b in T["buses"] if b["kind"] == "dep" and b["line"] in lines]
    for tr in T["trains_arr"]:
        for b in deps:
            if tr["t"] - P["near_before"] <= b["t"] < tr["t"] + P["m_t2b"]:
                ax.plot([tr["t"], b["t"]], [yT - 0.25, yL[b["line"]]], color=BAD, lw=0.7, alpha=0.8, zorder=1)
    for tr in T["trains_arr"]:
        if lo <= tr["t"] <= hi:
            big = tr["cat"] in IC_CATS
            ax.plot([tr["t"], tr["t"]], [yT - (0.3 if big else 0.15), yT + (0.3 if big else 0.15)],
                    color=INK if big else MUTED, lw=1.1, zorder=2)
    for ln in lines:
        ax.axhline(yL[ln], color=RULE, lw=0.5, zorder=0)
        xs = [b["t"] for b in deps if b["line"] == ln]
        ax.scatter(xs, [yL[ln]] * len(xs), s=16, color=LC[ln], edgecolor="white", linewidth=0.6, zorder=3)
    ax.set_yticks([yT] + [yL[ln] for ln in lines], ["Pociągi"] + [f"Linia {ln}" for ln in lines])
    ax.set_xticks([h * 60 for h in range(5, 24, 2)], [f"{h}:00" for h in range(5, 24, 2)])
    ax.set_xlim(lo, hi)
    ax.set_ylim(-0.6, yT + 0.6)
    ax.tick_params(axis="y", length=0)
    ax.spines["left"].set_visible(False)
    ax.grid(axis="x", color=RULE, lw=0.5)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_stages(R: dict, variant: str, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(6.4, 2.5))
    styles = {"Roboczy": (INK, "o"), "Sobota": (INK2, "s"), "Niedziela": (MUTED, "D")}
    for day in DAYS:
        if day not in R["day_types"]:
            continue
        c = R["day_types"][day]["variants"][variant]["curve"]
        col, mk = styles[day]
        xs, ys = [p["changes"] for p in c], [p["good_per_day"] for p in c]
        ax.plot(xs, ys, color=col, lw=1.6, marker=mk, ms=5, mec="white", mew=1)
        g0 = ys[0]
        ax.text(xs[-1] + 1.5, ys[-1], f"{DAYSHORT[day]}: {ys[0]:.0f} → {ys[-1]:.0f} (+{100 * (ys[-1] - g0) / g0:.0f}%)",
                va="center", fontsize=7, color=INK)
    ax.set_xlabel("zmienione kursy (etapy 1, 2 i wszystkie zmiany)", fontsize=7)
    ax.set_ylabel("dobre przesiadki dziennie", fontsize=7)
    ax.grid(color=RULE, lw=0.5)
    ax.set_axisbelow(True)
    ax.set_ylim(bottom=0)
    xm = max(p["changes"] for d in R["day_types"].values() for p in d["variants"][variant]["curve"])
    ax.set_xlim(-2, xm * 1.45)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_gantt(v: dict, path: Path) -> None:
    lo, hi = 4.5 * 60, 23.5 * 60
    fig, axes = plt.subplots(2, 1, figsize=(6.4, 0.16 * 2 * len(v["blocks_before"]) + 1.0), sharex=True)
    for ax, key, title in zip(axes, ("blocks_before", "blocks_after"), ("Obecny rozkład", "Po korekcie (wszystkie zmiany)")):
        blocks = v[key]
        for i, b in enumerate(blocks):
            y = len(blocks) - 1 - i
            for t in b:
                ax.barh(y, max(1.5, t["end"] - t["start"] - 1), left=t["start"], height=0.72,
                        color=LC.get(str(t["route"]), MUTED), edgecolor="none")
        ax.set_yticks([len(blocks) - 1 - i for i in range(len(blocks))], [f"#{i + 1}" for i in range(len(blocks))], fontsize=5.5)
        ax.set_xlim(lo, hi)
        ax.set_title(title, fontsize=8, loc="left", color=INK)
        ax.tick_params(axis="y", length=0)
        ax.spines["left"].set_visible(False)
        ax.grid(axis="x", color=RULE, lw=0.5)
        ax.set_axisbelow(True)
    axes[-1].set_xticks([h * 60 for h in range(5, 24, 2)], [f"{h}:00" for h in range(5, 24, 2)])
    routes = sorted({str(t["route"]) for b in v["blocks_before"] for t in b})
    handles = [matplotlib.patches.Patch(color=LC.get(r, MUTED), label=f"linia {r}") for r in routes]
    axes[0].legend(handles=handles, ncol=len(routes), fontsize=6.5, frameon=False, loc="lower left",
                   bbox_to_anchor=(0, 1.08), handlelength=1.2, columnspacing=1.2)
    fig.tight_layout(h_pad=0.8)
    fig.savefig(path)
    plt.close(fig)


# ---------------------------------------------------------------- LaTeX
PREAMBLE = r"""\documentclass[10pt,a4paper]{article}
\usepackage[a4paper, top=2.0cm, bottom=2.2cm, left=2.2cm, right=2.2cm, headsep=0.6cm]{geometry}
\usepackage{fontspec}
\setmainfont{texgyretermes}[Extension=.otf, UprightFont=*-regular, BoldFont=*-bold, ItalicFont=*-italic, BoldItalicFont=*-bolditalic]
\setsansfont{texgyreheros}[Extension=.otf, UprightFont=*-regular, BoldFont=*-bold, ItalicFont=*-italic, BoldItalicFont=*-bolditalic]
\usepackage{babel}
\babelprovide[import, main]{polish}
\usepackage{microtype}
\usepackage{array, booktabs, longtable, tabularx}
\usepackage[table]{xcolor}
\usepackage{graphicx}
\usepackage{enumitem}
\usepackage{parskip}
\usepackage{fancyhdr}
\usepackage{titlesec}
\usepackage[hidelinks]{hyperref}
\emergencystretch=3em
\setlist{itemsep=2pt, topsep=3pt, leftmargin=1.4em}
\definecolor{L1}{HTML}{2A78D6}\definecolor{L2}{HTML}{EB6834}\definecolor{L3}{HTML}{1BAF7A}
\definecolor{L4}{HTML}{EDA100}\definecolor{L7}{HTML}{E87BA4}\definecolor{muted}{HTML}{767A84}
\definecolor{bad}{HTML}{C23030}\definecolor{good}{HTML}{0A8A0A}
\newcommand{\linedot}[1]{\textcolor{#1}{\rule[0.1ex]{0.9ex}{0.9ex}}\,}
\newcommand{\mut}[1]{\textcolor{muted}{#1}}
\titleformat{\section}{\sffamily\large\bfseries}{\thesection.}{0.5em}{}
\titleformat{\subsection}{\sffamily\normalsize\bfseries}{\thesubsection.}{0.5em}{}
\titlespacing*{\section}{0pt}{1.2em}{0.4em}
\titlespacing*{\subsection}{0pt}{0.9em}{0.3em}
\setlength{\LTpre}{4pt}\setlength{\LTpost}{4pt}
\pagestyle{fancy}\fancyhf{}
\renewcommand{\headrulewidth}{0.3pt}
"""


# Public mode (--public): the reports without the references to the letters, for
# publication on a web page. PUBLIC["head"] is the text in the page header.
PUBLIC = {"on": False, "head": ""}


def att(letter_text: str, public_text: str) -> str:
    """Text for the attachment of the letter, or for the public report."""
    return public_text if PUBLIC["on"] else letter_text


def header(attachment: str, title: str) -> str:
    left = PUBLIC["head"] if PUBLIC["on"] else f"{attachment} do wniosku z dnia \\makebox[2.4cm]{{\\dotfill}} 2026 r."
    return (PREAMBLE + f"\\fancyhead[L]{{\\sffamily\\footnotesize {left}}}\n"
            f"\\fancyhead[R]{{\\sffamily\\footnotesize {title}}}\n\\fancyfoot[C]{{\\sffamily\\footnotesize \\thepage}}\n"
            "\\begin{document}\n")


def title_block(kicker: str, title: str, question: str, meta: str) -> str:
    return (f"{{\\sffamily\\footnotesize\\bfseries\\MakeUppercase{{{kicker}}}}}\\par\\smallskip\n"
            f"{{\\sffamily\\LARGE\\bfseries {title}}}\\par\\medskip\n"
            f"{{\\large {question}}}\\par\\medskip\n{{\\small\\mut{{{meta}}}}}\\par\\bigskip\n")


def figure(path: str, caption: str) -> str:
    """A figure in the text flow (no float), with its caption under it."""
    return (f"\\par\\medskip\\noindent\\begin{{minipage}}{{\\textwidth}}\\centering\\includegraphics[width=\\textwidth]{{{path}}}\\par\\smallskip\n"
            f"{{\\small\\mut{{{caption}}}}}\\end{{minipage}}\\par\\medskip\n")


# ---------------------------------------------------------------- attachment 2
def rep_txt(D: dict, d: str) -> str:
    """Date of the test day and, when used, a note on the actual train times."""
    act = (D.get("rep_times") or {}).get(d) == "actual"
    return fdate(D["rep_dates"][d]) + (", rzeczywiste godziny pociągów" if act else "")


def verdict(D: dict) -> str:
    """Answer to the question of the report, from the test results (same rules as the web report)."""
    P, days = D["params"], [d for d in DAYS if d in D["overall"]]
    W = D["overall"]["Roboczy"]["plain"]
    l1 = D["per_line"]["Roboczy"]["1"][T2B]
    nm = lambda d: D["overall"][d]["plain"]["t2b_near_miss"]
    worse = [d for d in days if nm(d)["percentile"] >= 0.95]
    edge = [d for d in days if 0.9 <= nm(d)["percentile"] < 0.95]
    w_good = "Roboczy" in worse
    t = ("W dzień roboczy teza się potwierdza. " if w_good else "W dzień roboczy teza się nie potwierdza dla całej sieci. ")
    t += (f"W dniu testowym ({rep_txt(D, 'Roboczy')}) autobus odjeżdża w chwili przyjazdu pociągu przy "
          f"\\textbf{{{pct(W['t2b_near_miss']['observed'])}}} pociągów. Rozkład losowy daje \\textbf{{{pct(W['t2b_near_miss']['null_mean'])}}} "
          f"(90\\,\\% wyników losowych: {pct(W['t2b_near_miss']['null_p05'])}--{pct(W['t2b_near_miss']['null_p95'])}).")
    if not w_good:
        if "Roboczy" in edge:
            t += " Wynik jest blisko górnej granicy losowości, ale mieści się w niej."
        t += " Wrażenie „uciekającego autobusu” jest prawdziwe, ale wynika głównie z liczby kursów i skupień pociągów, a nie z ułożenia rozkładu."
    t += (f" Rozkłady nie są ze sobą skoordynowane: według rozkładu planowego dobrych przesiadek (czekanie do {P['good_window']} min) "
          "jest mniej więcej tyle, ile przy rozkładzie losowym.")
    if l1.get("near_miss_pct", 0) >= 0.95:
        t += (f" Wyjątkiem jest \\textbf{{linia 1}} w dni robocze: autobus „ucieka” częściej niż losowo "
              f"({pct(l1['rep_near_miss'])} wobec {pct(l1['null_near_miss'])}).")
        eve = [c for c in D["collisions"].get("Roboczy") or [] if c["line"] == "1" and c["time"] >= "17:00" and c.get("gap")]
        if eve:
            t += (f" Chodzi głównie o {len(eve)} odjazdy po 17:00, po których następny kurs jest za "
                  f"{num(min(c['gap'] for c in eve))}--{num(max(c['gap'] for c in eve))} min.")
    other = [d for d in worse if d != "Roboczy"]
    if other:
        t += " " + "; ".join(f"{DAYNAME[d]} ({rep_txt(D, d)}): autobus „ucieka” częściej niż przy 95\\,\\% rozkładów losowych "
                             f"({pct(nm(d)['observed'])} wobec {pct(nm(d)['null_mean'])}), więc tego dnia teza się potwierdza" for d in other) + "."
    e2 = [d for d in edge if d != "Roboczy"]
    if e2:
        t += " " + " i ".join(DAYNAME[d] for d in e2) + ": wynik jest blisko górnej granicy losowości, ale kursów jest mało i test nie daje pewności."
    return t


def test_days_section(D: dict) -> str:
    T = sorted(D.get("test_days") or [], key=lambda r: r["date"])
    if not T:
        return ""
    P = D["params"]
    ws = [f"{w:g}" for w in (P.get("test_windows") or [P["good_window"]])]
    out = ["\\section{Dni testowe: rozkład planowy i rzeczywiste godziny pociągów}\n",
           "Rozkład ZKM jest ten sam każdego dnia danego typu, ale pociągi się spóźniają. Dlatego wyniki sprawdzono na minionych dniach, "
           "dla których są rzeczywiste godziny prawie wszystkich pociągów na stacji Iława Główna (monitor API PKP PLK). Dla każdego typu dnia "
           "dniem testowym jest dzień z największą liczbą pociągów spóźnionych ponad 5 min. Tabela podaje udział pociągów z dobrą przesiadką "
           f"na dowolną linię dla okien czekania {' i '.join(w + ' min' for w in ws)}: według planu / w rzeczywistości.\n\n"]
    wr = next((r for r in T if r["date"] == D["rep_dates"].get("Roboczy")), None)
    if wr:
        w0 = wr["windows"][ws[0]]
        out.append(f"W dniu roboczym {fdate(wr['date'])} średnie opóźnienie przyjazdu wynosiło {num(wr['delay_mean'], 1)} min, a {wr['late_over_5']} "
                   f"z {wr['arr_with_data']} pociągów przyjechało ponad 5 min po czasie. Przy oknie {ws[0]} min dobrą przesiadkę z pociągu na autobus "
                   f"miało {pct(w0['planned']['t2b_good'])} pociągów według planu i {pct(w0['actual']['t2b_good'])} w rzeczywistości")
        if len(ws) > 1:
            w1 = wr["windows"][ws[1]]
            out.append(f"; przy oknie {ws[1]} min: {pct(w1['planned']['t2b_good'])} i {pct(w1['actual']['t2b_good'])}")
        out.append(".\n\n")
    cols = [("Dzień", "l"), ("Dane", "r"), ("Opóźn. śr./75\\,\\%", "r"), ("$>$5 min", "r")]
    for w in ws:
        cols += [(f"P$\\to$A {w} min", "r"), (f"A$\\to$P {w} min", "r")]
    rows = []
    for r in T:
        rep = D["rep_dates"].get(r["day_type"]) == r["date"]
        day = f"{r['date'][8:10]}.{r['date'][5:7]} {({'Roboczy': 'rob.', 'Sobota': 'sob.', 'Niedziela': 'niedz.'})[r['day_type']]}"
        row = ["\\textbf{" + day + "}" if rep else day,
               f"{r['arr_with_data']}/{r['trains_arr']}", f"{num(r['delay_mean'], 1)}/{num(r['delay_p75'])} min", str(r["late_over_5"])]
        for w in ws:
            x = r["windows"][w]
            row += [f"{pct(x['planned']['t2b_good'])}/{pct(x['actual']['t2b_good'])}", f"{pct(x['planned']['b2t_good'])}/{pct(x['actual']['b2t_good'])}"]
        rows.append(row)
    out.append(table(cols, rows, size="footnotesize"))
    out.append("\\mut{\\footnotesize Pogrubienie: dzień testowy (rob.: dzień roboczy, sob.: sobota, niedz.: niedziela). P$\\to$A: z pociągu na autobus, A$\\to$P: z autobusu do pociągu, wartości: plan/rzeczywistość. "
               "Godziny autobusów są planowe, bo brak danych o faktycznych odjazdach. Spóźniony odjazd pociągu wydłuża czekanie na peronie, "
               "ale daje też czas autobusom, które według planu przyjeżdżają za późno.}\n")
    return "".join(out)


def attachment_przesiadki(D: dict) -> str:
    P = D["params"]
    W = D["overall"]["Roboczy"]["plain"]
    l1 = D["per_line"]["Roboczy"]["1"][T2B]
    icH, mon = D["dynamic"].get("ic_history"), D["dynamic"].get("plk_monitor")
    days = [d for d in DAYS if d in D["overall"]]
    edge = [d for d in days if d != "Roboczy" and D["overall"][d]["plain"]["t2b_near_miss"]["percentile"] >= 0.9]
    c = D["counts"]
    out = [header("Załącznik nr 2", "Przesiadki Iława Główna")]
    out.append(title_block(att("Załącznik nr 2 · analiza rozkładów", "Raport ZKM Iława · analiza rozkładów"), "Przesiadki Iława Główna",
                           "Czy autobusy ZKM odjeżdżają spod dworca akurat wtedy, gdy przyjeżdżają pociągi?",
                           f"Dworzec Iława Główna: {c['Roboczy']['trains']} pociągów w dzień roboczy. Przystanek Dworcowa: "
                           f"linie ZKM 1, 2, 3, 4, 7, {c['Roboczy']['bus_dep']} odjazdów w dzień roboczy. Stan na {fdate(D['generated'])}."))

    # answer (the same rules as the web report)
    out.append("\\section*{Odpowiedź}\n")
    out.append(verdict(D) + "\n\n")
    keep_ic = None
    if icH and icH.get("per_line_t2b"):
        lk = [v for v in icH["per_line_t2b"].values() if v["n_planned_good"] > 20]
        if lk:
            keep_ic = sum(v["p_keep_planned_good"] * v["n_planned_good"] for v in lk) / sum(v["n_planned_good"] for v in lk)
    r_keep = None
    if mon and mon.get("keep_by_line_category"):
        rk = [v for k, v in mon["keep_by_line_category"].items() if k.endswith("|R")]
        if rk:
            r_keep = sum(v["p_keep"] * v["n"] for v in rk) / sum(v["n"] for v in rk)
    ic_delay = icH["t2b"]["delay"]["median"] if icH else None
    r_delay = mon["delay_by_category"]["R"]["median"] if mon and mon["delay_by_category"].get("R") else None
    out.append(table([("Wskaźnik", "p{9.2cm}"), ("Wartość", "r"), ("Dla porównania", "p{4.2cm}")], [
        [f"Pociągi, którym autobus „ucieka” (dzień roboczy {fdate(D['rep_dates']['Roboczy'])})", pct(W["t2b_near_miss"]["observed"]),
         f"losowo {pct(W['t2b_near_miss']['null_mean'])}; {W['n_train_arr']} przyjazdów"],
        ["To samo dla linii 1 (dzień roboczy)", pct(l1["rep_near_miss"]),
         f"losowo {pct(l1['null_near_miss'])}; wyżej niż w {round(100 * l1['near_miss_pct'])}\\,\\% rozkładów losowych"],
        ["Mediana opóźnienia pociągów PKP Intercity w Iławie", f"{num(ic_delay)} min", f"POLREGIO: {num(r_delay)} min"],
        [f"Ciasne przesiadki z IC na daną linię (czekanie do {P['m_t2b'] + P['good_window']} min), które się udają",
         pct(keep_ic), f"POLREGIO: {pct(r_keep)}"]]))

    # how to read: glossary and method, before the results
    out.append("\\section*{Jak czytać ten raport}\n")
    out.append("\\subsection*{Słowniczek}\n\\begin{description}[itemsep=1pt, leftmargin=0pt]\n"
               "\\item[Para pociąg--linia] jeden pociąg i jedna linia ZKM. Podróżny potrzebuje konkretnej linii, więc każda para liczy się osobno.\n"
               f"\\item[Czas dojścia] {P['m_t2b']} min z peronu na przystanek Dworcowa i {P['m_b2t']} min z przystanku na peron.\n"
               "\\item[Czekanie] czas od dojścia na przystanek do odjazdu autobusu albo od dojścia na peron do odjazdu pociągu.\n"
               f"\\item[Dobra przesiadka] czekanie po dojściu najwyżej {P['good_window']} min.\n"
               f"\\item[Ciasna przesiadka] według rozkładu czekanie na linię najwyżej {P['m_t2b'] + P['good_window']} min od przyjazdu pociągu. Takie przesiadki najłatwiej zrywa opóźnienie.\n"
               "\\item[Autobus „ucieka”] odjeżdża od 2 min przed przyjazdem pociągu do chwili, w której podróżni dochodzą na przystanek. "
               "W drugą stronę: autobus przyjeżdża tak późno, że podróżni „nie zdążą” na pociąg.\n"
               "\\item[Rozkład losowy] rozkład ZKM, w którym każdą linię przesunięto o losowy czas od $-30$ do $+30$ min. Kursy i odstępy są takie same, "
               "znika tylko związek z pociągami. Porównanie z nim pokazuje, czy rozkład jest dopasowany do pociągów.\n"
               "\\item[90\\,\\% wyników losowych, percentyl] zakres, w którym mieści się 90\\,\\% wyników rozkładów losowych. Wynik poza nim jest lepszy albo gorszy niż przypadek.\n"
               "\\item[Kategorie pociągów] EIP, EIC, IC i TLK to pociągi PKP Intercity; R to pociągi regionalne POLREGIO.\n"
               "\\item[Przystanki Dworcowa] 005-01 to odjazdy do miasta, 079-00 to przyjazdy z miasta.\n"
               "\\item[Typ dnia] dzień roboczy, sobota albo niedziela i święto, według rozkładu ZKM.\n"
               "\\item[Dzień testowy] miniony dzień danego typu, dla którego są rzeczywiste godziny prawie wszystkich pociągów. Spośród takich dni "
               "wybrano dzień z największą liczbą pociągów spóźnionych ponad 5 min. Test tezy i wykresy jednego dnia używają rzeczywistych godzin pociągów z tego dnia.\n"
               "\\end{description}\n")
    out.append("\\subsection*{Metoda i założenia}\n\\begin{itemize}\n"
               "\\item \\textbf{Dane.} Rozkład ZKM z rozklad.com, rozkład pociągów wszystkich przewoźników z API PKP PLK, opóźnienia z API PKP PLK i spoznienia.me.\n"
               "\\item \\textbf{Model losowy.} 3000 powtórzeń dla sieci, 1000 dla każdej linii osobno.\n"
               f"\\item \\textbf{{Okres.}} Minione dni od {fdate(P['dates'][0])} do {fdate(P['dates'][1])}, z rozkładem ZKM ważnym od 1.09.2026. "
               "Wyniki zbiorcze używają planowych godzin pociągów. Dni testowe: "
               + "; ".join(f"{DAYSHORT[d]} {rep_txt(D, d)}" for d in days) + ".\n"
               "\\item \\textbf{Ograniczenia.} Brak danych o faktycznych odjazdach autobusów. Test przeprowadzono dla wielu linii i dni, "
               "więc pojedynczy wynik na granicy 95. percentyla może być przypadkowy.\n\\end{itemize}\n")

    # test days
    out.append(test_days_section(D))

    # test
    out.append("\\section{Test tezy: rozkład rzeczywisty na tle losowego}\n")
    out.append(f"Dla każdego pociągu sprawdzono, czy autobus odjeżdża w chwili, gdy podróżni jeszcze idą z peronu na przystanek: od 2 min przed "
               f"przyjazdem do {P['m_t2b']} min po przyjeździe. Ten sam wskaźnik policzono 3000 razy dla rozkładu, w którym każdą linię przesunięto "
               "o losowy czas od $-30$ do $+30$ min. Szary pas to zakres 90\\,\\% wyników losowych, pionowa kreska to średnia losowa. "
               "Znacznik poza pasem oznacza wynik lepszy (zielony, trójkąt w stronę lepszego wyniku) albo gorszy (czerwony) niż losowo. "
               "Test używa rzeczywistych godzin pociągów z dnia testowego danego typu.\n\n")
    FIG.mkdir(parents=True, exist_ok=True)
    fig_forest(D, "Roboczy", FIG / "przesiadki-test.pdf")
    out.append(figure("fig/przesiadki-test.pdf", f"Rys. 1. Dzień roboczy {rep_txt(D, 'Roboczy')}. Test obejmuje pociągi w godzinach kursowania danej linii."))
    rows = []
    for d in days:
        o = D["overall"][d]["plain"]
        rows.append([DAYNAME[d]] + [f"{pct(o[k]['observed'])} \\mut{{({pct(o[k]['null_mean'])})}}" for k in
                                    ("t2b_near_miss", "t2b_good", "b2t_near_miss", "b2t_good")])
    out.append("Wszystkie linie razem, dzień testowy każdego typu (w nawiasie średnia losowa):\n")
    out.append(table([("Typ dnia", "l"), ("Pociąg → autobus: ucieka", "r"), ("dobra", "r"),
                      ("Autobus → pociąg: nie zdąży", "r"), ("dobra", "r")], rows))

    # histogram
    out.append("\\section{Kiedy odjeżdża autobus względem przyjazdu pociągu}\n")
    out.append("Każda para „pociąg -- autobus” w odstępie od $-20$ do $+30$ min, w przedziałach 2-minutowych. Gdyby autobusy uciekały pociągom, "
               "słupki w czerwonym polu byłyby wyraźnie wyższe od linii poziomu losowego. Gdyby rozkłady były skoordynowane, wyższe byłyby słupki w zielonym polu.\n")
    fig_hist(D, "Roboczy", FIG / "przesiadki-odstepy.pdf")
    out.append(figure("fig/przesiadki-odstepy.pdf", f"Rys. 2. Dzień roboczy {rep_txt(D, 'Roboczy')}. Pozioma linia: poziom losowy."))

    # timeline
    out.append("\\section{Jeden dzień: przyjazdy pociągów i odjazdy autobusów}\n")
    out.append("Pionowe kreski to przyjazdy pociągów (długie: IC, EIP, EIC, TLK; krótkie: POLREGIO). Kropki to odjazdy autobusów spod dworca. "
               "Czerwona linia łączy pociąg z autobusem, który odjeżdża, zanim podróżni dojdą na przystanek.\n")
    fig_timeline(D, "Roboczy", FIG / "przesiadki-dzien.pdf")
    out.append(figure("fig/przesiadki-dzien.pdf", f"Rys. 3. Dzień roboczy {rep_txt(D, 'Roboczy')}."))

    # collisions
    out.append("\\section{Odjazdy, które „uciekają” pociągom}\n")
    out.append("Kursy, które w dniu testowym odjeżdżają w chwili przyjazdu co najmniej jednego pociągu, a następny kurs tej samej linii jest dopiero po 30 min lub później.\n")
    rows = []
    for d in days:
        for r in D["collisions"].get(d) or []:
            trains = "; ".join(f"{t['time']} {tex(t['cat'])} {tex(t['no'])}" for t in r["trains"])
            rows.append([DAYSHORT[d], r["time"], f"{dot(r['line'])} $\\to$ {tex(r['to'])}", trains, r.get("next") or "brak",
                         f"{num(r['gap'])} min" if r.get("gap") else "--"])
    out.append(table([("Dzień", "l"), ("Odjazd", "l"), ("Linia", "l"), ("Pociągi w tej chwili", "p{4.1cm}"), ("Następny", "l"), ("Czekanie", "r")],
                     rows, long=True, size="footnotesize"))

    # lines
    out.append("\\section{Czas oczekiwania na konkretną linię}\n")
    out.append(f"Podróżny potrzebuje konkretnej linii, a nie dowolnego autobusu. Mediana czasu od przyjazdu pociągu do odjazdu danej linii oraz od przyjazdu "
               f"autobusu do odjazdu pociągu, średnio ze wszystkich dni okresu analizy ({fdate(P['dates'][0])}--{fdate(P['dates'][1])}), z planowymi godzinami pociągów.\n")
    rows = []
    for d in days:
        for ln in LINES:
            x = D["per_line"][d].get(ln)
            if not x:
                continue
            a, b = x.get(T2B), x.get(B2T)
            rows.append([DAYSHORT[d], dot(ln), num((a or b)["n_buses_per_day"]), f"{num(a['median_wait'])} min" if a else "--",
                         pct(a["near_miss"]) if a else "--", pct(a["good"]) if a else "--", f"{num(b['median_wait'])} min" if b else "--"])
    out.append(table([("Dzień", "l"), ("Linia", "l"), ("Kursy/dzień", "r"), ("Pociąg → linia: mediana", "r"), ("„Ucieka”", "r"),
                      ("Dobra", "r"), ("Linia → pociąg: mediana", "r")], rows, long=True, size="footnotesize"))

    # dynamic
    out.append("\\section{Co robią opóźnienia pociągów}\n")
    icd, mnd = (icH or {}).get("days", []), (mon or {}).get("days", [])
    out.append(f"Rozkład ZKM porównano z faktycznymi czasami pociągów. Dla PKP Intercity są dane z {len(icd)} dni "
               f"({fdate(icd[0]) + '--' + fdate(icd[-1]) if icd else '--'}, źródło: spoznienia.me). Dla wszystkich przewoźników, także POLREGIO, "
               f"są dane z {len(mnd)} dni ({fdate(mnd[0]) + '--' + fdate(mnd[-1]) if mnd else '--'}, API PKP PLK). Dla autobusów nie ma publicznych "
               "danych o faktycznych odjazdach. Przyjęto odchylenie w granicach tolerancji ZKM: od 1 min przed czasem do 3 min po czasie. "
               f"„Ciasna” przesiadka to taka, w której według rozkładu podróżny czeka na swoją linię najwyżej {P['m_t2b'] + P['good_window']} min.\n\n")
    rows = []
    for lbl, obj in (("spoznienia.me", icH), ("API PLK", mon)):
        if not obj:
            continue
        for cat in ("EIP", "EIC", "IC", "TLK", "R"):
            v = obj["delay_by_category"].get(cat)
            if v:
                rows.append([f"{cat} \\mut{{{lbl}}}", num(v["n"]), f"{num(v['median'])} min", f"{num(v['p90'])} min", pct(v["share_over_5"])])
    out.append("\\subsection*{Opóźnienie przyjazdu do Iławy Głównej}\n")
    out.append(table([("Kategoria", "l"), ("n", "r"), ("Mediana", "r"), ("90. percentyl", "r"), ("Ponad 5 min", "r")], rows))
    rows = []
    for ln in LINES:
        a = icH["per_line_t2b"].get(ln) if icH else None
        b = mon["per_line_t2b"].get(ln) if mon else None
        r = mon["keep_by_line_category"].get(f"{ln}|R") if mon and mon.get("keep_by_line_category") else None
        if not a and not b:
            continue
        rows.append([dot(ln),
                     f"{pct(a['p_keep_planned_good'])} \\mut{{n={a['n_planned_good']}}}" if a and a["n_planned_good"] >= 10 else "--",
                     f"{pct(b['p_keep_planned_good'])} \\mut{{n={b['n_planned_good']}}}" if b and b["n_planned_good"] >= 5 else "--",
                     f"{pct(r['p_keep'])} \\mut{{n={r['n']}}}" if r else "--"])
    out.append("\\subsection*{Ciasne przesiadki pociąg → linia, które dochodzą do skutku}\n")
    out.append(table([("Linia", "l"), ("IC (spoznienia.me)", "r"), ("Wszystkie (API PLK)", "r"), ("POLREGIO", "r")], rows))
    if icH and icH.get("b2t"):
        b2 = icH["b2t"]
        out.append(f"W przesiadce autobus → pociąg opóźnienie pociągu nie zrywa połączenia, tylko wydłuża czekanie na peronie: średnio z "
                   f"{num(b2['planned_wait']['mean'], 1)} do {num(b2['actual_wait']['mean'], 1)} min (IC). Połączenie zrywa się tylko wtedy, gdy autobus "
                   f"przyjeżdża z opóźnieniem, a zapas jest mały: w modelu tolerancji ZKM dotyczy to {pct(1 - b2['p_keep_planned_bus_mean'])} ciasnych przesiadek.\n")

    # key
    K = D.get("key")
    if K:
        lay, reg = K["layover"].get("Roboczy", {}), K["regularity"].get("Roboczy", {})
        r2, r3 = reg.get("2", {}), reg.get("3", {})
        out.append("\\section{Według jakiego klucza ułożono rozkład?}\n")
        out.append("ZKM nie publikuje zasad, według których układa rozkład. Dane wykluczają cztery odpowiedzi i wskazują dwa tropy.\n"
                   "\\begin{itemize}\n\\item \\textbf{Nie pociągi.} Dopasowanie całej sieci do rozkładu kolejowego jest na poziomie losowym (punkt 1).\n"
                   f"\\item \\textbf{{Nie stały takt.}} Linie 2 i 3 odjeżdżają średnio co {num(min(r2['headway_median'], r3['headway_median']))}--"
                   f"{num(max(r2['headway_median'], r3['headway_median']))} min, ale o {r2['distinct_minutes']}--{r3['distinct_minutes']} różnych minutach godziny. "
                   f"Odstępy między kursami wynoszą od {num(min(r2['headway_min'], r3['headway_min']))} do {num(max(r2['headway_max'], r3['headway_max']))} min.\n"
                   f"\\item \\textbf{{Nie obiegi „na styk”.}} Od przyjazdu autobusu na Dworcową do najbliższego odjazdu tej samej linii mija zwykle "
                   f"{num(lay.get('2', {}).get('median'))} min (linia 2), {num(lay.get('3', {}).get('median'))} min (linia 3) i {num(lay.get('4', {}).get('median'))} min (linia 4).\n"
                   f"\\item \\textbf{{Nie szkoły.}} Rozkład nazywa się szkolnym, ale tylko {len(K['school_trips'])} kursy są oznaczone jako kursujące wyłącznie w dni nauki.\n"
                   "\\end{itemize}\n\\textbf{Trop pierwszy:} obecny rozkład ZKM lepiej pasuje do pociągów IC sprzed korekty rozkładu PKP z 30 sierpnia (druga tabela). "
                   "\\textbf{Trop drugi:} niektóre kierunki są obsłużone lepiej niż losowo, a pociągi z Olsztyna wyraźnie gorzej (pierwsza tabela).\n\n")

        def cell(cc, bad):
            if not cc:
                return "--"
            s = f"{pct(cc['observed'])} \\mut{{({pct(cc['null_mean'])})}}"
            if cc["percentile"] >= 0.95:
                s += " \\textcolor{bad}{gorzej}" if bad else " \\textcolor{good}{lepiej}"
            elif cc["percentile"] <= 0.05:
                s += " \\textcolor{good}{lepiej}" if bad else " \\textcolor{bad}{gorzej}"
            return s
        rows = [[f"{tex(r['group'])} \\mut{{{tex(r['by'])}}}", f"{r['n_arr']}/{r['n_dep']}", cell(r.get("t2b_near_miss"), True),
                 cell(r.get("t2b_good"), False), cell(r.get("b2t_near_miss"), True), cell(r.get("b2t_good"), False)]
                for r in K["fit"].get("Roboczy", [])]
        out.append("\\subsection*{Dopasowanie do grup pociągów w dzień roboczy}\n")
        out.append(table([("Grupa pociągów", "p{3.6cm}"), ("Przyj./odj.", "r"), ("P→A: ucieka", "l"), ("P→A: dobra", "l"),
                          ("A→P: nie zdąży", "l"), ("A→P: dobra", "l")], rows, size="footnotesize"))
        out.append("\\mut{\\footnotesize Wartość rzeczywista, w nawiasie średnia losowa. Ocena pojawia się, gdy wynik jest poza 90\\,\\% wyników losowych.}\n\n")
        ba = K.get("ic_before_after") or {}
        mets = [("t2b_near_miss", "Pociąg → autobus: autobus ucieka"), ("t2b_good", "Pociąg → autobus: dobra przesiadka"),
                ("b2t_near_miss", "Autobus → pociąg: nie zdąży"), ("b2t_good", "Autobus → pociąg: dobra przesiadka")]
        dks = list(ba.keys())
        rows = [[name] + [f"{pct(ba[d]['comparison'][k]['observed'])} \\mut{{({pct(ba[d]['comparison'][k]['null_mean'])})}}" for d in dks] for k, name in mets]
        out.append("\\subsection*{Pociągi IC przed korektą rozkładu PKP z 30 sierpnia i po niej}\n")
        out.append(table([("Wskaźnik", "l")] + [(f"{fdate(d)} {'(przed)' if d < '2026-08-30' else '(po)'}", "r") for d in dks], rows))
        out.append("\\mut{\\footnotesize Ten sam rozkład ZKM (od 1.09.2026) porównany z pociągami PKP Intercity z dnia roboczego przed korektą i po niej.}\n")

    # corrections pointer, monitoring, method
    out.append("\\section{Korekty rozkładu}\n")
    out.append("Propozycje zmian rozkładu przygotowuje osobne narzędzie. Przesuwa ono kursy o kilka minut tak, żeby przybyło dobrych przesiadek na Dworcowej, "
               "i zachowuje obiegi autobusów, przerwy kierowców, liczbę autobusów oraz czas pracy kierowców. " + att("Wyniki są w załączniku nr 3, a wykaz zmian w załączniku nr 1.", "Wyniki i pełny wykaz zmian są w raporcie „Korekty rozkładu ZKM Iława”.") + "\n")
    out.append("\\section{Pomiar faktycznych przyjazdów}\n")
    out.append("Faktyczne czasy pociągów zbiera monitor. Co 5 min pobiera z API PKP PLK stan kursów na stacji Iława Główna i zapisuje "
               "potwierdzone czasy przyjazdu i odjazdu. Monitor powinien pracować co najmniej 4 tygodnie, żeby dla każdego typu dnia było po kilka dni danych. "
               "Autobusy nie mają publicznych danych o faktycznych odjazdach: potrzebne są dane GPS z ZKM, obserwacje na przystanku albo założenie tolerancji ZKM, jak w tym raporcie.\n")
    srow = [r for r in D["sensitivity"] if r["day_type"] == "Roboczy"]
    rows = []
    for w in sorted({r["walk"] for r in srow}):
        a = next((r for r in srow if r["walk"] == w and r["direction"] == T2B), None)
        b = next((r for r in srow if r["walk"] == w and r["direction"] == B2T), None)
        rows.append([f"{w} min", pct(a and a["near_miss_rate"]), pct(a and a["good_rate"]), pct(b and b["near_miss_rate"]), pct(b and b["good_rate"])])
    out.append("\\section{Wrażliwość na czas dojścia}\n")
    out.append("Wyniki dla dnia roboczego przy innych czasach dojścia z peronu na przystanek i z przystanku na peron.\n")
    out.append(table([("Czas dojścia", "l"), ("P→A: ucieka", "r"), ("P→A: dobra", "r"), ("A→P: nie zdąży", "r"), ("A→P: dobra", "r")], rows))
    out.append("\\section*{Źródła}\n\\begin{itemize}\n"
               "\\item Rozkład jazdy ZKM Iława: \\url{https://rozklad.com/maps/index.php?IDKlienta=ILAWA_ZKM}\n"
               "\\item PKP PLK, Otwarte Dane Kolejowe: \\url{https://pdp-api.plk-sa.pl/}\n"
               "\\item Historia opóźnień PKP Intercity: \\url{https://spoznienia.me/}\n"
               "\\item Rozkład kolejowy GTFS: \\url{https://mkuran.pl/gtfs/}\n\\end{itemize}\n")
    out.append("\\end{document}\n")
    return "".join(out)


# ---------------------------------------------------------------- attachment 3
def pl_poc(n: int) -> str:
    """Polish plural of "pociąg" after a number."""
    return "pociąg" if n == 1 else "pociągi" if n % 10 in (2, 3, 4) and not 12 <= n % 100 <= 14 else "pociągów"


def qname(q) -> str:
    q = float(q or 0.5)
    return "mediana" if q == 0.5 else f"{num(100 * q)}.\\ percentyl"


def past_mean(R: dict, day: str, name: str) -> dict | None:
    return next((r for r in (R.get("past_test") or {}).get("summary", []) if r["day_type"] == day and r["timetable"] == name), None)


def past_section(R: dict) -> str:
    """Test of the timetables on past days with the actual train times."""
    PTR = R.get("past_test") or {}
    if not PTR.get("rows"):
        return ""
    names = {"obecny": "obecny rozkład", "propozycja": "pełna propozycja"}
    days = [d for d in DAYS if any(r["day_type"] == d for r in PTR["rows"])]
    out = ["\\section{Test na minionych dniach}\n",
           f"Obecny rozkład, etapy i pełną propozycję sprawdzono na {len(PTR['days'])} minionych dniach z danymi o opóźnieniach "
           f"({fdate(PTR['days'][0])}--{fdate(PTR['days'][-1])}). Rozkład ZKM i planowy rozkład pociągów są te same, zmieniają się tylko rzeczywiste "
           f"godziny pociągów. „Dobre”: czekanie po dojściu do {num(PTR['window'])} min. „Złapane”: czekanie do {num(PTR['window'])} min plus zapas "
           "na opóźnienie kategorii pociągu. Wartości to średnie na dzień.\n\n"]
    rows = []
    for d in days:
        for r in [x for x in PTR["summary"] if x["day_type"] == d]:
            rows.append([DAYSHORT[d] if r["timetable"] == "obecny" else "", names.get(r["timetable"], r["timetable"]), str(r["days"]),
                         num(r["plan_caught"]), num(r["actual_caught"]), num(r["plan_total"]), num(r["actual_total"]), num(r["actual_wait_t2b"], 1)])
    out.append(table([("Dzień", "l"), ("Rozkład", "l"), ("Dni", "r"), ("Złapane: plan", "r"), ("rzecz.", "r"), ("Dobre: plan", "r"), ("rzecz.", "r"),
                      ("Czekanie po pociągu [min]", "r")], rows, size="footnotesize"))
    part = sorted({r["date"] for r in PTR["rows"] if r["arr_with_data"] < 0.9 * r["trains_arr"]})
    wo = PTR.get("worst") or {}
    lines = []
    for d in days:
        w = wo.get(d)
        if not w:
            continue
        a = next((r for r in PTR["rows"] if r["date"] == w["date"] and r["timetable"] == "obecny"), None)
        b = next((r for r in PTR["rows"] if r["date"] == w["date"] and r["timetable"] == "propozycja"), None)
        if a and b:
            lines.append(f"{DAYSHORT[d]} {fdate(w['date'])}: {w['late_over_5']} {pl_poc(w['late_over_5'])} spóźnionych ponad 5 min, średnie opóźnienie {num(w['delay_mean'], 1)} min; "
                         f"złapane przesiadki {num(a['actual_caught'])} $\\to$ {num(b['actual_caught'])}, dobre {num(a['actual_total'])} $\\to$ {num(b['actual_total'])}")
    if lines:
        out.append("Najgorsze dni (najwięcej pociągów spóźnionych ponad 5 min):\n\\begin{itemize}\n" + "".join(f"\\item {x}.\n" for x in lines) + "\\end{itemize}\n")
    out.append("\\mut{\\footnotesize Czekanie po pociągu: średni czas od dojścia na przystanek do odjazdu najbliższego autobusu danej linii, dla wszystkich par "
               "pociąg--linia, najwyżej 60 min." + (f" W {len(part)} z {len(PTR['days'])} dni rzeczywiste godziny są znane tylko dla części pociągów "
               "(głównie PKP Intercity); pozostałe pociągi liczą się jako punktualne." if part else "") + "}\n")
    return "".join(out)


def attachment_korekty(R: dict, variant: str = "single") -> str:
    cfg = R["config"]
    sh, du, tr, dm = cfg["shifts"], cfg["duties"], cfg["transfers"], cfg["demand"]
    days = [d for d in DAYS if d in R["day_types"]]
    VN = {"single": "obiegi jednej linii", "interlined": "obiegi mieszane (zmiana linii na Dworcowej)"}
    out = [header("Załącznik nr 3", "Korekty rozkładu ZKM Iława")]
    feed = R.get("feed") or {}
    fs = str(feed.get("feed_start_date") or "20260901")
    out.append(title_block(att("Załącznik nr 3 · propozycja korekt", "Raport ZKM Iława · propozycja korekt"), "Korekty rozkładu ZKM Iława",
                           "Przesunięcia kursów o kilka minut, które dają więcej przesiadek z pociągów i do pociągów na przystanku Dworcowa.",
                           f"Rozkład ZKM ważny od {fdate(fs[:4] + '-' + fs[4:6] + '-' + fs[6:8])}. "
                           f"Przystanki: {', '.join(R['station_stops'])}. Obliczenia z dnia {fdate(R['generated'])}."))
    out.append("\\section*{Wynik}\n")
    out.append("Obiegi autobusów, przerwy kierowców, liczba autobusów i łączny czas pracy kierowców zostają zachowane. "
               f"Przesiadka jest dobra, gdy po dojściu z peronu ({tr['walk_train_to_bus']} min) lub na peron ({tr['walk_bus_to_train']} min) czeka się najwyżej "
               f"{tr['good_window']} min. Liczone są pary pociąg--linia, bo podróżny potrzebuje konkretnej linii. Do przyjazdu pociągu doliczany jest zapas "
               "na opóźnienie jego kategorii: " + ", ".join(f"{k} {num(v)} min" for k, v in (R.get("buffers") or {}).items()) + ". "
               + ("Autobus może tylko przyjechać na Dworcową wcześniej albo odjechać z niej później, więc każda zmiana daje przesiadce więcej czasu. "
                  if sh.get("shift_direction") == "buffer" else "")
               + f"Wyniki dotyczą wariantu: {VN[variant]}.\n")
    rows = {k: [] for k in ("a", "b", "c", "d", "e", "f", "g")}
    for d in days:
        v, r = R["day_types"][d]["variants"][variant], R["day_types"][d]
        nd = v["good_before"]["n_dates"]
        gb0, gb1 = v["good_buffered_before"]["total"] / nd, v["good_buffered_after"]["total"] / nd
        g0, g1 = v["good_before"]["total"] / nd, v["good_after"]["total"] / nd
        pa, pb = past_mean(R, d, "obecny"), past_mean(R, d, "propozycja")
        rows["a"].append(f"{num(gb0)} → {num(gb1)} \\mut{{({spct((gb1 - gb0) / gb0)})}}")
        rows["b"].append(f"{num(g0)} → {num(g1)} \\mut{{({spct((g1 - g0) / g0)})}}")
        rows["c"].append(f"{num(pa['actual_caught'])} → {num(pb['actual_caught'])} \\mut{{({spct((pb['actual_caught'] - pa['actual_caught']) / pa['actual_caught'])})}}" if pa and pb else "--")
        rows["d"].append(f"{len(v['changes'])} z {r['trips']}")
        rows["e"].append(f"{num(v['indicators_before']['duty_hours'], 1)} → {num(v['indicators_after']['duty_hours'], 1)}")
        rows["f"].append(f"{v['indicators_before']['peak_vehicles']} → {v['indicators_after']['peak_vehicles']}")
        rows["g"].append(f"{v['breaks_before']['ok_breaks']}/{v['breaks_before']['duties']} → {v['breaks_after']['ok_breaks']}/{v['breaks_after']['duties']}")
    labels = {"a": "Dobre przesiadki dziennie, z zapasem na opóźnienie", "b": "Bez zapasu (pociągi punktualne)",
              "c": "Minione dni, rzeczywiste godziny pociągów: złapane przesiadki dziennie", "d": "Zmienione kursy (wszystkie etapy)",
              "e": "Godziny pracy kierowców dziennie", "f": "Autobusy w szczycie", "g": "Służby z przerwą zgodną z ustawą"}
    out.append(table([("Wskaźnik", "p{5.4cm}")] + [(DAYNAME[d].split(" ")[0], "r") for d in days],
                     [[labels[k]] + rows[k] for k in labels], size="footnotesize"))

    # how to read: glossary and method, before the results
    out.append("\\section*{Jak czytać ten raport}\n")
    out.append("\\subsection*{Słowniczek}\n\\begin{description}[itemsep=1pt, leftmargin=0pt]\n"
               "\\item[Para pociąg--linia] jeden pociąg i jedna linia ZKM. Podróżny potrzebuje konkretnej linii, więc każda para liczy się osobno.\n"
               f"\\item[Dobra przesiadka] czekanie po dojściu najwyżej {tr['good_window']} min. Dojście trwa {tr['walk_train_to_bus']} min z peronu na przystanek "
               f"i {tr['walk_bus_to_train']} min z przystanku na peron.\n"
               f"\\item[Zapas na opóźnienie] czas doliczany do planowego przyjazdu pociągu: {qname(tr.get('delay_buffer_quantile'))} opóźnienia pociągów "
               f"danej kategorii na stacji Iława Główna, najwyżej {tr['delay_buffer_cap']} min. Obecnie: "
               + ", ".join(f"{k} {num(v)} min" for k, v in (R.get("buffers") or {}).items()) + ". Przy przesiadce z pociągu okno czekania zaczyna się po zapasie.\n"
               f"\\item[Złapana przesiadka] przesiadka, przy której po dojściu czeka się najwyżej {tr['good_window']} min plus zapas na opóźnienie kategorii pociągu. "
               "Na minionych dniach, przy rzeczywistych godzinach pociągów, pokazuje, ilu podróżnych zdąży na autobus (lub pociąg), który daje im rozkład. "
               "Gdy przesiadka się nie uda, czeka się na następny autobus linii.\n"
               "\\item[Kierunek przesunięć] " + ("autobus może tylko przyjechać na Dworcową wcześniej albo odjechać z niej później. Każda zmiana daje przesiadce "
                                                "więcej czasu, więc nie zależy od punktualności autobusu.\n" if sh.get("shift_direction") == "buffer"
                                                else "autobus może przyjechać i odjechać wcześniej lub później.\n") +
               "\\item[Przesunięcie kursu] zmiana godzin kursu na wszystkich jego przystankach o tyle samo minut.\n"
               "\\item[Etap] grupa zmian do wprowadzenia razem: etap 1 to 20 najskuteczniejszych zmian, etap 2 to łącznie 40, ostatni etap to wszystkie. Każdy etap zachowuje zmiany poprzedniego.\n"
               "\\item[Zmiana towarzysząca] zmiana bez własnego zysku, potrzebna do zachowania przerwy kierowcy, postoju autobusu albo kolejności kursów po innej zmianie.\n"
               "\\item[Obieg] kolejne kursy jednego autobusu w ciągu dnia. Obiegi jednej linii: autobus jeździ cały dzień na tej samej linii. "
               "Obiegi mieszane: autobus może zmienić linię na Dworcowej.\n"
               "\\item[Postój] czas między kursami jednego autobusu.\n"
               "\\item[Służba] czas pracy jednego kierowcy, od przygotowania autobusu do końca ostatniego kursu.\n"
               "\\item[Przerwa] postój kierowcy trwający co najmniej 15 min, wliczany do przerwy wymaganej ustawą o czasie pracy kierowców.\n"
               "\\item[Wozokilometr, wozogodzina] kilometr i godzina pracy autobusu, razem z dojazdami z zajezdni.\n"
               "\\item[Wariant ostrożny, średni, optymistyczny] założenia o liczbie pasażerów: udział podróżnych kolejowych, którzy jadą dalej autobusem, "
               "i wrażliwość na czas czekania.\n"
               "\\end{description}\n")
    out.append("\\subsection*{Metoda i założenia}\n\\begin{itemize}\n"
               "\\item \\textbf{Dane.} Rozkład ZKM z rozklad.com, przetworzony do formatu GTFS. Rozkład pociągów z API PKP PLK, opóźnienia z API PKP PLK i spoznienia.me.\n"
               f"\\item \\textbf{{Przesunięcia.}} Kurs zmienia się najwyżej o {sh['max_shift']} min (kursy szkolne o {sh['school_max_shift']} min). "
               "Każdy zmieniony kurs i każda minuta zmiany mają karę, więc optymalizator (program całkowitoliczbowy) zmienia tylko te kursy, które coś dają.\n"
               f"\\item \\textbf{{Obiegi.}} Postój między kursami autobusu co najmniej {cfg['blocks']['min_layover']} min. Kolejność kursów linii na Dworcowej zostaje ta sama.\n"
               "\\item \\textbf{Koszt.} Łączny czas pracy autobusów i kierowców nie może wzrosnąć, kilometry są te same. Każda służba zachowuje przerwę wymaganą ustawą.\n"
               f"\\item \\textbf{{Pociągi.}} Wszystkie pociągi mają tę samą wagę. Zapas na opóźnienie: {qname(tr.get('delay_buffer_quantile'))} z danych, "
               f"najwyżej {tr['delay_buffer_cap']} min.\n"
               "\\item \\textbf{Test na minionych dniach.} Obecny rozkład, każdy etap i pełna propozycja są sprawdzane na minionych dniach z danymi "
               "o opóźnieniach, od początku obowiązywania rozkładu ZKM. Godziny pociągów są rzeczywiste: z monitora PKP PLK (wszyscy przewoźnicy), a gdy go brak, "
               "z historii PKP Intercity (spoznienia.me). Pociąg bez danych liczy się jako punktualny. Godziny autobusów są planowe.\n"
               "\\item \\textbf{Ograniczenia.} Obiegi i służby są odtworzone z rozkładu, a nie pobrane z ZKM. Brak danych o faktycznych odjazdach autobusów. "
               "Propozycję trzeba sprawdzić z dyspozytorem ZKM przed wdrożeniem.\n"
               "\\item \\textbf{Powtarzalność.} Narzędzie można uruchomić ponownie po każdej zmianie rozkładu ZKM lub PKP.\n"
               "\\end{itemize}\n")

    # stages
    out.append("\\section{Etapy wdrożenia}\n")
    out.append("Każdy etap zachowuje zmiany poprzedniego i dodaje kolejne, więc rozkład można zmieniać krok po kroku. Etap 1 to 20 najbardziej opłacalnych "
               "zmian, etap 2 to łącznie 40 zmian, ostatni etap nie ma limitu. Liczone z zapasem na opóźnienie pociągu.\n")
    FIG.mkdir(parents=True, exist_ok=True)
    fig_stages(R, variant, FIG / "korekty-etapy.pdf")
    out.append(figure("fig/korekty-etapy.pdf", "Rys. 1. Dobre przesiadki dziennie po kolejnych etapach."))
    rows = []
    for d in days:
        c = R["day_types"][d]["variants"][variant]["curve"]
        g0 = c[0]["good_per_day"]
        for p in c:
            rows.append([DAYSHORT[d] if p["stage"] == 0 else "", "obecny rozkład" if not p["stage"] else f"etap {p['stage']}",
                         num(p["changes"]), num(p["good_per_day"]), spct((p["good_per_day"] - g0) / g0) if p["stage"] else "--"])
    out.append(table([("Dzień", "l"), ("Etap", "l"), ("Zmienione kursy", "r"), ("Dobre przesiadki/dzień", "r"), ("Zmiana", "r")], rows))
    out.append("Pełny wykaz zmienionych kursów, z pociągami, dla których przesiadka staje się dogodna, "
               + att("zawiera załącznik nr 1.", "jest w ostatnim rozdziale.") + "\n")
    out.append(past_section(R))

    # boards
    out.append("\\section{Odjazdy z przystanku Dworcowa po korekcie}\n")
    out.append("Godziny odjazdów w dniu planowania. Zmienione godziny: obecna $\\to$ \\textbf{proponowana}.\n")
    for d in days:
        r, v = R["day_types"][d], R["day_types"][d]["variants"][variant]
        after = {b["id"]: b for b in v["bus_after"]}
        lines = sorted({b["route"] for b in r["bus_rep"] if b["dep"] is not None}, key=lambda x: (len(x), x))
        out.append(f"\\subsection*{{{DAYNAME[d]} ({fdate(r['date'])})}}\n{{\\footnotesize\n")
        for ln in lines:
            deps = sorted(((b["dep"], after.get(b["id"], b)["dep"]) for b in r["bus_rep"] if b["route"] == ln and b["dep"] is not None), key=lambda x: x[1])
            items = [hm(o) if o == n else f"{hm(o)}$\\to$\\textbf{{{hm(n)}}}" for o, n in deps]
            out.append(f"\\noindent{dot(ln)}\\textbf{{Linia {tex(ln)}:}} " + ", ".join(items) + "\\par\\smallskip\n")
        out.append("}\n")

    # blocks and duties
    v = R["day_types"]["Roboczy"]["variants"][variant]
    out.append("\\section{Obiegi autobusów i służby kierowców}\n")
    out.append("Każdy wiersz to jeden autobus, każdy pasek to kurs. Obiegi odtworzono z rozkładu, bo ZKM ich nie publikuje: autobus bierze najbliższy kurs "
               "z tego samego przystanku końcowego, najpierw tej samej linii. Przesunięcia mieszczą się w postojach, więc obiegi się nie zmieniają.\n")
    fig_gantt(v, FIG / "korekty-obiegi.pdf")
    out.append(figure("fig/korekty-obiegi.pdf", f"Rys. 2. Obiegi autobusów w dzień roboczy ({VN[variant]})."))
    b0, b1 = v["breaks_before"], v["breaks_after"]
    out.append(f"Obiegi podzielono na służby kierowców na Dworcowej (najdłużej {num(float(du['max_duty']) / 60, 1)} godz. z przygotowaniem). "
               f"Przerwa: przy ponad 6 godz. jazdy co najmniej {du['break_after_6h']} min, przy ponad 8 godz. {du['break_after_8h']} min, w częściach po co najmniej "
               f"{du['break_part']} min w rozkładzie. Zgodnych służb w dzień roboczy: {b0['ok_breaks']} z {b0['duties']} obecnie, {b1['ok_breaks']} z {b1['duties']} po korekcie.\n")
    rows = [[f"\\#{x['block']}", x["start"], x["end"], num(x["driving_h"], 1), ", ".join(str(b) for b in x["breaks"]) or "--",
             str(x["needed"] or "--"), "zgodna" if x["ok"] else "\\textcolor{bad}{za krótka przerwa}"] for x in b1["list"]]
    out.append(table([("Autobus", "l"), ("Od", "l"), ("Do", "l"), ("Jazda [h]", "r"), ("Przerwy [min]", "p{5.2cm}"), ("Wymagane", "r"), ("", "l")],
                     rows, long=True, size="footnotesize"))

    # gaps
    out.append("\\section{Dodatkowe kursy bez dodatkowego autobusu}\n")
    out.append("Autobusy, które czekają na Dworcowej dłużej niż jeden kurs tam i z powrotem, mogą wykonać dodatkowy kurs tej samej linii bez nowego autobusu. "
               "Gdy postój wypada w środku służby, kierowca jest już w pracy, a kurs przechodzi tylko wtedy, gdy zostaje przerwa wymagana ustawą. "
               "Gdy służba kończy się na początku postoju, kurs wydłuża ją o podany czas. Każdy kurs sprawdzono osobno.\n")
    rows = []
    for d in days:
        for g in R["day_types"][d]["variants"][variant]["gaps"]:
            rows.append([DAYSHORT[d], dot(g["line"]), g["depart"], g["return"], f"{g['idle_from']}--{g['idle_to']}", f"+{g['new_transfers']}",
                         f"{num(g['line_gap_min'])} min" if g.get("line_gap_min") else "--", num(g["km"], 1),
                         "w służbie" if g.get("inside_duty", True) else f"+{num(g['extra_driver_h'], 1)} godz."])
    out.append(table([("Dzień", "l"), ("Linia", "l"), ("Odjazd", "l"), ("Powrót", "l"), ("Postój", "l"), ("Nowe dobre", "r"),
                      ("Luka w linii", "r"), ("km", "r"), ("Kierowca", "l")], rows, long=True, size="footnotesize"))

    # transport work
    out.append("\\section{Praca przewozowa}\n")
    out.append("Wskaźniki dzienne obecnego i proponowanego rozkładu, w obu wariantach obiegów. Wozokilometry obejmują dojazdy z zajezdni. "
               "Koszty i przychody w złotych narzędzie liczy dopiero po wpisaniu stawek i danych o sprzedaży biletów przewoźnika.\n")
    rows = []
    for d in days:
        for vn in R["day_types"][d]["variants"]:
            vv = R["day_types"][d]["variants"][vn]
            for when, lab in (("before", "obecny"), ("after", "proponowany")):
                i = vv[f"indicators_{when}"]
                rows.append([DAYSHORT[d], "jednej linii" if vn == "single" else "mieszane", lab, str(i["peak_vehicles"]), num(i["total_km"]),
                             num(i["vehicle_hours"], 1), str(i["duties"]), num(i["duty_hours"], 1)])
    out.append(table([("Dzień", "l"), ("Obiegi", "l"), ("Rozkład", "l"), ("Autobusy", "r"), ("Wozokm", "r"), ("Wozogodz.", "r"),
                      ("Służby", "r"), ("Godz. kierowców", "r")], rows, long=True, size="footnotesize"))
    rows = []
    for vn, A in (R.get("annual") or {}).items():
        for when, lab in (("before", "obecny"), ("after", "proponowany")):
            x = A[when]
            rows.append(["jednej linii" if vn == "single" else "mieszane", lab, str(x["peak_vehicles"]), num(x["total_km"]), num(x["vehicle_hours"]), num(x["duty_hours"])])
    out.append("\\subsection*{Rok, wszystkie typy dni}\n")
    out.append(table([("Obiegi", "l"), ("Rozkład", "l"), ("Autobusy w szczycie", "r"), ("Wozokm/rok", "r"), ("Wozogodz./rok", "r"), ("Godz. kierowców/rok", "r")], rows))
    if "interlined" in R["day_types"]["Roboczy"]["variants"]:
        vi = R["day_types"]["Roboczy"]["variants"]["interlined"]["indicators_before"]
        vs = R["day_types"]["Roboczy"]["variants"]["single"]["indicators_before"]
        out.append(f"Jeżeli autobus może zmienić linię na Dworcowej, obecny rozkład wymaga w dzień roboczy {vi['peak_vehicles']} autobusów w szczycie zamiast "
                   f"{vs['peak_vehicles']}, a godzin pracy kierowców jest o {pct(1 - vi['duty_hours'] / vs['duty_hours'])} mniej. "
                   "Nie wiadomo, jak ZKM układa obiegi obecnie.\n")

    # passengers
    v = R["day_types"]["Roboczy"]["variants"][variant]
    d_ = v["demand"]
    out.append("\\section{Ilu pasażerów mogą dać lepsze przesiadki}\n")
    out.append(f"Na stacji Iława Główna wsiada i wysiada średnio {num(float(dm['station_daily_exchange']))} osób na dobę (Urząd Transportu Kolejowego, 2025). "
               "Część z nich przesiada się tylko między pociągami. Nie wiadomo, ilu podróżnych jedzie dalej autobusem ZKM, dlatego narzędzie liczy trzy warianty: "
               "udział autobusu i wrażliwość na czas czekania to założenia, nie pomiar. "
               f"Średni czas czekania na przesiadkę (pociąg--linia, w obu kierunkach) zmienia się w dzień roboczy z {num(d_['wait_before'], 1)} do "
               f"{num(d_['wait_after'], 1)} min ({spct(d_['wait_change'], 1)}).\n")
    ad = (R.get("annual_demand") or {}).get(variant, {})
    nm = {"low": "ostrożny", "mid": "średni", "high": "optymistyczny"}
    rows = [[nm[k], pct(d_[k]["bus_share"]), num(d_[k]["elasticity"], 1), num(d_[k]["base_trips_day"]), spct(d_[k]["change"], 1),
             num(d_[k]["extra_trips_day"]), num(ad.get(k)) if ad else "--"] for k in ("low", "mid", "high")]
    out.append(table([("Wariant", "l"), ("Udział autobusu", "r"), ("Elastyczność", "r"), ("Przejazdy/dzień", "r"), ("Zmiana", "r"),
                      ("Dodatkowe/dzień", "r"), ("Dodatkowe/rok", "r")], rows))
    out.append("\\mut{\\footnotesize Wartości dzienne dotyczą dnia roboczego, roczne obejmują wszystkie typy dni. Elastyczność: o tyle procent zmienia się "
               "liczba pasażerów, gdy czas czekania zmienia się o 1\\,\\%.}\n")

    out.append("\\end{document}\n")
    return "".join(out)


# ---------------------------------------------------------------- main
def newest_plan() -> Path:
    runs = sorted(p for p in (ROOT / "data" / "plan").glob("*/result.json"))
    if not runs:
        raise SystemExit("No data/plan/<date>/result.json. Run zkmplan.py first.")
    return runs[-1]


def compile_pdf(tex_path: Path) -> None:
    exe = shutil.which("xelatex")
    if not exe:
        print("xelatex not found: compile the .tex files by hand.")
        return
    for _ in range(2):
        subprocess.run([exe, "-interaction=nonstopmode", "-halt-on-error", tex_path.name], cwd=tex_path.parent,
                       stdout=subprocess.DEVNULL, check=True)


def changes_section(R: dict) -> str:
    """The list of changes (the same table as attachment 1 of the letter)."""
    from zkmplan import write_latex
    f = OUT / "wykaz-zmian.tex"
    write_latex(R, f)
    return ("\\clearpage\n\\section{Wykaz proponowanych zmian}\n"
            "Wykaz dotyczy obiegów, w których autobus zostaje na jednej linii. Godziny są godzinami na przystanku Dworcowa: "
            "obecna $\\to$ \\textbf{proponowana}. Autobus przyjeżdża tylko wcześniej albo odjeżdża tylko później niż obecnie.\n"
            "\\begin{itemize}[itemsep=1pt]\n"
            "\\item \\textbf{Etap}: 1 -- najskuteczniejsze zmiany (do 20 kursów), 2 -- do 40 kursów łącznie, 3 -- pozostałe zmiany. "
            "Każdy etap zachowuje zmiany poprzedniego.\n"
            "\\item \\textbf{Inne przyst.}: zmiana godzin na pozostałych przystankach kursu, w minutach.\n"
            "\\item \\textbf{Zysk}: liczba dogodnych przesiadek dziennie, którą daje ta jedna zmiana.\n"
            "\\item \\textbf{Pociągi}: pociągi, z których (\\emph{z}) lub na które (\\emph{na}) przesiadka staje się dogodna. "
            "Kursywą: pociągi, dla których przesiadka przestaje być dogodna. Zmiana towarzysząca zachowuje przerwę kierowcy, "
            "postój autobusu lub kolejność kursów po innej zmianie.\n\\end{itemize}\n"
            "{\\footnotesize\\setlength{\\tabcolsep}{3pt}\\renewcommand{\\arraystretch}{1.15}\n\\input{wykaz-zmian.tex}\n}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report-data", default=str(ROOT / "reports" / "report_data.json"))
    ap.add_argument("--plan", default=None, help="result.json of zkmplan.py (default: newest run)")
    ap.add_argument("--variant", default="single", choices=["single", "interlined"])
    ap.add_argument("--pdf", action="store_true", help="compile with xelatex")
    ap.add_argument("--public", default=None, metavar="HEADER",
                    help="make the public versions (no references to the letters, with the list of changes); "
                         "HEADER is the text in the page header, for example the web address of the page")
    a = ap.parse_args()
    setup_mpl()
    OUT.mkdir(parents=True, exist_ok=True)
    D = json.loads(Path(a.report_data).read_text(encoding="utf-8"))
    R = json.loads(Path(a.plan or newest_plan()).read_text(encoding="utf-8"))
    p2, p3 = OUT / "zalacznik-2-przesiadki.tex", OUT / "zalacznik-3-korekty.tex"
    if a.public is not None:
        PUBLIC.update(on=True, head=tex(a.public))
        p2, p3 = OUT / "przesiadki-ilawa-glowna.tex", OUT / "korekty-rozkladu-zkm-ilawa.tex"
    p2.write_text(attachment_przesiadki(D), encoding="utf-8")
    k = attachment_korekty(R, a.variant)
    if PUBLIC["on"]:
        k = k.replace("\\end{document}\n", changes_section(R) + "\\end{document}\n")
    p3.write_text(k, encoding="utf-8")
    print(f"LaTeX: {p2}\n       {p3}")
    if a.pdf:
        for p in (p2, p3):
            compile_pdf(p)
            print(f"PDF:   {p.with_suffix('.pdf')}")


if __name__ == "__main__":
    main()

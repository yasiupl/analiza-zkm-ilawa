# ilawa-transport

This project measures how well the city buses (ZKM Iława) connect with the trains at the railway station Iława Główna. It also proposes small changes to the bus timetable that give more good connections.

The project has these parts:

- **Transfer analysis.** It compares the bus timetable with the planned train timetable and with the actual train times. It tests the result against random bus timetables.
- **Monitor.** It collects the actual train times at the station.
- **GTFS feed of ZKM Iława.** It converts the public bus timetable into a GTFS feed.
- **Timetable corrections (zkmplan).** It moves bus trips by a small number of minutes to get more good connections. The changes keep the vehicle blocks, the driver breaks, the number of buses and the driver hours.
- **Reports.** Web pages and printable PDF files with the results.

## Scope

| Item | Value |
|---|---|
| Railway station | Iława Główna (all carriers) |
| Bus stop | Dworcowa: 005-01 (departures to the city), 079-00 (arrivals from the city) |
| Bus lines | 1, 2, 3, 4, 7 |
| Day types | Roboczy (Monday to Friday), Sobota (Saturday), Niedziela (Sunday and public holidays) |

## Data sources

| Data | Source | Access |
|---|---|---|
| Bus timetable | rozklad.com (stop timetables of ZKM Iława) | Public web page |
| Planned trains, all carriers | GTFS `polish_trains.zip`, mkuran.pl | Public file |
| Planned and actual trains, all carriers | PKP PLK Otwarte Dane Kolejowe API | Free API key |
| History of PKP Intercity delays | api.spoznienia.me | Public API, 20 requests per minute |

There is no public data on the actual times of the buses. All calculations use the planned bus times. The dynamic analysis adds the ZKM tolerance (1 min early to 3 min late).

## Definitions

| Term | Meaning |
|---|---|
| Walk time (`m`) | Time from the platform to the bus stop (train to bus) or from the bus stop to the platform (bus to train). The defaults are 4 min and 5 min. |
| Wait | Time from the end of the walk to the departure of the bus (train to bus) or of the train (bus to train). |
| Good window | Longest wait that is a good connection. The default is 5 min. |
| Good connection | The wait is not longer than the good window. |
| Train-line pair | One train and one bus line. A passenger needs one specific line, so the tool counts each pair. |
| Near miss (train to bus) | A bus departs in the interval from 2 min before the train arrival to `m` after it. The passengers see the bus go. |
| Near miss (bus to train) | A bus arrives after the train departure minus `m` and not later than 2 min after it. The passengers cannot get the train. |
| Delay buffer | Time that zkmplan adds to the planned arrival of a train before the good window starts. It is a quantile of the arrival delays of the train category at the station, with an upper limit. |
| Caught connection | The wait is not longer than the good window plus the delay buffer of the train category. With the actual train times, it shows how many passengers get the bus (or the train) that the timetable gives them. |
| Null model | Each bus line moves by a random time from -30 to +30 min, many times. This keeps the bus intervals but removes all relation to the trains. If the actual timetable is not better than the null model, the timetables are not coordinated. |
| Test day | A past day of a day type with actual times for almost all trains. The analysis uses the day with the most trains that are more than 5 min late. |
| Capacity weight | Each train has an assumed number of seats by category. The weighted results show the effect on passengers, not on trains. |

## Requirements

1. Python 3.10 or later.
2. The Python packages in `requirements.txt`:

   ```
   pip install -r requirements.txt
   ```

3. For the printable reports and the letters: XeLaTeX with the TeX Gyre fonts (packages `fontspec`, `babel`, `pdfpages`, `longtable`, `booktabs`).
4. For the actual train times: a PKP PLK API key. Put the key in the file `.env` in the root folder of the repository:

   ```
   PLK_API_KEY=<your key>
   ```

   Do not add `.env` to git. The file `.gitignore` excludes it. Do not write the key in other files or in the output of commands.

## Full update

Do these steps in sequence to update all results and reports. The sections below give the details of each step.

1. Collect the data: `python collect.py`.
2. Make sure that the monitor operates (see "Monitor the actual train times").
3. Update the GTFS feed of ZKM Iława: `python zkm_gtfs.py --refresh`.
4. Analyze the transfers: `python analyze.py`, then `python optimize.py --part key --from-date <first day of the period>`.
5. Make the transfer report: `python report.py`.
6. Propose the timetable corrections: `python zkmplan.py`.
7. Make the printable reports: `python print_reports.py --pdf`.
8. If you publish the report page, do the steps in "Publish the report page".
9. If you use the letters, copy the list of changes and compile the letters (see "Letters").

## Procedures

### Collect the data

1. Run the collection script:

   ```
   python collect.py
   ```

2. Make sure that the script shows no errors for the sources that you need.

Options: `--days-back N` (days of delay history to collect), `--skip-plk`, `--skip-spoznienia`, `--skip-gtfs`.

### Monitor the actual train times

1. Run the monitor:

   ```
   python monitor.py
   ```

2. Keep the monitor in operation. It makes one request each 5 min (each 20 min from 01:00 to 04:00).
3. To run the monitor from cron or Task Scheduler, use `python monitor.py --once` each 5 min.

The API keeps the executed trains for approximately 3 days. A stop of the monitor that is shorter than 3 days does not cause a loss of data.

The monitor keeps its data in `data/monitor.sqlite`. It writes one CSV file for each day in `data/monitor/`. The analysis and zkmplan read these CSV files.

### Analyze the transfers

1. Run the analysis:

   ```
   python analyze.py
   ```

2. Run the analysis of the timetable key (layovers, regularity, fit to groups of trains):

   ```
   python optimize.py --part key --from-date <YYYY-MM-DD> --days <N>
   ```

   Use the same period as the analysis.
3. Make the report:

   ```
   python report.py
   ```

4. Read the report in `reports/przesiadki-ilawa.html`. Read the numeric results in `results/summary.json` and in the CSV files in `results/`.

The analysis uses the past days up to yesterday, but not the days before the start of the bus timetable. It does these steps:

1. It calculates the transfer metrics for all days of the period with the planned train times.
2. It finds the test days. For each test day, it counts the good connections for each test window with the planned and with the actual train times.
3. It tests the bus timetable against the null model on the test day of each day type, with the actual train times. If a day type has no test day, it uses a planned day.
4. It calculates the dynamic analysis: the effect of the actual train delays on each connection.

Options of `analyze.py`:

| Option | Default | Meaning |
|---|---|---|
| `--days` | 30 | Number of past days in the period |
| `--to-date` | yesterday | Last day of the period |
| `--from-date` | from `--days` | First day of the period |
| `--good-window` | 5 | Good window (min) |
| `--test-windows` | 5,10 | Good windows that the analysis counts on the test days (min) |
| `--null` | 5000 | Number of random timetables for each test day |
| `--m-t2b`, `--m-b2t` | 4, 5 | Walk times (min) |

### Make the GTFS feed of ZKM Iława

1. Run the converter:

   ```
   python zkm_gtfs.py
   ```

   To download the timetable again, use `--refresh`.
2. Read the result in `data/zkm_gtfs.zip` (and the same files in `data/zkm_gtfs/`). The file `data/zkm_gtfs/_report.json` gives the number of trips for each day type and line.
3. Make sure that the report shows 0 unexplained departures. If there are unexplained departures, the timetable on rozklad.com has a pattern that the converter does not know. Examine the departures in the report before you use the feed.

The feed has these services:

| Service | Days |
|---|---|
| `RO` | Monday to Friday, not public holidays |
| `RO_S` | Monday to Friday on school days only |
| `SO` | Saturday |
| `ND` | Sunday and public holidays |

The feed is not official. Compare it with the timetable of the operator before you use it.

### Propose timetable corrections (zkmplan)

1. Examine the parameters in `zkmplan.ini` (see the table below). The cost values are estimates. Replace them with the values of the operator when you have them.
2. Run the tool:

   ```
   python zkmplan.py
   ```

   The tool downloads the bus timetable and the train timetable, then calculates. To calculate with the files that you downloaded before, use `--no-fetch`.
3. Read the report in `reports/korekty-zkm.html`.
4. Read the results in `data/plan/<date of the run>/`:

   | File | Content |
   |---|---|
   | `result.json` | All results of the run |
   | `zmiany.csv` | Changed trips, for each day type, block variant and stage |
   | `zmiany.tex` | List of the changes for the letters (LaTeX) |
   | `gtfs_propozycja.zip` | GTFS feed with the proposed timetable |

5. Before you use a proposal, examine it with the dispatcher of the operator. The tool calculates the vehicle blocks and the driver duties from the timetable. They are not the blocks and duties of the operator.

Run the tool again when the bus timetable or the train timetable changes.

Options of `zkmplan.py`:

| Option | Meaning |
|---|---|
| `--no-fetch` | Use the files that you downloaded before |
| `--from-date YYYY-MM-DD` | First planning date (default: tomorrow) |
| `--day-types` | Day types to calculate, for example `Roboczy,Sobota` |
| `--variants` | `single`, `interlined` or `both` (replaces the value in the configuration) |
| `--time-limit N` | Seconds for the solver (replaces the value in the configuration) |
| `--set SECTION.KEY=VALUE` | Change one value of the configuration for this run. You can use it more than one time, for example `--set transfers.good_window=10`. |
| `--out FOLDER` | Folder for the results (use it to compare scenarios) |
| `--no-report` | Do not write the web report |
| `--config FILE` | Use a different configuration file |

Main parameters in `zkmplan.ini`:

| Section | Parameter | Meaning |
|---|---|---|
| `general` | `horizon_days` | Number of days, from the first planning date, that the tool uses to test each proposal |
| `general` | `dates_per_day_type` | Number of dates of each day type that the optimiser uses together |
| `general` | `past_days` | Number of past days for the test with the actual train times (0 = no test) |
| `transfers` | `walk_train_to_bus`, `walk_bus_to_train` | Walk times (min) |
| `transfers` | `good_window` | Good window (min) |
| `transfers` | `delay_buffer` | `observed` (buffer from the delay data) or `none` |
| `transfers` | `delay_buffer_quantile` | Quantile of the delays for the buffer: 0.5 is the median, 0.75 means that 3 of 4 trains are not later than the buffer |
| `transfers` | `delay_buffer_cap` | Largest buffer (min) |
| `shifts` | `max_shift`, `school_max_shift` | Largest change of a trip (min), for all trips and for school trips |
| `shifts` | `shift_direction` | `any`, or `buffer`: a bus can only arrive earlier and leave later at the station |
| `shifts` | `trip_change_penalty`, `habit_penalty`, `dwell_penalty` | Penalties that stop changes with a small gain |
| `shifts` | `stages` | Largest number of changed trips in each stage |
| `blocks` | `variants`, `min_layover` | Block variants and the shortest layover |
| `duties` | `max_duty`, `break_*` | Duty length and break rules |
| `costs` | unit costs, `show_money` | Unit costs. With `show_money = no`, the reports show no values in PLN. |
| `demand` | exchange, shares, elasticities, prices | Data for the passenger estimate |

#### How the tool works

1. The tool finds the vehicle blocks: each bus takes the next trip from the same terminal. There are two variants: each bus stays on one line (`single`), or a bus can change the line at the station (`interlined`).
2. The tool cuts the blocks into driver duties at the station and finds the breaks.
3. The tool calculates the indicators of one day: kilometres, vehicle hours, peak number of buses and driver hours. With unit costs, it also calculates the cost.
4. The tool calculates the delay buffer of each train category from the collected delays.
5. An optimiser (mixed integer program) moves the trips by a small number of minutes. It counts a good connection for each train-line pair, with the delay buffer. It has these limits:
   - Each trip moves not more than `max_shift` minutes (school trips not more than `school_max_shift`).
   - With `shift_direction = buffer`, a bus can only arrive at the station earlier and leave the station later.
   - The layover between two trips of one bus is not shorter than `min_layover`.
   - Each duty keeps the break that the law requires.
   - The total vehicle time and the total driver time do not increase.
   - The trips of one line keep their sequence at the station.
   Each changed trip and each minute of change gives a penalty, so a trip moves only when this gives more good connections.
6. The optimiser works in stages. Each stage keeps the changes of the stage before it, so the operator can put the changes into use step by step.
7. The tool tests the current timetable, each stage and the full proposal on the past days with the actual train times. It gives the good and the caught connections for each day and the worst day of each day type.
8. The tool finds buses that wait at the station for a long time. It proposes an extra trip of the same line when the trip gives more good connections and the driver keeps the break.
9. The tool estimates the effect on passengers from the passenger exchange at the station, the share of bus users and the change of the waiting time. It gives a low, a middle and a high variant.

### Test timetables on past days

`zkmplan.py` does this test for each run. To compare the results of more runs (for example, scenarios with different parameters), run:

```
python past_test.py --days 30 --windows 5,10 data/plan/<run 1>/result.json data/plan/<run 2>/result.json
```

The results are in `results/test_na_minionych_dniach/`: `dni.csv` (each day), `podsumowanie.csv` (means), `meta.json` (days and worst days).

### Make the printable reports

1. Make the data of the reports (see "Analyze the transfers" and "Propose timetable corrections").
2. Run:

   ```
   python print_reports.py --pdf
   ```

   The script uses the newest run in `data/plan/`. The files are in `reports/druk/`. The printable reports show no values in PLN.

The web pages of the reports have a button that downloads the printable PDF. To use it, publish the PDF in the same folder as the page: `zalacznik-2-przesiadki.pdf` next to `przesiadki-ilawa.html`, and `zalacznik-3-korekty.pdf` next to `korekty-zkm.html`.

The public reports do not name the scripts of this repository. This file is the reference for the operation of the tools.

### Publish the report page

The report page is one folder for a static web site. It contains a summary page, the two web reports, the printable reports and the data files (list of changes, past-day test, current and proposed GTFS feed).

1. Make the data (see "Analyze the transfers" and "Propose timetable corrections").
2. Make the public printable reports. They have no references to the letters, and the report on the corrections contains the full list of changes. Give the web address of the page as the header text:

   ```
   python print_reports.py --public "<site>/<path>/<date>-raport-zkm" --pdf
   ```

3. Make the page folder in the source folder of the web site:

   ```
   python publish_site.py --out <site source folder>/<path> --url https://<site>/<path>
   ```

   The script makes the folder `<date>-raport-zkm` (the date of the newest zkmplan run). It replaces the download button of the web reports with a plain link to the PDF file. It does not overwrite files that exist in the folder (see below); `--overwrite` does.
4. Open `index.html` of the new folder and make sure that all links work.
5. Commit the folder in the repository of the web site and publish it.

The layout of the summary page is in `reports/site_template.html`.

### The published page 14-200.pl/@yasiu/2026-09-30-raport-zkm

The published page is ahead of these tools. Its files were made with a version of `zkmplan.py` that is not in this repository (10-minute window with weight 0.5, the „runaway” departures, the cases for ZKM), and then edited by hand in the repository of 14-200.pl:

- `index.html`: the map of one weekday is part of the story of the page, with the basic terms; the tile shows counts of good transfers (84 → 130 by the timetable, 86 → 115 by the measured train times);
- `przesiadki.html`, `korekty.html`: fonts hosted with the page, the glossary and the method at the end of the report, the section „Pomiar faktycznych przyjazdów” with the measured train times, no estimate of passengers.

The templates in `reports/` do not have these edits, and `data/plan` has no run with the published numbers. Until they do, treat the published HTML files as the source:

- PDFs of the page, from the data embedded in its web reports:

  ```
  python print_reports.py --from-html <14-200.pl>/src/@yasiu/2026-09-30-raport-zkm --public "14-200.pl/@yasiu/2026-09-30-raport-zkm" --pdf
  ```

  The PDFs are compiled with `xelatex`, or with `tectonic` when there is no `xelatex`. The figures use TeX Gyre Heros (install the font so that matplotlib finds it).
- `publish_site.py` keeps the existing files of the page, so it does not undo the edits.

### Measured train arrivals (monitor_test.py)

`monitor_test.py` repeats the test on past days with the train times measured by the monitor (`data/monitor/*.csv`): the current timetable and the proposal (current timetable with all changes from `data/plan/2026-09-30/wykaz-zmian-opublikowany.csv`, the published list), at 5 and 10 minutes of waiting, with the „runaway” departures. Buses keep the planned times. It writes `results/pomiar/dni.csv` (a train without data counts as on time), `results/pomiar/dni-tylko-zmierzone.csv` (only measured trains) and `results/pomiar/walidacja-wrzesien.csv` (check against the published September test). `print_reports.py` uses these files for the section „Pomiar faktycznych przyjazdów”, and the page has `dni.csv` as `pomiar-dni.csv`.

```
python monitor_test.py
```

### Letters

The folder `pismo/` has two letters in LaTeX: a request for the timetable changes (with attachments 1 to 3) and a request for public information. The two letters use the common file `pismo/wspolne.tex`.

1. Copy the list of changes of the newest run:

   ```
   cp data/plan/<date of the run>/zmiany.tex pismo/zalacznik-1-zmiany.tex
   ```

2. Write the data of the sender at the top of `pismo/wspolne.tex`.
3. Make sure that the numbers in the text of `pismo/wspolne.tex` agree with the newest results.
4. In the folder `pismo/`, compile each letter two times with `xelatex`. Each PDF of the change request contains the letter, the list of changes (attachment 1) and the two printable reports (attachments 2 and 3).

## Troubleshooting

| Problem | Cause | Remedy |
|---|---|---|
| `PLK_API_KEY` not found | The file `.env` is missing | Make the file `.env` (see "Requirements") |
| The PKP PLK API gives an error (for example 403) | The network blocks the API, or the key is not correct | Run the tool on a computer with access to the API. Make sure that the key is correct. |
| The past-day test has no days | There are no delay data for the period | Operate the monitor, or collect the delay history with `collect.py` |
| The test days use planned times | No day of that type has actual times for almost all trains | Operate the monitor for one week or more |
| `lualatex` stops with a font error | The Latin Modern fonts are missing | Use `xelatex` |
| The solver stops at the time limit | The problem is large | Increase `time_limit` in `zkmplan.ini`, or use fewer `dates_per_day_type` |

## Files

| Path | Content |
|---|---|
| `collect.py` | Collection of timetables and delay history |
| `monitor.py` | Continuous collection of actual train times |
| `analyze.py` | Transfer analysis (static, test days, dynamic) |
| `optimize.py` | Analysis of the timetable key (`--part key`) |
| `report.py` | Web report of the transfer analysis |
| `zkm_gtfs.py` | Converter of the ZKM Iława timetable to GTFS |
| `zkmplan.py` | Tool for timetable corrections |
| `zkmplan.ini` | Parameters of the tool for timetable corrections |
| `past_test.py` | Test of timetables on past days with the actual train times |
| `monitor_test.py` | Test of the current and proposed timetable with the train times measured by the monitor |
| `print_reports.py` | Printable reports (LaTeX, PDF), also the public versions |
| `publish_site.py` | Public report page for a static web site |
| `pismo/` | Letters (LaTeX) |
| `reports/template.html` | Template of the transfer report |
| `reports/zkmplan_template.html` | Template of the report of the timetable corrections |
| `reports/site_template.html` | Layout of the summary page of the public report |
| `reports/druk/` | Printable reports and their figures |
| `results/pomiar/` | Results of `monitor_test.py` |
| `ilawa/model.py` | Transfer metrics and null model |
| `ilawa/zkm.py` | Bus events at the stop Dworcowa |
| `ilawa/zkm_scrape.py` | Reader of the rozklad.com pages |
| `ilawa/gtfs.py` | Extract of one station from a GTFS feed |
| `ilawa/gtfs_write.py` | Writer of the GTFS feed |
| `ilawa/plk.py` | Parser for PKP PLK API responses |
| `ilawa/plan/` | Blocks, duties, costs, optimiser, extra trips, passenger effect, past-day test and report of the tool |
| `data/monitor/` | Actual train times, one CSV file for each day |
| `data/raw/` | Raw responses from the sources |
| `data/zkm_gtfs.zip` | GTFS feed of ZKM Iława |
| `data/plan/` | Results of the tool for timetable corrections, one folder for each run |
| `results/` | Results of the transfer analysis |

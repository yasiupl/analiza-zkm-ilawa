# ilawa-transport

This project measures how well the city buses (ZKM Iława) connect with the trains at the railway station Iława Główna.

The project gives two analyses and two tools:

- **Static analysis.** It compares the planned bus timetable with the planned train timetable.
- **Dynamic analysis.** It compares the bus timetable with the actual times of the trains.
- **GTFS feed of ZKM Iława.** It converts the public bus timetable into a GTFS feed.
- **Timetable corrections.** It proposes small changes to the bus timetable that give more good connections with the trains. The changes keep the vehicle blocks, the driver breaks and the cost.

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

There is no public real-time data for the buses. The analysis uses the bus timetable and the ZKM tolerance (1 min early to 3 min late).

## Definitions

- `m` is the walk time between the platform and the bus stop. The default is 4 min (train to bus) and 5 min (bus to train).
- **Wait (train to bus).** The time from the train arrival to the first bus that departs at or after the arrival plus `m`.
- **Lead (bus to train).** The time from the last bus arrival, at or before the train departure minus `m`, to the train departure.
- **Good connection.** The wait (or lead) is not more than `m` + 10 min.
- **Near miss (train to bus).** A bus departs in the interval from 2 min before the train arrival to `m` after it. The passengers see the bus go.
- **Near miss (bus to train).** A bus arrives after the train departure minus `m` and not later than 2 min after it. The passengers cannot get the train.
- **Null model.** The analysis moves each bus line by a random time from -30 to +30 min, 5000 times. This keeps the bus intervals but removes all relation to the trains. If the actual timetable is not better than the null model, the timetables are not coordinated. If the actual near-miss rate is higher than the null model, the buses tend to depart when the trains arrive.
- **Capacity weight.** Each train has an assumed number of seats by category (for example EIP 402, IC 400, R 200). The weighted results show the effect on passengers, not on trains.

## Procedure

### Prepare

1. Install Python 3.10 or later.
2. Put the PKP PLK API key in the file `.env`:

   ```
   PLK_API_KEY=sk_live_...
   ```

   Do not add `.env` to git. The file `.gitignore` excludes it.

### Collect the data

1. Run the collection script:

   ```
   python collect.py
   ```

2. Make sure that the script shows no errors for the sources that you need.

### Monitor the actual train times

1. Run the monitor:

   ```
   python monitor.py
   ```

2. Keep the monitor in operation for 2 weeks or more. It makes one request each 5 min (each 20 min from 01:00 to 04:00).
3. To run the monitor from cron or Task Scheduler, use `python monitor.py --once` each 5 min.

The monitor keeps its data in `data/monitor.sqlite`. It writes one CSV file for each day in `data/monitor/`.

### Analyze

1. Install the analysis packages:

   ```
   pip install numpy pandas
   ```

2. Run the analysis:

   ```
   python analyze.py
   ```

3. Read the results in `results/summary.json` and in the CSV files in `results/`.

### Make the GTFS feed of ZKM Iława

1. Run the converter:

   ```
   python zkm_gtfs.py
   ```

2. Read the result in `data/zkm_gtfs.zip` (and the same files in `data/zkm_gtfs/`). The file `data/zkm_gtfs/_report.json` gives the number of trips for each day type and line.
3. Make sure that the report shows 0 unexplained departures. If there are unexplained departures, the timetable on rozklad.com has a pattern that the converter does not know. Examine the departures in the report before you use the feed.

The converter keeps the downloaded pages in `data/raw/zkm/cache/`. To download the timetable again, delete this folder.

The feed has these services:

| Service | Days |
|---|---|
| `RO` | Monday to Friday, not public holidays |
| `RO_S` | Monday to Friday on school days only |
| `SO` | Saturday |
| `ND` | Sunday and public holidays |

### Propose timetable corrections

1. Install the packages:

   ```
   pip install -r requirements.txt
   ```

2. Examine the parameters in `zkmplan.ini`. The cost values are estimates. Replace them with the values of the operator when you have them.
3. Run the tool:

   ```
   python zkmplan.py
   ```

   The tool downloads the bus timetable and the train timetable, then calculates. To calculate with the files that you downloaded before, use `python zkmplan.py --no-fetch`. To start the plan on a different date, use `--from-date YYYY-MM-DD`.
4. Read the report in `reports/korekty-zkm.html`.
5. Read the results in `data/plan/<date>/`:

   | File | Content |
   |---|---|
   | `result.json` | All results of the run |
   | `zmiany.csv` | Changed trips, for each day type, block variant and stage |
   | `gtfs_propozycja.zip` | GTFS feed with the proposed timetable |

6. Before you use a proposal, examine it with the dispatcher of the operator. The tool calculates the vehicle blocks and the driver duties from the timetable. They are not the blocks and duties of the operator.

Run the tool again when the bus timetable or the train timetable changes.

#### How the tool works

1. The tool finds the vehicle blocks: each bus takes the next trip from the same terminal. There are two variants: each bus stays on one line (`single`), or a bus can change the line at the station (`interlined`).
2. The tool cuts the blocks into driver duties at the station and finds the breaks.
3. The tool calculates the cost of one day: kilometres, vehicle hours, peak number of buses and driver hours, multiplied by the unit costs.
4. An optimiser (mixed integer program) moves the trips by a small number of minutes. It counts a good connection for each pair of a train and a bus line. It has these limits:
   - Each trip moves not more than `max_shift` minutes (school trips not more than `school_max_shift`).
   - The layover between two trips of one bus is not shorter than `min_layover`.
   - Each duty keeps the break that the law requires.
   - The total vehicle time and the total driver time do not increase.
   - The trips of one line keep their sequence at the station.
   Each changed trip and each minute of change gives a penalty, so a trip moves only when this gives more good connections.
5. The optimiser works in stages (`stages` in `zkmplan.ini`). Each stage keeps the changes of the stage before it, so the operator can put the changes into use step by step.
6. The tool finds buses that wait at the station for a long time. It proposes an extra trip of the same line when the trip gives more good connections and the driver keeps the break.
7. The tool estimates the effect on passengers from the passenger exchange at the station, the share of bus users and the change of the waiting time. The report gives a low, a middle and a high variant.

## Files

| Path | Content |
|---|---|
| `collect.py` | One-time collection of timetables and delay history |
| `monitor.py` | Continuous collection of actual train times |
| `analyze.py` | Static and dynamic analysis |
| `zkm_gtfs.py` | Converter of the ZKM Iława timetable to GTFS |
| `zkmplan.py` | Tool for timetable corrections |
| `zkmplan.ini` | Parameters of the tool for timetable corrections |
| `ilawa/zkm_scrape.py` | Reader of the rozklad.com pages |
| `ilawa/gtfs_write.py` | Writer of the GTFS feed |
| `ilawa/plan/` | Blocks, duties, costs, optimiser, extra trips, passenger effect and report of the tool |
| `reports/zkmplan_template.html` | Template of the report of the tool |
| `ilawa/model.py` | Transfer metrics and null model |
| `ilawa/zkm.py` | Bus events at the stop Dworcowa |
| `ilawa/gtfs.py` | Extract of one station from a GTFS feed |
| `ilawa/plk.py` | Parser for PKP PLK API responses |
| `data/zkm_dworcowa.json` | Bus timetable at the stop Dworcowa |
| `data/raw/` | Raw responses from the sources |
| `results/` | Analysis results |
| `data/zkm_gtfs.zip` | GTFS feed of ZKM Iława |
| `data/plan/` | Results of the tool for timetable corrections, one folder for each run |

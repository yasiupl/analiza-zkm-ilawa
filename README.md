# ilawa-transport

This project measures how well the city buses (ZKM Iława) connect with the trains at the railway station Iława Główna.

The project gives two analyses:

- **Static analysis.** It compares the planned bus timetable with the planned train timetable.
- **Dynamic analysis.** It compares the bus timetable with the actual times of the trains.

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

## Files

| Path | Content |
|---|---|
| `collect.py` | One-time collection of timetables and delay history |
| `monitor.py` | Continuous collection of actual train times |
| `analyze.py` | Static and dynamic analysis |
| `ilawa/model.py` | Transfer metrics and null model |
| `ilawa/zkm.py` | Bus events at the stop Dworcowa |
| `ilawa/gtfs.py` | Extract of one station from a GTFS feed |
| `ilawa/plk.py` | Parser for PKP PLK API responses |
| `data/zkm_dworcowa.json` | Bus timetable at the stop Dworcowa |
| `data/raw/` | Raw responses from the sources |
| `results/` | Analysis results |

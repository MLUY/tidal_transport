# Tidal Transport Planner (VIKTOR app) - draft

Insight in the transport planning of monopile barges from the fabricator to the marshalling port, given the
tidal restrictions for the load-in at the marshalling port. Specification: `instruct.txt`.

## Input tabs
- **Input**: barge fleet (name, Single / Double, up to 6 barges) and the generic barge cycle for all barges:
  towing distance (350 nm) and average speed (4 kn) -> sailing time calculated (87.5 h each way); load-out per
  MP 24 h, port departure fabricator 3 h, port entry 3 h, **load-in duration per MP 3 h (sets the tidal window)**, general load-in time allowance
  21 h, port departure 6 h. After changing barges or cargo classes press **Update tide matrix and barge start
  tables**.
- **Cargo-cycle**: cargo weight classes (A-D) and the tide levels for load-in (cm): rows = classes, columns
  sb1, sb2, ... = all barges in the Single configuration, then db1, db2, ... = the Double barges in the Double
  configuration (level for the heaviest monopile on board; `x` = not possible). The line *Columns* shows which
  barge each column is. Only columns of defined barges are shown (VIKTOR cannot hide table columns, so there is
  one table per fleet composition and only the matching one is visible).
- **Fabricator**: **requirement: production may never be delayed** - the storage may never be full when a
  monopile is ready (checked in the results: *storage requirement met / NOT MET*); name and position of the fabrication yard (default Busan, 35.10°N 129.04°E), storage capacity (6), number of load-out berths (1), monopiles ready on the earliest or
  latest date, monopile schedule (dates dd/mm/yyyy).
- **Marshalling port**: name and position (default Gunsan, 35.98°N 126.62°E), number of load-in berths (1), tidal data (csv; default Gunsan 2026, repeated yearly).
  Between the data points the tide follows a smooth curve (cubic, through every data point), used for both
  the calculation and the charts.
- **Weather**: section *Weather window for sailing* (collapsed by default): workability (%) per month for the sailing legs (default Jan 60, Feb 65, Mar 75, Apr 80, May 85,
  Jun-Aug 90, Sep 85, Oct 75, Nov 65, Dec 50). Weather downtime per leg = sailing time x (100 / workability - 1),
  month of the start of the leg; shown as *Weather downtime* before the sailing.
- **Planning**: start date per barge (barges start at the fabricator yard); button **Optimise start dates**:
  searches every barge's start date within +/- 60 days of the entered dates, first for no production delay
  (storage requirement), then for the earliest last load-in, then for the least waiting (one barge at a time,
  4-day steps then day by day, repeated until nothing improves) and fills
  in the best dates (result in *Last optimisation*); *Double barge max wait* (max. wait at the fabricator for the 2nd monopile).

- **Export** (last tab): downloads of the current planning:
  - *Complete planning (Excel)*: Summary (results and inputs), Barge cycles, Monopiles, Activities, Load-ins (tide
    at start / lowest / highest during each load-in), Stock, Tide (hourly);
  - *Barge cycles (Excel)*: one row per trip with the hours per activity, plus averages per barge;
  - *All activities (CSV)*: every activity of every barge (semicolon separated);
  - *Charts (interactive HTML)*: planning, barge cycles, tide, stock and towing map in one browser file.

## Rules (draft)
1. Monopiles are shipped in number order; each goes on the barge that can start its load-out first.
2. A monopile is ready on its earliest/latest date, or later when the yard is full (monopile k only after
   monopile k - capacity has been loaded out = production delay).
3. A Double barge takes the next monopile too when the Double column permits it and it is ready within the
   max. wait.
4. Berths: a fabricator berth is occupied from the start of the load-out until the port departure (barges wait
   when all are taken). A marshalling port berth is only occupied during a load-in: a barge waiting for the tide
   does not block it. If the tide is right but all berths are used by other barges loading-in, the barge waits
   for the next suitable tide with a free berth (*Waiting for berth (other barge loading-in)*).
5. Load-in starts on a rising tide and the tide stays at or above the level of the load configuration for the
   whole load-in (Double level with two monopiles on board). With two monopiles the second load-in waits for the next tide, at the Single level of
   the remaining monopile.
   The search looks up to a year ahead, so with a long load-in duration the barge waits (possibly months) until
   a suitable window. A configuration whose level is never held long enough in the tidal data is not used (a
   Double trip then sails single); if no barge can do a monopile, a message gives the longest window available.
6. After the last load-in: general load-in time allowance, port departure, sailing back.
7. A barge back at the fabricator with no monopile ready is shown as *Waiting for fabricator* (no gaps in
   the Gantt chart).

## Results
- **Planning**: Gantt per barge with all activities and waits, or *Barge cycles*: one bar per trip split into its
  activities, all starting at 0, with the total cycle duration (and dashed lines for a cycle without waiting), or (tab Planning, *Planning chart* = Detail of one
  trip) one barge trip: tidal curve with the required level per barge configuration, green where the tide stays
  at or above that level for at least the load-in duration (load-in possible), grey where it is too short, the
  load-ins and tidal waits, and the trip activities on an hourly axis; opens zoomed in on the
  load-in(s).
- **Tide**: tidal graph of the marshalling port (like the Tide Planner cycle view) with every load-in, pink
  vertical areas where barges wait for the tide and below the Gantt bars of all barge activities. Hover shows the
  barge configuration (single / double, monopiles, classes). Zoom: buttons (1 week ... all) + range slider to
  move to a date, drag in the graph to zoom (also the y-axis), double-click to reset.
- **Weather** (view, was Towing): by default the grey map of Korea with the fabrication yard, the marshalling port,
  an indicative towing route and the **wave data locations** (red diamonds); full width, legend below,
  zoom with a box, the + / - buttons or the scroll wheel. Tab *Weather* (input) -> *Show*:
  *Weather windows per location* or *combined*: availability of a weather window (Hs below the limit for the whole
  window) for window lengths in steps of 6 h per Hs limit, and per month for one Hs limit. Combined = all
  locations at the same moments, highest Hs counts.
  Wave data: csv files `<lon>E_<lat>N.csv` (or `<lon>E,<lat>N.csv`) with Year, Month, Day, Hour, Hs (2nd row units)
  in the repository root or `sample_data/waves`. Map
  (display only; the planning uses the towing distance input). Map data: Natural Earth 1:10m (public domain),
  taken from the world-atlas package (ISC licence), embedded in `app/transport/korea_map.json` - no map service
  needed.
- **Stock**: monopiles at the fabricator (with storage capacity and production delays), on the barges and
  delivered at the marshalling port over time.
- **Table**: per monopile: class, ready / produced, barge / trip, single / double, load-out, arrival, load-in,
  required tide, tidal wait.

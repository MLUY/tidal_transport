# Tidal Transport Planner (VIKTOR app) - draft

Insight in the transport planning of monopile barges from the fabricator to the marshalling port, given the
tidal restrictions for the load-in at the marshalling port. Specification: `instruct.txt`.

## Input tabs
- **Input**: barge fleet (name, Single / Double, up to 6 barges) and the generic barge cycle for all barges:
  towing distance (350 nm) and average speed (4 kn) -> sailing time calculated (87.5 h each way); load-out per
  MP 24 h, port departure fabricator 3 h, port entry 3 h, load-in per MP 3 h, general load-in time allowance
  21 h, port departure 6 h. After changing barges or cargo classes press **Update tide matrix and barge start
  tables**.
- **Cargo-cycle**: cargo weight classes (A-D) and the tide levels for load-in (cm): rows = classes, columns
  sb1, sb2, ... = all barges in the Single configuration, then db1, db2, ... = the Double barges in the Double
  configuration (level for the heaviest monopile on board; `x` = not possible). The line *Columns* shows which
  barge each column is. Only columns of defined barges are shown (VIKTOR cannot hide table columns, so there is
  one table per fleet composition and only the matching one is visible).
- **Fabricator**: storage capacity (6), number of load-out berths (1), monopiles ready on the earliest or
  latest date, monopile schedule (dates dd/mm/yyyy).
- **Marshalling port**: number of load-in berths (1), tidal data (csv; default Gunsan 2026, repeated yearly).
- **Planning**: start date per barge (barges start at the fabricator yard); *Double barge max wait* (max. wait at the fabricator for the 2nd monopile).

## Rules (draft)
1. Monopiles are shipped in number order; each goes on the barge that can start its load-out first.
2. A monopile is ready on its earliest/latest date, or later when the yard is full (monopile k only after
   monopile k - capacity has been loaded out = production delay).
3. A Double barge takes the next monopile too when the Double column permits it and it is ready within the
   max. wait.
4. Berths: a fabricator berth is occupied from the start of the load-out until the port departure, a
   marshalling port berth from the port entry until the port departure (so also while waiting for the tide).
   When all berths are taken the barge waits.
5. Load-in starts on a rising tide at or above the level of the load configuration (Double level with two
   monopiles on board). With two monopiles the second load-in waits for the next tide, at the Single level of
   the remaining monopile.
6. After the last load-in: general load-in time allowance, port departure, sailing back.
7. A barge back at the fabricator with no monopile ready is shown as *Waiting for fabricator* (no gaps in
   the Gantt chart).

## Results
- **Planning**: Gantt per barge with all activities and waits, or (tab Planning, *Planning chart* = Detail of one
  trip) one barge trip: tidal curve with the required levels, where the tide is rising and high enough (load-in
  may start), the load-ins and tidal waits, and the trip activities on an hourly axis; opens zoomed in on the
  load-in(s).
- **Tide**: tidal graph of the marshalling port (like the Tide Planner cycle view) with every load-in, pink
  vertical areas where barges wait for the tide and below the Gantt bars of all barge activities. Hover shows the
  barge configuration (single / double, monopiles, classes). Zoom: buttons (1 week ... all) + range slider to
  move to a date, drag in the graph to zoom (also the y-axis), double-click to reset.
- **Stock**: monopiles at the fabricator (with storage capacity and production delays), on the barges and
  delivered at the marshalling port over time.
- **Table**: per monopile: class, ready / produced, barge / trip, single / double, load-out, arrival, load-in,
  required tide, tidal wait.

# Tidal Transport Planner (VIKTOR app) - first draft

Transport schedule for barges carrying monopiles from the fabrication yard to the marshalling port, taking
into account cargo weight classes, barge configurations (Single / Double) and the tide at the marshalling
port (load-in). Structure, tide file format and tide handling follow the Tide Planner app.

## Input (tab *Input*)
- **Barge fleet**: name + configuration. Single = exactly 1 monopile; Double = 1 or 2 monopiles.
  After changing the fleet press **Update barge cycle and tide restriction tables**.
- **Cargo weight classes** (default A-D, 1000-1800 t; from <= weight < to; up to 6 classes).
- **Barge cycle** per barge: loading per monopile (24 h), sailing to port (96 h), discharging (2 h),
  sailing back (96 h).
- **Tide restrictions matrix**: minimum tide level (cm) for discharging, per barge and per class.
  Single row for every barge, Double row for Double barges (class of the heaviest monopile counts).
  `x` = not permitted. Columns 1-6 = 1st-6th cargo class.
- **Delivery schedule**: load-out no., location, weight, earliest and latest load-out date (37 monopiles).
- **Planning**: date the barges are available at the yard; max. wait of a Double barge for the 2nd monopile.
- **Tide**: csv (date-time, tide in cm above CD); default Gunsan 2026 hourly, repeated for other years.

## Scheduling rules (first draft)
1. Monopiles are shipped in load-out number order.
2. Each monopile goes on the barge that can start loading it first (barges with `x` for its class skipped).
3. A Double barge also takes the next monopile when the Double row permits the combination, that monopile
   is available within the max. wait, and the tide reaches the Double level within a month after arrival.
4. Discharging only when the tide is at or above the required level for the whole discharging time;
   otherwise the barge waits at the marshalling port.
5. A load-out completed after the 'latest' date (end of day) is marked LATE.

## Results
- **Planning**: Gantt per barge (loading, waiting for monopile, sailing, tidal wait, discharging, sailing back).
- **Load-in**: tide at the marshalling port with the required level and load-in moment of every trip;
  below the tidal waits and discharging per barge.
- **Table**: per monopile: class, barge/trip, single/double, load-out, arrival, required tide, tidal wait,
  load-in, on time.

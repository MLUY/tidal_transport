"""
Transport schedule for monopile barges: fabrication yard -> marshalling port (load-in on the tide).
Pure Python (no VIKTOR imports) so it can be tested on its own.

Rules (first draft):
* Monopiles are shipped in load-out number order.
* Each monopile goes on the barge that can start loading it first (barges that may not carry its
  cargo class - "x" in the tide restriction matrix - are skipped).
* A Double barge takes the next monopile as well when the combination is permitted (Double row of the
  matrix, class of the heaviest monopile), that monopile is available at the yard within the
  allowed waiting time after loading the first one, and the tide reaches the Double level within a month
  after arrival (otherwise the barge sails with one monopile).
* Barge cycle: loading (per monopile) -> sailing to the marshalling port -> waiting for the tide ->
  discharging -> sailing back to the yard.
* Discharging takes the discharging time per monopile (2 monopiles = twice as long) and needs the tide at or
  above the required level for the whole discharging time.
  Required level: Single row for one monopile, Double row for two (heaviest class on board).
* Load-out is late when loading is completed after the 'latest' date (end of that day).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .tide import DataEnd, GateRule, PlanningError, TideCurve, wait_for_gate

SINGLE, DOUBLE = "Single", "Double"

CAT_LOAD = "Loading"
CAT_WAIT_MP = "Waiting for monopile"
CAT_SAIL = "Sailing to port"
CAT_TIDE = "Tidal wait"
CAT_DISCHARGE = "Discharging (load-in)"
CAT_RETURN = "Sailing back"


@dataclass
class CargoClass:
    name: str
    from_t: float
    to_t: float


@dataclass
class Barge:
    name: str
    configuration: str                 # Single / Double
    loading_h: float                   # per monopile
    sailing_h: float                   # yard -> marshalling port
    discharging_h: float               # per monopile
    return_h: float                    # marshalling port -> yard
    single_cm: dict = field(default_factory=dict)   # class name -> required tide (cm), None = not permitted
    double_cm: dict = field(default_factory=dict)


@dataclass
class Monopile:
    number: int
    name: str
    weight_t: float
    earliest: date
    latest: date
    cargo_class: str = ""


@dataclass
class Trip:
    barge: str
    trip_no: int                       # trip number of this barge
    monopiles: list                    # Monopile objects (1 or 2)
    mode: str                          # Single / Double transport
    cargo_class: str                   # (heaviest) class on board
    required_cm: float
    load_start: datetime
    load_end: datetime
    arrival: datetime                  # at the marshalling port
    discharge_start: datetime
    discharge_end: datetime
    back_at_yard: datetime
    tidal_wait_h: float
    mp_wait_h: float                   # waiting at the yard for the second monopile
    late: list                         # numbers of monopiles loaded out after their latest date
    loaded_out: dict = field(default_factory=dict)   # monopile number -> load-out completed


@dataclass
class ScheduleResult:
    trips: list
    segments: list                     # Gantt bars: barge, category, start, finish, trip, detail
    barges: list
    monopiles: list
    tide: TideCurve
    start: datetime

    @property
    def finish(self) -> datetime:
        return max(t.discharge_end for t in self.trips)

    @property
    def total_tidal_wait_h(self) -> float:
        return sum(t.tidal_wait_h for t in self.trips)

    @property
    def late_count(self) -> int:
        return sum(len(t.late) for t in self.trips)


def classify(weight: float, classes: list[CargoClass]) -> str:
    """Class with from <= weight < to (the last class includes its upper limit)."""
    for k, c in enumerate(classes):
        if c.from_t <= weight < c.to_t or (k == len(classes) - 1 and weight == c.to_t):
            return c.name
    raise PlanningError(f"Weight {weight:g} t is not in any cargo class - extend the cargo weight classes.")


def validate(barges: list[Barge], classes: list[CargoClass], monopiles: list[Monopile]) -> None:
    if not barges:
        raise PlanningError("Define at least one barge.")
    if not classes:
        raise PlanningError("Define at least one cargo weight class.")
    if not monopiles:
        raise PlanningError("The delivery schedule is empty.")
    names = [b.name for b in barges]
    if len(set(names)) != len(names):
        raise PlanningError("Barge names must be unique.")
    for c in classes:
        if c.to_t <= c.from_t:
            raise PlanningError(f"Cargo class {c.name}: 'to' weight must be larger than 'from' weight.")
    for b in barges:
        if b.configuration not in (SINGLE, DOUBLE):
            raise PlanningError(f"Barge {b.name}: configuration must be Single or Double.")
        for v, label in ((b.loading_h, "loading"), (b.sailing_h, "sailing"), (b.discharging_h, "discharging"),
                         (b.return_h, "sailing back")):
            if v < 0:
                raise PlanningError(f"Barge {b.name}: {label} time cannot be negative.")
    for m in monopiles:
        if m.latest < m.earliest:
            raise PlanningError(f"Monopile {m.number} ({m.name}): latest date is before the earliest date.")


def _heaviest(a: str, b: str, classes: list[CargoClass]) -> str:
    order = {c.name: k for k, c in enumerate(classes)}
    return a if order[a] >= order[b] else b


def plan_transport(*, barges: list[Barge], classes: list[CargoClass], monopiles: list[Monopile],
                   tide: TideCurve, start: datetime, max_wait_second_h: float = 24.0) -> ScheduleResult:
    validate(barges, classes, monopiles)
    mps = sorted(monopiles, key=lambda m: m.number)
    for m in mps:
        m.cargo_class = classify(m.weight_t, classes)

    # tide data repeated for every year so the schedule never runs out of data
    last = max(m.latest for m in mps)
    if tide.can_repeat():
        tide = tide.repeated_to_cover(start - timedelta(days=2), datetime.combine(last, datetime.min.time())
                                      + timedelta(days=400))

    free = {b.name: start for b in barges}
    trip_count = {b.name: 0 for b in barges}
    trips, segments = [], []

    def seg(barge, cat, t0, t1, trip, detail):
        if t1 > t0:
            segments.append(dict(barge=barge, category=cat, start=t0, finish=t1, trip=trip, detail=detail))

    i = 0
    while i < len(mps):
        mp = mps[i]
        avail = datetime.combine(mp.earliest, datetime.min.time())
        options = [b for b in barges if b.single_cm.get(mp.cargo_class) is not None]
        if not options:
            raise PlanningError(f"No barge may carry monopile {mp.number} ({mp.name}, {mp.weight_t:g} t, class "
                                f"{mp.cargo_class}): all Single values for this class are 'x'.")

        def pair_for(b, load_start):
            if b.configuration != DOUBLE or i + 1 >= len(mps):
                return None
            nxt = mps[i + 1]
            cls = _heaviest(mp.cargo_class, nxt.cargo_class, classes)
            if b.double_cm.get(cls) is None:
                return None
            nxt_avail = datetime.combine(nxt.earliest, datetime.min.time())
            if nxt_avail > load_start + timedelta(hours=b.loading_h + max_wait_second_h):
                return None
            # only when the tide reaches the (higher) Double level within the search period
            arrive = (max(load_start + timedelta(hours=b.loading_h), nxt_avail)
                      + timedelta(hours=b.loading_h + b.sailing_h))
            try:
                wait_for_gate(tide, arrive, GateRule(b.double_cm[cls], b.double_cm[cls], 2 * b.discharging_h))
            except PlanningError:
                return None
            except DataEnd:
                pass
            return nxt

        # barge that can start loading first; on a tie a barge that can take two monopiles, then table order
        best = min(options, key=lambda b: (max(free[b.name], avail),
                                           pair_for(b, max(free[b.name], avail)) is None, barges.index(b)))
        load_start = max(free[best.name], avail)
        nxt = pair_for(best, load_start)
        trip_count[best.name] += 1
        tno = trip_count[best.name]
        label = f"{best.name} trip {tno}"

        load_end = load_start + timedelta(hours=best.loading_h)
        seg(best.name, CAT_LOAD, load_start, load_end, label, f"Loading {mp.name} ({mp.weight_t:g} t)")
        mp_wait = 0.0
        loaded = [mp]
        loaded_out = {mp.number: load_end}          # load-out completed per monopile
        if nxt is not None:
            nxt_avail = datetime.combine(nxt.earliest, datetime.min.time())
            t2 = max(load_end, nxt_avail)
            mp_wait = (t2 - load_end).total_seconds() / 3600
            seg(best.name, CAT_WAIT_MP, load_end, t2, label, f"Waiting for {nxt.name} at the yard")
            load_end = t2 + timedelta(hours=best.loading_h)
            seg(best.name, CAT_LOAD, t2, load_end, label, f"Loading {nxt.name} ({nxt.weight_t:g} t)")
            loaded.append(nxt)
            loaded_out[nxt.number] = load_end
            cls = _heaviest(mp.cargo_class, nxt.cargo_class, classes)
            mode, required = DOUBLE, best.double_cm[cls]
        else:
            cls = mp.cargo_class
            mode, required = SINGLE, best.single_cm[cls]

        arrival = load_end + timedelta(hours=best.sailing_h)
        seg(best.name, CAT_SAIL, load_end, arrival, label, f"Sailing to the marshalling port ({mode.lower()})")
        discharge_h = best.discharging_h * len(loaded)          # per monopile
        rule = GateRule(rising_cm=required, falling_cm=required, transit_h=discharge_h)
        try:
            wait = wait_for_gate(tide, arrival, rule)
        except DataEnd:
            raise PlanningError(f"{label}: the schedule runs past the end of the tidal data ({tide.end:%Y-%m-%d}). "
                                f"Upload tidal data covering the period, or at least one full year.")
        d0 = arrival + timedelta(hours=wait)
        d1 = d0 + timedelta(hours=discharge_h)
        seg(best.name, CAT_TIDE, arrival, d0, label, f"Waiting for tide >= {required:.0f} cm")
        seg(best.name, CAT_DISCHARGE, d0, d1, label,
            f"Load-in {' + '.join(m.name for m in loaded)} (tide >= {required:.0f} cm)")
        back = d1 + timedelta(hours=best.return_h)
        seg(best.name, CAT_RETURN, d1, back, label, "Sailing back to the yard")
        free[best.name] = back

        late = [m.number for m in loaded
                if loaded_out[m.number] > datetime.combine(m.latest, datetime.min.time()) + timedelta(days=1)]
        trips.append(Trip(barge=best.name, trip_no=tno, monopiles=loaded, mode=mode, cargo_class=cls,
                          required_cm=required, load_start=load_start, load_end=load_end, arrival=arrival,
                          discharge_start=d0, discharge_end=d1, back_at_yard=back, tidal_wait_h=wait,
                          mp_wait_h=mp_wait, late=late, loaded_out=loaded_out))
        i += len(loaded)

    return ScheduleResult(trips=trips, segments=segments, barges=barges, monopiles=mps, tide=tide, start=start)

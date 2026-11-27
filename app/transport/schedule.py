"""
Transport schedule for monopile barges: fabrication yard -> marshalling port (load-in on the tide).
Pure Python (no VIKTOR imports) so it can be tested on its own.

Generic barge cycle (the same for all barges):
  load-out at the fabricator (per monopile, at a load-out berth) -> port departure fabricator ->
  sailing to the marshalling port (distance / speed) -> port entry (needs a free berth) ->
  load-in per monopile (on the tide) -> general load-in time allowance -> port departure ->
  sailing back to the fabricator (distance / speed).

Rules (draft):
* Monopiles are shipped in load-out number order. A monopile is ready at the fabricator on its earliest or
  latest date (option), or later when the yard is full: with a storage capacity N, monopile k can only be
  produced once monopile k - N has been loaded out (production delay).
* Each monopile goes on the barge that can start loading it first (barge started, a load-out berth free,
  barges that may not carry its class - 'x' in the tide matrix - skipped).
* A Double barge also takes the next monopile when the Double column permits the combination (class of the
  heaviest monopile), it is ready within the allowed waiting time after loading the first, and the yard
  capacity allows it.
* Load-in starts on a RISING tide at or above the required level: Double column (heaviest class) with two
  monopiles on board, Single column of the barge with one. With two monopiles, the second load-in waits for the
  next tide (after the next low water), now at the Single level of the remaining monopile.
* Berths: a barge occupies a fabricator load-out berth from the start of loading until the start of the port
  departure, and a marshalling port berth from the start of the port entry until the start of the port
  departure. When all berths are taken the barge waits for one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np

from .tide import TIME_STEP_H, DataEnd, GateRule, PlanningError, TideCurve, wait_for_gate

SINGLE, DOUBLE = "Single", "Double"
READY_EARLIEST, READY_LATEST = "Earliest date", "Latest date"

CAT_BERTH_FAB = "Waiting for berth (fabricator)"
CAT_LOAD = "Load-out"
CAT_WAIT_MP = "Waiting for monopile"
CAT_DEP_FAB = "Port departure (fabricator)"
CAT_SAIL = "Sailing to marshalling port"
CAT_BERTH_MP = "Waiting for berth (marshalling port)"
CAT_ENTRY = "Port entry"
CAT_TIDE = "Tidal wait"
CAT_LOADIN = "Load-in"
CAT_ALLOW = "Load-in time allowance"
CAT_DEP = "Port departure"
CAT_RETURN = "Sailing back"
CATEGORIES = [CAT_BERTH_FAB, CAT_LOAD, CAT_WAIT_MP, CAT_DEP_FAB, CAT_SAIL, CAT_BERTH_MP, CAT_ENTRY, CAT_TIDE,
              CAT_LOADIN, CAT_ALLOW, CAT_DEP, CAT_RETURN]


@dataclass
class CargoClass:
    name: str
    from_t: float
    to_t: float


@dataclass
class Cycle:
    """Generic barge cycle (hours, distance in nm, speed in knots)."""
    distance_nm: float = 350.0
    speed_kn: float = 4.0
    loadout_h: float = 24.0            # per monopile
    dep_fab_h: float = 3.0
    entry_h: float = 3.0
    loadin_h: float = 3.0              # per monopile
    allowance_h: float = 21.0
    dep_h: float = 6.0

    @property
    def sailing_h(self) -> float:
        return self.distance_nm / self.speed_kn if self.speed_kn > 0 else 0.0


@dataclass
class Barge:
    name: str
    configuration: str                 # Single / Double
    start: datetime                    # available at the fabricator
    single_cm: dict = field(default_factory=dict)   # class -> required tide (cm), None = not permitted
    double_cm: dict = field(default_factory=dict)


@dataclass
class Monopile:
    number: int
    name: str
    weight_t: float
    earliest: date
    latest: date
    cargo_class: str = ""
    ready: datetime | None = None      # earliest / latest date (option)
    produced: datetime | None = None   # = ready, later when the yard was full
    loaded_out: datetime | None = None
    loadin_start: datetime | None = None
    loadin_cm: float | None = None     # required tide at its load-in
    loadin_wait_h: float = 0.0         # tidal wait before its load-in

    @property
    def production_delay_h(self) -> float:
        return max((self.produced - self.ready).total_seconds() / 3600, 0.0) if self.produced else 0.0


@dataclass
class Trip:
    barge: str
    trip_no: int
    monopiles: list
    mode: str
    load_start: datetime
    departure_fab: datetime
    arrival: datetime                  # at the marshalling port (before waiting for a berth)
    loadins: list                      # (monopile, required cm, tidal wait h, start, end)
    finish_port: datetime              # end of port departure (marshalling port)
    back_at_fab: datetime
    berth_wait_fab_h: float
    berth_wait_port_h: float
    mp_wait_h: float

    @property
    def tidal_wait_h(self) -> float:
        return sum(x[2] for x in self.loadins)


@dataclass
class ScheduleResult:
    trips: list
    segments: list
    barges: list
    monopiles: list
    tide: TideCurve
    cycle: Cycle
    yard_capacity: int | None = None

    @property
    def start(self) -> datetime:
        return min(t.load_start for t in self.trips)

    @property
    def finish(self) -> datetime:
        return max(x[4] for t in self.trips for x in t.loadins)

    @property
    def total_tidal_wait_h(self) -> float:
        return sum(t.tidal_wait_h for t in self.trips)

    @property
    def total_berth_wait_h(self) -> float:
        return sum(t.berth_wait_fab_h + t.berth_wait_port_h for t in self.trips)

    @property
    def production_delay_count(self) -> int:
        return sum(m.production_delay_h > 1e-9 for m in self.monopiles)

    def yard_stock(self) -> list:
        """(time, number of monopiles stored at the yard) at every change: +1 produced, -1 loaded out."""
        events = sorted([(m.produced, 1) for m in self.monopiles] + [(m.loaded_out, -1) for m in self.monopiles],
                        key=lambda e: (e[0], e[1]))
        out, n = [], 0
        for t, d in events:
            n += d
            out.append((t, n))
        return out


def classify(weight: float, classes: list[CargoClass]) -> str:
    """Class with from <= weight < to (the last class includes its upper limit)."""
    for k, c in enumerate(classes):
        if c.from_t <= weight < c.to_t or (k == len(classes) - 1 and weight == c.to_t):
            return c.name
    raise PlanningError(f"Weight {weight:g} t is not in any cargo class - extend the cargo weight classes.")


def _validate(barges, classes, monopiles, cycle):
    if not barges:
        raise PlanningError("Define at least one barge.")
    if not classes:
        raise PlanningError("Define at least one cargo weight class.")
    if not monopiles:
        raise PlanningError("The monopile schedule is empty.")
    if len({b.name for b in barges}) != len(barges):
        raise PlanningError("Barge names must be unique.")
    for c in classes:
        if c.to_t <= c.from_t:
            raise PlanningError(f"Cargo class {c.name}: 'to' weight must be larger than 'from' weight.")
    if cycle.speed_kn <= 0:
        raise PlanningError("The average towing speed must be more than 0 knots.")
    for name in ("distance_nm", "loadout_h", "dep_fab_h", "entry_h", "loadin_h", "allowance_h", "dep_h"):
        if getattr(cycle, name) < 0:
            raise PlanningError("Barge cycle durations and distance cannot be negative.")
    for b in barges:
        if b.configuration not in (SINGLE, DOUBLE):
            raise PlanningError(f"Barge {b.name}: configuration must be Single or Double.")
    for m in monopiles:
        if m.latest < m.earliest:
            raise PlanningError(f"Monopile {m.number} ({m.name}): latest date is before the earliest date.")


def _heaviest(a: str, b: str, classes: list[CargoClass]) -> str:
    order = {c.name: k for k, c in enumerate(classes)}
    return a if order[a] >= order[b] else b


def _rising_rule(level_cm: float) -> GateRule:
    """Open only on a rising tide at or above the level (falling tide never)."""
    return GateRule(rising_cm=level_cm, falling_cm=float("inf"), transit_h=0.0)


def _next_low_water(tide: TideCurve, t: datetime) -> datetime:
    """First moment after t at which the tide is rising again after having fallen (i.e. after the next low
    water), so that the search continues on the next tide."""
    h0 = tide.to_hours(t)
    hrs = h0 + np.arange(0, 30, TIME_STEP_H)
    if hrs[-1] > tide.hours[-1]:
        raise DataEnd()
    rising = tide.rising(hrs)
    falling = np.flatnonzero(~rising)
    if not falling.size:
        return t
    after = np.flatnonzero(rising[falling[0]:])
    k = falling[0] + (after[0] if after.size else 0)
    return tide.t0 + timedelta(hours=float(hrs[k]))


def plan_transport(*, barges: list[Barge], classes: list[CargoClass], monopiles: list[Monopile], tide: TideCurve,
                   cycle: Cycle, yard_capacity: int | None = 6, fab_berths: int = 1, port_berths: int = 1,
                   ready: str = READY_EARLIEST, max_wait_second_h: float = 24.0) -> ScheduleResult:
    _validate(barges, classes, monopiles, cycle)
    if yard_capacity is not None and yard_capacity < 1:
        raise PlanningError("The fabrication yard storage capacity must be at least 1 monopile.")
    if fab_berths < 1 or port_berths < 1:
        raise PlanningError("The number of berths must be at least 1.")
    mps = sorted(monopiles, key=lambda m: m.number)
    for m in mps:
        m.cargo_class = classify(m.weight_t, classes)
        m.ready = datetime.combine(m.latest if ready == READY_LATEST else m.earliest, datetime.min.time())
        m.produced = m.loaded_out = m.loadin_start = m.loadin_cm = None
        m.loadin_wait_h = 0.0

    first = min(b.start for b in barges)
    if tide.can_repeat():
        tide = tide.repeated_to_cover(first - timedelta(days=2),
                                      datetime.combine(max(m.latest for m in mps), datetime.min.time())
                                      + timedelta(days=400))

    def produced(k: int) -> datetime:
        t = mps[k].ready
        if yard_capacity and k >= yard_capacity:
            t = max(t, mps[k - yard_capacity].loaded_out)
        return t

    free = {b.name: b.start for b in barges}
    trip_count = {b.name: 0 for b in barges}
    fab_free = [first - timedelta(days=1)] * fab_berths       # moment each berth becomes free
    port_free = [first - timedelta(days=1)] * port_berths
    trips, segments = [], []
    h = lambda x: timedelta(hours=x)  # noqa: E731

    def seg(barge, cat, t0, t1, trip, detail):
        if t1 > t0:
            segments.append(dict(barge=barge, category=cat, start=t0, finish=t1, trip=trip, detail=detail))

    def gate(t: datetime, level: float, label: str) -> float:
        try:
            return wait_for_gate(tide, t, _rising_rule(level))
        except DataEnd:
            raise PlanningError(f"{label}: the schedule runs past the end of the tidal data ({tide.end:%Y-%m-%d}). "
                                f"Upload tidal data covering the period, or at least one full year.")
        except PlanningError:
            raise PlanningError(f"{label}: the tide does not reach {level:.0f} cm on a rising tide within a month. "
                                f"Check the tide matrix and the tidal data.")

    i = 0
    while i < len(mps):
        mp = mps[i]
        avail = mp.produced = produced(i)
        options = [b for b in barges if b.single_cm.get(mp.cargo_class) is not None]
        if not options:
            raise PlanningError(f"No barge may carry monopile {mp.number} ({mp.name}, {mp.weight_t:g} t, class "
                                f"{mp.cargo_class}): all Single values for this class are 'x'.")
        berth_free = min(fab_free)

        def pair_for(b, load_start):
            if b.configuration != DOUBLE or i + 1 >= len(mps) or yard_capacity == 1:
                return None
            nxt = mps[i + 1]
            cls = _heaviest(mp.cargo_class, nxt.cargo_class, classes)
            if b.double_cm.get(cls) is None:
                return None
            if produced(i + 1) > load_start + h(cycle.loadout_h + max_wait_second_h):
                return None
            return nxt

        def start_for(b):
            return max(free[b.name], avail, berth_free)

        best = min(options, key=lambda b: (start_for(b), pair_for(b, start_for(b)) is None, barges.index(b)))
        ready_at = max(free[best.name], avail)
        load_start = start_for(best)
        nxt = pair_for(best, load_start)
        trip_count[best.name] += 1
        tno = trip_count[best.name]
        label = f"{best.name} trip {tno}"
        berth_wait_fab = (load_start - ready_at).total_seconds() / 3600
        seg(best.name, CAT_BERTH_FAB, ready_at, load_start, label, "All fabricator load-out berths taken")

        t = load_start + h(cycle.loadout_h)
        seg(best.name, CAT_LOAD, load_start, t, label, f"Load-out {mp.name} ({mp.weight_t:g} t, class {mp.cargo_class})")
        mp.loaded_out = t
        loaded, mp_wait = [mp], 0.0
        if nxt is not None:
            t2 = max(t, produced(i + 1))
            nxt.produced = produced(i + 1)
            mp_wait = (t2 - t).total_seconds() / 3600
            seg(best.name, CAT_WAIT_MP, t, t2, label, f"Waiting for {nxt.name} at the fabricator")
            t = t2 + h(cycle.loadout_h)
            seg(best.name, CAT_LOAD, t2, t, label, f"Load-out {nxt.name} ({nxt.weight_t:g} t, class {nxt.cargo_class})")
            nxt.loaded_out = t
            loaded.append(nxt)
        mode = DOUBLE if len(loaded) == 2 else SINGLE
        k_berth = fab_free.index(min(fab_free))
        fab_free[k_berth] = t                                    # berth free at the start of the port departure
        dep_fab = t + h(cycle.dep_fab_h)
        seg(best.name, CAT_DEP_FAB, t, dep_fab, label, "Port departure fabricator")
        arrival = dep_fab + h(cycle.sailing_h)
        seg(best.name, CAT_SAIL, dep_fab, arrival, label,
            f"Towing {cycle.distance_nm:g} nm at {cycle.speed_kn:g} kn ({mode.lower()})")

        # marshalling port: wait for a free berth outside, then port entry
        k_port = port_free.index(min(port_free))
        entry = max(arrival, port_free[k_port])
        berth_wait_port = (entry - arrival).total_seconds() / 3600
        seg(best.name, CAT_BERTH_MP, arrival, entry, label, "All marshalling port berths taken")
        t = entry + h(cycle.entry_h)
        seg(best.name, CAT_ENTRY, entry, t, label, "Port entry")

        # load-ins on a rising tide: first at the configuration level, the second on the next tide
        loadins = []
        for n, m in enumerate(loaded):
            if n == 0 and mode == DOUBLE:
                level = best.double_cm[_heaviest(loaded[0].cargo_class, loaded[1].cargo_class, classes)]
                what = "double"
            else:
                level = best.single_cm[m.cargo_class]
                what = "single"
            search = t if n == 0 else _next_low_water(tide, loadins[-1][3])
            wait_h = gate(search, level, label)
            start = search + h(wait_h)
            wait_total = (start - t).total_seconds() / 3600
            seg(best.name, CAT_TIDE, t, start, label, f"Waiting for a rising tide >= {level:.0f} cm ({what})")
            end = start + h(cycle.loadin_h)
            seg(best.name, CAT_LOADIN, start, end, label, f"Load-in {m.name} (rising tide >= {level:.0f} cm, {what})")
            m.loadin_start, m.loadin_cm, m.loadin_wait_h = start, level, wait_total
            loadins.append((m, level, wait_total, start, end))
            t = end
        allow_end = t + h(cycle.allowance_h)
        seg(best.name, CAT_ALLOW, t, allow_end, label, "General load-in time allowance")
        port_free[k_port] = allow_end                             # berth free at the start of the port departure
        dep_end = allow_end + h(cycle.dep_h)
        seg(best.name, CAT_DEP, allow_end, dep_end, label, "Port departure marshalling port")
        back = dep_end + h(cycle.sailing_h)
        seg(best.name, CAT_RETURN, dep_end, back, label, "Towing back to the fabricator")
        free[best.name] = back

        trips.append(Trip(barge=best.name, trip_no=tno, monopiles=loaded, mode=mode, load_start=load_start,
                          departure_fab=dep_fab, arrival=arrival, loadins=loadins, finish_port=dep_end,
                          back_at_fab=back, berth_wait_fab_h=berth_wait_fab, berth_wait_port_h=berth_wait_port,
                          mp_wait_h=mp_wait))
        i += len(loaded)

    return ScheduleResult(trips=trips, segments=segments, barges=barges, monopiles=mps, tide=tide, cycle=cycle,
                          yard_capacity=yard_capacity)

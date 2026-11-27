import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
from munch import Munch
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import viktor as vkt

from .schedule import (CAT_ALLOW, CAT_WAIT_FAB, CAT_BERTH_FAB, CAT_BERTH_MP, CAT_DEP, CAT_DEP_FAB, CAT_ENTRY, CAT_LOAD,
                       CAT_LOADIN, CAT_RETURN, CAT_SAIL, CAT_TIDE, CAT_WAIT_MP, DOUBLE, READY_EARLIEST, READY_LATEST,
                       SINGLE, CAT_WEATHER, DEFAULT_WORKABILITY, MONTHS, Barge, CargoClass, Cycle, Monopile,
                       plan_transport)
from .tide import PlanningError, load_tide

DEFAULT_TIDE = Path(__file__).resolve().parents[2] / "sample_data" / "gunsan_tide_2026_hourly.csv"
MAX_BARGES = 6
S_COLS = tuple(f"s{k}" for k in range(1, MAX_BARGES + 1))      # tide table: sb1 ... = 1st ... barge (Single)
D_COLS = tuple(f"d{k}" for k in range(1, MAX_BARGES + 1))      # db1 ... = 1st ... Double barge (Double)
NOT_ALLOWED = "x"
TABS = ("input", "cargo", "fabricator", "port", "weather", "planning")

COLORS = {CAT_WAIT_FAB: "#dbdb8d", CAT_BERTH_FAB: "#bcbd22", CAT_LOAD: "#8c564b", CAT_WAIT_MP: "#c7c7c7", CAT_DEP_FAB: "#9467bd",
          CAT_WEATHER: "#17becf", CAT_SAIL: "#1f77b4", CAT_BERTH_MP: "#ff7f0e", CAT_ENTRY: "#9467bd", CAT_TIDE: "#d62728",
          CAT_LOADIN: "#2ca02c", CAT_ALLOW: "#98df8a", CAT_DEP: "#9467bd", CAT_RETURN: "#aec7e8"}

DEFAULT_BARGES = [dict(name="b1", configuration=DOUBLE), dict(name="b2", configuration=DOUBLE),
                  dict(name="b3", configuration=SINGLE)]
DEFAULT_STARTS = [dict(name="b1", start="15/03/2028"), dict(name="b2", start="15/03/2028"),
                  dict(name="b3", start="15/05/2028")]
DEFAULT_CLASSES = [dict(name="A", from_t=1000, to_t=1200), dict(name="B", from_t=1200, to_t=1400),
                   dict(name="C", from_t=1400, to_t=1600), dict(name="D", from_t=1600, to_t=1800)]
# per class: single level (all barges), double level (Double barges)
_DEFAULT_LEVELS = {"A": ("520", "550"), "B": ("540", "600"), "C": ("560", NOT_ALLOWED), "D": ("580", NOT_ALLOWED)}


def _matrix_row(code, n_single, n_double):
    single, double = _DEFAULT_LEVELS.get(code, ("", ""))
    return dict(code=code, **{S_COLS[k]: single for k in range(n_single)},
                **{D_COLS[k]: double for k in range(n_double)})


def _table_name(n_single: int, n_double: int) -> str:
    """VIKTOR cannot hide table columns, so there is one tide level table per fleet composition
    (number of barges, number of Double barges); only the one matching the fleet is shown."""
    return f"m{n_single}_{n_double}"


def _composition(params) -> tuple:
    fleet = _fleet(params)
    return len(fleet), sum(c == DOUBLE for _, c in fleet)


def _default_table(n_single, n_double) -> list:
    return [_matrix_row(c["name"], n_single, n_double) for c in DEFAULT_CLASSES]


def _table_visible(n_single, n_double):
    def visible(params, **kwargs):
        return _composition(params) == (n_single, n_double)
    return visible

_MONOPILES = """1 OSS 1520 06/03/2028 21/03/2028
2 WTG-31 1725 07/03/2028 22/03/2028
3 WTG-34 1596 08/03/2028 23/03/2028
4 WTG-33 1611 16/03/2028 31/03/2028
5 WTG-07 1588 17/03/2028 01/04/2028
6 WTG-06 1527 26/03/2028 10/04/2028
7 WTG-05 1428 27/03/2028 11/04/2028
8 WTG-13 1506 04/04/2028 19/04/2028
9 WTG-25 1527 05/04/2028 20/04/2028
10 WTG-32 1580 13/04/2028 28/04/2028
11 WTG-30 1146 14/04/2028 29/04/2028
12 WTG-36 1251 22/04/2028 07/05/2028
13 WTG-24 1248 23/04/2028 08/05/2028
14 WTG-16 1357 01/05/2028 16/05/2028
15 WTG-23 1283 02/05/2028 17/05/2028
16 WTG-03 1258 10/05/2028 25/05/2028
17 WTG-02 1206 11/05/2028 26/05/2028
18 WTG-22 1292 19/05/2028 03/06/2028
19 WTG-10 1207 20/05/2028 04/06/2028
20 WTG-01 1368 02/06/2028 17/06/2028
21 WTG-35 1402 03/06/2028 18/06/2028
22 WTG-08 1438 11/06/2028 26/06/2028
23 WTG-15 1310 12/06/2028 27/06/2028
24 WTG-14 1604 20/06/2028 05/07/2028
25 WTG-12 1387 21/06/2028 06/07/2028
26 WTG-04 1375 04/07/2028 19/07/2028
27 WTG-11 1343 05/07/2028 20/07/2028
28 WTG-21 1374 13/07/2028 28/07/2028
29 WTG-20 1412 14/07/2028 29/07/2028
30 WTG-19 1446 01/08/2028 16/08/2028
31 WTG-09 1250 02/08/2028 17/08/2028
32 WTG-27 1358 10/08/2028 25/08/2028
33 WTG-26 1440 11/08/2028 26/08/2028
34 WTG-17 1478 19/08/2028 03/09/2028
35 WTG-18 1402 20/08/2028 04/09/2028
36 WTG-29 1325 27/08/2028 11/09/2028
37 WTG-28 1392 31/08/2028 15/09/2028"""
DEFAULT_MONOPILES = [dict(number=int(n), name=nm, weight_t=float(w), earliest=e, latest=la)
                     for n, nm, w, e, la in (line.split() for line in _MONOPILES.splitlines())]


def _flat(params):
    out = {}
    for key, value in params.items():
        if key in TABS and isinstance(value, dict):
            out.update(_flat(value))
        else:
            out[key] = value
    return Munch(out)


def _fleet(params) -> list:
    """[(name, configuration)] of the barge fleet (rows with a name)."""
    p = params.input if "input" in params else params
    return [(str(r.name or "").strip(), r.configuration or SINGLE) for r in (p.barges or []) if str(r.name or "").strip()]


def _matrix_columns(params, **kwargs) -> str:
    fleet = _fleet(params)
    if not fleet:
        return "-"
    if len(fleet) > MAX_BARGES:
        return f"At most {MAX_BARGES} barges."
    single = ", ".join(f"sb{k + 1} = {n}" for k, (n, _) in enumerate(fleet))
    doubles = [n for n, c in fleet if c == DOUBLE]
    double = ", ".join(f"db{k + 1} = {n}" for k, n in enumerate(doubles)) or "no Double barges"
    return f"Single: {single} | Double: {double}"


def _sailing_text(params, **kwargs) -> str:
    p = params.input if "input" in params else params
    speed = float(p.speed_kn or 0)
    if speed <= 0:
        return "-"
    hrs = float(p.distance_nm or 0) / speed
    return f"{hrs:.1f} h ({hrs / 24:.1f} days) each way"


CHART_ALL, CHART_DETAIL = "Complete project", "Detail of one trip"
UI_ONLY = {"chart_mode", "detail_barge", "detail_trip"}      # display choices: no new calculation


def _barge_options(params, **kwargs) -> list:
    return [n for n, _ in _fleet(params)] or ["-"]


def _trip_options(params, **kwargs) -> list:
    """Trips of the selected barge, e.g. '3: WTG-36 + WTG-24 (double)'; plain numbers when no planning."""
    barge = (params.planning or {}).get("detail_barge") or next(iter(_barge_options(params)), "")
    try:
        res = _schedule(params)
    except Exception:  # noqa: BLE001 - the views show the input error
        return ["1"]
    trips = [t for t in res.trips if t.barge == barge]
    return [f"{t.trip_no}: {' + '.join(m.name for m in t.monopiles)} ({t.mode.lower()})" for t in trips] or ["1"]


def _trip_number(value) -> int:
    try:
        return int(str(value or "1").split(":")[0])
    except ValueError:
        return 1


# ---------------------------------------------------------------- parametrization
class Parametrization(vkt.Parametrization):
    # ---------------- Input: purpose, barge fleet, generic barge cycle
    input = vkt.Tab("Input")
    input.title = vkt.Text("## Tidal Transport Planner")
    input.intro = vkt.Text("*Insight in the transport planning of monopile barges from the fabricator to the "
                           "marshalling port, given the tidal restrictions for the load-in at the marshalling port.*")
    input.barge_text = vkt.Text("### Barge fleet\n*Single: carries exactly 1 monopile. Double: carries 1 or 2 "
                                f"monopiles.*")
    input.barges = vkt.Table("Barge fleet", default=DEFAULT_BARGES)
    input.barges.name = vkt.TextField("Name")
    input.barges.configuration = vkt.OptionField("Configuration", options=[SINGLE, DOUBLE])
    input.update_tables = vkt.SetParamsButton(
        "Update tide matrix and barge start tables", method="update_tables", flex=100,
        description="After changing barges or cargo classes: adds/removes rows, existing values are kept.")

    input.cycle_text = vkt.Text("### Barge cycle (all barges)")
    input.distance_nm = vkt.NumberField("Towing distance", suffix="nm", default=350, min=0, flex=50)
    input.speed_kn = vkt.NumberField("Average towing speed", suffix="kn", default=4, min=0.1, flex=50)
    input.sailing = vkt.OutputField("Sailing to marshalling port / back (calculated)", value=_sailing_text, flex=100)
    input.loadout_h = vkt.NumberField("Load-out at fabricator per MP", suffix="h", default=24, min=0, flex=50)
    input.dep_fab_h = vkt.NumberField("Port departure fabricator", suffix="h", default=3, min=0, flex=50)
    input.entry_h = vkt.NumberField("Port entry", suffix="h", default=3, min=0, flex=50)
    input.loadin_text = vkt.Text(
        "🔴 **LOAD-IN DURATION PER MP - this sets the tidal window.** For every load-in the tide must stay at or above "
        "the required level for this whole duration. This value is used for all tidal window calculations and "
        "charts.")
    input.loadin_h = vkt.NumberField("🔴 LOAD-IN DURATION PER MP (tidal window)", suffix="h", default=3, min=0,
                                     flex=100)
    input.allowance_h = vkt.NumberField("General load-in time allowance", suffix="h", default=21, min=0, flex=50)
    input.dep_h = vkt.NumberField("Port departure", suffix="h", default=6, min=0, flex=50)
    input.loadin_note = vkt.Text(
        "*Load-in rules (tide at the marshalling port):  \n"
        "- A load-in may only **start on a rising tide**.  \n"
        "- The tide must be **at or above the required level from the start until the end of the load-in** "
        "(load-in duration per MP). It may pass high water during the load-in, but not drop below the level.  \n"
        "- The required level depends on the load configuration (tab Cargo-cycle). If the tide does not stay high "
        "enough for the whole load-in, the barge waits for the next suitable tide.  \n"
        "- With two monopiles on board, the first load-in uses the Double level; after it, the barge waits for the "
        "next tide for the second load-in, at the Single level of the remaining monopile.*")

    # ---------------- Cargo-cycle: weight classes and tide matrix
    cargo = vkt.Tab("Cargo-cycle")
    cargo.class_text = vkt.Text("### Cargo weight classes\n*From weight <= weight < to weight.*")
    cargo.classes = vkt.Table("Cargo weight classes", default=DEFAULT_CLASSES)
    cargo.classes.name = vkt.TextField("Code")
    cargo.classes.from_t = vkt.NumberField("From", suffix="t")
    cargo.classes.to_t = vkt.NumberField("To", suffix="t")
    cargo.matrix_text = vkt.Text(
        "### Tide levels for load-in (cm)\n*Rows: cargo classes. Columns: first all barges in the **Single** "
        "configuration (sb1, sb2, ...: one monopile on board), then the Double barges in the **Double** configuration "
        "(db1, db2, ...: two monopiles). The value is the minimum tide level during the whole load-in for the heaviest monopile on board; "
        "'x' = configuration not possible.*")
    cargo.columns = vkt.OutputField("Columns", value=_matrix_columns, flex=100)
    for _n in range(1, MAX_BARGES + 1):
        for _d in range(0, _n + 1):
            _table = vkt.Table("Tide levels for load-in (cm)", visible=_table_visible(_n, _d),
                               default=_default_table(_n, _d))
            _table.code = vkt.TextField("Code")
            for _k in range(_n):
                setattr(_table, S_COLS[_k], vkt.TextField(f"sb{_k + 1}"))
            for _k in range(_d):
                setattr(_table, D_COLS[_k], vkt.TextField(f"db{_k + 1}"))
            setattr(cargo, _table_name(_n, _d), _table)

    # ---------------- Fabricator: yard, berths, monopile schedule
    fabricator = vkt.Tab("Fabricator")
    fabricator.yard_text = vkt.Text("### Fabrication yard")
    fabricator.yard_capacity = vkt.IntegerField("Fabrication yard storage capacity", suffix="MP", default=6, min=1,
                                                flex=50)
    fabricator.fab_berths = vkt.IntegerField("Number of load-out berths available", default=1, min=1, flex=50)
    fabricator.ready = vkt.OptionField("Monopile produced and ready for load-out on", options=[READY_EARLIEST,
                                                                                              READY_LATEST],
                                       default=READY_EARLIEST, flex=100)
    fabricator.mp_text = vkt.Text("### Monopile schedule\n*Dates as dd/mm/yyyy.*")
    fabricator.monopiles = vkt.Table("Monopile schedule", default=DEFAULT_MONOPILES)
    fabricator.monopiles.number = vkt.IntegerField("No.")
    fabricator.monopiles.name = vkt.TextField("Location")
    fabricator.monopiles.weight_t = vkt.NumberField("Weight", suffix="t")
    # dates as text: VIKTOR does not allow date fields in a table
    fabricator.monopiles.earliest = vkt.TextField("Earliest (dd/mm/yyyy)")
    fabricator.monopiles.latest = vkt.TextField("Latest (dd/mm/yyyy)")

    # ---------------- Marshalling port: berths, tide
    port = vkt.Tab("Marshalling port")
    port.port_text = vkt.Text("### Marshalling port")
    port.port_berths = vkt.IntegerField("Number of load-in berths available", default=1, min=1)
    port.tide_text = vkt.Text("### Tide at the marshalling port")
    port.tide_file = vkt.FileField("Tidal data (optional, default Gunsan 2026)", file_types=[".csv"])
    port.tide_note = vkt.Text("*File format: .csv, comma separated, header row, then one row per time (e.g. "
                              "hourly): column 1 date-time as 2026-01-01 00:00 (or 01/01/2026 00:00, day first), "
                              "column 2 tide level in cm above chart datum (CD). The data of one year is repeated "
                              "for every other year (same calendar date and time).*")

    # ---------------- Weather: monthly workability for the sailing legs
    weather = vkt.Tab("Weather")
    weather.text = vkt.Text(
        "### Weather workability for sailing\n*Percentage of the time the weather allows towing, per month. It is "
        "applied to both sailing legs (fabricator -> marshalling port and back): weather downtime = sailing time x "
        "(100 / workability - 1), using the month in which the leg starts. Example: 87.5 h sailing in January (60 %) "
        "gives 58.3 h weather downtime. 100 % = no weather downtime. Default values are reasonable starting "
        "points; replace them by the metocean data of the route.*")
    weather.workability = vkt.Table("Workability per month", default=[dict(month=m, pct=v) for m, v in
                                                                      zip(MONTHS, DEFAULT_WORKABILITY)])
    weather.workability.month = vkt.TextField("Month")
    weather.workability.pct = vkt.NumberField("Workability", suffix="%")

    # ---------------- Planning: barge start dates
    planning = vkt.Tab("Planning")
    planning.start_text = vkt.Text("### Barge start\n*The barges are assumed to start at the fabricator yard on "
                                   "their start date. With one load-out berth, barges wait until the berth is "
                                   "free.*")
    planning.starts = vkt.Table("Barge start", default=DEFAULT_STARTS)
    planning.starts.name = vkt.TextField("Barge")
    planning.starts.start = vkt.TextField("Start (dd/mm/yyyy)")
    planning.max_wait_second = vkt.NumberField("Double barge max wait", suffix="h", default=24, min=0)
    planning.max_wait_note = vkt.Text(
        "*Max. wait at the fabricator for the 2nd monopile: after loading the first monopile, a Double barge waits "
        "at most this long for the next monopile to be ready. If it is ready later, the barge sails with one "
        "monopile. Only when the Double configuration is allowed for the combination (db column, not 'x').*")

    planning.chart_text = vkt.Text("### Planning chart")
    planning.chart_mode = vkt.OptionField("Show", options=[CHART_ALL, CHART_DETAIL], default=CHART_ALL,
                                          variant="radio-inline", flex=100)
    planning.detail_barge = vkt.OptionField("Barge", options=_barge_options, flex=50,
                                            visible=vkt.IsEqual(vkt.Lookup("planning.chart_mode"), CHART_DETAIL))
    planning.detail_trip = vkt.OptionField("Trip", options=_trip_options, flex=50,
                                           visible=vkt.IsEqual(vkt.Lookup("planning.chart_mode"), CHART_DETAIL))


# ---------------------------------------------------------------- inputs -> engine
def _tide_bytes(params) -> bytes:
    return params.tide_file.file.getvalue_binary() if params.tide_file else DEFAULT_TIDE.read_bytes()


def _to_date(v):
    """Date from text dd/mm/yyyy (also d-m-yyyy, d.m.yyyy or yyyy-mm-dd)."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    text = str(v or "").strip()
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            pass
    raise ValueError(text)


def _cm(text, where: str):
    t = str(text if text is not None else "").strip().lower()
    if t in ("", NOT_ALLOWED):
        return None
    try:
        return float(t)
    except ValueError:
        raise vkt.UserError(f"Tide levels for load-in, {where}: '{text}' is not a number or 'x'.")


def _inputs(p):
    classes = [CargoClass(str(r.name or "").strip() or f"Class {k + 1}", float(r.from_t or 0), float(r.to_t or 0))
               for k, r in enumerate(p.classes or [])]
    fleet = _fleet(p)
    if len(fleet) > MAX_BARGES:
        raise vkt.UserError(f"At most {MAX_BARGES} barges.")
    n_double = sum(c == DOUBLE for _, c in fleet)
    matrix = {str(r.code or "").strip(): r for r in (p.get(_table_name(len(fleet), n_double)) or [])}
    missing = [c.name for c in classes if c.name not in matrix]
    starts = {str(r.name or "").strip(): r.start for r in (p.starts or [])}
    if missing or any(n not in starts for n, _ in fleet):
        raise vkt.UserError("The tide level table or the barge start table does not match the barges / cargo classes "
                            "- press 'Update tide matrix and barge start tables' (tab Input).")
    barges = []
    double_no = 0
    for k, (name, conf) in enumerate(fleet):
        try:
            start = _to_date(starts[name])
        except ValueError:
            start = None
        if start is None:
            raise vkt.UserError(f"Barge start: '{starts[name]}' for {name} is not a valid date (dd/mm/yyyy).")
        single = {c.name: _cm(matrix[c.name].get(S_COLS[k]), f"sb{k + 1} ({name}) class {c.name}")
                  for c in classes}
        double = {}
        if conf == DOUBLE:
            double = {c.name: _cm(matrix[c.name].get(D_COLS[double_no]), f"db{double_no + 1} ({name}) class {c.name}")
                      for c in classes}
            double_no += 1
        barges.append(Barge(name=name, configuration=conf, start=datetime.combine(start, datetime.min.time()),
                            single_cm=single, double_cm=double))
    monopiles = []
    for r in p.monopiles or []:
        if not (r.name or r.weight_t):
            continue
        try:
            e, la = _to_date(r.earliest), _to_date(r.latest)
        except ValueError as exc:
            raise vkt.UserError(f"Monopile schedule: monopile {r.number} ({r.name}): date '{exc}' is not valid - "
                                f"use dd/mm/yyyy, e.g. 06/03/2028.")
        if e is None or la is None:
            raise vkt.UserError(f"Monopile schedule: monopile {r.number} ({r.name}) needs an earliest and latest date.")
        monopiles.append(Monopile(int(r.number or 0), str(r.name or ""), float(r.weight_t or 0), e, la))
    cycle = Cycle(distance_nm=float(p.distance_nm or 0), speed_kn=float(p.speed_kn or 0),
                  loadout_h=float(p.loadout_h or 0), dep_fab_h=float(p.dep_fab_h or 0), entry_h=float(p.entry_h or 0),
                  loadin_h=float(p.loadin_h or 0), allowance_h=float(p.allowance_h or 0), dep_h=float(p.dep_h or 0))
    return barges, classes, monopiles, cycle


def _workability(p) -> list:
    rows = p.workability or []
    if len(rows) != 12:
        raise vkt.UserError("Weather: the workability table needs 12 rows (January ... December).")
    out = []
    for m, r in zip(MONTHS, rows):
        v = r.pct
        if v is None or not 0 < float(v) <= 100:
            raise vkt.UserError(f"Weather: workability for {m} must be more than 0 and at most 100 %.")
        out.append(float(v))
    return out


_CACHE: dict = {}


def _schedule(params):
    p = _flat(params)
    data = _tide_bytes(p)
    key = hashlib.sha1((json.dumps({k: v for k, v in p.items() if k != "tide_file" and k not in UI_ONLY},
                                   default=str, sort_keys=True)
                        + hashlib.sha1(data).hexdigest()).encode()).hexdigest()
    if key not in _CACHE:
        if len(_CACHE) > 20:
            _CACHE.clear()
        barges, classes, monopiles, cycle = _inputs(p)
        try:
            _CACHE[key] = plan_transport(
                barges=barges, classes=classes, monopiles=monopiles, tide=load_tide(data), cycle=cycle,
                yard_capacity=int(p.yard_capacity) if p.yard_capacity else None,
                fab_berths=int(p.fab_berths or 1), port_berths=int(p.port_berths or 1),
                ready=p.ready or READY_EARLIEST, max_wait_second_h=float(p.max_wait_second or 0),
                workability=_workability(p))
        except PlanningError as exc:
            raise vkt.UserError(str(exc))
    return _CACHE[key]


# ---------------------------------------------------------------- figures
def _summary(res) -> str:
    n_double = sum(t.mode == DOUBLE for t in res.trips)
    txt = (f"{len(res.monopiles)} monopiles in {len(res.trips)} trips ({n_double} double) | "
           f"start {res.start:%d %b %Y} | last load-in {res.finish:%d %b %Y} | tidal wait {res.total_tidal_wait_h:.0f} h"
           f" | berth wait {res.total_berth_wait_h:.0f} h | weather {res.total_weather_h:.0f} h")
    if res.production_delay_count:
        txt += f" | <span style='color:#d62728'>{res.production_delay_count} production delay(s), yard full</span>"
    return txt


def _layout_title(fig, lines, height):
    fig.update_layout(title=dict(text="<br>".join(lines), x=0.01, xanchor="left", y=1 - 18 / height, yanchor="top",
                                 font=dict(size=15)), height=height, legend_title_text="")


def build_gantt(res) -> go.Figure:
    rows = [dict(Barge=s["barge"], Category=s["category"], Start=s["start"], Finish=s["finish"], Trip=s["trip"],
                 Hours=round((s["finish"] - s["start"]).total_seconds() / 3600, 1), Detail=s["detail"])
            for s in res.segments]
    fig = px.timeline(rows, x_start="Start", x_end="Finish", y="Barge", color="Category",
                      color_discrete_map=COLORS, hover_data=["Trip", "Hours", "Detail"],
                      category_orders={"Category": list(COLORS)})
    fig.update_yaxes(categoryorder="array", categoryarray=[b.name for b in res.barges][::-1], title_text="")
    fig.update_xaxes(rangeslider_visible=True)
    _layout_title(fig, ["<b>Transport schedule</b>", _summary(res)], 600)
    fig.update_layout(margin=dict(l=100, t=80))
    return fig


def _trip_text(t) -> str:
    """Barge configuration of a trip for the hover info."""
    mps = " + ".join(f"{m.name} ({m.weight_t:g} t, class {m.cargo_class})" for m in t.monopiles)
    return f"{t.barge} trip {t.trip_no} - {t.mode.lower()} configuration: {mps}"


def build_tide(res) -> dict:
    """Tidal graph (like the Tide Planner cycle view): tide at the marshalling port with every load-in, pink
    vertical areas where barges wait for the tide, and below the activities of all barges as Gantt bars. Buttons
    and a range slider zoom to a date / period; the y-axis can be zoomed as well (drag in the graph)."""
    tide = res.tide
    by_trip = {f"{t.barge} trip {t.trip_no}": t for t in res.trips}
    t0 = res.start - timedelta(days=1)
    t1 = max(t.back_at_fab for t in res.trips) + timedelta(days=1)
    hrs = np.arange(max(tide.to_hours(t0), 0.0), min(tide.to_hours(t1), float(tide.hours[-1])), 0.25)
    times = [tide.t0 + timedelta(hours=float(h)) for h in hrs]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.68, 0.32], vertical_spacing=0.03)
    fig.add_trace(go.Scatter(x=times, y=tide.level_cm(hrs), mode="lines", name="Tide level",
                             line=dict(color="#1f77b4", width=1.3),
                             hovertemplate="%{x|%d-%m-%Y %H:%M}  %{y:.0f} cm<extra></extra>"), row=1, col=1)
    barge_colors = px.colors.qualitative.Dark2
    for k, b in enumerate(res.barges):
        loadins = [(t, x) for t in res.trips if t.barge == b.name for x in t.loadins]
        if not loadins:
            continue
        fig.add_trace(go.Scatter(
            x=[x[3] for _, x in loadins], y=[x[1] for _, x in loadins], mode="markers",
            name=f"{b.name} load-in", marker=dict(symbol="triangle-up", size=11, color=barge_colors[k % 8]),
            hoverinfo="text",
            text=[f"{_trip_text(t)}<br>Load-in {m.name}: {s:%d-%m %H:%M} - {e:%H:%M} on a rising tide >= {lvl:.0f} cm"
                  f"<br>Tidal wait {w:.1f} h" for t, (m, lvl, w, s, e) in loadins]), row=1, col=1)
    # Gantt bars per barge (one trace per activity, so the legend can switch them on and off)
    for cat, color in COLORS.items():
        segs = [s for s in res.segments if s["category"] == cat]
        if not segs:
            continue
        fig.add_trace(go.Bar(
            base=[s["start"] for s in segs], x=[(s["finish"] - s["start"]).total_seconds() * 1000 for s in segs],
            y=[s["barge"] for s in segs], orientation="h", marker_color=color, name=cat,
            hoverinfo="text", textposition="none",
            text=[f"{_trip_text(by_trip[s['trip']])}<br>{cat}: {s['detail']}<br>{s['start']:%d-%m %H:%M} - "
                  f"{s['finish']:%d-%m %H:%M} ({(s['finish'] - s['start']).total_seconds() / 3600:.1f} h)"
                  for s in segs]), row=2, col=1)
    # pink vertical areas where a barge waits for the tide (plain shapes: fast, also for many areas)
    shapes = [dict(type="rect", xref="x", yref="y domain", x0=s["start"].isoformat(), x1=s["finish"].isoformat(),
                   y0=0, y1=1, fillcolor="#e377c2", opacity=0.3, line=dict(width=0), layer="below")
              for s in res.segments if s["category"] == CAT_TIDE]
    fig.update_xaxes(type="date")
    # buttons on the bottom x-axis: the top axis follows it (shared axes), placed above the tidal graph
    fig.update_xaxes(row=2, col=1, rangeselector=dict(x=0, y=1.02, yanchor="bottom", buttons=[
        dict(count=7, label="1 week", step="day", stepmode="backward"),
        dict(count=14, label="2 weeks", step="day", stepmode="backward"),
        dict(count=1, label="1 month", step="month", stepmode="backward"),
        dict(count=3, label="3 months", step="month", stepmode="backward"),
        dict(label="All", step="all")]))
    fig.update_xaxes(row=2, col=1, rangeslider=dict(visible=True, thickness=0.06))
    fig.update_yaxes(title_text="Tide level (cm)", fixedrange=False, row=1, col=1)
    fig.update_yaxes(categoryorder="array", categoryarray=[b.name for b in res.barges][::-1], fixedrange=True,
                     row=2, col=1)
    small = ("Pink areas: waiting for the tide. Zoom with the buttons, the range slider below or by dragging in "
             "the graph (also vertically); double-click to reset.")
    if tide.repeated:
        small = (f"Tidal data {tide.data_start:%Y-%m-%d} - {tide.data_end:%Y-%m-%d} repeated for other years. "
                 + small)
    n = sum(len(t.loadins) for t in res.trips)
    _layout_title(fig, ["<b>Tide at the marshalling port and barge activities</b>",
                        f"{n} load-ins | tidal wait {res.total_tidal_wait_h:.0f} h "
                        f"(average {res.total_tidal_wait_h / n:.1f} h per load-in)<br><sup>{small}</sup>"], 720)
    fig.update_layout(barmode="overlay", hovermode="closest", margin=dict(t=125, l=70), dragmode="zoom")
    fig_dict = json.loads(fig.to_json())
    fig_dict["layout"]["shapes"] = shapes
    return fig_dict


def build_table(res) -> go.Figure:
    by_mp = {m.number: t for t in res.trips for m in t.monopiles}
    header = ["No.", "Location", "Weight (t)", "Class", "Ready", "Produced", "Barge / trip", "Transport",
              "Load-out done", "Arrival port", "Load-in", "Required tide (cm)", "Tidal wait (h)"]
    rows = []
    for m in res.monopiles:
        t = by_mp[m.number]
        rows.append([m.number, m.name, f"{m.weight_t:g}", m.cargo_class, f"{m.ready:%d-%m-%Y}",
                     f"{m.produced:%d-%m-%Y %H:%M}" + (f" (+{m.production_delay_h:.0f} h)"
                                                       if m.production_delay_h > 1e-9 else ""),
                     f"{t.barge} / {t.trip_no}", t.mode, f"{m.loaded_out:%d-%m-%Y %H:%M}",
                     f"{t.arrival:%d-%m-%Y %H:%M}", f"{m.loadin_start:%d-%m-%Y %H:%M}", f"{m.loadin_cm:.0f}",
                     f"{m.loadin_wait_h:.1f}"])
    cols = list(map(list, zip(*rows)))
    color = ["#f8d0d0" if "(+" in r[5] else "white" for r in rows]
    fig = go.Figure(go.Table(header=dict(values=header, align="left", fill_color="#e6e6e6"),
                             cells=dict(values=cols, align="left", fill_color=[color] * len(header))))
    per_barge = " | ".join(f"{b.name}: {sum(t.barge == b.name for t in res.trips)} trips" for b in res.barges)
    _layout_title(fig, ["<b>Monopile schedule</b>", f"{_summary(res)}<br>{per_barge}"], 800)
    fig.update_layout(margin=dict(t=95, l=10, r=10))
    return fig


def build_detail(res, barge: str, trip_no: int) -> dict:
    """Detail of one trip at the marshalling port: full tidal curve around the load-in(s) with the required tide
    levels, the moments the tide is rising and high enough (green), and below the trip's activities on an hourly
    time axis - so it can be checked that each load-in starts on a rising tide at the required level."""
    trip = next((t for t in res.trips if t.barge == barge and t.trip_no == trip_no), None)
    if trip is None:
        raise vkt.UserError(f"Trip {trip_no} of barge {barge} does not exist - choose another trip.")
    tide = res.tide
    label = f"{trip.barge} trip {trip.trip_no}"
    segs = [s for s in res.segments if s["trip"] == label]
    t0 = trip.arrival - timedelta(hours=6)
    t1 = trip.finish_port + timedelta(hours=6)
    hrs = np.arange(tide.to_hours(t0), tide.to_hours(t1), 1 / 12)
    times = [tide.t0 + timedelta(hours=float(h)) for h in hrs]
    level = tide.level_cm(hrs)
    rising = tide.rising(hrs)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.04)
    fig.add_trace(go.Scatter(x=times, y=level, mode="lines", name="Tide level", line=dict(color="#1f77b4", width=2),
                             hovertemplate="%{x|%d-%m %H:%M}  %{y:.0f} cm<extra></extra>"), row=1, col=1)
    # required level per load-in configuration of this trip (first load-in of a double trip: Double level)
    configs = []
    for n, (m, lvl, w, st, en) in enumerate(trip.loadins):
        if trip.mode == DOUBLE and n == 0:
            name = f"Double ({' + '.join(x.name for x in trip.monopiles)})"
        else:
            name = f"Single ({m.name}, class {m.cargo_class})"
        if lvl not in [c[1] for c in configs]:
            configs.append((name, lvl))
    dur = res.cycle.loadin_h
    fills = ["rgba(44,160,44,0.55)", "rgba(44,160,44,0.28)"]
    lines = ["#1a7f1a", "#6fbf6f"]
    shapes = []
    short_legend = True
    for k, (name, lvl) in enumerate(configs):
        # green: tide at or above the level for at least the load-in duration, starting on a rising tide
        # (load-in possible); grey: above the level, but shorter than the load-in duration
        above = level >= lvl
        edges = np.flatnonzero(np.diff(np.concatenate([[0], above.astype(int), [0]])))
        ok_x, ok_y, short_x, short_y = [], [], [], []
        for a, b in zip(edges[::2], edges[1::2]):
            run_h = (b - a) / 12
            xs = times[a:b] + times[a:b][::-1] + [None]
            ys = list(level[a:b]) + [lvl] * (b - a) + [None]
            if run_h >= dur and (a == 0 or rising[a]):
                ok_x += xs
                ok_y += ys
            else:
                short_x += xs
                short_y += ys
        fig.add_trace(go.Scatter(x=ok_x, y=ok_y, fill="toself", fillcolor=fills[k % 2], mode="lines",
                                 line=dict(width=0), hoverinfo="skip",
                                 name=f"{name}: tide >= {lvl:.0f} cm for >= {dur:g} h (load-in possible)"),
                      row=1, col=1)
        if short_x:
            fig.add_trace(go.Scatter(x=short_x, y=short_y, fill="toself", fillcolor="rgba(150,150,150,0.35)",
                                     mode="lines", line=dict(width=0), hoverinfo="skip",
                                     name=f"Above the level, but shorter than {dur:g} h", legendgroup="short",
                                     showlegend=short_legend), row=1, col=1)
            short_legend = False
        shapes.append(dict(type="line", xref="x domain", x0=0, x1=1, yref="y", y0=lvl, y1=lvl,
                           line=dict(color=lines[k % 2], dash="dash", width=2)))
        fig.add_annotation(xref="x domain", x=0.005, yref="y", y=lvl, xanchor="left", yanchor="bottom",
                           showarrow=False, text=f"{name}: required {lvl:.0f} cm",
                           font=dict(color=lines[k % 2]), row=1, col=1)
    for m, lvl, w, s, e in trip.loadins:
        shapes.append(dict(type="rect", xref="x", yref="y domain", x0=s.isoformat(), x1=e.isoformat(), y0=0, y1=1,
                           fillcolor="#1f77b4", opacity=0.12, line=dict(color="#1f77b4", width=1), layer="below"))
        fig.add_trace(go.Scatter(
            x=[s], y=[float(tide.level_cm(tide.to_hours(s)))], mode="markers+text", showlegend=False,
            marker=dict(symbol="triangle-up", size=13, color="#1f77b4"), text=[f"{m.name}"], textposition="top center",
            hoverinfo="text", hovertext=[f"Load-in {m.name} {s:%d-%m %H:%M} - {e:%H:%M} | required {lvl:.0f} cm | "
                                         f"tide {float(tide.level_cm(tide.to_hours(s))):.0f} cm, rising | "
                                         f"tidal wait {w:.1f} h"]), row=1, col=1)
    for s in segs:
        if s["category"] == CAT_TIDE:
            shapes.append(dict(type="rect", xref="x", yref="y domain", x0=s["start"].isoformat(),
                               x1=s["finish"].isoformat(), y0=0, y1=1, fillcolor="#e377c2", opacity=0.25,
                               line=dict(width=0), layer="below"))
    # trip activities on an hourly axis
    for cat, color in COLORS.items():
        cs = [s for s in segs if s["category"] == cat and s["finish"] > t0 and s["start"] < t1]
        if not cs:
            continue
        fig.add_trace(go.Bar(
            base=[max(s["start"], t0) for s in cs],
            x=[(min(s["finish"], t1) - max(s["start"], t0)).total_seconds() * 1000 for s in cs],
            y=[cat] * len(cs), orientation="h", marker_color=color, name=cat, showlegend=False,
            text=[f"{(s['finish'] - s['start']).total_seconds() / 3600:.1f} h" for s in cs], textposition="inside",
            insidetextanchor="middle", hoverinfo="text",
            hovertext=[f"{cat}: {s['detail']}<br>{s['start']:%d-%m %H:%M} - {s['finish']:%d-%m %H:%M} "
                       f"({(s['finish'] - s['start']).total_seconds() / 3600:.1f} h)" for s in cs]), row=2, col=1)
    order = [c for c in COLORS if any(s["category"] == c and s["finish"] > t0 and s["start"] < t1 for s in segs)]
    fig.update_yaxes(categoryorder="array", categoryarray=order[::-1], row=2, col=1)
    fig.update_yaxes(title_text="Tide level (cm)", row=1, col=1)
    # opens zoomed in on the load-in(s) with an hourly grid; zoom out (double-click) for the whole port stay
    z0 = min(x[3] for x in trip.loadins) - timedelta(hours=8)
    z1 = max(x[4] for x in trip.loadins) + timedelta(hours=8)
    hourly = dict(dtick=3600000, showgrid=True, gridcolor="#e0e0e0")
    fig.update_xaxes(type="date", range=[z0.isoformat(), z1.isoformat()], minor=hourly, gridcolor="#b0b0b0")
    fig.update_xaxes(tickformat="%H:%M\n%d-%m-%Y", row=2, col=1)
    mps = " + ".join(f"{m.name} ({m.weight_t:g} t, class {m.cargo_class})" for m in trip.monopiles)
    loadins = " | ".join(f"load-in {m.name} {s:%d-%m %H:%M} at >= {lvl:.0f} cm (wait {w:.1f} h)"
                         for m, lvl, w, s, e in trip.loadins)
    _layout_title(fig, [f"<b>Detail {label}</b> - {trip.mode.lower()} configuration: {mps}",
                        f"Arrival {trip.arrival:%d-%m-%Y %H:%M} | {loadins}<br><sup>Green: tide at or above the "
                        f"required level of this barge configuration for at least the load-in duration "
                        f"({res.cycle.loadin_h:g} h) - load-in possible. Grey: above the level but too short. Blue band: "
                        f"load-in, pink: waiting for the tide. Thin grid lines: every hour; double-click for the whole "
                        f"port stay.</sup>"], 720)
    fig.update_layout(barmode="overlay", hovermode="closest", margin=dict(t=110, l=230), legend_title_text="",
                      legend=dict(orientation="h", y=-0.12))
    fig_dict = json.loads(fig.to_json())
    fig_dict["layout"]["shapes"] = shapes
    return fig_dict


def build_stock(res) -> go.Figure:
    """Stock curves: monopiles at the fabricator (with the storage capacity) and on the barges (top), and
    monopiles delivered at the marshalling port (bottom)."""
    st = res.stock()

    def xy(pts):
        return [t for t, _ in pts], [n for _, n in pts]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.5, 0.5], vertical_spacing=0.08,
                        subplot_titles=("Fabricator and on the barges", "Marshalling port (delivered)"))
    x, y = xy(st["fabricator"])
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", line_shape="hv", name="At the fabricator",
                             line=dict(color="#8c564b", width=2), fill="tozeroy",
                             fillcolor="rgba(140,86,75,0.15)"), row=1, col=1)
    x, y = xy(st["barges"])
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", line_shape="hv", name="On the barges (in transit / waiting)",
                             line=dict(color="#1f77b4", width=2, dash="dot")), row=1, col=1)
    if res.yard_capacity:
        fig.add_hline(y=res.yard_capacity, line=dict(color="#d62728", dash="dash"), row=1, col=1,
                      annotation_text=f"Fabricator storage capacity {res.yard_capacity}",
                      annotation_position="top left")
    delayed = [m for m in res.monopiles if m.production_delay_h > 1e-9]
    if delayed:
        fig.add_trace(go.Scatter(
            x=[m.produced for m in delayed], y=[res.yard_capacity] * len(delayed), mode="markers",
            name="Production delayed (storage full)", marker=dict(symbol="x", size=10, color="#d62728"),
            text=[f"{m.number} {m.name}: ready {m.ready:%d-%m-%Y}, produced {m.produced:%d-%m-%Y %H:%M} "
                  f"(+{m.production_delay_h:.0f} h)" for m in delayed], hoverinfo="text"), row=1, col=1)
    x, y = xy(st["port"])
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", line_shape="hv", name="At the marshalling port",
                             line=dict(color="#2ca02c", width=2), fill="tozeroy",
                             fillcolor="rgba(44,160,44,0.15)"), row=2, col=1)
    peak = max(n for _, n in st["fabricator"])
    _layout_title(fig, ["<b>Monopile stock</b>",
                        f"Fabricator max. {peak}" + (f" (capacity {res.yard_capacity})" if res.yard_capacity else "")
                        + f" | {res.production_delay_count} production delay(s) | marshalling port "
                          f"{len(res.monopiles)} delivered, last {res.finish:%d %b %Y}"], 720)
    fig.update_yaxes(title_text="Monopiles", dtick=1, rangemode="tozero", row=1, col=1)
    fig.update_yaxes(title_text="Monopiles", rangemode="tozero", row=2, col=1)
    fig.update_xaxes(type="date")
    fig.update_layout(margin=dict(t=95), hovermode="x unified")
    return fig


# ---------------------------------------------------------------- controller
class TransportPlan(vkt.Controller):
    label = "Transport plan"
    parametrization = Parametrization

    def update_tables(self, params, **kwargs):
        """Tide level table (rows = cargo classes) and barge start table in line with the fleet / classes;
        existing values are kept, new rows get the default values."""
        fleet = _fleet(params)
        if not fleet or len(fleet) > MAX_BARGES:
            raise vkt.UserError(f"Define 1 to {MAX_BARGES} barges.")
        n, d = len(fleet), sum(c == DOUBLE for _, c in fleet)
        name = _table_name(n, d)
        codes = [str(r.name or "").strip() for r in (params.cargo.classes or []) if str(r.name or "").strip()]
        old = {str(r.code or "").strip(): dict(r) for r in (params.cargo.get(name) or [])}
        matrix = []
        for code in codes:                                   # rows = cargo classes; empty cells get the default
            row = dict(old.get(code) or {})
            default = _matrix_row(code, n, d)
            matrix.append({k: (row.get(k) if str(row.get(k) or "").strip() else v) for k, v in default.items()})
        starts_old = {str(r.name or "").strip(): r.start for r in (params.planning.starts or [])}
        first = starts_old.get(fleet[0][0]) if fleet else None
        starts = [dict(name=n, start=starts_old.get(n) or first or "15/03/2028") for n, _ in fleet]
        return vkt.SetParamsResult({"cargo": {name: matrix}, "planning": {"starts": starts}})

    @vkt.PlotlyView("Planning", duration_guess=1)
    def planning_view(self, params, **kwargs):
        res = _schedule(params)
        plan = params.planning or {}
        if plan.get("chart_mode") == CHART_DETAIL:
            barge = plan.get("detail_barge") or res.barges[0].name
            return vkt.PlotlyResult(build_detail(res, barge, _trip_number(plan.get("detail_trip"))))
        return vkt.PlotlyResult(build_gantt(res).to_json())

    @vkt.PlotlyView("Tide", duration_guess=1)
    def tide_view(self, params, **kwargs):
        return vkt.PlotlyResult(build_tide(_schedule(params)))

    @vkt.PlotlyView("Stock", duration_guess=1)
    def stock_view(self, params, **kwargs):
        return vkt.PlotlyResult(build_stock(_schedule(params)).to_json())

    @vkt.PlotlyView("Table", duration_guess=1)
    def table_view(self, params, **kwargs):
        return vkt.PlotlyResult(build_table(_schedule(params)).to_json())

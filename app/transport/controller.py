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

from .schedule import (CAT_ALLOW, CAT_BERTH_FAB, CAT_BERTH_MP, CAT_DEP, CAT_DEP_FAB, CAT_ENTRY, CAT_LOAD,
                       CAT_LOADIN, CAT_RETURN, CAT_SAIL, CAT_TIDE, CAT_WAIT_MP, DOUBLE, READY_EARLIEST, READY_LATEST,
                       SINGLE, Barge, CargoClass, Cycle, Monopile, plan_transport)
from .tide import PlanningError, load_tide

DEFAULT_TIDE = Path(__file__).resolve().parents[2] / "sample_data" / "gunsan_tide_2026_hourly.csv"
MAX_BARGES = 6
S_COLS = tuple(f"s{k}" for k in range(1, MAX_BARGES + 1))      # tide matrix: Single columns = barge 1 ... 6
D_COLS = tuple(f"d{k}" for k in range(1, MAX_BARGES + 1))      # Double columns = barge 1 ... 6
NOT_ALLOWED = "x"
TABS = ("input", "cargo", "fabricator", "port", "planning")

COLORS = {CAT_BERTH_FAB: "#bcbd22", CAT_LOAD: "#8c564b", CAT_WAIT_MP: "#c7c7c7", CAT_DEP_FAB: "#9467bd",
          CAT_SAIL: "#1f77b4", CAT_BERTH_MP: "#ff7f0e", CAT_ENTRY: "#9467bd", CAT_TIDE: "#d62728",
          CAT_LOADIN: "#2ca02c", CAT_ALLOW: "#98df8a", CAT_DEP: "#9467bd", CAT_RETURN: "#aec7e8"}

DEFAULT_BARGES = [dict(name="barge_1", configuration=DOUBLE), dict(name="barge_2", configuration=DOUBLE),
                  dict(name="barge_3", configuration=SINGLE)]
DEFAULT_STARTS = [dict(name="barge_1", start="15/03/2028"), dict(name="barge_2", start="15/03/2028"),
                  dict(name="barge_3", start="15/05/2028")]
DEFAULT_CLASSES = [dict(name="A", from_t=1000, to_t=1200), dict(name="B", from_t=1200, to_t=1400),
                   dict(name="C", from_t=1400, to_t=1600), dict(name="D", from_t=1600, to_t=1800)]
# per class: single level (all barges), double level (Double barges)
_DEFAULT_LEVELS = {"A": ("520", "550"), "B": ("540", "600"), "C": ("560", NOT_ALLOWED), "D": ("580", NOT_ALLOWED)}


def _matrix_row(code, barges):
    single, double = _DEFAULT_LEVELS.get(code, ("", ""))
    row = dict(code=code)
    for k, col in enumerate(S_COLS):
        row[col] = single if k < len(barges) else ""
    for k, col in enumerate(D_COLS):
        row[col] = double if k < len(barges) and barges[k][1] == DOUBLE else ""
    return row


_FLEET = [(b["name"], b["configuration"]) for b in DEFAULT_BARGES]
DEFAULT_MATRIX = [_matrix_row(c["name"], _FLEET) for c in DEFAULT_CLASSES]

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
    single = ", ".join(f"Single {k + 1} = {n}" for k, (n, _) in enumerate(fleet))
    double = ", ".join(f"Double {k + 1} = {n}" for k, (n, c) in enumerate(fleet) if c == DOUBLE) or "none"
    return f"{single} | {double}"


def _sailing_text(params, **kwargs) -> str:
    p = params.input if "input" in params else params
    speed = float(p.speed_kn or 0)
    if speed <= 0:
        return "-"
    hrs = float(p.distance_nm or 0) / speed
    return f"{hrs:.1f} h ({hrs / 24:.1f} days) each way"


# ---------------------------------------------------------------- parametrization
class Parametrization(vkt.Parametrization):
    # ---------------- Input: purpose, barge fleet, generic barge cycle
    input = vkt.Tab("Input")
    input.title = vkt.Text("## Tidal Transport Planner")
    input.intro = vkt.Text("*Insight in the transport planning of monopile barges from the fabricator to the "
                           "marshalling port, given the tidal restrictions for the load-in at the marshalling port.*")
    input.barge_text = vkt.Text("### Barge fleet\n*Single: carries exactly 1 monopile. Double: carries 1 or 2 "
                                f"monopiles. Up to {MAX_BARGES} barges.*")
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
    input.loadin_h = vkt.NumberField("Load-in duration per MP", suffix="h", default=3, min=0, flex=50)
    input.allowance_h = vkt.NumberField("General load-in time allowance", suffix="h", default=21, min=0, flex=50)
    input.dep_h = vkt.NumberField("Port departure", suffix="h", default=6, min=0, flex=50)
    input.loadin_note = vkt.Text(
        "*Load-in starts on a rising tide from a particular tide level. This tide level depends on the load "
        "configuration (tab Cargo-cycle); if the tide level is not reached the barge has to wait. If a barge has two "
        "monopiles loaded and the first one is transferred, it waits until the next tide for the next load-in.*")

    # ---------------- Cargo-cycle: weight classes and tide matrix
    cargo = vkt.Tab("Cargo-cycle")
    cargo.class_text = vkt.Text("### Cargo weight classes\n*From weight <= weight < to weight.*")
    cargo.classes = vkt.Table("Cargo weight classes", default=DEFAULT_CLASSES)
    cargo.classes.name = vkt.TextField("Code")
    cargo.classes.from_t = vkt.NumberField("From", suffix="t")
    cargo.classes.to_t = vkt.NumberField("To", suffix="t")
    cargo.matrix_text = vkt.Text(
        "### Tide levels for load-in (cm)\n*Rows: cargo classes. Columns: barges - first the **Single** "
        "configuration (one monopile on board), then the **Double** configuration (two monopiles). The value is the "
        "tide level needed for the heaviest monopile on board; 'x' = configuration not possible.*")
    cargo.columns = vkt.OutputField("Columns", value=_matrix_columns, flex=100)
    cargo.matrix = vkt.Table("Tide levels for load-in (cm)", default=DEFAULT_MATRIX)
    cargo.matrix.code = vkt.TextField("Code")
    for _k, _col in enumerate(S_COLS):
        setattr(cargo.matrix, _col, vkt.TextField(f"Single {_k + 1}"))
    for _k, _col in enumerate(D_COLS):
        setattr(cargo.matrix, _col, vkt.TextField(f"Double {_k + 1}"))

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

    # ---------------- Planning: barge start dates
    planning = vkt.Tab("Planning")
    planning.start_text = vkt.Text("### Barge start\n*The barges are assumed to start at the fabricator yard on "
                                   "their start date. With one load-out berth, barges wait until the berth is "
                                   "free.*")
    planning.starts = vkt.Table("Barge start", default=DEFAULT_STARTS)
    planning.starts.name = vkt.TextField("Barge")
    planning.starts.start = vkt.TextField("Start (dd/mm/yyyy)")
    planning.max_wait_second = vkt.NumberField("Double barge: max. wait for the second monopile", suffix="h",
                                               default=24, min=0,
                                               description="A Double barge waits at most this long after loading "
                                                           "the first monopile for the next one; otherwise it sails "
                                                           "with one.")


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
        raise vkt.UserError(f"At most {MAX_BARGES} barges (columns of the tide level table).")
    matrix = {str(r.code or "").strip(): r for r in (p.matrix or [])}
    missing = [c.name for c in classes if c.name not in matrix]
    starts = {str(r.name or "").strip(): r.start for r in (p.starts or [])}
    if missing or any(n not in starts for n, _ in fleet):
        raise vkt.UserError("The tide level table or the barge start table does not match the barges / cargo classes "
                            "- press 'Update tide matrix and barge start tables' (tab Input).")
    barges = []
    for k, (name, conf) in enumerate(fleet):
        try:
            start = _to_date(starts[name])
        except ValueError:
            start = None
        if start is None:
            raise vkt.UserError(f"Barge start: '{starts[name]}' for {name} is not a valid date (dd/mm/yyyy).")
        single = {c.name: _cm(matrix[c.name].get(S_COLS[k]), f"Single {k + 1} ({name}) class {c.name}")
                  for c in classes}
        double = ({c.name: _cm(matrix[c.name].get(D_COLS[k]), f"Double {k + 1} ({name}) class {c.name}")
                   for c in classes} if conf == DOUBLE else {})
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


_CACHE: dict = {}


def _schedule(params):
    p = _flat(params)
    data = _tide_bytes(p)
    key = hashlib.sha1((json.dumps({k: v for k, v in p.items() if k != "tide_file"}, default=str, sort_keys=True)
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
                ready=p.ready or READY_EARLIEST, max_wait_second_h=float(p.max_wait_second or 0))
        except PlanningError as exc:
            raise vkt.UserError(str(exc))
    return _CACHE[key]


# ---------------------------------------------------------------- figures
def _summary(res) -> str:
    n_double = sum(t.mode == DOUBLE for t in res.trips)
    txt = (f"{len(res.monopiles)} monopiles in {len(res.trips)} trips ({n_double} double) | "
           f"start {res.start:%d %b %Y} | last load-in {res.finish:%d %b %Y} | tidal wait {res.total_tidal_wait_h:.0f} h"
           f" | berth wait {res.total_berth_wait_h:.0f} h")
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


def build_loadin(res) -> go.Figure:
    """Tide at the marshalling port with every load-in (required level on a rising tide) and below the port
    activities (tidal wait / load-in) per barge."""
    tide = res.tide
    t0 = min(t.arrival for t in res.trips) - timedelta(hours=12)
    t1 = res.finish + timedelta(hours=12)
    hrs = np.arange(tide.to_hours(t0), tide.to_hours(t1), 0.25)
    times = [tide.t0 + timedelta(hours=float(h)) for h in hrs]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.04)
    fig.add_trace(go.Scatter(x=times, y=tide.level_cm(hrs), mode="lines", name="Tide level",
                             line=dict(color="#1f77b4", width=1.2)), row=1, col=1)
    barge_colors = px.colors.qualitative.Dark2
    for k, b in enumerate(res.barges):
        loadins = [(t, x) for t in res.trips if t.barge == b.name for x in t.loadins]
        if not loadins:
            continue
        color = barge_colors[k % len(barge_colors)]
        fig.add_trace(go.Scatter(
            x=[x[3] for _, x in loadins], y=[x[1] for _, x in loadins], mode="markers",
            name=f"{b.name} load-in", marker=dict(symbol="triangle-up", size=11, color=color),
            hoverinfo="text",
            text=[f"{b.name} trip {t.trip_no} ({t.mode.lower()}): load-in {m.name} (class {m.cargo_class})<br>"
                  f"Start {s:%d-%m %H:%M} on a rising tide >= {lvl:.0f} cm | tidal wait {w:.1f} h"
                  for t, (m, lvl, w, s, e) in loadins]), row=1, col=1)
    for cat in (CAT_TIDE, CAT_LOADIN):
        segs = [s for s in res.segments if s["category"] == cat]
        if segs:
            fig.add_trace(go.Bar(
                base=[s["start"] for s in segs], x=[(s["finish"] - s["start"]).total_seconds() * 1000 for s in segs],
                y=[s["barge"] for s in segs], orientation="h", marker_color=COLORS[cat], name=cat,
                hoverinfo="text", text=[f"{s['trip']}: {s['detail']} {s['start']:%d-%m %H:%M} - "
                                        f"{s['finish']:%d-%m %H:%M}" for s in segs], textposition="none"),
                row=2, col=1)
    fig.update_xaxes(type="date")
    fig.update_yaxes(title_text="Tide level (cm)", row=1, col=1)
    fig.update_yaxes(categoryorder="array", categoryarray=[b.name for b in res.barges][::-1], row=2, col=1)
    note = ""
    if tide.repeated:
        note = (f"<br><sup>Tidal data {tide.data_start:%Y-%m-%d} - {tide.data_end:%Y-%m-%d} repeated for other "
                f"years</sup>")
    n = sum(len(t.loadins) for t in res.trips)
    _layout_title(fig, ["<b>Load-in at the marshalling port</b>",
                        f"{n} load-ins | tidal wait {res.total_tidal_wait_h:.0f} h "
                        f"(average {res.total_tidal_wait_h / n:.1f} h per load-in){note}"], 650)
    fig.update_layout(barmode="overlay", hovermode="closest", margin=dict(t=90))
    return fig


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


def build_yard(res) -> go.Figure:
    """Number of produced monopiles stored at the fabrication yard over time, with the yard capacity."""
    stock = res.yard_stock()
    fig = go.Figure(go.Scatter(x=[t for t, _ in stock], y=[n for _, n in stock], mode="lines", line_shape="hv",
                               name="Monopiles at the yard", line=dict(color="#1f77b4", width=2),
                               fill="tozeroy", fillcolor="rgba(31,119,180,0.15)"))
    if res.yard_capacity:
        fig.add_hline(y=res.yard_capacity, line=dict(color="#d62728", dash="dash"),
                      annotation_text=f"Storage capacity {res.yard_capacity}", annotation_position="top left")
    delayed = [m for m in res.monopiles if m.production_delay_h > 1e-9]
    if delayed:
        fig.add_trace(go.Scatter(
            x=[m.produced for m in delayed], y=[res.yard_capacity] * len(delayed), mode="markers",
            name="Production delayed (yard full)", marker=dict(symbol="x", size=10, color="#d62728"),
            text=[f"{m.number} {m.name}: ready {m.ready:%d-%m-%Y}, produced {m.produced:%d-%m-%Y %H:%M} "
                  f"(+{m.production_delay_h:.0f} h)" for m in delayed], hoverinfo="text"))
    peak = max(n for _, n in stock)
    _layout_title(fig, ["<b>Fabrication yard stock</b>",
                        f"Max. {peak} monopiles stored" + (f" (capacity {res.yard_capacity})" if res.yard_capacity
                                                           else "") +
                        f" | {res.production_delay_count} production delay(s)"], 520)
    fig.update_yaxes(title_text="Monopiles at the yard", dtick=1, rangemode="tozero")
    fig.update_layout(margin=dict(t=80), hovermode="x unified")
    return fig


# ---------------------------------------------------------------- controller
class TransportPlan(vkt.Controller):
    label = "Transport plan"
    parametrization = Parametrization

    def update_tables(self, params, **kwargs):
        """Tide level table (rows = cargo classes) and barge start table in line with the fleet / classes;
        existing values are kept, new rows get the default values."""
        fleet = _fleet(params)
        codes = [str(r.name or "").strip() for r in (params.cargo.classes or []) if str(r.name or "").strip()]
        old = {str(r.code or "").strip(): dict(r) for r in (params.cargo.matrix or [])}
        matrix = []
        for code in codes:
            row = old.get(code) or _matrix_row(code, fleet)
            for k, (name, conf) in enumerate(fleet):            # new barge columns get defaults, removed are emptied
                default = _matrix_row(code, fleet)
                if not str(row.get(S_COLS[k]) or "").strip():
                    row[S_COLS[k]] = default[S_COLS[k]]
                if conf == DOUBLE and not str(row.get(D_COLS[k]) or "").strip():
                    row[D_COLS[k]] = default[D_COLS[k]]
                if conf != DOUBLE:
                    row[D_COLS[k]] = ""
            for k in range(len(fleet), MAX_BARGES):
                row[S_COLS[k]] = row[D_COLS[k]] = ""
            matrix.append(row)
        starts_old = {str(r.name or "").strip(): r.start for r in (params.planning.starts or [])}
        first = starts_old.get(fleet[0][0]) if fleet else None
        starts = [dict(name=n, start=starts_old.get(n) or first or "15/03/2028") for n, _ in fleet]
        return vkt.SetParamsResult({"cargo": {"matrix": matrix}, "planning": {"starts": starts}})

    @vkt.PlotlyView("Planning", duration_guess=1)
    def planning_view(self, params, **kwargs):
        return vkt.PlotlyResult(build_gantt(_schedule(params)).to_json())

    @vkt.PlotlyView("Load-in", duration_guess=1)
    def loadin_view(self, params, **kwargs):
        return vkt.PlotlyResult(build_loadin(_schedule(params)).to_json())

    @vkt.PlotlyView("Yard", duration_guess=1)
    def yard_view(self, params, **kwargs):
        return vkt.PlotlyResult(build_yard(_schedule(params)).to_json())

    @vkt.PlotlyView("Table", duration_guess=1)
    def table_view(self, params, **kwargs):
        return vkt.PlotlyResult(build_table(_schedule(params)).to_json())

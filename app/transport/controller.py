import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import viktor as vkt

from .schedule import (CAT_DISCHARGE, CAT_LOAD, CAT_RETURN, CAT_SAIL, CAT_TIDE, CAT_WAIT_MP, DOUBLE, SINGLE,
                       Barge, CargoClass, Monopile, plan_transport)
from .tide import PlanningError, load_tide

DEFAULT_TIDE = Path(__file__).resolve().parents[2] / "sample_data" / "gunsan_tide_2026_hourly.csv"
CLASS_COLS = ("c1", "c2", "c3", "c4", "c5", "c6")      # tide matrix columns = 1st ... 6th cargo class
MAX_CLASSES = len(CLASS_COLS)
NOT_ALLOWED = "x"

COLORS = {CAT_LOAD: "#8c564b", CAT_WAIT_MP: "#c7c7c7", CAT_SAIL: "#1f77b4", CAT_TIDE: "#d62728",
          CAT_DISCHARGE: "#2ca02c", CAT_RETURN: "#aec7e8"}

DEFAULT_BARGES = [dict(name="barge_1", configuration=DOUBLE), dict(name="barge_2", configuration=DOUBLE),
                  dict(name="barge_3", configuration=SINGLE)]
DEFAULT_CLASSES = [dict(name="A", from_t=1000, to_t=1200), dict(name="B", from_t=1200, to_t=1400),
                   dict(name="C", from_t=1400, to_t=1600), dict(name="D", from_t=1600, to_t=1800)]
DEFAULT_CYCLE = dict(loading_h=24.0, sailing_h=96.0, discharging_h=2.0, return_h=96.0)
DEFAULT_SINGLE = ["600", "620", "640", "660", "", ""]
DEFAULT_DOUBLE = ["650", "700", NOT_ALLOWED, NOT_ALLOWED, "", ""]


def _matrix_row(name, transport, values):
    return dict(name=name, transport=transport, **dict(zip(CLASS_COLS, values)))


DEFAULT_MATRIX = [
    _matrix_row("barge_1", SINGLE, DEFAULT_SINGLE),
    _matrix_row("barge_2", SINGLE, DEFAULT_SINGLE),
    _matrix_row("barge_3", SINGLE, ["600", "620", "640", NOT_ALLOWED, "", ""]),
    _matrix_row("barge_1", DOUBLE, DEFAULT_DOUBLE),
    _matrix_row("barge_2", DOUBLE, DEFAULT_DOUBLE),
]

_DELIVERIES = """1 OSS 1520 06/03/2028 21/03/2028
2 WTG-31 1368.3 07/03/2028 22/03/2028
3 WTG-34 1206.1 08/03/2028 23/03/2028
4 WTG-33 1258.1 16/03/2028 31/03/2028
5 WTG-07 1374.7 17/03/2028 01/04/2028
6 WTG-06 1427.8 26/03/2028 10/04/2028
7 WTG-05 1526.5 27/03/2028 11/04/2028
8 WTG-13 1588 04/04/2028 19/04/2028
9 WTG-25 1438.3 05/04/2028 20/04/2028
10 WTG-32 1250.4 13/04/2028 28/04/2028
11 WTG-30 1207.4 14/04/2028 29/04/2028
12 WTG-36 1343.1 22/04/2028 07/05/2028
13 WTG-24 1387 23/04/2028 08/05/2028
14 WTG-16 1506 01/05/2028 16/05/2028
15 WTG-23 1603.9 02/05/2028 17/05/2028
16 WTG-03 1309.6 10/05/2028 25/05/2028
17 WTG-02 1357 11/05/2028 26/05/2028
18 WTG-22 1478.3 19/05/2028 03/06/2028
19 WTG-10 1401.7 20/05/2028 04/06/2028
20 WTG-01 1446.4 02/06/2028 17/06/2028
21 WTG-35 1412.1 03/06/2028 18/06/2028
22 WTG-08 1373.7 11/06/2028 26/06/2028
23 WTG-15 1291.7 12/06/2028 27/06/2028
24 WTG-14 1282.9 20/06/2028 05/07/2028
25 WTG-12 1247.9 21/06/2028 06/07/2028
26 WTG-04 1526.5 04/07/2028 19/07/2028
27 WTG-11 1440 05/07/2028 20/07/2028
28 WTG-21 1358.2 13/07/2028 28/07/2028
29 WTG-20 1391.9 14/07/2028 29/07/2028
30 WTG-19 1325.3 01/08/2028 16/08/2028
31 WTG-09 1146 02/08/2028 17/08/2028
32 WTG-27 1724.7 10/08/2028 25/08/2028
33 WTG-26 1579.6 11/08/2028 26/08/2028
34 WTG-17 1611 19/08/2028 03/09/2028
35 WTG-18 1596 20/08/2028 04/09/2028
36 WTG-29 1401.9 27/08/2028 11/09/2028
37 WTG-28 1250.9 31/08/2028 15/09/2028"""


def _dmy(text: str) -> str:
    d, m, y = text.split("/")
    return f"{y}-{m}-{d}"


DEFAULT_DELIVERIES = [dict(number=int(n), name=nm, weight_t=float(w), earliest=_dmy(e), latest=_dmy(la))
                      for n, nm, w, e, la in (line.split() for line in _DELIVERIES.splitlines())]


# ---------------------------------------------------------------- parametrization
class Parametrization(vkt.Parametrization):
    input = vkt.Tab("Input")
    input.title = vkt.Text("## Tidal Transport Planner")
    input.intro = vkt.Text(
        "*This application generates transport schedules for monopile transport barges while considering cargo "
        "weight classes, barge configurations and tidal restrictions.*")

    input.barge_text = vkt.Text("### Barges\n*Single: carries exactly 1 monopile. Double: carries 1 or 2 "
                                "monopiles.*")
    input.barges = vkt.Table("Barge fleet", default=DEFAULT_BARGES)
    input.barges.name = vkt.TextField("Name")
    input.barges.configuration = vkt.OptionField("Configuration", options=[SINGLE, DOUBLE])
    input.update_tables = vkt.SetParamsButton(
        "Update barge cycle and tide restriction tables", method="update_tables", flex=100,
        description="Adds rows for new barges and removes rows of deleted barges; existing values are kept.")

    input.class_text = vkt.Text("### Cargo weight classes\n*From weight <= weight < to weight. Up to "
                                f"{MAX_CLASSES} classes.*")
    input.classes = vkt.Table("Cargo weight classes", default=DEFAULT_CLASSES)
    input.classes.name = vkt.TextField("Class")
    input.classes.from_t = vkt.NumberField("From weight", suffix="t")
    input.classes.to_t = vkt.NumberField("To weight", suffix="t")

    input.cycle_text = vkt.Text("### Barge cycle\n*Per barge: loading per monopile at the fabrication yard, "
                                "sailing to the marshalling port, discharging per monopile (load-in, only when the tide is at "
                                "or above the required level) and sailing back to the yard.*")
    input.cycle = vkt.Table("Barge cycle", default=[dict(name=b["name"], **DEFAULT_CYCLE) for b in DEFAULT_BARGES])
    input.cycle.name = vkt.TextField("Barge")
    input.cycle.loading_h = vkt.NumberField("Loading per monopile", suffix="h")
    input.cycle.sailing_h = vkt.NumberField("Sailing to port", suffix="h")
    input.cycle.discharging_h = vkt.NumberField("Discharging per monopile", suffix="h")
    input.cycle.return_h = vkt.NumberField("Sailing back", suffix="h")

    input.matrix_text = vkt.Text(
        "### Tide restrictions matrix\n*Minimum required tide level (cm) at the marshalling port for discharging. "
        "Columns 1-6 = the 1st to 6th cargo class of the table above (default A-D). Single: one monopile on "
        "board. Double: two monopiles, the class of the heaviest monopile counts. 'x' (or empty) = not "
        "permitted.*")
    input.tide_matrix = vkt.Table("Tide restrictions (cm)", default=DEFAULT_MATRIX)
    input.tide_matrix.name = vkt.TextField("Barge")
    input.tide_matrix.transport = vkt.OptionField("Transport", options=[SINGLE, DOUBLE])
    for _k, _col in enumerate(CLASS_COLS):
        setattr(input.tide_matrix, _col, vkt.TextField(f"Class {_k + 1}"))

    input.delivery_text = vkt.Text("### Delivery schedule\n*Monopiles available for load-out at the fabrication "
                                   "yard from the earliest date; load-out must be completed by the latest date.*")
    input.deliveries = vkt.Table("Delivery schedule", default=DEFAULT_DELIVERIES)
    input.deliveries.number = vkt.IntegerField("Load-out no.")
    input.deliveries.name = vkt.TextField("Location")
    input.deliveries.weight_t = vkt.NumberField("Weight", suffix="t")
    input.deliveries.earliest = vkt.DateField("Earliest")
    input.deliveries.latest = vkt.DateField("Latest")

    input.planning_text = vkt.Text("### Planning")
    input.start_date = vkt.DateField("Barges available at the yard from", default=date(2028, 3, 6))
    input.max_wait_second = vkt.NumberField("Double barge: max. wait for the second monopile", suffix="h",
                                            default=24, min=0,
                                            description="A Double barge waits at most this long (after loading "
                                                        "the first monopile) for the next monopile; otherwise it "
                                                        "sails with one.")

    input.tide_text = vkt.Text("### Tide at the marshalling port (load-in)")
    input.tide_file = vkt.FileField("Tidal data (optional, default Gunsan 2026)", file_types=[".csv"])
    input.tide_note = vkt.Text("*File format: .csv, comma separated, header row, then one row per time (e.g. "
                               "hourly): column 1 date-time as 2026-01-01 00:00 (or 01/01/2026 00:00, day first), "
                               "column 2 tide level in cm above chart datum (CD). The data of one year is repeated "
                               "for every other year (same calendar date and time).*")


# ---------------------------------------------------------------- inputs -> engine
def _tide_bytes(params) -> bytes:
    return params.tide_file.file.getvalue_binary() if params.tide_file else DEFAULT_TIDE.read_bytes()


def _to_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and v:
        return date.fromisoformat(v[:10])
    return None


def _cm(text, where: str):
    t = str(text if text is not None else "").strip().lower()
    if t in ("", NOT_ALLOWED):
        return None
    try:
        return float(t)
    except ValueError:
        raise vkt.UserError(f"Tide restrictions matrix, {where}: '{text}' is not a number or 'x'.")


def _inputs(params):
    classes = [CargoClass(str(r.name or "").strip() or f"Class {k + 1}", float(r.from_t or 0), float(r.to_t or 0))
               for k, r in enumerate(params.classes or [])]
    if len(classes) > MAX_CLASSES:
        raise vkt.UserError(f"At most {MAX_CLASSES} cargo classes (columns of the tide restrictions matrix).")
    cycle = {str(r.name or "").strip(): r for r in (params.cycle or [])}
    matrix = {(str(r.name or "").strip(), r.transport): r for r in (params.tide_matrix or [])}
    barges = []
    for r in params.barges or []:
        name = str(r.name or "").strip()
        if not name:
            continue
        cyc = cycle.get(name)
        single = matrix.get((name, SINGLE))
        double = matrix.get((name, DOUBLE))
        if cyc is None or single is None or (r.configuration == DOUBLE and double is None):
            raise vkt.UserError(f"Barge {name} is missing in the barge cycle or tide restrictions table - press "
                                f"'Update barge cycle and tide restriction tables'.")

        def req(row, transport):
            if row is None:
                return {}
            return {c.name: _cm(row.get(col), f"{name} {transport} class {c.name}")
                    for c, col in zip(classes, CLASS_COLS)}

        barges.append(Barge(name=name, configuration=r.configuration or SINGLE,
                            loading_h=float(cyc.loading_h or 0), sailing_h=float(cyc.sailing_h or 0),
                            discharging_h=float(cyc.discharging_h or 0), return_h=float(cyc.return_h or 0),
                            single_cm=req(single, SINGLE),
                            double_cm=req(double, DOUBLE) if r.configuration == DOUBLE else {}))
    monopiles = []
    for r in params.deliveries or []:
        if not (r.name or r.weight_t):
            continue
        e, la = _to_date(r.earliest), _to_date(r.latest)
        if e is None or la is None:
            raise vkt.UserError(f"Delivery schedule: monopile {r.number} ({r.name}) needs an earliest and latest date.")
        monopiles.append(Monopile(int(r.number or 0), str(r.name or ""), float(r.weight_t or 0), e, la))
    return barges, classes, monopiles


_CACHE: dict = {}


def _schedule(params):
    data = _tide_bytes(params)
    key = hashlib.sha1((json.dumps({k: v for k, v in params.items() if k != "tide_file"}, default=str,
                                   sort_keys=True) + hashlib.sha1(data).hexdigest()).encode()).hexdigest()
    if key not in _CACHE:
        if len(_CACHE) > 20:
            _CACHE.clear()
        if not params.start_date:
            raise vkt.UserError("Please fill in the date from which the barges are available.")
        barges, classes, monopiles = _inputs(params)
        try:
            tide = load_tide(data)
            _CACHE[key] = plan_transport(barges=barges, classes=classes, monopiles=monopiles, tide=tide,
                                         start=datetime.combine(_to_date(params.start_date), datetime.min.time()),
                                         max_wait_second_h=float(params.max_wait_second or 0))
        except PlanningError as exc:
            raise vkt.UserError(str(exc))
    return _CACHE[key]


# ---------------------------------------------------------------- figures
def _summary(res) -> str:
    n_double = sum(t.mode == DOUBLE for t in res.trips)
    late = f" | <span style='color:#d62728'>{res.late_count} late load-out(s)</span>" if res.late_count else \
        " | all load-outs on time"
    return (f"{len(res.monopiles)} monopiles in {len(res.trips)} trips ({n_double} double) | "
            f"start {res.start:%d %b %Y} | last load-in {res.finish:%d %b %Y} | "
            f"tidal wait {res.total_tidal_wait_h:.0f} h{late}")


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
    _layout_title(fig, ["<b>Transport schedule</b>", _summary(res)], 560)
    fig.update_layout(margin=dict(l=100, t=80))
    return fig


def build_loadin(res) -> dict:
    """Tide at the marshalling port with each load-in: required level (line from arrival to end of discharging)
    and the discharging moment; below the port activities (tidal wait / discharging) per barge."""
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
        trips = [t for t in res.trips if t.barge == b.name]
        if not trips:
            continue
        color = barge_colors[k % len(barge_colors)]
        xs, ys = [], []
        for t in trips:
            xs += [t.arrival, t.discharge_end, None]
            ys += [t.required_cm, t.required_cm, None]
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=f"{b.name} required tide",
                                 line=dict(color=color, width=3), hoverinfo="skip", legendgroup=b.name),
                      row=1, col=1)
        fig.add_trace(go.Scatter(
            x=[t.discharge_start for t in trips], y=[t.required_cm for t in trips], mode="markers",
            name=f"{b.name} load-in", marker=dict(symbol="triangle-down", size=11, color=color),
            legendgroup=b.name, hoverinfo="text",
            text=[f"{b.name} trip {t.trip_no}: {' + '.join(m.name for m in t.monopiles)} ({t.mode.lower()}, "
                  f"class {t.cargo_class})<br>Arrival {t.arrival:%d-%m %H:%M} | load-in {t.discharge_start:%d-%m %H:%M}"
                  f"<br>Required tide {t.required_cm:.0f} cm | tidal wait {t.tidal_wait_h:.1f} h" for t in trips]),
            row=1, col=1)
    for cat in (CAT_TIDE, CAT_DISCHARGE):
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
    _layout_title(fig, ["<b>Load-in at the marshalling port</b>",
                        f"{len(res.trips)} load-ins | tidal wait {res.total_tidal_wait_h:.0f} h "
                        f"(average {res.total_tidal_wait_h / len(res.trips):.1f} h per trip){note}"], 650)
    fig.update_layout(barmode="overlay", hovermode="closest", margin=dict(t=90))
    return fig


def build_table(res) -> go.Figure:
    by_mp = {}
    for t in res.trips:
        for m in t.monopiles:
            by_mp[m.number] = t
    header = ["No.", "Location", "Weight (t)", "Class", "Earliest", "Latest", "Barge / trip", "Transport",
              "Load-out done", "Arrival port", "Required tide (cm)", "Tidal wait (h)", "Load-in", "On time"]
    rows = []
    for m in res.monopiles:
        t = by_mp[m.number]
        rows.append([m.number, m.name, f"{m.weight_t:g}", m.cargo_class, f"{m.earliest:%d-%m-%Y}",
                     f"{m.latest:%d-%m-%Y}", f"{t.barge} / {t.trip_no}", t.mode,
                     f"{t.loaded_out[m.number]:%d-%m-%Y %H:%M}", f"{t.arrival:%d-%m-%Y %H:%M}",
                     f"{t.required_cm:.0f}", f"{t.tidal_wait_h:.1f}", f"{t.discharge_start:%d-%m-%Y %H:%M}",
                     "LATE" if m.number in t.late else "yes"])
    cols = list(map(list, zip(*rows)))
    late_color = ["#f8d0d0" if r[-1] == "LATE" else "white" for r in rows]
    fig = go.Figure(go.Table(header=dict(values=header, align="left", fill_color="#e6e6e6"),
                             cells=dict(values=cols, align="left", fill_color=[late_color] * len(header))))
    per_barge = " | ".join(f"{b.name}: {sum(t.barge == b.name for t in res.trips)} trips" for b in res.barges)
    _layout_title(fig, ["<b>Monopile schedule</b>", f"{_summary(res)}<br>{per_barge}"], 800)
    fig.update_layout(margin=dict(t=95, l=10, r=10))
    return fig


# ---------------------------------------------------------------- controller
class TransportPlan(vkt.Controller):
    label = "Transport plan"
    parametrization = Parametrization

    def update_tables(self, params, **kwargs):
        """Barge cycle and tide restrictions tables in line with the barge fleet (existing values kept)."""
        names = [(str(r.name or "").strip(), r.configuration or SINGLE) for r in (params.input.barges or [])]
        names = [(n, c) for n, c in names if n]
        cyc = {str(r.name or "").strip(): dict(r) for r in (params.input.cycle or [])}
        mat = {(str(r.name or "").strip(), r.transport): dict(r) for r in (params.input.tide_matrix or [])}
        cycle = [cyc.get(n) or dict(name=n, **DEFAULT_CYCLE) for n, _ in names]
        matrix = [mat.get((n, SINGLE)) or _matrix_row(n, SINGLE, DEFAULT_SINGLE) for n, _ in names]
        matrix += [mat.get((n, DOUBLE)) or _matrix_row(n, DOUBLE, DEFAULT_DOUBLE) for n, c in names if c == DOUBLE]
        return vkt.SetParamsResult({"input": {"cycle": cycle, "tide_matrix": matrix}})

    @vkt.PlotlyView("Planning", duration_guess=1)
    def planning_view(self, params, **kwargs):
        return vkt.PlotlyResult(build_gantt(_schedule(params.input)).to_json())

    @vkt.PlotlyView("Load-in", duration_guess=1)
    def loadin_view(self, params, **kwargs):
        return vkt.PlotlyResult(build_loadin(_schedule(params.input)).to_json())

    @vkt.PlotlyView("Table", duration_guess=1)
    def table_view(self, params, **kwargs):
        return vkt.PlotlyResult(build_table(_schedule(params.input)).to_json())

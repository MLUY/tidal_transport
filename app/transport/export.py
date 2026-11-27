"""
Exports of the transport planning: Excel workbooks, CSV and an interactive HTML report.
Pure Python (openpyxl / plotly, no VIKTOR imports) so it can be tested on its own.
"""
from __future__ import annotations

import csv
import io
from datetime import timedelta

import numpy as np
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .schedule import CATEGORIES, DOUBLE

HEADER_FILL = PatternFill("solid", fgColor="DDE7F0")
BOLD = Font(bold=True)
DT = "dd-mm-yyyy hh:mm"


def _sheet(wb, title, header, rows, widths=None, formats=None):
    ws = wb.create_sheet(title)
    ws.append(header)
    for c in ws[1]:
        c.font, c.fill = BOLD, HEADER_FILL
        c.alignment = Alignment(wrap_text=True, vertical="top")
    for r in rows:
        ws.append(list(r))
    for k, h in enumerate(header, start=1):
        ws.column_dimensions[get_column_letter(k)].width = (widths or {}).get(h, max(10, min(len(str(h)) + 2, 40)))
        fmt = (formats or {}).get(h)
        if fmt:
            for cell in ws.iter_cols(min_col=k, max_col=k, min_row=2):
                for c in cell:
                    c.number_format = fmt
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    return ws


def _hours(a, b) -> float:
    return round((b - a).total_seconds() / 3600, 2)


def cycle_rows(res) -> tuple[list, list]:
    """Per trip: cycle start / end, duration and the hours per activity."""
    header = ["Barge", "Trip", "Configuration", "Monopiles", "Classes", "Cycle start", "Back at fabricator",
              "Cycle (days)", "Cycle (h)"] + [f"{c} (h)" for c in CATEGORIES]
    rows = []
    for t in res.trips:
        label = f"{t.barge} trip {t.trip_no}"
        segs = sorted([s for s in res.segments if s["trip"] == label], key=lambda s: s["start"])
        if not segs:
            continue
        t0, t1 = segs[0]["start"], segs[-1]["finish"]
        per_cat = {c: 0.0 for c in CATEGORIES}
        for s in segs:
            per_cat[s["category"]] += _hours(s["start"], s["finish"])
        rows.append([t.barge, t.trip_no, t.mode, " + ".join(m.name for m in t.monopiles),
                     " + ".join(m.cargo_class for m in t.monopiles), t0, t1, round(_hours(t0, t1) / 24, 2),
                     _hours(t0, t1)] + [round(per_cat[c], 2) for c in CATEGORIES])
    return header, rows


def monopile_rows(res) -> tuple[list, list]:
    by_mp = {m.number: t for t in res.trips for m in t.monopiles}
    header = ["No.", "Location", "Weight (t)", "Class", "Earliest", "Latest", "Ready", "Produced",
              "Production delay (h)", "Barge", "Trip", "Configuration", "Load-out done", "Arrival marshalling port",
              "Load-in start", "Load-in end", "Required tide (cm)", "Wait before load-in (h)"]
    rows = []
    for m in res.monopiles:
        t = by_mp[m.number]
        rows.append([m.number, m.name, m.weight_t, m.cargo_class, m.earliest, m.latest, m.ready, m.produced,
                     round(m.production_delay_h, 2), t.barge, t.trip_no, t.mode, m.loaded_out, t.arrival,
                     m.loadin_start, m.loadin_start + timedelta(hours=res.cycle.loadin_h), m.loadin_cm,
                     round(m.loadin_wait_h, 2)])
    return header, rows


def activity_rows(res) -> tuple[list, list]:
    header = ["Barge", "Trip", "Activity", "Start", "End", "Duration (h)", "Detail"]
    rows = [[s["barge"], s["trip"], s["category"], s["start"], s["finish"], _hours(s["start"], s["finish"]),
             s["detail"]] for s in sorted(res.segments, key=lambda s: (s["barge"], s["start"]))]
    return header, rows


def loadin_rows(res) -> tuple[list, list]:
    """Per load-in: required level and the tide at start / minimum during the load-in (check)."""
    tide = res.tide
    header = ["Barge", "Trip", "Monopile", "Configuration", "Load-in start", "Load-in end", "Required tide (cm)",
              "Tide at start (cm)", "Lowest tide during load-in (cm)", "Highest tide during load-in (cm)",
              "Wait before load-in (h)"]
    rows = []
    for t in res.trips:
        for n, (m, lvl, w, s, e) in enumerate(t.loadins):
            hrs = np.arange(tide.to_hours(s), tide.to_hours(e) + 1e-9, 1 / 60)
            lv = tide.level_cm(hrs)
            conf = "Double" if t.mode == DOUBLE and n == 0 else "Single"
            rows.append([t.barge, t.trip_no, m.name, conf, s, e, lvl, round(float(lv[0]), 1),
                         round(float(lv.min()), 1), round(float(lv.max()), 1), round(w, 2)])
    return header, rows


def stock_rows(res) -> tuple[list, list]:
    st = res.stock()
    events = sorted({t for pts in st.values() for t, _ in pts})

    def level_at(pts, t):
        n = 0
        for tt, v in pts:
            if tt <= t:
                n = v
            else:
                break
        return n

    header = ["Time", "At the fabricator", "On the barges", "At the marshalling port"]
    rows = [[t, level_at(st["fabricator"], t), level_at(st["barges"], t), level_at(st["port"], t)] for t in events]
    return header, rows


def tide_rows(res) -> tuple[list, list]:
    """Hourly tide at the marshalling port over the campaign."""
    tide = res.tide
    t0 = res.start.replace(minute=0, second=0, microsecond=0)
    t1 = max(t.back_at_fab for t in res.trips)
    header = ["Time", "Tide level (cm)", "Rising"]
    rows = []
    t = t0
    while t <= t1:
        h = tide.to_hours(t)
        rows.append([t, round(float(tide.level_cm(h)), 1), "yes" if bool(tide.rising(np.array([h]))[0]) else "no"])
        t += timedelta(hours=1)
    return header, rows


def summary_rows(res, inputs: list) -> list:
    n_double = sum(t.mode == DOUBLE for t in res.trips)
    out = [("Results", ""),
           ("Monopiles", len(res.monopiles)), ("Trips", len(res.trips)), ("Double trips", n_double),
           ("First load-out", res.start), ("Last load-in", res.finish),
           ("Total tidal wait (h)", round(res.total_tidal_wait_h, 1)),
           ("Total berth wait (h)", round(res.total_berth_wait_h, 1)),
           ("Total weather downtime (h)", round(res.total_weather_h, 1)),
           ("Production delays (yard full)", res.production_delay_count), ("", ""), ("Inputs", "")]
    return out + list(inputs)


def build_planning_excel(res, inputs: list) -> bytes:
    """Complete planning: summary, barge cycles, monopiles, activities, load-ins, stock and tide."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    for r in summary_rows(res, inputs):
        ws.append(list(r))
        if r[0] in ("Results", "Inputs"):
            ws.cell(ws.max_row, 1).font = BOLD
    ws.column_dimensions["A"].width = 42
    ws.column_dimensions["B"].width = 60
    for row in ws.iter_rows(min_col=2, max_col=2):
        for c in row:
            if hasattr(c.value, "year"):
                c.number_format = DT
    dt_cols = {"Cycle start": DT, "Back at fabricator": DT}
    h, r = cycle_rows(res)
    _sheet(wb, "Barge cycles", h, r, formats=dt_cols, widths={"Monopiles": 24})
    h, r = monopile_rows(res)
    _sheet(wb, "Monopiles", h, r, formats={k: DT for k in ("Ready", "Produced", "Load-out done",
                                                           "Arrival marshalling port", "Load-in start", "Load-in end")}
           | {"Earliest": "dd-mm-yyyy", "Latest": "dd-mm-yyyy"})
    h, r = activity_rows(res)
    _sheet(wb, "Activities", h, r, formats={"Start": DT, "End": DT}, widths={"Detail": 70, "Activity": 34})
    h, r = loadin_rows(res)
    _sheet(wb, "Load-ins", h, r, formats={"Load-in start": DT, "Load-in end": DT})
    h, r = stock_rows(res)
    _sheet(wb, "Stock", h, r, formats={"Time": DT}, widths={"Time": 18})
    h, r = tide_rows(res)
    _sheet(wb, "Tide", h, r, formats={"Time": DT}, widths={"Time": 18})
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_cycles_excel(res) -> bytes:
    """Barge cycles only: one row per trip with the hours per activity, plus averages per barge."""
    wb = Workbook()
    wb.remove(wb.active)
    h, r = cycle_rows(res)
    _sheet(wb, "Barge cycles", h, r, formats={"Cycle start": DT, "Back at fabricator": DT}, widths={"Monopiles": 24})
    per = []
    for b in res.barges:
        rows = [x for x in r if x[0] == b.name]
        if rows:
            days = [x[7] for x in rows]
            per.append([b.name, b.configuration, len(rows), round(float(np.mean(days)), 2), min(days), max(days)])
    _sheet(wb, "Per barge", ["Barge", "Barge type", "Trips", "Average cycle (days)", "Shortest (days)",
                             "Longest (days)"], per)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_activities_csv(res) -> bytes:
    h, r = activity_rows(res)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(h)
    for row in r:
        w.writerow([x.strftime("%d-%m-%Y %H:%M") if hasattr(x, "strftime") else x for x in row])
    return buf.getvalue().encode("utf-8-sig")        # BOM: Excel opens the special characters correctly


def build_html_report(title: str, figures: list) -> bytes:
    """Interactive charts (zoom, hover) in one HTML file that opens in any browser."""
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>"
             "<style>body{font-family:Arial,sans-serif;margin:16px;color:#222}h1{font-size:20px}"
             ".fig{margin-bottom:28px;border-bottom:1px solid #ddd}</style></head><body>", f"<h1>{title}</h1>"]
    for k, fig in enumerate(figures):
        parts.append("<div class='fig'>" + fig.to_html(full_html=False, include_plotlyjs="cdn" if k == 0 else False)
                     + "</div>")
    parts.append("</body></html>")
    return "".join(parts).encode("utf-8")

"""
Tidal data: loading, yearly repetition and the search for a tidal window.
Taken from the Tide Planner app (same file format and behaviour). Pure Python (no VIKTOR imports).
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

TIME_STEP_H = 1 / 12     # 5 min search step
MAX_WAIT_H = 24 * 31     # give up after a month


class PlanningError(Exception):
    """Input problem the user can fix."""


class DataEnd(Exception):
    """Internal: schedule reached the end of the tidal data."""


@dataclass
class GateRule:
    rising_cm: float
    falling_cm: float
    transit_h: float = 0.0   # condition must hold from the gate moment until gate + transit_h


class TideCurve:
    def __init__(self, df: pd.DataFrame, data_start: datetime | None = None, data_end: datetime | None = None):
        self.df = df
        # period covered by the original data file (differs from t0/end once the data is repeated)
        self.data_start = data_start or df["time"].iloc[0].to_pydatetime()
        self.data_end = data_end or df["time"].iloc[-1].to_pydatetime()
        self.t0 = df["time"].iloc[0].to_pydatetime()
        self.hours = ((df["time"] - df["time"].iloc[0]) / pd.Timedelta(hours=1)).to_numpy(float)
        self.cm = df["cm"].to_numpy(float)
        self._slope = np.diff(self.cm) / np.diff(self.hours)

    @property
    def end(self) -> datetime:
        return self.t0 + timedelta(hours=float(self.hours[-1]))

    @property
    def repeated(self) -> bool:
        return self.t0 < self.data_start or self.end > self.data_end

    def is_measured(self, ts: datetime) -> bool:
        return self.data_start <= ts <= self.data_end

    def can_repeat(self) -> bool:
        """At least one full year of data is needed to repeat it for other years."""
        return self.data_end >= self.data_start + pd.DateOffset(years=1) - timedelta(hours=1, minutes=1)

    def repeated_to_cover(self, start: datetime, end: datetime) -> "TideCurve":
        """New curve covering [start, end] by repeating the first year of the data for every year.
        The copies keep the calendar date and time (29 Feb falls back to 28 Feb).
        Whole years are built and kept, so many plannings (start date sensitivity) reuse them."""
        y0 = self.data_start.year
        k_min = min(start.year - y0 - 1, 0)
        k_max = max(end.year - y0 + 1, 0)
        cache = self.__dict__.setdefault("_cover_cache", {})
        if (k_min, k_max) in cache:
            return cache[(k_min, k_max)]
        base = self.df[self.df["time"] < pd.Timestamp(self.data_start) + pd.DateOffset(years=1)]
        parts = []
        for k in range(k_min, k_max + 1):
            part = base.copy()
            if k:
                part["time"] = part["time"] + pd.DateOffset(years=k)
            parts.append(part)
        df = pd.concat(parts).drop_duplicates("time").sort_values("time").reset_index(drop=True)
        # a non-leap data year has no 29 Feb: fill it in leap years with the tide of 28 Feb
        fill = []
        for year in range(df["time"].iloc[0].year, df["time"].iloc[-1].year + 1):
            if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
                feb29 = pd.Timestamp(year, 2, 29)
                if not ((df["time"] >= feb29) & (df["time"] < feb29 + pd.Timedelta(days=1))).any():
                    day = df[(df["time"] >= feb29 - pd.Timedelta(days=1)) & (df["time"] < feb29)].copy()
                    day["time"] = day["time"] + pd.Timedelta(days=1)
                    fill.append(day)
        if fill:
            df = pd.concat([df, *fill]).drop_duplicates("time").sort_values("time").reset_index(drop=True)
        cache[(k_min, k_max)] = TideCurve(df, self.data_start, self.data_end)
        return cache[(k_min, k_max)]

    def to_hours(self, ts: datetime) -> float:
        return (ts - self.t0) / timedelta(hours=1)

    def hour_of_day(self, hours):
        """Hour of the day (0-24). Rounded to 1e-6 h so that a restriction boundary such as 13:30 is
        exactly 13.5 (and not 13.4999999 because of floating point), whatever the reference time t0."""
        t0 = self.t0.hour + self.t0.minute / 60 + self.t0.second / 3600
        return np.round(t0 + np.asarray(hours, float), 6) % 24

    def level_cm(self, hours):
        return np.interp(hours, self.hours, self.cm)

    def rising(self, hours):
        """True where the tide is rising (slope of the data segment at that time)."""
        idx = np.clip(np.searchsorted(self.hours, hours, side="right") - 1, 0, len(self._slope) - 1)
        return self._slope[idx] > 0

    def gate_open(self, hours, rule: GateRule):
        hours = np.asarray(hours, float)
        need = np.where(self.rising(hours), rule.rising_cm, rule.falling_cm)
        return self.level_cm(hours) >= need


_TIME_FORMATS = ("%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S",
                 "%d-%m-%Y %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M")


def _parse_times(raw: pd.Series) -> pd.Series:
    """Date-times of the tidal file. A fixed format is ~50x faster than guessing per row, so the common
    formats are tried first (dd/mm/yyyy with a slash, as before); otherwise pandas guesses per row."""
    for fmt in _TIME_FORMATS:
        parsed = pd.to_datetime(raw, format=fmt, errors="coerce")
        if parsed.notna().mean() > 0.99:
            return parsed
    return pd.to_datetime(raw, errors="coerce", format="mixed", dayfirst="/" in raw.iloc[0])


def load_tide(data: bytes) -> TideCurve:
    """csv: first column date-time, second column tide level in cm."""
    try:
        df = pd.read_csv(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise PlanningError(f"Tidal data file could not be read as csv: {exc}")
    if df.shape[1] < 2:
        raise PlanningError("Tidal data needs two columns: date-time and tide level (cm).")
    df = df.iloc[:, :2].copy()
    df.columns = ["time", "cm"]
    raw = df["time"].astype(str).str.strip()
    df["time"] = _parse_times(raw)
    df["cm"] = pd.to_numeric(df["cm"], errors="coerce")
    df = df.dropna().drop_duplicates("time").sort_values("time").reset_index(drop=True)
    if len(df) < 2:
        raise PlanningError("Tidal data has fewer than 2 valid rows (check date format and numbers).")
    return TideCurve(df)


# candidate waits 0, 5 min, 10 min, ... up to MAX_WAIT_H. cumsum adds the step one by one, exactly as the
# original loop (w += TIME_STEP_H) did, so the candidate moments are bit-for-bit the same.
_WAITS = np.concatenate([[0.0], np.cumsum(np.full(int(MAX_WAIT_H / TIME_STEP_H) + 1, TIME_STEP_H))])
_WAITS = _WAITS[_WAITS <= MAX_WAIT_H]
_BLOCK = 288    # candidates checked at once (one day of 5-minute steps)


def wait_for_gate(tide: TideCurve, t: datetime, rule: GateRule) -> float:
    """Smallest wait (h) from t until the tide is high enough for the whole duration (rule.transit_h).
    The 5-minute candidates are checked a day at a time (vectorised) instead of one by one."""
    h0 = tide.to_hours(t)
    if rule.transit_h > 0:
        offs = np.linspace(0.0, rule.transit_h, max(int(math.ceil(rule.transit_h / TIME_STEP_H)) + 1, 2))
    else:
        offs = np.array([0.0])
    last = tide.hours[-1]
    for b in range(0, len(_WAITS), _BLOCK):
        ws = _WAITS[b:b + _BLOCK]
        beyond = h0 + ws + offs[-1] > last                  # past the end of the tidal data
        hrs = h0 + ws[:, None] + offs[None, :]
        ok = tide.gate_open(hrs, rule).all(axis=1)
        hit = np.flatnonzero(beyond | ok)
        if hit.size:
            k = hit[0]
            if beyond[k]:
                raise DataEnd()
            return float(ws[k])
    raise PlanningError("No tidal window found within a month. Check the required tide levels "
                        "and the tidal data.")

"""
Wave data (Hs time series per location) and weather window analysis.

* Files: csv with columns Year, Month, Day, Hour, Hs (second row = units), named after the position, e.g.
  125.667E_34.500N.csv or 126.000E,36.000N.csv. They are looked up in the repository root and in
  sample_data/waves.
* Weather window: a period in which Hs stays below the limit for at least the window length. Availability of a
  window = percentage of time at which such a window can start (persistence).
* Combined: all selected locations at the same moments (common time steps), the highest Hs of the locations
  counts (the route is only workable when every location is).
Pure Python (no VIKTOR imports) so it can be tested on its own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
FOLDERS = (ROOT, ROOT / "sample_data" / "waves")
_NAME = re.compile(r"(\d+(?:\.\d+)?)E[_,\s]+(\d+(?:\.\d+)?)N", re.IGNORECASE)
_CACHE: dict = {}


@dataclass
class WaveSeries:
    label: str            # file name without .csv
    lon: float
    lat: float
    time: np.ndarray      # datetime64[ns]
    hs: np.ndarray        # m
    step_h: float

    @property
    def period(self) -> str:
        t = pd.to_datetime(self.time)
        return f"{t[0]:%Y}-{t[-1]:%Y}"


def wave_files() -> list[Path]:
    out = []
    for folder in FOLDERS:
        if folder.is_dir():
            out += [p for p in sorted(folder.glob("*.csv")) if _NAME.search(p.stem)]
    return out


def position(path: Path) -> tuple[float, float]:
    """(lon, lat) from the file name."""
    m = _NAME.search(path.stem)
    return float(m.group(1)), float(m.group(2))


def load(path: Path) -> WaveSeries:
    key = (str(path), path.stat().st_mtime)
    if key not in _CACHE:
        df = pd.read_csv(path, skiprows=[1])
        df.columns = [c.strip() for c in df.columns]
        t = pd.to_datetime(df[["Year", "Month", "Day", "Hour"]].astype(int))
        hs = pd.to_numeric(df["Hs"], errors="coerce").to_numpy(float)
        step = float(pd.Series(t).diff().dropna().dt.total_seconds().mode()[0] / 3600)
        lon, lat = position(path)
        _CACHE[key] = WaveSeries(path.stem, lon, lat, t.to_numpy(), hs, step)
    return _CACHE[key]


def all_series() -> list[WaveSeries]:
    return [load(p) for p in wave_files()]


def combined(series: list[WaveSeries]) -> WaveSeries:
    """Highest Hs of all locations at the common time steps (coarsest step of the files)."""
    step = max(s.step_h for s in series)
    frames = []
    for s in series:
        sr = pd.Series(s.hs, index=pd.DatetimeIndex(s.time))
        hours = sr.index.hour + sr.index.minute / 60
        frames.append(sr[(hours % step) == 0])
    df = pd.concat(frames, axis=1, join="inner")
    return WaveSeries("Combined (" + ", ".join(s.label for s in series) + ")", float("nan"), float("nan"),
                      df.index.to_numpy(), df.max(axis=1, skipna=False).to_numpy(float), step)


def window_availability(hs: np.ndarray, step_h: float, limit: float, windows_h) -> np.ndarray:
    """Percentage of time steps at which a window of each length (h) with Hs < limit can start.
    Missing values (NaN) break a window and are left out of the total."""
    calm = np.nan_to_num(hs, nan=np.inf) < limit
    valid = ~np.isnan(hs)
    edges = np.flatnonzero(np.diff(np.concatenate([[0], calm.astype(int), [0]])))
    runs = edges[1::2] - edges[::2]                          # lengths of calm periods (steps)
    total = max(int(valid.sum()), 1)
    out = []
    for w in windows_h:
        need = int(np.ceil(w / step_h))                      # steps the window covers
        out.append(100.0 * np.clip(runs - need + 1, 0, None).sum() / total)
    return np.array(out)


def monthly_availability(series: WaveSeries, limit: float, windows_h) -> np.ndarray:
    """12 x len(windows) availability (%) per calendar month (window start in that month)."""
    months = pd.DatetimeIndex(series.time).month.to_numpy()
    calm = np.nan_to_num(series.hs, nan=np.inf) < limit
    # remaining calm hours from every step
    remaining = np.zeros(len(calm))
    run = 0
    for k in range(len(calm) - 1, -1, -1):
        run = run + 1 if calm[k] else 0
        remaining[k] = run * series.step_h
    valid = ~np.isnan(series.hs)
    out = np.zeros((12, len(windows_h)))
    for m in range(1, 13):
        sel = (months == m) & valid
        n = max(int(sel.sum()), 1)
        for j, w in enumerate(windows_h):
            out[m - 1, j] = 100.0 * (remaining[sel] >= w - 1e-9).sum() / n
    return out

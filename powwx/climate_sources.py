"""Small long-record sources for the "long view": NOAA RONI and ECCC AHCCD.

- **RONI** (Relative Oceanic Niño Index), NOAA Climate Prediction Center: the
  official El Niño / La Niña index since 1 Feb 2026 (it replaced ONI). Three-
  month running seasons (DJF, JFM, ...) from 1950. The last ~2 values get revised.
- **AHCCD** (Adjusted and Homogenized Canadian Climate Data), Environment and
  Climate Change Canada, Open Government Licence - Canada: monthly mean
  temperature for "Terrace" (1068134, 217 m, ~25 km from Shames), joined across
  station moves and adjusted for them, 1912-2020. The only *measured*,
  homogenized long temperature record near the hill.

Both are tiny, so the weekly reanalysis job refreshes them, and a new copy is
written only when the content changed (latest file wins on read).

    data/reanalysis/roni/roni__<UTC-timestamp>.csv
    data/reanalysis/ahccd/<station>__<UTC-timestamp>.csv
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import requests

RONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/RONI.ascii.txt"
AHCCD_URL = "https://api.weather.gc.ca/collections/ahccd-monthly/items"
AHCCD_TERRACE = "1068134"
AHCCD_MISSING = -9999.9
REQUEST_TIMEOUT = 60


def parse_roni(text: str):
    """CPC's whitespace table (SEAS YR ANOM) -> DataFrame[season, year, anom]."""
    import pandas as pd

    df = pd.read_csv(io.StringIO(text), sep=r"\s+")
    df = df.rename(columns={"SEAS": "season", "YR": "year", "ANOM": "anom"})
    return df[["season", "year", "anom"]].astype({"year": int, "anom": float})


def fetch_roni():
    resp = requests.get(RONI_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return parse_roni(resp.text)


def parse_ahccd(payload: dict):
    """GeoMet AHCCD monthly items -> DataFrame[date (YYYY-MM), temp_mean];
    the -9999.9 missing code is dropped."""
    import pandas as pd

    rows = [(f["properties"]["date"], f["properties"].get("temp_mean__temp_moyenne"))
            for f in payload.get("features", [])]
    df = pd.DataFrame(rows, columns=["date", "temp_mean"]).dropna()
    df = df[df["temp_mean"] > AHCCD_MISSING + 1]
    return df.sort_values("date").reset_index(drop=True)


def fetch_ahccd(station: str = AHCCD_TERRACE):
    resp = requests.get(AHCCD_URL, params={
        "f": "json", "limit": 5000, "station_id__id_station": station,
        "properties": "date,temp_mean__temp_moyenne"}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return parse_ahccd(resp.json())


def save_if_changed(df, *, data_dir: Path, name: str, prefix: str, fetched_at: datetime) -> Path | None:
    """Write ``df`` as a new CSV unless it equals the latest one already saved."""
    out_dir = data_dir / "reanalysis" / name
    text = df.to_csv(index=False, lineterminator="\n")
    latest = _latest(out_dir, prefix)
    if latest is not None and latest.read_text(encoding="utf-8") == text:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{prefix}__{fetched_at.strftime('%Y%m%dT%H%M%SZ')}.csv"
    out.write_text(text, encoding="utf-8", newline="\n")
    return out


def _latest(out_dir: Path, prefix: str) -> Path | None:
    files = sorted(out_dir.glob(f"{prefix}__*.csv"))
    return files[-1] if files else None


def load_latest(data_dir: Path, *, name: str, prefix: str):
    """The latest saved copy as a DataFrame, or None if there is none."""
    import pandas as pd

    path = _latest(data_dir / "reanalysis" / name, prefix)
    return pd.read_csv(path) if path is not None else None

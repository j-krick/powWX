"""Observation sources (the "actuals") for the viewer and Phase 2 verification.

Currently wired:
- **Station #58** (bottom, 740 m) via Avalanche Canada's public weather API.

Deferred (Phase 1b), stub included:
- **POW-O-METER** (top, 1150 m) via a published Google Sheet CSV.

Observations are normalised to the same variable names and units as the logged
Open-Meteo forecasts so the viewer can overlay them directly:
  temperature_2m (degC), precipitation (mm), snow_depth (m), snowfall (cm),
  wind_speed_10m (km/h), wind_direction_10m (deg).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

STATION_58_URL = "https://weather.prod.avalanche.ca/stations/58/measurements"
REQUEST_TIMEOUT = 30

# Avalanche Canada field -> (powWX variable, multiplier to reach forecast units).
# AvCan: temp degC, precip mm, snowHeight cm, newSnow cm, wind km/h, dir deg.
# Open-Meteo snow_depth is metres, so snowHeight cm is scaled by 0.01.
AVCAN_FIELD_MAP = {
    "airTempAvg": ("temperature_2m", 1.0),
    "hourlyPrecip": ("precipitation", 1.0),
    "snowHeight": ("snow_depth", 0.01),
    "newSnow": ("snowfall", 1.0),
    "windSpeedAvg": ("wind_speed_10m", 1.0),
    "windDirAvg": ("wind_direction_10m", 1.0),
}


def fetch_station_58(*, days: int = 7) -> list[dict]:
    """Fetch the last ``days`` of station #58 observations, normalised to long
    records ``{location, time, variable, value}`` (time is UTC ISO ...Z)."""
    from_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    resp = requests.get(
        STATION_58_URL,
        params={"fromDate": from_date},
        headers={"Accept": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    rows = resp.json()

    records: list[dict] = []
    for row in rows:
        ts = row.get("measurementDateTime")
        if not ts:
            continue
        for field, (variable, mult) in AVCAN_FIELD_MAP.items():
            value = row.get(field)
            if value is None:
                continue
            value = float(value) * mult
            # hourlyPrecip carries small negative sensor noise; clamp for display.
            if variable in ("precipitation", "snowfall") and value < 0:
                value = 0.0
            records.append(
                {
                    "location": "station_58",
                    "time": ts,
                    "variable": variable,
                    "value": round(value, 3),
                }
            )
    return records


def fetch_pow_o_meter(*, csv_url: str, column_map: dict) -> list[dict]:
    """Phase 1b: parse the POW-O-METER published-CSV into the same long schema.

    ``column_map`` maps CSV column names to (powWX variable, multiplier), plus a
    special ``"time"`` key giving the timestamp column. Not called until the
    published CSV URL + columns are configured.
    """
    import csv
    import io

    resp = requests.get(csv_url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    time_col = column_map["time"]

    records: list[dict] = []
    for row in reader:
        ts = row.get(time_col)
        if not ts:
            continue
        for col, spec in column_map.items():
            if col == "time":
                continue
            variable, mult = spec
            raw = row.get(col)
            if raw in (None, ""):
                continue
            try:
                value = float(raw) * mult
            except ValueError:
                continue
            records.append(
                {"location": "pow_o_meter", "time": ts, "variable": variable,
                 "value": round(value, 3)}
            )
    return records

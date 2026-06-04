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

# POW-O-METER (top, 1150 m): published Google-Sheet CSV. Timestamps are naive PST
# (UTC-8); snow depth is already in metres, temp in degC, humidity in %, so no
# unit scaling. Transmissions are suspended over summer, so the series ends in
# late May. This is the single source of truth for the POW-O-METER source config;
# both the viewer builder and the verification builder read it from here.
POW_O_METER_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vSt3wzU7iZgZDIyAEpbCJRxp91mo6"
    "yzA36v_beMGgBiuqRi9JJf2ySmtE5naWVA_UKKwpWZq0s0oN3y/pub"
    "?gid=906900269&single=true&output=csv"
)
POW_O_METER_TZ_OFFSET_HOURS = -8  # PST
POW_O_METER_COLUMN_MAP = {
    "time": "Measurement Time (PST)",
    "Air Temperature (°C)": ("temperature_2m", 1.0),
    "Humidity (%)": ("relative_humidity_2m", 1.0),
    "Snow Depth (m)": ("snow_depth", 1.0),
}

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


def fetch_pow_o_meter(
    *, csv_url: str, column_map: dict, source_utc_offset_hours: float = 0.0
) -> list[dict]:
    """Phase 1b: parse the POW-O-METER published-CSV into the same long schema.

    ``column_map`` maps CSV column names to (powWX variable, multiplier), plus a
    special ``"time"`` key giving the timestamp column.

    The sheet's timestamps are naive local time; ``source_utc_offset_hours`` is
    that local zone's offset from UTC (e.g. ``-8`` for PST), used to normalise the
    ``time`` field to a UTC ISO ``...Z`` string — the same form ``fetch_station_58``
    returns — so downstream alignment treats both stations identically.
    """
    import csv
    import io

    resp = requests.get(csv_url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    # Force UTF-8: requests guesses ISO-8859-1 for Google's CSV, which mangles
    # non-ASCII headers like "Air Temperature (°C)" so they fail to match the map.
    text = resp.content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    time_col = column_map["time"]
    offset = timedelta(hours=source_utc_offset_hours)

    records: list[dict] = []
    for row in reader:
        ts = row.get(time_col)
        if not ts:
            continue
        try:
            local = datetime.strptime(ts.strip(), "%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError):
            continue
        utc = (local - offset).strftime("%Y-%m-%dT%H:%M:%SZ")
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
                {"location": "pow_o_meter", "time": utc, "variable": variable,
                 "value": round(value, 3)}
            )
    return records


def fetch_pow_o_meter_default() -> list[dict]:
    """Fetch the full POW-O-METER history using the module's source config."""
    return fetch_pow_o_meter(
        csv_url=POW_O_METER_CSV_URL,
        column_map=POW_O_METER_COLUMN_MAP,
        source_utc_offset_hours=POW_O_METER_TZ_OFFSET_HOURS,
    )


# --------------------------------------------------------------------------- #
# PCIC / PCDS historical import (station #58 = MoTIe native id 52401)
# --------------------------------------------------------------------------- #
# The Avalanche Canada API only retains ~7 days, but the Pacific Climate Impacts
# Consortium's PCDS portal serves this same physical station's full history as a
# CSV download. That backfills the bottom station so the 2024->now forecast
# backfill becomes verifiable there too.
#
# PCDS CSVs are: line 1 banner, line 2 header, "None" as the null token, and
# **naive timestamps in America/Vancouver local clock time WITH daylight saving**
# (verified to the second against the AvCan feed: a fixed -8 offset would be 1 h
# wrong all summer). Each powWX variable maps to one or more PCDS native columns;
# the first non-null candidate per row wins (stations populate different fields).
PCDS_TZ = "America/Vancouver"
PCDS_FIELD_MAP = {
    # variable: (candidate PCDS columns in priority order, multiplier to powWX units)
    "temperature_2m": (["air_temp", "CURRENT_AIR_TEMPERATURE1", "CURRENT_AIR_TEMPERATURE2"], 1.0),
    "relative_humidity_2m": (["rel_hum", "RELATIVE_HUMIDITY1"], 1.0),
    "snow_depth": (["snw_dpth", "HEIGHT_OF_SNOW"], 0.01),          # cm -> m
    "snowfall": (["snwfl_amt_pst1hr", "STANDARD_SNOW"], 1.0),       # cm
    "precipitation": (["pcpn_amt_pst1hr", "PRECIPITATION_NEW"], 1.0),  # mm (1 h)
    "wind_speed_10m": (["avg_wnd_spd_10m_pst10mts", "MEASURED_WIND_SPEED1", "ACTUAL_WIND_SPEED"], 1.0),
    "wind_gusts_10m": (["max_wnd_spd_10m_pst1hr", "MAXIMUM_MEASURED_WIND_SPEED1"], 1.0),
    "wind_direction_10m": (["avg_wnd_dir_10m_pst10mts", "MEASURED_WIND_DIRECTION1", "ACTUAL_WIND_DIRECTION"], 1.0),
}


def parse_pcds_csv(path, *, location_id: str = "station_58") -> list[dict]:
    """Parse one PCDS station CSV into long records ``{location, time, variable,
    value, source}`` with UTC ISO ``...Z`` times. ``source`` is ``"pcds"``."""
    import pandas as pd

    df = pd.read_csv(path, skiprows=1, skipinitialspace=True, na_values=["None"])
    df.columns = [c.strip() for c in df.columns]
    if "time" not in df.columns:
        raise ValueError(f"{path}: no 'time' column after header parse")

    naive = pd.to_datetime(df["time"], errors="coerce")
    # Localise local-clock time (DST-aware) to UTC. DST-gap/overlap hours are rare
    # and unrecoverable, so drop them rather than guess.
    utc = naive.dt.tz_localize(PCDS_TZ, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")

    records: list[dict] = []
    for variable, (candidates, mult) in PCDS_FIELD_MAP.items():
        present = [c for c in candidates if c in df.columns]
        if not present:
            continue
        # Coalesce candidate columns: first non-null per row wins.
        series = df[present[0]]
        for c in present[1:]:
            series = series.combine_first(df[c])
        for t, raw in zip(utc, series):
            if pd.isna(t) or pd.isna(raw):
                continue
            value = float(raw) * mult
            if variable in ("precipitation", "snowfall") and value < 0:
                value = 0.0  # sensor noise; negative accumulation is non-physical
            records.append(
                {
                    "location": location_id,
                    "time": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "variable": variable,
                    "value": round(value, 3),
                    "source": "pcds",
                }
            )
    return records

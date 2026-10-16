"""Observation sources (the "actuals") for the viewer and Phase 2 verification.

- **Station #58** (bottom, 740 m) via Avalanche Canada's public weather API.
- **POW-O-METER** (top, 1150 m) via powOmeter's published data contract v1
  (``best_hourly.csv``, Mount Remo Backcountry Society, CC BY 4.0).

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

# POW-O-METER (top, 1150 m): powOmeter's published contract v1. best_hourly.csv is
# on the hour in true UTC (times before 2026-09-22 already corrected), interpolated
# by powOmeter, with readings rated "poor" removed. One row per (site_id, variable,
# statistic, hour); the station moved from site shames_top_a to shames_top_b on
# 2026-09-26. powOmeter's CHANGELOG.md announces any change to the contract.
POWOMETER_V1_URL = "https://mount-remo-backcountry-society.github.io/powOmeter/v1/"
# (variable, statistic) -> (powWX variable, multiplier to reach forecast units).
POWOMETER_V1_MAP = {
    ("air_temperature", "point"): ("temperature_2m", 1.0),
    ("relative_humidity", "point"): ("relative_humidity_2m", 1.0),
    ("snow_depth", "median"): ("snow_depth", 0.01),   # cm -> m
}
# Rows rated "suspect" are left out; "good" and "estimate" are kept.
POWOMETER_V1_DROP_QUALITY = {"suspect"}

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


def fetch_powometer_v1(*, base_url: str = POWOMETER_V1_URL) -> list[dict]:
    """Fetch POW-O-METER's full hourly history from powOmeter v1, normalised to
    long records ``{location, time, variable, value, site_id}`` (time is UTC ISO
    ``...Z``, location ``pow_o_meter``).

    ``site_id`` is kept because snow depth is not comparable across the station
    move; temperature and humidity can be pooled across sites.
    """
    import io

    import pandas as pd

    resp = requests.get(base_url + "best_hourly.csv", timeout=REQUEST_TIMEOUT * 4)
    resp.raise_for_status()
    df = pd.read_csv(io.BytesIO(resp.content),
                     usecols=["time_utc", "site_id", "variable", "statistic", "value", "quality"])
    df = df[~df["quality"].isin(POWOMETER_V1_DROP_QUALITY)].dropna(subset=["value"])

    records: list[dict] = []
    for (variable, statistic), (powwx_var, mult) in POWOMETER_V1_MAP.items():
        sub = df[(df["variable"] == variable) & (df["statistic"] == statistic)]
        for t, site, v in zip(sub["time_utc"], sub["site_id"], sub["value"]):
            records.append({"location": "pow_o_meter", "time": t, "variable": powwx_var,
                            "value": round(float(v) * mult, 3), "site_id": site})
    records.sort(key=lambda r: (r["variable"], r["time"]))
    return records


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

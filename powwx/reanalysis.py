"""ERA5-Land / ERA5 reanalysis for the stations, from Open-Meteo's archive API.

Reanalysis is *modelled* weather reconstructed from observations, available back
to 1950 (ERA5-Land, ~10 km) or 1940 (ERA5, ~25 km) with a ~5-day delay. powWX
uses it as the long climatology behind the "Season in context" section, because
the stations' own records are short (#58 since 2010, POW-O-METER since 2025).

Rules learned from checking it against the stations (2026-10-16):
  - Always name the model. ``best_match`` switches to ECMWF IFS from 2017, a
    jump that would fake a trend, so it is refused here.
  - Always pass the station ``elevation``: temperature is then lapse-rate
    corrected to the station height instead of the grid cell's mean height.
  - ERA5-Land snow depth is one ~10 km cell (the same for both stations, not
    height-corrected) and runs about 2.4x deeper than #58 measures. Use it only
    in relative terms (percentiles, % of its own median), never in metres.
  - ERA5 (not -Land) has no snow depth on Open-Meteo.

Layout (committed, append-only like the other logs; the latest fetch wins)::

    data/reanalysis/<model>/<location>/<first>_<last>__<UTC-timestamp>.parquet

Columns: location, date (BC local date, ISO), variable, value, model, fetched_at.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from . import openmeteo as om

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
TIMEZONE = "America/Vancouver"   # daily values are BC local days
# Model -> (first available date, daily variables fetched).
MODELS = {
    "era5_land": ("1950-01-01", ["temperature_2m_mean", "temperature_2m_min",
                                 "temperature_2m_max", "snow_depth_mean"]),
    "era5": ("1940-01-01", ["temperature_2m_mean", "temperature_2m_min",
                            "temperature_2m_max"]),
}
COLUMNS = ["location", "date", "variable", "value", "model", "fetched_at"]


def request_params(*, latitude: float, longitude: float, elevation: float, model: str,
                   start: str, end: str) -> dict:
    """Query parameters for one daily archive request. Refuses ``best_match`` and
    unknown models (see the module docstring)."""
    if model not in MODELS:
        raise ValueError(f"unsupported reanalysis model {model!r}; use one of {sorted(MODELS)}")
    return {
        "latitude": latitude,
        "longitude": longitude,
        "elevation": elevation,
        "start_date": start,
        "end_date": end,
        "daily": ",".join(MODELS[model][1]),
        "models": model,
        "timezone": TIMEZONE,
    }


def parse_daily(payload: dict, *, location: str, model: str, fetched_at: str) -> list[dict]:
    """Archive JSON -> long records; nulls (days not yet available) are dropped."""
    daily = payload.get("daily") or {}
    times = daily.get("time") or []
    records = []
    for variable in MODELS[model][1]:
        for t, v in zip(times, daily.get(variable) or []):
            if v is None:
                continue
            records.append({"location": location, "date": t, "variable": variable,
                            "value": float(v), "model": model, "fetched_at": fetched_at})
    return records


def fetch_daily(loc: dict, *, model: str, start: str, end: str) -> list[dict]:
    """Fetch one location's daily reanalysis for ``start``..``end`` (ISO dates)."""
    params = request_params(latitude=loc["latitude"], longitude=loc["longitude"],
                            elevation=loc["elevation_m"], model=model, start=start, end=end)
    payload = om.get_with_retry(ARCHIVE_URL, params, timeout=300)
    return parse_daily(payload, location=loc["id"], model=model,
                       fetched_at=om._iso(om.utcnow()))


def write_fetch(records: list[dict], *, data_dir: Path, fetched_at: datetime) -> Path | None:
    """Write one fetch to a new Parquet file (never overwrites)."""
    if not records:
        return None
    import pandas as pd

    df = pd.DataFrame.from_records(records)[COLUMNS]
    loc, model = df["location"].iloc[0], df["model"].iloc[0]
    out_dir = data_dir / "reanalysis" / model / loc
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = fetched_at.strftime("%Y%m%dT%H%M%SZ")
    out = out_dir / f"{df['date'].min()}_{df['date'].max()}__{stamp}.parquet"
    df.to_parquet(out, engine="pyarrow", index=False, compression="zstd")
    return out


def load_reanalysis(data_dir: Path, *, location: str, model: str = "era5_land"):
    """All cached daily values for one location+model as a wide DataFrame indexed
    by date (naive Timestamp), one column per variable. Where fetches overlap,
    the latest fetch wins (the last ~3 months are preliminary and get revised).
    Returns an empty DataFrame when nothing is cached."""
    import pandas as pd

    files = sorted((data_dir / "reanalysis" / model / location).glob("*.parquet"))
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df = (df.sort_values("fetched_at", kind="stable")
            .drop_duplicates(["date", "variable"], keep="last"))
    wide = df.pivot(index="date", columns="variable", values="value")
    wide.index = pd.to_datetime(wide.index)
    wide.columns.name = None
    return wide.sort_index()


def chunks(start: str, end: str, years: int) -> list[tuple[str, str]]:
    """Split ``start``..``end`` into consecutive spans of at most ``years`` years,
    so a long backfill is several modest requests (Open-Meteo weighs requests by
    length) and a failure loses one chunk, not everything."""
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    while a <= b:
        nxt = date(a.year + years, 1, 1)
        stop = min(b, date.fromordinal(nxt.toordinal() - 1))
        out.append((a.isoformat(), stop.isoformat()))
        a = nxt
    return out

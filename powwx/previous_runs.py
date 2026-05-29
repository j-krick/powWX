"""Backfill verification history from Open-Meteo's Previous Runs API.

The Previous Runs endpoint (a *separate* host) serves, for each valid time, the
value that was predicted N days earlier via the ``_previous_dayN`` suffix
(N = 1..7). That gives exact integer-day lead offsets — the cleanest possible
verification substrate — without waiting months for the live logger to fill in.

Coverage (per Open-Meteo docs): GFS 2m-temperature back to Mar 2021; all other
models back to ~Jan 2024. Short-range models (HRDPS/RDPS) only populate the
offsets within their horizon.

We query ONE model at a time: a single-model response uses unsuffixed keys
(``temperature_2m``, ``temperature_2m_previous_day1``, ...), which keeps parsing
unambiguous.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from dateutil import parser as dtparser

from . import config as cfg
from . import openmeteo as om

DEFAULT_OFFSETS = range(1, 8)  # _previous_day1 .. _previous_day7


def _previous_run_variables(variables: list[str], offsets) -> list[str]:
    """Expand base variables into the _previous_dayN request fields."""
    fields = list(variables)  # keep the "now" series too (cheap; we just skip it)
    for var in variables:
        for n in offsets:
            fields.append(f"{var}_previous_day{n}")
    return fields


def backfill(
    *,
    data_dir: Path | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    past_days: int = 7,
    forecast_days: int = 2,
    offsets=DEFAULT_OFFSETS,
) -> dict:
    """Pull previous-run history for every location/model into the forecast log.

    Two modes:
    - ``start_date``/``end_date`` ("YYYY-MM-DD"): an explicit historical window
      (the way to walk back to the archive start, ~Jan 2024).
    - otherwise ``past_days``/``forecast_days`` relative to now (default).

    Writes one Parquet file tagged by the window so repeated chunked calls don't
    collide. Returns a summary dict.
    """
    locations = cfg.load_locations()
    models_cfg = cfg.load_models()
    api = models_cfg["api"]
    variables = models_cfg["variables"]
    ids = cfg.model_ids(models_cfg)
    data_dir = data_dir or cfg.DATA_DIR

    use_range = start_date is not None and end_date is not None
    if use_range:
        extra = {"start_date": start_date, "end_date": end_date}
        fdays = None
        tag = f"backfill_{start_date}_{end_date}"
    else:
        extra = {"past_days": past_days}
        fdays = forecast_days
        tag = "backfill"

    fetched_at = om.utcnow()
    request_fields = _previous_run_variables(variables, offsets)

    all_records: list[dict] = []
    summary: dict[str, int] = {}
    for loc in locations:
        for mid in ids:
            raw = om.fetch_forecast(
                latitude=loc["latitude"],
                longitude=loc["longitude"],
                models=[mid],
                variables=request_fields,
                forecast_url=api["previous_runs_url"],
                forecast_days=fdays,
                timezone_name=api.get("timezone", "GMT"),
                extra_params=extra,
            )
            recs = _parse_previous_runs(
                raw,
                variables=variables,
                offsets=offsets,
                location_id=loc["id"],
                model=mid,
                fetched_at=fetched_at,
            )
            all_records.extend(recs)
            summary[f"{loc['id']}/{mid}"] = len(recs)

    from .storage import write_forecast_run

    path = write_forecast_run(
        all_records, data_dir=data_dir, fetched_at=fetched_at, tag=tag
    )
    return {
        "fetched_at": om._iso(fetched_at),
        "window": f"{start_date}..{end_date}" if use_range else f"past_days={past_days}",
        "n_records": len(all_records),
        "per_series": summary,
        "path": str(path) if path else None,
    }


def _parse_previous_runs(
    raw: dict,
    *,
    variables: list[str],
    offsets,
    location_id: str,
    model: str,
    fetched_at,
) -> list[dict]:
    """For each ``<var>_previous_dayN`` series, emit records with
    ``issued_at = valid_time - N days`` (the true lead-time offset)."""
    hourly = raw.get("hourly", {})
    times = hourly.get("time", [])
    fetched_iso = om._iso(fetched_at)
    var_set = set(variables)

    records: list[dict] = []
    for n in offsets:
        for var in variables:
            key = f"{var}_previous_day{n}"
            series = hourly.get(key)
            if series is None:
                continue
            for valid_time, value in zip(times, series):
                if value is None:
                    continue
                issued = dtparser.parse(valid_time) - timedelta(days=int(n))
                records.append(
                    {
                        "location": location_id,
                        "model": model,
                        "issued_at": issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "valid_time": valid_time,
                        "variable": var,
                        "value": float(value),
                        "source": "previous_runs",
                        "fetched_at": fetched_iso,
                    }
                )
    return records

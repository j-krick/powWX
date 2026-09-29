"""Append-only forecast logger: fetch all models for every location, write one
Parquet file for the run.

A live snapshot can't be re-fetched later, so the logger is deliberately
stubborn: each request gets a longer retry budget than the shared client
default, a location that still fails is retried in a second pass after a
cool-down, and whatever locations succeeded are written even if one didn't.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from . import config as cfg
from . import openmeteo as om

# Backoff 3, 6, 12, 24, 48, 96 s -> ~3 min of retrying per request (the client
# default of 4 retries gives up after ~45 s, which lost runs to Open-Meteo
# 429/503 bursts on shared GitHub runner IPs).
LOGGER_MAX_RETRIES = 6
# Cool-down before re-trying locations that failed the first pass.
SECOND_PASS_DELAY = 120  # seconds


def log_forecasts(*, data_dir: Path | None = None) -> dict:
    """Run the logger once. Returns a summary dict."""
    locations = cfg.load_locations()
    models_cfg = cfg.load_models()
    api = models_cfg["api"]
    variables = models_cfg["variables"]
    ids = cfg.model_ids(models_cfg)
    data_dir = data_dir or cfg.DATA_DIR

    fetched_at = om.utcnow()
    # The forecast endpoint exposes no per-model init time, so the request time
    # is our issued_at reference for the whole run (see openmeteo module docstring).
    issued_at = fetched_at

    all_records: list[dict] = []
    per_location: dict[str, int] = {}
    errors: dict[str, str] = {}

    pending = list(locations)
    for pass_no in (1, 2):
        if pass_no == 2:
            if not pending:
                break
            time.sleep(SECOND_PASS_DELAY)
        still_failing = []
        for loc in pending:
            try:
                raw = om.fetch_forecast(
                    latitude=loc["latitude"],
                    longitude=loc["longitude"],
                    models=ids,
                    variables=variables,
                    forecast_url=api["forecast_url"],
                    forecast_days=api.get("forecast_days", 16),
                    timezone_name=api.get("timezone", "GMT"),
                    max_retries=LOGGER_MAX_RETRIES,
                )
            except Exception as exc:  # noqa: BLE001 - keep the other locations
                errors[loc["id"]] = f"{type(exc).__name__}: {exc}"
                still_failing.append(loc)
                continue
            recs = om.parse_long(
                raw,
                model_ids=ids,
                variables=variables,
                location_id=loc["id"],
                issued_at=issued_at,
                fetched_at=fetched_at,
                source="live",
            )
            all_records.extend(recs)
            per_location[loc["id"]] = len(recs)
            errors.pop(loc["id"], None)
        pending = still_failing

    from .storage import write_forecast_run

    path = write_forecast_run(all_records, data_dir=data_dir, fetched_at=fetched_at)
    return {
        "fetched_at": om._iso(fetched_at),
        "n_records": len(all_records),
        "per_location": per_location,
        "failed_locations": errors,
        "path": str(path) if path else None,
    }

"""Append-only forecast logger: fetch all models for every location, write one
Parquet file for the run.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import config as cfg
from . import openmeteo as om


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
    for loc in locations:
        raw = om.fetch_forecast(
            latitude=loc["latitude"],
            longitude=loc["longitude"],
            models=ids,
            variables=variables,
            forecast_url=api["forecast_url"],
            forecast_days=api.get("forecast_days", 16),
            timezone_name=api.get("timezone", "GMT"),
        )
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

    from .storage import write_forecast_run

    path = write_forecast_run(all_records, data_dir=data_dir, fetched_at=fetched_at)
    return {
        "fetched_at": om._iso(fetched_at),
        "n_records": len(all_records),
        "per_location": per_location,
        "path": str(path) if path else None,
    }

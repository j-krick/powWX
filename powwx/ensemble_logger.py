"""Append-only ensemble logger: fetch each configured ensemble (GEPS for now) for
every location and write one Parquet file per run under ``data/ensembles/``.

Logged now because a live ensemble snapshot can't be re-fetched later; verified
later. Kept apart from :mod:`powwx.forecast_logger` so a failure here can never
cost the deterministic log, and in its own directory so the members never enter
the deterministic leaderboard or the blend.
"""

from __future__ import annotations

from pathlib import Path

from . import config as cfg
from . import openmeteo as om
from .forecast_logger import LOGGER_MAX_RETRIES


def log_ensembles(*, data_dir: Path | None = None) -> dict:
    """Run the ensemble logger once. Returns a summary dict."""
    locations = cfg.load_locations()
    ens = cfg.load_models().get("ensembles")
    data_dir = data_dir or cfg.DATA_DIR
    if not ens:
        return {"fetched_at": None, "n_records": 0, "per_location": {},
                "failed": {}, "path": None}

    fetched_at = om.utcnow()
    issued_at = fetched_at  # no run time exposed, as for the deterministic log

    all_records: list[dict] = []
    per_location: dict[str, int] = {}
    errors: dict[str, str] = {}
    for loc in locations:
        for model in ens["models"]:
            key = f"{loc['id']}/{model['id']}"
            try:
                raw = om.fetch_forecast(
                    latitude=loc["latitude"],
                    longitude=loc["longitude"],
                    models=[model["id"]],
                    variables=ens["variables"],
                    forecast_url=ens["url"],
                    forecast_days=ens.get("forecast_days", 16),
                    max_retries=LOGGER_MAX_RETRIES,
                )
            except Exception as exc:  # noqa: BLE001 - keep the other locations
                errors[key] = f"{type(exc).__name__}: {exc}"
                continue
            recs = om.parse_ensemble_long(
                raw,
                model_id=model["id"],
                variables=ens["variables"],
                location_id=loc["id"],
                issued_at=issued_at,
                fetched_at=fetched_at,
            )
            all_records.extend(recs)
            per_location[key] = len(recs)

    from .storage import write_forecast_run

    path = write_forecast_run(all_records, data_dir=data_dir, fetched_at=fetched_at,
                              tag="ens", subdir="ensembles")
    return {
        "fetched_at": om._iso(fetched_at),
        "n_records": len(all_records),
        "per_location": per_location,
        "failed": errors,
        "path": str(path) if path else None,
    }

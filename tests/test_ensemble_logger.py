"""Ensemble logging: members parse to their own model ids, the run lands in
data/ensembles/ (never data/forecasts/, so the leaderboard can't see it), and one
failed location doesn't discard the others."""

from datetime import datetime, timezone
from unittest import mock

import pandas as pd
import requests

from powwx import ensemble_logger as el
from powwx import openmeteo as om

T0 = datetime(2026, 10, 16, 21, 0, tzinfo=timezone.utc)
RAW = {"hourly": {
    "time": ["2026-10-16T00:00", "2026-10-16T01:00"],
    "temperature_2m": [1.0, 2.0],             # control
    "temperature_2m_member01": [1.5, None],   # null dropped
    "precipitation_member20": [0.0, 0.4],
    "snow_depth_member01": [0.1, 0.1],        # not configured -> skipped
}}
LOCATIONS = [
    {"id": "pow_o_meter", "latitude": 54.5, "longitude": -128.96},
    {"id": "station_58", "latitude": 54.48, "longitude": -128.95},
]
MODELS_CFG = {"ensembles": {
    "url": "https://example.invalid/v1/ensemble",
    "variables": ["temperature_2m", "precipitation"],
    "models": [{"id": "gem_global_ensemble"}],
}}


def test_parse_ensemble_members_become_model_ids():
    recs = om.parse_ensemble_long(RAW, model_id="gem_global_ensemble",
                                  variables=["temperature_2m", "precipitation"],
                                  location_id="pow_o_meter", issued_at=T0, fetched_at=T0)
    got = sorted({(r["model"], r["variable"]) for r in recs})
    assert got == [
        ("gem_global_ensemble_member00", "temperature_2m"),
        ("gem_global_ensemble_member01", "temperature_2m"),
        ("gem_global_ensemble_member20", "precipitation"),
    ]
    assert len(recs) == 5                     # 2 control + 1 member01 + 2 member20


def test_log_writes_to_ensembles_dir_and_keeps_good_locations(tmp_path):
    def fetch(*, latitude, **kw):
        if latitude == 54.48:
            raise requests.ConnectionError("down")
        return RAW

    with mock.patch.object(el.cfg, "load_locations", return_value=LOCATIONS), \
         mock.patch.object(el.cfg, "load_models", return_value=MODELS_CFG), \
         mock.patch.object(el.om, "fetch_forecast", side_effect=fetch):
        summary = el.log_ensembles(data_dir=tmp_path)

    assert list(summary["failed"]) == ["station_58/gem_global_ensemble"]
    assert not (tmp_path / "forecasts").exists()
    files = list((tmp_path / "ensembles").glob("issued_date=*/ens_*.parquet"))
    assert len(files) == 1
    df = pd.read_parquet(files[0])
    assert set(df["location"]) == {"pow_o_meter"}
    assert len(df) == summary["n_records"] == 5

"""Tests for the forecast logger's resilience: a location that fails is retried
in a second pass, and a persistent failure doesn't discard the locations that
succeeded (a live snapshot can't be re-fetched later)."""

from unittest import mock

import requests

from powwx import forecast_logger as fl

LOCATIONS = [
    {"id": "pow_o_meter", "latitude": 54.5, "longitude": -128.96},
    {"id": "station_58", "latitude": 54.48, "longitude": -128.95},
]
MODELS_CFG = {
    "api": {"forecast_url": "https://example.invalid/v1/forecast"},
    "variables": ["temperature_2m"],
    "models": [{"id": "gem_global"}],
}


def _fake_parse(raw, *, location_id, **_):
    return [{"location": location_id, "model": "gem_global", "value": raw["v"]}]


def _run(tmp_path, fetch, models_cfg=MODELS_CFG):
    with mock.patch.object(fl.cfg, "load_locations", return_value=LOCATIONS), \
         mock.patch.object(fl.cfg, "load_models", return_value=models_cfg), \
         mock.patch.object(fl.cfg, "model_ids", return_value=["gem_global"]), \
         mock.patch.object(fl.om, "fetch_forecast", side_effect=fetch) as f, \
         mock.patch.object(fl.om, "parse_long", side_effect=_fake_parse), \
         mock.patch.object(fl.time, "sleep") as sleep:
        summary = fl.log_forecasts(data_dir=tmp_path)
    return summary, f, sleep


def test_second_pass_recovers_failed_location(tmp_path):
    calls = {"n": 0}

    def fetch(*, latitude, **kw):
        calls["n"] += 1
        # station_58 fails its first attempt only.
        if latitude == 54.48 and calls["n"] == 2:
            raise requests.HTTPError("transient HTTP 503")
        return {"v": 1.0}

    summary, f, sleep = _run(tmp_path, fetch)
    assert summary["per_location"] == {"pow_o_meter": 1, "station_58": 1}
    assert summary["failed_locations"] == {}
    assert f.call_count == 3                      # 2 first pass + 1 retry
    sleep.assert_called_once_with(fl.SECOND_PASS_DELAY)
    # The logger asks for the longer retry budget, not the client default.
    assert all(c.kwargs["max_retries"] == fl.LOGGER_MAX_RETRIES for c in f.call_args_list)


def test_persistent_failure_keeps_successful_locations(tmp_path):
    def fetch(*, latitude, **kw):
        if latitude == 54.48:
            raise requests.HTTPError("transient HTTP 429")
        return {"v": 2.0}

    summary, _, _ = _run(tmp_path, fetch)
    assert summary["n_records"] == 1
    assert summary["per_location"] == {"pow_o_meter": 1}
    assert "429" in summary["failed_locations"]["station_58"]
    assert summary["path"] is not None             # partial run still written


def test_all_ok_skips_second_pass(tmp_path):
    summary, f, sleep = _run(tmp_path, lambda **kw: {"v": 3.0})
    assert summary["n_records"] == 2
    assert f.call_count == 2
    sleep.assert_not_called()


def test_missing_models_are_reported_per_location():
    # Review F19 item 4: a configured model Open-Meteo returns nothing for (e.g. a
    # renamed id) must be flagged, not silently absent.
    recs = [{"location": "pow_o_meter", "model": "gem_global"},
            {"location": "pow_o_meter", "model": "icon_global"},
            {"location": "station_58", "model": "gem_global"}]
    out = fl.missing_models(recs, ["gem_global", "icon_global"], ["pow_o_meter", "station_58"])
    assert out == {"station_58": ["icon_global"]}
    assert fl.missing_models([], ["gem_global"], ["pow_o_meter"]) == {"pow_o_meter": ["gem_global"]}


def test_run_init_kept_only_when_unchanged_during_the_fetch():
    from datetime import datetime, timezone
    t0 = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 19, 18, tzinfo=timezone.utc)
    before = {"gfs_seamless": t0, "icon_global": t0, "gem_global": None}
    after = {"gfs_seamless": t0, "icon_global": t1, "gem_global": None}
    assert fl.om.settled_run_inits(before, after) == {
        "gfs_seamless": "2026-10-19T12:00:00Z",   # same run throughout
        "icon_global": None,                       # a new run landed mid-fetch
        "gem_global": None}                        # meta.json unreadable


def test_run_inits_reads_each_domain_once_and_skips_models_without_one():
    with mock.patch.object(fl.om, "fetch_run_init", return_value="T") as f:
        out = fl.om.run_inits({"a": "dom1", "b": "dom1", "c": None})
    assert out == {"a": "T", "b": "T"}
    f.assert_called_once_with("dom1")


def test_logger_stores_run_init_on_every_row(tmp_path):
    from datetime import datetime, timezone
    cfg = {**MODELS_CFG, "models": [{"id": "gem_global", "run_meta": "cmc_gem_gdps_15km"}]}
    t0 = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    with mock.patch.object(fl.om, "fetch_run_init", return_value=t0):
        summary, _, _ = _run(tmp_path, lambda **kw: {"v": 3.0}, models_cfg=cfg)
    import pandas as pd
    df = pd.read_parquet(summary["path"])
    assert summary["run_init"] == {"gem_global": "2026-10-19T12:00:00Z"}
    assert (df["run_init"] == "2026-10-19T12:00:00Z").all()

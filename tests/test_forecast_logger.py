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


def _run(tmp_path, fetch):
    with mock.patch.object(fl.cfg, "load_locations", return_value=LOCATIONS), \
         mock.patch.object(fl.cfg, "load_models", return_value=MODELS_CFG), \
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

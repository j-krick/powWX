"""ERA5-Land / ERA5 cache: explicit model and elevation, parsing, latest fetch wins."""

from datetime import datetime, timezone

import pytest

from powwx import reanalysis as ra

LOC = {"id": "station_58", "latitude": 54.48497, "longitude": -128.95586, "elevation_m": 740}


def test_request_names_the_model_and_station_elevation():
    p = ra.request_params(latitude=1, longitude=2, elevation=740, model="era5_land",
                          start="2020-01-01", end="2020-12-31")
    assert p["models"] == "era5_land" and p["elevation"] == 740
    assert p["timezone"] == "America/Vancouver"
    assert "snow_depth_mean" in p["daily"]


def test_best_match_is_refused():
    # best_match switches to ECMWF IFS from 2017, which would fake a trend.
    with pytest.raises(ValueError):
        ra.request_params(latitude=1, longitude=2, elevation=740, model="best_match",
                          start="2020-01-01", end="2020-01-02")


def test_parse_drops_days_not_yet_available():
    payload = {"daily": {"time": ["2026-10-10", "2026-10-11"],
                         "temperature_2m_mean": [3.1, None],
                         "temperature_2m_min": [1.0, None],
                         "temperature_2m_max": [5.0, None]}}
    recs = ra.parse_daily(payload, location="station_58", model="era5", fetched_at="x")
    assert {r["date"] for r in recs} == {"2026-10-10"}
    assert len(recs) == 3


def test_fetch_daily_goes_through_the_retrying_getter(monkeypatch):
    seen = {}

    def fake_get(url, params, **kw):
        seen.update(url=url, params=params)
        return {"daily": {"time": ["2020-01-01"], "temperature_2m_mean": [-1.0]}}

    monkeypatch.setattr(ra.om, "get_with_retry", fake_get)
    recs = ra.fetch_daily(LOC, model="era5", start="2020-01-01", end="2020-01-01")
    assert seen["url"] == ra.ARCHIVE_URL and seen["params"]["elevation"] == 740
    assert recs[0]["value"] == -1.0 and recs[0]["location"] == "station_58"


def test_latest_fetch_wins_on_read(tmp_path):
    def rec(day, value, fetched):
        return {"location": "station_58", "date": day, "variable": "temperature_2m_mean",
                "value": value, "model": "era5_land", "fetched_at": fetched}

    ra.write_fetch([rec("2026-09-01", 1.0, "2026-09-10T00:00:00Z"),
                    rec("2026-09-02", 2.0, "2026-09-10T00:00:00Z")],
                   data_dir=tmp_path, fetched_at=datetime(2026, 9, 10, tzinfo=timezone.utc))
    ra.write_fetch([rec("2026-09-02", 2.5, "2026-10-10T00:00:00Z")],   # revised later
                   data_dir=tmp_path, fetched_at=datetime(2026, 10, 10, tzinfo=timezone.utc))
    wide = ra.load_reanalysis(tmp_path, location="station_58")
    assert wide["temperature_2m_mean"].tolist() == [1.0, 2.5]
    assert ra.load_reanalysis(tmp_path, location="pow_o_meter").empty


def test_chunks_cover_the_span_without_overlap():
    c = ra.chunks("1950-01-01", "1971-06-30", 10)
    assert c == [("1950-01-01", "1959-12-31"), ("1960-01-01", "1969-12-31"),
                 ("1970-01-01", "1971-06-30")]
    assert ra.chunks("2026-07-18", "2026-10-16", 10) == [("2026-07-18", "2026-10-16")]
